# Changelog

Every change to the ruleset version is recorded here with what it altered,
because reclassification changes historical numbers and that must be traceable.
Code changes that do not alter classification are not listed.

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
