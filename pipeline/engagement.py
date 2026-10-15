"""Engagement: how far state origin articles travel on public Facebook and Instagram posts, from aggregate
counts the owner looks up by hand in the Meta Content Library. Prepared and off (sources/measures.yaml).

No code here touches Meta, Facebook, Instagram, a login, a token, a credential or an API. The tracker
writes a list of public state origin addresses for the owner to look up himself, and imports back only
aggregate counts, in a file whose header is a strict whitelist (sources/engagement_schema.json):

  key_type      url_hash (one state origin article) or outlet_month (one outlet over one month)
  key           the url_hash column of the urls file, or <outlet_id>:<YYYY-MM>
  platform      facebook or instagram
  period_start, period_end   days only
  posts_n, reactions, shares, comments, views (optional)   counts, summed over the posts found
  suppressed    1 when posts_n was between 1 and ENGAGEMENT_MIN_CELL less one: no count is kept
  query_date    the day the library was queried
  mcl_query_ref the owner's reference for the query, as in data/engagement/QUERY_LOG.md

The validator refuses extra columns, any link to a platform (facebook.com, instagram.com, fb.com and the
like) or any link at all, account handles, digit strings longer than 15 (post and account ids), any time
finer than a day, text over 64 characters, and small cells: an unsuppressed row whose posts_n is between 1
and ENGAGEMENT_MIN_CELL less one. A suppressed row carries no count.

How the counts reach the export: the runner's database is the canonical one, so a file imported on the
owner's machine alone would be lost. The coded file is committed as data/engagement/counts_<range>.csv,
and compute() imports every committed counts file, in name order, at each export. Imports are idempotent:
a row already stored from the same file is left alone, and a row a later file gives new figures for (same
key, platform, period and query date) is replaced. Each stored row carries the sha256 of its file.

Command line:
  python -m pipeline.engagement urls --since YYYY-MM-DD --until YYYY-MM-DD [--out DIR] [--db PATH]
  python -m pipeline.engagement validate <csv> [--min-cell K]
  python -m pipeline.engagement import <csv> [--min-cell K] [--db PATH]
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import ipaddress
import json
import random
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from pipeline import config, measures, registry, store

MEASURE_ID = "engagement"
VERSION = "1"

# The smallest number of posts a kept cell may count (Meta's output rules forbid publishing small cells).
# A row with posts_n from 1 to ENGAGEMENT_MIN_CELL - 1 must be marked suppressed and carry no count.
ENGAGEMENT_MIN_CELL = int(getattr(config, "ENGAGEMENT_MIN_CELL", 5))
MAX_TEXT = 64
MAX_DIGITS = 15
SCHEMA_PATH = config.ROOT / "sources" / "engagement_schema.json"
DATA_DIR = config.ROOT / "data" / "engagement"
COUNTS_GLOB = "counts_*.csv"
URLS_FIELDS = ["article_id", "url_hash", "url", "domain", "outlet_id", "country", "route", "date", "headline"]
COUNT_FIELDS = ("posts_n", "reactions", "shares", "comments", "views")
SAMPLE_N = 20               # the hand check repeats this many lookups at most: each one is done by hand
CHECK_THRESHOLD = 0.8
SAMPLE_FIELDS = ["sample_id", "key_type", "key", "platform", "period_start", "period_end", "url", "headline",
                 "outlet_id", "posts_band", "note"]

_PLATFORM_LINK = re.compile(
    r"(facebook|instagram|fbcdn|fbsbx|messenger|threads|whatsapp|meta)\.(com|net)|\bfb\.(com|me|watch|gg)\b"
    r"|\binstagr\.am\b|\bm\.me\b|\bwa\.me\b|\bfacebookcorewwwi\b", re.I)
_ANY_LINK = re.compile(r"://|\bwww\.|\.(com|net|org|it|uk|de|fr|es)/", re.I)
_LONG_DIGITS = re.compile(r"\d{%d,}" % (MAX_DIGITS + 1))
_TIME = re.compile(r"\b\d{1,2}:\d{2}\b|\d{4}-\d{2}-\d{2}[T ]\d|\b\d{1,2}\s*(am|pm)\b|\bUTC\b", re.I)
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# The import schema
# ---------------------------------------------------------------------------

def load_schema(path: Optional[Path] = None) -> Dict:
    """The row schema: {columns, required, optional, props} from sources/engagement_schema.json."""
    with open(Path(path or SCHEMA_PATH), encoding="utf-8") as fh:
        raw = json.load(fh)
    items = raw["items"]
    props = items["properties"]
    required = list(items["required"])
    return {"columns": list(props), "required": required,
            "optional": [c for c in props if c not in required], "props": props}


def _field_problem(spec: Dict, col: str, value: str) -> Optional[str]:
    if "enum" in spec and value not in spec["enum"]:
        return "%s must be one of %s, not %r" % (col, ", ".join(spec["enum"]), value)
    if "maxLength" in spec and len(value) > spec["maxLength"]:
        return "%s is over %d characters" % (col, spec["maxLength"])
    if "pattern" in spec and not re.search(spec["pattern"], value):
        return "%s %r is not in the expected form (%s)" % (col, value, spec.get("description") or spec["pattern"])
    return None


def cell_problem(col: str, value: str, spec: Optional[Dict] = None) -> Optional[str]:
    """Why one value may not be imported, or None. The checks that protect privacy come first."""
    if len(value) > MAX_TEXT:
        return "text over %d characters (%d)" % (MAX_TEXT, len(value))
    if _PLATFORM_LINK.search(value):
        return "a link to a platform (facebook.com, instagram.com, fb.com and the like): counts only, never a link"
    if _ANY_LINK.search(value):
        return "a link: the file holds counts only, never an address"
    if "@" in value:
        return "an @ sign, which reads as an account handle or an email address"
    if _LONG_DIGITS.search(value) and not (col == "key" and _HEX32.match(value)):
        return "a digit string longer than %d, which reads as a post or account id" % MAX_DIGITS
    if _TIME.search(value):
        return "a time finer than a day: days only"
    if spec:
        return _field_problem(spec, col, value)
    return None


def _date(s: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(s) if _DAY.match(s or "") else None
    except ValueError:
        return None


def _int(s: str) -> Optional[int]:
    return int(s) if s not in (None, "") else None


def validate_rows(header: List[str], rows: List[List[str]], min_cell: Optional[int] = None,
                  schema: Optional[Dict] = None, today: Optional[dt.date] = None) -> Dict:
    """Check a counts file already read as a header and rows of strings. Returns
    {ok, errors: [(line, column, message)], warnings, rows: [parsed dicts], min_cell}. Line numbers are
    the file's, so the header is line 1."""
    k = int(min_cell if min_cell is not None else ENGAGEMENT_MIN_CELL)
    sch = schema or load_schema()
    today = today or dt.datetime.now(dt.timezone.utc).date()
    errors: List[Tuple[int, str, str]] = []
    out: List[Dict] = []
    head = [h.strip() for h in header]
    extra = [h for h in head if h not in sch["columns"]]
    if extra:
        errors.append((1, ",".join(extra), "column not allowed: %s. The header is a strict whitelist: %s"
                       % (", ".join(repr(h) for h in extra), ", ".join(sch["columns"]))))
    dup = sorted({h for h in head if head.count(h) > 1})
    if dup:
        errors.append((1, ",".join(dup), "column given more than once: %s" % ", ".join(dup)))
    missing = [c for c in sch["required"] if c not in head]
    if missing:
        errors.append((1, ",".join(missing), "required column missing: %s" % ", ".join(missing)))
    if errors:
        return {"ok": False, "errors": errors, "warnings": [], "rows": [], "min_cell": k}
    if not rows:
        errors.append((2, "", "no rows"))
    seen: Dict[Tuple, int] = {}
    for i, raw in enumerate(rows, start=2):
        if len(raw) != len(head):
            errors.append((i, "", "%d values for %d columns" % (len(raw), len(head))))
            continue
        vals = {h: (v or "").strip() for h, v in zip(head, raw)}
        bad = False
        for col in head:
            p = cell_problem(col, vals[col], sch["props"][col])
            if p:
                errors.append((i, col, p))
                bad = True
        if bad:
            continue
        for col in sch["optional"]:
            vals.setdefault(col, "")
        r = {c: vals[c] for c in sch["columns"]}
        for c in COUNT_FIELDS:
            r[c] = _int(vals.get(c, ""))
        r["suppressed"] = int(vals["suppressed"])
        ps, pe, q = _date(r["period_start"]), _date(r["period_end"]), _date(r["query_date"])
        if not ps or not pe or not q:
            errors.append((i, "period_start" if not ps else "period_end" if not pe else "query_date",
                           "not a calendar day"))
            continue
        if ps > pe:
            errors.append((i, "period_end", "period_end %s is before period_start %s" % (pe, ps)))
        if q < ps:
            errors.append((i, "query_date", "query_date %s is before the period it counts" % q))
        if q > today + dt.timedelta(days=1):
            errors.append((i, "query_date", "query_date %s is in the future" % q))
        if r["key_type"] == "url_hash" and not _HEX32.match(r["key"]):
            errors.append((i, "key", "a url_hash key is the 32 character url_hash from the urls file"))
        if r["key_type"] == "outlet_month":
            m = re.fullmatch(r"([a-z]{2,3}_[a-z0-9_]+):(\d{4}-\d{2})", r["key"])
            if not m:
                errors.append((i, "key", "an outlet_month key is <outlet_id>:<YYYY-MM>"))
            elif not (r["period_start"][:7] == m.group(2) == r["period_end"][:7]):
                errors.append((i, "period_start", "the period of an outlet_month row lies inside its month %s"
                               % m.group(2)))
        counts = [c for c in COUNT_FIELDS if r[c] is not None]
        if r["suppressed"] == 1:
            if counts:
                errors.append((i, ",".join(counts), "a suppressed row carries no count: leave %s blank"
                               % ", ".join(counts)))
        else:
            n = r["posts_n"]
            if n is None:
                errors.append((i, "posts_n", "posts_n is required unless the row is suppressed"))
            elif 1 <= n <= k - 1:
                errors.append((i, "posts_n", "small cell: posts_n %d is between 1 and %d but suppressed is 0. "
                               "Set suppressed to 1 and leave the counts blank" % (n, k - 1)))
            elif n == 0:
                nz = [c for c in ("reactions", "shares", "comments", "views") if r[c]]
                if nz:
                    errors.append((i, ",".join(nz), "posts_n is 0, so %s must be 0 or blank" % ", ".join(nz)))
            else:
                need = [c for c in ("reactions", "shares", "comments") if r[c] is None]
                if need:
                    errors.append((i, ",".join(need), "%s required when posts_n is given" % ", ".join(need)))
        uk = (r["key_type"], r["key"], r["platform"], r["period_start"], r["period_end"], r["query_date"])
        if uk in seen:
            errors.append((i, "key", "the same key, platform, period and query date as line %d" % seen[uk]))
        seen[uk] = i
        out.append(r)
    return {"ok": not errors, "errors": errors, "warnings": [], "rows": out if not errors else [], "min_cell": k}


def read_csv(path: Path) -> Tuple[List[str], List[List[str]], str]:
    """(header, rows, sha256 of the file's bytes). Blank lines are skipped."""
    data = Path(path).read_bytes()
    text = data.decode("utf-8-sig")
    rdr = csv.reader(io.StringIO(text, newline=""))
    lines = [row for row in rdr if any((c or "").strip() for c in row)]
    header = lines[0] if lines else []
    return header, lines[1:], hashlib.sha256(data).hexdigest()


def _state_origin_hashes(conn) -> set:
    try:
        return {r[0] for r in conn.execute(
            """SELECT a.url_hash FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
               WHERE a.gate_relevant=1 AND c.category='A'""")}
    except sqlite3.OperationalError:
        return set()


def validate_file(path: Path, min_cell: Optional[int] = None, conn=None, outlets: Optional[List[Dict]] = None) -> Dict:
    """validate_rows on a file, plus its path and sha256. With a connection, url_hash keys that are not a
    current state origin article, and outlet ids not in the registry, are warnings (an article can be
    reclassified between the urls file and the import); they are imported and counted only while they are
    state origin."""
    header, rows, sha = read_csv(path)
    rep = validate_rows(header, rows, min_cell)
    rep.update({"path": str(path), "sha256": sha, "n_rows": len(rows)})
    if rep["ok"] and conn is not None:
        known = _state_origin_hashes(conn)
        unknown = sorted({r["key"] for r in rep["rows"] if r["key_type"] == "url_hash" and r["key"] not in known})
        if unknown:
            rep["warnings"].append("%d url_hash keys are not a current state origin article in this database: %s"
                                   % (len(unknown), ", ".join(unknown[:5]) + (" and more" if len(unknown) > 5 else "")))
    if rep["ok"] and outlets is not None:
        ids = {o["id"] for o in outlets}
        bad = sorted({r["key"].split(":")[0] for r in rep["rows"] if r["key_type"] == "outlet_month"} - ids)
        if bad:
            rep["warnings"].append("outlet ids not in sources/outlets.yaml: %s" % ", ".join(bad))
    return rep


def format_report(rep: Dict, limit: int = 60) -> str:
    """A plain report of a validation, for the terminal."""
    lines = ["%s: %d rows, minimum cell %d" % (rep.get("path", "file"), rep.get("n_rows", len(rep.get("rows") or [])),
                                              rep.get("min_cell", ENGAGEMENT_MIN_CELL))]
    if rep.get("sha256"):
        lines.append("sha256 %s" % rep["sha256"])
    if rep["ok"]:
        rows = rep["rows"]
        sup = sum(1 for r in rows if r["suppressed"])
        lines.append("ACCEPTED: %d rows (%d url_hash, %d outlet_month, %d suppressed)" % (
            len(rows), sum(1 for r in rows if r["key_type"] == "url_hash"),
            sum(1 for r in rows if r["key_type"] == "outlet_month"), sup))
    else:
        lines.append("REJECTED: %d problems. Nothing was imported." % len(rep["errors"]))
        for line, col, msg in rep["errors"][:limit]:
            lines.append("  line %d%s: %s" % (line, ", column " + col if col else "", msg))
        if len(rep["errors"]) > limit:
            lines.append("  and %d more" % (len(rep["errors"]) - limit))
    for w in rep.get("warnings") or []:
        lines.append("warning: %s" % w)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

_UNIQUE = ("key_type", "key", "platform", "period_start", "period_end", "query_date")
_VALUES = ("posts_n", "reactions", "shares", "comments", "views", "suppressed", "mcl_query_ref")


def store_rows(conn, rows: List[Dict], sha: str, now: Optional[str] = None) -> Dict[str, int]:
    """Write validated rows to engagement_aggregates, idempotently. Returns {inserted, unchanged, replaced}."""
    now = now or store.utcnow()
    out = {"inserted": 0, "unchanged": 0, "replaced": 0}
    for r in rows:
        cur = conn.execute("SELECT * FROM engagement_aggregates WHERE %s" % " AND ".join("%s=?" % c for c in _UNIQUE),
                           [r[c] for c in _UNIQUE]).fetchone()
        if cur is None:
            cols = list(_UNIQUE) + list(_VALUES) + ["file_sha256", "imported_at"]
            conn.execute("INSERT INTO engagement_aggregates(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                         [r[c] for c in _UNIQUE + _VALUES] + [sha, now])
            out["inserted"] += 1
        elif cur["file_sha256"] == sha or all(cur[c] == r[c] for c in _VALUES):
            out["unchanged"] += 1
        else:
            conn.execute("UPDATE engagement_aggregates SET %s, file_sha256=?, imported_at=? WHERE id=?"
                         % ", ".join("%s=?" % c for c in _VALUES), [r[c] for c in _VALUES] + [sha, now, cur["id"]])
            out["replaced"] += 1
    return out


def import_file(conn, path: Path, min_cell: Optional[int] = None, outlets: Optional[List[Dict]] = None) -> Dict:
    """Validate a counts file and, only when it passes, store it. Returns the report with the write counts."""
    rep = validate_file(path, min_cell, conn, outlets)
    if not rep["ok"]:
        return rep
    rep["stored"] = store_rows(conn, rep["rows"], rep["sha256"])
    conn.commit()
    return rep


def committed_files(data_dir: Optional[Path] = None) -> List[Path]:
    """The committed counts files, in name order (a later name wins where two give the same cell)."""
    return sorted(Path(data_dir or DATA_DIR).glob(COUNTS_GLOB))


# ---------------------------------------------------------------------------
# The URL list for the owner's lookups
# ---------------------------------------------------------------------------

def _public_url(url: str) -> Optional[str]:
    """The domain of an address anyone can open, or None: http or https, a named host, no user part,
    not a local or numeric host, and not a platform."""
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host or "." not in host or p.username or p.password:
        return None
    if host == "localhost" or host.endswith((".local", ".localhost", ".test", ".internal")):
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    if _PLATFORM_LINK.search(host):
        return None
    return host[4:] if host.startswith("www.") else host


def _outlets(ctx_outlets=None) -> List[Dict]:
    if ctx_outlets is not None:
        return ctx_outlets
    try:
        return registry.load_outlets()
    except Exception as exc:  # noqa: BLE001  a registry problem leaves countries as stored
        _warn("engagement: outlets not loaded (%s)" % exc)
        return []


def _country_map(outlets: List[Dict]) -> Dict[str, str]:
    return {o["id"]: registry.export_country(o) for o in outlets}


def _day_sql() -> str:
    return "COALESCE(a.day_of_record, substr(a.discovered_at, 1, 10))"


def url_rows(conn, since: str, until: str, outlets: Optional[List[Dict]] = None) -> List[Dict]:
    """Current state origin articles on the days since <= day <= until, public addresses only."""
    cmap = _country_map(_outlets(outlets))
    rows = conn.execute(
        """SELECT a.id, a.url, a.url_hash, a.outlet_id, a.country, a.title, c.route, %s AS day
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 AND c.category='A' AND %s>=? AND %s<=?
           ORDER BY day, a.id""" % (_day_sql(), _day_sql(), _day_sql()), (since, until)).fetchall()
    out = []
    for r in rows:
        dom = _public_url(r["url"])
        if not dom:
            continue
        out.append({"article_id": r["id"], "url_hash": r["url_hash"], "url": r["url"].strip(), "domain": dom,
                    "outlet_id": r["outlet_id"], "country": cmap.get(r["outlet_id"], r["country"]),
                    "route": r["route"] or "unattributed", "date": r["day"],
                    "headline": " ".join((r["title"] or "").split())})
    return out


def write_urls(conn, since: str, until: str, out_dir: Optional[Path] = None,
               outlets: Optional[List[Dict]] = None) -> Tuple[Path, int]:
    if not (_date(since) and _date(until)) or since > until:
        raise ValueError("--since and --until are days, YYYY-MM-DD, with since on or before until")
    rows = url_rows(conn, since, until, outlets)
    d = Path(out_dir or DATA_DIR)
    d.mkdir(parents=True, exist_ok=True)
    path = d / ("urls_%s_%s.csv" % (since, until))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=URLS_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path, len(rows)


# ---------------------------------------------------------------------------
# The measure
# ---------------------------------------------------------------------------

def _table_rows(conn) -> int:
    try:
        return conn.execute("SELECT COUNT(*) FROM engagement_aggregates").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def inputs_present(conn) -> int:
    """The number of stored engagement rows. Before the first import into this database, the rows waiting
    in committed counts files that pass validation, so the export can import them. Never writes."""
    n = _table_rows(conn)
    if n:
        return n
    waiting = 0
    for f in committed_files():
        try:
            rep = validate_file(f)
        except Exception:  # noqa: BLE001  an unreadable file is reported by compute
            continue
        if rep["ok"]:
            waiting += len(rep["rows"])
    return waiting


def compute(conn, ctx, deadline) -> Dict:
    """Import every committed counts file, in name order. A file that fails validation is skipped and
    named; nothing else is written."""
    out = {"files": 0, "inserted": 0, "unchanged": 0, "replaced": 0, "rejected": []}
    for f in committed_files():
        if time.monotonic() >= deadline:
            out["stopped_at_deadline"] = True
            break
        rep = validate_file(f)
        if not rep["ok"]:
            _warn("engagement: %s rejected\n%s" % (f.name, format_report(rep, limit=10)))
            out["rejected"].append(f.name)
            continue
        got = store_rows(conn, rep["rows"], rep["sha256"])
        out["files"] += 1
        for k2, v in got.items():
            out[k2] += v
    return out


def latest_rows(conn) -> List[sqlite3.Row]:
    """One row per key and platform: the latest query, then the latest and widest period."""
    rows = conn.execute(
        """SELECT * FROM engagement_aggregates
           ORDER BY key_type, key, platform, query_date DESC, period_end DESC, period_start ASC, id DESC""").fetchall()
    out, seen = [], set()
    for r in rows:
        k2 = (r["key_type"], r["key"], r["platform"])
        if k2 in seen:
            continue
        seen.add(k2)
        out.append(r)
    return out


def _empty_group() -> Dict:
    return {"urls": 0, "found": 0, "suppressed": 0, "posts_n": 0, "reactions": 0, "shares": 0, "comments": 0,
            "views": 0, "views_given": 0}


def _add(g: Dict, r) -> None:
    g["suppressed"] += 1 if r["suppressed"] else 0
    for c in ("posts_n", "reactions", "shares", "comments"):
        g[c] += r[c] or 0
    if r["views"] is not None:
        g["views"] += r["views"]
        g["views_given"] += 1


def _share(a: int, b: int) -> Optional[float]:
    return round(a / b, 4) if b else None


def _state_origin_index(conn, outlets: List[Dict]) -> Dict[str, Dict]:
    cmap = _country_map(outlets)
    out = {}
    for r in conn.execute(
            """SELECT a.url_hash, a.url, a.outlet_id, a.country, c.route, %s AS day
               FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
               WHERE a.gate_relevant=1 AND c.category='A'""" % _day_sql()):
        out[r["url_hash"]] = {"outlet_id": r["outlet_id"], "country": cmap.get(r["outlet_id"], r["country"]),
                              "route": r["route"] or "unattributed", "day": r["day"],
                              "public": bool(_public_url(r["url"]))}
    return out


def build_export(conn, ctx) -> Dict:
    """engagement.json: totals per platform, per route, per outlet and per country, the share of the state
    origin addresses looked up that were found on a public post, and the outlet month rows. Never a
    single article's counts, an address or a headline."""
    ctx = ctx or {}
    outlets = _outlets(ctx.get("outlets"))
    names = {o["id"]: o["name"] for o in outlets}
    cmap = _country_map(outlets)
    from pipeline.export import ROUTE_LABELS
    so = _state_origin_index(conn, outlets)
    rows = latest_rows(conn)
    url_rows_ = [r for r in rows if r["key_type"] == "url_hash"]
    current = [r for r in url_rows_ if r["key"] in so]
    totals = {p: _empty_group() for p in ("facebook", "instagram", "all")}
    groups = {"route": defaultdict(_empty_group), "outlet": defaultdict(_empty_group), "country": defaultdict(_empty_group)}
    found_keys: Dict[str, Dict[str, set]] = {g: defaultdict(set) for g in groups}
    looked: Dict[str, Dict[str, set]] = {g: defaultdict(set) for g in groups}
    found_all, looked_all = set(), set()
    for r in current:
        a = so[r["key"]]
        found = bool(r["suppressed"]) or (r["posts_n"] or 0) > 0
        for p in (r["platform"], "all"):
            _add(totals[p], r)
        looked_all.add(r["key"])
        if found:
            found_all.add(r["key"])
        for g, gk in (("route", a["route"]), ("outlet", a["outlet_id"]), ("country", a["country"])):
            _add(groups[g][gk], r)
            looked[g][gk].add(r["key"])
            if found:
                found_keys[g][gk].add(r["key"])
    by_platform_found = {p: len({r["key"] for r in current if r["platform"] == p and
                                 (r["suppressed"] or (r["posts_n"] or 0) > 0)}) for p in ("facebook", "instagram")}
    for p in ("facebook", "instagram"):
        totals[p]["urls"] = len({r["key"] for r in current if r["platform"] == p})
        totals[p]["found"] = by_platform_found[p]
    totals["all"]["urls"], totals["all"]["found"] = len(looked_all), len(found_all)

    def listing(g, extra):
        out = []
        for gk, v in groups[g].items():
            v = dict(v)
            v["urls"], v["found"] = len(looked[g][gk]), len(found_keys[g][gk])
            v["share_found"] = _share(v["found"], v["urls"])
            v.update(extra(gk))
            out.append(v)
        out.sort(key=lambda e: (-e["posts_n"], -e["found"], str(e.get("id"))))
        return out

    periods = [(r["period_start"], r["period_end"]) for r in url_rows_]
    start = min((p[0] for p in periods), default=None)
    end = max((p[1] for p in periods), default=None)
    # the denominator of looked_up_share: what the urls file would list for the period looked up
    in_window = sum(1 for a in so.values() if a["public"] and start and start <= (a["day"] or "") <= end)
    outlet_months = []
    for r in rows:
        if r["key_type"] != "outlet_month":
            continue
        oid, month = r["key"].split(":", 1)
        o = next((x for x in outlets if x["id"] == oid), None)
        outlet_months.append({"outlet_id": oid, "name": names.get(oid, oid),
                              "country": registry.export_country(o) if o else None, "month": month,
                              "platform": r["platform"], "suppressed": bool(r["suppressed"]),
                              "posts_n": r["posts_n"], "reactions": r["reactions"], "shares": r["shares"],
                              "comments": r["comments"], "views": r["views"], "query_date": r["query_date"]})
    outlet_months.sort(key=lambda e: (e["month"], e["outlet_id"], e["platform"]))
    qd = sorted({r["query_date"] for r in rows})
    return {
        "generated_at": store.utcnow(),
        "version": VERSION,
        "min_cell": ENGAGEMENT_MIN_CELL,
        "platforms": ["facebook", "instagram"],
        "query_dates": {"first": qd[0] if qd else None, "last": qd[-1] if qd else None},
        "period": {"start": start, "end": end},
        "coverage": {
            "state_origin_urls": in_window,
            "looked_up": len(looked_all),
            "looked_up_share": _share(len(looked_all), in_window),
            "found": len(found_all),
            "share_found": _share(len(found_all), len(looked_all)),
            "no_longer_state_origin": len({r["key"] for r in url_rows_ if r["key"] not in so}),
        },
        "totals": totals,
        "by_route": listing("route", lambda k: {"id": k, "label": ROUTE_LABELS.get(k, k)}),
        "by_outlet": listing("outlet", lambda k: {"id": k, "name": names.get(k, k),
                                                  "country": cmap.get(k)}),
        "by_country": listing("country", lambda k: {"id": k}),
        "outlet_months": outlet_months,
    }


def meta_summary(conn, ctx) -> Dict:
    rows = latest_rows(conn)
    so = _state_origin_hashes(conn)
    cur = {r["key"] for r in rows if r["key_type"] == "url_hash" and r["key"] in so}
    found = {r["key"] for r in rows if r["key_type"] == "url_hash" and r["key"] in so
             and (r["suppressed"] or (r["posts_n"] or 0) > 0)}
    qd = sorted({r["query_date"] for r in rows})
    return {"cells": len(rows), "urls_looked_up": len(cur), "urls_found": len(found),
            "share_found": _share(len(found), len(cur)), "min_cell": ENGAGEMENT_MIN_CELL,
            "first_query": qd[0] if qd else None, "last_query": qd[-1] if qd else None}


def methodology_text(block) -> str:
    k = ENGAGEMENT_MIN_CELL
    paras = [
        "This measure asks how far state origin articles travel after they are published: how many public "
        "Facebook and Instagram posts linked to each one, and how many reactions, shares and comments those "
        "posts drew. It measures reach, not supply. Every other figure on this site counts what outlets "
        "publish; this one counts what happened afterwards to the part of it that was looked up, and it says "
        "nothing about whether anyone read the article itself.",
        "The tracker writes a list of the public addresses and headlines of state origin articles over a span "
        "of days. The owner looks each address up by hand in the Meta Content Library, a research tool run by "
        "Meta, and brings back aggregate counts only, per article or per outlet and month and per platform: "
        "the number of public posts found and their summed reactions, shares, comments and, where given, "
        "views. No code of this project connects to Meta. No post, account, handle, link to a platform or "
        "time of day is stored. A cell of 1 to %d posts is kept only as suppressed, with no count, and every "
        "file is checked against these rules and refused whole if any row breaks them. The files are kept in "
        "the repository with a log of the queries, so each figure can be traced to a query." % (k - 1),
        "The limits are large. The library covers public posts only, so private groups, messages and every "
        "other platform are missing. Only posts that carry the address are found, so the same text shared "
        "without a link, or under another address, is missed. Counts keep growing after the query date, so a "
        "later query reads higher. Suppressed cells add nothing to the sums, which are therefore lower bounds. "
        "Facebook and Instagram counts are not comparable with each other. The articles looked up are the "
        "state origin articles this tracker found, so the measure inherits its outlet list and its "
        "classification, and an article reclassified since its lookup is left out.",
        "The hand check repeats up to %d lookups chosen at random, without seeing the stored figures, and "
        "records the number of posts in bands: none, 1 to %d, %d to 19, 20 to 99, and 100 or more. Exact "
        "counts are not compared, because a later query also finds the posts made since the first one; the "
        "check asks whether each stored count was keyed to the right article and is of the right size. It "
        "passes when at least %d percent of the bands agree." % (SAMPLE_N, k - 1, k, int(CHECK_THRESHOLD * 100)),
    ]
    return "\n\n".join(paras)


# ---------------------------------------------------------------------------
# The hand check
# ---------------------------------------------------------------------------

def bands(k: Optional[int] = None) -> List[str]:
    k = int(k or ENGAGEMENT_MIN_CELL)
    return ["none", "few", "%d_19" % k, "20_99", "100_plus"]


def band_of(posts_n: Optional[int], suppressed, k: Optional[int] = None) -> str:
    k = int(k or ENGAGEMENT_MIN_CELL)
    b = bands(k)
    if suppressed:
        return b[1]
    n = posts_n or 0
    if n == 0:
        return b[0]
    if n < k:
        return b[1]
    if n < 20:
        return b[2]
    if n < 100:
        return b[3]
    return b[4]


def draw_sample(conn, n, seed, out_dir):
    """Up to SAMPLE_N stored cells (current state origin articles and outlet months) for the owner to look
    up again in the library, blind. The CSV shows the address and the period, never the stored figures;
    the coder writes the band of posts found in posts_band. The key holds the stored bands."""
    n = max(1, min(int(n or SAMPLE_N), SAMPLE_N))
    so = _state_origin_hashes(conn)
    rows = [r for r in latest_rows(conn) if r["key_type"] == "outlet_month" or r["key"] in so]
    rng = random.Random(str(seed))
    pick = rng.sample(rows, min(n, len(rows)))
    arts = {}
    hashes = [r["key"] for r in pick if r["key_type"] == "url_hash"]
    if hashes:
        for a in conn.execute("SELECT url_hash, url, title, outlet_id FROM articles WHERE url_hash IN (%s)"
                              % ",".join("?" * len(hashes)), hashes):
            arts[a["url_hash"]] = a
    out, answers = [], {}
    for i, r in enumerate(pick, start=1):
        sid = "e%02d" % i
        a = arts.get(r["key"])
        out.append({"sample_id": sid, "key_type": r["key_type"], "key": r["key"], "platform": r["platform"],
                    "period_start": r["period_start"], "period_end": r["period_end"],
                    "url": a["url"] if a else "", "headline": " ".join(((a["title"] if a else "") or "").split()),
                    "outlet_id": a["outlet_id"] if a else r["key"].split(":")[0], "posts_band": "", "note": ""})
        answers[sid] = band_of(r["posts_n"], r["suppressed"])
    key = {"answers": answers, "bands": bands(), "min_cell": ENGAGEMENT_MIN_CELL, "threshold": CHECK_THRESHOLD,
           "instructions": "Look each row up again in the Meta Content Library for the same platform and period, "
                           "and write the band of public posts found in posts_band: " + ", ".join(bands()) + "."}
    return measures.write_sample(Path(out_dir), MEASURE_ID, out, SAMPLE_FIELDS, key, version=VERSION, seed=seed)


def score_check(rows, key) -> Dict:
    """Band agreement between the stored cells and the coder's repeated lookups."""
    answers = (key or {}).get("answers") or {}
    valid = set((key or {}).get("bands") or bands((key or {}).get("min_cell")))
    coded = agree = 0
    for r in rows or []:
        sid = (r.get("sample_id") or "").strip()
        got = (r.get("posts_band") or "").strip().lower()
        if sid not in answers or got not in valid:
            continue
        coded += 1
        agree += 1 if got == answers[sid] else 0
    n = len(answers)
    return measures.check_result("band agreement", round(agree / coded, 4) if coded else None,
                                 float((key or {}).get("threshold") or CHECK_THRESHOLD), n, coded)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Engagement: the URL list for hand lookups, and the counts import")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("urls", help="write data/engagement/urls_<since>_<until>.csv of state origin addresses")
    u.add_argument("--since", required=True)
    u.add_argument("--until", required=True)
    u.add_argument("--out", default=None)
    u.add_argument("--db", default=None)
    v = sub.add_parser("validate", help="check a counts file and print a report")
    v.add_argument("csv")
    v.add_argument("--min-cell", type=int, default=None)
    i = sub.add_parser("import", help="validate a counts file, then store it in engagement_aggregates")
    i.add_argument("csv")
    i.add_argument("--min-cell", type=int, default=None)
    i.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "validate":
        rep = validate_file(Path(args.csv), args.min_cell)
        print(format_report(rep))
        return 0 if rep["ok"] else 1
    conn = store.connect(Path(args.db)) if args.db else store.connect()
    if args.cmd == "urls":
        path, n = write_urls(conn, args.since, args.until, Path(args.out) if args.out else None)
        print("wrote %d public state origin addresses to %s" % (n, path))
        return 0
    rep = import_file(conn, Path(args.csv), args.min_cell, _outlets())
    print(format_report(rep))
    if not rep["ok"]:
        return 1
    s = rep["stored"]
    print("stored: %d new, %d unchanged, %d replaced" % (s["inserted"], s["unchanged"], s["replaced"]))
    try:
        Path(args.csv).resolve().relative_to(DATA_DIR.resolve())
        if not Path(args.csv).name.startswith("counts_"):
            print("note: the export imports only data/engagement/counts_*.csv; rename the file to match")
    except ValueError:
        print("note: this import reached this database only. Commit the file as data/engagement/counts_<range>.csv, "
              "with its line in QUERY_LOG.md, so the export imports it on the runner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
