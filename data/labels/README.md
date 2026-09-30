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
