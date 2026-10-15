# Coding guide: unusual days (spikes)

The hand check of the unusual days measure (pipeline/spikes.py). Draw a sample with

    .venv/bin/python -m pipeline.measures sample spikes --seed <seed>

which writes `data/reviews/spikes_sample_<date>.csv` and its `.key.json`. The sample holds every flagged
day, up to 30 (a seeded random draw when there are more). Code the CSV only and do not open the key: it
holds the status and the confounders the export gave each day, and the check is blind to them.

## What each row is

One day on which one country, or the worldwide press release wires (WLD), carried far more state origin
placements than in its previous 28 complete days. The row gives the day, the country, the number of
placements, the number expected, the distinct underlying items, the outlets behind it, the top items and
up to 10 article links. The site shows the same day: select the country on the map and move the timeline
to that day, and the country panel lists its articles.

## What to judge

Fill `judgement` with one of

- `real`: a genuine surge of state text on that day. More state origin items really were placed in that
  country's monitored outlets than usual: a state visit, a summit, a sponsored supplement, a batch of wire
  copy or press releases that ran that day. It counts as real even when it is one outlet or one item
  placed many times, as long as the placements happened on that day and are state origin.
- `artefact`: the surge comes from how the data was collected or labelled, not from what was published.

For an artefact, fill `artefact_type` with the cause you found:

| artefact_type | what it looks like |
|---|---|
| new_outlet | the outlet behind most placements had just been added, so it had no history to compare with |
| backfill | the items were read back from an archive, published on the day but found much later |
| outage_recovery | a collector missed the days before and caught up: items published over several days found at once |
| relay_incomplete | the collector on the owner's machine missed the day before and delivered its items late |
| ruleset_change | the day's labels and the earlier days' labels were made under different rules |
| gate_change | a relevance gate change let in items that the earlier days would not have counted |
| saturation | the earlier days lost items between feed polls, so the comparison is too low |
| llm_ceiling | the earlier days' candidates still wait for the model, so their counts are too low |
| denominator_spike | everything collected surged that day, not state text in particular |
| duplicate_items | the same article counted several times (one URL under several addresses, a mirror) |
| misdated | the items were published on another day and only counted on this one |
| mislabelled | the items are not state origin |
| other | anything else; say what in `notes` |

Leave `artefact_type` empty for a real surge. Use `notes` for anything that helped decide, such as the
event behind a real surge.

## How to check a row

1. Open a handful of the links, the top items first. Is each one state origin text (a credited wire,
   a distribution stamp, a sponsored or partner disclosure, a diplomat's byline, or a copy of one)?
   If most are not, it is `mislabelled`.
2. Look at the publication dates on the pages. If most were published days before, it is `misdated`,
   `backfill` or `outage_recovery`.
3. Look at the outlets. If the main one has only a few days of history on the site, it is `new_outlet`.
4. If the items are genuine and dated on the day, ask whether something happened: a visit, a summit, a
   campaign, a supplement. A surge with no event can still be real (a batch of releases), so judge the
   placements, not the news value.

When unsure between real and artefact, write `artefact` with type `other` and explain in `notes`: the
check measures precision, so doubt counts against the measure, not for it.

## Scoring

The check passes when at least 80 percent of the coded days are judged `real` (precision of at least
0.8), once every row is coded. The score also reports the precision among the days the export showed as
clean, and how often an artefact's type matches one of the confounders the export gave it, which says
whether the confounder rules catch what a person finds.
