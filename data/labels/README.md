# Session labels

Verification judgements given by the classifier model outside the API. When the monthly API
budget is spent, articles waiting for the model can be judged by the same model with the
identical system prompt (pipeline/classify_llm.py, SYSTEM_PROMPT) in an interactive session on the
owner's machine, at no API cost. As on the API path, the model sees the headline and the body
only, never the outlet, the country or the URL.

Each `*.jsonl` file here holds one JSON object per line, every field required:

```
{"url_hash": "3f2a...", "category": "B", "confidence": 0.82,
 "evidence_quote": "...", "reasoning": "...", "china_sources_cited": ["Xinhua"],
 "independent_confirmation_present": false, "confirmation_evidence": null,
 "model_version": "claude-sonnet-5 (session)", "labelled_at": "2026-09-30T10:00:00Z",
 "source": "session"}
```

(shown wrapped here; in a file each object is on a single line)

- `category` is one of `A` (state origin), `B` (unchecked state sourcing), `C` (independent
  journalism) or `not_relevant`; `confidence` is a number from 0 to 1.
- `model_version` must name the session, so these labels stay distinguishable from API labels.
- `labelled_at` is an ISO date or datetime.

The hosted runner imports these files at the start of every run (`python -m pipeline.labels
ingest`). A label is applied only to an article still waiting for the model and with no current
classification, and it is stored exactly as an API reply would be. Everything else is counted and
skipped, so the files are applied once and can be ingested any number of times without inserting
anything twice. A malformed line is counted and skipped, never fatal. Keep the files committed:
they are the record of where these labels came from.

## api_first.jsonl

Not a labels file, and never ingested as one. It lists the waiting articles whose text the session
could not fetch on the owner's machine (HTTP 403, robots.txt, paywall, gone), so only the API,
which has the runner's own copy of the text, can judge them. One JSON object per line:

```
{"url_hash": "3f2a...", "reason": "HTTP 403", "listed_at": "2026-10-04", "source": "session"}
```

The model stage draws these articles first, then the other waiting articles. With
`TRACKER_SESSION_GRACE_DAYS` above 0 the second group is limited to articles discovered more than
that many days ago and younger ones are held for the session; since 8 October the default is 0, the
monthly budget is sized for the API to take everything, and the session route is a fallback for
when the budget is spent. A malformed line is counted and skipped, never fatal; a missing file is
an empty list.

## Check against the API, 2026-09-30

Before the first file was committed, 98 articles that already carried an API label were judged
again in the session, from text fetched again on the owner's machine. The two agreed on 78 of 98
(0.80), Cohen's kappa 0.71. The main difference runs one way: of 28 articles the API called
unchecked state sourcing, the session called 12 independent journalism, and the reverse happened
twice. Session labels therefore undercount unchecked state sourcing relative to the API, and that
category stays provisional. State origin agreed on 5 of 7; the other 2 were wire-credited items
that are not about China, which the session called not relevant.

## session-2026-09-30.jsonl, final contents

4,355 labels: 301 state origin, 494 unchecked state sourcing, 2,015 independent journalism and
1,545 not relevant. They cover the articles that were waiting for the model on 2026-09-30 and
whose text could be fetched again on the owner's machine; 232 of the 4,585 could not (refused,
gone or paywalled) and stay in the API queue. Every label's evidence quote was checked against its
own article text (4,450 of 4,453 found verbatim, counting the control articles).

Known weakness: press releases from Chinese companies on the distribution wires. The codebook does
not say whether a state-owned enterprise's commercial release is state origin, and the labels are
not consistent on it: some are state origin at low confidence, others not relevant. 55 of the 301
state origin labels carry a confidence under 0.6, and most of those are such releases. The
distribution wires are outside every country figure, so the map is not affected; the wires'
own state origin count is.

## session-2026-10-01.jsonl

239 labels for the articles that were waiting on 2026-10-01 and could be fetched on the owner's
machine: 37 state origin, 35 unchecked state sourcing, 121 independent journalism and 46 not
relevant. Same prompt, same checks; all 239 evidence quotes were found verbatim in their own
article. 213 further articles in the queue refuse the fetch (HTTP 403, robots.txt, gone or
paywalled) and can only be judged through the API from the runner's own copy of the text.
