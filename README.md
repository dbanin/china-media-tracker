# China state media tracker

A research instrument that discovers, every day, how much Chinese state-origin
and Chinese-state-sourced news content is published in every country it can
monitor, keeps that history permanently, and shows it on a world map.

It generalizes the coding framework of a completed study of 484 articles across
Italy, Canada and Australia (Stanford, with Professor Larry Diamond) and runs it
continuously.

Read METHODOLOGY.md for what the system does and what its numbers mean. That
file is generated from live values and is the authoritative description.

## Increments landed

1. Outlet registry, schema, feed validator, and dense coverage of the three
   study countries.

## Running locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m pipeline.registry              # summarize the registry
.venv/bin/python -m pipeline.validate_sources      # check every feed
```

Set `TRACKER_CONTACT` to a contact address before running the crawler against
live sites, so the user agent identifies a real person. Set
`TRACKER_REPO_URL` to the repository URL once it exists.

## The Chinese language rule

Outlets that publish primarily in Chinese are registered with `active: false`
and a note, not removed. The project owner does not read Chinese and decided
that machine translation would miss precisely the distinctions the coding
framework turns on. The exclusion is therefore visible and reversible.
