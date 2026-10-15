# Doctrine terms: hand check guide

This guide is for the people who hand code the doctrine term sample, the check that decides whether
the doctrine term tracker (`pipeline/doctrine.py`, terms in `pipeline/doctrine.yaml`) is shown as
checked, stays provisional, or is withheld, family by family.

## What is being checked

The tracker finds the Chinese state's set phrases (community with a shared future, the global
initiatives, the one-China principle and the rest) by keyword. The check measures **precision**:
of the matches it counts, how many really are the phrase in question. It does not measure whether
the article agrees with the phrase, who said it, or how many phrases the tracker missed.

## The sheet

Draw a sample with `python -m pipeline.measures sample doctrine --n 200`. It writes
`data/reviews/doctrine_sample_<date>.csv` and a `.key.json` beside it. Do not open the key.

Each row is one match: the article link, its language, the family it was counted for, the matched
term, and a snippet of about 30 words either side with the match between `[[` and `]]`. Rows are
spread evenly over families and languages, so rare families and languages are represented.

Fill in three columns:

- `correct`: `yes` or `no`, as below. Use `unreadable` when you cannot tell (a language you do not
  read and no one to ask, a garbled snippet and a dead link). Unreadable rows count as coded but are
  left out of precision.
- `coder`: your initials.
- `note`: optional, and required for every `no` (say what the match really is).

## Deciding yes or no

Answer one question: **is the text between `[[` and `]]` the family's phrase, used as that phrase?**

- `yes` when it is the phrase, whoever uses it and in whatever spirit: quoted from an official,
  written by the journalist, criticised, or mocked. "Critics say Xi Jinping Thought is propaganda"
  is a `yes` for Xi Jinping Thought.
- `yes` for a translation of the phrase in the article's language, as long as it renders the same
  Chinese set phrase.
- `no` when the words mean something else: a different body with the same acronym (GSI, GDI, BRI),
  a ring road or a sports league, "power politics" or "multipolar" in an unrelated sense, a name or a
  headline fragment that only looks like the phrase.
- `no` when the match is a fragment of a longer phrase with another meaning (for example a family
  name inside an organisation's name), or a navigation link, a tag list or another story's headline
  on the page rather than the article.
- Muted families (Belt and Road, Global South) are coded the same way: is it the phrase? Their
  common use outside Chinese state media is expected and is a `yes`.

If you read the article itself, judge only the matched occurrence in the snippet, not the article
as a whole.

## How it is scored

Precision per family is the share of `yes` among the `yes` and `no` rows of that family.

- A family is **checked** at a precision of 0.9 or more on at least 15 coded matches.
- A family is **withheld** below 0.8 once at least 5 of its matches are coded: the site then shows
  no figure for it, and it is left out of the share of articles using the vocabulary.
- Otherwise it stays **provisional**.
- The measure as a whole is checked when the pooled precision of the families not withheld reaches
  0.9 once every row is coded, and withheld when it does not.

The check is scored at every export from the newest committed sample of the current term list
version. A sample drawn under an older version no longer counts; draw a new one after the terms
change. Commit the coded CSV and its key together.
