# Meta Content Library query log

One line per query whose counts are committed in this folder. The engagement import keeps
mcl_query_ref and the file's sha256 on every row, so each figure on the site can be traced to a line
here. Record only what is needed to repeat the query: never a post, an account, a handle, a link to a
platform or a time of day.

Minimum cell in force: 5 (ENGAGEMENT_MIN_CELL). Write the stricter value here if Meta's output review
rules required one.

| mcl_query_ref | query_date | platform | key_type | period | urls file | counts file | rows | suppressed | output review | notes |
|---|---|---|---|---|---|---|---|---|---|---|
| Q2026 10 31 01 (example, delete) | 2026-10-31 | facebook | url_hash | 2026-10-01 to 2026-10-31 | urls_2026-10-01_2026-10-31.csv | counts_2026-10.csv | 0 | 0 | checked against Meta's rules on 2026-10-31 | search by link, public posts only |
