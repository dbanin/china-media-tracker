# Alteration score: hand check guide

This guide is for coding a sample drawn by `python -m pipeline.measures sample alteration --n 80`
(20 pairs per band). The sample is `data/reviews/alteration_sample_<date>.csv`; its answers are in
the `.key.json` beside it. Do not open the key while coding. The measure is report only: nothing you
code changes a label or a count.

Each row is a pair: a state origin copy published by a monitored outlet, and the original the tracker
paired it with (an item from a reference source, such as Xinhua English, or an earlier monitored copy
of the same item). The CSV shows the two headlines and links, never the tracker's band or score.

## Two kinds of original

`original_source` says which kind the row has:
- a `ref_` source (`ref_xinhua_en`, `ref_cgtn_en` and so on) is the state outlet's own page: the state
  text itself;
- an outlet id is an earlier copy of the same item at another monitored outlet. It is not the state
  text, so the band you give says how far one outlet's copy differs from another's. Code it the same
  way; the site reports the two kinds apart.

The tracker never takes an earlier copy at a sister outlet of the same media group (two ACM regional
papers, two Postmedia papers, two Nine mastheads) as the original, since sister papers share one copy
pool and the same placement. If a row pairs two outlets you know belong to one group, code it as usual
and write `sister outlet` in notes, so the group can be added to the registry.

## For each row

Open both links. Read the copy's article text and the original's article text. Ignore menus, related
links, photo captions, credit lines and datelines on both pages.

**same_text**: `yes` or `no`.
- `yes` when the original is the text the copy reproduces, in whole or in part, including when the copy
  cut it, added to it or rewrote some of it.
- `no` when it is a different article: another story on the same event, an earlier or later update with
  different text, or a different press release from the same issuer.
- If either page no longer loads, write `no` and say so in notes. A pair that cannot be judged is not a
  correct pair.

**coder_band** (only when same_text is yes): judge how many of the copy's words are not in the original,
counting rewritten sentences and added paragraphs, and ignoring credit lines and datelines.

| Band | Words in the copy that are not in the original |
|---|---|
| verbatim | under 3 percent: the same text, perhaps a word or two changed |
| light | 3 to 15 percent: a sentence rewritten, a short local line added |
| moderate | 15 to 40 percent: several paragraphs rewritten or added |
| substantial | 40 percent or more: much of the copy is the outlet's own text |

Cuts do not count toward the band: a copy that runs only the first three paragraphs of the original,
word for word, is verbatim. A changed headline does not count either. Estimate by eye; there is no need
to count words exactly.

**notes**: anything that made the call hard (a translation, a page that changed, a live blog, a sister
outlet).

## Scoring

The check passes when pair precision (yes among coded rows) is at least 0.9 and the quadratic weighted
kappa between your bands and the tracker's bands, on the pairs you coded yes, is at least 0.6. The
export scores the newest sample whose key matches the current method version; while rows are blank the
measure stays provisional, and if a completed check fails the figures are withheld until a new one
passes.
