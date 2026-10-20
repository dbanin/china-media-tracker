"""Agreement study. Draw a fixed random sample of model labelled articles, blank
the labels, produce a CSV for hand coding, and compute Cohen's kappa between the
model's labels and the hand labels.

Unchecked state sourcing counts are published only on the strength of this study
(owner's decision, 12 October 2026, after an outside review): a rerun of the same
model, or a second model, measures whether the judgement is stable, not whether
it is correct, so pipeline/second_rater.py is a stability check and nothing more.

Usage:
  python -m pipeline.agreement sample
      the fixed hand coding sample: about 200 articles for each language with at
      least 300 model labelled articles, stratified by language and, within each
      language, by the model's current category, seeded so the same database gives
      the same draw. Written to data/reviews/human_sample_<date>.csv. Coders work
      blind: the CSV carries no model label. See data/reviews/CODING_GUIDE.md.
  python -m pipeline.agreement sample --since-prompt [YYYY-MM-DD]
      the same draw from model labels made under the current classifier prompt
      (or the one named) only. Run it on the runner's snapshot (scripts/db_pull.sh):
      it refuses a database whose newest model label is more than
      SAMPLE_MAX_DB_AGE_DAYS old, since a local copy holds labels the runner has
      already replaced. See CODING_GUIDE.md for when to redraw.
  python -m pipeline.agreement sample --n 100 --out review/agreement_2026-09-03.csv
      the older pooled sample, kept for the record
  python -m pipeline.agreement compute --csv data/reviews/human_sample_<date>.csv [--partial] [--dry-run]

What the study compares against (since 17 October 2026). Each coded article is
compared with its model label current when compute runs, and only when that label
was made under the current classifier prompt (classify_llm.PROMPT_VERSION): the
labels the site counts are those, and a study that passed on labels since replaced
would certify nothing. An article whose current label is on an older prompt, or is
no longer a model label, is left out of the comparison and the number left out is
recorded and published. The key file still freezes, at sample time, the id of the
classification row then current; compute --against frozen compares with those
rows for the audit, prints the figures and records nothing. The human sample's
key holds the row ids only, never the categories: classification rows are never
deleted, and compute reads the category from the row, so the key can sit beside
the CSV without unblinding anyone who opens it. A study recorded under one prompt
stops counting as passed once the prompt changes (human_study_status: stale); one
that failed keeps withholding until a new study reports.

compute reads the filled CSV, computes kappa across all four categories and
kappa on the B versus C distinction alone (over the articles either coder
placed in B or C), stores the result in agreement_studies, and records every
hand label as a human review so the map's reviewed mode can use it. The coder
column holds pseudonymous coder ids (CODER_ID, such as c1), never names; compute
records nothing while a coder cell holds anything else.

A kappa on B versus C needs MIN_POSITIVE_FOR_KAPPA items in B from each rater.
When the model placed at least that many in B and the coders fewer, the study has
failed, not come out inconclusive, overall and in each language: the model calls
unchecked state sourcing where people reading the same articles do not, which is
the error the study exists to catch. Fewer than that from the model leaves it
inconclusive whatever the coders did, since the model's counts are then the ones
too small to test.
"""
import argparse
import csv
import datetime as dt
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pipeline import classify_llm, config, store

CATEGORIES = ["A", "B", "C", "not_relevant"]


# A binary kappa is reported only when both raters placed at least this many items in the class the
# study is about (B, unverified relay). The prompt tells the model to answer C when it is uncertain,
# so a sample with almost no B is the expected case, not a freak one, and on such a sample the
# formula returns a number that says nothing: 300 pairs of notB against notB came back as 1.0, which
# cleared the publication threshold on a study that never tested the distinction, while 299
# agreements and one disagreement came back as 0.0 and withheld counts that were fine. Ten on each
# side is the point below which one item moves the figure across the whole of its range.
MIN_POSITIVE_FOR_KAPPA = 10

# The fixed hand coding sample. About this many articles for each qualifying language, so a
# per language kappa rests on a sample a person can code in a few days, and every language large
# enough to move a published count gets its own check.
HUMAN_SAMPLE_PER_LANGUAGE = 200
# A language qualifies for its own stratum once the model has labelled at least this many of its
# articles. Below it a 200 item draw would take two thirds of the pool or more, and the language is
# covered by the overall kappa only.
HUMAN_SAMPLE_MIN_LABELLED = 300
HUMAN_SAMPLE_SEED = "human-sample-2026-10"
HUMAN_SAMPLE_STRATA = "language, then the model's current category within each language"
REVIEWS_DIR = config.ROOT / "data" / "reviews"
HUMAN_SAMPLE_COLUMNS = ["sample_id", "article_id", "url", "outlet", "language", "title",
                        "human_category", "coder", "note"]
# What coders write in human_category. compute also accepts the codebook's internal codes.
CODER_LABELS = {"state_origin": "A", "unchecked_state_sourcing": "B", "independent_journalism": "C",
                "not_about_china": "not_relevant"}
# What a coder writes in the coder column: a pseudonymous id, one to four letters then one to three
# digits (c1, c2, ra3), the same on every row that person codes. The sheet is committed, so a name
# or initials in it would publish who coded what; the owner keeps the id to person list off the repo.
CODER_ID = re.compile(r"^[A-Za-z]{1,4}[0-9]{1,3}$")
# sample --since-prompt refuses a database whose newest model label is older than this: the draw has
# to come from the runner's labels, and a local copy days old holds labels the runner has replaced.
SAMPLE_MAX_DB_AGE_DAYS = 2
# What compute compares the coders with, in words for the site (relay_human_study.labels_compared).
LABELS_COMPARED = {
    "current_label": "each coded article's model label current when the study was computed, made under classifier "
                     "prompt %s; articles whose current label was made under an older prompt, or is no longer a "
                     "model label, are left out of the comparison",
    "frozen": "the model labels frozen in the sample key when the sample was drawn, for the audit only",
    "pending": "each coded article's model label current when the study is computed, made under the classifier "
               "prompt then current (now %s); articles whose label is on an older prompt are left out and counted",
}


def cohens_kappa(pairs: List[Tuple[str, str]], min_positive: int = 0, positive=None) -> Optional[float]:
    """Cohen's kappa for two raters over the same items, or None when the sample cannot support one.

    None, never a number, when: there are no pairs; both raters between them used a single label,
    where the formula is 0/0 and perfect agreement on a constant sample is not a finding; or
    min_positive is set and either rater used `positive` fewer than min_positive times."""
    n = len(pairs)
    if n == 0:
        return None
    labels = sorted({a for a, _ in pairs} | {b for _, b in pairs}, key=str)
    if len(labels) < 2:
        return None
    if min_positive:
        if sum(1 for a, _ in pairs if a == positive) < min_positive:
            return None
        if sum(1 for _, b in pairs if b == positive) < min_positive:
            return None
    po = sum(1 for a, b in pairs if a == b) / float(n)
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    pe = sum((ca[l] / float(n)) * (cb[l] / float(n)) for l in labels)
    if pe >= 1.0:       # a marginal wholly inside one label; kappa is undefined
        return None
    return (po - pe) / (1.0 - pe)


def kappa_shortfall(pairs: List[Tuple[str, str]], positive="B", min_positive: int = MIN_POSITIVE_FOR_KAPPA) -> Optional[str]:
    """Why a binary kappa cannot be reported on these pairs, in a sentence, or None when it can."""
    if not pairs:
        return "no pairs in the comparison"
    a = sum(1 for x, _ in pairs if x == positive)
    b = sum(1 for _, y in pairs if y == positive)
    if a < min_positive or b < min_positive:
        return ("%d of %d pairs carry %s from the first rater and %d from the second; each rater must place at least "
                "%d articles in %s before a kappa on that distinction means anything"
                % (a, len(pairs), positive, b, min_positive,
                   {"B": "unchecked state sourcing"}.get(positive, positive)))
    if len({x for x, _ in pairs} | {y for _, y in pairs}) < 2:
        return "both raters used a single label, so there is no distinction to measure"
    return None


def kappa_failure(pairs: List[Tuple[str, str]], positive="B", min_positive: int = MIN_POSITIVE_FOR_KAPPA) -> Optional[str]:
    """Why a study with no reportable kappa has failed rather than come out inconclusive, or None.

    The first rater is the model, the second the coders. When the model placed at least min_positive
    items in `positive` and the coders fewer, the model sees unchecked state sourcing where people do
    not: that is the failure the study exists to catch, and it must withhold, not leave the counts
    provisional because the coders' side is too small for a kappa."""
    a = sum(1 for x, _ in pairs if x == positive)
    b = sum(1 for _, y in pairs if y == positive)
    if a >= min_positive and b < min_positive:
        return ("the model placed %d of %d articles in unchecked state sourcing and the coders %d, fewer than the %d "
                "a kappa needs: the model reports unchecked state sourcing that people reading the same articles do "
                "not find" % (a, len(pairs), b, min_positive))
    return None


def stratified_sample(rows: List[Dict], n: int, seed: Optional[int] = None) -> List[Dict]:
    """Roughly equal allocation across machine categories, remainder filled proportionally.
    Categories with too few items contribute everything they have."""
    rng = random.Random(seed)
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    for v in by_cat.values():
        rng.shuffle(v)
    cats = [c for c in CATEGORIES if by_cat.get(c)]
    if not cats:
        return []
    picked = []
    quota = {c: n // len(cats) for c in cats}
    for c in cats:
        picked.extend(by_cat[c][:quota[c]])
        by_cat[c] = by_cat[c][quota[c]:]
    leftovers = [r for c in cats for r in by_cat[c]]
    rng.shuffle(leftovers)
    picked.extend(leftovers[:max(0, n - len(picked))])
    rng.shuffle(picked)
    return picked


def classified_rows(conn) -> List[Dict]:
    rows = conn.execute(
        """SELECT a.id, a.title, a.url, a.url_hash, a.country, a.outlet_id, c.category, c.method, c.confidence, c.id AS cid
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 AND a.status='classified'"""
    ).fetchall()
    return [dict(r) for r in rows]


def human_pool(conn, since_prompt: Optional[str] = None) -> List[Dict]:
    """What the hand coding study checks: articles carrying a current model label and an extracted
    body. Labels the rules gave are out, because the question is whether the model's judgement holds.
    since_prompt keeps only labels made under that classifier prompt or a later one."""
    rows = conn.execute(
        """SELECT a.id, a.title, a.url, a.language, a.outlet_id, o.name AS outlet, c.category, c.id AS cid,
                  c.model_version
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           LEFT JOIN outlets o ON o.id=a.outlet_id
           WHERE c.method='llm' AND a.gate_relevant=1 AND COALESCE(a.body_chars, 0) > 0
           ORDER BY a.id"""
    ).fetchall()
    out = [dict(r) for r in rows]
    if since_prompt:
        out = [r for r in out if (classify_llm.prompt_version_of(r["model_version"]) or "") >= since_prompt]
    return out


def newest_model_label_age_days(conn, now: Optional[dt.datetime] = None) -> Optional[float]:
    """Days since the newest model label in this database was made, or None when it holds none. A
    runner snapshot is under a day old; a local copy is as old as the last pull."""
    newest = conn.execute("SELECT MAX(classified_at) FROM classifications WHERE method='llm'").fetchone()[0]
    if not newest:
        return None
    try:
        then = dt.datetime.fromisoformat(newest.replace("Z", "+00:00"))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    return (now - then).total_seconds() / 86400.0


def qualifying_languages(rows: List[Dict], min_labelled: int = HUMAN_SAMPLE_MIN_LABELLED) -> Dict[str, int]:
    """Languages with at least min_labelled model labelled articles, largest first, with their pool size."""
    counts = Counter(r["language"] for r in rows if r.get("language"))
    return {lang: n for lang, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n >= min_labelled}


def human_sample(rows: List[Dict], per_language: int = HUMAN_SAMPLE_PER_LANGUAGE,
                 min_labelled: int = HUMAN_SAMPLE_MIN_LABELLED, seed: str = HUMAN_SAMPLE_SEED) -> List[Dict]:
    """The fixed sample: each qualifying language is a stratum of its own, and within it the draw is
    stratified by the model's current category, equal allocation with the remainder filled at random,
    so unchecked state sourcing, rare in most languages, is present in numbers that can test the
    distinction. Every language has its own seed, so adding a language leaves the others unchanged."""
    out = []
    for lang in qualifying_languages(rows, min_labelled):
        pool = sorted((r for r in rows if r.get("language") == lang), key=lambda r: r["id"])
        out.extend(stratified_sample(pool, per_language, seed="%s:%s" % (seed, lang)))
    return out


def cmd_human_sample(conn, out: Path, per_language: int = HUMAN_SAMPLE_PER_LANGUAGE,
                     min_labelled: int = HUMAN_SAMPLE_MIN_LABELLED, seed: str = HUMAN_SAMPLE_SEED,
                     since_prompt: Optional[str] = None, max_db_age_days: float = SAMPLE_MAX_DB_AGE_DAYS) -> Dict:
    """Write the blind coding CSV and its key. Prints sizes per language and nothing about the model's
    labels, because the person running it is one of the coders.

    since_prompt draws from labels made under that prompt or a later one only (the redraw after a
    prompt change), and then refuses a database whose newest model label is over max_db_age_days
    old: a stale local copy would draw from labels the runner has since replaced. Neither path ever
    overwrites an existing sample, which coders may already be filling in."""
    if out.exists() or out.with_suffix(".key.json").exists():
        print("not written: %s or its key already exists. Coders may be working on it; choose another --out." % out)
        return {"n": 0, "refused": "exists", "out": str(out)}
    every = human_pool(conn)
    if since_prompt:
        age = newest_model_label_age_days(conn)
        if age is None or age > max_db_age_days:
            print("not written: the newest model label in this database is %s old. Draw the sample from the runner's "
                  "snapshot (scripts/db_pull.sh, then rerun): a local copy holds labels the runner has replaced."
                  % ("of no age, there are none" if age is None else "%.1f days" % age))
            return {"n": 0, "refused": "stale_database", "age_days": age, "out": str(out)}
    rows = human_pool(conn, since_prompt) if since_prompt else every
    older = len(every) - len(rows) if since_prompt else sum(
        1 for r in every if not classify_llm.on_current_prompt(r["model_version"]))
    if since_prompt and not rows:
        print("not written: no model label with an extracted body was made under prompt %s or later." % since_prompt)
        return {"n": 0, "refused": "empty_pool", "out": str(out)}
    langs = qualifying_languages(rows, min_labelled)
    picked = human_sample(rows, per_language, min_labelled, seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    sampled = Counter()
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HUMAN_SAMPLE_COLUMNS)
        for r in picked:
            sampled[r["language"]] += 1
            w.writerow(["%s-%03d" % (r["language"], sampled[r["language"]]), r["id"], r["url"],
                        r.get("outlet") or r.get("outlet_id") or "", r["language"], r["title"] or "", "", "", ""])
    key = {"kind": "human_sample", "created_at": store.utcnow(), "seed": seed, "per_language": per_language,
           "min_labelled": min_labelled, "stratified_on": HUMAN_SAMPLE_STRATA,
           "ruleset_version": config.RULESET_VERSION, "llm_model": config.LLM_MODEL, "n": len(picked),
           # The classifier prompt current at the draw, which is the codebook the coders apply, and
           # whether the pool was limited to labels made under it.
           "prompt_version": classify_llm.PROMPT_VERSION, "since_prompt": since_prompt,
           "pool_labels_on_older_prompt": older,
           "languages": {lang: {"pool": langs[lang], "sampled": sampled[lang]} for lang in langs},
           # Ids only. The category is read from the classification row when the study is computed.
           "items": [{"article_id": r["id"], "classification_id": r["cid"], "language": r["language"]} for r in picked]}
    key_path = out.with_suffix(".key.json")
    with open(key_path, "w", encoding="utf-8") as fh:
        json.dump(key, fh, indent=1)
    print("wrote %d items to %s (classification ids frozen in %s)" % (len(picked), out, key_path))
    for lang in langs:
        print("  %s: %d sampled from %d model labelled articles" % (lang, sampled[lang], langs[lang]))
    if since_prompt:
        print("drawn from labels made under prompt %s or later; %d model labels on older prompts were left out"
              % (since_prompt, older))
    elif older:
        print("warning: %d model labels in the pool were made under an older prompt than %s; compute will leave any "
              "sampled article still on one out of the comparison. Use --since-prompt on the runner's snapshot."
              % (older, classify_llm.PROMPT_VERSION))
    print("Code human_category blind, following data/reviews/CODING_GUIDE.md, then run compute.")
    return {"n": len(picked), "languages": key["languages"], "out": str(out), "key": str(key_path),
            "since_prompt": since_prompt, "pool_labels_on_older_prompt": older}


def _sample_keys(reviews_dir: Optional[Path] = None) -> List[Tuple[Path, Dict]]:
    """Every readable human sample key in data/reviews, oldest first."""
    out = []
    for path in sorted((reviews_dir or REVIEWS_DIR).glob("human_sample_*.key.json")):
        try:
            out.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def _sample_summary(path: Path, key: Dict) -> Dict:
    return {"file": path.name.replace(".key.json", ".csv"), "created_at": key.get("created_at"),
            "n": key.get("n"), "per_language": key.get("per_language"), "min_labelled": key.get("min_labelled"),
            "stratified_on": key.get("stratified_on"), "prompt_version": key.get("prompt_version"),
            "since_prompt": key.get("since_prompt"),
            "languages": {lang: (v or {}).get("sampled") for lang, v in (key.get("languages") or {}).items()}}


def latest_sample_info(reviews_dir: Optional[Path] = None) -> Optional[Dict]:
    """The newest human sample in data/reviews that is not superseded, summarised for the site, or None.
    A key marked superseded (its "superseded" block) is one coders must not start on."""
    live = [(p, k) for p, k in _sample_keys(reviews_dir) if not k.get("superseded")]
    return _sample_summary(*live[-1]) if live else None


def superseded_samples(reviews_dir: Optional[Path] = None) -> List[Dict]:
    """Samples marked superseded in their key, with the date and the reason, oldest first."""
    out = []
    for path, key in _sample_keys(reviews_dir):
        if key.get("superseded"):
            s = _sample_summary(path, key)
            s.update(superseded_on=(key["superseded"] or {}).get("on"), reason=(key["superseded"] or {}).get("reason"))
            out.append(s)
    return out


def human_study_status(conn, reviews_dir: Optional[Path] = None) -> Dict:
    """Where the hand coding study stands. passed is the only state that publishes unchecked state
    sourcing: kappa on unchecked sourcing versus independent journalism at or above the threshold,
    with the coders and the model each placing at least MIN_POSITIVE_FOR_KAPPA articles in unchecked state
    sourcing, on a sample coded to the end."""
    row = conn.execute("SELECT * FROM agreement_studies ORDER BY id DESC LIMIT 1").fetchone()
    current_prompt = classify_llm.PROMPT_VERSION
    out = {"threshold": config.KAPPA_WARNING_THRESHOLD, "min_positive": MIN_POSITIVE_FOR_KAPPA,
           "per_language": HUMAN_SAMPLE_PER_LANGUAGE, "min_labelled": HUMAN_SAMPLE_MIN_LABELLED,
           "stratified_on": HUMAN_SAMPLE_STRATA, "sample": latest_sample_info(reviews_dir),
           "superseded_samples": superseded_samples(reviews_dir),
           "n": None, "kappa_bc": None, "n_bc": None, "computed_at": None, "coded": None, "sampled": None,
           # Which labels the kappa is computed against, and under which classifier prompt. Before a study
           # reports, what the next one will compare against.
           "compared_against": None, "prompt_version": None, "current_prompt_version": current_prompt,
           "labels_compared": LABELS_COMPARED["pending"] % current_prompt,
           "excluded_older_prompt": None, "excluded_no_model_label": None, "stale": False, "failed_reason": None}
    if not row:
        out["state"] = ("sample_drawn" if out["sample"] else
                        "sample_superseded" if out["superseded_samples"] else "not_started")
    else:
        try:
            details = json.loads(row["details"] or "{}") or {}
        except ValueError:
            details = {}
        against = details.get("compared_against")
        study_prompt = details.get("prompt_version")
        out.update(n=row["sample_size"], kappa_bc=row["kappa_bc"], n_bc=row["n_bc"], computed_at=row["computed_at"],
                   coded=details.get("coded"), sampled=details.get("sampled"), compared_against=against,
                   prompt_version=study_prompt, excluded_older_prompt=details.get("excluded_older_prompt"),
                   excluded_no_model_label=details.get("excluded_no_model_label"),
                   failed_reason=details.get("kappa_bc_failed"))
        if against == "current_label":
            out["labels_compared"] = LABELS_COMPARED["current_label"] % (study_prompt or "not recorded")
        elif details.get("method") == "human_coding":
            # Recorded by compute before 17 October 2026, against the labels frozen at sample time.
            out["labels_compared"] = LABELS_COMPARED["frozen"]
        # A study cmd_compute recorded certifies the labels of the prompt it compared against, and no
        # others: it is stale once the prompt has changed, or when it compared frozen labels. A stale study
        # never passes; one that failed still withholds until a new study reports.
        out["stale"] = bool(details.get("method") == "human_coding" and not (
            against == "current_label" and study_prompt and study_prompt >= current_prompt))
        if details.get("sample_complete") is False:
            out["state"] = "incomplete"
        elif row["kappa_bc"] is None and details.get("kappa_bc_failed"):
            out["state"] = "failed"
        elif row["kappa_bc"] is None:
            out["state"] = "inconclusive"
            out["inconclusive"] = details.get("kappa_bc_inconclusive")
        elif row["kappa_bc"] < config.KAPPA_WARNING_THRESHOLD:
            out["state"] = "failed"
        elif out["stale"]:
            out["state"] = "stale"
        else:
            out["state"] = "passed"
    out["passed"] = out["state"] == "passed"
    return out


def _day_text(iso: Optional[str]) -> Optional[str]:
    """"2026-10-12T20:12:00+00:00" as "12 October 2026", or None."""
    try:
        d = dt.date.fromisoformat((iso or "")[:10])
    except ValueError:
        return None
    return "%d %s %d" % (d.day, d.strftime("%B"), d.year)


def provisional_reason(status: Dict) -> str:
    """Why unchecked state sourcing is still provisional, in one or two sentences for the site."""
    rule = ("The counts stay provisional until the owner and two research assistants have hand coded a fixed random "
            "sample, about %d articles in each language with at least %d model labelled articles, stratified by "
            "language and, within each language, by the model's current category, and Cohen's kappa between their "
            "codes and the model's on unchecked state sourcing versus independent journalism is at least %.1f, with "
            "the coders and the model each placing at least %d articles in unchecked state sourcing, overall and in "
            "every language with enough items. A study in which "
            "the model finds unchecked state sourcing at least %d times and the coders fewer has failed."
            % (status["per_language"], status["min_labelled"], status["threshold"], status["min_positive"],
               status["min_positive"]))
    state = status.get("state")
    if state == "incomplete":
        return ("The hand coding study is under way: %s of %s sampled articles are coded. %s"
                % (status.get("coded") or 0, status.get("sampled") or "the", rule))
    if state == "inconclusive":
        return ("The hand coding study reported no kappa on that distinction: %s. %s"
                % (status.get("inconclusive") or "the sample held too few items on one side", rule))
    against = (" The study compares the coders with %s." % status["labels_compared"]
               if status.get("labels_compared") else "")
    if state == "stale":
        return ("The last hand coding study, computed %s, put kappa at %.2f against labels made under classifier "
                "prompt %s. The prompt has changed since (now %s), so that result no longer covers the labels "
                "counted, and a new sample is drawn from labels made under the current prompt. %s"
                % (_day_text(status.get("computed_at")) or "on an unrecorded date", status.get("kappa_bc") or 0,
                   status.get("prompt_version") or "not recorded, against labels frozen at sample time,",
                   status.get("current_prompt_version"), rule))
    if state == "sample_drawn":
        return "No hand coding study has reported yet; the sample is drawn and being coded.%s %s" % (against, rule)
    if state == "sample_superseded":
        old = status["superseded_samples"][-1]
        return ("No hand coding study has reported yet. The sample drawn on %s was set aside before coding began: %s. "
                "A new sample is drawn from labels made under the current classifier prompt (%s) once the runner "
                "has relabelled them, and the study compares the coders with those labels. %s"
                % (_day_text(old.get("created_at")) or "an earlier date",
                   (old.get("reason") or "it drew labels made under an older classifier prompt").rstrip("."),
                   status.get("current_prompt_version"), rule))
    return "No hand coding study has reported yet.%s %s" % (against, rule)


def cmd_sample(conn, n: int, out: Path, seed: Optional[int]) -> Dict:
    rows = classified_rows(conn)
    picked = stratified_sample(rows, n, seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "article_id", "headline", "url", "excerpt", "human_category", "human_note"])
        for i, r in enumerate(picked, 1):
            body = store.load_body(r["url_hash"]) or ""
            w.writerow([i, r["id"], r["title"] or "", r["url"], body[:1500].replace("\n", " "), "", ""])
    key = {"created_at": store.utcnow(), "n": len(picked), "seed": seed, "ruleset_version": config.RULESET_VERSION,
           "items": [{"article_id": r["id"], "machine_category": r["category"], "method": r["method"],
                      "confidence": r["confidence"], "classification_id": r["cid"]} for r in picked]}
    key_path = out.with_suffix(".key.json")
    with open(key_path, "w", encoding="utf-8") as fh:
        json.dump(key, fh, indent=1)
    dist = Counter(r["category"] for r in picked)
    print("wrote %d items to %s (machine labels frozen in %s)" % (len(picked), out, key_path))
    print("machine label distribution in sample: %s" % dict(dist))
    print("Hand code the human_category column with A, B, C or not_relevant, then run compute.")
    return {"n": len(picked), "distribution": dict(dist)}


def compute_from_pairs(items: List[Dict]) -> Dict:
    pairs = [(it["machine_category"], it["human_category"]) for it in items]
    kappa_all = cohens_kappa(pairs)
    bc = [(m, h) for m, h in pairs if m in ("B", "C") or h in ("B", "C")]
    bc_binary = [("B" if m == "B" else "notB", "B" if h == "B" else "notB") for m, h in bc]
    kappa_bc = cohens_kappa(bc_binary, min_positive=MIN_POSITIVE_FOR_KAPPA, positive="B")
    kappa_bc_failed = kappa_failure(bc_binary) if kappa_bc is None else None
    kappa_bc_inconclusive = kappa_shortfall(bc_binary) if kappa_bc is None and not kappa_bc_failed else None
    confusion = defaultdict(Counter)
    for m, h in pairs:
        confusion[m][h] += 1
    # One category against the rest, for every category either coder used. A single kappa across
    # four categories hides where the disagreement sits.
    by_category = {}
    for cat in CATEGORIES:
        binary = [(m == cat, h == cat) for m, h in pairs]
        if any(m or h for m, h in binary):
            by_category[cat] = {"kappa": cohens_kappa(binary), "n_machine": sum(1 for m, _ in binary if m),
                                "n_human": sum(1 for _, h in binary if h)}
    # The unverified relay versus independent journalism judgement, per language of the article.
    bc_by_language = {}
    for lang in sorted({it.get("language") for it in items if it.get("language")}):
        sub = [("B" if it["machine_category"] == "B" else "notB", "B" if it["human_category"] == "B" else "notB")
               for it in items if it.get("language") == lang
               and (it["machine_category"] in ("B", "C") or it["human_category"] in ("B", "C"))]
        k = cohens_kappa(sub, min_positive=MIN_POSITIVE_FOR_KAPPA, positive="B")
        failed = kappa_failure(sub) if k is None else None
        # failed: the model's B at least MIN_POSITIVE_FOR_KAPPA here and the coders' fewer. The export's
        # language veto withholds such a language whatever its size (export.build_meta).
        bc_by_language[lang] = {"kappa": k, "n": len(sub), "n_b": sum(1 for a, _ in sub if a == "B"),
                                "n_b_human": sum(1 for _, b in sub if b == "B"),
                                "failed": bool(failed), "failed_reason": failed}
    return {"kappa_all": kappa_all, "kappa_bc": kappa_bc, "kappa_bc_inconclusive": kappa_bc_inconclusive,
            "kappa_bc_failed": kappa_bc_failed,
            "kappa_min_positive": MIN_POSITIVE_FOR_KAPPA, "n": len(pairs), "n_bc": len(bc),
            "agreement": sum(1 for m, h in pairs if m == h) / float(len(pairs)) if pairs else None,
            "confusion": {m: dict(c) for m, c in confusion.items()},
            "kappa_by_category": by_category, "bc_by_language": bc_by_language}


def coder_category(value: str) -> Optional[str]:
    """A human_category cell as a codebook code, or None when it is blank or not a category."""
    raw = (value or "").strip()
    if raw in CATEGORIES:
        return raw
    norm = raw.lower().replace(" ", "_").replace("-", "_")
    if norm in CODER_LABELS:
        return CODER_LABELS[norm]
    return raw.upper() if raw.upper() in ("A", "B", "C") else None


def _machine_label(conn, item: Dict, against: str = "current") -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """The model label the coders are compared with, as (category, classification id, why it is left out).

    against="current" (the default): the article's current label, when it is a model label made under
    the current classifier prompt. Otherwise the category is None and the third value says why:
    "older_prompt" for a model label made under an earlier prompt, which the runner will replace, or
    "no_model_label" when the current label is no longer the model's.
    against="frozen": the row frozen in the key at sample time, for the audit."""
    if against == "current":
        row = conn.execute("SELECT id, category, method, model_version FROM classifications WHERE article_id=? AND is_current=1",
                           (item["article_id"],)).fetchone()
        if not row or row["method"] != "llm":
            return None, None, "no_model_label"
        if not classify_llm.on_current_prompt(row["model_version"]):
            return None, row["id"], "older_prompt"
        return row["category"], row["id"], None
    if item.get("machine_category"):
        return item["machine_category"], item.get("classification_id"), None
    row = conn.execute("SELECT category FROM classifications WHERE id=?", (item.get("classification_id"),)).fetchone()
    return (row[0], item.get("classification_id"), None) if row else (None, None, "no_model_label")


def _machine_category(conn, item: Dict, against: str = "current") -> Optional[str]:
    """The model label's category alone (_machine_label), or None when the item is left out."""
    return _machine_label(conn, item, against)[0]


def cmd_compute(conn, csv_path: Path, reviewer: str, record: bool = True, partial: bool = False,
                against: str = "current") -> Dict:
    """Read the coded CSV, compute kappa against each article's current model label made under the
    current prompt (or, with against="frozen", the labels frozen at sample time, which records nothing),
    and record one agreement_studies row, whose details carry the per language results the export's
    language veto reads, plus one human_reviews row per compared article. A sample not yet coded to the
    end is not recorded unless partial is set, and a partial study never publishes: export treats it as
    incomplete. A superseded sample, and a sheet whose coder column holds anything but pseudonymous ids
    (CODER_ID), are never recorded."""
    key_path = csv_path.with_suffix(".key.json")
    with open(key_path, "r", encoding="utf-8") as fh:
        key = json.load(fh)
    machine = {it["article_id"]: it for it in key["items"]}
    items, accounted, coders, bad_coders = [], set(), set(), set()
    excluded = Counter()
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                aid = int(row.get("article_id") or "")
            except ValueError:
                continue
            if aid not in machine or aid in accounted:
                continue
            note = (row.get("note") or row.get("human_note") or "").strip()
            h = coder_category(row.get("human_category"))
            if h is None:
                if note:        # left blank with a reason, such as a page that no longer loads
                    accounted.add(aid)
                continue
            raw_coder = (row.get("coder") or "").strip()
            if raw_coder and not CODER_ID.match(raw_coder):
                bad_coders.add(raw_coder)
            # Coded, so the row counts towards a finished sheet whether or not it can be compared.
            accounted.add(aid)
            m, cid, why = _machine_label(conn, machine[aid], against)
            if m is None:
                excluded[why or "no_model_label"] += 1
                continue
            language = machine[aid].get("language")
            if not language:   # key files from before per-language kappa carry no language
                found = conn.execute("SELECT language FROM articles WHERE id=?", (aid,)).fetchone()
                language = found[0] if found else None
            coder = raw_coder or reviewer
            coders.add(coder)
            items.append({"article_id": aid, "machine_category": m, "human_category": h,
                          "classification_id": cid, "note": note, "language": language, "coder": coder})
    res = compute_from_pairs(items)
    res["sampled"], res["coded"] = len(machine), len(items)
    res["complete"] = len(accounted) >= len(machine)
    res["compared_against"] = "current_label" if against == "current" else "frozen"
    res["prompt_version"] = classify_llm.PROMPT_VERSION if against == "current" else None
    res["excluded_older_prompt"] = excluded.get("older_prompt", 0)
    res["excluded_no_model_label"] = excluded.get("no_model_label", 0)
    res["superseded"] = bool(key.get("superseded"))
    res["bad_coders"] = sorted(bad_coders)
    coverage = defaultdict(lambda: {"coded": 0, "sampled": 0})
    for it in machine.values():
        coverage[it.get("language") or "unknown"]["sampled"] += 1
    for it in items:
        coverage[it.get("language") or "unknown"]["coded"] += 1
    res["coded_by_language"] = dict(coverage)
    if against == "current":
        print("compared with each article's current model label under prompt %s; left out: %d whose label is on an "
              "older prompt, %d whose current label is no longer the model's"
              % (classify_llm.PROMPT_VERSION, res["excluded_older_prompt"], res["excluded_no_model_label"]))
    else:
        print("compared with the labels frozen at sample time: the audit view, recorded nowhere")
    if not items:
        print("no comparable coded rows found in %s" % csv_path)
        return res
    print("coded items compared: %d of %d sampled%s" % (len(items), len(machine), "" if res["complete"] else " (sample not finished)"))
    for lang, v in sorted(coverage.items()):
        k = (res["bc_by_language"].get(lang) or {}).get("kappa")
        print("  %s: %d of %d compared, kappa on unchecked sourcing versus independent %s"
              % (lang, v["coded"], v["sampled"], "%.3f" % k if k is not None else "undefined"))
    print("coded items: %d" % res["n"])
    print("agreement: %.3f" % res["agreement"])
    print("Cohen's kappa, all categories: %s" % ("%.3f" % res["kappa_all"] if res["kappa_all"] is not None else "undefined"))
    print("Cohen's kappa, B versus C (n=%d): %s" % (res["n_bc"], "%.3f" % res["kappa_bc"] if res["kappa_bc"] is not None else "undefined"))
    if res["kappa_bc_failed"]:
        print("  failed: %s. Unchecked state sourcing counts will be withheld." % res["kappa_bc_failed"])
    elif res["kappa_bc"] is None:
        print("  inconclusive: %s" % res["kappa_bc_inconclusive"])
    print("confusion (machine -> human): %s" % json.dumps(res["confusion"]))
    if res["kappa_bc"] is not None and res["kappa_bc"] < config.KAPPA_WARNING_THRESHOLD:
        print("kappa on B versus C is below %.1f. Unchecked state sourcing counts will be withheld." % config.KAPPA_WARNING_THRESHOLD)
    elif res["kappa_bc"] is not None and res["complete"]:
        print("kappa on B versus C is at or above %.1f. Unchecked state sourcing counts will be published, "
              "except in any language whose own kappa is below it." % config.KAPPA_WARNING_THRESHOLD)
    failed_langs = sorted(lang for lang, v in res["bc_by_language"].items() if v.get("failed"))
    if failed_langs:
        print("withheld by language, the model's unchecked state sourcing unmatched by the coders: %s" % ", ".join(failed_langs))
    if record and against != "current":
        record = False
    if record and res["superseded"]:
        print("not recorded: %s is marked superseded in its key (%s). Draw a new sample with --since-prompt."
              % (csv_path.name, (key.get("superseded") or {}).get("reason") or "no reason given"))
        record = False
    if record and bad_coders:
        print("not recorded: the coder column must hold pseudonymous ids such as c1 or c2, never a name or initials; "
              "found %d other values. Replace them and run compute again." % len(bad_coders))
        record = False
    if record and not res["complete"] and not partial:
        print("not recorded: the sample is not finished. Every row needs a category, or a note saying why it has none. "
              "Pass --partial to record an interim study; it will not publish anything.")
        record = False
    if record:
        conn.execute(
            "INSERT INTO agreement_studies(computed_at, sample_size, kappa_all, kappa_bc, n_bc, details) VALUES (?,?,?,?,?,?)",
            (store.utcnow(), res["n"], res["kappa_all"], res["kappa_bc"], res["n_bc"],
             json.dumps({"method": "human_coding", "csv": str(csv_path), "confusion": res["confusion"], "agreement": res["agreement"],
                         "key": str(key_path), "kappa_bc_inconclusive": res["kappa_bc_inconclusive"],
                         "kappa_bc_failed": res["kappa_bc_failed"],
                         "kappa_min_positive": res["kappa_min_positive"],
                         # export.build_meta reads these two for the per language veto and the category table.
                         "bc_by_language": res["bc_by_language"], "kappa_by_category": res["kappa_by_category"],
                         "sample_complete": res["complete"], "sampled": res["sampled"], "coded": res["coded"],
                         "coded_by_language": res["coded_by_language"], "coders": sorted(coders),
                         "stratified_on": key.get("stratified_on"), "seed": key.get("seed"),
                         # What the kappa is computed against (human_study_status reads these).
                         "compared_against": res["compared_against"], "prompt_version": res["prompt_version"],
                         "sample_prompt_version": key.get("prompt_version"),
                         "excluded_older_prompt": res["excluded_older_prompt"],
                         "excluded_no_model_label": res["excluded_no_model_label"]})),
        )
        for it in items:
            store.insert_human_review(conn, it["article_id"], it["classification_id"], it["human_category"], it.get("coder") or reviewer,
                                      note="agreement study %s. %s" % (csv_path.name, it["note"]))
        conn.commit()
        print("recorded in agreement_studies and human_reviews")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=None, help="the older pooled sample of this size instead of the human sample")
    s.add_argument("--out", default=None)
    s.add_argument("--seed", default=None, help="default %s for the human sample" % HUMAN_SAMPLE_SEED)
    s.add_argument("--per-language", type=int, default=HUMAN_SAMPLE_PER_LANGUAGE)
    s.add_argument("--min-labelled", type=int, default=HUMAN_SAMPLE_MIN_LABELLED)
    s.add_argument("--since-prompt", nargs="?", const="current", default=None, metavar="YYYY-MM-DD",
                   help="draw only from model labels made under this classifier prompt or a later one (alone: the "
                        "current prompt). Run on the runner's snapshot; refuses a database whose newest model label "
                        "is over %d days old" % SAMPLE_MAX_DB_AGE_DAYS)
    c = sub.add_parser("compute")
    c.add_argument("--csv", required=True)
    c.add_argument("--reviewer", default="hand_coder", help="used for rows whose coder column is blank")
    c.add_argument("--partial", action="store_true", help="record a study on a sample not yet coded to the end")
    c.add_argument("--dry-run", action="store_true", help="print the figures and record nothing")
    c.add_argument("--against", choices=["current", "frozen"], default="current",
                   help="compare with each article's current model label under the current prompt (default), or, for "
                        "the audit only and never recorded, with the label frozen at sample time")
    args = ap.parse_args(argv)
    conn = store.connect()
    today = dt.date.today().isoformat()
    if args.cmd == "sample" and args.n:
        cmd_sample(conn, args.n, Path(args.out or "review/agreement_%s.csv" % today),
                   int(args.seed) if args.seed is not None else None)
    elif args.cmd == "sample":
        since = classify_llm.PROMPT_VERSION if args.since_prompt == "current" else args.since_prompt
        res = cmd_human_sample(conn, Path(args.out or REVIEWS_DIR / ("human_sample_%s.csv" % today)),
                               args.per_language, args.min_labelled, args.seed or HUMAN_SAMPLE_SEED, since_prompt=since)
        if res.get("refused"):
            sys.exit(1)
    else:
        cmd_compute(conn, Path(args.csv), args.reviewer, record=not args.dry_run, partial=args.partial, against=args.against)


if __name__ == "__main__":
    main()
