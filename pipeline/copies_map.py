"""Same text, many countries: the state texts run by outlets in the most countries, for the site's
copies world map (docs/data/copies_map.json). Display only: nothing here changes a label, a count, a
denominator or the outlets monitored, and no figure on the main map, the ranked list, the top 10 or the
findings reads this file.

What one state text is. Each current state origin placement (pipeline/export.article_rows: one row per
placement, so a shared widget's copy at a second masthead, one page at two addresses and the parts of a
numbered series are left out, and an undetermined label, pipeline/model_only.py, is in no count) belongs
to its underlying item: its title group joined through body links and language editions
(body_dups.item_groups), and joined again through the original the alteration measure paired it with
(copies.attach_items: copies paired with one reference item, or with one earlier monitored copy, are one
text). A translation shares no original with the text it translates (pairing never crosses languages), so
it counts as a text of its own.

Counting. A text counts each outlet once, at the day of record of its earliest copy there (a shared
widget is one carrier, so it counts once per country, as everywhere on the site), and each country once.
Release wires are the worldwide distribution stratum (WLD): listed with the outlets that ran a text, never
a country, never drawn on the map. A text is shared when outlets in at least MIN_COUNTRIES countries ran
it. The ranked lists hold the TOP_N shared texts by number of countries, then outlets, then the earliest
first seen day, over the 30 complete days the site's map opens on (export.complete_window_30d: copies whose
day is in the window) and over all time.

Agency. The state publishers the copies' labels name (copies.credits_of over signatures, cited sources,
the model's triggers and a release headline's issuer; an uncredited copy takes the credit of the credited
item it repeats), and the publisher of the reference original a copy was paired with. The text's agency is
the one named by most of its copies, the reference original's publisher on a tie. A text no copy credits
is "not named", unless its route names the kind of author: a diplomat's signed article is "Chinese diplomat", a
release a distribution stamp shows is "release by a Chinese state body". A Xinhua Silk Road release is shown
as Xinhua Silk Road, Xinhua's commercial release service, not as Xinhua; a "Xinhua" that only the name of
Jiangsu's Xinhua Daily Media Group gave is dropped (refine_credits).

Alteration bands. Where a copy was scored (state_copies.band, pipeline/copies.py) its band is listed with
it, only while the alteration measure is provisional or checked, and the file carries the measure's status
so the site marks it provisional. Bands are never a reason a copy is in or out.

Headlines. Only those the site already shows: a text's headline is the title of one of its copies listed
in the country article files (articles/ISO3.json), else none. No URL and no body text is ever written.
"""
import datetime as dt
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from pipeline import body_dups, copies, lookalike, placements, registry, store

VERSION = "1"
TOP_N = 25
MIN_COUNTRIES = 2
SHOW_BANDS = ("provisional", "checked")
AGENCY_NAMES = {
    "xinhua": "Xinhua", "cgtn": "CGTN", "cmg": "China Media Group (CCTV, CRI)", "china_daily": "China Daily",
    "global_times": "Global Times", "peoples_daily": "People's Daily", "china_news_service": "China News Service",
    "xinhua_silk_road": "Xinhua Silk Road (Xinhua's release service)", "diplomat": "Chinese diplomat",
    "release": "Release by a Chinese state body", "unnamed": "Not named",
}
# Routes that name the kind of state author when no publisher is named.
ROUTE_AGENCY = {"diplomatic_byline": "diplomat", "distribution_stamp": "release"}
AGENCY_RANK = {a: i for i, a in enumerate(AGENCY_NAMES)}

_CREDIT_SQL = """SELECT a.id, a.title, a.llm_trigger, c.signatures_fired, c.china_sources_cited
                 FROM classifications c JOIN articles a ON a.id = c.article_id
                 WHERE c.is_current = 1 AND c.category = 'A'"""
_COPIES_SQL = """SELECT copy_id, original_kind, ref_id, original_article_id, original_source, original_published_at, band
                 FROM state_copies WHERE current = 1"""


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr)


def empty(alteration_status: Optional[str] = None) -> Dict:
    """The file's structure with nothing in it: no state text, or a failed export."""
    scope = {"ranked": [], "shared_texts": 0, "texts": 0, "countries": {}}
    return {"version": VERSION, "min_countries": MIN_COUNTRIES, "top_n": TOP_N, "window": None,
            "alteration": {"status": alteration_status or "off", "shown": False, "bands": list(copies.BANDS)},
            "agencies": dict(AGENCY_NAMES), "outlets": {}, "texts": {},
            "scopes": {"last_30d": dict(scope), "all_time": dict(scope)}}


_XINHUA_NEWSROOM = re.compile(r"\bxinhua\b(?!\s+(?:silk\s+road|daily|finance))", re.IGNORECASE)


def refine_credits(credits: Set[str], sources: Iterable = (), title: Optional[str] = None, sigs: Iterable = ()) -> Set[str]:
    """credits_of read for display: a Xinhua Silk Road release (Xinhua's commercial release service, whose
    releases are not on Xinhua's own pages) is shown as such, and a "xinhua" that only a provincial group's name
    gave (Xinhua Daily Media Group, Jiangsu's party paper group) is not Xinhua."""
    out = set(credits or ())
    if "xinhua" not in out:
        return out
    srcs = [str(x or "") for x in (sources or [])]
    if re.match(r"\s*xinhua\s+silk\s+road", title or "", re.IGNORECASE) or any(re.search(r"xinhua\s+silk\s+road", x, re.IGNORECASE) for x in srcs):
        out.discard("xinhua")
        out.add("xinhua_silk_road")
    elif not any(_XINHUA_NEWSROOM.search(x) for x in srcs) and not any(str(g).startswith(("xinhua_", "source_xinhua")) for g in sigs or ()) \
            and not _XINHUA_NEWSROOM.search(title or ""):
        out.discard("xinhua")
    return out


def _credits(conn) -> Dict[int, Set[str]]:
    """{article id: state publishers its current state origin label names}, an uncredited copy taking
    the credit of the credited item it repeats (as copies._current_copies does)."""
    out, trig = {}, {}
    for r in conn.execute(_CREDIT_SQL):
        out[r["id"]] = refine_credits(copies.credits_of(r["signatures_fired"], r["china_sources_cited"], r["llm_trigger"], r["title"]),
                                      copies._json(r["china_sources_cited"], []), r["title"], copies._json(r["signatures_fired"], []))
        trig[r["id"]] = r["llm_trigger"]
    for aid, cr in out.items():
        if not cr:
            m = lookalike.context_of(trig[aid])
            if m and out.get(m.get("article_id")):
                out[aid] = set(out[m["article_id"]])
    return out


def _state_copies(conn) -> Dict[int, Dict]:
    try:
        return {r["copy_id"]: dict(r) for r in conn.execute(_COPIES_SQL)}
    except sqlite3.OperationalError:
        return {}


def _day_in(day: str, window: Optional[Tuple[str, str]]) -> bool:
    return window is None or (window[0] <= day < window[1])


def agency_of(counts: Counter, reference: Counter) -> str:
    """The publisher most copies name; on a tie the reference original's publisher, then the site's order."""
    if not counts and not reference:
        return "unnamed"
    keys = set(counts) | set(reference)
    return min(keys, key=lambda a: (-counts.get(a, 0), -reference.get(a, 0), AGENCY_RANK.get(a, 99), a))


def rank_key(t: Dict) -> Tuple:
    return (-t["countries"], -t["outlets"], t["first_seen"] or "", t["id"])


def scope_counts(entries: Iterable[Dict], window: Optional[Tuple[str, str]] = None) -> Tuple[int, int]:
    """(countries, outlets) over a text's entries whose day is in window; WLD is never a country."""
    es = [e for e in entries if _day_in(e["d"], window)]
    return len({e["c"] for e in es if e["c"] != registry.WORLDWIDE}), len({(e["o"], e["c"]) for e in es})


def build(conn, outlets: Optional[List[Dict]] = None, window: Optional[Tuple[str, str]] = None,
          shown_ids: Optional[Set[int]] = None, alteration_status: Optional[str] = None,
          rows: Optional[List[Dict]] = None, top_n: int = TOP_N) -> Dict:
    """docs/data/copies_map.json. window is (first day, day after the last) of the 30 complete days;
    shown_ids the article ids the country article files list (headlines are taken from those only)."""
    from pipeline import export
    outlets = outlets if outlets is not None else registry.load_outlets()
    rows = rows if rows is not None else export.article_rows(conn)
    state = [r for r in rows if r.get("m_cat") == "A"]
    out = empty(alteration_status)
    out["window"] = {"start": window[0], "end": window[1]} if window else None
    show_bands = alteration_status in SHOW_BANDS
    out["alteration"]["shown"] = show_bands
    if not state:
        return out
    wire_ids = registry.distribution_wire_ids(outlets)
    om = {o["id"]: o for o in outlets}
    carriers = {c["id"]: c for c in placements.carrier_outlets(outlets)}
    credits = _credits(conn)
    sc = _state_copies(conn)
    ref_sources = copies.load_reference_sources()
    # copies.attach_items joins the item groups through the originals: it reads copy_id, dup_group_id,
    # original_kind, ref_id and original_article_id.
    pseudo = []
    for r in state:
        s = sc.get(r["id"]) or {}
        pseudo.append({"copy_id": r["id"], "dup_group_id": r.get("dup_group_id"), "original_kind": s.get("original_kind"),
                       "ref_id": s.get("ref_id"), "original_article_id": s.get("original_article_id")})
    copies.attach_items(conn, pseudo)
    by_item: Dict[str, List[Tuple[Dict, Dict]]] = defaultdict(list)
    for r, p in zip(state, pseudo):
        by_item[p["item_key"]].append((r, sc.get(r["id"]) or {}))
    texts = []
    for key, members in by_item.items():
        per_outlet: Dict[Tuple[str, str], Dict] = {}
        named, refpub, routes = Counter(), Counter(), Counter()
        originals = Counter()
        for r, s in sorted(members, key=lambda m: (export._day(m[0]), m[0]["id"])):
            day = export._day(r)
            country = registry.WORLDWIDE if r["outlet_id"] in wire_ids else r["country"]
            carrier = placements.carrier_id(r)
            cr = set(credits.get(r["id"]) or ())
            if s.get("original_kind") == "reference" and s.get("original_source"):
                pub = copies.source_publisher(s["original_source"], ref_sources)
                if pub:
                    refpub[pub] += 1
            if s.get("original_kind") in ("reference", "monitored") and s.get("original_source"):
                originals[(s["original_kind"], s["original_source"], (s.get("original_published_at") or "")[:10] or None)] += 1
            if not cr and r.get("route") in ROUTE_AGENCY:
                cr = {ROUTE_AGENCY[r["route"]]}
            named.update(cr)
            routes[r.get("route") or "unknown"] += 1
            k = (carrier, country)
            band = s.get("band") if show_bands else None
            if k not in per_outlet:
                per_outlet[k] = {"o": carrier, "c": country, "d": day, "id": r["id"], "b": band}
            elif per_outlet[k]["b"] is None and band:
                per_outlet[k]["b"] = band
        entries = sorted(per_outlet.values(), key=lambda e: (e["d"], e["c"], e["o"]))
        shown = [e for e in entries if shown_ids and e["id"] in shown_ids]
        title = lang = None
        if shown:
            hit = next(r for r, _s in members if r["id"] == shown[0]["id"])
            title, lang = hit.get("title"), hit.get("language")
        orig = None
        if originals:
            (kind, src, pub_day), _n = max(originals.items(), key=lambda kv: (kv[1], kv[0][0] == "reference", kv[0][1]))
            orig = {"kind": kind, "source": src, "published": pub_day}
        texts.append({"id": "t%d" % min(r["id"] for r, _s in members), "key": key, "first_seen": entries[0]["d"],
                      "agency": agency_of(named, refpub), "agencies": sorted(named, key=lambda a: (-named[a], AGENCY_RANK.get(a, 99))),
                      "routes": dict(routes), "headline": title, "headline_lang": lang, "original": orig, "entries": entries})
    scopes = {"last_30d": window, "all_time": None}
    keep: Set[str] = set()
    for name, win in scopes.items():
        if name == "last_30d" and not win:
            continue
        ranked, countries, n_texts = [], defaultdict(lambda: {"texts": 0, "placements": 0}), 0
        for t in texts:
            nc, no = scope_counts(t["entries"], win)
            if no:
                n_texts += 1
            if nc < MIN_COUNTRIES:
                continue
            ranked.append({"id": t["id"], "countries": nc, "outlets": no, "first_seen": t["first_seen"]})
            seen = set()
            for e in t["entries"]:
                if not _day_in(e["d"], win):
                    continue
                countries[e["c"]]["placements"] += 1
                if e["c"] not in seen:
                    seen.add(e["c"])
                    countries[e["c"]]["texts"] += 1
        ranked.sort(key=rank_key)
        top = ranked[:top_n]
        keep.update(t["id"] for t in top)
        out["scopes"][name] = {"ranked": [{k: v for k, v in t.items() if k != "first_seen"} for t in top],
                               "shared_texts": len(ranked), "texts": n_texts,
                               "countries": {c: dict(v) for c, v in sorted(countries.items())}}
    used = set()
    for t in texts:
        if t["id"] not in keep:
            continue
        ents = []
        for e in t["entries"]:
            used.add(e["o"])
            d = {"o": e["o"], "c": e["c"], "d": e["d"]}
            if e["b"]:
                d["b"] = e["b"]
            ents.append(d)
        out["texts"][t["id"]] = {"agency": t["agency"], "agencies": t["agencies"], "first_seen": t["first_seen"],
                                 "routes": t["routes"], "headline": t["headline"], "headline_lang": t["headline_lang"],
                                 "original": t["original"], "placements": ents}
    for oid in sorted(used):
        if oid in carriers:
            out["outlets"][oid] = {"name": carriers[oid]["name"], "shared_widget": True}
        else:
            o = om.get(oid) or {}
            out["outlets"][oid] = {"name": o.get("name") or oid}
    return out


def build_export(conn, outlets: Optional[List[Dict]] = None, latest: Optional[Dict] = None,
                 articles: Optional[Dict[str, List[Dict]]] = None, measures: Optional[Dict] = None) -> Dict:
    """build() with the export's own window, shown articles and alteration status, stamped."""
    from pipeline import export
    window = export.complete_window_30d(latest) if latest and latest.get("window_start_30d") else None
    shown = {e["id"] for lst in (articles or {}).values() for e in lst if e.get("id") is not None}
    status = ((measures or {}).get(copies.MEASURE_ID) or {}).get("status")
    data = build(conn, outlets, window=window, shown_ids=shown, alteration_status=status)
    data["generated_at"] = store.utcnow()
    return data


def build_export_quietly(conn, outlets: Optional[List[Dict]] = None, latest: Optional[Dict] = None,
                         articles: Optional[Dict[str, List[Dict]]] = None, measures: Optional[Dict] = None) -> Dict:
    """build_export, printing a failure and returning the empty structure, never stopping the export."""
    try:
        return build_export(conn, outlets, latest, articles, measures)
    except Exception as exc:  # noqa: BLE001  display only, like the planted export
        _warn("copies map not exported: %s: %s" % (type(exc).__name__, exc))
        return empty()


def methodology_text(data: Optional[Dict] = None) -> str:
    """The paragraph for METHODOLOGY.md, with the current counts when the file is given."""
    s = ""
    if data and data.get("scopes"):
        a, m = data["scopes"].get("all_time") or {}, data["scopes"].get("last_30d") or {}
        s = (" In the current export %s state texts ran in two or more countries over all time, and %s in the last 30 "
             "complete days." % ("{:,}".format(a.get("shared_texts") or 0), "{:,}".format(m.get("shared_texts") or 0)))
    return (
        "A display of copying, not a new count: the state texts run by outlets in the most "
        "countries, and the outlets and days on which each ran (docs/data/copies_map.json, pipeline/copies_map.py). One "
        "state text is the underlying item of its state origin placements (title groups joined through body links and "
        "language editions, as in the distinct item counts), joined again through the original the alteration measure "
        "paired the copies with, so copies of one Xinhua or CGTN text at different outlets are one text. The placement "
        "rules apply as everywhere else: a shared widget counts once per country, one page at two addresses once, and an "
        "undetermined label is left out. Each outlet counts once per text, at the day of its earliest copy, and each "
        "country once; release wires are listed but are never a country. A text is shared when it ran in at least %d "
        "countries, and the lists rank the top %d by countries, then outlets, over the 30 complete days the map opens on "
        "and over all time. The agency is the state publisher most of the copies' labels name, or the publisher of the "
        "reference original; a Xinhua Silk Road release is shown as Xinhua's commercial release service, not as Xinhua, "
        "and a release with no named publisher as a release by a Chinese state body. Translations are never paired across languages, so a translated copy is a text of its own "
        "and the spread of one text across languages is undercounted. Alteration bands, where a copy was scored, are "
        "provisional as long as the alteration measure is.%s" % (MIN_COUNTRIES, TOP_N, s))
