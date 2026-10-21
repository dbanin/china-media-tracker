"""Category B adjudication with the Anthropic API.

An article reaches this stage if it passed the relevance gate, was not
classified A deterministically, and either matched a weak A signature or an
official sourcing trigger. The model sees the headline and body only. It never
sees the outlet name or the country, so it cannot learn a geographic pattern
the map is supposed to discover.

Modes:
  synchronous (default)  one request per article, results stored immediately
  batch (--batch)        submits pending articles to the Message Batches API and
                         collects finished batches on later runs
  dry run                a fake client, no network, for tests and fixtures

The daily call ceiling (config.LLM_DAILY_CALL_CEILING) stops the stage and
records a ceiling event in llm_usage and run_log, so a truncated day is never
mistaken for a quiet day.

The API budget is kept for what the interactive session route (pipeline.labels) cannot reach. The
articles listed in data/labels/api_first.jsonl, whose text the session could not fetch, are drawn
first; other waiting articles are offered only once older than config.LLM_SESSION_GRACE_DAYS, and
younger ones are held for the session (split_pools).

Every label records the prompt it was made under (PROMPT_VERSION, in model_version). When the prompt
changes, current model labels made under an older one are judged again, model state origin labels
resting on no credit, stamp or byline first, then unchecked state sourcing labels, oldest first
within each tier (RELABEL_PRIORITY), with what the
new articles leave of each run's calls and at most config.LLM_RELABEL_DAY_SHARE of the day's cap
(relabel_candidates, relabel_allowance). The old label stays current until the new one is stored.
A model label made on a stub whose body was since replaced by the article (classify_rules.note_body_refresh
marks it body_refreshed) goes through the same queue, right after the unchecked state sourcing labels.

Every model label records who issued the text (issuer_type, ISSUER_TYPES), and a state origin label
records its route from the basis the model stated (state_basis, BASIS_ROUTES), never from the
signatures that sent the article.
"""
import datetime as dt
import json
import random
import re
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from pipeline import config, llm_cost, store

# The date of the system prompt below. Bump it whenever the prompt changes what a label means. Every
# model label records it in its model_version text, "claude-sonnet-5 (prompt 2026-10-12)" (a session
# label: "claude-sonnet-5 (session, prompt 2026-10-12)"), and a current model label made under an
# older prompt, or carrying no prompt tag at all (every label before 2026-10-12), is queued for the
# model again (relabel_candidates) while its old label stays current until the new one arrives.
# 2026-10-12: the factual-claim rule for unverified relay, and the reworded state origin definition.
# 2026-10-17: who counts as a state issuer (press releases, state-owned enterprises, the Hong Kong and
# Macao governments, state copy on any topic), and the issuer_type and state_basis fields.
PROMPT_VERSION = "2026-10-17"
_PROMPT_TAG = re.compile(r"\bprompt (\d{4}-\d{2}-\d{2})\b")


def prompt_version_of(model_version: Optional[str]) -> Optional[str]:
    """The prompt date a label's model_version records, or None when it records none."""
    m = _PROMPT_TAG.search(model_version or "")
    return m.group(1) if m else None


def with_prompt_version(model_version: Optional[str], prompt_version: Optional[str] = PROMPT_VERSION) -> Optional[str]:
    """model_version with the prompt date appended, "claude-sonnet-5 (prompt 2026-10-12)". Left as it
    is when it already records a prompt, or when prompt_version is None."""
    if not prompt_version or prompt_version_of(model_version):
        return model_version
    return "%s (prompt %s)" % ((model_version or "unrecorded").strip(), prompt_version)


def on_current_prompt(model_version: Optional[str]) -> bool:
    """True when a model label was made under the current PROMPT_VERSION (or a later one)."""
    v = prompt_version_of(model_version)
    return v is not None and v >= PROMPT_VERSION

CATEGORY_DEFINITIONS = """Category A, state origin. The text was authored or commissioned by a Chinese state entity and published without substantive editorial alteration. A Chinese state entity is a state media outlet or state media group (Xinhua, CGTN, China Daily, Global Times, People's Daily, CCTV, China News Service, China Radio International), a ministry or other government body, a Communist Party of China body, an embassy, consulate or diplomat, a provincial or municipal government or its propaganda department or information office, a state-owned enterprise, or a government department or statutory body of the Hong Kong or Macao Special Administrative Region. Xinhua wire copy, CGTN promotional releases, China Daily and Global Times syndication, sponsored or branded placements commissioned by Chinese state entities, including those written to order by the publisher's own branded content studio, and signed opinion pieces by Chinese ambassadors and embassy officials. The defining property is that the words are the state's, written by it or to its order, and the receiving publication did not substantively alter them. Deciding to run a piece, or placing it on an opinion page, is not an alteration. State text republished without its credit line is still state origin when the article is that text with no substantive alteration. State text is state origin whatever its topic: a Xinhua story about Nicaragua, run unaltered, is state origin.

Press releases. A press release is state origin only when a Chinese state entity issued or commissioned it: one carrying a state media credit such as "A news report from Xinhuanet", "China Daily" or "CGTN", or one issued by a ministry, a party or government body, an embassy, a provincial or municipal government or its propaganda department, or a state media group. Being published unaltered is not enough, and neither is a sponsored, paid or partner label, a press release section, or praise of China. A release issued by a private Chinese company, a Hong Kong or Macao private body, a university (a public one included), a charity, a trade show organiser, an auction house, a casino, a sports club or a foreign organisation is not state origin: it is independent journalism when it substantively concerns China, and not relevant otherwise. A commercial release of a state-owned enterprise (GAC, CRRC, Sinopec, State Grid, China Eastern, Shanghai Electric, Chery, Zoomlion and the like) is state origin, recorded as a state-owned enterprise release. A release of a Hong Kong or Macao government department or statutory body (an HKSAR government department, the Hong Kong Trade Development Council, the Hong Kong Tourism Board, the ICAC, a Macao government office) is state origin, recorded as a SAR government release.

Category B, unverified relay. The article was written by the local outlet, and its central content is a factual claim attributed to a Chinese official or state source, passed on without independent confirmation. A factual claim is an assertion of fact about events, numbers or actions: what happened, how many, who did what. A ministry spokesperson or Xinhua is the only authority for such a fact and the article treats it as established, with no second source. A stated position, policy, warning, threat, opinion or denial is not a factual claim: an article that reports what Beijing said it wants, intends, condemns or rejects is reporting the statement itself, and that is independent journalism even when no outside voice comments on it. For example, "Exports rose 8.7 percent in September, China's commerce ministry said", reported as the figure with no other source, is B. "China's foreign ministry warned Manila it would take resolute measures to defend its sovereignty", reported without outside comment, is C, because the article passes on a position, not a fact it failed to check.

Category C, independent journalism. The outlet's own reporting, including reporting that quotes Chinese officials but confirms, contextualizes or contests what they say, and reporting of a position, policy, warning, threat, opinion or denial attributed to a Chinese official or state source, with or without outside comment. A press release that is not state origin is coded here when it substantively concerns China.

Not relevant. Does not concern China in any substantive way; a passing mention does not make an article relevant. A Hong Kong, Macao or Taiwan domestic story with no mainland angle concerns China only when it substantively concerns the PRC state or its policy (cross-strait relations, the national security law, Beijing's role); otherwise it is not relevant. State origin text is never coded not relevant, whatever its topic."""

SYSTEM_PROMPT = """You are a coder on an academic media study. You place one news article into exactly one of four categories. The definitions below come from the study's codebook and are binding.

""" + CATEGORY_DEFINITIONS + """

Decide in this order. First, who wrote the text: if a Chinese state entity authored or commissioned it and it was published without substantive alteration, answer A, whatever the topic. Second, if it is not state origin, whether it concerns China in any substantive way; if it does not, answer not_relevant. Third, for an article that concerns China, choose between B and C.

Answer A only when the text itself was authored or commissioned by a Chinese state entity and published without substantive editorial alteration, for example a wire item carrying a Xinhua credit, a sponsored placement commissioned by a Chinese state entity, a release issued by a ministry, a provincial government, a state-owned enterprise or the Hong Kong or Macao government, or a signed piece by a Chinese diplomat. An article about state media, or one that quotes state media, is not A. Name the issuer of the text before you answer A. The issuer decides, not the presentation: a text that reads like a sponsored placement, comes in press release form, carries a paid or partner label, or was published unaltered is not A on that account. If the issuer is not a Chinese state entity, the answer is not A, however closely the text matches the pattern of a sponsored placement. A Chinese company is a state entity only when it is state-owned. Use what is widely known about the issuer: GAC, CRRC, Sinopec, State Grid, China Eastern, Shanghai Electric, Chery and Zoomlion are state-owned enterprises; vivo, XPENG, Huawei, MINISO, CATL, Tencent, Baidu, Fosun and Pudu are private companies. When neither the text nor what is widely known shows that a company is state-owned, treat it as private. State text about another country, such as a Xinhua item on a volcano in the Philippines carried under a partner agency's credit, is A, never not_relevant.

The critical distinction is between B and C, and it is a judgement about verification, not about topic or tone. An article highly critical of China that repeats an unverified Xinhua claim is still B. An article sympathetic to China that independently confirms every claim is C. Do not let the tone of the article, its political stance, or the topic move your judgement. Ask only: does official Chinese sourcing carry a factual claim that is central to this article, and does the article independently confirm, contextualize or contest that claim? A position, policy, warning, threat, opinion or denial attributed to Beijing is not a factual claim, so an article that reports one is C whether or not anyone else comments.

When you are genuinely uncertain between B and C, answer C. Under-counting B is the safer error for this study. Answer B only when you can quote a sentence in which an official Chinese source carries a factual assertion, central to the article, that the article treats as fact without any independent confirmation, contextualization or contest. That confirmation, contextualization or contest must concern the claim itself: when a central claim such as a death toll, a figure or the account of an event rests only on a Chinese official or state source, other context or background in the article (details of a vessel or a company, analysts discussing what the figure means) does not make it C, because it takes the claim as given instead of checking it.

Answer not_relevant when the article is not state origin and does not concern China in any substantive way. A passing mention does not make an article relevant. A Hong Kong, Macao or Taiwan domestic story with no mainland angle is not_relevant unless it substantively concerns the PRC state or its policy, such as cross-strait relations, the national security law or Beijing's role. A press release that is not state origin is C when it substantively concerns China and not_relevant otherwise, by the same test as any other article.

Return a JSON object with these fields and nothing else:
  category: "A", "B", "C" or "not_relevant"
  confidence: a number from 0.0 to 1.0 for how sure you are of the category
  evidence_quote: the single sentence from the article that most determines the label, copied exactly
  reasoning: two sentences at most
  china_sources_cited: list of official Chinese sources the article cites or quotes, for example "Xinhua" or "MOFA spokesperson Lin Jian", empty list if none
  independent_confirmation_present: true if the article independently confirms, contextualizes or contests the official Chinese claims it carries
  confirmation_evidence: the sentence showing that confirmation, contextualization or contest, or null
  issuer_type: who issued the text itself, not whom it quotes. "state_media" (a state media outlet or group), "government" (a ministry or other government body, a provincial or municipal government, its propaganda department or information office), "party" (a Communist Party of China body), "diplomatic" (an embassy, a consulate, an ambassador or another Chinese diplomat), "state_owned_enterprise" (a state-owned enterprise or its subsidiary), "sar_government" (a Hong Kong or Macao government department or statutory body), "private_release" (a press release or sponsored text from any other issuer: a private Chinese company, a Hong Kong or Macao private body, a university, a charity, a trade show organiser, an auction house, a casino, a sports club, a foreign organisation), or "none" (the outlet's own reporting, including reporting that substantively rewrites a state text). Category A needs one of the first six; "private_release" goes with C or not_relevant; "none" goes with B, C or not_relevant.
  state_basis: for category A, what in the text shows that the state authored or commissioned it. "credit_line" (a state media credit, dateline or byline on the text), "distribution_stamp" (a release distribution notice naming a Chinese state media or government issuer, such as "A news report from Xinhuanet" or "Source: Xinhua Silk Road"), "sponsored_disclosure" (a sponsored, paid, partner or advertorial notice on a text a Chinese state entity commissioned), "diplomatic_byline" (signed by a Chinese ambassador or diplomat), "context_match" (the text is the state origin item named in a context note), or "issuer_named" (the text is itself a release or statement of the state entity it names, with none of the above). "none" for every other category.

Worked examples.

Example 1. Headline: "China's grain output hits record high". Text: "BEIJING, Dec. 12 (Xinhua) -- China's grain output reached a record 706 million tonnes in 2024, up 1.6 percent year on year, the National Bureau of Statistics said Friday. The increase was attributed to expanded planting area and higher yields, the bureau said." Answer: {"category": "A", "confidence": 0.98, "evidence_quote": "BEIJING, Dec. 12 (Xinhua) -- China's grain output reached a record 706 million tonnes in 2024, up 1.6 percent year on year, the National Bureau of Statistics said Friday.", "reasoning": "The text carries a Xinhua wire dateline and is wire copy published unaltered. No local editorial judgement is present.", "china_sources_cited": ["Xinhua", "National Bureau of Statistics"], "independent_confirmation_present": false, "confirmation_evidence": null, "issuer_type": "state_media", "state_basis": "credit_line"}

Example 2. Headline: "Beijing rejects espionage claims after arrests". Text: "China has dismissed allegations that two nationals detained last week were working for its intelligence services. Foreign ministry spokesperson Mao Ning said on Tuesday the pair were ordinary businessmen and that the accusations were fabricated to smear China. She said the detentions violated the rights of Chinese citizens and demanded their immediate release. The two men were arrested in Manchester on Thursday." Answer: {"category": "C", "confidence": 0.8, "evidence_quote": "China has dismissed allegations that two nationals detained last week were working for its intelligence services.", "reasoning": "The article's central content is Beijing's denial and its demand for release, a stated position reported as a position, not an official factual claim treated as fact. Reporting a denial is independent journalism even though no outside source comments on it.", "china_sources_cited": ["MOFA spokesperson Mao Ning"], "independent_confirmation_present": false, "confirmation_evidence": null, "issuer_type": "none", "state_basis": "none"}

Example 3. Headline: "Beijing rejects espionage claims after arrests". Text: "China has dismissed allegations that two nationals detained last week were working for its intelligence services. Foreign ministry spokesperson Mao Ning said on Tuesday the pair were ordinary businessmen and that the accusations were fabricated to smear China. Court filings seen by this newspaper, however, show one of the men listed a Ministry of State Security address on his 2019 visa application, and a former colleague confirmed he had been introduced to clients as a government liaison. Police declined to comment." Answer: {"category": "C", "confidence": 0.92, "evidence_quote": "Court filings seen by this newspaper, however, show one of the men listed a Ministry of State Security address on his 2019 visa application, and a former colleague confirmed he had been introduced to clients as a government liaison.", "reasoning": "The same spokesperson claim appears, but the outlet contests it with documents and an independent source. The official claim is not treated as fact.", "china_sources_cited": ["MOFA spokesperson Mao Ning"], "independent_confirmation_present": true, "confirmation_evidence": "Court filings seen by this newspaper, however, show one of the men listed a Ministry of State Security address on his 2019 visa application, and a former colleague confirmed he had been introduced to clients as a government liaison.", "issuer_type": "none", "state_basis": "none"}

Example 4. Headline: "Xinjiang camps hold more than a million, leaked files show". Text: "Internal documents obtained by a consortium of newspapers describe the operation of detention camps in Xinjiang, including instructions to prevent escapes and to score detainees on ideological transformation. The files were authenticated by three independent experts. Beijing has called the camps vocational training centres. A statement from the Chinese embassy said the documents were fabricated and that the region was stable and prosperous. Former detainees interviewed for this story described forced political indoctrination." Answer: {"category": "C", "confidence": 0.95, "evidence_quote": "The files were authenticated by three independent experts.", "reasoning": "This is hostile to China and quotes the embassy, but the reporting rests on independently authenticated documents and interviews. Official Chinese claims are contested, not relayed.", "china_sources_cited": ["Chinese embassy statement"], "independent_confirmation_present": true, "confirmation_evidence": "Former detainees interviewed for this story described forced political indoctrination.", "issuer_type": "none", "state_basis": "none"}

Example 5. Headline: "China launches three astronauts to space station". Text: "China sent three astronauts to its Tiangong space station on Wednesday, state broadcaster CCTV reported. The Shenzhou-19 craft lifted off from the Jiuquan launch centre at 4:27 am, according to the China Manned Space Agency, which said the launch was a complete success. The crew will stay for six months. The mission is the latest step in China's plan to land astronauts on the moon by 2030, the agency said." Answer: {"category": "B", "confidence": 0.8, "evidence_quote": "The Shenzhou-19 craft lifted off from the Jiuquan launch centre at 4:27 am, according to the China Manned Space Agency, which said the launch was a complete success.", "reasoning": "The outlet wrote a short item in which every factual claim is carried by CCTV and the space agency and treated as fact. Nothing independent confirms or contextualizes them.", "china_sources_cited": ["CCTV", "China Manned Space Agency"], "independent_confirmation_present": false, "confirmation_evidence": null, "issuer_type": "none", "state_basis": "none"}

Example 6. Headline: "Nepal floods: rescuers reach cut-off villages". Text: "Rescue teams reached villages in Rasuwa district on Sunday after days of landslides. At least 40 people are confirmed dead, according to the Nepalese home ministry. Aid agencies said access remained difficult. Chinese state media reported that six people were also missing on the Tibetan side of the border." Answer: {"category": "C", "confidence": 0.7, "evidence_quote": "Rescue teams reached villages in Rasuwa district on Sunday after days of landslides.", "reasoning": "The article is independent reporting on Nepal with Nepalese and aid agency sourcing. A single attributed line from Chinese state media about the Tibetan side is marked as a report, not treated as established fact, and is peripheral, and uncertain between B and C, the answer is C.", "china_sources_cited": ["Chinese state media"], "independent_confirmation_present": false, "confirmation_evidence": null, "issuer_type": "none", "state_basis": "none"}

Example 7, a private company's release. Headline: "Lanshan Robotics opens new production base in Hefei". Text: "HEFEI, China, Sept. 3, 2026 /PRNewswire/ -- Lanshan Robotics, a privately held maker of service robots, today opened a production base in Hefei that doubles its annual capacity, to meet rising demand from hotels and hospitals across China. The company said the base would employ 1,200 people. Sponsored content." Answer: {"category": "C", "confidence": 0.85, "evidence_quote": "HEFEI, China, Sept. 3, 2026 /PRNewswire/ -- Lanshan Robotics, a privately held maker of service robots, today opened a production base in Hefei that doubles its annual capacity, to meet rising demand from hotels and hospitals across China.", "reasoning": "The issuer is a private Chinese company, not a Chinese state entity, so the release is not state origin even though it was published unaltered under a sponsored label. Its subject is business in China, so it concerns China and is coded C.", "china_sources_cited": [], "independent_confirmation_present": false, "confirmation_evidence": null, "issuer_type": "private_release", "state_basis": "none"}

Example 8, a state-owned enterprise's release. Headline: "GAC Cambodia plant starts production". Text: "PHNOM PENH, Cambodia, Sept. 20, 2026 /PRNewswire/ -- GAC Group today started production at its assembly plant in Cambodia, the carmaker's first in the country, with an initial capacity of 20,000 vehicles a year. The plant will assemble AION models for local and regional markets, GAC said." Answer: {"category": "A", "confidence": 0.85, "evidence_quote": "PHNOM PENH, Cambodia, Sept. 20, 2026 /PRNewswire/ -- GAC Group today started production at its assembly plant in Cambodia, the carmaker's first in the country, with an initial capacity of 20,000 vehicles a year.", "reasoning": "The release was issued by GAC Group, a state-owned carmaker, and is published as issued. A state-owned enterprise's commercial release is state origin, recorded with its own issuer type.", "china_sources_cited": [], "independent_confirmation_present": false, "confirmation_evidence": null, "issuer_type": "state_owned_enterprise", "state_basis": "issuer_named"}

Example 9, a Hong Kong SAR statutory body. Headline: "HKTDC to stage autumn electronics fair". Text: "HONG KONG, Oct. 1, 2026 /PRNewswire/ -- The Hong Kong Trade Development Council (HKTDC) will hold its Autumn Electronics Fair from 13 to 16 October at the Hong Kong Convention and Exhibition Centre, with more than 3,000 exhibitors expected. The HKTDC said the fair would help small and medium-sized firms reach buyers in mainland China and Southeast Asia." Answer: {"category": "A", "confidence": 0.85, "evidence_quote": "HONG KONG, Oct. 1, 2026 /PRNewswire/ -- The Hong Kong Trade Development Council (HKTDC) will hold its Autumn Electronics Fair from 13 to 16 October at the Hong Kong Convention and Exhibition Centre, with more than 3,000 exhibitors expected.", "reasoning": "The HKTDC is a statutory body of the Hong Kong SAR, which the codebook counts as a Chinese state entity, and its release is published as issued. It is state origin, recorded as a SAR government release.", "china_sources_cited": [], "independent_confirmation_present": false, "confirmation_evidence": null, "issuer_type": "sar_government", "state_basis": "issuer_named"}

Example 10, a Hong Kong university's release. Headline: "CityUHK team develops low-cost water sensor". Text: "HONG KONG, Sept. 15, 2026 /PRNewswire/ -- Researchers at City University of Hong Kong have developed a low-cost sensor that detects heavy metals in drinking water within minutes, the university said. The team plans to test the sensor with rural communities next year." Answer: {"category": "not_relevant", "confidence": 0.85, "evidence_quote": "Researchers at City University of Hong Kong have developed a low-cost sensor that detects heavy metals in drinking water within minutes, the university said.", "reasoning": "A university, a public one included, is not a Chinese state entity, so the release is not state origin. It is a Hong Kong story with no bearing on the PRC state or its policy, so it does not concern China.", "china_sources_cited": [], "independent_confirmation_present": false, "confirmation_evidence": null, "issuer_type": "private_release", "state_basis": "none"}

Example 11, state copy on a topic other than China. Headline: "Small volcano near Philippine capital erupts". Text: "MANILA, Oct. 9 (Bernama-Xinhua) -- A small volcano south of the Philippine capital erupted on Thursday, sending a column of ash about a kilometre into the sky, the country's volcanology institute said. No casualties were reported." Answer: {"category": "A", "confidence": 0.9, "evidence_quote": "MANILA, Oct. 9 (Bernama-Xinhua) -- A small volcano south of the Philippine capital erupted on Thursday, sending a column of ash about a kilometre into the sky, the country's volcanology institute said.", "reasoning": "The text carries a Xinhua credit and is published unaltered, so the words are the state's. State text is state origin whatever its topic, so it is A although it is about the Philippines.", "china_sources_cited": ["Xinhua"], "independent_confirmation_present": false, "confirmation_evidence": null, "issuer_type": "state_media", "state_basis": "credit_line"}
"""

# Who issued the text the model judged (its issuer_type field, stored on the label). The first six are
# Chinese state issuers, the only ones a state origin label may carry; state_owned_enterprise and
# sar_government are shown as their own lines on the site, so readers can leave them out. private_release
# marks a release from any other issuer, coded C or not relevant, so those can be counted on their own.
STATE_ISSUER_TYPES = ("state_media", "government", "party", "diplomatic", "state_owned_enterprise", "sar_government")
ISSUER_TYPES = STATE_ISSUER_TYPES + ("private_release", "none")
# What in the text shows the state wrote or commissioned it (the model's state_basis field), and the route
# a state origin label it gave is stored under. issuer_named, a release or statement of the state entity
# it names with no credit, stamp, disclosure or byline, is unattributed; so is "none" or no basis at all.
BASIS_ROUTES = {"credit_line": "wire_credit", "distribution_stamp": "distribution_stamp",
                "sponsored_disclosure": "sponsored_disclosure", "diplomatic_byline": "diplomatic_byline",
                "context_match": "uncredited_copy", "issuer_named": "unattributed"}
STATE_BASES = tuple(BASIS_ROUTES) + ("none",)

# A reply whose category and issuer_type contradict each other (inconsistency_of). It is stored, never
# retried, with the reason in the label's needs_review column, counted in the export's meta
# (inconsistent_labels), and left for a person to settle. A retry would be a second paid call for the
# same text, and in the batch path a second batch a day later. Whatever the reply, an inconsistent one
# never adds a state origin label:
#   state_origin_without_state_issuer  category A with issuer_type private_release or none. The audit
#       found the issuer the safer half (the model wrote "not a state entity" and answered A anyway),
#       so the label is stored as independent journalism (C), not A, with no route.
#   state_issuer_without_state_origin  category B, C or not_relevant with a state issuer type, which
#       would mean state origin. The category is kept as returned; it is not raised to A.
STATE_ORIGIN_WITHOUT_STATE_ISSUER = "state_origin_without_state_issuer"
STATE_ISSUER_WITHOUT_STATE_ORIGIN = "state_issuer_without_state_origin"
INCONSISTENCIES = (STATE_ORIGIN_WITHOUT_STATE_ISSUER, STATE_ISSUER_WITHOUT_STATE_ORIGIN)


def inconsistency_of(data: Dict) -> Optional[str]:
    """Why a reply's category and issuer_type contradict each other (INCONSISTENCIES), or None. A reply
    with no issuer_type (one made before the field existed) is never inconsistent."""
    issuer = issuer_type_of(data)
    if issuer is None:
        return None
    if data.get("category") == "A" and issuer in ("private_release", "none"):
        return STATE_ORIGIN_WITHOUT_STATE_ISSUER
    if data.get("category") in ("B", "C", "not_relevant") and issuer in STATE_ISSUER_TYPES:
        return STATE_ISSUER_WITHOUT_STATE_ORIGIN
    return None

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["A", "B", "C", "not_relevant"]},
        "confidence": {"type": "number"},
        "evidence_quote": {"type": "string"},
        "reasoning": {"type": "string"},
        "china_sources_cited": {"type": "array", "items": {"type": "string"}},
        "independent_confirmation_present": {"type": "boolean"},
        "confirmation_evidence": {"type": ["string", "null"]},
        "issuer_type": {"type": "string", "enum": list(ISSUER_TYPES)},
        "state_basis": {"type": "string", "enum": list(STATE_BASES)},
    },
    "required": ["category", "confidence", "evidence_quote", "reasoning", "china_sources_cited",
                 "independent_confirmation_present", "confirmation_evidence", "issuer_type", "state_basis"],
    "additionalProperties": False,
}


def build_user_message(title: str, body: str, lookalike: Optional[Dict] = None) -> str:
    """Headline and body only. No outlet, no country, no URL, no author of the article itself. An
    article the rules sent because it nearly repeats a state origin item (pipeline/lookalike.py) also
    carries that item's headline and outlet, the other outlet's and never its own, as context."""
    body = (body or "").strip()
    if len(body) > config.LLM_BODY_CHAR_LIMIT:
        body = body[:config.LLM_BODY_CHAR_LIMIT] + "\n[text truncated at %d characters]" % config.LLM_BODY_CHAR_LIMIT
    context = ""
    if lookalike and lookalike.get("title"):
        context = ("Context, not part of the article: another outlet (%s) published a nearly identical item, headlined "
                   "\"%s\", which this study coded as state origin. Decide whether this article is that state text with "
                   "no substantive alteration.\n\n" % (lookalike.get("outlet") or "unnamed", lookalike["title"].strip()))
    return "Headline: %s\n\nArticle text:\n%s\n\n%sClassify this article. Return only the JSON object." % (
        (title or "").strip(), body, context)


def lookalike_context(row) -> Optional[Dict]:
    """The lookalike match the rules recorded in an articles row's llm_trigger, or None."""
    from pipeline import lookalike
    try:
        return lookalike.context_of(row["llm_trigger"])
    except (IndexError, KeyError):
        return None


def request_params(title: str, body: str, lookalike: Optional[Dict] = None) -> Dict:
    return {
        "model": config.LLM_MODEL,
        "max_tokens": config.LLM_MAX_TOKENS,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": build_user_message(title, body, lookalike)}],
        "output_config": {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    }


def parse_response(message) -> Optional[Dict]:
    """Return the parsed dict, or None with the failure reason in ['_error'].

    Every return carries ['_usage'], whether or not the content parsed. The API bills a refusal, a
    reply cut off at max_tokens and malformed JSON exactly like a usable answer, and a truncated
    reply is the most expensive kind of all because it burns the whole output budget. Attaching
    usage only on the success path recorded those calls at zero tokens and zero dollars, so the
    monthly budget was spent partly out of sight."""
    usage = getattr(message, "usage", None)

    def out(data: Dict) -> Dict:
        data["_usage"] = usage
        return data

    stop = getattr(message, "stop_reason", None)
    if stop == "refusal":
        return out({"_error": "refusal"})
    # A reply cut off at max_tokens cannot be valid JSON, and would otherwise be counted as malformed
    # output rather than as a budget that was too small for a long evidence quote.
    if stop == "max_tokens":
        return out({"_error": "truncated", "_raw": getattr(getattr(message, "content", [None])[0], "text", None)})
    text = None
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text = block.text
            break
    if not text:
        return out({"_error": "no_text"})
    try:
        data = json.loads(text)
    except ValueError:
        return out({"_error": "invalid_json", "_raw": text})
    if data.get("category") not in ("A", "B", "C", "not_relevant"):
        return out({"_error": "bad_category", "_raw": text})
    try:
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        data["confidence"] = 0.0
    data["issuer_type"] = issuer_type_of(data)
    data["state_basis"] = state_basis_of(data)
    data["_raw"] = text
    return out(data)


def _field_of(data: Dict, field: str, allowed) -> Optional[str]:
    """data[field] when it is one of allowed; else the same field from the reply text in data['_raw'] (a
    session label passes its whole line there, pipeline.labels._data); else None. A reply made under a
    prompt older than 2026-10-17 has neither field, and gives None."""
    value = data.get(field)
    if value is None and isinstance(data.get("_raw"), str):
        try:
            raw = json.loads(data["_raw"])
        except ValueError:
            raw = None
        value = raw.get(field) if isinstance(raw, dict) else None
    return value if isinstance(value, str) and value in allowed else None


def issuer_type_of(data: Dict) -> Optional[str]:
    """The issuer_type of a parsed reply, one of ISSUER_TYPES, or None when it gives none or another value."""
    return _field_of(data, "issuer_type", ISSUER_TYPES)


def state_basis_of(data: Dict) -> Optional[str]:
    """The state_basis of a parsed reply, one of STATE_BASES, or None when it gives none or another value."""
    return _field_of(data, "state_basis", STATE_BASES)


def book_usage(conn, message, calls: int = 0, batched: bool = False, date: Optional[str] = None) -> Dict[str, int]:
    """Record what one reply cost, parsed or not. The single place both the synchronous path and
    the batch collector book tokens, so the two cannot drift apart again."""
    usage = getattr(message, "usage", None)
    tokens = llm_cost.usage_tokens(usage)
    store.record_llm_usage(conn, calls, tokens["input_tokens"], tokens["output_tokens"],
                           cache_creation_tokens=tokens["cache_creation_tokens"],
                           cache_read_tokens=tokens["cache_read_tokens"],
                           cost_usd=llm_cost.usage_cost(usage, batched=batched), date=date)
    return tokens


class DryRunClient:
    """Fake client for tests and dry runs. Never touches the network."""

    class _Messages:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **params):
            self.outer.calls.append(params)
            body = params["messages"][0]["content"]
            cat = self.outer.answer(body)
            payload = {"category": cat, "confidence": 0.9 if cat != "B" else 0.7,
                       "evidence_quote": body.split("\n")[0][:120], "reasoning": "Dry run.",
                       "china_sources_cited": [], "independent_confirmation_present": cat == "C",
                       "confirmation_evidence": None,
                       "issuer_type": "state_media" if cat == "A" else "none",
                       "state_basis": "credit_line" if cat == "A" else "none"}

            class Block:
                type = "text"
                text = json.dumps(payload)

            class Usage:
                input_tokens = 100
                output_tokens = 50
                cache_creation_input_tokens = 0
                cache_read_input_tokens = 0

            class Msg:
                stop_reason = "end_turn"
                content = [Block()]
                usage = Usage()
                model = "dry-run"
            return Msg()

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer or (lambda body: "C")
        self.messages = DryRunClient._Messages(self)


class NoApiKey(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# The account's own usage limit
# ---------------------------------------------------------------------------
# When the Anthropic account reaches the usage or spend limit set on it, every request fails with
# BadRequestError 400 "You have reached your specified API usage limits. You will regain access on
# 2026-11-01 at 00:00 UTC." That error used to leave classify_llm.run from _submit_batch, which failed
# the workflow step and skipped export, the commit, the snapshot and the Pages deploy, so the site
# froze. It is now treated like a spent budget: no further call this run, every article left waiting,
# the stage finished normally, and the date access returns is kept (llm_provider_limit, one row) so later
# runs make no request at all before it. The export shows it as meta.llm_budget.provider_limit.
# The owner can raise the limit before that date, so once PROVIDER_LIMIT_RECHECK has passed since the
# limit was recorded a run tries again: a refused request costs nothing and records the limit afresh,
# and a request that goes through clears it.
PROVIDER_LIMIT_RECHECK = dt.timedelta(hours=6)
_LIMIT_ERRORS = ("BadRequestError", "PermissionDeniedError", "RateLimitError")
_LIMIT_PHRASES = ("usage limit", "spend limit", "spending limit", "credit balance")
_REGAIN = re.compile(r"regain access on (\d{4}-\d{2}-\d{2})(?:\s+at\s+(\d{1,2}:\d{2}))?", re.I)


def provider_limit_of(exc, now: Optional[dt.datetime] = None) -> Optional[Dict]:
    """{"until", "message"} when exc is the account's usage, spend or credit limit, else None. A
    BadRequestError, PermissionDeniedError or RateLimitError (or a subclass) whose message names the
    limit, or whose error type is about billing or credit. "until" is the "regain access on" time in the
    message (UTC), or, when it names none, the next UTC midnight, so the pause costs at most one failed
    request a day. Every other error gives None and keeps its usual handling."""
    if not any(c.__name__ in _LIMIT_ERRORS for c in type(exc).__mro__):
        return None
    body = getattr(exc, "body", None)
    err = body.get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    message = str(err.get("message") or exc)
    kind = str(err.get("type") or "").lower()
    if not (any(p in message.lower() for p in _LIMIT_PHRASES) or "billing" in kind or "credit" in kind):
        return None
    m = _REGAIN.search(message)
    until = None
    if m:
        try:
            until = dt.datetime.fromisoformat("%sT%s" % (m.group(1), (m.group(2) or "00:00").zfill(5))).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            until = None
    if until is None:
        now = now or dt.datetime.now(dt.timezone.utc)
        until = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"until": until.isoformat(), "message": message[:300]}


def record_provider_limit(conn, limit: Dict) -> None:
    """Keep the account limit and the time access returns, replacing any earlier one."""
    conn.execute("""CREATE TABLE IF NOT EXISTS llm_provider_limit (id INTEGER PRIMARY KEY CHECK (id = 1),
                    until TEXT NOT NULL, message TEXT, recorded_at TEXT NOT NULL)""")
    conn.execute("INSERT OR REPLACE INTO llm_provider_limit(id, until, message, recorded_at) VALUES (1, ?, ?, ?)",
                 (limit["until"], limit.get("message"), store.utcnow()))
    conn.commit()


def active_provider_limit(conn, now: Optional[dt.datetime] = None, recheck: bool = False) -> Optional[Dict]:
    """The recorded account limit while it still holds ({"until", "message", "recorded_at"}), or None:
    none recorded, or its time has passed. With recheck (the stages that would call), also None once
    PROVIDER_LIMIT_RECHECK has passed since it was recorded, so one run tries again. Reads only; a
    database without the table has none."""
    import sqlite3
    try:
        row = conn.execute("SELECT until, message, recorded_at FROM llm_provider_limit WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    try:
        until = dt.datetime.fromisoformat(row[0])
    except (TypeError, ValueError):
        return None
    until = until if until.tzinfo else until.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    if until <= now:
        return None
    if recheck:
        try:
            recorded = dt.datetime.fromisoformat(row[2])
            recorded = recorded if recorded.tzinfo else recorded.replace(tzinfo=dt.timezone.utc)
            if now - recorded >= PROVIDER_LIMIT_RECHECK:
                return None
        except (TypeError, ValueError):
            return None
    return {"until": row[0], "message": row[1], "recorded_at": row[2]}


def clear_provider_limit(conn) -> None:
    """A request went through, so the account limit no longer holds (the owner raised it)."""
    import sqlite3
    try:
        if conn.execute("DELETE FROM llm_provider_limit").rowcount:
            conn.commit()
    except sqlite3.OperationalError:
        pass


def _paused(conn, log_id, counts: Dict, limit: Dict) -> Dict:
    """Finish a classify_llm stage the account limit stops: no call, the free work done (stranded batch
    articles returned, near-duplicate copies), every other article left waiting, the stage a success."""
    expired = expire_stale_batches(conn)
    if expired["batches_expired"] or expired["articles_returned"] or expired["orphans_returned"]:
        counts["expired_batches"] = expired
    for r in pending_articles(conn, 100000):
        if _copy_from_dup_group(conn, r):
            counts["copied"] = counts.get("copied", 0) + 1
            conn.commit()
    counts["pending"] = conn.execute("SELECT COUNT(*) FROM articles WHERE status='awaiting_llm'").fetchone()[0]
    counts["skipped"] = "provider_limit"
    counts["provider_limit"] = {"until": limit["until"], "message": limit.get("message")}
    conn.commit()
    store.finish_stage(conn, log_id, True, counts,
                       notes="model calls paused by the account usage limit until %s" % limit["until"])
    return counts


def make_client(dry_run: bool = False):
    if dry_run:
        return DryRunClient()
    # GitHub Actions passes an unset secret as an empty string; the SDK would accept the
    # empty key at construction and fail obscurely on the first request.
    if not config._env("ANTHROPIC_API_KEY", ""):
        raise NoApiKey("ANTHROPIC_API_KEY is not set; the verification stage is skipped")
    import anthropic
    return anthropic.Anthropic(api_key=config._env("ANTHROPIC_API_KEY", ""))


def _collapse_dup_groups(rows: List) -> List:
    """Collapse a to-do list to at most one representative row per non-null dup_group_id, in the
    order given. The rest are simply left out; they stay at 'awaiting_llm' and are picked up by
    _settle_pending_dup_siblings once the representative has a label, or by the ordinary
    _copy_from_dup_group sweep on a later run if the representative is never drawn this run."""
    seen = set()
    representatives = []
    for r in rows:
        gid = r["dup_group_id"]
        if gid:
            if gid in seen:
                continue
            seen.add(gid)
        representatives.append(r)
    return representatives


def _settle_pending_dup_siblings(conn, article_id: int) -> int:
    """After article_id gets a fresh (non-copied) label, copy it into every other still-pending
    ('awaiting_llm') member of its near-duplicate group, via the existing _copy_from_dup_group
    path. Returns the number settled. Used both after a synchronous classification and after a
    batch collection, so a held-back sibling never has to wait for a separate run to be copied for
    free."""
    row = conn.execute("SELECT dup_group_id FROM articles WHERE id=?", (article_id,)).fetchone()
    gid = row["dup_group_id"] if row else None
    if not gid:
        return 0
    siblings = conn.execute(
        "SELECT * FROM articles WHERE dup_group_id=? AND id<>? AND status='awaiting_llm'",
        (gid, article_id),
    ).fetchall()
    settled = 0
    for sib in siblings:
        if _copy_from_dup_group(conn, sib):
            settled += 1
            conn.commit()
    return settled


def _settle_stale_dup_siblings(conn, article_id: int) -> int:
    """After article_id gets a label under the current prompt, copy it into every sibling of its
    near-duplicate group whose current label is a model label made under an older prompt, or made on
    a stub since replaced (body_refreshed) before this label, so one call relabels the whole group. A
    no-op when article_id's own label is on an older prompt. Returns the number settled."""
    row = conn.execute("SELECT dup_group_id FROM articles WHERE id=?", (article_id,)).fetchone()
    gid = row["dup_group_id"] if row else None
    if not gid:
        return 0
    siblings = conn.execute(
        """SELECT a.*, c.model_version AS label_model, c.body_refreshed AS label_refreshed FROM articles a
           JOIN classifications c ON c.article_id=a.id AND c.is_current=1 AND c.method='llm'
           WHERE a.dup_group_id=? AND a.id<>? AND a.status='classified'""",
        (gid, article_id),
    ).fetchall()
    settled = 0
    for sib in siblings:
        if on_current_prompt(sib["label_model"]) and not sib["label_refreshed"]:
            continue
        if _copy_from_dup_group(conn, sib, current_prompt_only=True, refreshed_after=_refreshed_at(sib)):
            settled += 1
            conn.commit()
    return settled


# A relabel that failed (a refusal, a reply cut off or malformed) is recorded in fail_reason with this
# prefix and not offered again, so one article cannot take a call on every run. Its old label stays.
RELABEL_FAILED = "relabel_failed:"


def _live_batch_ids(conn) -> set:
    """Article ids in classification batches still waiting to be collected."""
    live = set()
    for row in conn.execute("SELECT article_ids FROM llm_batches WHERE status='submitted' AND kind='classify'"):
        try:
            live.update(json.loads(row["article_ids"] or "[]"))
        except ValueError:
            pass
    return live


# The order the relabel queue works in: one tier after another, oldest label first within each tier.
# The issuer rule of 2026-10-17 moves state origin labels the model gave on no credit, stamp or byline
# more than anything else: the label accuracy audit found private company, university and sports club
# releases among them (about 390 placements), so model state origin labels whose route is unattributed,
# sponsored_disclosure or uncredited_copy go first (A_model_weak; a model state origin label with no
# route yet counts as unattributed). Unchecked state sourcing (B) comes next: the factual-claim rule of
# 2026-10-12 moves it to independent journalism more than anything else, and oldest first puts the
# labels made before that rule, which over-called positions as facts, ahead of those made under it.
# A label of any other category made on a stub since replaced by the article (body_refreshed,
# classify_rules.note_body_refresh) comes third, whatever its prompt: the model never read the text,
# credit line included, so the label says little, and the Straits Times paid releases and Antara
# English releases it covers are state origin candidates. Next come independent journalism (C) labels
# the old rule may have under-called: the model recorded official Chinese sourcing (a sourcing
# trigger in llm_trigger, or a name in china_sources_cited) and no independent confirmation. Then
# the other state origin labels (A: wire_credit, distribution_stamp, diplomatic_byline). Every other
# label (the remaining C labels, not about China) comes last.
RELABEL_PRIORITY = ("A_model_weak", "B", "body_refreshed", "C_official_unconfirmed", "A")
# The routes of a model state origin label that rest on no credit line, distribution stamp or byline.
WEAK_MODEL_ROUTES = ("unattributed", "sponsored_disclosure", "uncredited_copy")


def _refreshed_at(r) -> Optional[str]:
    """When the body under a relabel candidate's label was replaced (the time in its body_refreshed
    marker), or None when it was not."""
    marker = r["label_refreshed"] if "label_refreshed" in r.keys() else None
    return marker.split(" ", 1)[0] if marker else None


def _relabel_tier(r) -> int:
    """Position of a relabel candidate's current label in RELABEL_PRIORITY; len() for the rest."""
    key = r["label_category"]
    route = r["label_route"] if "label_route" in r.keys() else None
    if key == "A" and (route or "unattributed") in WEAK_MODEL_ROUTES:
        return RELABEL_PRIORITY.index("A_model_weak")
    if key != "B" and _refreshed_at(r):
        key = "body_refreshed"
    if key == "C" and not r["label_confirmed"]:
        try:
            trig = json.loads(r["llm_trigger"] or "{}") or {}
            cited = json.loads(r["label_sources"] or "[]") or []
        except (ValueError, TypeError):
            trig, cited = {}, []
        if (isinstance(trig, dict) and trig.get("triggers")) or cited:
            key = "C_official_unconfirmed"
    return RELABEL_PRIORITY.index(key) if key in RELABEL_PRIORITY else len(RELABEL_PRIORITY)


def relabel_candidates(conn) -> List:
    """Articles whose current label is a model label made under an older prompt (PROMPT_VERSION), or
    made on a stub since replaced by the article (body_refreshed, whatever its prompt),
    in RELABEL_PRIORITY order, oldest label first within each tier. Their label stays current, and their status stays classified, until the new
    label is stored; nothing here withdraws a label. Left out: an article already in a batch waiting
    to be collected, one whose relabel failed before (RELABEL_FAILED), and one whose body is not on
    disk and cannot be fetched again by this collector, which would otherwise sit at the head of the
    queue on every run."""
    from pipeline import registry
    from pipeline.classify_rules import REFETCH_ATTEMPT_LIMIT
    live = _live_batch_ids(conn)
    owners = {o["id"]: registry.fetcher_of(o) for o in registry.load_outlets()}   # who can refetch the page
    rows = conn.execute(
        """SELECT a.*, c.model_version AS label_model, c.classified_at AS label_at,
                  c.category AS label_category, c.china_sources_cited AS label_sources,
                  c.independent_confirmation_present AS label_confirmed,
                  c.body_refreshed AS label_refreshed, c.route AS label_route FROM articles a
           JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE c.method='llm' AND a.status='classified'
             AND (a.fail_reason IS NULL OR substr(a.fail_reason, 1, ?)<>?)
           ORDER BY c.classified_at ASC, a.id ASC""",
        (len(RELABEL_FAILED), RELABEL_FAILED),
    ).fetchall()
    out = []
    for r in rows:
        if (on_current_prompt(r["label_model"]) and not r["label_refreshed"]) or r["id"] in live:
            continue
        if not store.body_path(r["url_hash"]).exists():
            mine = owners.get(r["outlet_id"], config.COLLECTOR) == config.COLLECTOR
            if (r["fetch_attempts"] or 0) >= REFETCH_ATTEMPT_LIMIT or not mine:
                continue
        out.append(r)
    out.sort(key=_relabel_tier)  # stable: oldest label first within each tier
    return out


def relabel_calls_today(conn, today: str) -> int:
    """Relabel calls the classify_llm runs of this UTC day have already made or submitted."""
    row = conn.execute(
        """SELECT COALESCE(SUM(json_extract(counts, '$.relabel_sent')), 0) FROM run_log
           WHERE stage='classify_llm' AND substr(started_at, 1, 10)=? AND json_valid(counts)""",
        (today,),
    ).fetchone()
    return int(row[0] or 0)


def relabel_allowance(ceiling: int, used_today: int, now: dt.datetime, share: Optional[float] = None) -> int:
    """Relabel calls this run may make: config.LLM_RELABEL_DAY_SHARE of the day's binding cap, paced
    over the UTC day so the hourly runs late in the day keep room for the new articles that arrive
    then, less what the day's earlier runs already sent. The hour in progress counts as elapsed, so
    the first run after midnight is not shut out."""
    share = config.LLM_RELABEL_DAY_SHARE if share is None else share
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = min(1.0, (now - midnight).total_seconds() / 86400.0 + 1.0 / 24)
    return max(0, int(ceiling * share * elapsed) - used_today)


def _copy_from_dup_group(conn, row, current_prompt_only: bool = False, refreshed_after: Optional[str] = None) -> bool:
    """If another placement of the same underlying item already has an LLM label, copy it.
    current_prompt_only copies only a label made under the current prompt: a relabel must not copy
    a sibling's label that is as old as its own. refreshed_after (the time row's body was replaced,
    _refreshed_at) copies only a label made since, which read a body as current as row's. A label
    itself marked body_refreshed rests on a stub and is never copied."""
    if not row["dup_group_id"]:
        return False
    others = conn.execute(
        """SELECT c.* FROM classifications c JOIN articles a ON a.id=c.article_id
           WHERE a.dup_group_id=? AND a.id<>? AND c.is_current=1 AND c.method='llm' ORDER BY c.id DESC""",
        (row["dup_group_id"], row["id"]),
    ).fetchall()
    other = next((o for o in others
                  if (not current_prompt_only or on_current_prompt(o["model_version"]))
                  and not ("body_refreshed" in o.keys() and o["body_refreshed"])
                  and (not refreshed_after or (o["classified_at"] or "") >= refreshed_after)), None)
    if not other:
        return False
    store.insert_classification(
        conn, row["id"], "llm", other["category"], other["confidence"],
        evidence_quote=other["evidence_quote"],
        reasoning="[copied from article %d, same near-duplicate group] %s" % (other["article_id"], other["reasoning"] or ""),
        china_sources_cited=json.loads(other["china_sources_cited"] or "[]"),
        independent_confirmation_present=other["independent_confirmation_present"],
        confirmation_evidence=other["confirmation_evidence"], model_version=other["model_version"],
        raw_response=other["raw_response"], ruleset_version=other["ruleset_version"],
        # The label's route and issuer go with it. Left empty, the route of a copied state origin label
        # was filled later from this placement's own candidate signatures (classify_rules.ensure_routes),
        # which is how a weak signature's route came to stand under a label the model gave.
        route=other["route"] if other["category"] == "A" else None,
        issuer_type=other["issuer_type"] if "issuer_type" in other.keys() else None,
        needs_review=other["needs_review"] if "needs_review" in other.keys() else None,
    )
    return True


def _model_route(conn, article_id: int, data: Optional[Dict] = None) -> str:
    """Route of a state origin label the model gave, from the basis the model stated (state_basis,
    BASIS_ROUTES), or unattributed. Never from the candidate signatures that sent the article: a weak
    signature is why the model was asked, not what the model found, and inheriting it put a GAC
    factory release under the diplomatic byline route. Two checks against the record:
    diplomatic_byline stands only with a diplomatic issuer, and uncredited_copy only when the
    lookalike check sent the article (its context note is what context_match points at). On an
    article the lookalike check sent, a label that names no credit, stamp, disclosure or byline
    (issuer_named, none, or a reply from before state_basis existed) is uncredited_copy, as
    classify_rules.ensure_routes would make it."""
    from pipeline.classify_rules import UNCREDITED_COPY, _trigger_of
    data = data or {}
    basis = state_basis_of(data)
    route = BASIS_ROUTES.get(basis) or "unattributed"
    if route == "diplomatic_byline" and issuer_type_of(data) not in (None, "diplomatic"):
        route = "unattributed"
    row = conn.execute("SELECT llm_trigger FROM articles WHERE id=?", (article_id,)).fetchone()
    lookalike = isinstance(_trigger_of(row["llm_trigger"] if row else None).get("lookalike"), dict)
    if route == UNCREDITED_COPY and not lookalike:
        route = "unattributed"
    if lookalike and route == "unattributed":
        route = UNCREDITED_COPY
    return route


def store_result(conn, article_id: int, data: Dict, model_version: str,
                 prompt_version: Optional[str] = PROMPT_VERSION) -> Optional[str]:
    """Store a model label. The prompt it was made under is recorded in model_version
    (with_prompt_version); pipeline.labels passes prompt_version=None, because a session label names
    its own prompt or, naming none, is taken to predate the current one. Returns the inconsistency of
    the reply (inconsistency_of), stored in needs_review, or None; a state origin reply with no state
    issuer is stored as C."""
    flaw = inconsistency_of(data)
    category, reasoning = data["category"], data.get("reasoning")
    if flaw == STATE_ORIGIN_WITHOUT_STATE_ISSUER:
        category = "C"
        reasoning = "[inconsistent reply: state origin with issuer type %s, stored as independent journalism for review] %s" % (
            issuer_type_of(data), reasoning or "")
    elif flaw:
        reasoning = "[inconsistent reply: %s with issuer type %s, kept for review] %s" % (
            category, issuer_type_of(data), reasoning or "")
    store.insert_classification(
        conn, article_id, "llm", category, data["confidence"],
        evidence_quote=data.get("evidence_quote"), reasoning=reasoning,
        china_sources_cited=data.get("china_sources_cited") or [],
        independent_confirmation_present=data.get("independent_confirmation_present"),
        confirmation_evidence=data.get("confirmation_evidence"),
        model_version=with_prompt_version(model_version, prompt_version),
        raw_response=data.get("_raw"),
        route=_model_route(conn, article_id, data) if category == "A" else None,
        issuer_type=issuer_type_of(data), needs_review=flaw,
    )
    return flaw


def pending_articles(conn, limit: int) -> List:
    return conn.execute(
        "SELECT * FROM articles WHERE status='awaiting_llm' ORDER BY discovered_at ASC, id ASC LIMIT ?", (limit,)
    ).fetchall()


def draw(rows: List, k: int, seed: str) -> Tuple[List, Dict[str, List[int]]]:
    """Stratified random draw of k rows across countries.

    Every country gets the same sampling fraction of its eligible rows; the rounding remainder
    goes to the largest fractional parts, ties broken at random. First come first served would
    sample by time zone: the countries whose articles arrive late in the UTC day would be the ones
    the ceiling truncates. Rows come back in random order, so a run that stops at its deadline
    also stops at random. Returns the chosen rows and {country: [eligible, drawn]}."""
    rng = random.Random(seed)
    by_country = defaultdict(list)
    for r in rows:
        by_country[r["country"]].append(r)
    total = len(rows)
    k = max(0, min(k, total))
    quota, remainders = {}, []
    for c in sorted(by_country):
        exact = len(by_country[c]) * k / float(total)
        quota[c] = int(exact)
        remainders.append((exact - quota[c], rng.random(), c))
    for _, _, c in sorted(remainders, reverse=True)[:k - sum(quota.values())]:
        quota[c] += 1
    chosen = []
    for c in sorted(by_country):
        pool = list(by_country[c])
        rng.shuffle(pool)
        chosen.extend(pool[:quota[c]])
    rng.shuffle(chosen)
    return chosen, {c: [len(by_country[c]), quota[c]] for c in sorted(by_country)}


def _discovered(row) -> Optional[dt.datetime]:
    """discovered_at as an aware UTC datetime, or None when it cannot be read."""
    text = (row["discovered_at"] or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        when = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def split_pools(rows: List, api_first: Dict[str, str], now: dt.datetime,
                grace_days: Optional[int] = None) -> Tuple[List, List, int]:
    """Split the to-do list for the API between the two routes that label waiting articles.

    The interactive session (pipeline.labels) refetches article texts on the owner's machine at no
    API cost, and the API budget is kept for what it cannot reach. Returns (api_first, grace, held):
    the rows listed in data/labels/api_first.jsonl, whose text the session could not fetch; the
    other rows discovered more than grace_days ago (config.LLM_SESSION_GRACE_DAYS by default); and
    how many younger rows are held for the session and not offered to the API this run. A row whose
    discovery time cannot be read is not held, so nothing can wait forever. grace_days 0 holds
    nothing."""
    grace_days = config.LLM_SESSION_GRACE_DAYS if grace_days is None else grace_days
    cutoff = now - dt.timedelta(days=grace_days)
    first, grace, held = [], [], 0
    for r in rows:
        if r["url_hash"] in api_first:
            first.append(r)
            continue
        when = _discovered(r) if grace_days > 0 else None
        if when is not None and when >= cutoff:
            held += 1
        else:
            grace.append(r)
    return first, grace, held


def run(conn, run_id: str, deadline: Optional[float] = None, batch: bool = False, dry_run: bool = False,
        client=None, max_consecutive_errors: int = 3) -> Dict:
    """Run one classify_llm stage.

    CHANGELOG (no RULESET_VERSION/GATE_VERSION bump: this changes which calls are made, not what
    any label means). Near-duplicate members of one dup_group_id are normally labelled once and
    copied to their siblings for free via _copy_from_dup_group. That copy only sees a label
    already recorded before *this* row was checked, so when several still-unlabelled members of
    the same group were drawn into the same run or the same batch submission, none of them had a
    label yet at submission time and all went out as separate, billable calls, about 3.5% of a
    month's model calls, purely mechanical waste. The to-do list is now collapsed to at most one
    representative per dup_group_id before draw() runs, so only the representative is ever drawn
    or submitted and only it counts against the daily ceiling and the per-country draw quotas; the
    held-back siblings stay queued at 'awaiting_llm' and are settled, via the same
    _copy_from_dup_group path, the moment the representative gets a label: synchronously right
    after it is classified, or after a later run collects the batch that classified it.
    """
    log_id = store.start_stage(conn, run_id, "classify_llm")
    counts = {"pending": 0, "classified": 0, "copied": 0, "errors": 0, "ceiling_hit": False,
              "calls": 0, "batch_submitted": 0, "batch_collected": 0, "skipped_deadline": 0}
    # Two caps, the lower binds: the daily call ceiling, and the calls today's share of the monthly
    # budget pays for. The budget cap depends only on days before today, so every run in a day sees
    # the same figure and the hourly runs cannot each spend a day's allowance.
    budget = llm_cost.daily_cap(conn, batched=batch)
    counts["budget"] = budget
    ceiling = config.LLM_DAILY_CALL_CEILING
    if budget["cap"] is not None and budget["cap"] < ceiling:
        ceiling = budget["cap"]
        counts["budget_bound"] = True
    used = store.llm_calls_today(conn)
    remaining = max(0, ceiling - used)
    # The account's usage limit, recorded by an earlier run, holds until a known time: no request at all
    # before it, not even one that would fail.
    limit = None if dry_run else active_provider_limit(conn, recheck=True)
    if limit:
        return _paused(conn, log_id, counts, limit)
    if client is None:
        try:
            client = make_client(dry_run=dry_run)
        except NoApiKey as exc:
            counts["pending"] = conn.execute("SELECT COUNT(*) FROM articles WHERE status='awaiting_llm'").fetchone()[0]
            counts["skipped"] = "no_api_key"
            store.finish_stage(conn, log_id, True, counts, notes=str(exc))
            return counts
        except Exception as exc:
            store.finish_stage(conn, log_id, False, counts, notes="client init failed: %s: %s" % (type(exc).__name__, exc))
            counts["error"] = "client_init_failed"
            return counts

    # Results of an earlier batch are collected on any run, not only on another batch run. A
    # submission would otherwise sit uncollected while the hourly synchronous runs ignored it.
    if not dry_run and conn.execute("SELECT COUNT(*) FROM llm_batches WHERE status='submitted' AND kind='classify'").fetchone()[0]:
        try:
            counts.update(_collect_batches(conn, client))
        except Exception as exc:
            # A failure of the Batches API is recorded and the run goes on. Letting it out of here
            # failed the whole workflow step, which skipped export, the commit, the snapshot and
            # the Pages deploy: the site stopped updating because one batch could not be read.
            counts["collect_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
            limit = provider_limit_of(exc)
            if limit:
                record_provider_limit(conn, limit)
                counts["provider_limit"] = limit
        if counts.get("provider_limit"):
            return _paused(conn, log_id, counts, counts["provider_limit"])
    # Articles stranded by a batch that will never be collected come back here, before the pending
    # list is read, so they are classified rather than waiting forever while counting as pending.
    expired = expire_stale_batches(conn)
    if expired["batches_expired"] or expired["articles_returned"] or expired["orphans_returned"]:
        counts["expired_batches"] = expired

    # Near-duplicate copies cost nothing and do not count against the ceiling.
    rows = pending_articles(conn, 100000)
    counts["pending"] = len(rows)
    todo = []
    for r in rows:
        if _copy_from_dup_group(conn, r):
            counts["copied"] += 1
            conn.commit()
        else:
            todo.append(r)
    # Several still-unlabelled members of one dup_group_id must not all be drawn or submitted this
    # run: none would have a label yet to copy from, so all would go out as separate real calls.
    # Only one representative per group is offered to draw(); the rest stay at 'awaiting_llm' and
    # are settled for free by _settle_pending_dup_siblings once the representative has a label.
    todo = _collapse_dup_groups(todo)
    # The API budget is for the articles the session route (pipeline.labels) cannot reach. Those
    # listed in data/labels/api_first.jsonl, whose text the session could not fetch, are drawn
    # first; then, if the budget allows, the others that have waited longer than the grace period.
    # Younger articles are held for the session and not offered to the API at all this run.
    from pipeline import labels as labels_mod
    now = dt.datetime.now(dt.timezone.utc)
    af_stats = {}
    api_first = labels_mod.load_api_first(stats=af_stats)
    if af_stats.get("malformed"):
        counts["api_first_malformed"] = af_stats["malformed"]
        counts["api_first_malformed_samples"] = af_stats["malformed_samples"]
    first_pool, grace_pool, held = split_pools(todo, api_first, now)
    eligible = len(first_pool) + len(grace_pool)
    counts.update({"api_first_eligible": len(first_pool), "api_first_sent": 0,
                   "grace_eligible": len(grace_pool), "grace_sent": 0, "held_for_session": held})
    # When the ceiling binds, the articles sent are a stratified random draw across countries within
    # each pool and the allocation is recorded; otherwise every eligible article is sent, in random
    # order within its pool. In synchronous mode, later members of a near-duplicate group still copy
    # from a member classified earlier in this run.
    today = now.date().isoformat()
    binding = remaining < eligible
    chosen_first, alloc_first = draw(first_pool, remaining, "llm-draw-api-first-%s" % today)
    chosen_grace, alloc_grace = draw(grace_pool, remaining - len(chosen_first), "llm-draw-%s" % today)
    chosen = chosen_first + chosen_grace
    allocation = {}
    for alloc in (alloc_first, alloc_grace):
        for c, (e, d) in alloc.items():
            prev = allocation.get(c, [0, 0])
            allocation[c] = [prev[0] + e, prev[1] + d]
    first_ids = {r["id"] for r in chosen_first}
    if binding:
        counts["ceiling_hit"] = True
        counts["draw"] = {"eligible": eligible, "drawn": len(chosen), "countries": len(allocation)}
    # Model labels made under an older prompt are judged again, in RELABEL_PRIORITY order, with what the new
    # articles leave of this run's calls, and never more than config.LLM_RELABEL_DAY_SHARE of the
    # day's cap, paced over the day (relabel_allowance). The old label stays current, and the
    # article stays classified, until the new label is stored, so no article is ever left unlabelled.
    # A failure here costs the relabels of this run, never the new articles.
    try:
        relabel_pool = _collapse_dup_groups(relabel_candidates(conn))
    except Exception as exc:
        relabel_pool = []
        counts["relabel_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    relabel_allow = relabel_allowance(ceiling, relabel_calls_today(conn, today), now)
    chosen_relabel = relabel_pool[:max(0, min(relabel_allow, remaining - len(chosen)))]
    relabel_ids = {r["id"] for r in chosen_relabel}
    counts.update({"prompt_version": PROMPT_VERSION, "relabel_eligible": len(relabel_pool),
                   "relabel_allowance": relabel_allow, "relabel_sent": 0, "relabelled": 0, "relabel_copied": 0,
                   "relabel_body_refreshed": sum(1 for r in relabel_pool if _refreshed_at(r))})

    if batch and not dry_run:
        submitted = []
        try:
            n = _submit_batch(conn, client, chosen + chosen_relabel, submitted_ids=submitted, relabel_ids=relabel_ids)
        except Exception as exc:
            # The account limit stops the run like a spent budget; nothing was submitted, so every
            # article keeps its status. Any other error leaves here as before.
            limit = provider_limit_of(exc)
            if not limit:
                raise
            record_provider_limit(conn, limit)
            return _paused(conn, log_id, counts, limit)
        counts["batch_submitted"] = n
        if n:
            clear_provider_limit(conn)
        counts["relabel_sent"] = sum(1 for aid in submitted if aid in relabel_ids)
        counts["api_first_sent"] = sum(1 for aid in submitted if aid in first_ids)
        counts["grace_sent"] = n - counts["api_first_sent"] - counts["relabel_sent"]
        store.record_llm_usage(conn, n, 0, 0, ceiling_hit=binding)
        if binding:
            store.record_llm_sampling(conn, today, allocation)
        conn.commit()
        store.finish_stage(conn, log_id, True, counts, ceiling_hit=binding,
                           notes="%s %d reached; %d left pending after a stratified draw"
                                 % ("budget cap" if counts.get("budget_bound") else "ceiling", ceiling,
                                    eligible - (n - counts["relabel_sent"])) if binding else "")
        return counts

    sent = defaultdict(int)
    consecutive_errors = 0
    for r in chosen + chosen_relabel:
        relabel = r["id"] in relabel_ids
        if deadline and time.time() > deadline:
            counts["skipped_deadline"] += 1
            continue
        if _copy_from_dup_group(conn, r, current_prompt_only=relabel, refreshed_after=_refreshed_at(r) if relabel else None):
            counts["relabel_copied" if relabel else "copied"] += 1
            conn.commit()
            counts["copied"] += _settle_pending_dup_siblings(conn, r["id"])
            counts["relabel_copied"] += _settle_stale_dup_siblings(conn, r["id"])
            continue
        if remaining <= 0:
            break
        from pipeline.classify_rules import ensure_body
        body = ensure_body(conn, r)
        if not body:
            counts["body_unavailable"] = counts.get("body_unavailable", 0) + 1
            continue
        params = request_params(r["title"], body, lookalike_context(r))
        try:
            message = client.messages.create(**params)
        except Exception as exc:
            limit = provider_limit_of(exc)
            if limit:
                # No further call this run, and none in later runs until access returns.
                record_provider_limit(conn, limit)
                counts["provider_limit"] = limit
                break
            counts["errors"] += 1
            consecutive_errors += 1
            counts["last_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
            if consecutive_errors >= max_consecutive_errors:
                _record_draw(conn, today, binding, allocation, sent)
                store.finish_stage(conn, log_id, False, counts, notes="aborted after repeated API errors: %s: %s" % (type(exc).__name__, str(exc)[:300]))
                return counts
            time.sleep(min(30, 2 ** consecutive_errors))
            continue
        remaining -= 1
        counts["calls"] += 1
        if relabel:
            counts["relabel_sent"] += 1
        else:
            counts["api_first_sent" if r["id"] in first_ids else "grace_sent"] += 1
            sent[r["country"]] += 1
        # Booked before the reply is inspected: the call is billed whether or not it parsed.
        book_usage(conn, message, calls=1)
        if counts["calls"] == 1 and not dry_run:
            clear_provider_limit(conn)
        data = parse_response(message)
        if not data or data.get("_error"):
            kind = (data or {}).get("_error") or "no_response"
            counts["errors"] += 1
            # Which kind, so a failure rate can be diagnosed without re-running: a refusal, a reply cut
            # off at max_tokens, and malformed JSON need different answers, and counting them together
            # told us only that one call in six failed.
            kinds = counts.setdefault("error_kinds", {})
            kinds[kind] = kinds.get(kind, 0) + 1
            if kind in ("invalid_json", "truncated") and "error_sample" not in counts:
                counts["error_sample"] = (data.get("_raw") or "")[-300:]
            consecutive_errors += 1
            if relabel:
                # The old label stands and the article stays classified; it is not offered again.
                store.update_article(conn, r["id"], fail_reason=RELABEL_FAILED + kind)
            elif kind == "refusal":
                store.update_article(conn, r["id"], status="failed", fail_reason="llm_refusal")
            conn.commit()
            continue
        consecutive_errors = 0
        if store_result(conn, r["id"], data, getattr(message, "model", None) or config.LLM_MODEL):
            counts["inconsistent"] = counts.get("inconsistent", 0) + 1
        counts["relabelled" if relabel else "classified"] += 1
        conn.commit()
        counts["copied"] += _settle_pending_dup_siblings(conn, r["id"])
        counts["relabel_copied"] += _settle_stale_dup_siblings(conn, r["id"])
    _record_draw(conn, today, binding, allocation, sent)
    left = conn.execute("SELECT COUNT(*) FROM articles WHERE status='awaiting_llm'").fetchone()[0]
    notes = ("daily %s of %d calls reached; a stratified draw left %d articles pending"
             % ("budget cap" if counts.get("budget_bound") else "ceiling", ceiling, left)) if binding else ""
    if counts.get("provider_limit"):
        counts["pending"] = left
        notes = ("model calls stopped by the account usage limit until %s; %s"
                 % (counts["provider_limit"]["until"], notes)).rstrip("; ")
    store.finish_stage(conn, log_id, True, counts, ceiling_hit=binding, notes=notes)
    return counts


def _record_draw(conn, today: str, binding: bool, allocation: Dict[str, List[int]], sent: Dict[str, int]) -> None:
    """Record a binding draw: the ceiling event, and per country the eligible articles and the calls
    actually made, which fall short of the allocation when a body is missing or the deadline passes."""
    if not binding:
        return
    store.record_llm_usage(conn, 0, 0, 0, ceiling_hit=True)
    store.record_llm_sampling(conn, today, {c: [eligible, sent.get(c, 0)] for c, (eligible, _) in allocation.items()})
    conn.commit()


# ---------------------------------------------------------------------------
# Batch mode
# ---------------------------------------------------------------------------

def _submit_batch(conn, client, rows, submitted_ids: Optional[List[int]] = None, relabel_ids=None) -> int:
    """Submit rows with a body to the Batches API; returns how many. submitted_ids, when given, is
    extended with their article ids. Articles in relabel_ids keep their status and their current
    label while the batch runs; the others move to llm_submitted. The batch row records the prompt
    version, so a batch submitted before a prompt change is stored as made under the older prompt."""
    relabel_ids = relabel_ids or set()
    if not rows:
        return 0
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    requests = []
    ids = []
    from pipeline.classify_rules import ensure_body
    for r in rows:
        body = ensure_body(conn, r)
        if not body:
            continue
        requests.append(Request(custom_id=str(r["id"]), params=MessageCreateParamsNonStreaming(**request_params(r["title"], body, lookalike_context(r)))))
        ids.append(r["id"])
    if not requests:
        return 0
    batch = client.messages.batches.create(requests=requests)
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version) VALUES (?,?,?,?,?)",
                 (batch.id, store.utcnow(), "submitted", json.dumps(ids), with_prompt_version(config.LLM_MODEL)))
    for aid in ids:
        if aid not in relabel_ids:
            store.update_article(conn, aid, status="llm_submitted")
    if submitted_ids is not None:
        submitted_ids.extend(ids)
    return len(ids)


def _collect_batches(conn, client) -> Dict:
    out = {"batch_collected": 0, "batch_errors": 0, "copied": 0}
    # The second rater's study batches carry other ids and are collected by pipeline.second_rater.
    for b in conn.execute("SELECT * FROM llm_batches WHERE status='submitted' AND kind='classify'").fetchall():
        # One batch that cannot be read must not stop the others, and must not stop the run: the
        # workflow step runs under pipefail, so an exception here would skip export, the commit,
        # the snapshot and the Pages deploy, and the site would stop updating because a batch
        # could not be collected. A batch that fails part way stays 'submitted' and is retried on
        # a later run, or expired by expire_stale_batches.
        try:
            info = client.messages.batches.retrieve(b["batch_id"])
            if info.processing_status != "ended":
                continue
            submitted_day = (b["submitted_at"] or "")[:10] or None
            for result in client.messages.batches.results(b["batch_id"]):
                aid = int(result.custom_id)
                rtype = result.result.type
                message = getattr(result.result, "message", None)
                if message is not None:
                    # Booked on the submission day, beside the calls it recorded, at the batch
                    # price. Booked for every reply the API returned, including one that did not
                    # parse and an errored result that still carries a message.
                    book_usage(conn, message, batched=True, date=submitted_day)
                if rtype == "succeeded":
                    data = parse_response(message)
                    if data and not data.get("_error"):
                        # The prompt the batch was submitted under, from its row: a batch from
                        # before PROMPT_VERSION records none and its labels are stored untagged.
                        if store_result(conn, aid, data, getattr(message, "model", None) or b["model_version"],
                                        prompt_version=prompt_version_of(b["model_version"])):
                            out["inconsistent"] = out.get("inconsistent", 0) + 1
                        out["batch_collected"] += 1
                        # A held-back sibling submitted to this or an earlier batch is settled for
                        # free the moment its group's representative is collected, rather than
                        # waiting for the next run's generic _copy_from_dup_group sweep.
                        out["copied"] += _settle_pending_dup_siblings(conn, aid)
                        out["relabel_copied"] = out.get("relabel_copied", 0) + _settle_stale_dup_siblings(conn, aid)
                        continue
                    kind = (data or {}).get("_error") or "no_response"
                elif rtype in ("errored", "expired", "canceled"):
                    kind = rtype
                else:
                    continue
                # The kind, so a batch failure rate can be diagnosed without re-running it.
                kinds = out.setdefault("batch_error_kinds", {})
                kinds[kind] = kinds.get(kind, 0) + 1
                if store.current_classification(conn, aid) is not None:
                    # A relabel: the old label stands and the article stays classified.
                    store.update_article(conn, aid, fail_reason=RELABEL_FAILED + kind)
                else:
                    store.update_article(conn, aid, status="awaiting_llm")
                out["batch_errors"] += 1
            conn.execute("UPDATE llm_batches SET status='collected', collected_at=? WHERE batch_id=?", (store.utcnow(), b["batch_id"]))
            conn.commit()
        except Exception as exc:
            conn.commit()      # keep the results already stored and the usage already booked
            out.setdefault("collect_errors", []).append(
                "%s: %s: %s" % (b["batch_id"], type(exc).__name__, str(exc)[:200]))
            limit = provider_limit_of(exc)
            if limit:
                # The other batches would fail the same way; they are collected once access returns.
                record_provider_limit(conn, limit)
                out["provider_limit"] = limit
                break
    return out


# The Batches API finishes or expires a batch within 24 hours and keeps its results for 29 days.
# A batch still 'submitted' this many days later is not going to be collected, and its articles sit
# at 'llm_submitted', where neither pending_articles nor the collector can see them while they are
# still counted as pending. Well inside the 29 day window, so a batch reclaimed here could still
# have been collected had the API come back.
BATCH_EXPIRY_DAYS = 7


def expire_stale_batches(conn, days: int = BATCH_EXPIRY_DAYS, now: Optional[dt.datetime] = None) -> Dict:
    """Return the articles of batches too old to be collected to 'awaiting_llm', and mark those
    batches failed. Also frees articles left at 'llm_submitted' by a batch row that is gone, which
    nothing else would ever look at again."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(days=days)).isoformat()
    out = {"batches_expired": 0, "articles_returned": 0, "orphans_returned": 0}
    stale = conn.execute(
        "SELECT batch_id, article_ids FROM llm_batches WHERE status='submitted' AND kind='classify' AND submitted_at < ?",
        (cutoff,)).fetchall()
    for b in stale:
        try:
            ids = json.loads(b["article_ids"] or "[]")
        except ValueError:
            ids = []
        for aid in ids:
            cur = conn.execute("UPDATE articles SET status='awaiting_llm' WHERE id=? AND status='llm_submitted'", (aid,))
            out["articles_returned"] += cur.rowcount
        conn.execute("UPDATE llm_batches SET status='failed', collected_at=? WHERE batch_id=?",
                     (store.utcnow(), b["batch_id"]))
        out["batches_expired"] += 1
    live = set()
    for row in conn.execute("SELECT article_ids FROM llm_batches WHERE status='submitted'"):
        try:
            live.update(json.loads(row["article_ids"] or "[]"))
        except ValueError:
            pass
    orphans = [r["id"] for r in conn.execute("SELECT id FROM articles WHERE status='llm_submitted'") if r["id"] not in live]
    for aid in orphans:
        conn.execute("UPDATE articles SET status='awaiting_llm' WHERE id=?", (aid,))
        out["orphans_returned"] += 1
    if out["batches_expired"] or out["orphans_returned"]:
        conn.commit()
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = store.connect()
    print(json.dumps(run(conn, "manual", batch=args.batch, dry_run=args.dry_run)))
