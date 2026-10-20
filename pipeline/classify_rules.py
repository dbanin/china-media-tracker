"""Deterministic Category A detection and LLM routing.

Runs before any LLM call. Transparent and reproducible: every match records
which pattern fired, so any Category A count can be broken down by mechanism.

Decision rule:
  - any strong signature, or weak signatures from two different groups when a
    Chinese state entity is named somewhere in the article or its page chrome
      -> Category A, confidence 1.0, method rules, no LLM call
  - one weak signature
      -> routed to the LLM as an A candidate, unless the item arrived through a
         gate-exempt section (press_release, sponsored, partner; fetch_feeds.py
         skips the keyword gate for these by design) and its body does not clear
         the same body-level relevance test used below for the residual C versus
         not_relevant call; such an item is not_relevant by rules instead, with
         no LLM call. A strong signature still gives A regardless of arrival.
  - no A signature but an official sourcing trigger
      -> routed to the LLM for the B versus C judgement
  - no A signature and no trigger, but the headline, feed summary or body
    fingerprint nearly repeats a current state origin item from another outlet,
    discovered from eleven days before to eleven days after the article
    (pipeline/lookalike.py; agency copy republished without its credit line
    names nothing a signature can see)
      -> routed to the LLM as an A candidate, the match recorded in llm_trigger
         under "lookalike". sweep_lookalikes repeats the check at the end of each
         stage for articles labelled before their counterpart was.
  - no A signature, no trigger and no lookalike
      -> Category C by rules if the body substantively concerns China
         (at least BODY_RELEVANCE_MIN_HITS distinct gate terms, or
         BODY_RELEVANCE_MIN_TOTAL occurrences, or a term in the headline
         with at least two occurrences), otherwise not_relevant by rules. Both carry confidence below 1.0
         and are sampled by the agreement study like every other label.
"""
import datetime as dt
import json
import re
import time
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import yaml

from pipeline import config, extract, gate, lookalike, store

BODY_RELEVANCE_MIN_HITS = 3
BODY_RELEVANCE_MIN_TOTAL = 5
REFETCH_ATTEMPT_LIMIT = 3
RECLASSIFY_PER_RUN = 2000
# sweep_lookalikes looks at the state origin labels made in this many past hours. This counts from
# when the label was made (classified_at), not from when either article was discovered: a credited
# copy discovered lookalike.WINDOW_AFTER_DAYS after its uncredited twin is labelled when it arrives
# and is swept then, against rules labels as old as that window. It only has to span the gap between
# two stage runs (scheduled hourly), so a wider window needs no longer look-back.
LOOKALIKE_SWEEP_HOURS = 48
HEAD_CHARS = 400
TAIL_CHARS = 600


@lru_cache(maxsize=None)
def load_signatures(path=str(config.SIGNATURES_PATH)) -> Dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    compiled = {"ruleset_version": data["ruleset_version"], "exclusions": [], "groups": {}, "llm_triggers": []}
    for ex in data.get("exclusions", []):
        compiled["exclusions"].append((ex["id"], re.compile(ex["regex"], re.MULTILINE | re.UNICODE)))
    for gname, g in data["groups"].items():
        pats = []
        for p in g["patterns"]:
            if p.get("post_check") and p["post_check"] not in POST_CHECKS:
                raise ValueError("signature %s: unknown post_check %r" % (p["id"], p["post_check"]))
            pats.append({
                "id": p["id"], "group": gname, "strength": p["strength"],
                "scope": p.get("scope", "body"),
                "re": re.compile(p["regex"], re.MULTILINE | re.UNICODE),
                "post_check": p.get("post_check"),
            })
        compiled["groups"][gname] = pats
    for t in data.get("llm_triggers", []):
        compiled["llm_triggers"].append((t["id"], re.compile(t["regex"], re.MULTILINE | re.UNICODE)))
    return compiled


@lru_cache(maxsize=None)
def load_diplomats(path=str(config.DIPLOMATS_PATH)) -> List[str]:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    # An entry's native list holds the same person's name as printed in another script (Чжан Ханьхуэй,
    # 다이빙, 呉江浩, لياو ليتشيانغ); each form is matched exactly as the Latin name is.
    names = set()
    for p in data.get("personnel", []):
        names.add(p["name"])
        names.update(p.get("native") or [])
    return sorted(names)


# Scripts written without spaces between words (Han, kana, Thai, Khmer): a name there has no word
# boundary on either side ("呉江浩大使", "นายจาง เจี้ยนเว่ย"), so it is matched as it stands.
_UNSPACED_RE = re.compile(r"[\u0e00-\u0e7f\u1780-\u17ff\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")


def name_pattern(name: str) -> str:
    """The regex for a listed name: the whole name, between word boundaries unless its script is
    written without spaces. A hyphen joined to a letter counts as part of the name, so the name is
    never matched inside a hyphenated or longer one: "Chen Li" is not found in "Yu-chen Li" (the
    Guardian's Taipei reporter), nor "Wang Yu" in "Wang Yu-ting"."""
    e = re.escape(name)
    return e if _UNSPACED_RE.search(name) else r"(?<!\w)(?<!\w-)%s(?!\w)(?!-\w)" % e


# The route by which a state origin item reached the page, named after the signature group that
# established it. Stored on every state origin label, so penetration can be broken down by route
# rather than only by country.
ROUTES = [("credit_dateline", "wire_credit"), ("distribution_stamp", "distribution_stamp"),
          ("sponsored_placement", "sponsored_disclosure"), ("authored_by_state", "diplomatic_byline")]
# A model label on an article the lookalike check sent (pipeline/lookalike.py: no signature, a near
# repeat of a current state origin item) has its own route, uncredited_copy, rather than unattributed:
# a deterministic match to a credited item stands behind it, though no signature in its own text does.
UNCREDITED_COPY = "uncredited_copy"
ROUTE_IDS = [r for _, r in ROUTES] + [UNCREDITED_COPY, "unattributed"]

# Where an article was collected, a separate variable from the route: an editorial feed, or an outlet's
# press release, sponsored or partner section (registry section_feeds). A Xinhua Silk Road release found in
# a press release section arrived by the section and carries a distribution stamp or wire credit.
SECTION_ARRIVALS = {"press_release": "press_release_section", "sponsored": "sponsored_section", "partner": "partner_section"}
ARRIVAL_IDS = ["editorial_feed"] + list(SECTION_ARRIVALS.values())


def arrival_of(gate_terms) -> str:
    """Arrival from an article's stored gate terms: items from a section feed carry "section:<kind>"."""
    try:
        terms = json.loads(gate_terms or "[]") if isinstance(gate_terms, str) else (gate_terms or [])
    except ValueError:
        terms = []
    for t in terms:
        if isinstance(t, str) and t.startswith("section:") and t[8:] in SECTION_ARRIVALS:
            return SECTION_ARRIVALS[t[8:]]
    return "editorial_feed"


def route_of(matches: List[Dict]) -> str:
    """Route of a state origin label. Strong signatures decide before weak ones, and when several
    groups fired at the same strength the first in ROUTES wins, so the field is deterministic.
    Hints never set a route. A label that no signature supports is unattributed."""
    for strength in ("strong", "weak"):
        groups = {m["group"] for m in matches if m["strength"] == strength}
        for group, route in ROUTES:
            if group in groups:
                return route
    return "unattributed"


@lru_cache(maxsize=None)
def _signature_index() -> Dict[str, Tuple[str, str]]:
    sigs = load_signatures()
    idx = {p["id"]: (p["group"], p["strength"]) for pats in sigs["groups"].values() for p in pats}
    idx["diplomat_list_author"] = ("authored_by_state", "strong")
    idx["diplomat_list_name_only"] = ("authored_by_state", "weak")
    return idx


def route_of_ids(ids) -> str:
    """Route from stored signature ids. Ids no longer in the ruleset are ignored."""
    idx = _signature_index()
    return route_of([{"group": idx[i][0], "strength": idx[i][1]} for i in (ids or []) if i in idx])


def _trigger_of(llm_trigger) -> Dict:
    try:
        trig = json.loads(llm_trigger or "{}") if isinstance(llm_trigger, (str, bytes)) else (llm_trigger or {})
    except ValueError:
        return {}
    return trig if isinstance(trig, dict) else {}


def model_route(llm_trigger) -> str:
    """Route for a model state origin label stored without one, from the article's llm_trigger:
    uncredited_copy when the lookalike check sent it (a match recorded under "lookalike"), otherwise
    unattributed. Never the route of the candidate signatures that sent it (ruleset 2026.10.2): a weak
    signature is why the model was asked, not what the model found, and inheriting it put a GAC
    factory release under the diplomatic byline route. A model label stored now carries the route of
    the basis the model stated (classify_llm._model_route); older inherited routes are corrected when
    their labels are judged again."""
    trig = _trigger_of(llm_trigger)
    if isinstance(trig.get("lookalike"), dict):
        return UNCREDITED_COPY
    return "unattributed"


def ensure_routes(conn) -> int:
    """Fill the route of every current state origin label that lacks one: a rules label from the
    signatures that fired, a model label unattributed or, when the lookalike check sent it,
    uncredited_copy (model_route), never from the candidate signatures.
    A model label stored as unattributed on an article the lookalike check sent, from before
    uncredited_copy existed, is moved to uncredited_copy. Returns the number of labels written."""
    rows = conn.execute(
        """SELECT c.id, c.method, c.signatures_fired, c.route, a.llm_trigger FROM classifications c JOIN articles a ON a.id=c.article_id
           WHERE c.is_current=1 AND c.category='A' AND (c.route IS NULL OR
                 (c.method='llm' AND c.route='unattributed' AND a.llm_trigger LIKE '%"lookalike"%'))"""
    ).fetchall()
    written = 0
    for r in rows:
        if r["method"] == "rules":
            ids = json.loads(r["signatures_fired"] or "[]") or _trigger_of(r["llm_trigger"]).get("a_candidate") or []
            route = route_of_ids(ids)
        else:
            route = model_route(r["llm_trigger"])
        if route == r["route"]:
            continue
        conn.execute("UPDATE classifications SET route=? WHERE id=?", (route, r["id"]))
        written += 1
    conn.commit()
    return written


# Two weak signals (an ad label plus a "not reviewed by the publisher" note, say) only add up to
# state origin when the piece names a Chinese state entity. Without this, a Chery or Huawei
# advertorial counted as state origin.
#
# Only named organisations belong here. Ruleset 2026.09.8 removed the topic phrases that used to
# sit in this list, because the two weak signature path needs the entity test to be satisfied
# anywhere in the author field, title, body or page chrome: a travel advertorial that mentioned
# the Silk Road, plus two weak sponsorship signals, was asserted as state origin at confidence 1.0
# with no model check. "Belt and Road", "Silk Road", "Chinese government" and its translations,
# bare "State Council", bare "Information Office" and bare "Publicity Department" are gone; the
# last three survive in their full organisational forms.
STATE_ENTITY_RE = re.compile(
    r"(?i)\b(Xinhua|Xinhuanet|Nuova Cina|CGTN|China Daily|Global Times|People'?s Daily|Quotidiano del Popolo|CCTV|China Central Television|"
    r"China Media Group|CICG|China International Publishing|China International Communication Group|China News Service|ECNS|China Radio International|(?-i:CRI[ \u00a0]?(?:Online|ONLINE|English|ENGLISH))|"
    r"Chinese Embassy|Embassy of (the People'?s Republic of )?China|Chinese Consulate|Consulate[- ]General of (the People'?s Republic of )?China|"
    r"ambasciata (cinese|della Cina|della Repubblica Popolare Cinese)|ambassade de Chine|embajada de China|embaixada da China|chinesische Botschaft|"
    r"Ministry of (Foreign Affairs|Commerce|Culture and Tourism|Education) of (the People'?s Republic of )?China|Chinese (Foreign|Commerce|Culture) Ministry|"
    r"(State Council|Chinese State Council) Information Office|Information Office of the (State Council|[A-Z][a-z]+ (Municipal|Provincial))|"
    r"Publicity Department of the (Central Committee|Communist Party)|Central Publicity Department|"
    r"People'?s Government|Municipal (People'?s )?Government|Provincial (People'?s )?Government|Government of [A-Z][a-z]+ (Province|Municipality|Autonomous Region)|"
    r"Communist Party of China|Chinese Communist Party|Confucius Institute|China Cultural Cent(er|re)|"
    r"China (National )?Tourism (Administration|Office)|Hong Kong (SAR )?Government|Macao (SAR )?Government|Hong Kong Trade Development Council|Invest Hong Kong|"
    r"Chinese People'?s Association|China Public Diplomacy|Taiwan Affairs Office|Overseas Chinese Affairs (Office|Department))\b"
)

# "CMG" on its own is a stock ticker before it is a broadcaster: Computer Modelling Group Ltd
# trades as CMG in Toronto, and its GlobeNewswire releases were reaching the state entity test.
# The acronym counts only next to a word that places it in China, which is how the real China
# Media Group items are written ("Kineska medijska grupa (CMG)", "중국중앙방송총국(CMG)",
# "Медиакорпорация Китая ... CMG"). The name spelled out needs no such help and is in
# STATE_ENTITY_RE above.
_CHINA_CONTEXT = (
    r"Chin[aeoêé]\w*|Cin[ae]\w*|Kina|Kine|kinesk\w*|Kitajsk\w*|"
    r"Кита[йяею]\w*|китайск\w*|Кина|кинеск\w*|"
    r"Çin|Tiongkok|Trung Quốc|中国|中國|中央|중국|"
    r"الصين|סין|چین|Κίν\w*"
)
CMG_WITH_CHINA_RE = re.compile(
    r"(?i)(?:%s)[^\n]{0,40}?\bCMG\b|\bCMG\b[^\n]{0,40}?(?:%s)" % (_CHINA_CONTEXT, _CHINA_CONTEXT),
    re.UNICODE,
)


# The same organisations written in their own scripts. Without these the two weak signature path
# could only ever fire on an outlet that spelled a Chinese outlet's name in Latin letters, which
# is one of the ways the instrument used to be blind outside Latin script. Only unambiguous
# organisation names are listed: the Greek "Nea Kina", which is also the ordinary phrase for a
# new China, and the Vietnamese name of the People's Daily, which is also the name of Vietnam's
# own party paper, are deliberately absent. China Radio International is 中国国际广播电台, and its
# site 国际在线 (CRI Online) counts only as a name: followed by punctuation, the end of the text, or
# a word that makes it a source (消息, 报道, 记者, 网), because 国际在线教育 is international online
# education and 国际在线支付 international online payment. CRI's Japanese name 中国国際放送局 counts as
# written; its Korean name 중국국제방송(국) only before a particle or a non Hangul character, so that
# a longer compound such as 중국국제방송장비전시회 (a broadcasting equipment fair) does not.
STATE_ENTITY_NATIVE_RE = re.compile(
    r"(?i)(Σινχούα|Σινχουά|Σίνχουα|Σινχουα|Ξινχούα|Ξινχουά|Ξίνχουα|Ξινχουα|"
    r"שינחואה|סינחואה|شین[‌ ]?هوا|Şinhua|Tân Hoa X[ãa]|"
    r"Синьхуа|Медиакорпорация Китая|Жэньминь жибао|Хуаньцю шибао|Центральное телевидение Китая|"
    r"وكالة أنباء الصين الجديدة|شينخوا|شينهوا|"
    r"신화통신사|신화통신|신화사|인민일보|환구시보|중국중앙방송총국|중국중앙방송|차이나데일리|"
    r"新華社通信|新華社|チャイナ・デイリー|チャイナデイリー|中国中央テレビ|"
    r"新华社|新华网|新華網|中国日报|中國日報|环球时报|環球時報|人民日报|人民日報|"
    r"中国中央电视台|中央电视台|中央广播电视总台|中央廣播電視總台|中国国际电视台|中国新闻社|中新社|"
    r"中国国际广播电台|中國國際廣播電台|中國國際廣播電臺|中国国際放送局|"
    r"중국국제방송(?:국)?(?=[은는이가의에와과도을를로만]|[^가-힣]|$)|"
    r"(?:国际在线|國際在線)(?=消息|报道|報道|记者|記者|专稿|專稿|综合|綜合|讯|訊|网|網|新闻|新聞|\W|$))",
    re.UNICODE,
)


def has_state_entity(text: str) -> bool:
    """True when the text names a Chinese state organisation, as opposed to a China topic."""
    text = text or ""
    return bool(STATE_ENTITY_RE.search(text) or STATE_ENTITY_NATIVE_RE.search(text)
                or CMG_WITH_CHINA_RE.search(text))

# The same four titles (ambassador, consul general, charge d'affaires, spokesperson) in the non-Latin
# scripts of the languages with at least five active outlets in sources/outlets.yaml on 2026-10-12:
# Arabic and Persian, Cyrillic (Russian, Ukrainian, Serbian, Macedonian, Bulgarian), Korean, Greek,
# Hebrew, Japanese, Thai, Hindi and Khmer. Only the title, never the mission: посольство, 대사관, 大使館,
# สถานเอกอัครราชทูต, राजदूतावास and the consulates do not match. Arabic and Hebrew titles take their
# attached prefixes (السفير, לשגריר). Korean, Japanese, Thai and Khmer are written without a space
# between the country and the title (중국대사, 中国大使, เอกอัครราชทูตจีน), so those carry no boundary.
_NATIVE_TITLE_UNSPACED = (
    r"대사(?:대리)?(?!관|증)|총영사(?!관)|대변인|"
    r"臨時代理大使|大使(?!館)|総領事(?!館)|報道官|"
    r"(?<!สถาน)(?:เอกอัครราชทูต|กงสุลใหญ่)|อุปทูต|โฆษก|"
    r"(?<!ស្ថាន)ឯកអគ្គរដ្ឋទូត|អគ្គកុងស៊ុល(?!ដ្ឋាន)|ភារធារី|អ្នកនាំពាក្យ"
)
_NATIVE_TITLE = (
    r"(?<!\w)(?:[وفبل]?ال|لل|[وف])?(?:سف[يی]ر(?:ة|ه)?|قنصل(?:ة)?\s+(?:ال)?عام(?:ة)?|قائم(?:ة)?\s+ب(?:ال)?أعمال|"
    r"(?:متحدث|ناطق)(?:ة)?\s+(?:باسم|(?:ال)?رسمي(?:ة)?))(?!\w)|"
    r"(?<!\w)(?:سر\s?[کك]نسول|[کك]اردار|سخنگو(?:ی)?)(?!\w)|"
    r"(?<!\w)(?:посол(?:а|у|ом|ы|ов|ів|ові)?|посланик(?:а|ът|от)?|амбасадор(?:а|у|ом|ка|ката|от|ки)?|ЧПП|"
    r"генеральн\w*\s+консул(?:а|у|ом|ы)?|генконсул(?:а|у|ом)?|генерал(?:ни|ен|ниот)\s+(?:консул|конзул)(?:а|у|ом|ът|от)?|"
    r"временн\w*\s+поверенн\w*|тимчасов\w*\s+повірен\w*|временно\s+управляващ\w*|отправни(?:к|ца)\s+послова|"
    r"отпра(?:в)?ник\s+на\s+работите|официальн\w*\s+представител\w*|офіційн\w*\s+представни\w*|"
    r"пресс?-?секретар\w*|речни(?:к|ця|ка|ці)|портпарол\w*|говорител\w*)(?!\w)|"
    r"(?<!\w)(?:πρ[έε]σβ(?:ης|η|ειρα|ειρας|εως|εις)|πρεσβευτ(?:[ήη]ς|[ήη]|[έε]ς)|"
    r"γενικ(?:[όο]ς|[ήη]|ο[ύυ])\s+πρ[όο]ξεν(?:ος|ου|ο)|επιτετραμμ[έε]ν(?:ος|η|ου)|"
    r"εκπρ[όο]σωπ(?:ος|ου)\s+(?:τ[ύυ]που|του\s+υπουργε[ίι]ου\s+εξωτερικ[ώω]ν))(?!\w)|"
    r"(?<!\w)ו?[הלב]?(?:שגריר(?:ת|ה)?|קונסול(?:ית)?\s+ה?כלל(?:י|ית)|מיופ[הת]\s+ה?כוח|דובר(?:ת)?)(?!\w)|"
    r"(?<![\u0900-\u097F])(?:राजदूत(?!ावास)|महा\s?वाणिज्य\s?दूत(?!ावास)|प्रवक्ता)|"
    + _NATIVE_TITLE_UNSPACED
)
# The same titles in the other Latin script languages of active outlets, with their declined forms:
# Polish, Romanian and Serbian ambasador (ambasadorem, ambasadorul, ambasadoarea), Czech and Slovak
# velvyslanec / veľvyslanec, Slovenian and Croatian veleposlanik / veleposlanica, Hungarian nagykövet,
# Swedish, Danish and Norwegian ambassadör / ambassadør, Finnish suurlähettiläs, Turkish büyükelçi,
# Indonesian and Malay duta besar (dubes), Vietnamese đại sứ, Swahili balozi, the consuls general and
# charges d'affaires in each, and the feminine forms the list above lacks. The mission never matches:
# ambasada, velvyslanectví, veleposlaništvo, nagykövetség, büyükelçilik, kedutaan, đại sứ quán, ubalozi.
_LATIN_TITLE = (
    r"ambassadrice|embaixadora|botschafterin|generalkonsul(?:in)?|consul-generaal|cônsul-geral|consulul general|"
    r"ambasad(?:or|oar)\w*|ve[lľ]vysl(?:anec|ance|ancem|anci|ankyně|ankyňa|ankyni|ankyne)|"
    r"veleposlani(?:k|ka|ku|kom|ca|ce|ci|co)|nagykövet(?:e|ét|ként|nek|et)?|főkonzul(?!átus)\w*|ügyvivő\w*|"
    r"ambassad[öø]r(?:en|er|erne|erna)?|suurlähettilä\w*|pääkonsul\w*|asiainhoitaja\w*|"
    r"büyükelçi(?!li[kğ])\w*|başkonsolos(?!lu[kğ])\w*|maslahatgüzar\w*|"
    r"duta besar|dubes|konsul jend?eral|kuasa usaha|đại sứ(?!\s+quán)|tổng lãnh sự(?!\s+quán)|đại biện|balozi|"
    r"(?:generaln[iíy]|generálny)\s+kon[sz]ul\w*|kon[sz]ul\w*\s+generaln\w*|"
    r"o[dt]pravni(?:k|ca)\s+poslov\w*|însărcinat cu afaceri"
)
_TITLE_RE = re.compile(
    r"(?i)(?:\b(ambassador|ambassadeur|ambasciatore|ambasciatrice|embajador|embajadora|embaixador|botschafter|"
    r"consul[- ]general|console generale|consul général|cónsul general|chargé d.affaires|incaricato d.affari|"
    r"spokesperson|spokesman|spokeswoman|portavoce|porte-parole|portavoz|foreign minister|vice foreign minister|"
    r"ministro degli esteri|ministre des affaires étrangères|" + _LATIN_TITLE + r")\b|" + _NATIVE_TITLE + ")"
)


_HEAD_BYLINE_RE = re.compile(r"(?i)^\s*(?:by|par|di|por|von|door|av|de|بقلم:?|به\s+قلم:?|מאת:?|Автор:?|"
                             r"Γράφει\s+[οη]|Του|Της)\s+(?:H\.E\.\s*)?([^\n,،]{3,60})")


def _diplomat_head_byline(head: str) -> Optional[str]:
    """The byline line at the top of the body when it names a listed diplomat and carries a
    diplomatic title on the same line, else None."""
    for line in [ln for ln in head.splitlines() if ln.strip()][:3]:
        m = _HEAD_BYLINE_RE.match(line)
        if not m or not _TITLE_RE.search(line) or _REPORTED_RE.search(line):
            continue
        for name in load_diplomats():
            if re.search(name_pattern(name), m.group(1), re.IGNORECASE) \
                    and _title_is_chinese(line, name):
                return line.strip()
    return None


# What may stand between a listed name and a diplomatic title when the title describes that person:
# an article, "former" or "acting", the country adjective, "H.E.". Any other word ("Xiao Qian, who met
# the ambassador", "Wang Yu is a reporter who interviewed the ambassador") means the title is someone
# else's.
_TITLE_LEAD = (r"(?:(?:H\.E\.|the|an?|former|acting|then|current|deputy|Chinese|China[’']s|PRC|chinesische[rns]?|"
               r"il|la|le|el|ex|der|die|o|"
               # the same in the Latin script languages of _LATIN_TITLE: the China adjective or genitive
               # (kitajska veleposlanica, chiński ambasador, Kinas ambassadør, Çin'in) and "former"
               r"Chinas|Kinas|Kiinan|Çin(?:[’']in)?|Kína|kínai|kitajsk\w*|kinesk\w*|kinesisk\w*|chińsk\w*|"
               r"čínsk\w*|kiinalai\w*|chinez\w*|Chinees|nekdanj\w*|bivš\w*|były|była|bývalý|bývalá|fostul|"
               r"fosta|entinen|tidligere|tidigare|voormalig\w*|eski|mantan|cựu)\s+|l[’'])")
# The same in the scripts where the country or the host post comes before the title: 중국대사, 주한 중국대사,
# 中대사 (a Korean headline's 中), 駐日中国大使, चीन के राजदूत, and "former" in Russian, Ukrainian and Greek.
_TITLE_LEAD_NATIVE = (r"(?:(?:中華人民共和国|中华人民共和国|中国|中國|중국|中(?=[가-힣])|चीन(?:ी)?(?:\s+के)?)\s*|"
                      r"주[가-힣]{1,2}\s+|[駐驻]\w|前|전\s+|(?:бывш|колишн)\w+\s+|πρώην\s+|[οη]\s+)")
_TITLE_NEAR = r"(?:%s|%s){0,4}%s" % (_TITLE_LEAD, _TITLE_LEAD_NATIVE, _TITLE_RE.pattern[len("(?i)"):])
# A name followed directly by its title, with only a space or nothing between, is how a Korean,
# Japanese or Thai byline reads ("다이빙 주한 중국대사", "呉江浩駐日中国大使", "นายจาง เจี้ยนเว่ย เอกอัครราชทูตจีน").
_UNSPACED_TITLE_AHEAD = r"(?:%s){0,4}(?:%s)" % (_TITLE_LEAD_NATIVE, _NATIVE_TITLE_UNSPACED)
# A bio line about the author that does not repeat the name: "The writer is the Chinese Ambassador to
# Pakistan.", "L'auteur est l'ambassadeur de Chine en France".
_AUTHOR_IS_RE = re.compile(
    r"(?i)(?:\b(?:the|this)\s+(?:author|writer)\s+(?:is|was)|\bl[’']aut(?:eur|rice|ore)\s+(?:est|était|è)|"
    r"\b(?:el|la)\s+autora?\s+(?:es|fue)|\b(?:der|die)\s+Autor(?:in)?\s+(?:ist|war)|\b[oa]\s+autora?\s+(?:é|foi)|"
    r"\bavtor(?:ica)?\s+(?:članka\s+)?je|\bautor(?:ica|ka)?\s+(?:je|jest)|\bauto(?:rul|area)\s+(?:este|e)|"
    r"\bde\s+(?:auteur|schrijver)\s+is|\b(?:författaren|skribenten)\s+är|\b(?:forfatteren|skribenten)\s+er|"
    r"\bkirjoittaja\s+on|\bpenulis\s+(?:adalah|ialah|merupakan)|\btác giả\s+là|\bmwandishi\s+ni)\s+"
    + _TITLE_NEAR)
# The same bio line in Hungarian has no verb: "A szerző Kína budapesti nagykövete", "A szerző a Kínai
# Népköztársaság magyarországi nagykövete" (the writer [is] China's ambassador in Budapest). It counts only
# with China as the possessor right after "A szerző" (Kína, a Kínai Népköztársaság, a KNK), then at most
# four modifiers (a place adjective such as budapesti, volt for former, rendkívüli és meghatalmazott,
# Magyarországra akkreditált), then the title with its possessive ending (nagykövete, főkonzulja,
# ügyvivője) closing the phrase, so "A szerző Magyarország pekingi nagykövete" (Hungary's ambassador in
# Beijing) and "A szerző Kína budapesti nagykövetének tanácsadója" (the ambassador's adviser) do not fire.
_AUTHOR_IS_VERBLESS_RE = re.compile(
    r"(?i)(?<!\w)a\s+szerző,?\s+(?:az?\s+)?(?:Kína|Kínai\s+Népköztársaság|(?-i:KNK))(?:nak\s+a)?\s+"
    r"(?:(?:volt|korábbi|jelenlegi|megbízott|ideiglenes|rendkívüli\s+és\s+meghatalmazott|\w+i|"
    r"\w+(?:ra|re)\s+akkreditált)\s+){0,4}(?:nagykövete|főkonzulja|ügyvivője)(?!\w)"
    r"(?=[ \t]*(?:\Z|[.,;:)!?\n]|volt(?!\w)|\d{4}|(?-i:[A-ZÁÉÍÓÖŐÚÜŰ])\w*(?:on|en|ön|ban|ben)(?!\w)))")
# The same bio line as a label with no verb, the writer word and then a dash, a colon or a comma: Polish
# and Czech "Autor: ambasador ChRL w Polsce", "Autor, velvyslanec ČLR v ČR" (or with a dash), Indonesian
# "Penulis, Duta Besar China untuk Indonesia", Vietnamese "Tác giả: Đại sứ Trung Quốc tại Việt Nam",
# English "The writer, Chinese Ambassador to Australia"; and Turkish, where the copula is a suffix on the
# title, with or without the comma: "Yazar, Çin'in Ankara Büyükelçisidir", "Yazar Çin'in Ankara
# Büyükelçisi". The label opens its line (after list marks or a page label key), the title group t may
# carry up to four words before the title ("Çin'in Ankara"), and what follows the label must be a title
# phrase and nothing else (_author_label_reads: _is_title_line), so "Yazar Çin'in Ankara Büyükelçisi
# ile görüştü" (the writer met China's ambassador) does not fire.
_AUTHOR_LABEL_RE = re.compile(
    r"(?im)^[ \t*\u2022>(\[_]*(?:[\w@][\w@ -]{0,30}:[ \t]*)?"
    r"(?:(?:(?:the|this)[ \t]+(?:author|writer)|autor(?:ka|ica)?|penulis|tác[ \t]+giả|yazar)"
    r"(?:[ \t]*[:,\u2013\u2014][ \t]*|[ \t]+-[ \t]+)|yazar[ \t]+)"
    r"(?P<t>(?:[^\W\d_][\w'\u2019.-]*[ \t]+){0,4}?(?:" + _TITLE_RE.pattern[len("(?i)"):] + "))")
# An opinion or commentary section, read from a short page label or kicker line (meta section,
# articleSection, @type, a kicker above the body). A long line is a headline or a sentence, where
# "comment" or "opinion" says nothing about the section.
_OPINION_RE = re.compile(
    r"(?i)\b(opinions?|op-?eds?|commentary|comment|columns?|columnists?|viewpoints?|OpinionNewsArticle|"
    r"opini[oó]n|opiniones|opinione|opini[aã]o|tribune|tribuna|meinung|kommentar|gastbeitrag|analistas|"
    r"colaborador invitado|columnistas?|punto de vista|point de vue|commenti|editoriale|"
    r"mnenj[ae]|gostujoče pero|mišljenj[ae]|koment[aá]r\w*|kolumn\w*|stavovi|pogledi|opinie|felieton\w*|"
    r"publicysty\w*|publicisti\w*|publicisztika|n[aá]zory|v[ée]lem[ée]ny|nézőpont|opinii|comentarii|"
    r"commentaar|ingezonden\w*|debatt?|ledare|krönik\w*|kronik\w*|åsikt\w*|meninger|mielipite\w*|"
    r"mielipide|kolumni\w*|pääkirjoitu\w*|vieraskynä|görüş|yorum|köşe yazı\w*|opini|kolom|tajuk rencana|"
    r"rencana|ý kiến|bình luận|góc nhìn|diễn đàn|maoni|"
    r"رأي|آراء|مقالات|Мнени[ея]|Колонк[аи]|Думк[аи]|Άποψη|Απόψεις|דעות|오피니언|기고|オピニオン|寄稿|"
    r"ความคิดเห็น|विचार|دیدگاه|یادداشت)\b")


def _in_opinion_section(title: str, head: str, labels: str) -> bool:
    lines = (labels or "").splitlines() + [ln for ln in (head or "").splitlines() if ln.strip()][:4]
    for line in lines + [title or ""]:
        line = re.sub(r"(?i)^\s*(articleSection|@type|genre|keywords)\s*:\s*", "", line).strip()
        m = _OPINION_RE.search(line)
        if m and (len(line) <= 40 or (m.start() == 0 and re.match(r"\s*[|:]", line[m.end():]))):
            return True
    return False


# The title also has to make the person China's diplomat: "Li Dong, Ambassador of Singapore" signing a
# piece is Singapore's envoy, whoever else on the diplomat list shares the name. The title phrase is
# the title word with the words bound to it: back to the last comma, colon, line break or verb ("is",
# "est"), and on to the host country ("to Australia", "en México", "in Italia"), so China as the host
# country ("Ambassador of Singapore to China") does not count. The phrase refers to China when it
# carries a word of _CHINA_CONTEXT (Chinese, China's, of China, chinesische, cinese, chino and china,
# chinois and de Chine, chinês and da China, and the native scripts listed there) or PRC.
# Forms the title phrase also reads that _CHINA_CONTEXT lacks: Thai, Hindi and Khmer China, the full
# name in Japanese and Chinese, Korean headline 中 before a Hangul title, the Greek adjective without
# its accent on the first syllable (Κινέζος) and in capitals, the Serbian genitive Кине and Russian КНР.
_CHINA_NATIVE_EXTRA = (r"จีน|चीन|ចិន|中華人民共和国|中华人民共和国|中(?=[가-힣])|Κιν[έε]ζ\w*|ΚΙΝ[ΑΕ]\w*|"
                       r"(?<!\w)Кин[еиу](?!\w)|(?<!\w)КНР(?!\w)")
# And in the Latin script languages of _LATIN_TITLE: Hungarian Kína and kínai, Czech and Slovak Čína and
# čínský, Finnish Kiinan and kiinalainen, Polish Chin, Chiny and chiński, Vietnamese Trung Hoa, the Kenyan
# Swahili Uchina ("Balozi wa Uchina nchini Kenya"; Tanzania writes China), and the
# abbreviations ChRL, ČLR, ÇHC, RRT, RRC and the Hungarian KNK. Only the forms that name China as the
# sending country: the Hungarian Kínában and Finnish Kiinassa (in China) are the host country and are left out.
_CHINA_LATIN_EXTRA = (r"(?<!\w)(?:Kína(?!\w)|kínai\w*|Čín\w*|Kiinan(?!\w)|kiinalai\w*|chińsk\w*|"
                      r"(?-i:Chin(?:y|ach)?)(?!\w)|Trung Hoa|Uchina(?!\w)|(?-i:ChRL|ČLR|ÇHC|RRT|RRC|KNK)(?!\w))")
_CHINA_WORD_RE = re.compile(r"(?i)(?<![A-Za-zÀ-ÿ])(?:%s)|\bP\.?R\.?C\b|%s|%s"
                            % (_CHINA_CONTEXT, _CHINA_NATIVE_EXTRA, _CHINA_LATIN_EXTRA),
                            re.UNICODE)
_TITLE_PRE_STOP = re.compile(r"(?i)[,;:|/(\n*]|\b(?:is|was|serves as|has served as|est|était|è|es|fue|é|ist|war|"
                             r"je|jest|este|är|er|on|adalah|ialah|là|ni)\s|"
                             r"[،、，：・।。]|\sमें(?:\s|$)")
_TITLE_POST_STOP = re.compile(
    r"(?i)[,;:|/(\n*]|\.(?:\s|$)|\s(?:to|in|en|em|no|na|nos|nas|au|aux|à|a|ante|nach|presso|bei|at|auprès|"
    r"accredited|for|who|qui|que|che|said|says|"
    # in the Latin script languages of _LATIN_TITLE ("ambasador Chin w Polsce", "veleposlanica Kitajske v
    # Sloveniji", "Kinas ambassadør i Danmark", "Duta Besar China untuk Indonesia", "Đại sứ Trung Quốc tại
    # Việt Nam", "Balozi wa China nchini Tanzania"); "i" is not the Polish "and" of "Nadzwyczajny i Pełnomocny"
    r"w|we|przy|v|ve|pri|při|u|în|te|bij|i(?!\s+pe[lł]nomocn)|til|till|vid|ved|untuk|ke|tại|ở|nchini|katika)\b|"
    # the host country in the other scripts: "посол Китая в России", "سفير الصين لدى مصر", "سفیر چین در
    # ایران", "Πρέσβης της Κίνας στην Ελλάδα", "שגריר סין בישראל", "เอกอัครราชทูตจีนประจำประเทศไทย"
    r"[،、，：・।。]|\s(?:в|во|у|при|στην|στη|στον|στο|στα|σε|لدى|في|إلى|الى|در|نزد)(?!\w)|\sب(?=ال)|"
    r"\s[בל](?!שעבר|פועל)\w|ประจำ|ប្រចាំ")
# The host post written before the country, removed from the title phrase before it is read: Korean
# 주한 (in Korea) and "중국 주재" (stationed in China), Japanese 駐日 and 駐中国, so that 주중 한국대사 and
# 駐中国日本大使 (another country's envoy to China) are not China's diplomats and 駐日中国大使 is.
_NATIVE_HOST_RE = re.compile(r"주중국|\S*\s*주재|주[가-힣](?=\s|중국|$)|"
                             r"[駐驻在](?:中華人民共和国|中华人民共和国|中国|中國|[華华])|(?:中国|中國)駐在|[駐驻]\w")
# Words a title phrase may carry without naming any country: the _TITLE_LEAD words and the formal
# "Extraordinary and Plenipotentiary" in its translations.
_TITLE_FILLER_RE = re.compile(
    r"(?i)(?:\bH\.E\.|\b(?:the|an?|former|acting|then|current|deputy|il|la|le|el|ex|der|die|o|l|and|et|e|y|und|"
    r"extraordinary|plenipotentiary|extraordinaire|plénipotentiaire|straordinario|plenipotenziario|"
    r"extraordinario|plenipotenciario|extraordinário|plenipotenciário|außerordentlicher|bevollmächtigter)\b|"
    r"(?<!\w)(?:ο|η|της|του|τον|την|πρώην|νέος|νέα|έκτακτος|πληρεξούσιος|και|бывш\w*|нов\w*|колишн\w*|"
    r"чрезвычайн\w*|полномочн\w*|надзвичайн\w*|повноважн\w*|извънреден|пълномощен|и|і|г-жа|г-н|"
    r"ال?سابق|ال?جديد|فوق\s+العادة|و?ال?مفوض|السيد|السيدة|לשעבר|בפועל|ה?חדש(?:ה)?|ד\"ר|جدید|"
    r"신임|전|新任|前|元|นาย|นาง|อดีต)(?!\w)|Н\.\s?Пр\.)")


# The host country written where other languages put the sender, cut from the title phrase before it is
# read (_host_slot): Turkish puts the sender in the genitive and the host between it and the title,
# "Türkiye'nin Çin Büyükelçisi" (Turkey's ambassador to China) against "Çin'in Ankara Büyükelçisi";
# Hungarian puts a host adjective directly before the possessed title, "Magyarország kínai nagykövete"
# (Hungary's ambassador to China) against "Kína budapesti nagykövete"; a word joined to the title by a
# hyphen is the host, "Suomen Kiinan-suurlähettiläs", "Danmarks Kina-ambassadør", "China-Botschafter",
# and so is the second of two Finnish genitives, "Suomen Kiinan suurlähettiläs"; Swahili names the
# sender right after wa, so "Balozi wa Tanzania China" is Tanzania's; Indonesian and Malay also
# introduce the host with di ("Duta Besar RI di Tiongkok"), which is the Italian "of" elsewhere.
_TR_GENITIVE_RE = re.compile(r"\S['’]n?[ıiuü]n(?!\w)")


def _host_slot(pre: str, bound: str, title: str):
    """(pre, bound, host): the title phrase before and after the title with the host country cut out,
    and the text cut."""
    host = []

    def cut_last_word(text: str) -> str:
        w = text.rstrip().rsplit(None, 1)[-1]
        host.append(w)
        return text[:text.rstrip().rfind(w)]

    if pre.endswith("-") and pre.strip():
        pre = cut_last_word(pre)
    words = pre.split()
    if re.match(r"(?i)büyükelçi|başkonsolos|maslahatgüzar", title):
        g = list(_TR_GENITIVE_RE.finditer(pre))
        if g:
            host.append(pre[g[-1].end():])
            pre = pre[:g[-1].end()]
    elif re.match(r"(?i)nagykövet[eé]|főkonzulj|ügyvivőj", title):
        if len(words) >= 2 and words[-1].lower().endswith("i") \
                and any(w.lower() not in ("a", "az") for w in words[:-1]):
            pre = cut_last_word(pre)
    elif re.match(r"(?i)suurlähettilä|pääkonsul|asiainhoitaja", title):
        if len(words) >= 2 and all(re.match(r"[A-ZÅÄÖ]\w*n$", w) for w in words[-2:]):
            pre = cut_last_word(pre)
    elif re.match(r"(?i)balozi$", title):
        m = re.match(r"(?i)\s+(?:mdogo\s+)?(?:wa|za)\s+(?:Jamhuri\s+ya\s+(?:Watu\s+|Muungano\s+)?wa\s+)?\S+", bound)
        if m:
            host.append(bound[m.end():])
            bound = m.group(0)
    elif re.match(r"(?i)duta besar|dubes|konsul jend?eral|kuasa usaha", title):
        m = re.search(r"(?i)\s(?:di|pada)\b", bound)
        if m:
            host.append(bound[m.end():])
            bound = bound[:m.start()]
    return pre, bound, " ".join(host)


def _title_side(line: str, t) -> str:
    """"china" when the title phrase around the title match t refers to China, "bare" when it names
    no country at all ("Ambassador", "Consul General in Perth"), "other" when it names another, or
    names China only as the host country ("Ambassador to China", "Türkiye'nin Çin Büyükelçisi")."""
    pre = line[:t.start()]
    cuts = [s.end() for s in _TITLE_PRE_STOP.finditer(pre)]
    pre = pre[cuts[-1]:] if cuts else pre
    post = line[t.end():]
    stop = _TITLE_POST_STOP.search(post)
    pre, bound, host = _host_slot(pre, post[:stop.start()] if stop else post, t.group(0))
    phrase = _NATIVE_HOST_RE.sub(" ", pre + " " + bound)
    if _CHINA_WORD_RE.search(phrase):
        return "china"
    if stop and re.search(r"[^\W\d_]", stop.group(0)) \
            and not re.match(r"(?i)\s*(?:who|qui|que|che|said|says)$", stop.group(0)):
        # the host country after a word that introduces it ("to China", "untuk Tiongkok", "katika China")
        host += " " + " ".join(re.split(r"[,;:|/(\n*]|\.(?:\s|$)", post[stop.end():])[0].split()[:6])
    if _CHINA_WORD_RE.search(host):
        return "other"  # an envoy to China is never China's diplomat, whatever the page says
    return "other" if re.search(r"\w", _TITLE_FILLER_RE.sub(" ", phrase)) else "bare"


def _names_chinese_mission(text: str) -> bool:
    """True when the text names a Chinese embassy or consulate (the mission forms of STATE_ENTITY_RE)."""
    return any(re.search(r"(?i)embass|consul|ambasciata|ambassade|embajada|embaixada|botschaft", m.group(0))
               for m in STATE_ENTITY_RE.finditer(text or ""))


def _title_is_chinese(text: str, name: str, lo: int = 0, hi: Optional[int] = None, page: str = "") -> bool:
    """Whether a diplomatic title starting in text[lo:hi] makes the person China's diplomat: its title
    phrase refers to China, or it names no country and the line names a Chinese embassy or consulate.
    page is given only for a title in the author field ("Liao Liqiang; Ambassador"): such a bare title
    also counts when the page labels or the head of the body name China. A title phrase naming another
    country never counts."""
    hi = len(text) if hi is None else hi
    a = text.rfind("\n", 0, lo) + 1
    b = text.find("\n", hi)
    line = text[a:] if b < 0 else text[a:b]
    # the listed name is neither part of the title phrase nor a country: blank it, keeping the offsets
    line = re.sub(r"(?i)%s\*?" % name_pattern(name), lambda m: "," + " " * (len(m.group(0)) - 1), line)
    sides = {_title_side(line, t) for t in _TITLE_RE.finditer(line) if lo - a <= t.start() < hi - a}
    if "china" in sides:
        return True
    if "bare" not in sides:
        return False
    return _names_chinese_mission(line) or bool(page and _CHINA_WORD_RE.search(page))


# A title on its own line directly under the name, as an author box prints it: "Zhang Yang" and then
# "Embaixador da República Popular da China em Cabo Verde" in the page labels of an Expresso das Ilhas
# op-ed. The name line holds the name alone. The title line is a title phrase and nothing else: at most
# four words before the title (the lead words, a China word or capitalised words), after it only words
# that end in the China word ("da República Popular da China") or none, and then the host post ("em Cabo
# Verde": capitalised words and connectives). A sentence or a headline under a namesake reporter's
# byline ("Chinese ambassador Liu Jinsong visits Adelaide", "The Chinese ambassador said on Monday ...")
# is not a title line.
_TITLE_LINE_CONNECTIVES = re.compile(
    r"(?i)^(?:the|of|and|de|da|do|das|dos|del|della|di|la|le|l|du|des|y|e|et|und|van|der|den|og|och|ja|ve|"
    r"dan|và|na|wa|a|z|ze|i|v|w|u|în|pe|lângă|al|el|von|zu|für)$")


def _is_title_line(line: str) -> bool:
    t = _TITLE_RE.search(line)
    if not t or re.search(r"[!?]|\.\s+\S", line):
        return False
    lead_rx = re.compile(r"(?i)^(?:%s|%s)+$" % (_TITLE_LEAD, _TITLE_LEAD_NATIVE))
    pre = line[:t.start()].split()
    if len(pre) > 4 or any(not (w[:1].isupper() or _CHINA_WORD_RE.search(w) or lead_rx.match(w + " "))
                           for w in pre):
        return False
    post = line[t.end():]
    stop = _TITLE_POST_STOP.search(post)
    bound, host = (post[:stop.start()], post[stop.end():]) if stop else (post, "")
    if re.search(r"\w", _TITLE_FILLER_RE.sub(" ", bound)):
        china = list(_CHINA_WORD_RE.finditer(bound))
        if not china:
            return False
        bound = bound[china[-1].end():]
    words = re.findall(r"[^\s,;:()]+", bound) + re.findall(r"[^\s,;:()]+", host)
    if len(words) > 10:
        return False
    return all(w[:1].isupper() or _CHINA_WORD_RE.search(w) or _TITLE_LINE_CONNECTIVES.match(w.strip(".'’"))
               or not re.search(r"[^\W\d_]", w) for w in words)


def _title_line_under_name(name: str, text: str) -> Optional[str]:
    """The name on a line of its own with a title line (_is_title_line) directly under it, at most one
    blank line between, when the title makes the person China's diplomat; else None."""
    rx = re.compile(r"(?im)^[ \t]*%s\*?[ \t,;:]*\n(?:[ \t]*\n)?[ \t]*([^\n]{3,160}?)[ \t]*$" % name_pattern(name))
    for m in rx.finditer(text or ""):
        line = m.group(1)
        if _is_title_line(line) and _title_is_chinese(line, name):
            return re.sub(r"\s*\n\s*", " / ", m.group(0).strip())
    return None

# A titled name in reported speech or narrative, not in a byline, author box, signature or bio: "Wang
# Yu, Chinese Ambassador to Australia, said ...", "... told reporters", "according to Chinese Ambassador
# Wang Yu". A speech or action verb in the sentence of the titled name makes it reported speech, in the
# languages the title test reads. Only unambiguous forms: "states" (the United States), the Swedish
# "sa", the Indonesian "kata" (word), the Vietnamese "theo" (following) and the Portuguese and Italian
# "segundo" and "secondo" (second) without their article are left out. "Writes" and "argues" are how a
# standfirst introduces a signed piece, and "expressed" is the disclaimer under one ("The views expressed
# are his own"): none is listed.
_REPORTED_RE = re.compile(
    r"(?i)(?<!\w)(?:said|says|saying|told|tells|telling|stated|noted|notes|added|adds|according\s+to|"
    r"stressed|stresses|emphasi[sz]ed|warned|warns|urged|urges|announced|announces|claimed|insisted|"
    r"explained|remarked|commented|responded|replied|pointed\s+out|reiterated|highlighted|"
    r"met|meets|meeting\s+with|visited|visits|attended|spoke|speaks|speaking|addressed|addressing|praised|"
    r"critici[sz]ed|denied|denies|rejected|rejects|welcomed|called\s+(?:on|for)|calls\s+(?:on|for)|accused|"
    r"slammed|slams|hit\s+back|in\s+an\s+interview|"
    # German, Dutch, the Scandinavian languages
    r"sagte|sagt|erklärte|betonte|teilte\s+mit|laut|zufolge|zei|zegt|verklaarde|volgens|sade|säger|"
    r"uppgav|enligt|sagde|sier|uttalte|udtalte|ifølge|"
    # French, Italian, Spanish, Portuguese, Romanian
    r"a\s+(?:déclaré|dit|affirmé|souligné|indiqué|ajouté|expliqué|estimé|rappelé)|déclare|selon|"
    r"ha\s+(?:detto|dichiarato|affermato|sottolineato|spiegato|aggiunto)|dichiara|afferma|"
    r"secondo\s+(?:il|la|lo|l[’']|quanto)|dijo|afirmó|declaró|señaló|aseguró|explicó|indicó|destacó|"
    r"añadió|agregó|subrayó|manifestó|según|de\s+acuerdo\s+con|d[’']après|disse|afirmou|declarou|destacou|acrescentou|explicou|"
    r"sublinhou|segundo\s+(?:o|a)|a\s+(?:declarat|spus|afirmat|subliniat)|potrivit|"
    # Polish, Czech, Slovak, Slovenian, Croatian and Serbian, Hungarian, Finnish, Turkish
    r"powiedział\w*|stwierdził\w*|podkreślił\w*|oświadczył\w*|zaznaczył\w*|według|řekl\w*|uvedl\w*|"
    r"podle|povedal\w*|uviedol\w*|podľa|dejal\w*|izjavil\w*|poudaril\w*|po\s+besedah|rekao|rekla|"
    r"izjavio|izjavila|istakao|istakla|kazao|kazala|mondta|közölte|szerint|hangsúlyozta|nyilatkozta|"
    r"sanoi|kertoi|dedi|söyledi|açıkladı|belirtti|ifade\s+etti|"
    # Indonesian and Malay, Vietnamese, Swahili
    r"mengatakan|menyatakan|berkata|menurut|ujar\w*|cho\s+biết|phát\s+biểu|theo\s+lời|alisema|"
    r"amesema|alieleza|kwa\s+mujibu\s+wa|"
    # Russian, Ukrainian, Bulgarian, Serbian; Greek; Hebrew; Arabic; Persian
    r"заявил\w*|сказал\w*|отметил\w*|подчеркнул\w*|сообщил\w*|по\s+словам|заявив\w*|сказав\w*|"
    r"зазначив\w*|наголосив\w*|каза|заяви|изјавио|рекао|δήλωσε|είπε|τόνισε|ανέφερε|σύμφωνα\s+με|"
    r"ו?(?:אמר|אמרה|הודיע|הודיעה|לדברי|לדבריו|לדבריה)|و?(?:قال|قالت|صرح|صرّح|أكد|أكدت|أشار|أشارت|أضاف|"
    r"أضافت)|(?:وفق|بحسب|حسب)(?:اً|ا)?|گفت|اعلام\s+کرد|به\s+گفته)(?!\w)|"
    # Korean, Japanese, Chinese, Thai, Hindi, Khmer: written without word boundaries
    r"밝혔다|말했다|강조했다|전했다|따르면|述べた|語った|強調した|明らかにした|によると|表示|指出|强调|強調|"
    r"กล่าว|เปิดเผย|(?<![ऀ-ॿ])(?:कहा|बताया|अनुसार)|បានថ្លែង|មានប្រសាសន៍")
# Where a sentence ends: a full stop before a capital, other end punctuation, a line break.
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s+[^\W\d_a-zà-ÿ]|\s*$)|[!?。！？]|\n")
# What may stand before the name at the start of a bio or signature line: list marks, an honorific, a
# page label key ("description: ").
_BIO_LINE_LEAD_RE = re.compile(
    r"(?i)^[\s*\u2022\u00b7>(\[_\u2013\u2014-]*(?:[@\w][\w@ -]{0,30}:\s*)?"
    r"(?:(?:H\.E\.|S\.E\.|Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Prof\.?|นาย|นาง)\s*)*$")


def _sentence_around(text: str, lo: int, hi: int) -> str:
    """The sentence holding text[lo:hi]: back to the last sentence end before it, on to the next after."""
    starts = [s.end() for s in _SENTENCE_END_RE.finditer(text, 0, lo)]
    after = _SENTENCE_END_RE.search(text, hi)
    return text[starts[-1] if starts else 0:after.start() if after else len(text)]


def _is_reported(text: str, lo: int, hi: int) -> bool:
    """True when the sentence of the titled name text[lo:hi] carries a speech or action verb."""
    return bool(_REPORTED_RE.search(_sentence_around(text, lo, hi)))


def _bio_reads_as_author(text: str, m, where: str, name_led: bool) -> bool:
    """Whether a bio match m in text (the head, the tail or the page labels, given as where) reads as a
    byline, signature or bio, not as reported speech or narrative. Never when its sentence carries a
    speech or action verb. A bio that begins with the name ("Xiao Qian is the Chinese Ambassador to
    Australia", "Liu Jinsong, Chinese Ambassador to Australia", after an honorific or a title: "H.E.
    Ambassador Xiao Qian is ...") must also begin its line, and stand in a byline or bio position, on a
    line with no speech or action verb in the body: in the head among the first three lines, after
    short lines only (a kicker, a date), not after running text; in the tail as the last line or
    followed only by up to two short lines (a disclaimer, a credit); in the page labels on a label
    line of its own. A bio about "the writer" may stand anywhere."""
    if _is_reported(text, m.start(), m.end()):
        return False
    if not name_led:
        return True
    ls = text.rfind("\n", 0, m.start()) + 1
    # a title before the name is part of the signature: "H.E. Ambassador Xiao Qian is ..."
    if not _BIO_LINE_LEAD_RE.match(re.sub(r"(?i)(?:%s)\s*$" % _TITLE_NEAR, "", text[ls:m.start()])):
        return False
    le = text.find("\n", m.end())
    if where in ("head", "tail") and _REPORTED_RE.search(text[ls:len(text) if le < 0 else le]):
        return False  # "Wang Yu is the Chinese ambassador to Australia. Officials said ..."
    if where == "head":
        before = [ln.strip() for ln in text[:ls].splitlines() if ln.strip()]
        return len(before) < 3 and all(len(ln) <= 120 for ln in before)
    if where == "tail":
        if ls == 0 and len(text) >= TAIL_CHARS:
            return False  # the tail begins inside a line: the name may be mid-sentence
        nl = text.find("\n", m.end())
        rest = [] if nl < 0 else [ln.strip() for ln in text[nl + 1:].splitlines() if ln.strip()]
        return len(rest) <= 2 and all(len(ln) <= 160 and not _REPORTED_RE.search(ln) for ln in rest)
    return True


def _author_label_reads(text: str, name: str, m, where: str) -> bool:
    """For an author label line (_AUTHOR_LABEL_RE): what follows the label is a title phrase and nothing
    else (_is_title_line), it is not the tail cut inside a line, and the title makes the author China's
    diplomat (_title_is_chinese), read with the label blanked so that "Autor" or "Yazar" is not taken
    for a word of the title phrase."""
    t = m.start("t")
    le = text.find("\n", t)
    if not _is_title_line(text[t:len(text) if le < 0 else le].strip()):
        return False
    if where == "tail" and "\n" not in text[:m.start()] and len(text) >= TAIL_CHARS:
        return False
    probe = text[:m.start()] + "," + " " * (t - m.start() - 1) + text[t:]
    return _title_is_chinese(probe, name, t, m.end())


def _opinion_line_is_author_line(text: str, m) -> bool:
    """For the opinion section check: the titled name has to be on a byline or author line, not inside a
    running sentence. "Chen Daojiang" over "Embajador de China en México" counts unless the title line
    carries a speech or action verb; "Chinese Ambassador Chen Daojiang" counts when the line opens with
    the title (after "By" and the lead words: Chinese, H.E., former) and what follows the name is at
    most a short tag after a separator, with no speech or action verb."""
    ls = text.rfind("\n", 0, m.start()) + 1
    if "\n" in m.group(0):  # the name on a line of its own, the title on the next
        nl = m.group(0).rfind("\n") + m.start()
        return bool(_BIO_LINE_LEAD_RE.match(text[ls:m.start()])) and not _is_reported(text, nl + 1, m.end())
    le = text.find("\n", m.end())
    pre, post = text[ls:m.start()], text[m.end():len(text) if le < 0 else le]
    lead = re.compile(r"(?i)^[\s*\u2022>(\[\u2013\u2014-]*(?:(?:by|par|di|por|von|door|av|de)\s+)?(?:%s|%s)*$"
                      % (_TITLE_LEAD, _TITLE_LEAD_NATIVE))
    if not lead.match(pre) or _REPORTED_RE.search(post):
        return False
    return not re.search(r"\w", post) or bool(
        re.match(r"\s*[|,;:(/\u2013\u2014-]", post) and len(post.split()) <= 12)


def _diplomat_title_for_author(name: str, author: str, title: str, head: str, tail: str,
                               labels: str) -> Optional[str]:
    """The text that gives the listed name in the author field a diplomatic title, else None.
    The title has to describe the author, not merely appear near the piece: it is in the author
    field itself; or on the byline line at the top of the body naming the same person ("By Liu
    Jinsong, Chinese Ambassador to Australia"); or in a bio line in the head, tail or page labels
    that names the same person or speaks of the author ("Xiao Qian is the Chinese ambassador to
    Australia", "Liu Jinsong, Chinese Ambassador to Australia", "The writer is the Chinese Ambassador
    to Pakistan", or a footnote "*Embajador de China en Mexico" under an author field marked "*");
    or on a title line of its own directly under the name (_title_line_under_name: "Zhang Yang" and
    then "Embaixador da República Popular da China em Cabo Verde" in the page labels);
    or, in an opinion or commentary section, next to the name ("Chen Daojiang" with "Embajador de
    China en Mexico" on the next line, "Chinese Ambassador Chen Daojiang"). In each place the title
    must make the person China's diplomat (_title_is_chinese): "Li Dong, Ambassador of Singapore" and
    "Singaporean ambassador" do not fire. A title in the author field that names no country
    ("Liao Liqiang; Ambassador") counts when the page labels or the head of the body name China.
    A titled name in reported speech or narrative never counts (_bio_reads_as_author,
    _opinion_line_is_author_line): "Wang Yu, Chinese Ambassador to Australia, said ...", "Chinese
    Ambassador Wang Yu" inside a sentence of an opinion piece, "Wang Yu is the Chinese ambassador to
    Australia" in a paragraph that more reporting follows."""
    if _TITLE_RE.search(author):
        if _title_is_chinese(author, name, page="%s\n%s" % (labels, head)):
            return author.strip()
        if not _title_is_chinese(author, name, page="China"):
            return None  # the author field makes the person another country's diplomat
    nm = r"%s\*?" % name_pattern(name)

    def line_of(text: str, m) -> str:
        return text[text.rfind("\n", 0, m.start()) + 1:].split("\n", 1)[0].strip()

    for line in [ln for ln in head.splitlines() if ln.strip()][:3]:
        m = _HEAD_BYLINE_RE.match(line)
        if m and _TITLE_RE.search(line) and re.search(r"(?i)%s" % name_pattern(name), m.group(1)) \
                and _title_is_chinese(line, name) and not _REPORTED_RE.search(line):
            return line.strip()
    bio = re.compile(r"(?i)%s\s*(?:[,،、，・|]\s*|\s(?:is|was|serves as|has served as|est|était|è|es|fue|é|ist|war|"
                     r"je|jest|este|är|er|adalah|ialah|merupakan|là|ni)\s+|"
                     r"\s[\u2013\u2014-]\s+|[ \t]*(?=%s))%s" % (nm, _UNSPACED_TITLE_AHEAD, _TITLE_NEAR))
    for where, text in (("head", head), ("tail", tail), ("labels", labels)):
        for rx in (bio, _AUTHOR_IS_RE, _AUTHOR_IS_VERBLESS_RE, _AUTHOR_LABEL_RE):
            for m in rx.finditer(text):
                if (_author_label_reads(text, name, m, where) if rx is _AUTHOR_LABEL_RE
                        else _title_is_chinese(text, name, m.start(), m.end())) \
                        and _bio_reads_as_author(text, m, where, rx is bio):
                    return line_of(text, m)
    for text in (head, tail, labels):
        hit = _title_line_under_name(name, text)
        if hit:
            return hit
    if "*" in author:
        for m in re.finditer(r"(?im)^[ \t]*\*+[ \t]*%s" % _TITLE_NEAR, tail):
            if _title_is_chinese(tail, name, m.start(), m.end()):
                return line_of(tail, m)
    if _in_opinion_section(title, head, labels):
        # The title line under the name is a short title, not a sentence ("Chinese ambassador Xiao
        # Qian said on Monday that ..." under a namesake's byline ends in a full stop).
        near = re.compile(r"(?im)%s[ \t,;:|]*\n[ \t]*%s[^\n.!?]{0,80}$|%s[ \t]+%s"
                          % (nm, _TITLE_NEAR, _TITLE_RE.pattern[len("(?i)"):], nm))
        for text in (head, tail, labels):
            for m in near.finditer(text):
                if _title_is_chinese(text, name, m.start(), m.end()) and _opinion_line_is_author_line(text, m):
                    return re.sub(r"\s*\n\s*", " / ", m.group(0).strip())
    return None


def _byline_title_is_chinese(text: str, m) -> bool:
    """Post-check for diplomat_byline_head: the title in the byline line m matched ("By Li Dong,
    Ambassador of ...") must make the named person China's diplomat (_title_is_chinese). The regex
    only asks for a China word within 80 characters after the title, which China as the host country
    satisfies: "By Li Dong, Ambassador of Singapore to China" does not fire."""
    lead = len(m.group(0)) - len(m.group(0).lstrip())
    lo = m.start() + lead
    b = _HEAD_BYLINE_RE.match(text[lo:].split("\n", 1)[0])
    return bool(b and b.group(1).strip()) and _title_is_chinese(text, b.group(1).strip(), lo, m.end())


def _author_title_is_chinese(text: str, m) -> bool:
    """Post-check for diplomat_title_author_field: the title in the author field must make the person
    China's diplomat, by the title phrase test of _title_is_chinese (_title_side). The regex only asks
    for a China word within 80 characters of the title, which China as the host country satisfies:
    "Shen Bo, Nederlands ambassadeur in China", "Deutschlands China-Botschafterin" and "Li Dong;
    Ambassador to China" do not fire, "Xiao Qian, Chinese Ambassador to Australia" and "Embajador de
    China en México" do. _byline_title_is_chinese does not fit: an author field has no "By" to read the
    name from, and the signature lists titles _TITLE_RE does not read (counsellor, embajadora, charge
    d'affaires without the accent), so the title read here is the one the regex matched (group 2 when
    the title comes first, group 5 when the China word does), with any _TITLE_RE title from the start of
    the match to the end of the line, because the regex can swallow a second title ("Li Dong, Ambassador
    of Singapore to China; Xiao Qian, Chinese Ambassador"). A title naming no country counts only when
    the line names a Chinese embassy or consulate ("Wang Lutong, Ambassador, Embassy of the People's
    Republic of China")."""
    g = 2 if m.group(2) is not None else 5
    a = text.rfind("\n", 0, m.start(g)) + 1
    b = text.find("\n", m.end(g))
    line = text[a:] if b < 0 else text[a:b]
    titles = [re.compile(re.escape(m.group(g)), re.IGNORECASE).match(line, m.start(g) - a)]
    titles += [t for t in _TITLE_RE.finditer(line) if t.start() >= m.start() - a]
    for t in titles:
        side = _title_side(line, t)
        if side == "china" or (side == "bare" and _names_chinese_mission(line)):
            return True
    return False


# Checks a signature can name in pipeline/signatures.yaml (post_check): a regex match counts only when
# the check accepts it, and the first match that passes is the one reported.
# Where a paid placement disclosure attached to this item sits (post_check disclosure_marker, ruleset
# 2026.10.2), read on the disclosure scope: the author field, the page labels, and the body's first
# HEAD_CHARS and last TAIL_CHARS characters, in that order. The pattern's named group "marker" (the
# disclosure phrase) must stand
#   - in a page label line of the item's own (a JSON-LD sponsor, funder, articleSection, genre, keywords
#     or creator value, the source the page credits, or a meta value, which extract.page_labels writes
#     before the first JSON-LD line), anywhere: "free";
#   - at the opening of the headline tag (the first label line) or of a body line of at most
#     DISCLOSURE_LINE_MAX characters, after brackets or list marks, a short label ("Disclosure:") or
#     "This content is": "start";
#   - in the author field, or in any other label line, which is page chrome (kickers, ad slots, menu
#     items, sidebar links), only with the whole match, the state entity too, inside that one field or
#     line: "line". A desk byline ("Advertorial Desk", on every Laotian Times advertorial) says only
#     that someone paid, and the weak sponsored patterns send it to the model.
# A sponsored phrase in prose ("co-sponsored by the Shaanxi Provincial People's Government"), in a menu
# ("Contenuto sponsorizzato", "materiał partnera") or in an ad slot ("(Anzeige) Solaranlagen") no longer
# pairs with a state outlet named elsewhere on the page.
DISCLOSURE_CHECK = "disclosure_marker"
DISCLOSURE_LINE_MAX = 160
_OWN_LABEL_RE = re.compile(r"^(?:sponsor|funder|articleSection|genre|keywords|creator|source):")
_JSONLD_LINE_RE = re.compile(r"^(?:sponsor|funder|articleSection|genre|keywords|@type|creator|publisher):")
_LINE_LEAD_RE = re.compile(r"[ \t(\[*\u2022\u00b7|>_\u2013\u2014-]*")
_LEAD_LABEL_RE = re.compile(r"[^\W\d_][^\n:]{0,24}:[ \t]*")
_LEAD_THIS_RE = re.compile(r"(?i)(?:this|the following)[ \t]+(?:content|article|story|post|feature|page|piece|supplement|report)"
                           r"[ \t]+(?:is|was|has been)[ \t]+")


def _opening_starts(line: str, off: int) -> List[Tuple[str, int, int]]:
    """The "start" regions of a line: where a disclosure opening it may begin."""
    p0 = _LINE_LEAD_RE.match(line).end()
    starts = {p0}
    for rx in (_LEAD_LABEL_RE, _LEAD_THIS_RE):
        m = rx.match(line, p0)
        if m:
            starts.add(m.end())
    return [("start", off + p, off + p + 1) for p in sorted(starts)]


def disclosure_scope(author: str, labels: str, head: str, tail: str) -> Tuple[str, List[Tuple[str, int, int]]]:
    """(text, regions) of the disclosure scope: see DISCLOSURE_CHECK above."""
    regions: List[Tuple[str, int, int]] = []
    if author:
        regions.append(("line", 0, len(author)))
    off = len(author) + 1
    lines = (labels or "").split("\n")
    first_jsonld = next((k for k, ln in enumerate(lines) if _JSONLD_LINE_RE.match(ln)), None)
    for k, ln in enumerate(lines):
        if _OWN_LABEL_RE.match(ln) or (first_jsonld is not None and 0 < k < first_jsonld):
            regions.append(("free", off, off + len(ln)))
        elif k == 0:
            regions += _opening_starts(ln, off)
        else:
            regions.append(("line", off, off + len(ln)))
        off += len(ln) + 1
    for part in (head, tail):
        for ln in part.split("\n"):
            if ln.strip() and len(ln.strip()) <= DISCLOSURE_LINE_MAX:
                regions += _opening_starts(ln, off)
            off += len(ln) + 1
    return "\n".join([author, labels or "", head, tail]), regions


def _disclosure_ok(m, regions) -> bool:
    ms = m.start("marker")
    for kind, a, b in regions:
        if (kind == "free" and a <= ms < b) or (kind == "start" and ms == a) or \
                (kind == "line" and a <= m.start() and m.end() <= b):
            return True
    return False


def _disclosure_search(rx, text: str, regions):
    """The first match of rx whose marker stands where a disclosure of this item sits. Every start is
    tried, so a phrase in a menu does not hide a disclosure line that overlaps its match."""
    pos = 0
    while pos <= len(text):
        m = rx.search(text, pos)
        if not m:
            return None
        if _disclosure_ok(m, regions):
            return m
        pos = m.start() + 1
    return None


POST_CHECKS = {"title_is_chinese": _byline_title_is_chinese,
               "author_title_is_chinese": _author_title_is_chinese,
               # read on the disclosure scope with its regions, by _disclosure_search in match_signatures
               DISCLOSURE_CHECK: _disclosure_ok}


def apply_exclusions(text: str, sigs: Dict) -> Tuple[str, List[str]]:
    fired = []
    for ex_id, rx in sigs["exclusions"]:
        text, n = rx.subn(" ", text)
        if n:
            fired.append(ex_id)
    return text, fired


# Evidence spans. A match is stored with SPAN_CONTEXT characters of text on each side, widened
# outward to the nearest word boundary (whitespace, or punctuation that never sits inside a word or a
# link) by at most SPAN_REACH more, so the span neither starts nor ends inside a word and a long link
# cannot blow it up. An edge where text was left out is marked with an ellipsis (SPAN_MARK): set off by
# a space when the edge falls on a word boundary, right against the text when the reach ran out inside
# a long token. Where the edge sits in a script written without spaces (the ranges of
# export._NO_SPACE) the plain character window stays, unmarked, as it always was. Spans are evidence
# text only: no decision reads them. pipeline/export.py reads the marks; spans stored before them
# carry none and are still trimmed there as raw character windows.
SPAN_CONTEXT = 60
SPAN_REACH = 20
SPAN_MARK = "\u2026"
_SPAN_BREAK = set(',;!()[]{}<>|"\u201c\u201d\u00ab\u00bb')
_SPAN_NO_SPACE = re.compile("[\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff\u3001-\u303f\u3040-\u30ff\u3400-\u4dbf"
                            "\u4e00-\u9fff\uf900-\ufaff\uff01-\uff9f]")


def _span_break(ch: str) -> bool:
    return ch.isspace() or ch in _SPAN_BREAK or bool(_SPAN_NO_SPACE.match(ch))


def evidence_span(text: str, start: int, end: int) -> str:
    """The stored evidence for a match at text[start:end]: see SPAN_CONTEXT above."""
    n = len(text)
    lo, hi = max(0, start - SPAN_CONTEXT), min(n, end + SPAN_CONTEXT)
    lead = trail = ""
    if lo > 0 and not (_SPAN_NO_SPACE.match(text[lo - 1]) or _SPAN_NO_SPACE.match(text[lo])):
        p = next((p for p in range(lo, max(0, lo - SPAN_REACH) - 1, -1)
                  if p == 0 or _span_break(text[p - 1]) or text[p].isspace()), None)
        lo, lead = (lo, SPAN_MARK) if p is None else (p, SPAN_MARK + " " if p else "")
    if hi < n and not (_SPAN_NO_SPACE.match(text[hi - 1]) or _SPAN_NO_SPACE.match(text[hi])):
        q = next((q for q in range(hi, min(n, hi + SPAN_REACH) + 1)
                  if q == n or _span_break(text[q]) or text[q - 1].isspace()), None)
        hi, trail = (hi, SPAN_MARK) if q is None else (q, " " + SPAN_MARK if q < n else "")
    core = text[lo:hi].strip()
    return lead + core + trail if core else core


def match_signatures(title: str, body: str, author: Optional[str], sigs: Optional[Dict] = None,
                     labels: str = "") -> Dict:
    """Return dict with matches (list of {id, group, strength, scope, span}), exclusions, and decision."""
    sigs = sigs or load_signatures()
    body = body or ""
    author = author or ""
    title = title or ""
    body_clean, excl = apply_exclusions(body, sigs)
    scopes = {
        "author": author,
        "head": body_clean[:HEAD_CHARS],
        "tail": body_clean[-TAIL_CHARS:],
        "body": body_clean,
        "labels": labels or "",
        "labels_head_tail": "\n".join([labels or "", body_clean[:HEAD_CHARS], body_clean[-TAIL_CHARS:]]),
        # Article scoped: where a disclosure attached to THIS article sits. Page labels carry site
        # furniture (navigation strips, ad slots, site-wide menu items), so a bare sponsored phrase
        # found there says nothing about the article; only the paired state entity patterns may read them.
        "byline_head": "\n".join([author, title, body_clean[:HEAD_CHARS]]),
        "head_tail": "\n".join([body_clean[:HEAD_CHARS], body_clean[-TAIL_CHARS:]]),
        "any": "\n".join([author, title, body_clean]),
        "any_with_labels": "\n".join([author, title, body_clean, labels or ""]),
    }
    scopes["disclosure"], disclosure_regions = disclosure_scope(author, labels or "", scopes["head"], scopes["tail"])
    matches = []
    for gname, pats in sigs["groups"].items():
        for p in pats:
            text = scopes.get(p["scope"], body_clean)
            if not text:
                continue
            check = POST_CHECKS.get(p.get("post_check") or "")
            if p.get("post_check") == DISCLOSURE_CHECK:
                m = _disclosure_search(p["re"], text, disclosure_regions)
            else:
                m = p["re"].search(text) if not check else \
                    next((x for x in p["re"].finditer(text) if check(text, x)), None)
            if m:
                matches.append({"id": p["id"], "group": gname, "strength": p["strength"],
                                "scope": p["scope"], "span": evidence_span(text, m.start(), m.end())})

    # Diplomat list check. A listed name in the author field is strong when a diplomatic title
    # describes that person (_diplomat_title_for_author: the author field, the byline line, a bio
    # line naming the author, or next to the name in an opinion section). A title merely somewhere
    # in the head, tail or labels is not enough: Xiao Qian, Wang Yu, Li Dong and Chen Xu are common
    # names, and a namesake's story can mention an ambassador. A listed name without such a title is
    # weak, because a reporter can share a name with a diplomat, so it goes to the LLM as an A candidate.
    listed = [n for n in load_diplomats() if re.search(name_pattern(n), author, re.IGNORECASE)]
    if listed:
        why = next((w for w in (_diplomat_title_for_author(n, author, title, scopes["head"], scopes["tail"],
                                                           labels or "") for n in listed) if w), None)
        if why:
            span = author[:120] if why == author.strip() else "%s / %s" % (author[:60].strip(), why[:140])
            matches.append({"id": "diplomat_list_author", "group": "authored_by_state",
                            "strength": "strong", "scope": "author", "span": span})
        else:
            matches.append({"id": "diplomat_list_name_only", "group": "authored_by_state",
                            "strength": "weak", "scope": "author", "span": author[:120]})
    else:
        # The same signed piece with the byline at the top of the body instead of in the author
        # field, "By Liu Jinsong, Chinese Ambassador to Australia". diplomat_byline_head needs the
        # title before the country word, so it misses this adjective form. A listed name in a byline
        # position with a diplomatic title on the same line is as strong as the author field; with
        # no title on that line nothing fires, because a reporter can share a name with a diplomat.
        hit = _diplomat_head_byline(scopes["head"])
        if hit:
            matches.append({"id": "diplomat_list_author", "group": "authored_by_state",
                            "strength": "strong", "scope": "head", "span": hit[:120]})

    strong = [m for m in matches if m["strength"] == "strong"]
    weak_groups = {m["group"] for m in matches if m["strength"] == "weak"}  # hints never count
    state_entity = has_state_entity(scopes["any_with_labels"])
    if strong or (len(weak_groups) >= 2 and state_entity):
        decision = "A"
    elif matches:
        decision = "A_candidate"
    else:
        decision = "none"

    triggers = []
    trig_text = "\n".join([title, body_clean])
    for t_id, rx in sigs["llm_triggers"]:
        m = rx.search(trig_text)
        if m:
            triggers.append({"id": t_id, "span": evidence_span(trig_text, m.start(), m.end())})

    return {"matches": matches, "exclusions": excl, "decision": decision, "triggers": triggers,
            "state_entity": state_entity, "ruleset_version": sigs["ruleset_version"]}


def body_relevance(title: str, body: str, language: str, country: Optional[str] = None) -> Tuple[bool, str]:
    """The residual C versus not_relevant call. Returns (relevant, explanation). From ruleset
    2026.10.2 the names of Hong Kong, Macao and Taiwan count only when a mainland term is also present
    (gate.count residual), and BRI only beside another China term, so the label never rests on one of
    them alone."""
    distinct, total, in_title = gate.count(title, body, language, country, residual=True)
    why = "%d distinct China terms, %d occurrences%s" % (distinct, total, ", one in the headline" if in_title else "")
    if distinct >= BODY_RELEVANCE_MIN_HITS or total >= BODY_RELEVANCE_MIN_TOTAL or (in_title and total >= 2):
        return True, why
    return False, why


def labels_for(row) -> str:
    """Page chrome labels. Stored in the database at fetch time; the saved raw HTML is the
    fallback for rows fetched before the column existed."""
    try:
        stored = row["page_labels"]
    except (IndexError, KeyError):
        stored = None
    if stored:
        return stored
    p = row["raw_html_path"]
    if not p:
        return ""
    import gzip
    full = config.ROOT / p
    if not full.exists():
        return ""
    try:
        with gzip.open(full, "rt", encoding="utf-8") as fh:
            return extract.page_labels(fh.read())
    except Exception:
        return ""


def saved_html(row) -> Optional[str]:
    """The page as fetched, from the gzipped copy saved beside the body, or None."""
    p = row["raw_html_path"]
    if not p:
        return None
    import gzip
    full = config.ROOT / p
    if not full.exists():
        return None
    try:
        with gzip.open(full, "rt", encoding="utf-8") as fh:
            return fh.read()
    except Exception:  # noqa: BLE001  an unreadable archive copy changes nothing
        return None


def unreadable_reason(conn, row, body: str, html: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """(status, reason) when the stored body is not the article, else None. Paywalled when the saved
    page carries a paywall marker the fetch did not know; failed when the body is a challenge page,
    has no running text, or is an outlet template that does not carry its headlines. A label made on
    such a body rested on the headline and on boilerplate."""
    if html:
        pw, why = extract.detect_paywall(html, body)
        if pw:
            return "paywalled", why
    bad, why = extract.detect_unreadable(None, body, row["title"])
    if bad:
        return "failed", "unreadable: %s" % why
    from pipeline import body_dups
    why = body_dups.template_unreadable(conn, row, body)
    if why:
        return "failed", "unreadable: %s" % why
    return None


# A stored body with little running text is read again from its saved page when the page's own
# paragraphs hold at least this many times as much: the extractor took the wrong block (the Lowy
# Interpreter's standfirst). Paragraphs also hold related headlines, so this only prompts a new
# reading, kept when it at least doubles the running text; it never fails an article by itself.
REREAD_PARAGRAPH_RATIO = 5


def reread_saved(conn, row, html: str, short: Optional[str] = None, stub: Optional[str] = None) -> Optional[str]:
    """Run extraction again on the saved page of a body that is not the article, or of a short body
    (given as short), so a repaired extractor (extract.PRUNE_XPATH, extract._fallback_text) applies to
    pages already fetched without a request. Returns the new body, saved and fingerprinted, or None
    when the page yields no better one. stub says why the old body was not the article, for
    note_body_refresh (a short body is one)."""
    ex = extract.extract(html, row["url"])
    text = ex["text"]
    if not text or len(text) < extract.EMPTY_BODY_CHARS or extract.detect_paywall(html, text)[0] \
            or extract.detect_unreadable(html, text, row["title"])[0]:
        return None
    if short is not None and extract.prose_chars(text) < 2 * extract.prose_chars(short):
        return None
    old = _body_on_record(conn, row)
    store.update_article(conn, row["id"], body_hash=store.save_body(row["url_hash"], text), body_chars=len(text))
    note_body_refresh(conn, row["id"], old, stub or ("short body" if short is not None else None))
    from pipeline import body_dups
    body_dups.record(conn, row, text)
    return text


# A body replaced by one at least this many times as long was a stub: a model label made on it is
# judged again (note_body_refresh), as is one made on a body that was unreadable or extracted under
# an older reading of its template. A body replaced by a different one of about the same length (a
# page edited since) leaves the label alone.
REFRESHED_BODY_RATIO = 2


def _body_on_record(conn, row) -> Tuple[Optional[str], Optional[int]]:
    """(body_hash, body_chars) the database holds for the article now, before its body is replaced."""
    got = conn.execute("SELECT body_hash, body_chars FROM articles WHERE id=?", (row["id"],)).fetchone()
    return (got[0], got[1]) if got else (None, None)


def note_body_refresh(conn, article_id: int, old: Tuple[Optional[str], Optional[int]],
                      stub: Optional[str] = None) -> int:
    """After an article's body was replaced (extracted again or fetched again), mark its current model
    label body_refreshed when the new body differs materially from the one the label was made on: a
    different body_hash, and the old body a stub (stub gives why: unreadable, an older extraction),
    or at most 1/REFRESHED_BODY_RATIO of the new length. The model never read the new text, so the
    article is queued for it again (classify_llm.relabel_candidates); the label stays current until
    the new one arrives, and a rules label is never marked. old is (body_hash, body_chars) before the
    replacement (_body_on_record). Returns the number of labels marked."""
    old_hash, old_chars = old
    new_hash, new_chars = _body_on_record(conn, {"id": article_id})
    if not old_hash or not new_hash or new_hash == old_hash:
        return 0
    if not stub and old_chars is not None and (new_chars or 0) >= REFRESHED_BODY_RATIO * max(1, old_chars):
        stub = "short body (%d of %d characters)" % (old_chars, new_chars)
    if not stub:
        return 0
    import sqlite3
    try:
        return conn.execute(
            "UPDATE classifications SET body_refreshed=? WHERE article_id=? AND is_current=1 AND method='llm'",
            ("%s %s" % (store.utcnow(), stub), article_id)).rowcount
    except sqlite3.OperationalError:  # a database opened without the migration
        return 0


def withdraw_labels(conn, article_id: int) -> None:
    """Withdraw every current label of an article whose text turned out not to be the article. The
    rows stay, marked not current, as every superseded label does."""
    conn.execute("UPDATE classifications SET is_current=0 WHERE article_id=? AND is_current=1", (article_id,))
    conn.execute("UPDATE articles SET llm_pending=0 WHERE id=?", (article_id,))


def stale_body(conn, row) -> Optional[str]:
    """Why this article's stored body was extracted under an older reading of its template
    (extract.EXTRACTION_CHANGES), or None when it was not. The version is read from the database,
    not from the row, which may have been read before the body was extracted again."""
    if not extract.stale_extraction(row["url"], None):
        return None  # not a template whose extraction changed: no query
    import sqlite3
    try:
        got = conn.execute("SELECT extract_version FROM articles WHERE id=?", (row["id"],)).fetchone()
    except sqlite3.OperationalError:  # a database opened without the migration: nothing is versioned
        return None
    return extract.stale_extraction(row["url"], got[0] if got else None)


def reextract_saved_page(conn, row) -> Optional[str]:
    """Extract a stale body again from the page saved when the article was fetched, under the current
    extraction (saved_html and reread_saved, the same re-reading classify_article gives a body that
    is not the article), and store it with the current extraction version. None when the saved page
    is not on disk (the runner keeps bodies, not pages): the article is then fetched again. A reading
    that yields no usable body leaves the stored body as it is, for classify_article's checks; either
    way the page has been read under the current version and is not read again."""
    html = saved_html(row)
    if html is None:
        return None
    why = stale_body(conn, row)
    fresh = reread_saved(conn, row, html, stub="older extraction (%s)" % why if why else None)
    store.update_article(conn, row["id"], extract_version=config.EXTRACT_VERSION)
    conn.commit()
    return fresh or store.load_body(row["url_hash"]) or ""


def ensure_body(conn, row) -> str:
    """Return the article body, re-fetching it when the gzipped file is absent.

    Bodies are not committed, so a runner that did not fetch the article has no body
    for it. Classifying an empty body would silently produce not_relevant, which is
    the wrong answer, so the article is fetched again under the usual politeness rules.

    A body extracted under an older reading of its outlet's template (stale_body) is not the
    article as the page reads now, so it is discarded: extracted again from the saved page when
    that is on disk, otherwise fetched again by the same path and rules as a missing body
    (robots.txt, pacing, the project's user agent, paywalls left alone). When neither can be done
    the article is treated as having no body and its existing label stands.

    A model label made on a stub that the new body replaces is queued for the model again
    (note_body_refresh).
    """
    body = store.load_body(row["url_hash"])
    stale = stale_body(conn, row)
    if body and stale:
        fresh = reextract_saved_page(conn, row)
        if fresh is not None:
            return fresh
        body = None
    if body:
        return body
    from pipeline import fetch_articles, registry
    prior_status = row["status"]
    if row["fetch_attempts"] >= REFETCH_ATTEMPT_LIMIT:
        return ""
    if not registry.is_mine(row["outlet_id"]):
        return ""
    old = _body_on_record(conn, row)
    res = fetch_articles.process_article(store.connect, row)
    if res["status"] == "fetched":
        conn.execute("UPDATE articles SET status=? WHERE id=?", (prior_status, row["id"]))
        note_body_refresh(conn, row["id"], old, "older extraction (%s)" % stale if stale else None)
        conn.commit()
        return store.load_body(row["url_hash"]) or ""
    if res.get("text_hash") and res["text_hash"] == row["body_hash"]:
        # The page served now, paywalled or not the article, is the very text the stored label was
        # made on: the label rested on it and goes. The status the re-fetch recorded stands.
        withdraw_labels(conn, row["id"])
        conn.commit()
        return ""
    if prior_status in ("awaiting_llm", "llm_submitted", "classified"):
        # A candidate stays a candidate and a classified article keeps its label. The failed
        # re-fetch is recorded in fail_reason and fetch_attempts, not by demoting the article.
        conn.execute("UPDATE articles SET status=?, fail_reason=? WHERE id=?",
                     (prior_status, "refetch_%s:%s" % (res["status"], res.get("reason")), row["id"]))
        conn.commit()
    return ""


def route_lookalike(conn, article_id: int, match: Dict) -> None:
    """Send an article that nearly repeats a state origin item to the model as a state origin
    candidate. The match (the state origin item's article id, outlet, headline, the similarity and the
    method) is kept in llm_trigger; the model is shown that item's headline and outlet as context."""
    trig = {"a_candidate": [], "triggers": [], "spans": [], "lookalike": match}
    # A rules label from an earlier ruleset or run is withdrawn, not deleted; the LLM supplies the next one.
    conn.execute("UPDATE classifications SET is_current=0 WHERE article_id=? AND method='rules'", (article_id,))
    store.update_article(conn, article_id, status="awaiting_llm", llm_pending=1,
                         llm_trigger=json.dumps(trig, ensure_ascii=False))


def classify_article(conn, row, looks: Optional["lookalike.RouteIndex"] = None) -> Dict:
    """Label or route one article. looks is the stage's index of state origin items
    (lookalike.build_index over the stage's rows); without one, an index for this row alone is built."""
    body = ensure_body(conn, row)
    if not body:
        # The re-fetch recorded why (paywalled, blocked, failed). Leave that status in place.
        return {"outcome": "body_unavailable"}
    html = saved_html(row)
    bad = unreadable_reason(conn, row, body, html)
    fresh = None
    if bad and bad[0] == "failed" and html:
        fresh = reread_saved(conn, row, html, stub=bad[1])
    elif not bad and html and extract.prose_chars(body) < extract.FALLBACK_PROSE_CHARS and \
            extract.paragraph_prose_chars(html) >= REREAD_PARAGRAPH_RATIO * max(1, extract.prose_chars(body)):
        fresh = reread_saved(conn, row, html, short=body)
    if fresh:
        body = fresh
        bad = unreadable_reason(conn, row, body, html)
    if bad:
        # Not the article: no label, and the article drops out of the counts as unreadable.
        withdraw_labels(conn, row["id"])
        store.update_article(conn, row["id"], status=bad[0], fail_reason=bad[1])
        return {"outcome": "unreadable"}
    res = match_signatures(row["title"], body, row["author"], labels=labels_for(row))
    fired = [m["id"] for m in res["matches"]]
    if res["decision"] == "A":
        evidence = res["matches"][0]["span"]
        store.insert_classification(
            conn, row["id"], "rules", "A", 1.0, evidence_quote=evidence,
            reasoning="Deterministic signature match: %s" % ", ".join(fired),
            signatures_fired=fired, ruleset_version=res["ruleset_version"],
            route=route_of(res["matches"]),
        )
        return {"outcome": "A"}
    candidate = res["decision"] == "A_candidate"
    if candidate and arrival_of(row["gate_terms"]) != "editorial_feed":
        # Section feeds (press_release, sponsored, partner) skip the keyword gate by design
        # (fetch_feeds.py), so a weak signature here is the only thing standing between an
        # unrelated item and a model call. These outlets' own generic paid-content and wire
        # disclosures trip a weak signature on stories with nothing to do with China; require
        # the item to clear the same body-level China relevance test the residual rule below
        # uses before its weak signature earns a model call. A strong signature already
        # returned above and is unaffected; an official sourcing trigger still routes on its
        # own, independent of this check.
        relevant, _ = body_relevance(row["title"], body, row["language"], row["country"])
        if not relevant:
            candidate = False
    if candidate or res["triggers"]:
        trig = {"a_candidate": fired, "triggers": [t["id"] for t in res["triggers"]],
                "spans": [t["span"] for t in res["triggers"]][:3] + [m["span"] for m in res["matches"]][:2]}
        # A rules label from an earlier ruleset is withdrawn, not deleted; the LLM supplies the next one.
        conn.execute("UPDATE classifications SET is_current=0 WHERE article_id=? AND method='rules'", (row["id"],))
        store.update_article(conn, row["id"], status="awaiting_llm", llm_pending=1, llm_trigger=json.dumps(trig))
        return {"outcome": "llm"}
    # Nothing in the text names the state, but the article may be state text run without its credit.
    looks = looks if looks is not None else lookalike.build_index(conn, [row])
    if len(looks):
        match = looks.match(row, lookalike.fingerprint_of(conn, row["id"], body) if looks.sigs else None)
        if match:
            route_lookalike(conn, row["id"], match)
            return {"outcome": "lookalike"}
    relevant, why = body_relevance(row["title"], body, row["language"], row["country"])
    if relevant:
        store.insert_classification(
            conn, row["id"], "rules", "C", 0.75,
            reasoning="No state-origin signature and no official sourcing trigger; the body substantively concerns China (%s)." % why,
            signatures_fired=["residual_c"], ruleset_version=res["ruleset_version"],
        )
        return {"outcome": "C"}
    store.insert_classification(
        conn, row["id"], "rules", "not_relevant", 0.7,
        reasoning="No state-origin signature, no official sourcing trigger, and the body barely mentions China (%s)." % why,
        signatures_fired=["residual_not_relevant"], ruleset_version=res["ruleset_version"],
    )
    return {"outcome": "not_relevant"}


def run(conn, run_id: str, deadline: Optional[float] = None, status: str = "fetched") -> Dict:
    log_id = store.start_stage(conn, run_id, "classify_rules")
    # A ruleset that does not compile must stop the stage loudly, never mark articles failed one by one.
    load_signatures()
    load_diplomats()
    # Repair articles a broken ruleset marked failed on an earlier run: queue them for a fresh fetch.
    repaired = conn.execute("UPDATE articles SET status='queued', fail_reason=NULL WHERE fail_reason LIKE 'classify_exception:%'").rowcount
    # Repair articles an earlier failed body re-fetch demoted to failed although they still carry a
    # current label: the label stands, the status goes back to classified.
    relabelled = conn.execute(
        """UPDATE articles SET status='classified' WHERE status='failed'
             AND EXISTS (SELECT 1 FROM classifications c WHERE c.article_id=articles.id AND c.is_current=1)"""
    ).rowcount
    if repaired or relabelled:
        conn.commit()
    rows = store.articles_by_status(conn, status, limit=100000)
    counts = {"input": len(rows), "A": 0, "llm": 0, "lookalike": 0, "C": 0, "not_relevant": 0, "skipped_deadline": 0,
              "repaired": repaired, "relabelled": relabelled, "body_unavailable": 0, "unreadable": 0}
    looks = lookalike.build_index(conn, rows)
    for r in rows:
        if deadline and time.time() > deadline:
            counts["skipped_deadline"] += 1
            continue
        try:
            out = classify_article(conn, r, looks)
            counts[out["outcome"]] += 1
        except Exception as exc:
            store.update_article(conn, r["id"], status="failed", fail_reason="classify_exception:%s" % type(exc).__name__)
            counts["failed"] = counts.get("failed", 0) + 1
        conn.commit()
    # Articles labelled before their state origin counterpart was.
    counts["lookalike_sweep"] = sweep_lookalikes(conn, deadline=deadline)
    # Carry articles labelled under an older ruleset forward, as far as the budget allows.
    counts["reclassified"] = reclassify(conn, run_id, deadline=deadline, limit=RECLASSIFY_PER_RUN)
    store.finish_stage(conn, log_id, True, counts)
    return counts


def reclassify(conn, run_id: str, since: Optional[str] = None, deadline: Optional[float] = None,
               limit: Optional[int] = None) -> Dict:
    """Reclassify forward under the current ruleset. Earlier rows stay, marked not current.
    Only rows whose current label carries an older ruleset are touched, so the call is
    idempotent and can be spread over several hourly runs under a deadline. Rows whose stored
    body predates their template's current extraction (stale_body) are touched too, whatever
    the ruleset of their label: ensure_body extracts the body again before they are classified.

    From ruleset 2026.10.2 the rows are taken in order of what an old label can do wrong: current rules
    state origin labels first (a misfire there is published at confidence 1.0), then the other rules
    labels and the articles without a label, then the model labels, each by id, an article whose fetch
    attempts are spent last within its group. A model label is kept unless the rules now say state
    origin or its body is not the article (as before); once the current rules have read its body and
    kept it, it carries the current ruleset_version, so it leaves the queue. Until then a kept model
    label stayed under its old version and was taken again on every run: on the 2026-10-12 snapshot
    about 9,800 of them came first by id, and with RECLASSIFY_PER_RUN the queue never reached rules
    labels behind them (the Computer Modelling Group releases of 16 September, labelled state origin
    under 2026.09.7, were still published)."""
    q = """SELECT a.* FROM articles a
           WHERE a.status IN ('classified','awaiting_llm')
             AND (NOT EXISTS (SELECT 1 FROM classifications c WHERE c.article_id=a.id AND c.is_current=1 AND c.ruleset_version=?)"""
    params = [config.RULESET_VERSION]
    # and, under any ruleset, articles of a template whose extraction changed since their body was stored
    # (stale_body filters out bodies stored under a later version than the change but not the current one)
    for _changed, prefix, _why in extract.EXTRACTION_CHANGES:
        q += " OR (substr(a.url, 1, ?)=? AND (a.extract_version IS NULL OR a.extract_version<>?))"
        params += [len(prefix), prefix, config.EXTRACT_VERSION]
    q += ")"
    if since:
        q += " AND a.discovered_at >= ?"
        params.append(since)
    q += """ ORDER BY COALESCE((SELECT CASE WHEN c.method='rules' AND c.category='A' THEN 0 WHEN c.method='llm' THEN 2 ELSE 1 END
                               FROM classifications c WHERE c.article_id=a.id AND c.is_current=1
                               ORDER BY c.id DESC LIMIT 1), 1),
                      CASE WHEN a.fetch_attempts >= ? THEN 1 ELSE 0 END, a.id"""
    params.append(REFETCH_ATTEMPT_LIMIT)
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(q, params).fetchall()
    counts = {"input": len(rows), "A": 0, "llm": 0, "lookalike": 0, "C": 0, "not_relevant": 0, "body_unavailable": 0,
              "unreadable": 0, "skipped_deadline": 0, "kept_llm": 0}
    looks = lookalike.build_index(conn, rows)
    for r in rows:
        if deadline and time.time() > deadline:
            counts["skipped_deadline"] += 1
            continue
        cur = store.current_classification(conn, r["id"])
        stale = stale_body(conn, r)
        if cur and cur["ruleset_version"] == config.RULESET_VERSION and not stale:
            counts["already_current"] = counts.get("already_current", 0) + 1
            continue
        if stale:
            counts["stale_body"] = counts.get("stale_body", 0) + 1
        if cur and cur["method"] == "llm":
            # keep LLM judgements unless the rules now say A, or the body they were made on is not the article;
            # a stale body is extracted again first, so the rules read the article and not the old extraction;
            # a kept label made on the stub is marked body_refreshed there and goes back to the model
            body = (ensure_body(conn, r) if stale else store.load_body(r["url_hash"])) or ""
            res = match_signatures(r["title"], body, r["author"], labels=labels_for(r))
            if res["decision"] != "A" and not (body and unreadable_reason(conn, r, body, saved_html(r))):
                if body:
                    # read under the current ruleset and kept: carried forward, so it is not taken again
                    conn.execute("UPDATE classifications SET ruleset_version=? WHERE id=?",
                                 (config.RULESET_VERSION, cur["id"]))
                    conn.commit()
                    counts["kept_llm"] += 1
                continue
        out = classify_article(conn, r, looks)
        counts[out["outcome"]] += 1
        conn.commit()
    return counts


def sweep_lookalikes(conn, now: Optional[dt.datetime] = None, deadline: Optional[float] = None,
                     hours: float = LOOKALIKE_SWEEP_HOURS) -> Dict:
    """The lookalike check again, for articles already labelled independent journalism or not
    relevant by rules, against the state origin labels made in the last `hours`. The credited copy is
    often labelled after the uncredited one: in the same rules pass, or by the model later in the run
    (on the 2026-10-12 database, 67 of the 131 lookalikes had their counterpart labelled after them).
    Each new state origin label is compared with the rules labelled articles discovered (or credibly
    published, lookalike.published_ts) from lookalike.WINDOW_AFTER_DAYS before it to
    lookalike.WINDOW_BEFORE_DAYS after it (the route's window seen from the other side), however long
    ago they were labelled; the route's own test then decides each pair. An article routed
    here leaves the rules labels, so a repeated sweep does not route it twice. An article whose body
    is not on disk and cannot be fetched again by this collector keeps its rules label: the model
    could not read it, and it would wait for a judgement that never comes."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(hours=hours)).replace(microsecond=0).isoformat()
    out = {"refs": 0, "checked": 0, "routed": 0, "stopped_deadline": False}
    # The same references as the stage index: only state origin labels a credit supports.
    credited, routes = lookalike.credited_reference_sql("c")
    refs = [dict(r) for r in conn.execute(
        """SELECT a.id AS article_id, a.outlet_id, a.language, a.title, a.summary, a.published_at, a.discovered_at,
                  a.day_of_record, o.name AS outlet_name
           FROM classifications c JOIN articles a ON a.id = c.article_id LEFT JOIN outlets o ON o.id = a.outlet_id
           WHERE c.is_current = 1 AND c.category = 'A' AND c.classified_at >= ? AND %s ORDER BY a.id""" % credited,
        [since] + list(routes))]
    span = lookalike._span(refs, lookalike.WINDOW_AFTER_DAYS, lookalike.WINDOW_BEFORE_DAYS)
    if span is None:
        return out
    out["refs"] = len(refs)
    from pipeline import registry
    loaded = []

    def outlets():
        if not loaded:
            loaded.append(registry.load_outlets())
        return loaded[0]
    looks = lookalike.RouteIndex(refs, lookalike.fingerprints(conn, [r["article_id"] for r in refs]),
                                 boiler=lookalike.feed_boilerplate(conn))
    where, params = lookalike._span_sql(span)
    rows = [r for r in conn.execute(
        """SELECT a.id, a.outlet_id, a.language, a.title, a.summary, a.published_at, a.discovered_at, a.day_of_record,
                  a.url_hash, a.fetch_attempts
           FROM articles a JOIN classifications c ON c.article_id = a.id
           WHERE %s
             AND c.is_current = 1 AND c.method = 'rules' AND c.category IN ('C', 'not_relevant')
           ORDER BY a.id""" % where, params) if lookalike._in_span(r, span)]
    sigs = lookalike.fingerprints(conn, [r["id"] for r in rows]) if looks.sigs else {}
    for i, r in enumerate(rows):
        if deadline and i % 500 == 0 and time.time() > deadline:
            out["stopped_deadline"] = True
            break
        out["checked"] += 1
        match = looks.match(r, sigs.get(r["id"]))
        if not match:
            continue
        if not store.body_path(r["url_hash"]).exists() and (
                (r["fetch_attempts"] or 0) >= REFETCH_ATTEMPT_LIMIT or not registry.is_mine(r["outlet_id"], outlets())):
            out["unreadable"] = out.get("unreadable", 0) + 1
            continue
        route_lookalike(conn, r["id"], match)
        out["routed"] += 1
    conn.commit()
    return out


if __name__ == "__main__":
    import sys
    conn = store.connect()
    if len(sys.argv) > 1 and sys.argv[1] == "reclassify":
        print(json.dumps(reclassify(conn, "manual", since=sys.argv[2] if len(sys.argv) > 2 else None)))
    else:
        print(json.dumps(run(conn, "manual")))
