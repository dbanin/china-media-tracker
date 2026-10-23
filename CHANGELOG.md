# Changelog

Changes to the ruleset, the relevance gate, the model prompt, the data schema,
the collectors, the measures and the site are recorded here with what each
altered, so that a change in historical numbers can be traced.

## Site: the Start here section shares one column and one spacing scale (2026-10-23)

A new read only script, scripts/country_export.py, writes the aggregate country export for the paper: counts only, for Italy, Canada, Australia and Singapore, from the committed audit files, docs/data and sources/outlets.yaml, one CSV per table with a country column and a README with every definition and limitation. It keeps three tiers apart in every table (tier 1 split into 1a found by the rules and 1b the evidence route named by the model; tier 2 hand confirmed, 0 because no hand coding exists; tier 3 undetermined, never added in), removes the copies the audit lines mark with counted_as (218 in Italy, 4,212 in Australia), and recovers the model uncredited copies of undetermined items, which the audit files cannot show, from the site's own daily state origin counts (10 in Canada, 8 in Australia and 33 in Singapore, none in Italy), so its state origin totals equal the site's (505, 17, 49 and 31 in the export of 23 October 2026 at 04:50 UTC). Themes map the tracker's rule based themes onto a fixed list of twelve with keyword rules on the headline and gate terms for Taiwan, Hong Kong, the South China Sea, Xinjiang and Tibet and trade, under a stated priority order. An overlay option applies session labels not yet ingested by the runner as the ingest would. No article text, headline, quote or address reaches any file; tests/test_country_export.py checks that with 13 tests.

## Session labels for the 1,353 articles waiting while the API account is paused (2026-10-23)

With the API account at its usage limit since 2026-10-19, 1,353 articles were waiting for the model at the 2026-10-23 04:50 UTC export. On the owner's machine their pages were fetched again through the tracker's own fetch code (robots.txt, the per domain pacing and Crawl-delay, the declared user agent, no paywall bypass, the runner's extractor and body cap), 1,289 of them successfully, and judged from the headline and body only, never the outlet, country or URL, with the current system prompt and output schema (prompt 2026-10-17) by claude-opus-5-5 in an interactive session, not by the API's claude-sonnet-5; every label says so in its model_version, "claude-opus-5-5 (session, prompt 2026-10-17)". The labels are in data/labels/session-2026-10-23.jsonl: 832 independent journalism, 269 state origin, 102 unchecked state sourcing and 86 not about China; of the state origin labels 154 name a credit line, distribution stamp or diplomat's byline and count, and 115 rest on the issuer's name or a sponsored notice only the model saw and are undetermined, in no count. The 51 articles whose pages answered 403 or 404 are listed in data/labels/api_first.jsonl for the API (46 added, 5 were already there); 13 from the retired relay outlets were not fetched. The runner applies the labels at its next run through python -m pipeline.labels ingest, only to articles still waiting.

## Country notes: Chad after the session labels (2026-10-23)

Session labels made under prompt 2026-10-17 must now carry issuer_type and state_basis, as the output schema requires of the API. A label naming that prompt or a later one without either field, or with a value outside classify_llm.ISSUER_TYPES or STATE_BASES, is counted as malformed and skipped, and its article keeps waiting. Before today such a line was accepted and, if a field was missing, stored as a state origin label with no issuer and the unattributed route, so it would have been undetermined without anyone being told. A valid line already reached the stored route through its raw line; pipeline.labels now hands both fields to classify_llm.store_result itself, the way parse_response does, so credit_line is stored as wire_credit, issuer_named as unattributed, and a label whose category and issuer contradict each other is flagged for review exactly as an API reply is, and counted in the ingest's new inconsistent count. Labels from older prompts load as before: the 3 committed session files (4,734 lines, all older_prompt) give 0 malformed lines. A session label names the model that made it, for example "claude-opus-5-5 (session, prompt 2026-10-17)", which is accepted, stored as written and counted as current. export-prompt takes --model to print that name, and the stability check now pairs only labels made by the classifier model (claude-sonnet-5, its session labels included), so a label from another model is never reported as part of a same model rerun. The methodology text and data/labels/README.md say so. 13 new tests in tests/test_labels_session_fields.py.

## Licensed archive check: ProQuest TDM Studio, September to October 2026 (2026-10-22)

The owner ran the tracker's state origin signatures and model triggers (pipeline/signatures.yaml) as a rules only screen over licensed ProQuest full text in TDM Studio, editorial pages only, for seven titles: The Globe and Mail, National Post, Toronto Sun, Toronto Star, Calgary Herald, The Business Times and El Cronista. Of 1,613 records pulled, 1,597 were left after removing duplicates: 380 concern China and 1,217 do not, and none carried a state origin signature (no Xinhua or CGTN credit, no release stamp, no sponsor notice, no diplomat byline). Of the 380, 59 cite Chinese official or state sources and await review inside the archive, because their text cannot reach the tracker's model; the other 321 are recorded as no state signature found, never as independent journalism, because no verification step ran. Valor Econômico returned no records because it was not searchable there. The aggregate counts are in sources/archive_checks.yaml, shaped by sources/archive_checks_schema.json and checked by pipeline/archive_checks.py, which refuses a file whose rows do not add up (for each title China relevant equals awaiting model plus no state signature, each column adds up to its total, China relevant plus not about China adds up to the 1,597, and the trigger counts, from state media reported in English 22 down to China Daily 1, are at most the 59 and together at least that many). The export writes them to their own side file, docs/data/archive_checks.json, which no count, map, total, share, top 10 or finding reads. The site shows a Licensed archive check block in the methodology beside the paywall disclosure, and one line in the panels of Canada (five titles, 286 China articles, 54 awaiting review), Singapore (one title, 82, 4) and Argentina (one title, 12, 1), each summed from the title rows. METHODOLOGY.md's Known limits and the README carry one paragraph each. Only counts are recorded, never an article's text, headline or address; the ruleset version used was not recorded with the run.

## Data schema 17 (2026-10-23)

The side file docs/data/archive_checks.json, the licensed archive check, is added; no count changes. Site assets are now version 89.

## Undetermined items excluded from every count, as in the original study (2026-10-22)

The original study codes an item whose authorship or commissioning cannot be settled, and could be state, as undetermined, and leaves it out of every category count; the tracker now does the same, and this replaces the morning's separate figure for state origin by the model's judgement alone. Code M becomes code U in the daily files, latest.json, the rollups, outlets.json, the article lists and global_series.json, meta.model_only_state_origin becomes meta.undetermined, findings.model_only becomes findings.undetermined, and the CSV column model_only_state_origin becomes undetermined. Two kinds of current state origin label are undetermined: the 1,123 model labels whose route is unattributed or empty, and the 130 model labels whose route is a sponsored disclosure, which the rules did not confirm because they look for a disclosure on the article itself and ignore sidebar and menu advertisements; the 20 sponsored disclosures the rules found stay state origin, and so do the model's 107 uncredited copies, 4 distribution stamps and 7 diplomat's bylines. Undetermined items are in no count, share or denominator: not in state origin, not in China coverage, whose total is now state origin, unchecked state sourcing and independent journalism only, not in the map under any basis, the themes, the doctrine rates, the spike, alteration and speed measures, outlets.json counts, the ranked list, the Top 10 or the findings sentences. They are shown only as their own figure, a row in the all monitored countries table and the country panel, one line of What we found, the CSV column and the route filter's undetermined layer, marked not counted, and they stay a row of the gate accounting, which still adds up. Nothing rejoins state origin: the blind hand check is kept, renamed undetermined_a, and now only reports what the coders found, how many they settled as state origin, how many they left undetermined and how many could be state. On the committed audit files state origin is 1,077 placements all time (959 by rules, 118 by the model) and 668 in the 30 complete days ending 21 October 2026, undetermined 1,253 and 793, and the share of China coverage is 2.25 percent all time (1,077 of 47,854) and 2.21 percent over the 30 days (668 of 30,241). Italy stays first in the Top 10 with 293, then Indonesia 76, Singapore 46, Australia 26, Canada 22, Malaysia 21, Guyana and South Africa tied at 10, Senegal 9 and Oman 6. The banner, the route cards, the computer FAQ, the side panel caption, the methodology and README now say so, and the notes for Chad, Malaysia, Singapore, Hong Kong, Peru, Brazil and Laos were rewritten: Peru's state origin count is now zero, Brazil's is five placements at Exame alone, and Laos's eleven placements at The Laotian Times, whose 106 sponsored calls by the model are undetermined. Stored labels are unchanged.

## An uncredited copy counts only when the item it repeats is settled state origin (2026-10-22)

The original study counts an item with no credit as state origin only when its text matches a known state original, and the uncredited copy route now follows that rule at both ends. A lookalike reference, for the rules stage, the sweep and the export alike, is a current state origin label a credit supports that is not undetermined (pipeline/model_only.py, reference_sql, which lookalike.credited_reference_sql now returns): a wire credit, a distribution stamp, a sponsored disclosure the rules found, a diplomat's byline, a model label naming one of the first, second or fourth, or a rules label from before routes were stored; a sponsored disclosure only the model named is no longer a reference. An existing model uncredited copy whose recorded reference (the match under "lookalike" in articles.llm_trigger, else the one kept in state_copies) no longer has such a label, because that item is undetermined, was relabelled or is itself an uncredited copy, is undetermined at export time and counted under U in the daily files, latest.json, outlets.json, the article lists, the measures, the Top 10 and the findings, and meta.undetermined.by_route lists it under uncredited_copy; stored labels are unchanged. Neither the committed audit files nor the 2026-10-12 database record these copies' references, so each was matched again by headline, feed summary or body fingerprint against the current labels in the audit files: of the 107 counted uncredited copies, 34 repeat a settled item and stay, 60 repeat an item that is undetermined (50 a call naming no marker, mostly PR Newswire and Canberra Times copies of company releases, and 10 a sponsored disclosure only the model named, 9 of them at The Laotian Times), and 13 could not be matched again and are counted as staying. On that basis state origin is 1,017 placements all time and 626 in the 30 complete days ending 21 October 2026, undetermined 1,313 and 835, and the share of China coverage 2.13 percent all time (1,017 of 47,794) and 2.07 percent over the 30 days (626 of 30,199). Singapore loses 33 placements, Indonesia 8, Australia 5 and Malaysia 4, and the Top 10 over the 30 days is Italy 293, Indonesia 70, Australia 24, Singapore 22, Canada 20, Malaysia 17, Guyana and South Africa tied at 10, Senegal 9 and Oman 6; in Singapore The Straits Times and CNA are now tied at 11 each over the 30 days. The pairing of the alteration measure keeps its credited routes unchanged, because its monitored original is only the earlier text a counted copy is compared with. The methodology, the site's known blind spots and README now say that an uncredited copy counts only when the item it repeats is settled state origin and that a copy of an undetermined item is undetermined itself.

## Wording: four ways the tracker differs from the original study (2026-10-22)

Known limits in METHODOLOGY.md, the methodology section of the page and a paragraph of the README now say plainly where the tracker differs from the original study of 596 articles across Italy, Canada, Australia and Singapore, so that no figure is set beside the study's without them. First, the unit: the study counted identical wire copy once per publication, while the tracker counts every placement in each monitored outlet, with only the exceptions of pipeline/placements.py (a story shown by one shared widget across a network's mastheads counts once per country, one page at two addresses and the parts of one numbered series count once, and language editions of one wire release are one underlying item), so its counts and shares cannot be set beside the study's; the text refers to the Australian AAP example already given beside it rather than repeat it. Second, the not relevant threshold (at least 3 distinct China terms in the body, or 5 occurrences, or a headline term with at least 2 occurrences, read from BODY_RELEVANCE_MIN_HITS and BODY_RELEVANCE_MIN_TOTAL in pipeline/classify_rules.py) is called the tracker's own rule, not the study's codebook, here and where the pipeline is described, which now takes the numbers from the constants. Third, unchecked state sourcing is looser than the study's five level verification scale: the model is asked whether a central factual claim was "passed on without independent confirmation", and context counts against that only when it concerns the claim itself, so an article that sets the claim in independent context can be counted where the study would code it independent journalism (27 of 58 such items in the study's Australian sample); the model's instructions share the study's rule on tone but not its rule that a second official or another outlet repeating a claim is not corroboration. The count probably runs higher than the study's, stays provisional, and every such label is judged again once the five level scale enters the model's instructions after 23 October 2026. Fourth, section headers are not evidence: the rules read a sponsored or partner disclosure only on the article itself, a section header counts only when it names a Chinese state entity, the model sees headline and body only, and an item's arrival through a press release, sponsored or partner section is recorded but never makes it state origin, so paid sections the study would catch can be missed or end up undetermined until the model is given the address and section, planned after the same date. The FAQ on the reliability of unchecked state sourcing gains one sentence to the same effect. tests/test_paper_disclosures.py pins all four disclosures and the live constants. Nothing was measured and no label, count, ruleset or prompt changed.

## Every text names the third kind of undetermined item (2026-10-22)

Once an uncredited copy of an undetermined item became undetermined itself, a few texts still listed only two kinds or made a claim the third kind undoes, and each is corrected. The Singapore note no longer says The Straits Times carried more in the 30 complete days ending 21 October 2026: with the 33 Singaporean copies of undetermined items left out (24 at The Straits Times, 9 at CNA, matched again on the committed audit files) the two are tied at 11 each, so the note now says without a count that its count is carried by two outlets, The Straits Times and CNA, and that many of its state origin labels, at both outlets, rest on the model's judgement alone or are uncredited copies of items that are undetermined themselves, and so are undetermined and excluded from every count; its language rule and no agreement sentences are kept. The notes for Malaysia, Indonesia, Laos, Peru, Brazil, Chad, Hong Kong, Italy, South Africa, Guyana, Mali, Iran, Guinea and Senegal were checked against the same reconstruction and still hold (Malaysia's state origin is now Utusan Malaysia's 27 stamped placements alone, Laos's 9 stamped placements at The Laotian Times, Indonesia's Antara and Antara English by wire credit). The Routes and arrival paragraph of the page, the route paragraph and the state origin split sentence of the methodology, the undetermined route card and README now say that an uncredited copy counts only when the item it repeats is settled state origin and that a copy of an undetermined item repeats no known state original, so it is undetermined too, and the Australian duplicate figures now say Australia's undetermined placements are model calls with no checkable marker and uncredited copies of such calls; without them the fall is still tenfold, 410 placements to 41. The numbers sheet lists meta.undetermined.by_route.uncredited_copy, when the export has it, as copies of undetermined items, says they are in no total, not in state origin and not in its uncredited copy route, and adds them to the plain undetermined sentence. No live text keeps the old provisional category or its counts of 1,077, 1,253 or 1,123.

## 2026-10-22

The Start here section follows one spacing scale (8, 12, 16, 24 and 32 pixels) and one content column. The intro, the category cards and the route cards now share the card's full inner width and the same left and right edges. On screens 960 pixels and wider the intro runs in two text columns of about 67 to 71 characters a line, with a 32 pixel gutter equal to the card grid's gap, where before it was one column of 85 to 94 characters that left the right third of the card empty. The six category cards sit in two columns instead of three; the five evidence routes sit three over two and the undetermined card spans the full row, where before six cards 186 pixels wide stood 1,413 pixels tall at a 2,000 pixel screen. Card padding is 24 pixels on every side (16 on a phone), the route numbers no longer touch the route names, the open and close marker has a fixed width so the title no longer shifts when the section toggles, and the title and subtitle share one baseline. At 1,280 pixels the section is 2,540 pixels tall instead of 3,590, and at 2,000 pixels 2,405 instead of 3,164. Nothing else on the page moves, the wording is unchanged, and a phone at 375 pixels still has no sideways scroll.

## 2026-10-22

Country notes checked again after the export of 2026-10-23T06:26:21Z, which applied 1,300 session labels to articles that were waiting for the model. Measured from the committed audit files with pipeline/model_only.py's own rules (copies with counted_as skipped), Chad now has 5 state origin placements with evidence from 3 September to 21 October 2026, all at Alwihda Info, all wire credit labels from the session naming a People's Daily credit line, dated 19 and 21 October, against 45 undetermined (44 at Alwihda Info). The Chad note said its count was zero and that none named a credit line, so it is rewritten count free: Chad's state origin is small and rests on Alwihda Info's People's Daily items, and most of its state origin calls are undetermined. The Laos note stays as it was: its 13 counted placements are all at The Laotian Times, 11 of them through distribution stamps, and 106 of its 107 undetermined calls are sponsored calls at the same outlet; the one new undetermined call is a session label with no marker, which the note's wording leaves true, so only its test changed. The notes for Malaysia, Singapore, Indonesia, Italy, Peru, Brazil, Hong Kong, South Africa, Guyana, Mali, Iran, Guinea, Senegal and the worldwide stratum still hold and are unchanged. The Chad and Laos checks in tests/test_country_notes_2026_10_22.py now read the shape of each claim rather than a count, and a new file, tests/test_notes_after_session_labels.py, checks the carrier and route claims of the undated notes for Indonesia, Italy, South Africa, Guyana and Mali; every check that reads the audit files skips on the GitHub runner.

Site assets are now version 90.

## Tools: one numbers sheet from one export (2026-10-22)

The counts move with every export, so a talk should quote every figure from one export only. scripts/numbers_sheet.py, read only and standard library only, reads one export's site files (docs/data by default: meta.json, latest.json, global_series.json and the daily files), refuses with exit code 2 when their schema_version or generated_at disagree, and writes one Markdown sheet stamped with that export's generated_at, schema, ruleset, gate and prompt versions: the collection scale, the gate accounting rows and whether they add up to the gate total, state origin with its five routes and its rules and model parts, the category resting on the model's judgement alone, unchecked state sourcing with its publication status and kappa threshold, independent journalism, not about China, China coverage and the state origin share of it, all time and over the 30 complete days the map opens on, the worldwide distribution stratum apart from the countries, the top 10 countries with underlying items, shares and ties, the hand check of every provisional figure, the model stage, and one plain sentence per headline figure, saying "most" only above 50 percent. Each figure names its numerator, denominator, window, counting unit and the key it comes from, and a figure the files do not carry, such as each route split by rules and model, is written as not in this export. It reads the model only category under its new name, undetermined (code U, meta.undetermined, findings.undetermined), and under its old one (code M, meta.model_only_state_origin, findings.model_only), labels it as what it is in that export, and never adds it into any total or denominator: on the committed export of 22 October 2026 (schema 15), whose own china_total of 49,107 and share of 2.46 percent include the 1,123 model only placements, the sheet gives China coverage as 47,984 and state origin as 1,207 of them, 2.52 percent, and in the 30 complete days to 21 October 733 of 30,306, 2.42 percent, with Italy first at 293 placements (280 underlying items, 40.0 percent of the world's 733). Run python3 scripts/numbers_sheet.py --out FILE after the export the talk will quote. tests/test_numbers_sheet.py checks both shapes, the totals, the complete day window, ties, the "most" rule and that the sheet carries no dashes.

## Data schema 16 (2026-10-22)

The category M becomes U, undetermined, excluded from every count and denominator, with meta.undetermined and findings.undetermined in place of meta.model_only_state_origin and findings.model_only. Site assets are now version 88.

## State origin by the model's judgement alone held apart until a hand check passes (2026-10-22)

A state origin label the model gave with no credit line, distribution stamp, sponsored disclosure or diplomat's byline named as its evidence, and with no near copy of a credited state item behind it (route unattributed), is no longer counted in state origin. Of the 2,289 state origin placements in the export of 22 October 2026, 1,123 were such labels; the label accuracy audit had found about 390 private company, university and sports club releases among the weak model calls, and the model is kept blind to outlet, country and address, so these calls carry no marker a reader can check. They are now their own figure, state origin by the model's judgement alone (provisional), code M in the daily files, latest.json, outlets.json and the article lists, shown as their own row in the all monitored countries table and the country panel and as one line in What we found, and kept out of every state origin count, share, the map's colours under every basis, the ranked list, the Top 10 countries, the findings sentences, the worked examples, the spike alerts, the alteration, speed and doctrine measures and both CSV exports, which carry them in a column of their own. They stay in the China coverage denominator, so the China related total does not move. The route filter's model judgement option draws them as a layer of their own, marked provisional. On the committed data state origin falls from 2,289 to 1,166 placements all time and from 1,420 to 692 in the 30 complete days ending 21 October, and its share of China coverage from 4.8 to 2.4 percent; Italy stays first with 276 placements, and Malaysia falls from second to seventh. Stored labels are unchanged and the audit files keep every label with its route. A blind hand check decides when they rejoin: python -m pipeline.model_only sample draws a seeded random sample of them, stratified by language and mixed with control rows the model judged independent journalism or not about China, into data/reviews/model_only_a_sample_<day>.csv with its key beside it, and the export scores the newest committed sample. They rejoin state origin when coders confirm at least 90 percent of at least 100 coded labels, their figure is withheld below 80 percent, and they stay provisional otherwise; meta.model_only_state_origin gives the count, the status and the check, and METHODOLOGY.md says why they are held apart.

## Accounting: every article that passed the gate is accounted for, and the rules stage gets its slice again (2026-10-22)

An outside review added up the site's figures and found about 12,000 articles that passed the relevance gate unexplained: the summary showed classified, paywalled and waiting articles but not the others. At the export of 2026-10-22 00:02 UTC the audit files hold 116,266 gate passed rows: 90,806 classified, 12,057 paywalled, 7,918 whose fetch failed after retries or whose page held no readable article, 1,534 blocked by robots.txt, 3,377 fetched but not yet through the rules stage and 574 waiting for the model, which add up to 116,266. meta.json now carries gate_accounting, the same parts counted from the articles table on the basis of articles_gate_relevant, with the fetched rows split into those waiting for the rules stage and those fetched on the owner's machine whose text never reached the runner (355 expected, 49 of them from pk_app in September), and the count of stored rows the site counts as one placement with another (4,245). The summary table and every country's breakdown gain the rows for failed fetches, robots.txt blocks and fetched articles waiting for the rules stage, and a total, and say that the rows add up: 2,289 state origin, 1,921 unchecked state sourcing, 476 flagged, 43,808 independent, 38,824 not about China, 12,057 paywalled, 7,918 failed, 1,534 blocked and 3,194 waiting make the 112,021 articles that passed the gate through 2026-10-21, each placement counted once. METHODOLOGY.md states the same accounting. The 3,377 waiting articles were a backlog, not a passing state: none existed at any export up to 2026-10-19 21:32, and 3,328 of them were discovered on 2026-10-20 and 21. Since the pipeline change of 2026-10-20, discovery took up to 28 of the run's 35 minutes and the reference fetch ran to the deadline, so the rules stage was skipped whenever the two used the budget, and once went 13 hours without running. Discovery and the fetch now stop four minutes before the deadline in a full run, so the rules stage always runs, and a stage that overruns no longer cancels the stages after it. Every share on the site now states what it divides: 4.8 percent is 2,289 state origin articles of 48,018 China related articles since 2026-09-03 through 2026-10-21, Italy's 21 percent is 304 of the 1,420 state origin placements in the 30 days ending 2026-10-21, the paywalled share is 12,057 of 112,021 fetch attempts, and the README's first run figure for Canada is 6 of 27 gated articles. The 4.4 percent the review saw is the world share stated in the entry of 2026-10-19, which no current page repeats.

## Collection: the relay on the owner's machine is retired, and its outlets are a disclosed gap (2026-10-22)

The relay fetched outlets that refuse the crawler from GitHub's runner addresses through the owner's home connection and pushed them to the public relay branch, which works around the sites' own access control and sits badly with a crawler whose identity never changes, so it is stopped and RELAY_BUNDLE_KEY is never to be added. The 123 active outlets marked collector: self_hosted are now inactive in sources/outlets.yaml with inactive_since 2026-10-22 and the reason "blocks the crawler from its server addresses; collection from another network stopped on 2026-10-22 (relay retired)"; their entries and collector field stay for the history. That leaves the Cook Islands and Kiribati with no active outlet and 87 more countries with fewer, among them Italy 2 of 23, Canada 2 of 25, the United States 3 of 44, Israel, Malaysia and Sri Lanka 4 each. The pipeline workflow no longer ingests or acknowledges relay bundles (the ingest, cache confirmation and acknowledgement steps are gone), no step is given RELAY_BUNDLE_KEY, scripts/db_snapshot.sh uploads only the scrubbed public copy and never the whole database or the bodies, encrypted or not, and scripts/db_restore.sh and scripts/db_pull.sh read only that copy and never ask for a key. The weekly feed validation no longer moves an outlet the runner cannot reach to the relay: it deactivates it with the runner's reason, like any other dead outlet, and it no longer reads relay feed health. On the runner, a gate relevant article of a relay outlet still stored as fetched with no body is settled as failed with fail_reason relay_dropped_text_not_delivered (classify_rules.settle_relay_dropped), so nothing retries it, and the gate accounting keeps it in fetched text never delivered, unread and uncounted; about 355 rows in the audit files are in that state. meta.relay_collector gains retired_on, dropped_outlets, dropped_by_country, countries_all_inactive and countries_partial, is never stale, and counts no shared outlet; no day after 2026-10-22 is marked incomplete, the earlier red days stay on the timeline, and each country that lost relay outlets carries a relay_stopped warning. The methodology's Known limits list every country the gap touches. History stays: of the 116,266 gated rows in data/export, 4,528 came from the dropped outlets, 25 of them state origin (20 of those resting on the model's judgement alone), 72 unchecked state sourcing and 1,834 independent journalism. scripts/relay.sh and pipeline/relay.py are kept, marked retired. No label changes.

## Reliability: the publication threshold for unchecked state sourcing rises to kappa 0.7 (2026-10-22)

The kappa a hand coding study must reach on unchecked state sourcing versus independent journalism before those counts are published rises from 0.6 to 0.7 (config.KAPPA_WARNING_THRESHOLD, which meta.kappa_warning_threshold, the provisional reason, the methodology and the per language rule all read). An outside review noted that 0.6 is only moderate agreement, and the coders in the original study reached 0.91; 0.7 is substantial agreement, and the methodology now says so. The same threshold governs the stability check, so a current same model rerun below 0.7, not 0.6, now withholds the provisional counts. The site texts in index.html, content.json and compute.js, the seven fallbacks in app.js and the coding guide say 0.7. Nothing published changes today: no hand coding study has reported and the only stability check (0.62, 9 October 2026) was made under an earlier prompt and is kept as history. The kappa thresholds of the alteration check (0.6, quadratic weighted) and the other measures are unchanged.

## Wording: the original study, independent journalism, the uncredited copy lower bound and two blind spots (2026-10-22)

The README now gives the original study as 596 articles across four countries, Italy, Canada, Australia and Singapore, not 484 articles across three, and says the registry was seeded densely in three of those four. Independent journalism is defined first by what it is not, not state origin and not unchecked state sourcing, and then by what it holds: the outlet's own reporting, copy from news agencies outside the Chinese state such as Reuters or AP, reporting of an official position, policy, warning, threat, opinion or denial, quotes that are confirmed, contextualised or contested, and press releases of issuers outside the Chinese state that substantively concern China; the change is in the meta categories block of the export, docs/content.json and the methodology section of the page, the interface name is unchanged and the model's prompt is untouched. Known limits in METHODOLOGY.md, the page and the README now say the uncredited copy count is a lower bound: the lookalike check compares an article only with state origin labels another monitored outlet ran under a wire credit, distribution stamp, sponsored disclosure or diplomat's byline (or a rules label from before routes were stored), within 11 days either side and in the same language, never with a label that rests on the model alone and never with the six English reference editions, which serve only the alteration and speed measures; state text that no monitored outlet ever ran with a credit, such as Xinhua copy in Italian, Spanish or Portuguese run uncredited everywhere, and the state services' editions in other languages, which are not read, cannot be matched. The Chinese language rule stays, and beside it the same three places now say it is a blind spot: Chinese-language outlets outside China are registered but not monitored, the original study found state-linked content through that layer, in the lifestyle section of a Chinese-language diaspora paper in Canada, and state origin carried in Chinese-language diaspora media is not counted. METHODOLOGY.md and the page's note on placements say that counts are sensitive to how duplicates are defined: counting each release one shared AAP widget showed across ten ACM mastheads once per country cut Australia's state origin from 1,350 placements to 135 over 2026-09-03 to 2026-10-15, tenfold, and since the tracker counts every placement across a country's monitored outlets over its window while the original study sampled newspaper pages, the two Australian figures are not comparable. The per country shares of repeated items in METHODOLOGY.md now name their basis, the items each country screened from 2026-10-01 to 2026-10-11. No 4.4 or 7.3 percent world share was left in current site or methodology text. Nothing was measured and no label, count, ruleset or prompt changed.

## Site: a preliminary figures banner under the title (2026-10-22)

A banner now sits under the title and above the map controls on every visit, with no way to close it. It says the counts are published as preliminary while the hand checks run, that state origin counts only placements with visible evidence (a wire credit or dateline, a press release distribution stamp, a sponsored or partner disclosure, a Chinese diplomat's byline, or a close match with credited state text), that state origin judged by the model alone and unchecked state sourcing are shown apart and marked provisional, and that no figure has yet been checked against human coding, with a link to the methodology. Its text lives in content.json under preliminary_banner and its default is written into index.html, so it shows without JavaScript. The page rebuilds it from the export: once meta.relay_human_study reports a passed study, or meta.model_only_state_origin carries a passed status, the banner names that check as passed and stops calling the figure provisional, and a failed study turns the provisional sentence into a withheld one. A bare count or a missing model_only_state_origin key is read as provisional. No label, count or denominator changes.

## Country notes and the panel caption follow the model only split (2026-10-22)

Six hand written country notes, one answer in the questions and the country panel caption still described state origin as it was counted before the labels resting on the model's judgement alone were held apart, and each is rewritten with its dates stated and no counts in it. Measured on the committed audit files with pipeline.model_only's own test: Chad has no state origin with evidence from 3 September to 21 October 2026 (all 45 of its labels rest on the model alone, 44 at Alwihda Info), so its note no longer says its count is carried by Alwihda and now says the count for those dates is zero; Malaysia's note no longer counts the unattributed route and says Utusan Malaysia carries its state origin mostly through press release stamps (26 of 30 all time, 16 of 20 in the 30 complete days ending 21 October), while several times as many labels, 164 against 30, rest on the model alone, most of them at Utusan; Singapore's note no longer says CNA carries its count but names The Straits Times and CNA, with The Straits Times ahead in those 30 days (25 to 20) and more labels resting on the model alone (69, 49 of them at CNA); Peru's note says its seven placements, all La República sponsored placements, ran on 15 September 2026, so the 30 days show none; Brazil's note says Exame's sponsored placements all ran in September; and Hong Kong's note and the answer to Is a computer deciding this? say a state origin call with no marker a reader can check is shown apart as provisional. The panel caption no longer says six rows: the panel now shows state origin with its underlying items, the model only row, unchecked state sourcing, flagged, independent, not about China, paywalled, failed fetches, robots.txt blocks and articles waiting for a fetch or the rules stage, and the caption names them without a count and puts the time chart above the breakdown, where the panel draws it. The page's Australian duplicate figures, 1,350 placements to 135, now say they include the placements resting on the model alone; without them the fall is also tenfold, 460 to 46 on the committed data. Italy, Indonesia, Laos, South Africa, Guyana, Mali, Iran, Guinea, Senegal and the worldwide note still hold and are unchanged. tests/test_country_notes_2026_10_22.py checks every corrected claim against the audit files for the stated days.

## Site: the republication speed panel no longer widens the page at phone width (2026-10-22)

At a phone width of 375 pixels the whole page scrolled sideways by 19 pixels (document width 394). The theme grid was not the cause: it already scrolls inside its own wrapper, with the fading edge and the "Scroll the grid sideways" note, and its 1002 pixel table stays inside a 309 pixel box. The cause was the line under each bar in "How soon state text is republished", such as "149 pairs, 109 originals", which could not wrap inside its 4.5rem column and reached 394 pixels. Below 700 pixels that line now wraps onto two lines, and the median above it stays on one. Measured in headless Chrome, the page width now equals the window at 320, 360, 375, 390, 414, 768 and 1280 pixels, in the theme grid's whole world and one country views, with no console errors. tests/test_themes_overflow.py checks the wrapping rule and the grid's own scroll box.

## Data schema 15 (2026-10-22)

The data files gain the model-only category M in every count, meta.model_only_state_origin and findings.model_only, meta.gate_accounting, and the retired relay fields in meta.relay_collector. Site assets are now version 87.

## Top 10 countries under What we found (2026-10-21)

What we found now ranks the ten countries where the most state origin placements appeared in the 30 complete days ending on the day the map's timeline ends, with the dates in the heading. The counts come from the same daily aggregate the map and the ranked list open on, so every number matches theirs, and they stay fixed whatever the map's controls show. Each row gives the rank, the country, its placements with the underlying items beside them, a thin bar against the top count, and its state origin share of its China coverage, which reads n/a below the usual minimum of five China articles. Equal counts share a rank and are marked tied, and a cut through a tie at the tenth row is stated. Worldwide distribution through the press release wires is not a country, so it is not ranked; one line gives its count over the same days. Unchecked state sourcing is not part of the ranking. A row opens that country's view, by click or keyboard.

## Site: all China coverage shades split the values into equal shares (2026-10-21)

Under all China coverage the map's seven shades were steps up to the 95th percentile of every date, the same rule as the other measures, but China coverage runs from a handful of articles to thousands per country, so in the 30 days ending 2026-10-20 73 of the 175 shaded countries fell in the darkest two shades. The shades of all China coverage, under every basis and window, now split the pooled values into seven about equal shares (compute.scaleCap returns the cut points, keyTiers takes them), pooled over the dates whose window is complete, since the short windows of the first weeks would otherwise fill the lower shades; in the same window the shades now hold 10, 21, 25, 28, 29, 29 and 28 countries. A shade still means the same number on every date until the next data update, and the key says how the shades are cut and gives the highest value on any date. State origin and unchecked state sourcing keep steps up to the cap. No count changes.

Site assets are now version 86.

## Model stage: a raised usage limit is noticed within six hours (2026-10-21)

The account limit recorded on 2026-10-19 named 2026-11-01 as the day access returns, and the model stage made no request before that date, so raising the limit on the account would have changed nothing until then. A limit recorded more than six hours ago (classify_llm.PROVIDER_LIMIT_RECHECK) now lets the next run that would call try again: a refused request costs nothing and records the limit afresh, so later runs wait another six hours, and a request that goes through clears it, which also removes the pause from the site. The reliability study follows the same rule. The export still reports the limit until it is cleared. No count or label changes.

## Pipeline: a timing test on a slow runner no longer holds back the export (2026-10-21)

The 20:11 UTC run of 2026-10-21 skipped its export because tests/test_measures.py::test_each_measure_gets_its_budget_and_all_of_them_the_total failed: it shared a 0.35 second total between a measure that slept 0.3 seconds and the next, and on a slow runner the whole total was spent before the second began. The test now uses a 3 second total, a 1.5 second first measure and a second that sleeps past its own 0.5 second budget, which leaves a second and a half for setup. In both workflows each failing test is now written as an annotation, which the public API shows without signing in, and in the pipeline up to three failing tests get one second try: a test that passes it is reported as flaky and the export goes ahead, while a test that fails twice still turns the run into a collect run. The relay ingest also treats the ProgrammingError that Python 3.11 raises for a value SQLite will not bind as a malformed record, as Python 3.9 did with InterfaceError (15ea178). No count or label changes.

## Model stage: the account's usage limit pauses the model stage until access returns (2026-10-19)

From 2026-10-19 07:50 UTC every run failed at the classification step: the Anthropic account had reached the usage limit set on it, the batch submission raised "You have reached your specified API usage limits. You will regain access on 2026-11-01 at 00:00 UTC.", and the error left the step, so export, the commit, the snapshot and the deploy were skipped and the site stopped updating. That error, and any other usage, spend, credit or billing limit (classify_llm.provider_limit_of), is now handled like a spent budget, in the synchronous and batch paths, in batch collection and in the reliability study: no further call that run, every article left waiting, the stage finished normally, and the time access returns kept in the database (the table llm_provider_limit), so later runs make no request at all before it. The export reports it as meta.llm_budget.provider_limit, with the date and the message, so the notes can say the model stage is paused by the account limit until then. Every other API error is handled as before. This completes the hotfix below.

## Hotfix: an API account usage limit pauses the model stage, not the run (2026-10-19)

Committed to main on 2026-10-19 (42b7601), ahead of the rest of this release, after the account usage limit above had stopped every run since 07:50 UTC that day. run.cmd_classify now catches that one error, rolls back, leaves every waiting article waiting and lets collection, the export and the deploy go on; every other error still stops the run. The hotfix keeps nothing, so each run asks the API again and is refused again. The full handling, which remembers the date access returns so later runs make no request before it and sets meta.llm_budget.provider_limit, ships with this release (the entry above). No count or label changes.

## Counting: one placement per shared widget release, per page and per numbered story; language editions as one item (2026-10-19)

Four kinds of stored row were counted more than once. The AAP release pages of the ten ACM mastheads are all filled from one AAP listing, so each of the 135 state origin releases they carried from 2026-09-03 to 2026-10-15 was counted ten times, and every one of Australia's 1,350 state origin placements was such a copy. Adnkronos publishes an Immediapress release at two addresses, with and without the distributor in the path, and seven state origin releases were stored at both. Italpress runs one Xinhua photo story as parts numbered (1) to (n), each at its own address, and 225 of its 532 state origin placements were such parts. And the language editions of one PR Newswire release (nine of a Shanghai Electric order, nine of a Blokees launch, four of a Farizon debut) counted as separate underlying items. A new module, pipeline/placements.py, now decides what is one placement: the rows of one country that share a shared widget's fetch key (fetch_articles.FETCH_KEY_RULES, the AAP release service the only one), the same canonical address, or the same numbered story at one outlet on one day count once, through one representative row (a reviewed row first, then a labelled one, then the first stored). A release shown by one shared widget across one network thus counts once per country, like a release on a distribution wire, and the mirroring is disclosed. Every copy stays stored, labelled and in the audit files, where a copy now names the row it counts through (counted_as). The rule applies everywhere a placement is counted: daily_counts and the daily files, latest.json, the findings, the top carrier (which credits a shared widget's placements to the widget, named "AAP press release pages of 10 ACM mastheads"), the routes total in meta, the article lists (one entry per placement, with copies and shared_widget on the entry that stands for copies), outlets.json (whose counts now add up to their country, with the copies listed per outlet under not_counted), the worked examples, and the inputs of the spikes, alteration and speed, doctrine and engagement measures. store.canonical_url now drops the segment between /immediapress/ and an Adnkronos item address, so the second address is never stored again; a one time step gives the stored rows the new hash (121 of 127 on the local database), and the six whose twin is stored too keep theirs and count once. Europa Press splits long releases into numbered parts the same way, and those count once too. Language editions are different: each is a page of its own and stays a placement, but the editions of one release on a distribution wire, discovered within 36 hours, in different languages, from the same issuer when both name one and sharing the names and figures a translation keeps, are now one underlying item (body_dups.item_groups joins them); a link that would put two titles in one language into one item is refused, which keeps two releases of one issuer about one event apart. meta.shared_widgets and meta.duplicate_placements disclose each rule with its stored rows, counted placements and state origin rows, per country. Measured on the audit files to 2026-10-15: Australia falls from 1,350 state origin placements to 135 (its state origin share of China coverage from 76.0 to 27.1 percent), Italy from 593 to 441 with its 406 underlying items unchanged, the worldwide stratum keeps its 295 placements while its underlying items fall from 282 to 245, and the world total falls from 3,278 to 1,910 placements (from 7.3 to 4.4 percent of China coverage). Over the last 30 complete days Italy, not Australia, now carries the most state origin. Australia's discovered totals still count each masthead's copy of every release the gate rejected, which cannot be recovered after the prune; no published share uses them, since the ACM mastheads are not among Australia's top outlets. No ruleset, gate or label change.

## Privacy, copyright and crawler politeness (2026-10-19)

The relay branch is public, so the relay now seals its bundle with a key it reads from the environment (RELAY_BUNDLE_KEY on the runner, a key file named by RELAY_BUNDLE_KEY_FILE on the owner's machine), using encrypt then MAC from the standard library (pipeline/bundle_crypto.py), and refuses outright to push a bundle that carries a body in clear; until the key exists every body, paywalled ones included, is left out, the bundle says so, and the runner fetches again what it can. The public database snapshot is now a scrubbed copy (pipeline/snapshot_scrub.py): no bylines, feed summaries, raw model output, coder names, doctrine excerpts or email addresses, vacuumed, while the Actions cache keeps the full database. Evidence quotes on the site and in the audit files are cut to about forty words on word boundaries, the matched phrase kept whole. A byline match on a common persona name (Jenny Williams, Alice Johnson, Michael Harrison, Sophia Gonzalez, Mia Clark) is held for confirmation and published only with a second signal, an outlet or a date window from the list or a front of the same operation. Redirects are followed by hand, each target checked against its own robots.txt and paced, and a Retry-After on a 429 or 503 is honoured. The site carries a strict Content Security Policy, requirements are pinned to the versions in use, and the saved news pages in the extraction and bad body fixtures now hold synthetic text in the same markup, with no reporter's name or email.

## Ruleset 2026.10.2 (2026-10-17)

The label accuracy audit of 16 October found rules labels resting on page chrome and on single ambiguous words, and old misfires still published because reclassification never reached them. A paid placement disclosure paired with a Chinese state entity now counts only where a disclosure attached to the item sits (classify_rules.DISCLOSURE_CHECK, on the eleven sponsored_*_with_state_entity and state_entity_then_sponsored_en patterns): in a page label of the item's own (its JSON-LD section, genre, keywords or sponsor, or a meta value) anywhere; at the opening of the headline or of a short line in the first 400 or last 600 characters of the body; and in the author field or a line of page chrome only with the state entity named in that same field or line. Ad slots and menu items such as "(Anzeige) Solaranlagen", "Kostenlose Onlinespiele (Partnerangebot)", "Contenuto sponsorizzato", "materiał partnera" and a sidebar of "Sponsored Content" links had labelled news state origin at confidence 1.0 whenever a state outlet or a Chinese province was named within 600 characters: four Focus reports (1485003, 1553961, 2744135, 2858494), two Bankier items (2061638, 3248167), ANSA 2173513 and SunStar 2498530 and 2516894. Bare "Anzeige" is no longer a German disclosure and "co-sponsored by" no longer reads as sponsored (Tehran Times 1766752, a film festival "co-sponsored by the Shaanxi Provincial People's Government"); a Publi-Reportage section value with the Chinese Embassy named in the opening (Mosaique Guinee 2710645) stays state origin, and a desk byline such as "Advertorial Desk" still sends an item to the model as before. The native script wire end credit (state_outlet_wire_end_native) must now close the body and must not follow a line holding only a list mark, because the AI glossary Maeil Business prints under its stories had labelled report 1630643 state origin on its term line 신화통신. In the residual rule BRI counts only within 200 characters of another China term or Belt and Road, and the names of Hong Kong, Macao and Taiwan count only when the text also names the mainland; 148 rules independent journalism labels on the 12 October snapshot rested on BRI alone (132 Indonesian items about Bank Rakyat Indonesia or the BRI Super League, the rest Thai text and the French police unit "la BRI"), and others on a territory's name alone (Hong Kong football and cricket, a LEGOLAND Hong Kong event, a Finnish bank tender in Taiwan). Replayed read only on the 6,220 current labels whose bodies the snapshot holds, 103 residual independent journalism labels become not relevant and one model candidate (a CityUHK release in a press release section, whose only China term was Hong Kong) is not relevant by rules; no state origin decision changes there, and the cited state origin items, rebuilt from their stored text in tests/fixtures/rules/cases.yaml, no longer reach state origin. The lookalike route (pipeline/lookalike.py and sweep_lookalikes) now takes as references only state origin labels with a credited route (copies.CREDITED_ROUTES) or a rules label from before routes were stored, so a model only label (a vivo or iQIYI release, a CityUHK notice, a Chelsea and GAC item) no longer spreads to uncredited copies of the same text. A model label stored without a route is unattributed, or uncredited_copy when the lookalike check sent it, and never takes the route of the candidate signatures that sent it (that is how a GAC factory release reached the diplomatic byline route). Reclassification takes current rules state origin labels first, then the other rules labels, then model labels, an article whose fetch attempts are spent last in its group; a model label the current rules read and keep now carries the current version and leaves the queue. Before, about 4,000 kept model labels with low ids filled the 2,000 rows of every run, so 788 rules state origin labels under older rulesets were never reached, among them the Computer Modelling Group releases 981125, 981192 and 981252 labelled under 2026.09.7 by the bare CMG ticker.

The residual rule counts China terms with the gate's own matcher (gate.count), so two changes described under Gate 2026.10.2 below reach rules labels as well: in Devanagari, Bengali and the other Indic scripts a term no longer starts in the middle of a word, so प्राचीन (ancient) and कोचीन (Cochin) no longer count as China in a Hindi body, nor চীন inside প্রাচীন in a Bengali one, and the not_china_phrases (bone china and fine china in lower case, the "China Rose" sneaker colourway) are removed from the text before it is counted. The model prompt changes the same day, and the model labels made under the older prompt are judged again through the model's relabel queue (Model prompt 2026-10-17 below).

## Gate 2026.10.2 (2026-10-17)

Like every gate change, this applies only to items discovered after it takes effect. BRI no longer admits an item alone: it counts only beside another China term or Belt and Road within 200 characters (the corroborated block in keywords.yaml), because it is Bank Rakyat Indonesia, the BRI Super League, the French police unit "la BRI" and Latin letters in Thai text as often as it is the Belt and Road Initiative. Names in which China is no country are removed before matching for every outlet (not_china_phrases, case sensitive): bone china and fine china in lower case, and the Air Jordan colourway "China Rose" in quotation marks or beside a sneaker word, after a Yahoo Shopping release date story was labelled independent journalism on it; "exports to China rose 15.5%" and "regulators fine China Evergrande" keep their China. The names of Hong Kong, Macao and Taiwan still admit an item at the gate; only the residual rule reads them differently. Replayed read only on the 288,001 items of the 12 October snapshot that did not arrive through a section feed, the change turns away 247 items admitted on BRI alone (219 Indonesian, 9 from Côte d'Ivoire, 9 French, 6 Algerian, 2 Haitian, one each from Benin and Nigeria; 140 carry a rules independent journalism label, 100 a not relevant label, none a state origin or unchecked state sourcing label, and none concerns the Belt and Road) and the "China Rose" story, and changes no other decision.

The keyword gate's word boundary did not know that in Devanagari, Bengali and the other Indic scripts a vowel sign or a virama is a combining mark rather than a word character, so a term could start in the middle of a word: चीन (China) matched inside प्राचीन (ancient) and कोचीन (Cochin), চীন inside প্রাচীন, and the Persian چین inside بلاک‌چین (blockchain). Measured on the 2026-10-12 snapshot: of the 719 Hindi and Bengali items the gate had admitted (571 and 148), 54 were admitted only through the word for ancient (40 Hindi, 14 Bengali, and 2 Nepali besides), and of those 54, 27 were never labelled, 18 were labelled not relevant and 9 were labelled independent journalism by a reading of the body, which the gate does not see (4 Hindi, 5 Bengali; Nepali 2 not relevant); with Cochin and the three Persian compounds, 60 previously admitted items would not be admitted now. A term now never starts right after a letter, a digit or a combining mark, nor after a zero width joiner inside a word (pipeline/termlists.py); its end still admits a vowel sign, because these languages inflect by suffix (চীনে, in China; চীনের, of China), and Thai, Lao, Khmer and Burmese keep their substring matching. The theme counter and the doctrine term matcher read word starts the same way, and in their Hindi substring lists a term no longer starts right after a vowel sign (three Hindi theme tags in the snapshot change, no doctrine tag). Two term rules are new: an asterisk is the rest of the word, and a zero width joiner or non-joiner in a term matches either or none. Amharic, which writes its prepositions onto the noun, gains the prefixed forms (የቻይና, ለቻይና, ከቻይና, በቻይና, በቤጂንግ, በታይዋን and their endings), so "የቻይናው ፕሬዚዳንት ሺ ጂንፒንግ" is now read. Marathi, Telugu, Kannada, Malayalam, Gujarati, Punjabi in Gurmukhi, Odia and Assamese, which had no list and were matched on the Latin terms alone, have lists of their own: China, Chinese, Beijing, Xi Jinping, Taiwan, Hong Kong and Xinhua as each language writes them, with their case and postposition forms, checked on 2026-10-19 against headlines and articles of major outlets in each language (among them Eenadu, Sakshi, Prajavani, Malayala Manorama, Gujarat Samachar, Jagbani, Dharitri, ETV Bharat and Asomiya Pratidin), with the spellings not seen there marked in the list.

## Model prompt 2026-10-17

The label accuracy audit found the model calling about 390 placements of private press releases state origin (vivo, XPENG, Huawei, MINISO, CATL, Tencent, Baidu, Fosun, Pudu, Sands China, a Hong Kong university, CPA Australia, Chelsea FC and others), sometimes while writing that the issuer was not a state entity but matched the sponsored placement pattern, and labelling Xinhua copy on other countries one way one day and the other way the next. PROMPT_VERSION in pipeline/classify_llm.py is now 2026-10-17, and the codebook in the prompt now says who a Chinese state entity is and that a press release is state origin only when one issued or commissioned it: being published unaltered, a sponsored label or praise of China is not enough, and a release from a private company, a Hong Kong or Macao private body, a university, a charity, a trade show organiser, an auction house, a casino, a sports club or a foreign organisation is independent journalism when it substantively concerns China and not relevant otherwise. Commercial releases of state-owned enterprises, and releases of the Hong Kong and Macao governments and statutory bodies, stay state origin; a Hong Kong, Macao or Taiwan domestic story concerns China only when it concerns the PRC state or its policy; and state text is state origin whatever its topic. Five worked examples were added (a private company release, a state-owned enterprise release, a Hong Kong statutory body, a Hong Kong university, Xinhua copy on the Philippines). The model now also returns issuer_type, one of state media, government, party, diplomatic, state-owned enterprise, SAR government, private release or none (classify_llm.ISSUER_TYPES), stored on each label in a new classifications column and exported on the article records, in the audit files and as counts per issuer type in meta, so the site can show state-owned enterprise and SAR government releases as separate lines and count the private releases; and state_basis, the evidence it found, from which the route of a model state origin label is now taken (classify_llm.BASIS_ROUTES), never from the weak signature that sent the article, which had put a GAC factory release under the diplomatic byline route. A label copied to a near duplicate keeps its route and issuer type.

Every current model label is judged again under the new prompt, within the existing monthly budget of 110 dollars and through the normal relabel queue, one tier after another and oldest label first within each (classify_llm.RELABEL_PRIORITY): model state origin labels resting on no credit, stamp or byline (routes unattributed, sponsored disclosure and uncredited copy, or no route yet) first; then unchecked state sourcing, which puts the labels made before the factual-claim rule of 2026-10-12 ahead of those made under it; then labels made on a stub since replaced by the article; then independent journalism labels where the model recorded official Chinese sourcing and no independent confirmation; then the other state origin labels; then the rest. Each keeps its old label until the new one is stored.

A reply whose category and issuer type contradict each other is now stored once, never retried, with the reason in a new needs_review column (state_origin_without_state_issuer or state_issuer_without_state_origin), shown on the article records and in the audit files and counted in meta (inconsistent_labels). Such a reply never adds state origin: state origin with a private release or no outside issuer is stored as independent journalism, because the audit found the issuer the more reliable half of such a reply, and unchecked state sourcing, independent journalism or not relevant with a state issuer keeps the category the model gave.

## Data schema 14 (2026-10-17)

The issuer rule, the counting of placements, the coverage warnings, the measures and the model stage's account limit add export fields and database tables (config.SCHEMA_VERSION 14). The schema itself changes no label; the counts that change, change through the placement rule described under Counting above.

- Labels: meta.issuer_types, the eight issuer types (state_media, government, party, diplomatic, state_owned_enterprise, sar_government, private_release, none); meta.issuer_type_totals, the current labels per issuer type and category ({"by_type", "unrecorded"}, unrecorded counting the model labels made before the field existed); and meta.inconsistent_labels ({"total", "by_reason"}). Every article entry carries issuer_type and needs_review, and an audit line carries each when it is set.
- Placements: meta.shared_widgets (per widget its name, network, url_prefix, outlets, outlets_showing and countries, with stored_rows, counted_placements, rows_not_counted, state_origin_rows and state_origin_counted) and meta.duplicate_placements (same_page, numbered_series and language_editions, the first two with the same counts per country, the last with groups, rows, state_origin_groups, state_origin_rows and window_hours). An article entry that stands for copies carries copies, and shared_widget when a widget delivered them; an audit line of a copy carries counted_as. In outlets.json each outlet carries not_counted (the copies counted through another outlet's row, null when there are none), counts.disc is now the items discovered, from the same counters as the daily files, and counts.stored the rows stored; the site reads neither.
- Top carrier and route: in latest.json each country's top_carrier keeps id, name and count and gains total, share, tied, tied_ids, tied_names, network and, for a widget, shared_widget; top_route gains total, share, tied, tied_ids and unattributed, the placements with no route signature, which stay in its total. findings.top_country gains underlying, top_outlet carries the same details, and top_route_detail stands beside top_route, which stays the route id.
- Coverage: each country in latest.json carries mnew and miss_share in its blocks, feeds_frozen, outlets_working and no_working_outlet; language_support is capped at partial where outlets are excluded by project rule; the warning types unreadable, language_excluded, few_items and no_working_outlet are new, and feed_saturation now also fires on the missed share. meta.feed_saturation gains estimated_new_items, missed_share and missed_warning_share (0.10). latest.json gains window_end_30d and window_end_30d_partial.
- Stamps: every object data file carries the export's schema_version and generated_at, the values in meta.json, and meta.stale_after_hours (24, config.DATA_STALE_HOURS) tells the site when to call the data stale.
- Model stage: meta.llm_budget.provider_limit, {"until", "message", "recorded_at"} while the account's usage limit pauses the model stage, else null.
- Hand coding study: meta.relay_human_study gains compared_against, prompt_version, current_prompt_version, labels_compared, excluded_older_prompt, excluded_no_model_label, stale, failed_reason and superseded_samples, and the state sample_superseded.
- Measures: alteration.json counts distinct originals and items beside its pairs (originals, items, item_bands, item_shares, median_novel_items, median_kept_items, scored_reference and scored_monitored, and sister_only for copies whose only earlier copy is at a sister outlet), and alteration.json and speed.json carry reference_sources, each source's pages fetched, expired, failed and paywalled, its share_date_only and share_date_only_after_day_one and its flags; speed.json counts originals and originals_timed beside its pairs, and each of its reference sources carries a time_check, the times of the items two of its listings both carry, compared. spikes.json gives each flagged day uniq (its distinct underlying items), tested, expected, dispersion and outlets, and its method outlet_cap, dispersion_days, model and check_relabelling_only; the hand check sample carries underlying_items and tested. Each block of meta.measures carries budget_seconds. The registry gains an optional network field per outlet.
- Database: new columns classifications.issuer_type and classifications.needs_review, and feed_health.newest_entry (mirrored in reference_feed_health), each added by a one-time migration step; new tables llm_provider_limit (one row: until, message, recorded_at), doctrine_snippets (up to five matches per article and family with about 30 words either side, dropped from the public snapshot) and reference_time_checks (each reference item's time in two listings of one source, compared). Placements and language edition links are not stored: pipeline/placements.py works them out from the stored rows at each export, so no table holds them, and a one-time step gives the Adnkronos Immediapress rows the hash of their new canonical address. The scrubbed public snapshot carries a snapshot_info table. The relay bundle is sealed, and its feed health records carry newest_entry.

## The hand coding study checks the labels the site counts (2026-10-17)

The fixed sample drawn on 12 October 2026 (1,400 articles) took every one of its labels under a classifier prompt older than the current one, 786 by the API and 614 in a session, none carrying a prompt tag, and the study compared the coders with the label frozen at sample time, so a study that passed would have certified labels the runner is replacing. pipeline/agreement.py compute now compares each coded article with its model label current when compute runs, and only when that label was made under the current prompt; an article whose current label is on an older prompt, or is no longer the model's, is left out and counted, and the study records which labels it compared, under which prompt, and how many it left out, published in meta.relay_human_study (compared_against, prompt_version, current_prompt_version, labels_compared, excluded_older_prompt, excluded_no_model_label). The frozen labels stay in the key for the audit, and compute --against frozen prints them and records nothing. A study counts for its own prompt only: once the prompt changes, one that passed becomes stale and the counts go back to provisional, and one that failed keeps withholding until a new study reports. sample --since-prompt draws only labels made under the current prompt (or a named one), refuses a database whose newest model label is more than two days old, since it must run on the runner's snapshot, and never overwrites an existing sample. The 12 October sample is marked superseded in its key and in data/reviews/CODING_GUIDE.md, nobody had begun coding it, compute will not record a study on it, and the site now says it was set aside and that a new sample is drawn once the runner has relabelled under prompt 2026-10-17; no new sample is drawn yet. A study in which the model placed at least 10 articles in unchecked state sourcing and the coders fewer than 10 has failed, not come out inconclusive: overall that withholds the counts, and in a language it withholds that language whatever its size (relay_withheld_languages). Fewer than 10 from the model still leaves the study inconclusive. The coder column takes pseudonymous ids such as c1 instead of names or initials, and compute records nothing while it holds anything else. No count or label changes.

## Doctrine terms 2026.10.2, unusual days version 2 and the measures' time limits (2026-10-17)

The new measures audit found that 45 percent of the matches of multipolar world and people-to-people exchanges had no China word within 140 characters: the phrases are also the stated policy of India, Russia, Vietnam and the BRICS. Both families are now muted, left out of the share of articles using the vocabulary, and counted only when China, Chinese, Beijing, Xi Jinping or their rendering in the article's language appears within 140 characters (a china_context list in pipeline/doctrine.yaml, matched by pipeline/termlists.py). The generic Spanish, French and Russian forms of people-to-people exchanges and the Spanish and Portuguese forms of power politics are dropped, and an acronym in brackets beside its full term ("Belt and Road Initiative (BRI)") is one mention and counts once, where the Uses column had counted it twice. Every article is tagged again at the next export. Each tag now stores up to five of each family's matches with about 30 words either side in a new table, doctrine_snippets, so a hand check sample drawn on a machine without the bodies no longer leans to headlines, and the sample is spread over the families first and their languages within each, so every family can reach the 15 coded matches its status needs. Unusual days (version 2) now test the day's distinct underlying items with each outlet adding at most 3, since 13 of the 14 days flagged in the first month were one outlet's, against a negative binomial tail on the country's own spread over its last 90 days, since the daily counts varied a median 2.1 times as much as a Poisson model allows; the estimate of days flagged by chance is now given as a rough guide, not a promise. On the snapshot of 12 October the change takes the flagged days from 11 to 1. A ruleset change makes a day one to check only when it took effect inside the 28 days the day is compared with, so it stops affecting new days 28 days later however long relabelling takes, and the page says plainly when days are to check because relabelling is unfinished. Choosing an unusual day now asks the map for state origin, that one day and both distributions. In the gate every measure has its own time budget, all of them together 900 seconds, a measure still running two minutes past its deadline is stopped and recorded as failed, and once the total is used up the rest are not run and say why; a sources/measures.yaml that cannot be read keeps the previous export's measures, marked with the error, instead of dropping them all in silence. A hand check draw that is empty is refused, one on a day that already has a sample gets a numbered name instead of overwriting it, and the command line uses each measure's own sample size, 20 pairs per band for the alteration score where it had drawn 15. No label, count or denominator changes.

## Copies, speed and reference originals: sister outlets, distinct originals, honest wording (2026-10-17)

The alteration score had no copy scored against a state original: every scored pair set a copy beside an earlier monitored copy, most of them at a sister paper of the same group, and the site still said the copies ran "the state text verbatim". Outlets now carry a network field (ACM, News Corp, Nine, Seven West, Postmedia, Torstar, Québecor, Bell Media, Stuff, SPH Media, Media Prima and others named in the registry notes and ownership sources, 122 outlets in 40 groups), and an earlier copy at a sister outlet is never taken as the original; such copies are counted as sister only. Both measures now count distinct originals and underlying items beside their pairs: the lead shares weigh each underlying item once, the two kinds of original are reported apart, figures against an earlier monitored copy are worded that way until reference pairs are scored, and a median or an outlet ranking needs distinct originals, not pairs, so one press release run by eleven mastheads no longer makes a median. The reference fetch now reads first the state outlets' pages whose headlines match current copies, even once expired, then the newest, in a slice of its own that the article fetch cannot use up; each source's counts of pages fetched, expired, failed and paywalled and its date only shares go to the side files and meta, and a source still giving only a date after an item's first day is flagged. CGTN's RSS and sitemap times are compared for every item both list, so a switch of the sitemap to true UTC would show as times reading early; date only pairs are counted in the zone of the date; the diagnostic thresholds no longer flag on one stray item or sit just above the observed values. The credit and dateline cleaners no longer drop sentences that merely name AP, AGI or CNA or strip ordinary bracketed words, and the CGTN cleaner copes with a missing accept line. Measure versions move to 2, so every copy is paired again at the next export.

## Prose: the site and the documents say what the system does (2026-10-17)

The site, the FAQ, the README and METHODOLOGY.md no longer call state origin the one label decided without the model: METHODOLOGY.md now states, from the export, how many state origin labels fixed rules decided from a signature and how many are model judgements (62 percent at the last export), and the FAQ answer to "Is a computer deciding this?" says the method is the author's and the computer carries it out, with the model making the calls no visible marker settles. The static technical notes, which still quoted a 30 dollar budget and old ruleset, gate, relay and saturation figures, are removed, since the facts list gives each of them live. A sixth route card explains state origin the model judged with no signature, the largest route. Country notes and FAQ answers lose counts, carriers and routes that had gone stale (Taiwan, the United States, Italy, Iran, Guinea, Singapore, South Africa, Hong Kong, Senegal, Chad, Brazil), Italy's credit is given as "(XINHUA/ITALPRESS)", and the FAQ speaks in the first person like the owner's note. Over-claims are softened: state text "run without substantive change" rather than "unaltered", "every item the tracker saw", "about 1,700 monitored outlets" rather than the world's newspapers, a gate that keeps state copy announcing itself in a title rather than one that never discards it, and release wires not tied to any one country rather than reaching every country. The model draw is recorded as a diagnostic, not a reweighting basis; the relabel queue is described by its priority tiers, read from the code; the not about China definition follows the codebook; the kappa rule names the two raters; the note under the running total computes the repeats' share of today's total. METHODOLOGY.md gains a Known limits section, generated from the registry and the export: the Chinese language exclusion by country, session labels and their measured undercount of unchecked state sourcing, translated uncredited copy, outlet selection and the outlets refusing the crawler, the model's 12,000 character body limit, and who counts as the Chinese state; it also explains issuer types, the model's routes from the evidence it names, the hand coding study's states and failure rule, and the scrubbed public snapshot beside the encrypted backups; the README says the relay bundle is sealed with the owner's key, or carries no bodies at all without it. The README no longer names a university, and the CHANGELOG header describes what the file records.

## Findings: "most" only above half, and ties reported as ties (2026-10-17)

The What we found box said Australia's state origin came "most of them through The Courier (Ballarat)", which carried 94 of 940 placements, tied with nine sister mastheads and picked by its id; it said most of it "arrived by the wire credit route", which carried 200 of 940, because the placements with no route signature were dropped before the comparison; it named not assessed outlets as the group publishing at the highest per outlet rate; and it explained the largest ownership share with a cause the data does not show. The export now writes, beside every top carrier and top route in latest.json and in findings.top_country, the count, the window's total, the share and how many entries tie for the lead, keeps the unsigned placements in the route's total and reports them beside it, adds the top country's distinct underlying items, and never lets outlets with no sourced owner lead the ownership comparison. The site builds its sentences and tooltips from those figures: "most" and "mostly" appear only above 50 percent, a tie reads "spread evenly across 10 outlets" or "led jointly by", a route under half is "the most common signed route" with the unsigned count beside it, the top country's placements carry their underlying items, the ownership sentences compare only groups with a known owner and state each group's share of outlets instead of a cause, and a sentence whose figures are missing or counted over different placements is left out rather than filled. The leaning chart note no longer quotes per outlet figures that contradicted the table beside it.

## Site interface: timeline, loading, the per 1,000 share, small screens and accessibility (2026-10-17)

The map and theme timelines now end on the last complete day, and a line under the timeline names the partial export day left out; Play rewinds when it starts there. The page now loads only the daily months that the opening view reads, which is the 31 days ending on the last complete day. The other months load once, when an earlier day, Total, Play, a country's full time series or the daily CSV needs them. Until then the all time cards come from the export's own totals less the partial day, the timeline curves for unloaded days come from global_series.json, and the colour keys say which days their scale is pooled over. % of all its news now reads per 1,000 items in the ranked list and the panel. The heading and key name the counted outlets it divides by. A country whose counted outlets carried none of its articles shows n/a with the reason, not 0.0% (Australia, for example). The panel's outlet table is labelled all time. The tracker:select event and TrackerApp.open take a measure, a window and a distribution, so an unusual day opens as state origin, That day and Both. At 720 pixels or less the View toggle names the list, the ranked list has its own Scope and Country search, and it opens on its top 25 rows with a button for the rest. A selection below the fold scrolls the panel into view and gives it the focus. Every segmented control is a labelled group with aria-pressed, and the theme counter's themes, headings and rows work from the keyboard. Country search ranks exact codes and aliases first, so us opens the United States and uk the United Kingdom, with U.S., Holland and PRC added, and the focus stays in the box. The faint text colour now passes 4.5:1, and grid cells take dark ink from Lab lightness 55. The route select is disabled with a note under other measures. The findings box says it counts both distributions when the toggle shows one. A notice appears when the data is more than a day old or when the files come from different exports. A line under the cards gives state origin from state-owned enterprises and from SAR government bodies, private releases and labels not yet judged under the issuer rule, and article rows carry an issuer badge. The hand coding study states sample_superseded and stale have their own words, and a study that failed without a kappa is described by its failed_reason. The panel's language note says plainly when outlets in a language are registered but not monitored under a project rule, and keeps the keyword list note for genuinely partial coverage.

## Coverage warnings say what a country loses (2026-10-17)

The feed warning now rests on the estimated share of a country's feed items missed between polls (missed against missed plus found, over the polls the current estimator covers, shown per country as miss_share beside the new mnew count), and fires at 10 percent: no country reached the old trigger of 25 percent of polls saturated while Egypt, Albania, Kosovo, Iran and Georgia each lost about 40 to 50 percent of their items; a saturation share of 25 percent still warns on its own. Each feed's newest entry date is stored in feed_health.newest_entry (recorded by the feed poll and carried in the relay bundle), and an editorial feed whose newest item is more than 14 days old is frozen: it counts as failing in feeds_ok, in the failing feeds warning and in outlets.json, and each country reports feeds_frozen. A combined unreadable warning fires when paywalls, failed fetches and robots.txt blocks together reach 33 percent of attempts, which flags Ireland's 50 percent; a country with registered outlets excluded by project rule in a language its active outlets do not read (the Chinese language outlets of Taiwan, Hong Kong, Singapore, Malaysia, Australia and South Korea) shows partial language support and a language_excluded warning; a country with fewer than 50 China relevant items in the last 30 days is warned that it is too few to compare (few_items); and a monitored country none of whose active outlets delivered an item through a working feed in 30 days is flagged no_working_outlet, with outlets_working beside it. The wires' feed polls are counted under the worldwide distribution stratum instead of the United States. Western Sahara and Kiribati, which have active outlets, leave the gaps file, countries_in_gaps and meta.gaps follow the coverage latest.json shows, and Bouvet Island, whose one outlet is inactive, keeps its uninhabited reason as a gap. latest.json states where its 30 day window ends (window_end_30d, the partial export day, marked window_end_30d_partial). Every object data file now carries the export's schema_version and generated_at, the values in meta.json, so the site can tell a file left from another export, and meta.stale_after_hours (24) tells the site when to call the data stale. Nothing is removed from any count.

## The workflows, the database cache, the snapshot and the relay (2026-10-17)

The test suite now runs on every push and pull request (.github/workflows/tests.yml), and in the pipeline it runs on export runs before the database is restored and before collection, not after a 35 minute collection: a failing test turns that run into a collect run, so the hour's collection still happens and the site keeps its last good data, and fails the run at the end, which opens the failure issue. Twice a single failing test had held the site back for a day or more. Both workflows run on ubuntu-24.04 rather than ubuntu-latest, which moves to Ubuntu 26 on 2026-10-19. The Actions cache key carries the run attempt, and the cache keeps data/tracker.db-journal beside the database: with journal_mode=DELETE a writer killed mid transaction leaves a hot journal, and without it the cache had passed on a torn file. scripts/db_restore.sh opens the cached file with PRAGMA quick_check, which rolls such a journal back, and sets aside a copy that still fails for the snapshot. When a snapshot exists but cannot be read after three tries the step fails, where it used to fall back without a word to the seed of 2026-09-04; and a new step before the export (scripts/export_guard.py) refuses a database whose discoveries, counted with the junk feed ledger, fall more than half a percent below the articles_discovered the site shows at the branch tip. The public snapshot goes through pipeline/snapshot_scrub.py; beside it the whole database and the article bodies, which until now lived only in the Actions cache, are published encrypted with RELAY_BUNDLE_KEY and restored on a cache miss, and are never published in clear when the key is missing. The model key reaches only the two steps that call the model, the relay key only the steps that open a bundle or handle the snapshot, the stage step reads the site data at the branch tip so a queued run does not export again, a validate run collects for 20 minutes and caps the feed check at 15 so it fits the job's 55, and the relay is acknowledged only once the cache is seen to hold the saved database. On the owner's machine scripts/relay.sh runs under caffeinate -i, stops a run still going 45 minutes after it began by the clock on the wall, so a run woken from sleep no longer pushes data hours old, and uses HTTPS when origin cannot be reached over SSH, pushing the bundle again over HTTPS when a run fails over SSH. scripts/db_pull.sh, which refreshes the owner's local copy, now prefers the whole encrypted database, decrypted with the relay key from RELAY_BUNDLE_KEY or the relay's key file, and falls back to the scrubbed copy with a message saying why and what that copy lacks. No count, label or published figure changes.

## A damaged file, a malformed feed or bundle, a full disk and the clock no longer stop a run (2026-10-17)

Body files are written through a temporary file and renamed into place, so a killed run or a full disk leaves the old body or the whole new one, and store.load_body treats a file it cannot read (truncated, not gzip, not UTF-8) as a missing body, logging it once and counting it: one truncated body had crashed the reclassify loop on every run and with it every export, and had stopped the body link pass and the planted bylines scan for good. Discovery handles each feed on its own: a feed whose result or entries break the loop (a list for a summary, a number for a link, text with a lone surrogate) is rolled back alone, counted in feed_errors and recorded as a failed poll, and the next feed is read; feed text is cleaned of lone surrogates and control characters and a title is cut at 1,000 characters; a feed the parser itself raises on (a character reference to a lone surrogate) is a failed poll of that feed, parse_error, where it had lost the outlet's other feeds for the poll. The relay ingest takes each record under its own savepoint and skips one that cannot be taken in (bad JSON, an outlet day without its date, a value of the wrong type) with a skipped_malformed count, keeps what a truncated bundle delivered, leaves out a body that does not decode, and holds the acknowledgement after any of these so the next bundle sends the window again; a body delivered for a row that changed in nothing else but has no body file (one withheld before the bundle key existed) is now stored. An item discovered a second before midnight is counted on the day it is stamped with, where a lock wait had put its discovery in the next day and month. The export writes into a staging copy of docs/data and swaps it in at the end, so a full disk part way leaves the published files as they were, and it refuses to publish an articles_discovered more than half a percent below the one already published, less what the junk feed correction took off in the run (TRACKER_EXPORT_ALLOW_SHRINK=1 lets a deliberate correction through). The outlet registry is parsed with the C YAML loader and once per version of the file, which took 2.5 seconds a parse four times an export; the section and junk feed corrections and the alteration measure commit in steps, so the write lock is no longer held for up to 55 seconds; and the article fetch stops early enough for the reference fetch's 90 second slice to fit inside the run's budget. Sitemap requests follow redirects by hand, each target checked against its own robots.txt. In outlets.json, counts.disc is now the items discovered, from the same counters as the daily files, with the rows stored beside it as counts.stored; the site does not read either. The missed-items estimate is rounded once per feed poll row, so the outlet, day, country and overall totals add up to one another where rounding each sum had given three different totals. The test suite can run on a fake clock (TRACKER_TEST_NOW), and a test that read today's date when it was collected now reads it when it runs. No label changes; discovery counts change only for items stamped just before midnight.

## Registry and feed validation: runner failures go to the relay, one editorial feed per outlet (2026-10-17)

The weekly feed validation no longer deactivates an outlet because the GitHub Actions runner cannot reach it. Every feed failing from the runner for more than seven days now moves the outlet to the relay on the owner's network (collector self_hosted, with the new collector_since date and a dated note), and only the relay's own feed health, read from the relay database or from the feed health records of the relay bundle and used only while it is fresh, can deactivate it; robots.txt refusals are deactivated directly, since no network changes what a site asks. Outlets only the relay polls are now judged at all. A feed that never worked is judged by the days since the outlet and its feeds entered the registry, read from the registry's history, instead of a count of 168 checks that assumed hourly polling. Re-testing the 78 outlets deactivated this way found 46 refused by robots.txt and 9 reachable from the owner's network, which are back on the relay with Thairath, Ratopati and AGI among them; 4 of the 301 outlets deactivated for HTTP 403 on 2026-09-02 also answered (none of a sample of 40), and Daily Nation is back on a new feed. The registry now refuses one editorial feed or news sitemap on two active outlets, which credited every Infobae item to Argentina and split Daily NK and NK News between two countries; Peru's Infobae has its own feed, and the duplicate NZZ, Daily NK and NK News entries are inactive. Shared sponsored and release sections of one network stay allowed. Frozen feeds of NHK, UNIAN English, NBS, Republic and RTVC were deactivated, CityNews has a live feed, GlobeNewswire, Times of Israel, AlterPresse, Scoop, Togo Actualite, RRI, CubaSi, Nordlysid and The Standard were deactivated after both networks failed, four outlets refused only by the runner moved to the relay, and eight saturated outlets are now polled by both collectors.

## Thirty seven outlets for thinly measured countries and India's regional languages (2026-10-17)

The collection audit found populous countries with deep China ties that had almost nothing to measure (Sudan 2 China relevant items in 30 days, Ethiopia 12, Angola 13, Zambia 14, Somalia 16, DR Congo and Mozambique 20, Malawi and Tanzania 22, Niger 23, Kenya 43, Uganda 76) and India covered only in English and Hindi. The registry gains 37 active outlets on the hosted collector, each verified on 2026-10-17 from the owner's Mac with the project user agent through the tracker's own feed and page helpers: robots.txt allows the feed, the feed answers with at least ten items, the newest under seven days old, and three sample articles read through the extractor with running prose and no paywall (one sample each for the four BBC services, to keep to five requests on bbc.com). Ethiopia gains BBC News Amharic and Ethiopia Observer; Somalia BBC News Somali, Caasimada Online, the federal government's Radio Muqdisho and Horseed Media; Tanzania BBC News Swahili and Michuzi Blog; Sudan the Arabic Sudan Tribune, Darfur24 and Ayin Network; Zambia Zambia Monitor and The Zambian Observer; Niger Niger Inter and Journal du Niger; Kenya Kahawa Tungu, Tuko and Kenyans.co.ke; Uganda Watchdog Uganda and PML Daily; Mozambique the state controlled weekly Domingo, MZNews and Ikweli; Malawi Malawi Freedom Network, Maravi Express and Business Malawi; DR Congo Congo Profond and Habari RDC; Angola Notícias de Angola, Hold On Angola and Mercado; India BBC News Tamil and Dinamani in Tamil, Sangbad Pratidin and News18 Bangla in Bengali, News18 Urdu and Siasat Urdu in Urdu. The four BBC services are recorded as state funded international broadcasters like BBC Hindi. None carries an audience rank, so in countries whose share of all published items uses ranked outlets they are counted in the totals but not in that denominator. Counts for these countries rise from the day the outlets are first polled, and earlier days are not filled in, so a rise across 2026-10-17 is partly the wider registry.

## Theme tags read a body that arrives late (2026-10-14)

An article tagged with themes before its body was fetched kept the themes of its title and feed summary for good, because the export re-tagged only articles whose stored themes version differed. The stored tag now records whether the body was read ({"v", "t", "b"}, b 1 when it was), and each export tags again every article tagged without a body whose body file now exists, newest first, within three minutes, carrying the rest to the next export and listing how many are left as themes_body_retags_left. A tag made with the body is never replaced by one made without it under the same version. Tags stored before this change carry no b and count as made without a body, so each is read again once when its body is there and left alone when it is not. On the local database 6,371 articles have a body file and are read again; 698 of them change their themes, 606 of these because the body had never been read (142 move from Other to a theme). The themes version stays 2026.09.4: the terms and the stated reading, the whole body, are unchanged, and a version change would re-tag every article whether or not its body is present. Counts rise for days with late bodies as the re-reading reaches them. The daily files and article lists are otherwise unchanged; each audit line carries the tag as stored, now with b.

## Data schema 13 (2026-10-14)

The research measures and the reference sources add an export field, six side files, four database
tables, a column and a relay record type. Nothing in the schema changes a label, a count, a denominator
or the outlets monitored, and China stays unmonitored.

- New export field: meta.measures, one block per measure in sources/measures.yaml (pipeline/measures.py),
  with its status (off, provisional, checked or withheld), label, version, file, enabled, inputs_present,
  min_inputs, the hand check (state, file, n, coded, statistic, value, threshold and checked_on), reason,
  method_text and summary, and error when the measure failed.
- New side files in docs/data, one per measure that is not off: alteration.json, speed.json,
  doctrine.json, spikes.json, engagement.json and syndication.json. None carries article body text. A
  measure that is off has no side file, and one an earlier export left is removed; a measure that fails
  is recorded as off with its error and an empty side file, and never stops the export.
- New database tables: reference_items (the reference sources' items, with their body fingerprint),
  reference_feed_health (their feed health, kept apart from the monitored outlets'), state_copies (one
  row per state origin copy, with the original it was paired with and its alteration and speed metrics)
  and engagement_aggregates (the counts imported from hand lookups). New column: articles.doctrine, the
  doctrine term matches, NULL until an article is tagged. The tables are created when a database lacks
  them and the column is added when it is missing, so an older database opens as before, and the prune
  (prune_gated_out, expire_seen) never touches any of them.
- The relay bundle carries a new record type, reference_item, which the hosted side writes into
  reference_items and nowhere else. A hosted side older than this skips it as a record type it does
  not know.

## Reference sources: Chinese state outlets read as originals only (2026-10-14)

The collector now reads the English editions of Xinhua, CGTN, Global Times, People's Daily, China Daily
and ECNS as reference sources (sources/reference.yaml, pipeline/reference.py), so state copy in
monitored outlets can be compared with the text it came from. They are originals only. A reference
source has no country and an id of its own (ref_*). Its items go to reference_items and nowhere else:
they are never classified, never counted and never in a denominator. China stays unmonitored and
sources/gaps.yaml is unchanged, and every published figure is the same with or without them, which a
test checks file by file. Each hourly discover stage polls their listings within its own time cap:
CGTN's section RSS and latest news sitemap, ECNS's RSS, the Global Times news sitemap, Xinhua's timed
list pages, and the People's Daily, China Daily and Xinhua homepages. Every request goes through the
crawler's usual robots.txt check, per domain pacing and user agent. Each fetch stage then reads up to
200 of their newest pages (config.REFERENCE_FETCH_PER_RUN). Several listings misstate their times, so
each listing has a written time rule: CGTN's sitemap and ECNS's feed write Beijing time as if it were
UTC, the Global Times sitemap is trusted for the date only, and the time is taken from the article page
where a listing gives none. Bodies are stored without the outlets' menus, footers and related link
lists, along with a fingerprint. Paywalled pages are never fetched again, and the body of an item no
copy was paired with is deleted 30 days after it was fetched (config.REFERENCE_UNPAIRED_BODY_DAYS),
while its row and fingerprint are kept. Xinhua has no Italian edition, so Italpress's Xinhua copy has to
be paired with Xinhua English. CRI English and CGTN Italian could not be read. All six sources start on
the hosted runner, and a source refused there for three days can move to the relay, whose bundle now
carries reference items.

## New measures, provisional until hand checked (2026-10-14)

Six new measures share one foundation, and none of them changes a label, a count, a denominator or the
outlets monitored. sources/measures.yaml lists each measure with its module, whether it is switched on
and the inputs it needs. The alteration score, republication speed, doctrine terms and unusual days
are switched on; engagement and syndication deals are built and switched off. At export time
pipeline/measures.py runs each one in its own try/except, writes its file to docs/data/<id>.json and
records its state in meta.measures: off (the site hides it), provisional until its own hand check
passes, checked, or withheld when the latest completed check fails. Checks are scored from the newest
committed data/reviews/<id>_sample_*.csv and its key, so hand coding reaches the published site through
git. METHODOLOGY.md gains a "New measures (provisional)" section that lists only the measures that are
not off, and the site gains a section for state copy and its originals, one each for doctrine terms,
engagement and syndication deals, a slot for unusual days under What we found, and one full note per
provisional or withheld measure. A measure that is off changes no published file except meta.measures.

The alteration score tests the second half of the state origin definition: that the text runs without
substantive editorial alteration. pipeline/copies.py gives every current state origin label one row in
state_copies and pairs it with the original it reproduces. It looks first for a reference item whose
publisher the copy's credit names, in the copy's language, from eleven days before the copy to one day
after it, found by headline, feed summary or body fingerprint, and with at least 30 percent of the
copy's words lining up when both texts are stored. Failing that, it takes the earliest credited copy of
the same item at another monitored outlet, and otherwise it records why there is none. Translations,
such as Italpress running Xinhua English in Italian, are never matched across languages. The lookalike
match is copied into the row, so a reclassification cannot lose it, and copies from the last 14 days are
paired again at every export. Both texts are compared after credit lines, datelines and an outlet's
repeated template lines are removed. The score is the share of the copy's words not found in the
original, in provisional bands: verbatim under 3 percent, light under 15, moderate under 40,
substantial 40 or more. Cuts, headline changes and two weak keyword cues, local context and
counterpoint, are reported separately. alteration.json gives the band shares by route, country (wires
under WLD), outlet (at least 5 scored copies), reference source and language, with pair coverage and
reference recall. It carries no headline, link or body text. Every substantial copy is listed in
data/export/alteration_review.csv for review by hand, and data/reviews/ALTERATION_GUIDE.md sets out the
blind check: 20 pairs per band, passing at pair precision 0.9 and quadratic weighted kappa 0.6. Bodies
are stored only where the tracker runs, so the site's coverage line says how many copies could be
scored.

Republication speed reports how soon a state origin copy runs after the state original it reproduces,
from the pairs the alteration score stores (pipeline/speed.py). Each lag is recomputed from the two
publication times, and only when both are credible against their own discovery; the time the tracker
found an item is never a lag. Where the original is the state outlet's own page the lag is absolute,
and where only an earlier monitored copy was found it is relative, behind the first monitored copy; the
two are never pooled. Lags fall into same hour, same day, 1 to 3 days and later. Pages that give only a
date are bucketed by calendar date and kept out of the medians, and copies more than an hour ahead of
their original or without a credible time are counted but kept outside every distribution.
docs/data/speed.json gives counts, medians and quartiles by route, country (wires under WLD), reference
source and outlet (from 10 timed pairs), with coverage and, for every reference source, a check on its
times: the median gap to discovery, the share published after discovery, which points to a zone
labelled wrongly, and the share that give only a date. The site shows it as a second panel under State
copy and its originals, with a bar per route and the fastest outlets. The hand check is 60 reference
pairs whose times a person reads off both pages (data/reviews/SPEED_GUIDE.md), and it passes at 85
percent bucket agreement.

Doctrine terms counts how often China coverage uses the Chinese state's own set phrases: community
with a shared future, the global initiatives, the Belt and Road, win-win cooperation, the multipolar
world, hegemonism and power politics, Cold War mentality and zero-sum thinking, true multilateralism,
Chinese modernization, the one-China principle, the Global South, people-to-people exchanges and Xi
Jinping Thought. The terms (pipeline/doctrine.yaml, version 2026.10.1) are in English and in the
renderings Xinhua, People's Daily, CGTN and the Chinese foreign ministry and its embassies publish in 16
more languages, each with the page it was checked on; a language without a published rendering is
matched on English alone. A new generic matcher (pipeline/termlists.py) reads terms the way the theme
counter does and adds occurrence counts, exclusions (BRI Liga and Bank Rakyat Indonesia are not the Belt
and Road, the one China policy is not the one-China principle, a bare win-win and the generic hegemony
never count) and a rule that an acronym counts only beside a full term of its family. Every relevant
article is tagged in the headline, the feed summary and the body, into articles.doctrine, and tagged
again once its body arrives; a body-read tag is never replaced by a title-only one. doctrine.json gives,
per day of record and country, each family's China coverage, state origin, unchecked state sourcing,
independent journalism and occurrences, and the site's Doctrine terms section ranks the families by
their rate per 1,000 China articles of each kind, leads with the share of independent journalism using
the vocabulary, mutes the Belt and Road and the Global South as common beyond state usage, and draws one
weekly timeline. A hand check of up to 200 matches, spread over families and languages
(data/reviews/DOCTRINE_GUIDE.md), checks a family at a precision of 0.9 on 15 coded matches and
withholds it below 0.8.

Unusual days flags days on which a country, or the worldwide press release wires, carried far more
state origin placements than usual. pipeline/spikes.py reads the same day of record and country mapping
as the daily files and tests complete days only, never the partial export day. Each day is set against
the 28 complete days before it, counting only days on which the country's outlets were collected at all
and needing at least 14 of them; the expected count is their mean once the busiest tenth is set aside,
with a floor of 0.2. A day is flagged when it has at least 5 placements, at least three times the
expected count, and a Poisson tail under 1 in 1,000, computed on the log scale in plain Python.
spikes.json gives every flagged day with its rate per discovered item, its distinct underlying items,
the outlets and items behind it and up to 40 articles taken straight from the articles table, so past
the 80 per country cap of the article files, together with the number of tests run and the false flags
the model expects by chance (about 0.1 over 5,963 country days on the 13 October snapshot). Every
flagged day carries the checks that could make it an artefact rather than a surge of state text: a newly
added outlet, a backfill read, a collector catching up after a gap, an incomplete day for the collector
on the owner's machine, labels made under a different ruleset, a relevance gate change that moved the
level, saturated feeds, candidates still waiting for the model, and a surge in everything collected. A
day with any of them is shown muted under What we found as one to check, and the repeats in the old
discovery counts are noted as touching the rate only. The site lists the most recent clean days first,
opens a day's country on the map at that day when selected, and marks unusual days on the map
timeline. The hand check codes up to 30 flagged days as a real surge or an artefact against
data/reviews/SPIKES_GUIDE.md and passes at a precision of 0.8.

Engagement (pipeline/engagement.py) is built and stays switched off. It asks how far state origin
articles travel after publication, which is reach, not supply, from aggregate counts the owner looks up
by hand in the Meta Content Library: no code in the repository touches Meta, a login, a token or an
API. `python -m pipeline.engagement urls --since --until` writes data/engagement/urls_<range>.csv, the
current state origin articles of those days with public addresses only, their url_hash, outlet, country
as the export counts it (release wires under WLD), route, day and headline; these files are not
committed. The counts come back in a file whose header is a strict whitelist
(sources/engagement_schema.json: key_type, key, platform, period, posts_n, reactions, shares, comments,
optional views, suppressed, query_date and mcl_query_ref), and `validate` refuses the whole file, line
by line, for an extra column, any link to a platform or any address at all, an @ handle, a digit string
longer than 15, a time finer than a day, text over 64 characters, or a small cell: posts_n from 1 to 4
without the row suppressed (ENGAGEMENT_MIN_CELL, 5). `import` validates, then writes
engagement_aggregates idempotently with the file's sha256. Because the runner's database is the
canonical one, the coded file is committed as data/engagement/counts_<range>.csv with its line in
data/engagement/QUERY_LOG.md, and the export imports every committed counts file each time it runs.
Once switched on, engagement.json carries totals per platform, route, outlet and country, and the share
of the state origin addresses looked up that some public post linked to, never a single article's
counts, an address or a headline; the site shows that share, a table of the ten countries with the most
posts and the status chip. Its hand check repeats up to 20 lookups blind and compares the band of posts
found, not the exact count, since posts keep arriving after the first query; it passes at 80 percent
agreement.

Syndication deals is built and stays switched off. sources/syndication.yaml lists publicly announced
agreements between outlets and Chinese state media: the outlet (or an outlet the tracker does not
monitor, named), the partner, the kind of deal, the date signed with its precision, and a public source
page that states the deal and the date, marked stated when a party announced it and reported when a
third party did. `python -m pipeline.syndication validate` checks the file against
sources/syndication_schema.json and against the outlet registry, and refuses a precision that does not
match the date, a duplicate deal or an entry without a source. Most deals were signed before collection
began on 3 September 2026, so they appear on a timeline only. For a deal signed on a known day or month
at least 28 days inside the collected period, pipeline/syndication.py compares the outlet's state origin
share of its China coverage over the 28 days before and after it, with at least 50 China articles a side
and a Newcombe interval on the difference, and lists what else changed in the window: ruleset and gate
changes, days the owner's machine collected incompletely, days the outlet discovered nothing and
saturated feed polls. The comparison shows timing, not cause. The measure stays switched off in
sources/measures.yaml until at least 10 sourced, dated entries name monitored outlets, and its hand
check rereads the source page of 20 entries and passes when 0.9 of them confirm the deal and its date.

## Site: unusual days open any country, and a hidden country redraws every figure (2026-10-14)

Selecting an unusual day now opens it through a small public hook in the page script
(window.TrackerApp, with selectCountry and setDay, and a "tracker:select" event on the document) rather
than by driving the page's own controls. The timeline moves to that day, or, for a day it does not
offer, to the last day before it (the first day when there is none); the country's panel opens and the
map scrolls into view. Before, an unusual day for a row missing from the ranked list, such as the
worldwide wires under Country outlets, moved the day but never opened the row. The unusual days list now
sends the event alone, and falls back to the scrubber and the ranked list only on a page without the
hook.

Picking a country the Distribution toggle hides, such as the worldwide wires under Country outlets or
any country under Worldwide wires, switched the toggle back to Both but left the sparkline, its spike
and version marks, the KPI cards and the findings on the old mode's numbers, whether the pick came from
the search box, the map, a link or the ranked list. Every such pick now redraws them too, once each, so
the toggle and every figure agree on Both. A pick the toggle already shows draws exactly what it drew
before, and the public hook opens a country through the same path. No ruleset, gate, category or
themes change.

## Ruleset 2026.10.1 (2026-10-12)

An outside review of the methodology, and the audits it prompted, found state text the rules could not
see: a diplomat list six weeks out of date, diplomatic titles read wherever they stood on the page,
official sourcing in most languages that no trigger read, agency copy republished without its credit
line, and stored bodies that were not the article at all. Every change below, the diplomat list
included, carries this one version. Articles labelled under 2026.09.9 are reclassified forward at 2,000
per hourly run as usual (classify_rules.RECLASSIFY_PER_RUN). The model prompt changes the same day
(prompt 2026-10-12, the last two items), and the labels made under the older prompt are judged again.
Counts are on the local snapshot of 12 October; the bodies held there grew from 6,218 to 7,089 as the
day's fetches ran, so each count names the set it was measured on.

- The diplomat list (pipeline/diplomats.yaml) is part of the ruleset, because a name on it decides a
  state origin label. It still named Xiao Qian as ambassador to Australia six weeks after Liu Jinsong
  took up the post on 2026-08-24, and Liu signed an op-ed in the Australian Financial Review on
  2026-09-16. Liu Jinsong is added, with the consuls-general Wang Yu (Sydney), Fang Xinwen (Melbourne),
  Sun Zhaoyang (Brisbane), Fu Lihua (Perth, who signed an article in The West Australian on 2026-10-01)
  and Li Dong (Adelaide), and the two acting heads named on the Perth and Adelaide consulate sites in
  October. Xiao Qian stays listed as former ambassador: former post-holders are always kept, because a
  piece signed while in post is state origin whenever it is collected, and matching reads the name,
  never the post. The ambassador was then checked against the embassy website for each of the 23
  countries with the most active monitored outlets (the top 20, with four countries tied at 14 for the
  last place). Six posts had changed hands: Hou Yue (Italy, credentials presented 2026-09-29), Lu Shan
  (Poland), Jiang Xuebin (Turkey), Wan Li (Tunisia) and Chen Feng (Romania, with the charge d'affaires
  Zhou Xiang, who headed the embassy from late August) are added, and Dai Bing, listed as ambassador to
  Bulgaria, is ambassador to the Republic of Korea, where his predecessor Xing Haiming is added as
  former. Sun Linjiang, Liu Shaobin, Wu Ken, Lu Kang, Fan Xianrong and Zhang Run (Mexico) are marked
  former, and Deng Li is recorded as ambassador to France. None of the 315,773 stored author fields
  carries a name added in these two steps, and the new byline check below finds no hit in the 6,218
  bodies then held.
- Entries that came in with the same refresh, or had gone stale, were checked against mission websites
  and the foreign ministry. Zhang Ming (head of the mission to the EU and then SCO secretary-general,
  never ambassador to Russia), Chen Dong (Central African Republic, not France), Chen Xu (Geneva, not
  Israel), Wei Hongtian (Grenada and Somalia, not Nigeria) and Fei Shengchao (Belgium, not Italy) now
  carry the posts they held. Names listed twice each turned out to be one person and are now one entry
  carrying the current post and the former ones with a source: Wang Lutong, Hua Chunying, Jia Guide
  (permanent representative at Geneva, formerly ambassador to Italy and Peru), Cai Run (EU mission,
  formerly Israel and Portugal), Xiao Junzheng (Israel, formerly Greece), Zhang Yiming (formerly UAE and
  Namibia), and Zhang Run and Wang Wei, whose second entries (Ireland, Mozambique) named posts neither
  ever held. Han Chunlin is ambassador to Kazakhstan and former ambassador to Romania. He Xiangdong is
  former ambassador to Ireland and his successor since November 2024, Zhao Xiyuan, is added; Han Jing
  is ambassador to Zambia, as listed, and was ambassador to Suriname until 2024, where Lin Ji succeeded
  him and is added; Zhang Wei is ambassador to Benin, never to Bulgaria, where Dai Qingli is ambassador
  and is added; Guo Shaochun is consul-general in Los Angeles and was ambassador to Zimbabwe from 2019 to
  2022, never to Ireland; Wei Xiaodong is confirmed as consul-general in Istanbul and added; Fang Qiu,
  ambassador to Greece since November 2024, is added; and Zeng Jixin, ambassador to the United Arab
  Emirates since the spring (foreign ministry notice of 2026-04-21), is added. Removed, because no record
  shows them in any Chinese diplomatic post: Wang Xiaotao (first head of the foreign aid agency, never a
  diplomat), Ren Li and Wang Xiaodong (nor among the ambassadors to Hungary or Turkey), and Wang Liyong
  (listed as ambassador to Greece). None of the four removed names appears in a stored author field or
  in the 7,061 bodies then held.
- The list was then rebuilt against the foreign ministry's mission pages and each mission's own website
  on 2026-10-12, and every entry checked carries the URL that confirmed it. It names the head of mission
  in 178 of the 182 countries with an active monitored outlet and a Chinese mission (the other 53 of the
  235 are territories, or have no relations with China), with the consuls-general in the ten countries
  with the most active outlets. 146 names are added, among them six charges d'affaires who run embassies
  without an ambassador in post or who sign articles in the local press (Yemen, Mongolia, Finland,
  Sudan, Georgia and, before Zhou Qian arrived, the Czech Republic), and 35 entries are corrected: most
  are ambassadors since succeeded and now marked former, eight are marked former but unverified because
  no record of the post was found and the post now has another holder, four were listed in a post no
  record shows them holding (Wang Xuefeng is ambassador to Denmark, Yu Bo to Honduras, Zhang Jianwei to
  Thailand and Chen Chuandong to Lebanon), and two, Zhang Jianguo and Ji Guangbin, appear in no Chinese
  diplomatic post and are kept for the owner to check. Lithuania (a charge d'affaires office with no
  named head), Eritrea, Liechtenstein and Kiribati remain without a current head. Chen Li, consul-general
  in New York, is listed because a listed name no longer matches inside a hyphenated or longer name
  (below), so the Guardian's Taipei reporter Yu-chen Li, in three stored author fields, is not taken for
  him. Of the 203,394 stored author fields that are not empty, four carry a name the rebuild added:
  Zeng Jixin's op-ed in The National on 2026-09-15, labelled independent journalism under 2026.09.7,
  whose page label reads "Zeng Jixin is the Chinese ambassador to the UAE", so it is state origin by
  diplomat_list_author once its body is fetched again at reclassification (the body is no longer held;
  if it cannot be fetched the old label stands); a Le Renouveau piece by Zhu Kewei, ambassador to
  Burundi, already state origin by its author field title; and the two pieces by Zhang Yang and Kang Yan
  whose outcome the next item gives. 32 of the 7,061 bodies then held contain a newly listed name, none
  in a byline, and none changes label.
- diplomat_list_author, the strong signature for a listed name in the author field, now fires only when
  a diplomatic title describes that person and makes them China's diplomat. The title must stand in the
  author field itself, on the byline line, in a bio line that names the author or speaks of "the
  writer", on a title line of its own directly under the name, or next to the name in an opinion or
  commentary section. A title elsewhere in the head, tail or page labels no longer counts, so a namesake
  whose piece mentions an ambassador stays a weak candidate for the model. The title phrase must say
  Chinese, China's, of China or PRC, or the same in another language the test reads, or the line must
  name a Chinese embassy or consulate; China as the host country never counts, so "Li Dong, Ambassador
  of Singapore" and Singapore's ambassador to China stay weak candidates. A title in the author field
  that names no country, as in Sada Elbalad's "Liao Liqiang; Ambassador", counts only when the page
  labels or the opening of the body name China, and a title that names no sender and has China as its
  host ("Ambassador to China") never takes China from the page. A sentence with a speech or action verb
  (said, told, noted, according to and their equivalents) is reported speech, not a byline, so "Wang Yu,
  Chinese Ambassador to Australia, said on Monday" in a namesake reporter's story does not fire; a bio
  that begins with the name must begin its line and stand among the first short lines of the body or as
  its last line, followed at most by two short lines such as a disclaimer, so "Xiao Qian is the Chinese
  Ambassador to Australia." alone at the end still fires; in an opinion section "Chinese Ambassador Wang
  Yu" counts only on a byline or author line of its own; and the author box form "Wang Yu | Chinese
  Ambassador to Australia", which matched nothing before, now fires. A bio line with no verb counts as one
  that speaks of the writer, on a line of its own in the head, tail or page labels, when the writer word
  is followed by a dash, a colon or a comma and then only a title phrase ("Autor: ambasador ChRL w
  Polsce", "Penulis, Duta Besar China untuk Indonesia", "Tác giả: Đại sứ Trung Quốc tại Việt Nam", "The
  writer, Chinese Ambassador to Australia"), in Turkish with the copula as a suffix on the title ("Yazar,
  Çin'in Ankara Büyükelçisidir"), and in Hungarian when China stands as the possessor right after "A
  szerző" and the title carries its possessive ending (nagykövete, főkonzulja, ügyvivője), so "A szerző
  Kína budapesti nagykövete" fires and "A szerző Magyarország pekingi nagykövete" does not. A title on
  its own line directly under the name, as Expresso das Ilhas prints "Zhang Yang" over "Embaixador da
  República Popular da China em Cabo Verde" in its page labels, counts when that line is a title phrase
  and not a headline or a sentence ("Chinese ambassador Liu Jinsong visits Adelaide" under a namesake's
  byline does not). When the author field is empty, a listed name on a byline line at the top of the
  body with China's diplomatic title on the same line ("By Liu Jinsong, Chinese Ambassador to
  Australia"), which matched nothing because diplomat_byline_head needs the title before the country
  word, now fires the signature too; a listed name in a body byline with no such title fires nothing. A
  listed name no longer matches inside a hyphenated or longer name. Of the five state origin labels the
  diplomat list carried, four keep their label on the text still held, and the fifth, a La Jornada op-ed
  by Chen Daojiang whose body is no longer held, is decided again when it is reclassified, as is the
  Sada Elbalad article, whose stored page labels do not name China (its stored summary, which opens with
  Xi Jinping as "Chinese President", suggests the body will keep the label). The Zhang Yang op-ed in
  Expresso das Ilhas, labelled independent journalism by the residual rule, is state origin on its stored
  page labels once its body is fetched again (if it cannot be fetched the old label stands). The Kang Yan
  piece in Delo stays a candidate for the model, because its stored page labels give only the section
  (Mnenja) and its title "veleposlanica Kitajske" is in the summary, which matching does not read. No
  other of the 13 stored author fields that carry a listed name, no stored page label and none of the
  6,855 to 6,857 bodies held changes outcome, and none of the 1,970 stored Hungarian rows carries a
  listed name in its author field.
- The title test reads ambassador, consul general and charge d'affaires in Polish, Czech, Slovak,
  Slovenian, Croatian, Serbian, Hungarian, Romanian, Dutch, Swedish, Danish, Norwegian, Finnish, Turkish,
  Indonesian, Malay, Vietnamese and Swahili (veleposlanica, ambasador, velvyslanec, nagykövet,
  ambassadør, suurlähettiläs, büyükelçi, duta besar, đại sứ, balozi, never the embassy), with the China
  forms that make the person China's diplomat (Kitajske, Chin, ChRL, Číny, Kína, the Kínai
  Népköztársaság or KNK, Kinas, Kiinan, Çin'in, and the Kenyan Swahili Uchina), the opinion section names
  of those languages (Mnenja, Mišljenja, Opinie, Názory, Vélemény, Debatt, Mielipide, Görüş, Opini, Ý
  kiến, Maoni), and the host country wherever each language puts it, before the title as well as after
  it: "ambasador Polski w Chinach", "Türkiye'nin Çin Büyükelçisi", "Magyarország kínai nagykövete",
  "Suomen Kiinan-suurlähettiläs", "Danmarks Kina-ambassadør", "Balozi wa Tanzania katika China" and
  "Duta Besar RI di Tiongkok" do not make a listed name China's diplomat, while "Çin'in Ankara
  Büyükelçisi", "Çin Halk Cumhuriyeti'nin Ankara Büyükelçisi" and "Balozi wa Uchina nchini Kenya" do.
  Titles in the scripts of the languages with at least five active outlets (Arabic and Persian, Cyrillic
  for Russian, Ukrainian, Bulgarian, Serbian and Macedonian, Korean, Greek, Hebrew, Japanese, Thai, Hindi
  and Khmer) count as the Latin ones do, with the host country (after в, لدى, در, στην, ב, ประจำ or
  ប្រចាំ, before में or 주재, after 駐) never counting as China, and fourteen entries also carry the name
  as printed in those scripts (16 forms, from the embassy site's page in that language for Russia,
  Ukraine, Kazakhstan, Kyrgyzstan, Bulgaria, Korea, Japan, Egypt and Yemen, and from a news report in
  that language for Thailand, Israel, Greece, Syria and the former ambassador to Korea), so "Чжан
  Ханьхуэй, Посол Китая в России" or "لياو ليتشيانغ، سفير الصين لدى مصر" fires diplomat_list_author
  while "посол России в Китае" does not. One stored row changes: an Argumenty i Fakty op-ed signed
  "Чжан Ханьхуэй", labelled independent journalism by the residual rule, becomes a candidate for the
  model (its body is no longer held, so it is fetched again when reclassified).
- The byline signature diplomat_byline_head took any China word within 80 characters after the title, so
  "By Li Dong, Ambassador of Singapore to China" fired it with China only as the host country, and the
  author field signature diplomat_title_author_field did the same with "Shen Bo, Nederlands ambassadeur
  in China", "Deutschlands China-Botschafterin" and "Li Dong; Ambassador to China". Both now count a
  match only when its title phrase makes the person China's diplomat by the test above, or, in the author
  field, names no country on a line that names a Chinese embassy or consulate, so "Ambassador of China to
  Kenya", "Embajador de China en México", "Ambassadeur de Chine en France", "Ambasciatore della
  Repubblica Popolare Cinese in Italia", "Xiao Qian, Chinese Ambassador to Australia" and "Botschafter
  der Volksrepublik China in Deutschland" still fire. The only stored match of each keeps its label: a
  Tirana Times op-ed by the ambassador to Albania, and the Le Renouveau piece by Zhu Kewei.
- The China Radio International credit, and the same name in the state entity test, now read
  CRIOnline, CRIONLINE, CRIENGLISH and CRI English, the forms CRI uses for itself as a release issuer and
  on its former English site, and China Plus, the brand of its English service, in a credit line or
  dateline only, because "China Plus One" is a supply chain phrase. The acronym must be upper case,
  because the old case insensitive pattern also matched "Cri Cri online" in a Mexican listing and "CRI
  online" in an Italian Red Cross notice (both now regression cases, neither a stored row), and "CRI
  Online" or "CRI English" labels an article only in a credit line, a dateline, the author field, or in
  credit form in the first 400 or last 600 characters (before a colon, alone on a line, after a
  copyright mark), so a story that mentions an interview with CRI Online goes to the model. The CGTN
  credit line, dateline, closing line and author field read CGTN Radio, the name CRI's English site
  (english.cri.cn) now carries, and CGTN Digital, CGTN Africa, CGTN America, CGTN Europe and CGTN
  Français, where "Source: CGTN Radio" matched nothing before because the name had to stand alone. CRI's
  Chinese names 中国国际广播电台 and 国际在线 (the second only before punctuation or a source word such as
  消息 or 报道, because 国际在线教育 is international online education), its Japanese name 中国国際放送局 and
  its Korean name 중국국제방송 (only before a particle or a non Hangul character, so 중국국제방송장비전시회, a
  broadcasting equipment fair, does not count) count in the state entity test that two weak signals
  need and as a citation that sends an article to the model, as 中央广播电视总台 already did, and in a
  source line in Chinese script (来源, 來源, 稿源 and the other markers) when the name ends the line, so
  "来源：国际在线教育" does not. The credit lines of CGTN, Xinhua, China Daily, the Global Times, People's
  Daily, China News Service, CRI and China Media Group take a full width colon and a trailing date, as in
  "Source：CGTN 2026-09-06" on english.sdchina.com, while a line that goes on as a sentence after the name
  still does not count. Four stored rows change, all on title and summary alone: a CRI release carried by
  Antara that the rules had labelled independent journalism becomes state origin, two Utusan copies the
  model had already labelled state origin now reach that label by rule, and one PR Newswire release is
  still with the model. All four open "A news report from CRIOnline:", so the later narrowing changes
  none of them, and no stored row carries any of the CJK names.
- Seven West Media (7NEWS, PerthNow, The West) names an article's source (AAP, AP, Reuters, CNN, NBC, PA,
  Deutsche Presse Agentur, BANG, or a house desk such as 7NEWS Sport) only in the "source" field of the
  article's own object in the data the page carries, never in the extracted body, and bylines wire copy
  "Digital Staff" or "Staff Writers", so syndicated Xinhua copy there would have carried no credit for
  any signature to read. The fetch stage now reads that field (extract.embedded_source), only from the
  object whose id is the article's c-ID, so photo credits ("AP, Xinhua" on a related card's picture) and
  related cards' sources are never taken, and adds it to the page labels as "source: AAP"; when the
  byline is a desk the source is credited beside it ("Staff Writers; Reuters"), or stands alone when
  there is no byline, where the author scoped Xinhua signatures already read it. A new signature,
  state_outlet_source_label, reads that page label line and labels state origin an article credited to
  Xinhua, CGTN, China Daily, the Global Times, People's Daily, China News Service or China Radio
  International, as the whole source or one item of a source list, so a named writer with source Xinhua
  is labelled as a desk byline already was. All 817 Seven West pages in the Australian audit carry the
  field, 76 name an outside source, and 19 of those have a desk byline that gains the credit. None of the
  90,970 stored page labels carries a state source line, and the 32 Seven West pages among the 4,019
  saved pages credit only AAP, NBC, NewsWire and the publisher's own names, so no stored article changes
  label. Rows fetched earlier keep their stored labels and author until fetched again. The extraction
  version does not change, because no body changes.
- Official sourcing triggers, which send an article to the model, read far more. Statements by Chinese
  ministries and state bodies with no spokesperson named ("China's commerce ministry said in a
  statement") matched no trigger: new state body triggers in English, Italian, French, Spanish,
  Portuguese, German, Russian, Arabic, Korean, Japanese, Chinese, Indonesian and Vietnamese read the
  commerce, defence, foreign, public security and other ministries, the State Council, the NDRC, the
  statistics bureau, customs, police and provincial governments when China is attached to the name of
  the body and a reporting verb stands in the same sentence (so "Taiwan's defense ministry said it had
  tracked around 35 Chinese warplanes" does not fire), and a police statement released on Weibo, WeChat
  or Douyin counts too. On the stored titles, summaries and the 6,226 bodies held for classified articles
  this sends 339 more articles to the model, 124 of them on foreign ministry statements, and stops 3,
  about $3.36 at one cent a batched call. Generic attributions to the Chinese state with a reporting verb
  ("Chinese authorities said 16 people were killed", "China said Tuesday", "Beijing confirms", "the
  Chinese government said", "Chinese officials told", "according to Chinese authorities") and their forms
  in Italian, French, Spanish, Portuguese, German, Russian, Arabic, Korean, Japanese, Indonesian, Malay
  and Vietnamese now count, with China or Beijing only as the subject, never after a preposition ("held
  in China says", "the US ambassador to China said"), as part of a name ("MGM China reported", "China
  coach says") or before a verb that does not report ("Chinese authorities have been criticised"). On the
  same set this sends 271 more (159 in English, 31 Portuguese, 29 Spanish, 13 French, 13 Arabic, 9
  Indonesian, 6 Russian, 4 Korean, 3 Italian, 3 Vietnamese, 1 Japanese), about $2.71; 39 of a sample of
  40 read were a real attribution to the Chinese state, and the fortieth ("las autoridades de China
  declararon el estado de alerta") no longer fires. Five more triggers read Chinese state outlets
  (Синхуа, Сіньхуа, Sinchua, Hszinhua, the Global Times in Cyrillic, Persian and Korean, and CCTV beside
  a state broadcaster word), the foreign ministry spokesperson (with the spokespersons' names in
  Cyrillic, Greek, Arabic, Persian, Devanagari and Thai), Chinese embassies and consulates, Chinese state
  media, and statements by Chinese ministries, state bodies and police, in Croatian, Bosnian, Serbian,
  Macedonian, Bulgarian, Ukrainian, Greek, Polish, Czech, Slovak, Hungarian, Romanian, Turkish, Thai,
  Persian, Hindi, Nepali and Hebrew, and Korean 중국대사관 and 관영매체 written without a space, each only
  when China is part of the phrase, so "Амбасадор Кине" (the ambassador), an embassy in China, "Çin
  Dışişleri Bakanı" (the minister) and another country's state agency do not fire. This sends 290 more,
  265 of them labelled by the rules until now (203 discovered in the last 30 days), and stops none,
  about $2.65; 24 of a read-through of 30 cite a Chinese official source or state media. The state media
  trigger for languages without a trigger of their own read the bare phrases "państwowe media", "státní
  média", "állami média", "κρατικά μέσα", "devlet medyası", "관영 매체", "国営メディア" and "государственные
  СМИ" with no China in them, against the rule the English trigger follows. They now count only with
  China, Beijing or a Chinese state outlet in the same sentence (in Korean also 中, the headline
  abbreviation), so "íránská státní média", "az orosz állami média" and a Cambodian "관영 매체" no longer
  fire, and the forms with China attached that state_media_reported_more already reads (Romanian,
  Turkish, Thai, Hindi and Korean) are no longer listed twice. Of the 119 classified articles it fired on
  in their titles, summaries and the 6,218 bodies held, 113 still fire it, one is still sent to the
  model by another trigger, and five lose the route: the Cambodian story and four that meant China's
  state media with China named only in an earlier sentence (Korean, Czech, Polish and Turkish, one of
  them labelled unchecked state sourcing by the model). Picture credits in the forms "(KCNA/Handout via
  Xinhua)", "(Wang Gang/China News Service/VCG via Getty Images)" and "( Reuters: China Daily )" are
  removed before matching, while text credits such as "(AFP/Xinhua)" stay. No rules decision of state
  origin changes from any trigger.
- Uncredited copies of state origin items now go to the model. Agency copy republished without its
  credit line names no credit, state entity or official source, so the rules labelled it independent
  journalism or not relevant without a model call; the recall audit's describe found 153 such articles
  whose headline or feed summary nearly repeats a state origin item, about 49 of them Italpress items at
  Quotidiano.net, Libero and Il Tempo that Italpress itself ran stamped "(XINHUA/ITALPRESS)", and 35 press
  releases at CNA and The Straits Times whose copies at the Laotian Times, PR Newswire or CNA were
  labelled state origin. When an article would be labelled independent journalism or not relevant by
  rules, the rules stage (classify_rules.classify_article) compares it with the current state origin
  items of other outlets, using the recall audit's measures, moved to pipeline/lookalike.py and shared by
  both: headline word sets at a Jaccard similarity of 0.6 or more, the feed summary's five word shingles
  at a containment of 0.5 or more, read without the boilerplate the item's feed repeats
  (pipeline/boilerplate.py), and the body's MinHash fingerprint from pipeline/body_dups.py at that
  module's own threshold, never a body it marked as page template text. A match goes to the model as a
  state origin candidate, with the matched article's id, outlet, headline, similarity and method recorded
  in llm_trigger under "lookalike", and the model, and the second rater of the stability check, is shown
  that item's headline and outlet as context, never the article's own outlet or country. The window runs
  eleven days either side (lookalike.WINDOW_BEFORE_DAYS and WINDOW_AFTER_DAYS), not ten before and two
  after as first written: the recall audit found 17 lookalikes whose credited item came 2.9 to 20.5 days
  later, 13 of them judged by hand to be the same item (GlobeNewswire releases on the Postmedia sites that
  Antara reran eight days later, Xinhua copy that IOL, Sidwaya and La Republica ran days after the
  uncredited copy), and across all 132 cross-outlet pairs of state origin items with matching headlines
  the second copy came within 10.2 days of the first, while a twelve day pair of Djokovic China Open
  stories falls outside. The window is measured between publication times when both items have a
  credible one (not after discovery, not before 2026-09-03, and not more than 14 days before discovery
  unless a backfill read brought the item in), else between discoveries, so an item found late no longer
  moves its pair. When two credited copies are equally similar, the route and the recall audit name the
  earliest one, then the lower article id; before, the pick followed Python's per process string hash
  seed, and the same Straits Times release was matched to CNA in one run and PR Newswire in the next. The
  credited copy is often labelled after the uncredited one (67 of the first 131 matches), so each rules
  stage also sweeps the state origin labels made in the last 48 hours (LOOKALIKE_SWEEP_HOURS) against the
  rules labels discovered up to the window's width before them, however old those labels are. On the 12
  October database the route sends 145 articles from the backlog (131 at the first window, 118 by
  headline and 13 by summary, and 14 more at the wider one; bodies could not be compared there, as almost
  none are stored locally), about $1.45 at 1.0 cent a batched call, and about three to four a day after
  that, roughly a dollar a month. A state origin label the model gives such an article is stored with its
  own route, uncredited_copy (classify_rules.model_route), rather than unattributed, which is left for
  model labels no signature or match stands behind; ensure_routes moves older such labels at export, and
  the route is listed in meta.routes and route_totals, the route select, a fifth Start here route card
  ("Uncredited copy") and the methodology prose. The interface no longer says that every article waiting
  for the model carries official Chinese sourcing: the waiting line holds official sourcing, a weak
  signature, or a near copy of a credited state item.
- Bodies that are not the article are no longer classified. The body near-duplicate pass flagged 50
  template bodies in the local snapshot (one text stored under several headlines of one outlet), and a
  scan of the 6,855 stored bodies found the same failures without a template: a bot or script challenge
  served with status 200 (Le Monde's "Client Challenge" page, every one of its 103 stored bodies at 286
  characters; DIGITIMES "Access denied"), soft paywalls the detector missed (Asahi Shimbun teasers,
  DIGITIMES "requires paid subscription" teasers, the NHK ONE registration and terms notice in front of
  NHK News), pages the browser builds from data so the served document held only menus (the New Straits
  Times Press sites NST and Harian Metro), the wrong block of the page (Afghanistan International's
  related article card carrying another story; the Lowy Interpreter's standfirst in place of its prose
  blocks), a loading notice (the Lebanese National News Agency) and an injected spam block
  (BusinessWorld). Labels made on them rested on the headline. Extraction now prunes related article
  cards, reads the NST template's embedded article unless it is marked premium or the page carries any
  paywall marker, joins the Lowy prose blocks, and tries trafilatura's precision mode whenever a body has
  under 600 characters of running text (pipeline/extract.py, with golden fixtures). Paywall detection
  adds the Asahi, DIGITIMES and NHK markers and a stated truncation ("残り886文字"). A challenge page, or a
  body of 200 characters or more with under 150 characters of running text (lines of seven words or
  more, or 25 characters in a script without spaces; on the snapshot nothing real fell below 150), is
  recorded at fetch as failed with an "unreadable:" reason and is not retried. A consent or cookie wall
  served in place of the article is failed the same way at any length, as "unreadable: consent wall",
  when the page title is a consent dialog's or consent platform text (Seznam, Google's "Before you
  continue", Didomi, OneTrust, Quantcast, Sourcepoint, Funding Choices, Taboola) fills at least half the
  body, and the body carries under half of the headline's words; consent is never given. The classify
  stage applies the same tests to stored bodies, re-reads the saved page with the repaired extractor
  first, and also fails a template body whose text carries under half of the words of the headlines it
  was stored under (body_dups.template_unreadable; on the snapshot every extraction failure was under 32
  percent and every real follow up story 58 percent or more). Such an article loses its current labels,
  model labels included, and drops out of every count as unreadable; a re-fetch that serves the same
  unreadable text as the stored body withdraws the label too. In the local snapshot 39 current labels go
  (24 independent journalism, 14 not about China, 1 model independent journalism): 19 become paywalled
  and 20 failed. 22 bodies are read again from their saved pages; there 7 not about China labels become
  independent journalism and one independent journalism label goes to the model. The consent wall test
  fails only the 122 Novinky.cz and Seznam Zprávy articles, whose stored bodies were all Seznam's 1,091
  character wall, withdrawing 121 not about China labels and 1 independent journalism label. Video only
  pages (France 24 video, Il Tempo and Libero "pillole") still carry a headline and a few lines and are
  left for review.
- Two templates are read again under extraction version 2026.10.1 (config.EXTRACT_VERSION, now recorded
  on every stored body as articles.extract_version; extract.EXTRACTION_CHANGES lists the templates whose
  reading changed). The same Chinese state paid releases, distributed on PR Newswire between 24 and 30
  September, were state origin at Channel NewsAsia, Utusan and PR Newswire and independent journalism by
  rules at The Straits Times and Antara English, because the extracted body never held the credit. The
  Straits Times paid press release section builds each release in the browser from a React Router data
  payload, so all 410 stored bodies from the section ran 205 to 424 characters of disclaimer and
  headline; at Antara the opening paragraph, the one carrying the "(ANTARA/PRNewswire)" stamp, is bare
  text ahead of the first line break, and extraction dropped it on every Antara English article (20 of
  22 checked among its 144 stored bodies). pipeline/extract.py now reads the release from the Straits
  Times payload (only on that route, and never on a page carrying any paywall marker), with its headline
  above it as the other outlets print it, and keeps Antara's opening paragraph. A reclassification used
  to reuse the stored body; classify_rules.ensure_body now discards a body of a listed template stored
  under an older version and extracts it again from the page saved at fetch time when that is on disk,
  or else fetches it again by the ordinary fetch path (robots.txt honoured, three seconds per domain, the
  project user agent, paywalled pages left alone). An article that cannot be fetched again keeps its
  label and is not labelled on the stub. Reclassification takes these articles whatever the ruleset of
  their label, and a model label is replaced only when the rules now read state origin, as before. On
  pages fetched again for all 100 rows labelled independent journalism by rules at the two (32 Straits
  Times paid releases, 68 Antara English articles), two Xinhua Silk Road releases (Mazu culture, the
  Changsha tourism conference) carry the headline issuer pattern and are state origin by rules, as their
  Channel NewsAsia and PR Newswire copies are; 61 more show their wire stamp and go to the model like
  their copies elsewhere, among them the Hangzhou Investment Promotion Bureau release at both outlets and
  the Shaanxi International Communication Center film festival release, whose issuers no signature
  names, and 16 of the 61 have a copy the model judged state origin at another outlet; the other 37,
  Antara's own reporting, show no signature and stay where they were. A model label kept over a
  refreshed body that differs from the stub it was made on (unreadable, an older extraction, or at most
  half as long) is marked body_refreshed and queued for the model again, its old label current until the
  new one arrives: up to 52 labels (9 Straits Times, 43 Antara English), 6 of them certain from saved
  pages. Other Straits Times sections and Indonesian Antara read the same under both versions and are
  untouched. Copies of one item that differ only in what the outlet itself printed are not reconciled:
  each copy is judged on its own text, and 21 title-linked groups (44 rows) still hold a copy with a
  strong credit beside a copy with none, 18 of the groups Xinhua video items Italpress distributes that
  Il Tempo, Libero and Quotidiano.net run with an Italpress credit only.
- The model prompt now carries a date, PROMPT_VERSION 2026-10-12 in pipeline/classify_llm.py, and every
  model label records it in its model version, for example "claude-sonnet-5 (prompt 2026-10-12)"; a
  session label names it the same way or counts as older. Three changes share it: the reworded state
  origin definition (see Definitions below), the sentence saying that state text republished without its
  credit line is still state origin when the article is that text with no substantive alteration, and
  the factual-claim rule for unchecked state sourcing. The codebook counted "a ministry spokesperson is
  quoted and nothing checks the claim" as unverified relay, so every "Beijing said" story qualified,
  including one that only reports a stated position, and relay ran high, most of all in wire-heavy
  markets. An article is now unchecked state sourcing only when its central content is a factual claim
  attributed to a Chinese official or state source (an assertion about events, numbers or actions) passed
  on without independent confirmation; reporting a stated position, policy, warning, threat, opinion or
  denial is independent journalism even without outside comment. The prompt gives two contrasting
  examples (a commerce ministry export figure is relay, a foreign ministry warning to Manila is not), and
  its worked example of a reported espionage denial, which had taught relay, now teaches independent
  journalism. Checking a claim means checking that claim itself: when a central death toll, figure or
  event account rests only on a Chinese official or state source, unrelated context or background
  elsewhere in the article does not make it independent journalism, a case the old prompt let through
  twice in an audit of Australian wire copy. The new generic attribution trigger above sends exactly
  these articles to the model, which decides under this rule.
- Current model labels made under an older prompt (all 9,781 in the local snapshot, 8,921 calls after
  near-duplicate copying, about 89 dollars at 1.0 cent a batched call) are judged again through the API
  with what new articles leave of each run's calls, never more than TRACKER_LLM_RELABEL_SHARE (default
  0.5) of the day's binding cap, paced over the day. Each keeps its old label until the new one is
  stored, so no article is left unlabelled, and a relabel that fails keeps its label and is not retried.
  The queue works in category order rather than oldest label first (classify_llm.RELABEL_PRIORITY,
  relabel_candidates), oldest first within each tier: unchecked state sourcing labels first, because the
  factual-claim rule changes that figure most; then labels kept over a refreshed body (body_refreshed,
  above), whatever their prompt; then independent journalism labels where the model recorded official
  Chinese sourcing (a sourcing trigger in llm_trigger or a name in china_sources_cited) and no
  independent confirmation, the ones the old rule may have under-called; then state origin; then the
  rest. The 1,715 unchecked state sourcing labels (1,626 calls once near-duplicates are collapsed) take
  about 13 days at 130 relabel calls a day, the room 340 new articles leave under a 470 call cap, and
  about 7 days at the full half share of 235; oldest first, the last of them would have waited about 69
  days. Days whose labels still carry an older prompt get the same grey timeline mark as labels on an
  older ruleset. The unchecked state sourcing and independent journalism figures will move as the
  relabelling proceeds.

## Gate 2026.10.1 (2026-10-12)

The gate decides what enters the corpus, not how an article is labelled, and gate decisions are
recorded per item and never revisited, so this applies only to items discovered after today. Measured
first on the 2026-10-12 snapshot: of 68,676 gate relevant items classified in the last 30 days, 33,485
(48.8 percent) were labelled not relevant by the rules, but 20,213 of those came from gate exempt section
feeds that never met the keyword gate (among them the 7NEWS "sponsored" feed, which carries sport and
lifestyle: 716 items, 2 relevant; it is removed and its items corrected, see the collector entries
below). Of 45,382 items the keyword gate itself admitted, 13,272 (29.2 percent) were not relevant, spread
across ordinary news paths and single mentions of the country name in Korean, Spanish, Thai and
Vietnamese news, where over everything collected a single summary term also admitted 164 state origin
and 103 unchecked state sourcing labels, so it cannot be demoted everywhere.

- In a sports section, read from the item's URL path (sport, sports, deportes, esportes, tenis, sukan,
  olahraga and the other segments under sports_sections in keywords.yaml; the article's own slug is not
  read), one China term found only in the feed summary no longer admits the item: it needs a term in the
  headline, two distinct terms, or a state media name such as Xinhua or CGTN, and a state origin
  signature still admits it as before. A rejected item carries "sports_section:uncorroborated" among its
  gate terms. Replayed over every item collected so far (2026-09-03 to 2026-10-12), the change would have
  kept out 1,344 items, 1,125 of them not relevant and 219 independent journalism labels, all but one
  from the residual rule (tournament reports from Beijing and Shanghai, Asian Games results naming the
  Chinese team), and no state origin or unchecked state sourcing label. Lifestyle, travel and
  entertainment paths are left alone, because a Xinhua sourced bridge feature in a lifestyle magazine is
  unchecked state sourcing and would have been lost.
- The keyword terms are matched on the feed summary without the boilerplate its feed repeats in most of
  its items (pipeline/boilerplate.py, such as Ghana News Agency's promotional wrapper), while a state
  origin signature is still read in the whole summary. Replayed over the 29,773 items of the last 30 days
  on feeds with boilerplate, no gate decision changed (one item's list of matched terms did). The section
  feed correction (pipeline/section_retag.py), the language correction (pipeline/language_retag.py) and
  the fixture dry run (pipeline/dryrun.py) run the gate the way discovery does, without the feed's
  boilerplate and with the item's url, so they decide the same way on the same item.
- An item that carries a title and nothing else, as the entries of a Google News sitemap do, is also
  admitted by a separate title_only list in keywords.yaml (Chinese cities and districts, fairs such as
  the Canton Fair and CAEXPO, state initiatives, state media and provincial issuer brands such as
  iChongqing and SZMG, Chinese companies whose names alone were at least as often China coverage as not,
  and Spanish, Portuguese and Serbian forms), recorded as "title_only:<term>" among the gate terms. On
  stored titles it raised title-only recall of PR Newswire's state origin releases from 103 to 145 of 210
  (49 to 69 percent) and of its China items from 108 to 150 of 223 while admitting 3 more of its 229
  items labelled not relevant, and raised Telegraf from 35 to 38 of 39, Rio Times from 23 to 25 of 49 and
  Clarín from 82 to 83 of 94 with no item labelled not relevant. On the saved sitemaps it adds about 8 to
  25 fetches a day, all at PR Newswire. The generated methodology has a paragraph on the list: what it
  holds, that it applies to news sitemap items only because they carry no summary, how companies were
  chosen, and its measured effect.
- The main Serbian list now also carries the other cases of Kina, kineski, Peking and Tajvan in Latin and
  Cyrillic (Kinu, Kinom, kineske, kineskog, kineskom, Кине, кинеског and the like) and Xi Jinping and Mao
  as Serbian writes them (Si Đinping, Mao Cedunga, Си Ђинпинг), with Kinu, Kinom and their Cyrillic forms
  counted only when capitalised because "u kinu" is "at the cinema", while Pekinga, Šangaju and
  Kineskinja stay out because on stored items they were mostly the Beijing and Shanghai tennis
  tournaments and athletes. Replayed over the stored Serbian items the additions would have admitted 8
  more, 1 labelled China related, 1 not relevant and 6 never labelled.
- The Croatian and Bosnian lists now carry the same case forms, with Xi Jinping and Mao as each writes
  them (Si Đinping, Mao Ce-tung, Mao Zedonga) and, for Bosnian, the Cyrillic forms, under the same cinema
  guard and with the same tennis and athlete forms left out. Replayed over the stored Croatian and
  Bosnian items they would have admitted 10 more, 1 labelled not relevant and 9 never labelled.

## Data schema 12 (2026-10-12)

Underlying items now count each item once per window and scope, and the day's other changes add export
fields and database tables. No label changes come from the schema itself.

- Underlying items, the number of distinct pieces behind a count of placements, were distinct within one
  day and one country only and then summed: over the days of a window in the page, and across countries
  for the world totals in latest.json. A story placed on three days of a 30 day window counted as three
  underlying items, and one carried in two countries as two in a world figure. Each day of the daily
  files now carries underlying_items, the item ids behind its state origin and unchecked state sourcing
  counts per country (about 34 KB on September's 2.2 MB file), and the page counts the distinct ids over
  whatever window, day and Distribution setting it shows; latest.json's uniqA and uniqAB do the same for
  its all time and 30 day blocks and, in its totals, across countries. Title groups are joined through
  body links (pipeline/body_dups.py) for these figures, which can only fall. Items are still counted per
  label, and a day's own figure in the daily CSV is unchanged. Data from an older export carries no ids,
  and the page then says its underlying items are summed day by day. On the local snapshot of 12
  October, the 30 days ending 11 October: Italy 297 to 292, the world 1,163 to 1,155, the worldwide wires
  164 and Indonesia 105 unchanged; the snapshot holds no body links, which are where most cross country
  duplicates are found.
- New export fields: meta.llm_prompt_version and labels_on_older_prompt (in meta, on each day of the
  daily files and in global_series.json, for the grey timeline mark); meta.relay_human_study,
  relay_provisional_reason and relay_publishable for the hand coding study, and
  relay_reliability_history for a stability check made under an older prompt, with the check's
  prompt_version; meta.candidate_recall, the latest recall audit of the candidate gate; meta.themes_changes,
  the dated themes versions; the uncredited_copy route in meta.routes and route_totals, where every route
  now appears, at zero until something reaches it; in outlets.json, leaning_source_weak and
  leaning_second_source, and the ownership group state_funded_international; missed_estimate_since beside
  the missed item estimate; and the counts of relayed and shared outlets (relay_outlets, shared_outlets).
- New database tables: body_fingerprints and body_links (body near-duplicates), feed_boilerplate (per
  feed summary boilerplate, kept a day), recall_audits, aap_state (where the AAP listing read stopped),
  junk_discovery (the ledger of discoveries taken off for junk feeds), feed_poll_log (each poll of a feed
  both collectors poll), seen_urls (the addresses of pruned items, so they are not counted again), and
  schema_migrations (one-time migration steps); on the relay's database,
  relay_changes and relay_ack. New columns: articles.audit_keep, articles.day_of_record and
  articles.extract_version, classifications.body_refreshed, feed_polls.collector (part of the key) and
  feed_polls.estimator. Every column is added by a one-time migration step, so an older database opens
  as before.

## Publication: a human coding study decides (2026-10-12)

An outside review pointed out that the reliability study measured stability, not correctness: the
automatic study is the same model (claude-sonnet-5) re-judging its own sample with the same prompt
(classify_llm.request_params, only the model name overridden, no temperature set, so the API default
applies), and a passing rerun (kappa 0.62 on 9 October) had replaced the provisional marking with
published counts. The owner has reversed the earlier decision to run without a human in the loop: he and
two research assistants hand code a fixed sample, and unchecked state sourcing is published only when
Cohen's kappa between their codes and the model on unchecked state sourcing versus independent
journalism is at least 0.6 (config.KAPPA_WARNING_THRESHOLD), with at least 10 articles on each side,
overall; a language with at least 20 items in that comparison and a lower kappa is withheld on its own.
Until then the counts are provisional, with the reason in meta.json (relay_provisional_reason,
relay_human_study). pipeline.agreement sample now draws the hand coding sample, seeded and stratified by
language and, within each language, by the model's current category: about 200 articles for each
language with at least 300 model labelled articles, from articles with an extracted body. The sample
drawn on 12 October (data/reviews/human_sample_2026-10-12.csv) holds 1,400 articles, 200 each in
English, Spanish, Malay, Korean, Arabic, French and Portuguese, with no model label in the sheet;
data/reviews/CODING_GUIDE.md tells coders how to decide and fill it, and compute now stores the per
language results that the export's language veto reads, which it previously left out. The model study
is still run and recorded as a stability check: it never makes counts publishable, a passing one leaves
them provisional, and a current one below 0.6 withholds them. Because labels from the older prompt are
being judged again, pairing a first label from that prompt with a re-judgement under the current one
would measure the prompt change and call it stability, so pipeline/second_rater.py draws only first
labels made under the current prompt, and the automatic study waits, as "waiting_for_labels" in the
study state, until RELIABILITY_MIN_POOL such labels have a body on disk; a forced study waits too. A
study batch keeps the prompt its requests carried, pairs only first labels made under that same prompt
(one judged again while the batch ran drops out), and records the prompt in the study's details; a new
prompt is a reason for a new study, like a new model. The export treats a check made under an older or
unknown prompt as history: it moves from relay_reliability to relay_reliability_history, withholds no
counts and vetoes no language. A check stored before studies recorded their prompt is dated by when it
was computed, so the 9 October rerun is now history and the site shows no current stability figure until
the next study. METHODOLOGY.md (pipeline/methodology.py) gains a stability paragraph from
meta.relay_reliability_history giving the old check's date, sample size and kappa, saying it ran under
an earlier version of the classifier's instructions and is kept as history, withholding nothing, and
saying from meta.relay_study when the next check runs; the provisional counts paragraph points to a check
"described below" only when one is rendered, and the withheld languages sentence names unchecked state
sourcing counts as its subject instead of a dangling "They". On the site, the notes and the Stability
check row of the method facts carry one sentence from TrackerCompute.stabilityStatusText, and the
Reliability study technical note, the unchecked state sourcing FAQ answer and definition in
docs/content.json and the method note in docs/index.html say that hand coding alone decides
publication. No ruleset, gate or category change.

## Definitions: state origin is authored or commissioned, then run unaltered (2026-10-12)

The state origin definition said the text was written by an entity of the Chinese state and that nobody
at the receiving publication formed an editorial view, and two of its own examples failed it: the
Corriere della Sera placements were written by the publisher's own studio, CairoRcs, to a Xinhua
commission, and an opinion editor chose to run every ambassador's op-ed. The definition now reads, as
the original study's codebook does, text authored or commissioned by a Chinese state entity and
published without substantive editorial alteration; deciding to run a piece, or placing it on an
opinion page, is not an alteration. Rewritten in the model prompt's state origin paragraph and its
"Answer A only when" line (pipeline/classify_llm.py, part of prompt 2026-10-12), the exported category
text that feeds the methodology file (pipeline/export.py), the site's category definition
(docs/content.json) and the method prose (docs/index.html). The four routes already match the wording:
a wire credit and a distribution stamp carry state authored text, a sponsored or partner disclosure
needs a named state entity as the sponsor, and a diplomatic byline is state authored. No ruleset, gate
or category count change; METHODOLOGY.md picks up the wording at the next export.

## Model stage: session labels name the prompt they were made under (2026-10-12)

Labels made in an interactive session must now carry model_version "claude-sonnet-5 (session, prompt
<PROMPT_VERSION>)", today "claude-sonnet-5 (session, prompt 2026-10-12)". A new command, `python -m
pipeline.labels export-prompt --out <dir>`, writes SYSTEM_PROMPT.md and OUTPUT_SCHEMA.json from
classify_llm as the code holds them, each headed with the prompt version, a sha256 of its content and
the model_version the labels must carry, so a session pass is never run on a stale copy of the prompt;
data/labels/README.md says the labelling agents must be given these two exports. The ingest
(pipeline.labels) counts as older_prompt and skips, never fatally, a session label made under an older
prompt or naming none, since it would only be queued for the API again; the article stays in the queue
for a label under the current prompt. A line whose model_version does not name the session is
malformed, so a session label can no longer pass for an API label ("claude-sonnet-5 (prompt
2026-10-12)"). The committed files from 30 September to 4 October name no prompt and are skipped on
every ingest, which changes nothing in the database: all their labels were applied at the time. No
ruleset, prompt or category change.

## Audits: what the candidate gate and the relevance gate miss (2026-10-12)

The model reads an article only when a weak state origin signature or an official sourcing trigger
fires (classify_rules.classify_article; llm_triggers in pipeline/signatures.yaml), and agency copy run
without a credit line names none of these. On the 12 October snapshot 69,504 articles were labelled
independent journalism or not relevant by rules and never reached the model, against 9,781 the model
judged, and 153 of them repeat the headline or feed summary of a state origin item another outlet
carried with its credit (the lookalike route of ruleset 2026.10.1 now sends such copies to the model).
New pipeline/recall_audit.py: `sample` draws a seeded sample of those articles, stratified by language,
with a tenth of the draws on the lookalikes and the outlets with a wire copy habit (named, news
agencies, five or more state origin labels, or three or more lookalikes) oversampled threefold, and
writes a CSV with no rule label to data/reviews/ beside a key that freezes the strata and the rule
labels; `compute` weights the coders' labels back to the population and stores the share placed in
state origin or unchecked state sourcing, with a Wilson interval on the effective sample size, per
language and overall, in a new recall_audits table, which the export reads as meta.candidate_recall.
METHODOLOGY.md and the README say the gate cannot see uncredited relay and give the latest miss rate.
The first sample, 300 articles, is data/reviews/recall_audit_2026-10-12.csv; the rate is not yet
measured. AsiaOne, which the sample names for oversampling, was registered the same day.

Items the relevance gate rejects are still pruned after three days, but a random 2 percent of them
(config.GATE_AUDIT_KEEP_SHARE) is now kept for good, so the gate's recall can be audited for any past
period instead of only the last three days. Whether an item is kept is decided by a salted hash of its
url_hash (store.gate_audit_keep): reproducible, the same on the relay and the hosted side, and blind to
outlet, country and language. The decision is recorded in a new articles.audit_keep column, added to
older databases on open and backfilled for the rejections still stored (4,434 of 217,678 on the
2026-10-12 snapshot, 2.0 percent); prune_gated_out decides any undecided rejection before it deletes and
never deletes a kept one, and rows with a planted byline flag stay exempt as before. Kept rows keep every
field. pipeline/gate_audit.py `sample` now draws only from the kept set, takes --since and --until,
stratifies by language with a per-language floor, and writes each row's stratum size and weight;
`compute` reports the weighted rate next to the raw one. The database grows by about 1,700 rows, roughly
1.8 MB, a day, some 50 to 55 MB a month. No ruleset, gate or category change.

## Relay: outlets refused by the runner move to the relay, with their refused articles (2026-10-12)

Pearls and Irritations, which has run signed pieces by Chinese embassy officials, refused the hosted
runner on all 45 China-relevant articles it found there (HTTP 403), and article 3945 could not be
classified again because its re-fetch was refused (refetch_failed:http_403). Every active outlet with
most of its China-relevant articles of the last 30 days refused by the runner (HTTP 403 or 429) was
requested once from the owner's machine with the project user agent and the robots.txt check, through
the pipeline's own fetch and extraction: 22 returned the article and move to the self-hosted collector,
each with a dated note giving the evidence (Pearls and Irritations, Reporter.al, Semanario Universidad,
El Confidencial, Daily Graphic, The Standard Gambia, Kandit News, Linkiesta, 38 North, Yabiladi, Telma,
ION News, The Vibes, One PNG, Ndarinfo, Suriname Herald, North Press Agency, Avesta, iWitness News, El
Nacional, Al Sahwa, News Diggers). 50 outlets refused that request as well and stay where they are, among
them TASS, Kyunghyang Shinmun, Bloomberg, NDTV and the New York Times; the seven relay outlets in the same
position (The Washington Times, Times of Israel, Davar, Behadrei Haredim, Khmer Times, UPI, Tempo) are
refused from both places. Until now the relay fetched only what its own feeds discovered, so an article
the runner had found and been refused stayed failed once it left the feed. The relay now reads the
machine's copy of the main database, read only, and queues those articles for its own outlets, at most
20 per outlet per run and once each, and not for an outlet whose latest fetch on the relay was refused
too (pipeline/relay.py seed_refused). The bundle carries articles fetched in its seven days as well as
those discovered in them; on ingest a failed row moves on to fetched without keeping its old failure,
and a labelled article whose re-fetch was refused gets its body back with its label and status
untouched, so it can be classified again. The relay also no longer re-admits an item the section feed
correction gated out from its older copy. No ruleset, gate or category change; collection only.

## Relay: the bundle carries what changed, and no checkout keeps old bundles (2026-10-12)

The relay branch always held one parentless commit, so the repository on GitHub never grew, but the
owner's checkout did: the bundle was committed in it, and each force push left the old bundle in the
reflog of origin/relay, 230 bundles and 667 MiB by 2026-10-12. The commit is now made and pushed from a
throwaway repository (pipeline/relay.py push_bundle), so nothing stays behind. With the shared polling of
148 outlets the hourly bundle would have grown from 3.98 MB to about 9.3 MB, nearly all of it rows the
runner already held, sent again every hour for seven days. After it saves its database the runner now
force-pushes an acknowledgement to the relay-ack branch (python -m pipeline.relay ack): the build time of
the latest bundle it took in with no gap before it. The next bundle carries only the article rows and
shared-feed polls changed since an hour before that, read from a change log that triggers keep on the
relay's database (relay_changes); the small per feed and per day records still cover the whole window,
and a bundle opens with a record saying which kind it is. A runner whose database went back to an older
cache or the snapshot sees the gap, keeps its own acknowledgement and sends it back, so the next bundle
fills the gap; with no acknowledgement, or one older than seven days, the bundle is whole as before. Two
things ingest never used stay home: entry dates of unsaturated polls, which only rejudging a saturated
poll reads, and gated-out rows of shared outlets older than ingest accepts. On a simulation of the relay
database with the shared outlets added, a whole bundle is 7.1 MB instead of 9.3 MB, and an hourly bundle
with the acknowledgement one to two hours old is 0.6 to 1.0 MB. No ruleset, gate or category change;
collection only.

## Collector: short feeds polled by both collectors (2026-10-12)

Every active feed was measured on the feed_polls ledger from 12 September: 1,159 of 2,139 polled feeds
return 20 items or fewer, and 221 of those (218 outlets, all on the hosted collector) came back as a full
window with nothing seen before in more than a tenth of their polls, together 11,867 of 41,663 polls and
an estimated 331 thousand missed items (under the estimator of the time; see the next entry), of which an
estimated 4,800 would have been China coverage. The hosted runs that actually fired were a median 2.7
hours apart (3.6 hours in the last week, against a cron of every hour), so dropped runs, not small feeds
alone, drive most of it. The thirteen worst feeds were requested once each with the project user agent
after the robots.txt check: none honours a count or page size parameter, and no same scope larger RSS
feed was found. An outlet can now carry collector: both. The runner and the relay on the owner's machine
then both poll its feeds, and its article pages are still fetched once, by the runner. The relay's items
reach the main database through the bundle and are deduplicated by URL, so each article is inserted
once, counted in discovery once and linked to near-duplicate headlines once. The runner keeps its own
feed health for these outlets. Every poll of a shared feed is logged with the collector that made it
(feed_poll_log), and a poll counts as saturated only when its window misses the previous poll by either
collector; a relay poll is judged against the runner's polls when its bundle is ingested, and a runner
poll that a relay poll reached the bundle after is judged again. 148 outlets moved to both: the 150
outlets behind the 152 short feeds the audit expected to fall under a tenth of polls saturated, less four
that are now relayed only, plus PR Newswire and GlobeNewswire; the registry counts 149 since Yemen Press
Agency's Arabic feed became an outlet of its own. That adds about 208 feed requests an hour to the relay
and no page fetches. The site and the methodology count the relay's own outlets and the shared ones
apart, badge a shared outlet "also relayed", and when the relay falls silent call only its own outlets
incomplete, saying that the shared ones fall back to the runner's hourly polls. Collection only; no
ruleset, gate or category change.

## Collector: feed saturation measured for the relayed feeds, and guarded against batch publishers (2026-10-12)

The relay recorded every feed poll in its own database, but its bundle carried only feed health, so
feed saturation and the missed-items estimate were never measured on the hosted side for the outlets it
collects: on the 12 October snapshot 28 of their feeds had any poll rows, and those only from the runner.
Each bundle now carries the relay's per feed and day poll rows for its seven day window, and feed_polls
records which collector made each poll (collector, part of the key, so a feed polled by both keeps one
row each; rows already stored are attributed to the collector that owns the database). The hosted side
replaces its copy of a relay row with the day's running total rather than adding to it, so a bundle sent
twice counts each poll once, and never touches the runner's own rows. On the snapshot this adds 792
feed-days, 7,127 polls and 297 saturated polls; meta.feed_saturation and the country saturation warnings
include them, and the section feed flooding guard reads the larger single collector's ledger so an item
new to both is not counted twice. The estimate of items lost between polls was the window's own
publishing rate times the gap since the previous successful poll, and a publisher that replaces a short
feed wholesale once a day made every batch look saturated with a window a few minutes wide: NewsMada
delivered 118 new items in 30 days and was booked 7,953 missed, and five such feeds about 15,000 between
them. The rate is now taken over at least two hours, or the feed's median window span over the last
three days when that is shorter (never under a quarter hour); one poll's estimate is at most twice the
new items the feed delivered per day over the previous fourteen days, scaled for gaps over a day; and a
feed polled at least three times a day that delivers no more than two and a half windows a day is a
batch publisher, whose burst polls count only whole batches that fell between polls. Replayed on the
three and a half days whose every item is still on record, the estimate falls from 57,651 to 34,028;
extrapolated to the ledger since 15 September it falls from 624,924 to about 406,000, the five batch
feeds from 15,423 to 1,121, and the estimated missed China items from 8,859 to about 5,400. Ledger rows
already written keep the old figures, but every row now names the estimator that made it
(feed_polls.estimator, config.MISSED_ESTIMATOR_VERSION 2026-10-12; v1 for older rows), and the site, the
daily files, the country warnings and outlets.json report the estimate only from rows of the current
estimator, marked "estimated since" the first day it ran, while polls and full windows still count every
poll on record. Collection diagnostics only; no ruleset, gate or category change.

## Collector: Google News sitemaps beside short RSS feeds (2026-10-12)

Some short RSS feeds overflow between polls at a busy site: at peak the ten items of the Rio Times feed
spanned 0.19 hours and Clarín's 0.34, and PR Newswire's twenty item list lost an estimated 431 China items
in 30 days, about 103 of them state origin. A feed of type news_sitemap (registry field news_sitemaps,
pipeline/news_sitemap.py) now reads the Google News sitemap a site lists in robots.txt, which carries
every article of the last two days with its title and publication date. It reads a urlset or a sitemap
index (at most the three most recently modified child files, one level), under the same robots.txt
check, per domain pacing and user agent as every other request. Each sitemap is polled as an extra
editorial feed after the outlet's RSS, which stays. It keeps entries up to 48 hours old, a day inside the
three day retention of rejected items, so nothing is counted twice. Items already known are skipped
before the keyword gate. New items pass the gate on their title alone, because a sitemap has no summary,
with the title_only terms of Gate 2026.10.1: with them a title alone passes 69 percent of PR Newswire's
past state origin items (49 percent on the main lists only), 25 of 49 Rio Times China items, and 38 of 39
at Telegraf and 83 of 94 at Clarín. An item the gate turned down on its title is readmitted if an RSS
feed later brings a summary that passes. A sitemap item with the same path and title as one already
stored for the outlet on another host, as with Telegraf's www and politika hosts, is not inserted. When
the sitemap reaches back to the previous poll, a saturated RSS poll's missed estimate is scaled down by
the share of the RSS window that the sitemap also lists. Each sitemap URL was requested once with the
project user agent. Registered: PR Newswire (prnewswire.com's whole English release list, issuers
worldwide included; 31 of the 36 RSS entries in its window were on it, and the items keep the outlet id
us_prnewswire in the worldwide distribution stratum), Rio Times, Clarín, Telegraf (its sitemap leaves out
the ona. and biznis. subdomains, which the RSS still covers) and AsiaOne. Daily Times answered 403 and is
not registered. Expected cost at the current cadence: about 1,700 sitemap requests a month and roughly
300 to 850 extra article fetches before the title_only list, which adds about 8 to 25 a day at PR
Newswire, because only items whose titles pass the gate are fetched. Collection only; no ruleset or
category change.

## Collector: the AAP release pages of ten ACM mastheads are read (2026-10-12)

The Canberra Times, Newcastle Herald, Illawarra Mercury, The Examiner and the six ACM mastheads added
today each carry a "Press Releases from AAP" page, where paid releases from Xinhua Silk Road, People's
Daily, CGTN, Global Times, CMG and provincial bodies reach Australian readers (49 from 2026-09-03 to
2026-10-12, found by hand). The page is an empty shell filled in the browser by AAP's aaprwidget.js, so it
was recorded as unreadable and never polled. A new section type, aap_widget (pipeline/section_pages.py),
reads the widget's own source, the AAP release listing at newsroom.aap.com.au, under the usual robots.txt
check, user agent and one request every three seconds. The first read goes back to 2026-09-03 (146
pages, 1,459 releases, all 49 among them). The releases count as each masthead's output, like any press
release section, and AAP is not registered as a worldwide distribution wire. Each masthead keeps its own
copy, linked by its title to the other copies as one underlying item, and arrives tagged as the outlet's
press release section. Because only about a quarter of AAP releases name a China term (352 of 1,459) and
all 49 state releases do, the keyword gate applies to these items, unlike other section feeds.
Australia's discovered totals rise by about 37 items a day per masthead; the backfill counts on each
release's publication day (see the backfill entry). Discovery reads the listing once before any worker
starts (section_pages.aap_preread, called from fetch_feeds.run), because reading it at the first
masthead's request, under a lock, held about ten of the sixteen discovery workers through the first
run's backfill of roughly 8.5 minutes; the mastheads take their copies from what was read. The read may
use at most 30 percent of the time the stage has left (AAP_PREREAD_SHARE). A read cut short there, or at
the 200 page cap, keeps what it reached, and the page it stopped on, the oldest release it reached and
the date it was reading back to are stored in a new aap_state table once a masthead has been polled with
the read; the next run reads the new releases from page 1, then resumes at the stored page and reads on
to the stored date, stepping back toward page 1 if withdrawn releases have moved the stretch up. A page
that fails still fails the read it belongs to, so the next run reads that stretch again. Each run reads
back to the earliest successful poll among the mastheads polled in the last seven days
(AAP_RECENT_DAYS), less six hours, so a run stopped at its deadline after some mastheads leaves the next
read covering the rest, while a masthead failing for longer no longer sets the date. Each masthead keeps
its own row for a release, at the release's page with a "masthead" parameter, so the fetch stage used to
download the same page once per masthead (about 3,500 requests for the backfill and 90 a day after, all
to one host). pipeline.fetch_articles now maps a URL to the page it copies (FETCH_KEY_RULES, with the AAP
release service as the only rule): the first row of a key is fetched, and its siblings, moved up behind
it in the queue, take its saved body and fetch outcome without a request, after a check that the body
file on disk still matches its hash. Only a saved body is lent, so one bad response is not copied to
every masthead, and each row is still classified on its own. No category, ruleset or gate term change;
collection only.

## Section discovery: plural section names, and special readers kept on a re-probe (2026-10-12)

The section probe matched "Business Wire" only in the singular, so AsiaOne's old "Business Wires" section
(/business-wires) did not count as a press release section, and "Advertorials", "Partner Contents",
"Paid Posts", "Branded Contents" and "Brand Studios" slipped the same way. The press release pattern now
takes the plural and the spaced forms of Business Wire, PR Newswire, GlobeNewswire, EIN Presswire and
Accesswire, plus "Press Wire" and "PR Wire"; the sponsored and partner patterns take the plurals of their
English names. A wire still needs its PR qualifier: a bare "Newswire", "Wires" or "Newswires" is an
agency desk or an outlet's name (newswire.lk), and "Business", "Newsletter" and "Partners" alone stay
editorial. A desk crediting a sponsor and a site-wide feed are still never sections. `discover_sections
apply` rebuilt every section feed from the probe file and would have turned the ten aap_widget sections
back into type page, which reads nothing on those pages. It now keeps any section feed whose type is
neither rss nor page exactly as registered, whether the new probe finds the same section, misses it or
cannot reach the site, and keeps that section's record (and its readable flag) in release_sections. The
probe recognises a section page that embeds newsroom.aap.com.au/static/aaprwidget.js and records it as
aap_widget without looking for a feed on it, so another ACM masthead added later is registered on the
right reader. No section already recorded changes kind; no ruleset, gate or category change.

## Collector: items from the seven removed section feeds face the keyword gate (2026-10-12)

Seven feeds registered as press release, sponsored or partner sections were removed on 2026-10-12: the
five desks and site-wide feeds of the entry further down (Index.hr's sport desk, the Mirror's rss.xml, LIGA.net's
/ua/all/rss.xml, Novaya Gazeta Europe's /feed/rss and Asia-Plus's asiaplus.news/feed/), the Japan Times
top stories feed and the 7NEWS "Sponsored" feed (next entries). Every item they brought in had passed
without the keyword gate and still carried a section arrival tag. Each export and each relay run now
drops that tag and runs the gate on the stored title and summary (pipeline/section_retag.py), as for any
editorial feed item, without the feed's summary boilerplate and with the item's url, as discovery reads
it. Asia-Plus's Russian site feed is now polled by tj_asiaplus, the Russian outlet split from Asia-Plus
English, so the 409 items the section poll stored under the English outlet first move once to
tj_asiaplus with their outlet-day discovery counts and the feed's poll records, so none is counted under
Asia-Plus English and none twice, and are then gated in Russian there (13 stay relevant on the local
database). For the first six feeds, 8,502 articles on the local database: 42 stay relevant, keep their
labels and now count as arriving by an editorial feed; the other 8,460 become gated out, which takes them
out of every count, and their 8,250 current labels (8,239 not about China, 11 independent journalism, no
state origin or unchecked state sourcing) are withdrawn and kept. The 7NEWS feed's 716 items all fail the
gate, and their labels (714 not about China, 2 independent journalism) are withdrawn and kept. fail_reason
records why, and each day's gate relevant discovery count drops by one per item. Pruning never deletes a
gated-out row that holds a label or a review. The relay no longer re-admits a corrected item from its
older copy. The correction is idempotent. Over the first six feeds, section arrival counts for relevant
articles fall from 8,772 to 4,175 sponsored, 3,826 to 672 partner and 15,174 to 14,423 press release,
and the 7NEWS items leave the sponsored count as well. All seven removed feeds are recorded with their
reasons as rejections on their sections in release_sections, the one list both section discovery and
the correction read, so a later probe or apply never registers them again, nor the 7NEWS topic page, the
Index.hr sport desk or the single Japan Times release they came from. No ruleset, signature or category
change.

## Registry: 7NEWS "Sponsored" section feed removed (2026-10-12)

7NEWS was registered with a sponsored section at /lifestyle/sponsored, polled on /lifestyle/sponsored/feed,
because the homepage links that page as "Sponsored". The page is the site's dormant "Your Feed" topic
(described as deals, products and offers), which the site also attaches to sport and 7you stories, so its
feed is the latest stories across desks: requested once with the project user agent after the robots.txt
check, it returned 100 items, 63 from /sport/ and 23 from /7you/, and every card on the page is marked as
not sponsored. Over 30 days it brought in 716 items through the section's gate exemption with the term
section:sponsored; 714 were labelled not about China (714 of 7NEWS's 727 such labels) and 2 independent
journalism. The feed is removed and the section kept in release_sections as unreadable, the outlet and its
editorial feed /rss unchanged. /sponsored and /brandedcontent return 404, and no other sponsored, partner
or branded content section was found on the homepage, so nothing replaces it. PerthNow and The West
Australian, on the same platform, register no section feed and link no such section. No ruleset, gate or
category change; collection only.

## Registry: Japan Times release and sponsored sections corrected (2026-10-12)

The Japan Times was registered with a partner section at /comfacts/348, which is a single press release
(GAKU naming the paper a media partner), polled on /feed/topstories/, the "Today's Top Stories" feed every
page declares as its alternate. That feed has been frozen since February 2025: its five items are news
stories from 2023 to 2025, which entered the database through the section's gate exemption with the term
section:partner. The page belongs to the paper's news release section, /comfacts ("News releases, powered
by Comfacts"), whose own feed /comfacts/feed/ returned 30 releases, and the paper's sponsored content
lives apart on info.japantimes.co.jp ("Special supplements: sponsored contents planned and edited by JT
Media Enterprise Division"), whose feed /cms/feed/ returned 10 items. Both were requested once with the
project user agent after the robots.txt check, and both now replace the wrong entry, as press release
and sponsored sections. No China Daily, China Watch or Xinhua placement was found in either section or
among the paper's stored articles; the sponsored archive holds one 2022 country report on the Greater
Bay Area. No ruleset, gate or category change; collection only.

## Registry and collector: feeds read in the wrong language, and feeds that served junk (2026-10-12)

A script count over the titles of the last 30 days (share of Cyrillic, Arabic, Han, Hangul, Thai,
Devanagari and Thaana letters, and a stopword check for the Latin script languages) found outlets whose
feeds publish in another language than the one registered, so the keyword gate, the residual rules and
theme tagging read them on the wrong terms. Asia-Plus English had carried the Russian site's feed, so
"Таджикистан и Китай намерены расширить сотрудничество" was read on English terms. Corrected in
sources/outlets.yaml, each outlet's notes saying how: Haqqin.az is Russian, not Azerbaijani; Tunisie Focus
French, not Arabic; Goobjoog Somali, PSM News Dhivehi, Mimi Mefo Info French and NewsMada Malagasy. The
Arabic feeds of North Press Agency and Yemen Press Agency, Monaco Tribune's English feed and Asia-Plus's
Russian feed become outlets of their own language. Hankyoreh English now polls its English edition feed,
since its old feed serves the Korean site. Telex English is inactive because its feed served the
Hungarian site and no English feed could be confirmed. Stored articles keep the language they were
discovered with, and reclassification never revisits a language, so a new correction
(pipeline/language_retag.py) runs at the start of every discover stage. It sets each article's language
to its feed's and clears its theme tags. It withdraws the residual rules labels made in the wrong
language and keeps them, 64 on the local database (51 not about China, 13 independent journalism), and
the rules stage judges those articles again. It also reruns the gate, as the collector reads it, on
gated-out items still stored, which re-admits 6 (3 Korean, 2 Hungarian, 1 Arabic); items the section
feed correction gated out are left to it, since it already gated them in the feed's language. 1,440
articles in 14 outlets change language, including 35 older items from The National and the Oman Observer
stored as Arabic before those outlets were registered as English. Model labels and state origin labels
from a signature are not touched. The same title count found seven feeds that no longer carry their
outlet's news: Les Nouvelles Calédoniennes now polls its site's own /rss.xml, since its FeedBurner feed
serves Vietnamese shoe advertisements, and Centrafrique Presse, La Voix de Djibouti, Guatevisión, ORTC,
BelaPAN and the Pacific Islands News Association are inactive, because their feeds serve gambling posts,
advertorials, machine-generated titles or a "No Feed Access" placeholder and no clean feed could be
found on their sites. Deactivation withdraws no stored label, so a second correction
(pipeline/junk_feeds.py, run at the start of every discover stage and every export) sets aside every
item the seven feeds brought in from the first day their stored titles were junk (2026-09-03, or
2026-09-24 for the three outlets added that week), which each outlet's new junk_feed entry records. Its
labels are withdrawn and kept as not current, among them the one label taken from these items, a
Centrafrique Presse gambling post counted as independent journalism. On the local database that is 334
stored items, none ever requeued or pruned, and 2,127 discoveries taken off the per day denominators
(BelaPAN 1,163, Centrafrique Presse 505, Guatevisión 121, La Voix de Djibouti and ORTC 120 each, Les
Nouvelles Calédoniennes 60, the Pacific Islands News Association 38), recorded in a ledger
(junk_discovery) so that a rerun or a relay bundle sent again takes nothing off twice. No ruleset,
signature, keyword or category change.

## Collection: pruned items are not counted again (2026-10-12)

The address of every pruned item is now remembered (store.seen_urls, keyed on the first 15 hex digits of
its url_hash), for SEEN_URL_RETENTION_DAYS (120) from its latest sighting, or SEEN_URL_UNSEEN_DAYS (14)
for one no feed lists again, so a feed that keeps listing an item no longer brings it back and counts it
among the items screened after every prune. Such repeats were 8.2 percent of the discoveries of
2026-10-01 to 2026-10-11, at least 123,682 since collection began and near 8 percent by extrapolation,
so discovery denominators fall by about 8 percent from the day this runs. The days before it are left
as counted, because the pruned addresses that would show which items came back are gone; the export
dates the change (meta.rediscovery.fixed_from, the day after the first run that remembers pruned
addresses), the site notes it under the running total, and a share taken across that date compares a
denominator with repeats against one without. On the relay, items
handed off and pruned are remembered the same way. Collection only; no ruleset, gate or category change.

## Collection: backfilled items count on their publication day (2026-10-12)

Every figure still counts an item on the UTC day it was discovered, with one exception decided at insert
time and stored in a new articles.day_of_record column. An item brought in by a backfill read counts on
its publication day when it was discovered more than BACKFILL_LAG_HOURS (36) after it was published and
was published on or after COLLECTION_START (2026-09-03). Its discovery is recorded on the same day, so
the count and its "% of all its news" denominator move together, and Australia's timeline does not show
five weeks of AAP releases (about 1,459 for each of ten mastheads) on one day. A feed's first successful
poll ever, and its first after more than BACKFILL_GAP_DAYS (3) without one, is a backfill read too, so a
new outlet or section (38 feeds of 28 outlets at the next run, 21 of them Australian) or a feed back from
an outage (18 feeds) does not put its older items on the day it was read. The rule was not applied to
every item: measured on the 2026-10-12 database, it would have moved 1,473 of 80,272 classified articles
(1.8 percent, 23 percent in Kazakhstan and 18 in Australia), and the denominators of days whose rejected
items are already pruned could not have moved with them. No existing row changes day. Items backfilled
onto days before the Gate 2026.09.6 and 2026.09.7 marks were admitted by the current gate. The
methodology says so under What the numbers mean, beside the discovery day rule, with the lag, the gap
and the start date read from the configuration. No ruleset, gate or category change.

## Collector: feed summary boilerplate found per feed (2026-10-12)

Some feeds wrap every item's summary in the same text. Ghana News Agency's world feed puts "Stay
up-to-date on the latest news from Ghana and the world" before the lede and "Get the full content now"
after it, so any two of its items shared about 55 percent of their summary shingles whatever they
reported, and the recall audit counted four of its articles as lookalikes of another GNA item. A new
module, pipeline/boilerplate.py, finds such text per feed from the data: in a feed's items discovered in
the 30 days before its latest one (at most 200, and only when at least 10 carry a summary), every five
word shingle found in more than half of the summaries is boilerplate, and those words are removed before
the summary is read. The result is kept per feed in a feed_boilerplate table for a day. On the
2026-10-12 snapshot 368 of the 2,161 feeds active in the last 30 days have some; the worst are members
only and log in notices (Budapest Times, News Diggers), placeholder Lorem Ipsum text (NBS Uganda,
Guatevision), a podcast feed's fixed line (Sveriges Radio), Post-Courier and GNA's wrappers, and most of
the rest are "The post ... appeared first on" lines. The lookalike route and the recall audit compare
summaries without it: the audit's lookalikes go from 153 to 149 (summary matches from 17 to 13), the
four dropped being exactly the GNA same outlet pairs, and the route's matches over the same population
are unchanged at 145. The keyword gate reads it the same way (Gate 2026.10.1). Body near duplicate
linking never reads summaries and is unaffected. No category change.

## Underlying items: near-duplicates linked on body text as well as headline (2026-10-12)

Title matching (pipeline/fetch_feeds.py, word overlap of 0.7 within a country over three days) missed
syndicated copy under a rewritten headline and never looked across countries. The fetch stage now
fingerprints every saved body (a 64 value one permutation MinHash of five word shingles, five character
shingles for scripts without spaces, pure Python, about a millisecond a body) into a new
body_fingerprints table, and links articles from different outlets, in any countries, discovered within
seven days of each other, whose signatures estimate at least half their shingles shared, into a new
body_links table (pipeline/body_dups.py). Same outlet pairs stay with title matching, and a body matching
its own outlet's text under another headline is treated as page template and never linked (50 such
bodies locally, mostly paywall stubs and extraction failures). On the 6,855 bodies stored locally the
pass linked 124 pairs title matching had left apart, 104 across countries (AP, Reuters, AFP and EFE copy
under new headlines, Media OutReach and GlobeNewswire releases, republished commentary, and one Xi'an
International Communication Center piece run by outlets in Kyrgyzstan and Uzbekistan); none of a hand
checked sample of 30 joined two different items, while pairs between 0.3 and 0.5 began to join
different versions or different stories about one event. articles.dup_group_id is unchanged and stays
the only group the model's label is copied along, so no category count moves. The export joins title
groups through body links for the underlying item figures (uniqA, uniqAB and the syndicated badge), which
can only fall. Measured while underlying items were still counted per country and day, the links moved 3
independent journalism items and 1 not relevant item, none in state origin or unchecked state sourcing,
because most links cross countries; since Data schema 12 counts each item once per window and across
countries, links across days and countries count too. Translations are not matched and no machine
translation is attempted; the methodology file and the README say so. Stored bodies are fingerprinted by
the fetch stage for up to three minutes a run until none is left, or at once with python -m
pipeline.body_dups backfill. No ruleset, gate or category change.

## Crawler: robots.txt Crawl-delay honoured (2026-10-12)

The crawler read robots.txt as RFC 9309 defines it, which has no Crawl-delay, so a site asking for one
was still fetched every three seconds; AsiaOne asks for 60. pipeline/robots.py now reads Crawl-delay from
the same group as the Allow and Disallow rules (the group naming the crawler, else "*"), and the one
rate limiter, pipeline.fetch_articles._rate_wait, paces each domain at the larger of three seconds and
that delay, capped at ten minutes with a log line above the cap. Article pages, feeds, section pages,
section discovery and the relay all pace through it. Fetching robots.txt once for each of the 1,996
domains behind the active outlets (1,869 serve one) found 147 that set a Crawl-delay for us, none in a
group naming the crawler: 39 at three seconds or less, 68 above three and up to ten, 24 up to 30, 11 up
to 60 and 5 at ten minutes or more (one asks for 1,200 seconds and is paced at 600). The 108 domains above
three seconds carry 135 feeds of 100 active outlets and 8,497 gate relevant articles over the last 60
days, most of them at ten seconds (Mirror, SCMP, CNA, The Straits Times). So that a slow domain does not
hold up the run, a worker sleeping through a long delay hands its slot to the next domain, an article
whose slot would fall past the deadline stays queued for the next run without spending an attempt, a
failing slow site is retried on a later run rather than within this one, and a feed host with a long
delay gets its first feed every run and further feeds only while they fit in ten minutes, taking turns by
the hour. Classification is unchanged.

## Store: one-time migration steps run once per database (2026-10-12)

pipeline/store._migrate seeded daily_discovery and daily_outlet_discovery from the articles table
whenever they were empty, on every new connection, and the other data backfills in it (audit_keep
marking, the per outlet-day gate_relevant backfill, llm_usage pricing, the feed_polls rebuild) ran
whenever their column was missing. The fetch workers each open their own connection, so several
connections opened together on an unmigrated database all found the step undone and ran it: the seeds
failed on the primary key and the column steps on "duplicate column name". Each of these steps now checks
and runs under BEGIN IMMEDIATE and is recorded in a new schema_migrations table in the same transaction,
so exactly one connection runs it and the rest wait for the lock and skip it. Every column the migration
adds (day_of_record and extract_version among them) is added the same way. On a new database the seeds
find no articles and are recorded as done, so they never later turn articles stored without
record_discovery into discovery counts. The runner's database already has its discovery tables filled
and its columns added, so nothing is recounted; each step is recorded on its first connection after this
change. No ruleset, gate or category change.

## Registry: AsiaOne registered (2026-10-12)

AsiaOne (Singapore) is registered as sg_asiaone, because the owner's paper found it had the highest
unchecked state sourcing of the outlets it read, with agency copy run without a credit line, and it was
not in the registry at all. Its China, Asia and World section feeds (asiaone.com/rss-feed/63335, /4 and
/5) were each requested once with the project user agent through pipeline.feeds_util.fetch_feed, after
the robots.txt check, and each returned 10 items; the News feed (/rss-feed/1) returns 404. They are
polled by the hosted collector, at the 60 second Crawl-delay AsiaOne asks for, and its Google News
sitemap is read beside them. No press release, sponsored or partner section was found on the homepage
under the discovery rules, and the old /business-wires section returns 404. Ownership is private: SPH
Media has been a minority shareholder since a December 2022 private buyout, so the leaning is unassessed
rather than copied from The Straits Times. The recall audit already names sg_asiaone for oversampling, so
its items are now drawn more often. No ruleset, gate or category change; collection only.

## Registry: Australian Chinese-language outlets registered inactive (2026-10-12)

The Chinese language rule says outlets that publish primarily in Chinese are registered inactive with a
note so the exclusion is visible, but no Australian one was registered. Seven are now, all `active:
false` with the rule's standard inactive_reason: Sydney Today (今日悉尼), 1688.com.au and its Daily Chinese
Herald (澳洲日报), the Australian Chinese Daily (澳洲新報), Vision China Times (看中國), Australian Chinese
News (大华时代, Melbourne), ABC Chinese and SBS Chinese (the Mandarin service, which au_sbs's note already
said was excluded). Each note gives the source that confirms the outlet is Australian and writes in
Chinese. Only Vision China Times shows a feed; for the others the schema's one required URL is the
homepage or section page, and the note says so. The pages of Sydney Today, 1688 and the Australian
Chinese Daily answered a bot challenge, so their language was confirmed from published research rather
than the site. No article page was requested. Australia's active outlet count does not change (30), and
it now has 48 registered outlets; no ruleset, gate or category change.

## Registry: Le Monde, Novinky.cz and Seznam Zprávy article pages are unreadable by automated clients (2026-10-12)

Every stored Le Monde body is its 286 character "Client Challenge" bot page (103 of 103), and every
stored Novinky.cz and Seznam Zprávy body is the same 1,091 character Seznam consent wall ("Nastavení
souhlasu s personalizací", 61 of 61 each), all served with HTTP 200 in place of the article; ruleset
2026.10.1 now fails such bodies as unreadable. Le Monde's robots.txt allows the article paths for the
project user agent, but the comment at its head says robots and other automatic browsing of the site
are prohibited and asks for a licence, so no article was requested from the owner's machine and the
outlet was not moved to the self-hosted collector. Novinky.cz and Seznam Zprávy allow the paths; two
articles of each, requested once from the owner's machine with the project user agent, returned the same
wall, so the self-hosted collector would not help. No challenge is solved and no consent is accepted
automatically: each outlet's notes record the finding, the outlets stay active on the hosted collector
for their feeds, and licensed or publisher granted access is the only route to their article text. No
ruleset, gate or category change.

## Registry and site: international broadcasters, ownership sources and the US based leaning rater (2026-10-12)

Government funded broadcasters aimed at audiences abroad get their own ownership value,
state_funded_international, instead of counting as public service beside the BBC at home, ABC or CBC:
Radio Free Asia (five services), RFE/RL (Georgian, Armenian, Turkmen, Belarusian and Current Time),
Deutsche Welle (three feeds), RFI and France 24 (five feeds), the BBC World Service language services
(Brasil, Hindi, Persian, Burmese), KBS World and RNZ Pacific, 24 outlets, 19 of them active. Public
service goes from 75 to 56 active outlets. State run outlets without editorial independence, such as RT
and Sputnik, stay under state. The schema, the research merge, the export's ownership groups and the
"Who publishes it" chart (a fifth ownership colour, validated on adjacent pairs against the card
surface) carry the new group. Three ownership records now rest on primary sources. Radio Free Asia's
source is the U.S. Agency for Global Media's firewall page, which cites the statute (22 U.S.C. 6204(b))
protecting the professional independence of the networks it funds, instead of a US based rating site.
TRT World (inactive) is recorded as state: since 2018 TRT is governed by Presidential Decree No. 4, which
replaced the organ articles of Law 2954, the President appoints its board and director general, its
foreign broadcasts are tasked with promoting the state, and nothing protects its editorial decisions
from the government, whatever its legal label as an impartial public entity says. Radio Taiwan
International is recorded as state as well: its Establishment Act makes it a foundation budgeted and
supervised by the Ministry of Culture, which selects its board, and the Act has no editorial
independence provision, so it does not meet the definition of state_funded_international. Each entry's
note gives the reason. Media Bias/Fact Check, which rates on a United States spectrum, was the only
source for 239 leanings outside the United States (229 active). No documented rule asks for two sources,
so they are kept, but the export now marks them leaning_source_weak, the chart's table gives each leaning
group's count of them with a caution, and the note under the chart names where every leaning comes from.
A new optional leaning_second_source that is not the same rater lifts the mark. No ruleset, gate or
category change.

## Site: the combined state-linked measure and the per million people basis are removed (2026-10-12)

The State-linked articles measure added state origin, unchecked state sourcing and the candidates
carrying official Chinese sourcing that are still waiting for the model, so one figure mixed judged
counts with an unjudged queue whose size varies by country. It is gone from the Measure buttons above
the map, the theme counter and the leaning section, from the ranked list, the legend, the sparkline, the
worldwide line and the country panel, and from both CSV exports (the view export loses its target and
target_in_published_items columns, the daily export its target_in_published_items column). A stale
selection of it falls back to state origin. The waiting candidates stay visible only as their own
figure, labelled as not a finding: the country panel's verification pending row and outlet column, the
dotted line in the panel chart, the notes strip, official_sourcing_pending in the view export and
awaiting_model in the daily export. The data files are unchanged; the theme counts keep their combined
slot, which no measure reads. The method text already called articles per million residents the least
defensible denominator, and a figure that cannot be defended is not shown: the Per million people button
is gone from both Basis rows, above the map and above the ranked list, with its four metrics, its grid
entries, its legend unit, its not-on-the-scale line, the population line in the tooltip, the 100,000
resident floor and the worldwide stratum's "not applicable" legend line (docs/compute.js, docs/app.js,
docs/index.html). A saved or stale selection of it falls back to Number of articles in the same measure.
The method prose (docs/index.html, pipeline/methodology.py, README.md) says in one sentence that no per
capita figure is shown because population is a poor denominator for what outlets publish, and the
worldwide stratum note (docs/content.json) no longer mentions a per million value. Population stays in
the data files: sources/population.json's note, the Population figures row of the method page, the
denominator passages in the method page, METHODOLOGY.md's template and the README, and the comment over
the basis grid in compute.js now say that the export carries each country's resident population in
latest.json, that it reaches the page only in the population column of the CSV of the current view,
which keeps its place, and that no figure is divided by it. Three functions nothing called any more are
removed (relayStudyText and distributionName in docs/app.js, plantedPlural in docs/compute.js), and the
comment on the theme counts in pipeline/export.py describes the second slot as a combined count kept so
the file layout does not change. No ruleset, gate or category count change; METHODOLOGY.md picks up the
wording at the next export.

## Site: phone layout, complete days, whole-word quotes and labels in their own case (2026-10-12)

The site no longer scrolls sideways on a phone: example quotes show a bare link as its host
(globenewswire.com rather than a long attachment URL), and quotes, headlines, outlet names, notes and
tooltips wrap at 320 to 375 pixels, as do the example and theme card grids, the route picker, the
timeline controls, the theme counter's count line and the leaning table. The top country card, its share
and the findings sentence count the 30 complete days the map and ranked list open on, so on 2026-10-12
Italy reads 423 articles (32 percent of the world's state origin, 37 percent of the country outlets'
under that toggle) instead of 436, which had included the partial export day. The all time cards and the
"What we found" summary end on the same last complete day as the map's All time view (state origin 1,627
rather than 1,642 on 2026-10-12, matching the map under Both, Country outlets and Worldwide wires), the
map tooltip's top carrier and route count the same 30 complete days as the figure beside them, and
Articles screened is labelled as the collector's running total. Example quotes no longer start or end
inside a word ("ttend the summit", "una quo"): the export drops a word the rules window cut in half and
marks each cut with an ellipsis, cutting on characters in Chinese, Japanese and Thai, and the highlighted
phrase still lines up; the page no longer trims such a quote a second time, while quotes from older data
are trimmed as before. The rules store each matched span widened to the nearest whole word on both sides
(at most 20 more characters, and unchanged in Chinese, Japanese and Thai) with an ellipsis where text was
left out, so new quotes keep every word. The map legend printed the All China coverage unit as "china
articles per active monitored outlet", because it lowered the first letter of every unit, and the same
blanket lowering turned "Signed by a Chinese diplomat", the themes "Taiwan and Hong Kong", "Chinese
domestic politics" and "Belt and Road" and "% of its China coverage" into lower case mid-sentence. A new
helper, lowerFirst in docs/compute.js, lowers only the first letter of a sentence case label, and never
when the label opens with China, Chinese, a place such as Taiwan or Hong Kong, a country name or an
acronym; every mid-sentence label in docs/app.js goes through it, and tests/frontend_smoke.js checks the
unit for every measure and basis. The map's cap, the 95th percentile of every country's value on every
date of the timeline, and the theme counter's own cap are fixed across the dates shown but computed in
the browser from the published data, so each daily export recomputes them and a shade can stand for a
different number from one week to the next. The map key, both theme counter keys, the Color scale
paragraph and the methodology now say so in the same words, and the method text also says that past 60
days the cap is pooled over 60 evenly spaced dates. No cap is shipped in the export, since one computed
at export time from the same files would be the number the browser already computes; holding one cap
across updates is a baseline choice left to the owner. No figure or classification changes beyond the
complete-day windows.

## Site and methodology: themes versions marked (2026-10-12)

Themes versions now carry dates, as ruleset and gate changes do, and the theme timeline marks each one
with a green dash-dot line whose tooltip names the version and what changed. The history is kept next to
the code that reads it (HISTORY in pipeline/themes.py, exported as meta.themes_changes): 2026.09.3 on
2026-09-14, when the counter was introduced and read only the first 800 characters of each body, and
2026.09.4 on 2026-09-15, when it began reading the whole body. A version bump in themes.yaml without a
dated entry fails the tests. The themes paragraph in METHODOLOGY.md named only the current version;
pipeline/methodology.py now writes one sentence from meta.themes_changes listing each version with the
day it took effect and what changed, and says that every export re-tags each article whose tags were
made under another version, so the published series has no break at a version change and the timeline
marks record when the method changed. An export without themes_changes says that no earlier version is
recorded. No ruleset, gate, category or themes change.

## Documentation: README figures and stale references brought up to date (2026-10-12)

The README still gave figures from earlier days as if they were current. The registry expansion increment
now says its counts (1,469 active outlets in 233 countries, 10 Chinese-language exclusions) were true at
the time and adds the 12 October registry from pipeline.registry (2,128 outlets, 1,703 active across 235
countries and territories in 68 languages plus the two press release wires, about 310 inactive for
blocking the crawler, 19 Chinese-language outlets excluded, seven of them Australian, and 17 gap
entries), the last also stated under the Chinese language rule. The cost paragraph claimed a full 600
call day cost two to four dollars; it now describes the monthly budget (TRACKER_LLM_MONTHLY_BUDGET_USD,
110 dollars) and the second daily cap it sets, and the measured price of a call from recorded tokens,
about two cents at full price and one through the Batches API, at which the budget binds before the
ceiling (235 synchronous or 470 batched calls a day on 12 October). The theme counter reads term lists in
English plus eighteen other languages, not fifteen, follows the Distribution toggle as well as Measure
and Window, and leaves about one in ten China articles with no theme, not a quarter, mostly in
Bulgarian, Ukrainian and Greek, which have no list of their own. The ruleset carry forward takes up to
2,000 articles a run, not a few hundred. The registry unevenness paragraph lists the three measures and
four bases the site has after today's removals, names the Distribution toggle and gives the current
spread (44 outlets in the United States against a median of 6, 16 single-outlet countries). The workflow
variables list gains the monthly budget and session grace variables, the API key is marked as added since
the 2026-09-04 setup, the 900 pixel map breakpoint is marked as of the time (720 now), and AsiaOne is
described as registered the same day as the recall snapshot. The comments on the relay metric in
docs/compute.js and on RELAY_PROVISIONAL_DISPLAY in pipeline/config.py, and the README paragraph on
unchecked state sourcing, no longer say that any study clears or settles the counts: only hand coding
publishes them, a hand coding kappa or a current stability check below the threshold withholds them, and
a passing stability check leaves them provisional. The README gives the interface's names for the four
categories and says that unverified relay is the prompt's internal name. No ruleset, gate, category or
data change.

## Registry: wider Australian coverage (2026-10-12)

Australia goes from 20 to 30 active outlets and from 23 to 41 registered. Every candidate feed was
requested once with the project user agent through pipeline.feeds_util.fetch_feed, after the
robots.txt check and at most one request per domain every three seconds. Added and polled by the
hosted collector: The Spectator Australia, The Saturday Paper, Inside Story and six ACM regional
mastheads (Bendigo Advertiser, The Courier, The Border Mail, The Standard, The Daily Advertiser, The
Advocate). The Australian Financial Review is reactivated on /rss/feed.xml (its paywalled articles are
recorded as paywalled, as for any outlet), and ABC News gains its Just In feed. Registered inactive so
the gap is visible: The New Daily, InDaily, Michael West Media and East Asia Forum, whose feeds return
403 from the owner's machine as well, and news.com.au, Sky News Australia, The Daily Telegraph, The
Courier-Mail and The Advertiser, whose robots.txt (like The Australian's and the Herald Sun's, retested
the same day) disallows every crawler not on a named list, so they were not requested. AAP, 9News and
10 News publish no public feed found. No ruleset, gate or category change; collection only.

## Collector: five registered section feeds were desks or site-wide feeds (2026-10-12)

Five section feeds in sources/outlets.yaml were never sections. Index.hr's sport desk was registered as
sponsored because its header reads "Sport sponsored by" (3,650 items, 2 relevant). The other four are
site-wide feeds that a section page declared as its alternate: the Mirror's rss.xml as partner (3,070
items, 13 relevant), LIGA.net's /ua/all/rss.xml as press release (721, 9), Novaya Gazeta Europe's
/feed/rss as sponsored (498, 9) and Asia-Plus's asiaplus.news/feed/ as sponsored (398, 2). Nearly every item
from them carried a section arrival tag and covered every desk. They are removed, the outlets and their
editorial feeds stay, each outlet's notes say why, and release_sections keeps the section with feed null
and readable false. Discovery now reads only the words before a sponsor credit ("Sport sponsored by",
"Weather presented by") as the section name, never takes a feed whose path is only feed words and a
language edition (/rss.xml, /feed, /feed/rss, /ua/all/rss.xml, /rss) as a section's feed, and applies
both rules again to earlier probe results. Articles already collected keep their rows, tags and
verdicts. The registry's section feed total in the export falls from 136 to 131. No category, article
count, ruleset or gate change; collection only.

## Collector: section feeds that flood the queue lose the gate exemption (2026-10-12)

Press release, sponsored and partner section feeds skip the keyword gate because that is where
paid placements sit. Discovery sometimes registers a whole desk as a section (Index.hr's sport
page, whose header reads "Sport sponsored by", admitted 3,650 items in 30 days with 2 relevant;
ten such feeds were a sixth of all fetches). A section feed whose resolved items over the trailing
30 days exceed 500 with under 2 percent classified anything other than not relevant now has the
keyword gate applied again until it quietens or earns the exemption back (fetch_feeds
section_feed_flooding; the discover counts gain section_feeds_guarded). Pending items are never
read as irrelevance. No category, count, ruleset, gate or schema version change.

## Distribution toggle: country outlets and worldwide wires apart (2026-10-11)

The map card gains a Distribution toggle beside Measure, with Both (the default, today's figures),
Country outlets and Worldwide wires. It drives every figure that sums across countries: the map, the
ranked list and the legend line, the timeline sparkline, the "What we found" cards, the theme
counter's world totals, the panel's world table and both CSV exports, which gain a distribution
column. Under Country outlets the worldwide stratum leaves every sum; under Worldwide wires only it
remains, the map is left unfilled with a caption saying the wires are not drawn by country, and the
top country card becomes the wires' own 30 day count. Outlets monitored and the leaning chart count
newsrooms only and never include the wires. Opening a row the mode hides, from the search, the list
or a link, puts the toggle back to Both. The export writes, beside each world figure, the stratum's
own part: a per day `wld` block in global_series.json and `state_origin_wires`, `unchecked_wires`,
`independent_wires` and `china_total_wires` under meta.findings.all_time, plus a `last_30d` block
with the world's and the wires' state origin; files without them read as zero wires. Schema version
11. No count changes: interface and export fields only. No category, ruleset or gate change.

## Worldwide distribution stratum for the press release wires (2026-10-11)

Press release distribution wires (PR Newswire, GlobeNewswire) were counted on their own and appeared
in no total: in the last 30 days that left 179 state origin releases off the map and the country
list. They are now counted as a stratum of their own, "Worldwide distribution", under the code WLD in
latest.json, the daily files, outlets.json (the wires carry country WLD in the export only) and
articles/WLD.json. Country counts are unchanged: the stratum is never attributed to the United States
and never added into any country's numbers. World totals, the global series and the findings totals
now include the worldwide stratum; the top country card never names it. The site lists it in the
ranked list and the country search, adds one line under the map legend, and says in the state origin
card how many arrived through the wires. meta.distribution_wires keeps its old name and equals the
stratum. Schema version 10. No category, ruleset or gate change.

## Stress test before the 12 October presentation (2026-10-11)

Two audits of the 8 and 9 October changes, and the fixes. The recurring bylines diagnostic no longer
leaves the command line (`python -m pipeline.planted recurring`): on the site it listed syndicated
agency reporters under a heading about invented journalists, so planted.json drops it and the section
shows only matches against the published watchlist. The daily model cap no longer discounts batched
calls twice: llm_cost.measured_per_call now measures the full price of a call from the stored token
counts and the batch discount is applied once, and days with an uncollected batch, and today, are left
out of the measurement; the live cap of about 900 calls a day was roughly double what 110 dollars buys.
The persona matcher splits multi-author fields on punctuation and "and" before normalising, tries the
reversed "Last, First" order, strips honorifics, accepts a byline word only at the start of a line or
after "written", and matches front names as whole phrases, so "una declaracion de Alice Johnson" and
"National Social Research Center" no longer flag. The scan compiles one alternation for all front names
and domains, stops at the stage deadline and checkpoints every commit; load_personas validates types,
ids and operation references and warns instead of failing; the export never stops on a bad watchlist;
flags_total gains articles_total. On the site, each render step is isolated so one failure cannot skip
the rest or bind every control twice, a missing planted.json says so instead of reading as a finding,
the Start here and FAQ text describe the reliability check as done, and theme data tolerates nulls.
No category, count, ruleset, gate or schema version change.

## Planted bylines: matches against personas named in published investigations (2026-10-08)

A new side channel, pipeline/planted.py, reads sources/personas.yaml (personas and false front
organisations named in published investigations of planted contributions, each entry cited to a
public report) and flags articles whose author field equals a listed name, whose body opens with a
byline naming one, or whose title or body cites a listed front or one of its domains. Names are
compared after normalisation (casefold, diacritics stripped, punctuation dropped, middle initials
optional) and a single word never matches. The author field is checked on every discovered item,
including those the relevance gate rejected, because a planted byline need not be about China; a
flagged item is exempt from the gated out pruning so the row stays readable. Flags live in a new
planted_flags table, are never deleted, and are written at the end of the discover and fetch
stages and before the export; a failure there is recorded in the stage's counts and never stops
a run. The export writes docs/data/planted.json (operations, personas, fronts, flags newest
first and capped at 500, counts by country and outlet, and a diagnostic of bylines that recur
across at least three outlets and two countries in 30 days, with generic bylines, outlet and wire
names and pure wire copy left out) and meta.json gains a planted block. A flag means the byline
matches a persona named in a published investigation, cited there; the tracker cannot prove a
byline is fictitious, the recurring table is for human review, and absence of a match proves
nothing. No category, count or ruleset change: no flagged article is folded into state origin,
unchecked state sourcing or independent journalism, and no published figure moves. Schema
version 9 for the new file and meta block only.

## Model stage: budget sized for the API to judge every waiting article (2026-10-08)

The default monthly budget (TRACKER_LLM_MONTHLY_BUDGET_USD) rises from 30 to 110 dollars and the
session hold (TRACKER_SESSION_GRACE_DAYS) defaults to 0 instead of 3, so every article the rules
cannot settle is offered to the API on the day it is discovered. 110 dollars covers about 340 calls a
day at the measured cost of a call, the intake seen in September across both routes. The session
route stays as a fallback for when the budget is spent, and data/labels/api_first.jsonl still puts
the articles it cannot fetch first. The daily cap now divides the budget by the measured average
cost of a call over the current and previous month once at least 500 recorded calls exist
(llm_cost.measured_per_call), instead of always by the full price constant, which would have
allowed only about 200 calls a day; the cap reports per_call_measured. No ruleset, gate or schema
version change: what a label means is unchanged; only how many articles the API takes, and when.

## Model stage: API budget kept for what the session route cannot reach (2026-10-04)

Since 2026-09-30 waiting articles are also judged by the session route (pipeline/labels.py),
which refetches their text on the owner's machine at no API cost. Some sites refuse that fetch
(HTTP 403, robots.txt, paywall, gone), so those articles can only be judged by the API from the
runner's own copy of the text. The API budget is now kept for them. A new committed list,
data/labels/api_first.jsonl (one object per line: url_hash, reason, listed_at, source), names
them; pipeline.labels.load_api_first reads it, skipping and counting a malformed line, and the
labels ingest never reads it as a labels file. classify_llm.run now splits the waiting articles
after the near-duplicate collapse into two pools: the listed ones, drawn first, and the others
discovered more than LLM_SESSION_GRACE_DAYS days ago (env TRACKER_SESSION_GRACE_DAYS, default 3;
0 turns the hold off), drawn with what budget remains, each with the existing stratified draw.
Younger articles are held for the session and not offered to the API on that run; being held is
not a ceiling event. The stage's counts gain api_first_eligible, api_first_sent, grace_eligible,
grace_sent and held_for_session (sent is calls made, or requests submitted in batch mode).
With no list and every article past the grace period the stage draws exactly as before.
METHODOLOGY.md says so in the model stage section. No ruleset, gate or schema version change:
what a label means is unchanged; only which articles the API takes, and when, changes.

## Model stage: session labels imported by the runner (2026-09-30)

The monthly API budget ran out on 2026-09-16 and 4,585 articles are waiting for the verification
judgement. They may now be judged by the same model, with the identical system prompt and the
headline and body only (never the outlet, the country or the URL), through an interactive session
on the owner's machine at no API cost. The session's answers are committed as data/labels/*.jsonl,
one JSON object per line, and the new pipeline/labels.py imports them on the hosted runner at the
start of every run, right after the relay bundle (workflow step "Ingest session labels, if any").
A label is applied only to an article still waiting for the model with no current classification,
through classify_llm.store_result, the call the API path uses, so the label row, the route of a
state origin label, the article status and the near-duplicate copies come out exactly as after an
API reply. Everything else is counted and skipped, so the files can be ingested any number of
times without inserting anything twice; each ingest records a run_log row (stage labels_ingest).
These labels carry a model version that names the session, for example "claude-sonnet-5
(session)", keep their own JSON line as the raw response, count as model labels in every figure
and are sampled by the reliability study like any other. meta.json gains session_labels (total and
by model version), a part of llm_labels_total rather than an addition to it, and METHODOLOGY.md
says how these labels are produced. No ruleset or schema version change: what a label means is
unchanged, and the new meta key is additive.

## Export: atomic file writes (2026-09-30)

A local preview loaded docs/data/latest.json while an export was still writing it, got a
half-written file, and rendered the whole map as not monitored. pipeline/export.py now writes
every generated file (the docs/data JSON, the per-country article files and the audit jsonl
lines) to a temp file in the same directory and renames it into place, so a page loaded mid
export always reads either the old file or the new one, never a partial one. No label or figure
changes.

## Crawler: robots.txt read as RFC 9309 defines it (2026-09-28)

The weekly feed check on 2026-09-28 reported 47 feeds blocked by robots.txt. Thirteen of them,
at eight outlets (iDNES, Lidovky, Kloop, Kommersant and its English edition, Lenta, Sveriges
Radio and teleSUR), are in fact open to crawlers. urllib.robotparser applies the first matching
rule in file order where the standard applies the longest match, so "Disallow: /" followed by
"Allow: /rss.aspx" closed the feed the publisher had opened; it has no "*" or "$" wildcards; and
it drops a "?" from a rule, so "Disallow: /?" became "Disallow: /" and closed Lenta entirely. The
crawler also required both its own group and the "*" group to allow a URL, where the standard has
a group naming the crawler replace the "*" group. pipeline/robots.py now does all four the way the
standard says. The other 34 feeds really do disallow crawlers and stay blocked. All eight outlets
had stayed active in the registry, so their per outlet denominators counted outlets that were
contributing nothing; they contribute again from the next run. No label meaning changes.

## Ruleset 2026.09.9 (2026-09-25)

A budget diagnosis of September's model spend, run after the $30/month cap
was hit on 2026-09-16, traced most of the waste to two mechanisms rather than
to genuine judgement calls.

- classify_article no longer routes a gate-exempt section item (press_release,
  sponsored or partner; fetch_feeds.py skips the keyword gate for these by
  design) to the model on a weak signature alone. It now also requires the
  item to clear the same body-level China relevance test the residual C
  versus not_relevant rule already used (body_relevance, backed by
  gate.count). Strong signatures still give Category A outright, with no
  model call, exactly as before; an official sourcing trigger still routes to
  the model on its own. Of the 3,921 model calls made in September, 1,119
  (28.5%) carried a section tag, all reached by this same weak-signature
  path, and 1,059 of those (94.6%) came back not_relevant, 77.6% of every
  wasted call this period. Article bodies are not retained past
  classification, so whether the new test would actually have failed on each
  of those 1,059 could not be confirmed against September's stored data; on
  the working assumption behind this change, that a not_relevant model
  verdict on a section item usually means its body never substantively named
  China either, roughly 1,059 calls would have been avoided. The other 60
  section-tagged, weak-signature calls this period labelled A (59) or C (1);
  these are the change's exposure, an upper bound rather than a measured
  loss, since a genuine Chinese state placement names a China entity in its
  own body almost by definition and should still clear the relevance test and
  reach the model as before. Both figures come from the stored gate_terms and
  llm_trigger of every current September classification.
- Two exclusions (the existing signatures.yaml exclusions mechanism) strip
  recurring, content-independent site furniture that was extracted into the
  article body and read as a state-linked sponsorship signal: nst_recirc_boilerplate
  for New Straits Times (my_nst), where a "Branded Content" label inside a
  fixed five-line recirculation widget fired all five language variants of
  the sponsored_placement group at once on unrelated sports and entertainment
  stories, and bharian_nav_menu_boilerplate for Berita Harian (my_bharian),
  where the "Iklan Web" item in the site's own navigation menu fired the
  sponsored_asia weak signature the same way. In September, my_nst made 49
  model calls that carried only this span (48 not_relevant, 1 C) and
  my_bharian made 51 (47 not_relevant, 2 B, 2 C); both exclusions were built
  directly from those calls' stored llm_trigger spans. All 100 calls would
  now be kept out of the model, since neither outlet's items carried any
  other signature or trigger. The 5 that had reached B or C (1 at my_nst, 4
  at my_bharian) would be relabelled not_relevant by rule on the next
  reclassification; the model's own reasoning on all 5 already described the
  scraped body as site navigation with no actual article content, so this is
  a loss of a low-confidence default judgement, not of a substantiated label.
  Each regex matches the whole recurring block, not the bare disclosure word,
  so it does not touch a genuine per-article sponsorship disclosure. A third
  outlet named in the same diagnosis, Utusan Malaysia (my_utusan), was
  checked and excluded from this change: its wire_stamp_alone hits (58 of its
  59 September model calls) are each a distinct PRNewswire release from a
  different company in its press-release section, not a repeating span, so
  there is no literal boilerplate to exclude there; that outlet's waste is
  addressed by the section change above instead.
- Combined, the two changes would have kept roughly 1,159 of September's
  3,921 model calls (about 30%) out of the model: an estimated 1,059 from the
  section change plus a fully measured 100 from the two exclusions, against
  an upper bound of 65 at-risk labels (60 unmeasured from the section change,
  expected to be far fewer in practice, plus 5 measured B or C labels from
  the exclusions).

## Ruleset 2026.09.8 (2026-09-22)

An audit of the whole instrument found that most state origin labels rested on
patterns that could not carry them, and that the labels were concentrated in a
handful of outlets because of how those patterns were written rather than
because of anything in the world. Relabelling changes historical numbers.

- The three distribution stamp patterns paired the carrier's own name with any
  state media mention within 3,000 characters, across paragraphs. A carrier
  names itself in every one of its own datelines, so the test collapsed to
  "does this outlet mention Chinese state media at all". An agency report about
  Chinese disinformation was labelled state origin. The state entity must now
  stand where a credit stands: in the release's own issuer line within 150
  characters of the wire dash, in a source trailer anchored to the start of a
  line, or in a slash credit inside the stamp. A new pattern keeps the genuine
  case where the release headline names the issuer above the wire dateline.
  Measured before the change: 231 of Italy's 242 labels and 118 of the United
  States' 122 came through these patterns from one carrier each.
- china_media_group_credit no longer accepts the bare acronym. CMG is the
  Toronto ticker of Computer Modelling Group Ltd, whose buyback releases carry
  "news release" within a few words of it, and three Canadian articles about
  that company were labelled Chinese state origin, 19 percent of the country's
  count.
- The state entity test that lets two weak signals become a label no longer
  accepts topic words. Silk Road, Belt and Road, Chinese government, State
  Council and Information Office are subjects, not issuers, and a travel
  advertorial naming the Silk Road plus two sponsorship phrases produced a
  confidence 1.0 label with no model call.
- Native script credit lines added for Greek, Hebrew, Persian, Turkish,
  Vietnamese, Russian, Arabic, Korean, Japanese and Chinese. The ruleset carried
  58 Latin spellings of Xinhua and none in the first five, against 12 active
  Greek outlets, 11 Persian and 10 Hebrew, so state origin was structurally
  undetectable outside Latin script and every comparison between countries was
  confounded with script. Each name counts only where a credit stands: after a
  source marker, alone in the author field, alone on a closing line, or in a
  wire dateline. A bare mention in the body is a citation and goes to the model,
  as the Latin spellings already did.
- A native script photo credit exclusion, so a picture credit is not read as a
  text credit in those scripts.

## Gate 2026.09.7 (2026-09-22)

The gate decides what enters the corpus, not how an article is labelled, and
gate decisions are recorded per item and never revisited, so this applies only
to items discovered after today.

- Korean is now matched as a substring, like the other languages that attach
  grammar to the noun. Korean particles join the noun with no space, so a word
  boundary missed every inflected mention: the bare form matched while "of
  China", "China and Taiwan" and "in Beijing" did not. Korean is the third
  largest source of unchecked state sourcing labels, so those counts were a
  floor. Two Korean terms are now corroborating rather than admitting, because
  they are homographs: Xinjiang is also the word for a kidney, and the term for
  the Communist Party sits inside the word for heavy industry.
- Latin script terms keep their word boundary and their case rule in every
  language, including the ones matched as substrings. They did not before, so
  "Xi" matched inside "Taxi", "BRI" inside "British" and "PLA" inside "display"
  in Thai, Japanese, Lao, Khmer and Burmese items.
- Croatian, Serbian and Bosnian "Kina", "Kine" and "Kini" now require a capital
  first letter. They are China and also the genitive and plural of the word for
  a cinema, so lowercase forms admitted film listings: 86 percent of the
  Croatian corpus turned out not relevant. An all capitals headline still
  matches, and the country is a proper noun, so real uses are unaffected.
- The residual relevance rule counts one stretch of text once. The term list
  overlaps itself, so "Xi Jinping" counted as both "Xi" and "Xi Jinping" and a
  headline naming one Chinese person cleared the "a term in the headline and at
  least two occurrences" branch on its own, and was published as independent
  journalism.

## Data schema 8 (2026-09-28)

Three new generated pieces for a front end aimed at an educated newcomer. No
label changes, and every figure below reuses the existing rollups and
definitions: discovery day attribution, distribution wires excluded from
every country figure, paywalled items uncounted, unchecked state sourcing
gated by relay_visible and relay_provisional exactly as before.

- meta.findings, a small summary the front end turns into a "what we found"
  box: since when the data runs, the all time state origin, unchecked state
  sourcing and independent totals with the state share of China coverage, the
  country carrying the most state origin in the last 30 days with the outlet
  and route that carried most of it, and state origin output by the
  publishing outlet's ownership, all time and active outlets only. Unchecked
  state sourcing is null in this box whenever it may not be shown, the same
  gate the rest of the interface already applies.
- docs/data/examples.json, up to six recent worked examples each for state
  origin and, when it may be shown, unchecked state sourcing: headline,
  outlet, country, url, discovery date, route, and the short matched span
  that produced the label, cut to at most 25 words, with the character
  offsets of the matched phrase inside it so the front end can highlight it.
  Examples are varied by country and outlet, most recent first, current
  ruleset labels only, distribution wires excluded.
- latest.json carries top_carrier and top_route per country for the last 30
  days of state origin, so a map tooltip can say what carried the country's
  number.

## Data schema 7 (2026-09-22)

Changes to what is counted and where, from the same audit. Anyone comparing
figures across this date needs all of these.

- Every figure is now attributed to the UTC day the item was discovered.
  Numerators used the published date where a feed gave one while every
  denominator used discovery, so shares were computed against a denominator
  that did not contain the same articles: on 1,033 of 2,857 country days the
  numerator exceeded its own denominator. It also manufactured 14 days of
  history before collection began, a published timeline starting 2026-08-20
  with real article counts and no discovery at all, which read as a ramp up
  that never happened. Published dates are kept on every article record.
- Global press release distribution services are a new registry tier and are
  counted as their own stratum, never inside a country. A worldwide release
  service is not a national newsroom and its releases are not placements in the
  market it is registered in. This moves 122 state origin labels out of the
  United States, whose count goes from 122 to 0, and the world total from 658
  to 536. The releases are still collected, classified and reported, under
  distribution_wires in meta.json.
- State origin with no signature at all is reported as its own stratum rather
  than as a row in the route table. It is the model asserting state origin
  where the deterministic layer found nothing, which is a different kind of
  evidence. It was 37 percent of the total.
- Figures containing unchecked state sourcing are omitted from latest.json when
  that label may not be shown, because a share published beside the state origin
  count and the China total gives the withheld number back by subtraction.
- A country whose unjudged backlog rivals its judged articles carries a warning
  that it is not comparable with countries whose backlog is smaller.
- The audit trail is one file per day instead of one per month. A month in one
  file meant a fresh 39.5 MB blob in every export commit, twice a day.
- METHODOLOGY.md no longer claims the counts rest on hand coding when the study
  behind them is a model rerun, and no longer claims the recorded sampling
  fractions can be used to reweight counts. They cannot.

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
