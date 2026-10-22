# Hand coding guide

This guide is for the people who hand code the fixed sample that decides whether unchecked state
sourcing counts are published. Until this study passes, those counts are shown as provisional.

**Do not start on `human_sample_2026-10-12.csv`.** It was superseded on 17 October 2026, before
anyone coded it: every one of its 1,400 labels was made under a classifier prompt older than the
current one (786 by the API and 614 in a session, none carrying a prompt tag), so a study on it
would check labels the runner is replacing. Its key says so (`"superseded"`), and compute will not
record a study on it. Wait for the owner to draw the new sample (see "When to redraw" below).

## The authority

The codebook is the category definitions in `pipeline/classify_llm.py`: `CATEGORY_DEFINITIONS`,
and the decision rules and worked examples in `SYSTEM_PROMPT` that follow them. That is exactly
what the model reads. Read it in full before you start, and again whenever the ruleset version in
`pipeline/config.py` (`RULESET_VERSION`) changes. If anything below disagrees with the prompt, the
prompt wins: this guide only summarises the order in which to decide.

## Before you start

- Code blind. The sheet carries no model label. Do not open the `.key.json` file beside it, and do
  not look the article up on the tracker's site, in its data files or in the database.
- Code alone. Do not discuss a row with another coder until the whole sample is coded.
- Read the article at the link, all of it. If the page is gone, paywalled, or plainly not the
  article the headline describes, leave `human_category` blank and say why in `note`
  (for example `unreadable: page removed`). Do not guess.

## Deciding, in order

1. Does the article substantively concern China? A passing mention does not count. If not:
   `not_about_china`.
2. Were the words written or commissioned by a Chinese state entity and run without substantive
   alteration? A Xinhua, CGTN or China Daily credit or dateline, a placement commissioned by a
   Chinese state entity, a signed piece by a Chinese diplomat. If so: `state_origin`. An article
   about state media, or one that quotes it, is not state origin.
3. Otherwise the local outlet wrote it. Is its central content a factual claim (what happened,
   how many, who did what) whose only authority is an official Chinese source or state media,
   treated as established, with nothing that independently confirms, contextualises or contests
   it? If so: `unchecked_state_sourcing`.
4. A stated position, policy, warning, threat, opinion or denial is not a factual claim. An
   article that reports what Beijing says it wants, intends, condemns or rejects is reporting the
   statement, and is `independent_journalism`, with or without outside comment. So is reporting
   that confirms, contextualises or contests the official claim.
5. Ignore tone, stance and topic: a piece hostile to China can be unchecked state sourcing, and a
   sympathetic one can be independent journalism. When you are genuinely torn between steps 3 and
   4, choose `independent_journalism`, the same rule the model follows.

## Filling the sheet

The sheet is `human_sample_<date>.csv`, one row per article. Fill three columns and touch no other:

| Column | What to write |
| --- | --- |
| `human_category` | one of `state_origin`, `unchecked_state_sourcing`, `independent_journalism`, `not_about_china`, or blank with a reason in `note` |
| `coder` | your pseudonymous coder id, such as `c1`, `c2` or `c3`, the same on every row you code |
| `note` | optional, except on a blank row; one line |

The sheet is committed to a public repository, so never write a name or initials in it. The
owner gives each coder an id (one to four letters then one to three digits) and keeps the list of
who holds which id off the repository; compute records nothing while a `coder` cell holds anything
else.

Do not add, delete, reorder or duplicate rows, and do not change `article_id`. Save as UTF-8 CSV.
In a spreadsheet, import the file as UTF-8 so headlines in Arabic, Korean and other scripts
survive. The owner assigns rows to coders by language; each row is coded by one person.

## The sample

`python -m pipeline.agreement sample` draws it from the model labelled articles with an extracted
body: about 200 articles for each language with at least 300 model labelled articles, stratified by
language and, within each language, spread evenly across the model's current categories, with a
fixed seed, so the same database gives the same draw. The stratification puts unchecked state
sourcing into the sample in numbers that can test it; the kappa therefore describes this sample,
not the proportions of the whole dataset. The sample drawn on 12 October 2026 holds 1,400 articles,
200 each in English, Spanish, Malay, Korean, Arabic, French and Portuguese. In Malay the model
had labelled only 5 articles as unchecked state sourcing, fewer than the 10 a kappa needs, so no
Malay figure on that distinction can come out of this sample; Malay rests on the overall one.

That sample is superseded (above) and is kept only for the record.

The key file freezes, for each article, the model label current when the sample was drawn (as a
row id, never as a category), and the classifier prompt then current (`prompt_version`), which is
the codebook the coders apply. compute does not compare against the frozen label by default, though:
see below.

## When to redraw

Draw a new sample whenever the classifier prompt (`PROMPT_VERSION` in `pipeline/classify_llm.py`)
changes what a label means, before anyone starts coding the old one, and once the runner has judged
the older labels again under the new prompt (the export's `labels_on_older_prompt` is at or near
zero). A sample drawn while that relabel is still running draws only from new articles and from the
labels judged again first, which are the unchecked state sourcing ones. Draw it with

    scripts/db_pull.sh
    python -m pipeline.agreement sample --since-prompt

`--since-prompt` alone keeps only labels made under the current prompt; `--since-prompt 2026-10-17`
names one. It must run on the runner's snapshot, which `scripts/db_pull.sh` fetches: a local
database holds labels the runner has since replaced, so the command refuses one whose newest model
label is more than two days old, and it never overwrites an existing sample. Then mark the sample
it replaces as superseded in that sample's key, as the 12 October one is, and tell the coders.

## How the result is computed and recorded

Run, against the database the daily update uses:

    python -m pipeline.agreement compute --csv data/reviews/human_sample_<date>.csv --dry-run
    python -m pipeline.agreement compute --csv data/reviews/human_sample_<date>.csv

compute compares each coded article with its model label current at the time compute runs, and
only when that label was made under the current classifier prompt: those are the labels the site
counts. An article whose current label is on an older prompt, or is no longer the model's, is left
out of the comparison; compute prints how many, and the study records and publishes the number
(`excluded_older_prompt`, `excluded_no_model_label`). `--against frozen` compares with the labels
frozen in the key instead, for the audit; it prints the figures and records nothing.

`--dry-run` prints the figures and records nothing; use it to watch progress. Without it, compute
records, once every row is coded or blank with a reason:

- one `human_reviews` row per coded article, with the `coder` column as reviewer;
- one `agreement_studies` row: sample size, Cohen's kappa across all four categories, kappa on
  unchecked state sourcing versus independent journalism, and in its details the same comparison
  per language (`bc_by_language`), kappa for each category against the rest, the coders, and
  whether the sample was finished.

An unfinished sample is not recorded unless `--partial` is given, and a partial study never
publishes anything. Nothing is recorded from a superseded sample, or while a `coder` cell holds
anything but a pseudonymous id.

The next export reads the newest `agreement_studies` row. Unchecked state sourcing is published
when kappa on unchecked state sourcing versus independent journalism is at least 0.7
(`config.KAPPA_WARNING_THRESHOLD`), with the coders and the model each placing at least 10
articles in unchecked state sourcing. A language with at least 20 articles in that comparison and
a kappa below 0.7 is withheld on its own. Below 0.7 overall, the counts are withheld everywhere.

A kappa needs the coders and the model each to place at least 10 articles in unchecked state
sourcing. When the model placed at least 10 there and the coders fewer than 10, the study has
failed, not come out inconclusive:
the model finds unchecked state sourcing that people reading the same articles do not, which is
the error this study exists to catch. Overall that withholds the counts everywhere; in one language
it withholds that language, whatever its size. When the model itself placed fewer than 10 there,
the study is inconclusive and the counts stay provisional.

A study counts for the prompt it was computed under and no other. Once the classifier prompt
changes, a study that passed stops counting as passed and the counts are provisional again until a
new sample is coded; a study that failed keeps withholding until a new one reports. The model's
rerun of its own sample (`pipeline/second_rater.py`) is a stability check and never takes the
place of this study.
