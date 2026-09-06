# Changelog

Every change to the ruleset version is recorded here with what it altered,
because reclassification changes historical numbers and that must be traceable.
Code changes that do not alter classification are not listed.

## Ruleset 2026.09.3 (2026-09-05)

Parenthetical credits (cgtn_credit, china_daily_credit, global_times_credit,
peoples_daily_credit, china_news_service_credit, cri_credit) now count only in
the dateline form at the start of a line, "BEIJING (CGTN) --". Under 2026.09.2
the prose "China Global Television Network (CGTN) released a video" gave a
state origin label to a report about CGTN.

A new exclusion, syndication_disclaimer, removes wire boilerplate such as
"Except for the headline, this article has not been edited by ..." before
the sponsored placement patterns run, so agency copy in Indian outlets is no
longer routed as a sponsorship candidate.

Three official sourcing triggers were narrowed. cites_cctv requires
broadcaster or China context in the same sentence on either side of the
acronym, because "Detectives have reviewed CCTV" was routing crime stories
as candidates. mofa_spokesperson and state_media_reported_en require a
Chinese entity in the same sentence, because "Foreign Ministry spokeswoman
Maria Zakharova" and "according to state media" about the Korean Central
News Agency were treated as Chinese sourcing.

The residual rule that separates independent journalism from not relevant
now counts occurrences as well as distinct terms: an article is independent
journalism when the body has at least three distinct China terms, or at
least five occurrences, or a China term in the headline and at least two
occurrences. Under 2026.09.2 a report that said "China" twenty times and
nothing else scored one distinct term and was labelled not relevant.

The relevance gate changed alongside, without a version of its own because
gate decisions are recorded per item and never revisited: plain "Sino" is
now the prefix form "Sino-" only, since it matched the Spanish and Italian
conjunction; TikTok, ByteDance, Huawei, BYD, Alibaba, Tencent, Sinopec,
Mainland and CCTV count only with a second term; and outlets in Hong Kong,
Macau and Taiwan no longer pass the gate on the name of their own territory.

Articles classified under 2026.09.2 are reclassified forward during the
hourly runs; earlier rows remain in the database marked not current.

## Ruleset 2026.09.2 (2026-09-03)

Added a third signature strength, hint, that never contributes to a Category A
decision and only routes the article to the LLM as an A candidate. The press
release wire stamp pattern (wire_stamp_alone) moved from weak to hint. Under
2026.09.1 a company release carried by PR Newswire in a partner outlet whose
page chrome says "Advertorial" counted as two weak signatures from two groups
and became Category A, which wrongly labelled Huawei and vivo product
launches as state origin. Under 2026.09.2 such items go to the LLM, which
must find a Chinese state issuer before answering A.

The diplomat title patterns on the head and tail of the body
(diplomat_title_head, diplomat_title_tail) moved from strong to weak. A
news report that opens by naming "Wu Jie, ambassadeur de Chine en Côte
d'Ivoire" matched the head pattern and became Category A although a
journalist wrote it. Signed pieces still become A by rules through the
author field (diplomat_title_author_field, diplomat_list_author) and
through the new explicit byline pattern diplomat_byline_head; everything
else goes to the LLM. Articles classified
under 2026.09.1 were reclassified forward; the earlier rows remain in the
database marked not current.

## Ruleset 2026.09.1 (2026-09-02)

Initial ruleset. Signature groups: credit and dateline, distribution stamps,
sponsored placement disclosures per language, authored by state. See
pipeline/signatures.yaml for the patterns and tests/fixtures for the evidence
each pattern was checked against.
