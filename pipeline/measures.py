"""The research measures: one shared provisional gate, and the contract every measure module follows.

The measures (sources/measures.yaml) are report only. None of them changes a label, a count, a
denominator or the outlets monitored, and China stays unmonitored. Each is shown on the site marked
provisional, with its own methodology paragraph, until its own hand check passes.

Module interface
----------------
A measure is a module named in sources/measures.yaml. It is imported lazily, at export time, so a
module that does not exist yet leaves its measure off with the reason "not built". It defines:

  MEASURE_ID = "<id>"                  the id in sources/measures.yaml
  VERSION = "<version>"                optional. Recorded in meta; a hand check whose key carries another
                                       measure_version is stale and does not count. Bump it only when the
                                       method changes what a hand check would judge.
  inputs_present(conn) -> int          how many inputs exist (rows, pairs, entries); True/False is read
                                       as 1/0. The measure runs only when this is at least min_inputs.
                                       Must be cheap and must not write.
  compute(conn, ctx, deadline) -> dict  writes the measure's own rows (its own tables or columns only)
                                       and returns counts for the log. deadline is a time.monotonic()
                                       value: stop when time.monotonic() >= deadline and resume on the
                                       next export. Commit what it writes.
  build_export(conn, ctx) -> dict      the JSON written to docs/data/<id>.json. Never article body text.
  meta_summary(conn, ctx) -> dict      a small summary kept in meta.measures[id].summary.
  methodology_text(block) -> str       one or more paragraphs for METHODOLOGY.md and the site's method
                                       section, given the measure's meta block (status and check set).
                                       Plain prose, no heading, no em or en dashes.
  draw_sample(conn, n, seed, out_dir) -> (csv_path, key_path)
                                       draws the hand check sample. Write it with write_sample() so the
                                       names are data/reviews/<id>_sample_<YYYY-MM-DD>.csv and .key.json.
                                       The CSV is what coders see (blind); the key holds the answers.
  score_check(rows, key) -> dict       scores a coded sample: rows are the CSV rows (dicts of strings),
                                       key is the parsed .key.json. Returns
                                       {statistic, value, threshold, passed, n, coded} and optionally
                                       complete (default: n > 0 and coded >= n). check_result() builds it.

ctx, the second argument of compute, build_export and meta_summary, is a dict with
  daily        export.build_daily's {month: {...}}
  latest       export.build_latest's latest.json
  outlets      the registry's outlets (registry.load_outlets)
  outlets_json export.build_outlets's outlets.json
  meta         meta.json as built so far (without measures)
  export_dir   the directory docs/data/<id>.json is written to (a Path)
  measures     the blocks of the measures already run in this export, by id

The gate
--------
meta.measures[id] = {status, label, version, file, enabled, inputs_present, min_inputs, check, reason,
method_text, summary} and, when the measure failed, error. status is one of
  off          not built, switched off, inputs below min_inputs, or failed; the site hides the section
  provisional  running, and no completed hand check of the current version has passed: none yet, or
               the newest is still being coded
  checked      the newest completed hand check passed
  withheld     the newest completed hand check failed; the site shows no figure, only that it is withheld
check = {state, file, n, coded, statistic, value, threshold, checked_on}, state one of none,
incomplete, passed, failed, stale, error. Checks are scored at export time from the newest committed
data/reviews/<id>_sample_*.csv and its .key.json, so hand coding done on the owner's machine reaches
the runner through git.

run_all_quietly runs every measure in the yaml order, each in its own try/except: a failure is
printed, rolled back, recorded in the block (status off, error) with an empty side file, and never
stops the export. A measure that is off has no side file; one left by an earlier export is removed.

Command line:
  python -m pipeline.measures status            the gate for every measure, without computing
  python -m pipeline.measures sample <id> [--n N] [--seed S] [--out DIR]
"""
import argparse
import csv
import datetime as dt
import importlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import yaml

from pipeline import config

MEASURES_YAML = config.ROOT / "sources" / "measures.yaml"
REVIEWS_DIR = config.ROOT / "data" / "reviews"
STATUSES = ("off", "provisional", "checked", "withheld")
CHECK_STATES = ("none", "incomplete", "passed", "failed", "stale", "error")
DEFAULT_BUDGET_SECONDS = 300
REQUIRED = ("compute", "build_export", "meta_summary", "methodology_text", "draw_sample", "score_check",
            "inputs_present")
METHOD_HEADING = "New measures (provisional)"
METHOD_INTRO = ("These measures are report only: none of them changes a label, a count, a denominator or the "
                "outlets monitored. Each is marked provisional until its own hand check passes, and withheld if "
                "the latest completed hand check fails.")
STATUS_WORDS = {"provisional": "provisional", "checked": "checked by hand", "withheld": "withheld"}
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config(path: Optional[Path] = None) -> List[Dict]:
    """sources/measures.yaml as a list of entries in run order, validated."""
    p = Path(path or MEASURES_YAML)
    with open(p, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or []
    if not isinstance(raw, list):
        raise ValueError("%s: expected a list of measures" % p)
    out, seen = [], set()
    for e in raw:
        if not isinstance(e, dict) or not re.fullmatch(r"[a-z][a-z0-9_]*", str(e.get("id") or "")):
            raise ValueError("%s: every measure needs an id of lower case letters, digits and _: %r" % (p, e))
        if e["id"] in seen:
            raise ValueError("%s: duplicate measure id %s" % (p, e["id"]))
        seen.add(e["id"])
        if not isinstance(e.get("module"), str) or not e["module"]:
            raise ValueError("%s: measure %s has no module" % (p, e["id"]))
        if not isinstance(e.get("enabled"), bool):
            raise ValueError("%s: measure %s needs enabled: true or false" % (p, e["id"]))
        mi = e.get("min_inputs", 1)
        if not isinstance(mi, int) or isinstance(mi, bool) or mi < 0:
            raise ValueError("%s: measure %s min_inputs must be a whole number" % (p, e["id"]))
        out.append({"id": e["id"], "label": str(e.get("label") or e["id"]), "module": e["module"],
                    "enabled": e["enabled"], "min_inputs": mi,
                    "budget_seconds": float(e.get("budget_seconds", DEFAULT_BUDGET_SECONDS))})
    return out


def import_measure(module_name: str):
    """The measure's module, or None when it does not exist yet. An import that fails inside an
    existing module raises: that is a broken measure, not an unbuilt one."""
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name and (module_name == exc.name or module_name.startswith(exc.name + ".")):
            return None
        raise


def side_file(measure_id: str) -> str:
    """The side file's name in docs/data, as meta.measures[id].file carries it."""
    return "%s.json" % measure_id


# ---------------------------------------------------------------------------
# Hand checks
# ---------------------------------------------------------------------------

def check_result(statistic: str, value: Optional[float], threshold: float, n: int, coded: int,
                 passed: Optional[bool] = None, complete: Optional[bool] = None, **extra) -> Dict:
    """The dict score_check returns. passed defaults to value >= threshold once the sample is
    complete (every row coded) and value is known; it is None while coding is incomplete."""
    if complete is None:
        complete = n > 0 and coded >= n
    if passed is None and complete and value is not None:
        passed = value >= threshold
    out = {"statistic": statistic, "value": value, "threshold": threshold, "passed": passed if complete else None,
           "n": n, "coded": coded, "complete": bool(complete)}
    out.update(extra)
    return out


def sample_files(measure_id: str, reviews_dir: Optional[Path] = None) -> List[Tuple[Path, Path]]:
    """(csv, key) pairs of the measure's hand check samples, newest first. A CSV without its key is
    skipped: it cannot be scored."""
    d = Path(reviews_dir or REVIEWS_DIR)
    out = []
    for c in sorted(d.glob("%s_sample_*.csv" % measure_id), key=lambda p: p.name, reverse=True):
        k = c.with_suffix(".key.json")
        if k.exists():
            out.append((c, k))
    return out


def write_sample(out_dir: Path, measure_id: str, rows: List[Dict], fieldnames: List[str], key: Dict,
                 version: Optional[str] = None, seed=None, day: Optional[str] = None) -> Tuple[Path, Path]:
    """Write a hand check sample: data/reviews/<id>_sample_<day>.csv (what the coders see) and
    .key.json (the answers, plus measure_id, measure_version, seed, n and created_at)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    day = day or dt.datetime.now(dt.timezone.utc).date().isoformat()
    csv_path = out_dir / ("%s_sample_%s.csv" % (measure_id, day))
    key_path = csv_path.with_suffix(".key.json")
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    full = {"measure_id": measure_id, "measure_version": version, "seed": seed, "n": len(rows),
            "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()}
    full.update(key or {})
    with open(key_path, "w", encoding="utf-8") as fh:
        json.dump(full, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    return csv_path, key_path


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(config.ROOT))
    except ValueError:
        return str(p)


def _empty_check(state: str = "none") -> Dict:
    return {"state": state, "file": None, "n": 0, "coded": 0, "statistic": None, "value": None,
            "threshold": None, "checked_on": None}


def score_sample(mod, csv_path: Path, key_path: Path) -> Dict:
    """One sample scored through the module's score_check, as a check block."""
    with open(key_path, encoding="utf-8") as fh:
        key = json.load(fh)
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    m = _DATE.search(csv_path.name)
    check = _empty_check()
    check.update({"file": _rel(csv_path), "checked_on": key.get("checked_on") or (m.group(1) if m else None)})
    version = getattr(mod, "VERSION", None)
    kv = key.get("measure_version")
    if version is not None and kv is not None and str(kv) != str(version):
        check.update({"state": "stale", "made_under": kv})
        return check
    r = mod.score_check(rows, key) or {}
    n, coded = int(r.get("n") or 0), int(r.get("coded") or 0)
    complete = r.get("complete")
    if complete is None:
        complete = n > 0 and coded >= n
    passed = r.get("passed")
    state = ("passed" if passed else "failed") if (complete and passed is not None) else "incomplete"
    check.update({"state": state, "n": n, "coded": coded, "statistic": r.get("statistic"), "value": r.get("value"),
                  "threshold": r.get("threshold")})
    return check


def _fmt(v) -> str:
    return "n/a" if v is None else ("%.2f" % v if isinstance(v, float) else str(v))


def assess(mod, measure_id: str, reviews_dir: Optional[Path] = None) -> Tuple[str, Dict, str]:
    """(status, check, reason) for a measure that runs, from its hand check samples. The newest
    sample of the current version decides; while it is still being coded, an earlier completed check
    that failed keeps the measure withheld, and one that passed leaves it provisional."""
    try:
        samples = sample_files(measure_id, reviews_dir)
        checks = []
        for c, k in samples:
            checks.append(score_sample(mod, c, k))
            if checks[-1]["state"] in ("passed", "failed"):
                break
    except Exception as exc:  # noqa: BLE001  a broken sample file is reported, never a stop
        chk = _empty_check("error")
        chk["error"] = "%s: %s" % (type(exc).__name__, exc)
        return "provisional", chk, "the hand check could not be scored (%s)" % chk["error"]
    current = [c for c in checks if c["state"] != "stale"]
    if not checks:
        return "provisional", _empty_check(), "no hand check yet"
    if not current:
        return "provisional", checks[0], ("the latest hand check was made under version %s; a new one is needed"
                                          % checks[0].get("made_under"))
    newest = current[0]
    done = next((c for c in current if c["state"] in ("passed", "failed")), None)

    def stat(c):
        return "%s %s against %s (n = %d)" % (c.get("statistic") or "score", _fmt(c.get("value")),
                                              _fmt(c.get("threshold")), c.get("coded") or 0)
    if newest["state"] == "passed":
        return "checked", newest, "hand check passed on %s: %s" % (newest.get("checked_on"), stat(newest))
    if newest["state"] == "failed":
        return "withheld", newest, "hand check failed on %s: %s" % (newest.get("checked_on"), stat(newest))
    progress = "%d of %d coded" % (newest.get("coded") or 0, newest.get("n") or 0)
    if done and done["state"] == "failed":
        return "withheld", done, ("the latest completed hand check failed on %s: %s; a new one is under way, %s"
                                  % (done.get("checked_on"), stat(done), progress))
    if done:
        return "provisional", newest, "a new hand check is under way, %s" % progress
    return "provisional", newest, "hand check under way, %s" % progress


# ---------------------------------------------------------------------------
# Running the measures
# ---------------------------------------------------------------------------

def _block(spec: Dict, mod=None) -> Dict:
    return {"status": "off", "label": spec["label"], "version": getattr(mod, "VERSION", None) if mod else None,
            "file": None, "enabled": spec["enabled"], "inputs_present": None, "min_inputs": spec["min_inputs"],
            "check": _empty_check(), "reason": "", "method_text": "", "summary": {}}


def _remove_side_file(export_dir: Path, measure_id: str) -> None:
    try:
        (Path(export_dir) / side_file(measure_id)).unlink()
    except FileNotFoundError:
        pass


def run_measure(conn: sqlite3.Connection, spec: Dict, export_dir: Path, ctx: Dict,
                write_json: Callable, reviews_dir: Optional[Path] = None) -> Dict:
    """One measure through the gate. Raises on failure; run_all_quietly catches."""
    mod = import_measure(spec["module"])
    if mod is None:
        b = _block(spec)
        b["reason"] = "not built"
        _remove_side_file(export_dir, spec["id"])
        return b
    missing = [f for f in REQUIRED if not callable(getattr(mod, f, None))]
    if missing:
        raise TypeError("%s lacks %s" % (spec["module"], ", ".join(missing)))
    if getattr(mod, "MEASURE_ID", spec["id"]) != spec["id"]:
        raise ValueError("%s has MEASURE_ID %r, not %r" % (spec["module"], mod.MEASURE_ID, spec["id"]))
    b = _block(spec, mod)
    if not spec["enabled"]:
        b["reason"] = "switched off in sources/measures.yaml"
        _remove_side_file(export_dir, spec["id"])
        return b
    present = mod.inputs_present(conn)
    b["inputs_present"] = int(present or 0)
    if b["inputs_present"] < spec["min_inputs"]:
        b["reason"] = "waiting for inputs: %d of at least %d" % (b["inputs_present"], spec["min_inputs"])
        _remove_side_file(export_dir, spec["id"])
        return b
    deadline = time.monotonic() + spec["budget_seconds"]
    b["computed"] = mod.compute(conn, ctx, deadline) or {}
    conn.commit()
    data = mod.build_export(conn, ctx)
    write_json(Path(export_dir) / side_file(spec["id"]), data if data is not None else {})
    b["file"] = side_file(spec["id"])
    b["summary"] = mod.meta_summary(conn, ctx) or {}
    b["status"], b["check"], b["reason"] = assess(mod, spec["id"], reviews_dir)
    b["method_text"] = mod.methodology_text(dict(b)) or ""
    return b


def run_all_quietly(conn: sqlite3.Connection, export_dir: Path, ctx: Optional[Dict] = None,
                    write_json: Optional[Callable] = None, config_path: Optional[Path] = None,
                    reviews_dir: Optional[Path] = None) -> Dict[str, Dict]:
    """Every measure in sources/measures.yaml through the gate, for the export: {id: block}. Each runs
    in its own try/except; a failure is printed, rolled back and recorded in its block (status off,
    error, an empty side file) and never stops the export. Side files are written with write_json
    (export.write_json, atomic, by default)."""
    if write_json is None:
        from pipeline.export import write_json as _wj
        write_json = _wj
    ctx = dict(ctx or {})
    ctx.setdefault("export_dir", Path(export_dir))
    out: Dict[str, Dict] = {}
    ctx["measures"] = out
    try:
        specs = load_config(config_path)
    except Exception as exc:  # noqa: BLE001
        _warn("measures config failed: %s: %s" % (type(exc).__name__, exc))
        return out
    for spec in specs:
        try:
            conn.commit()   # whatever the export has done so far is kept apart from this measure's work
            out[spec["id"]] = run_measure(conn, spec, export_dir, ctx, write_json, reviews_dir)
        except Exception as exc:  # noqa: BLE001  anything at all is logged and swallowed here
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            err = "%s: %s" % (type(exc).__name__, exc)
            _warn("measure %s failed: %s" % (spec["id"], err))
            b = _block(spec)
            b.update({"reason": "failed: %s" % err, "error": err, "file": side_file(spec["id"])})
            try:
                write_json(Path(export_dir) / side_file(spec["id"]), {})
            except Exception as exc2:  # noqa: BLE001
                _warn("measure %s side file failed: %s" % (spec["id"], exc2))
            out[spec["id"]] = b
    return out


# ---------------------------------------------------------------------------
# Methodology
# ---------------------------------------------------------------------------

def methodology_text(meta: Dict) -> str:
    """The METHODOLOGY.md section for every measure that is not off: one heading, a short intro and
    each measure's method_text, or "" when every measure is off. Ends with a blank line, so the
    template reads the same when it is empty."""
    blocks = (meta or {}).get("measures") or {}
    paras = []
    for mid, b in blocks.items():
        if not isinstance(b, dict) or b.get("status", "off") == "off" or not (b.get("method_text") or "").strip():
            continue
        paras.append("**%s (%s).** %s" % (b.get("label") or mid, STATUS_WORDS.get(b["status"], b["status"]),
                                         b["method_text"].strip()))
    if not paras:
        return ""
    return "## %s\n\n%s\n\n%s\n\n" % (METHOD_HEADING, METHOD_INTRO, "\n\n".join(paras))


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def status(conn: sqlite3.Connection, config_path: Optional[Path] = None,
           reviews_dir: Optional[Path] = None) -> Dict[str, Dict]:
    """The gate for every measure without computing or writing anything."""
    out = {}
    for spec in load_config(config_path):
        try:
            mod = import_measure(spec["module"])
        except Exception as exc:  # noqa: BLE001
            out[spec["id"]] = {"status": "off", "reason": "failed: %s: %s" % (type(exc).__name__, exc)}
            continue
        b = _block(spec, mod)
        if mod is None:
            b["reason"] = "not built"
        elif not spec["enabled"]:
            b["reason"] = "switched off in sources/measures.yaml"
        else:
            b["inputs_present"] = int(mod.inputs_present(conn) or 0)
            if b["inputs_present"] < spec["min_inputs"]:
                b["reason"] = "waiting for inputs: %d of at least %d" % (b["inputs_present"], spec["min_inputs"])
            else:
                b["status"], b["check"], b["reason"] = assess(mod, spec["id"], reviews_dir)
        out[spec["id"]] = b
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Research measures and their hand checks")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="the gate for every measure, without computing")
    s = sub.add_parser("sample", help="draw a hand check sample into data/reviews")
    s.add_argument("measure")
    s.add_argument("--n", type=int, default=60)
    s.add_argument("--seed", default=None)
    s.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    from pipeline import store
    conn = store.connect()
    if args.cmd == "sample":
        spec = next((m for m in load_config() if m["id"] == args.measure), None)
        if spec is None:
            raise SystemExit("unknown measure %s" % args.measure)
        mod = import_measure(spec["module"])
        if mod is None:
            raise SystemExit("measure %s is not built" % args.measure)
        seed = args.seed or "%s-%s" % (args.measure, dt.datetime.now(dt.timezone.utc).date().isoformat())
        paths = mod.draw_sample(conn, args.n, seed, Path(args.out) if args.out else REVIEWS_DIR)
        print("wrote %s" % ", ".join(str(p) for p in (paths or [])))
    else:
        print(json.dumps(status(conn), indent=1, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
