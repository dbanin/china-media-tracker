# Changelog

Every change to the ruleset version is recorded here with what it altered,
because reclassification changes historical numbers and that must be traceable.
Code changes that do not alter classification are not listed.

## Data schema 6 (2026-09-20)

No label changes. Unverified relay can now be mapped on its own, beside state
origin, and the way its counts are released changed, which anyone reading the
CSV exports needs to know.

- Before any reliability study has run, relay counts are shown as provisional
  instead of withheld: one model's judgement, marked as unchecked everywhere it
  appears. In the CSV exports the unverified_relay columns are filled where they
  used to be blank, and relay_status reads provisional. A new column,
  unverified_relay_underlying_items, counts distinct items behind the placements.
- Provisional means not checked yet, never checked and failed. Once a study
  reports, the counts are published if kappa on the relay versus independent
  journalism judgement is at least 0.6 and withheld if it is not.
- The study runs by itself: the same model re-judges a stratified sample of 250
  labelled articles through the Batches API, at most once a calendar month, when
  the monthly model budget can pay for it. Its pairs are kept in
  data/export/reliability/.
- Daily files carry tb (relay among the items the top outlets published), so
  relay has a share of monitored output, and theme counts carry a fourth value
  for relay. meta.json gains relay_provisional and relay_study.
- Fixed on the way: recording a second rater study raised after its calls had
  been paid for, and the per language result of a model study was stored under
  a key the export never read, so a language could not be withheld on it.

## Ruleset 2026.09.7 (2026-09-15)

Every one of the 229 published state origin labels was read, not sampled. Six
were wrong, 2.6 percent, and all six are the same kind of mistake: an outlet's
own coverage of Chinese state activity treated as Chinese state text. That is
the distinction the instrument exists to draw, so the correction makes the
headline number smaller and more defensible.

Four rested on a cooperation phrase sitting near a Chinese entity: a Bahraini
culture authority hosting a Chinese show "in cooperation with the Chinese
embassy", a Senegalese report on a panel CGTN organised, a Sierra Leonean report
on a Chinese medical team "in partnership with" a local clinic, a Syrian
business story citing Xinhua. Those phrases are now removed from the paired
sponsored patterns and from the bare Arabic, Indonesian and Russian patterns,
which require a real disclosure term instead, and sponsored_prose_phrases_head_tail
is retired. One rested on xinhua_writer_byline matching "According to a report by
Xinhua reporter" mid sentence in a locally bylined article; that pattern is now
anchored to a byline position. One rested on a diplomat name inside an author
field that held navigation text; author fields are now cleaned in extraction,
which is where the defect was, and backfilled over stored rows.

Five further labels were correct but misrouted: Antara republishing Xinhua copy
in Indonesian, recorded as a sponsored disclosure because no credit pattern
matched a co-credit byline. Two additive patterns, xinhua_co_credit_author and
xinhua_translator_credit, move them to wire credit, so the route table stops
overstating sponsored placements by five.

immediapress_with_state_entity and wire_stamp_with_state_entity are untouched.
They carry 73 labels, the Italpress "(XINHUA/ITALPRESS)" syndication and the PR
Newswire releases sourced to Xinhua and Global Times, and those are exactly what
the paired patterns are for.

Expect state origin to fall from 229 to about 223 as articles are reclassified
forward at 2,000 per hourly run. The mixed ruleset marks stay on the timeline
until that finishes, which is the honest reading: labels made under different
rules should not be read as one series.

## Ruleset 2026.09.6 (2026-09-15)

Bare sponsored disclosure patterns are now scoped to the article instead of the
whole page. The eleven weak sponsored_* patterns move from any_with_labels to a
new byline_head scope (author field, headline, first 400 characters of the
body), sponsored_prose_phrases_head_tail moves to a head_tail scope that does
not read page labels, and cairorcs_studio moves to labels_head_tail. Every
pattern that pairs a disclosure with a named Chinese state entity is unchanged
and still reads page chrome, because there the entity names who paid.

Page labels capture site furniture, so the unscoped patterns fired on
navigation strips, ad slots and menu entries rather than on a disclosure
attached to the article: a staff bylined New Straits Times sports report with a
"Branded Content" item in its "What To Read Next" strip, a Focus war report
beside an "(Anzeige) Solaranlagen" ad slot, a site-wide "Sponsored Content" menu
entry. Measured on the model queue, 459 of 2,586 waiting articles, 18 percent,
were queued this way.

Measured effect over 673 articles: 194 fired before, 75 still fire, 119 stop.
None of the 119 had "Advertorial" or "Xinhua" in the author field, and all 52
that did still fire, so signed advertorials and wire bylines are untouched. In
that set no article moved into or out of state origin; the changes are
candidates that are no longer sent to the model. Two fixtures were added from
real articles, and articles labelled under 2026.09.5 are reclassified forward
during the hourly runs, 2,000 per run.

## Gate 2026.09.6 (2026-09-15)

Made after a methodology review. The signature list is unchanged, so the
ruleset version stays 2026.09.5 and no article is relabelled; these change what
reaches classification and what the interface may publish. The gate carries its
own version because it decides what enters the corpus, and that boundary moved
today.

Gate decisions are recorded per item and never revisited, so this gate applies
to items discovered from 2026-09-15 onward. Items the old gate rejected are not
re-examined, and those rejected more than three days ago are already pruned.

What re-examining them would recover was measured rather than assumed. Running
the new gate over the 151,367 rejections still stored admits 7: all of them on
the new province and city terms (Jiangxi, Hengqin, Shanxi, Guangdong, Dalian),
none on the state origin signature exemption, and two of the seven are the
Dalian Commodity Exchange in palm oil reports, which the residual relevance rule
drops afterwards. A re-gate pass is therefore not worth the run time. That
figure bounds only what is recoverable from rejections still stored: it cannot
speak for items already pruned, or for feed entries that rolled off a feed
before any poll saw them, so it is not evidence that the earlier gate lost
nothing.

The relevance gate now carries every Chinese province, autonomous region and
the largest cities, and an item whose title or summary already fires a state
origin decision passes whatever its keywords say. In three days of rejected
items, province-only headlines such as a Jiangxi cooperation anniversary and a
Dalian APEC meeting were discarded before any signature ran. Outlets in
Singapore and Malaysia no longer pass on phrases about their own Chinese
communities (home_phrases in pipeline/keywords.yaml). Items from an outlet's
press release, sponsored or partner section skip the gate.

When the daily model call ceiling binds, the articles sent are a stratified
random draw across countries with the allocation recorded per day, instead of
the oldest first.

Every state origin label now stores its route (the signature group that
established it) and every article its arrival (editorial feed or a release
section). Existing labels are filled at the next export. Every feed poll records
whether its window reached the previous poll, with an estimate of missed items,
and the relay collector's hourly passes travel in its bundle.

The interface shows unverified relay as not yet measured until the model has
run, withholds relay counts until kappa on the relay versus independent
journalism distinction reaches the threshold, defaults to state origin only,
renames the share of all published items to a share of monitored output with a
floor of five outlets, fixes the color scale across dates, and adds a no data
state for languages without a keyword list.

Themes version 2026.09.4 tags on the whole body instead of the first 800
characters; every article is re-tagged at the next export.

## Ruleset 2026.09.5 (2026-09-14)

A bare "CGTN" line now counts as a credit only in the last 600 characters
of the body (cgtn_bare_line_tail). Under 2026.09.4 the same line anywhere in
the body was a strong credit, and an Indian Express explainer that surveys
Chinese outlets under the headings "Xinhua", "CGTN" and "Global Times" was
labelled state origin. Credit lines of the form "Source: CGTN" and the
dateline form are unchanged. Articles labelled under 2026.09.4 are
reclassified forward during the hourly runs.

## Ruleset 2026.09.4 (2026-09-11)

Two weak signatures from different groups now give a state origin label
only when a Chinese state entity is named somewhere in the article or its
page chrome: a state media outlet, an embassy or consulate, a ministry, a
provincial or municipal government, a party body, or a state cultural or
tourism agency. Under 2026.09.3 a Chery car advertorial in a Pakistani
outlet became state origin from an ad label plus a "not edited by the
publisher" note, with no state entity anywhere in it. Such pieces now go to
the model as candidates. Strong signatures are unchanged. Articles labelled
under 2026.09.3 are reclassified forward during the hourly runs.

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
