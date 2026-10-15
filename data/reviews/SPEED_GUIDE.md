# Republication speed: hand check guide

This guide is for the person who hand codes the sample that decides whether the republication speed
measure is shown as checked. Until a check passes, the site marks it provisional.

## What is being checked

The measure pairs a state origin copy in a monitored outlet with the state outlet's own page (a
reference original), reads the publication time each page gives, and puts the gap into a bucket.
The check asks one question: reading the times off the two pages yourself, do you get the same
bucket? It passes when at least 85 percent of the coded pairs agree.

You are not judging whether the pair is right (the alteration score's check does that), only the
times.

## The sheet

Draw it with `python -m pipeline.measures sample speed --n 60`. It writes
`data/reviews/speed_sample_<date>.csv` and a `.key.json` beside it.

- Code blind. Do not open the `.key.json`, and do not look the pair up on the tracker's site or in its
  data files.
- Each row has the copy's link (`copy_url`) and the original's link (`original_url`). Open both.
- Fill four cells per row and leave the rest as they are:

| Cell | What to enter |
|---|---|
| `copy_time_shown` | the publication time the copy's page shows |
| `copy_zone_shown` | the zone that time is in |
| `original_time_shown` | the publication time the original's page shows |
| `original_zone_shown` | the zone that time is in |
| `note` | anything odd, in a few words |

## How to write a time

- A time of day: `YYYY-MM-DD HH:MM`, on a 24 hour clock, exactly as the page shows it. Do not convert
  it to another zone yourself; the scorer does that from the zone you give.
- A date with no time of day: `YYYY-MM-DD`.
- No publication time or date anywhere on the page: `none`.
- A page that is gone, paywalled or not the article: `none`, and say why in `note`.

## How to write a zone

- An offset as the page gives it: `+08:00`, `UTC+8`, `GMT-5`.
- Or one of these names: `UTC`, `GMT`, `BJT` (Beijing), `CET`, `CEST`, `BST`, `EET`, `EEST`, `MSK`,
  `IST` (India), `WIB`, `SGT`, `HKT`, `PHT`, `JST`, `KST`, `AEST`, `AEDT`, `EST`, `EDT`, `PST`, `PDT`,
  `ART`, `BRT`. `CST` is read as China Standard Time.
- If the page shows a time but no zone, and nothing on the page states one, write `unknown`. The
  time then counts as its date only. Do not guess the zone from the outlet's country.

## Which time to take

- Take the time labelled as publication ("Published", "Pubblicato", a dateline time). If the page
  shows both a publication and an update time, take the publication time and write `updated shown` in
  `note`.
- If the page shows only an update time ("Updated", "Aggiornato", "Last modified"), take it, and write
  `update time only` in `note`. Those pages are a known weakness of the measure, and the notes tell
  us how often it bites.
- Read the time in the page itself, not in the browser tab, a share widget or a related links list.
- Xinhua and People's Daily usually show Beijing time; China Daily shows it with a +08:00 offset;
  CGTN and Global Times show their own labels. Write what the page says.

## The buckets the scorer uses

You do not need to bucket anything, but this is what your times are turned into:

- Both times with a time of day and a zone: the gap in hours. Same hour (under 1 h, and a copy up to
  an hour ahead counts here too), same day (1 to 24 h), 1 to 3 days (24 to 72 h), later.
- Either side a date only (or zone `unknown`): the gap in calendar days. Same date, 1 to 3 days,
  later.
- A copy more than an hour (or a calendar day) ahead of its original: copy ahead of its original.
- Either side `none`: no credible time.

A row counts as coded once both time cells hold a time, a date or `none`. A row with a time the
scorer cannot read counts as not coded, so the check stays incomplete until it is fixed.

## When you finish

Save the CSV in place, keeping the header and the `pair` column unchanged, and commit it with its key.
The next export scores it. If the measure's version changes before then, the sample is stale and a
new one is needed.
