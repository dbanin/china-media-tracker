# Engagement counts

This folder holds the aggregate counts behind the engagement measure (pipeline/engagement.py), which
asks how far state origin articles travel on public Facebook and Instagram posts. It measures reach,
not supply. The measure ships switched off (`enabled: false` in sources/measures.yaml) and the site
section stays hidden until it is switched on and rows exist.

No code in this repository touches Meta, Facebook, Instagram, a login, a token, a credential or an API.
You look the addresses up yourself in the Meta Content Library and bring back aggregate counts only.

## Steps

1. Write the list of addresses to look up:

       python -m pipeline.engagement urls --since 2026-10-01 --until 2026-10-31

   This writes `data/engagement/urls_<since>_<until>.csv` with article_id, url_hash, url, domain,
   outlet_id, country (as the export counts it, release wires under WLD), route, date and headline: the
   current state origin articles of those days, public addresses only. It is a working file; it holds
   nothing that is not already public, but it does not need to be committed.

2. Look the addresses up in the Meta Content Library, inside Meta's own environment, under your own
   access. Count per article (key_type `url_hash`, key = the url_hash column) or per outlet and month
   (key_type `outlet_month`, key = `<outlet_id>:<YYYY-MM>`), and per platform.

3. Before anything leaves Meta's environment, check Meta's own output review rules for the Content
   Library: what may be exported, the minimum cell sizes they require, any review or approval step, and
   whether the counts may be published. The checks below are a floor that this repository enforces; they
   do not replace Meta's rules, and where Meta's are stricter, Meta's apply (raise the minimum cell with
   `--min-cell`, and keep the stricter value in QUERY_LOG.md).

4. Fill a counts file whose header is exactly these columns (views may be left out; no other column is
   accepted; sources/engagement_schema.json is the schema):

       key_type,key,platform,period_start,period_end,posts_n,reactions,shares,comments,views,suppressed,query_date,mcl_query_ref

   platform is facebook or instagram. Periods and the query date are days, YYYY-MM-DD, never a time.
   posts_n is the number of public posts that link to the address; reactions, shares, comments and views
   are summed over those posts. When posts_n is from 1 to 4 (the minimum cell, ENGAGEMENT_MIN_CELL, is
   5), set suppressed to 1 and leave every count blank. mcl_query_ref is your own reference for the
   query, letters, digits, spaces and `_ . : #` only, the same one you write in QUERY_LOG.md.

5. Validate, then import into your local database to see it:

       python -m pipeline.engagement validate data/engagement/counts_2026-10.csv
       python -m pipeline.engagement import data/engagement/counts_2026-10.csv

   The validator refuses the whole file, with a line by line report, if any row has an extra column, a
   link (facebook.com, instagram.com, fb.com and the like, or any address), an @ handle, a digit string
   longer than 15 (a post or account id), a time finer than a day, text over 64 characters, or a small
   cell (posts_n from 1 to 4 without suppressed set). The import is idempotent and keeps the sha256 of
   the file on every row; a later file that gives new figures for the same cell replaces them.

6. Commit the coded file here as `counts_<range>.csv`, with its line in QUERY_LOG.md. The runner's
   database is the canonical one, so a local import alone never reaches the site: the export imports
   every committed `counts_*.csv` in this folder, in name order, each time it runs. When correcting a
   file, replace it rather than adding a second one beside it.

7. To publish, set `enabled: true` for engagement in sources/measures.yaml. The section then shows,
   marked provisional, until a hand check passes.

## Hand check

    python -m pipeline.measures sample engagement --n 20

This draws up to 20 stored cells into data/reviews/engagement_sample_<date>.csv, showing the address and
the period but never the stored figures. Look each one up again and write the band of posts found in
posts_band: none, few (1 to 4), 5_19, 20_99 or 100_plus. Bands are compared rather than exact counts
because posts keep arriving after the first query. The check passes at 80 percent agreement.
