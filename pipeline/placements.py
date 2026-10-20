"""What counts as one placement, and which language editions are one underlying item.

A placement is one item published by one outlet, and the counts (daily_counts, latest.json, the
findings, articles/ISO3.json, outlets.json and the measures' inputs) count placements. Some stored
rows are not placements of their own. Every one stays stored, labelled and in the audit files; it is
only left out of the counts, where the row that stands for it (its representative) counts once:

  - shared widgets. One release listing copied into several outlets of one network, such as the
    "Press Releases from AAP" pages of ten Australian Community Media mastheads, which are all filled
    from the one AAP listing (section_pages.aap_entries gives each masthead its own row, and
    fetch_articles.FETCH_KEY_RULES maps the rows to the page they copy). One shared widget is one
    network's placement, like a release on a distribution wire, so it counts once per country
    (DECISIONS 5), whatever the number of mastheads that show it. Any rule in FETCH_KEY_RULES is a
    shared widget; SHARED_WIDGETS names it for the export.
  - one page at two addresses. Adnkronos publishes an Immediapress release at
    /immediapress/<distributor>/<slug>_<id> and again at /immediapress/<slug>_<id>; the item id at the
    end is the same. store.canonical_url now maps both to the second, so a new pair is never stored
    twice, and the rows stored before that change count once here, because the key below is the
    canonical address recomputed from each row's url, not the url_hash it was stored under.
  - numbered series. Italpress runs one Xinhua photo story as parts titled "... (1)" to "... (n)",
    each at its own address, and Europa Press splits a long release the same way. The parts of one
    story at one outlet on one day are one placement (SERIES_TITLE).

The representative of a set is a row with a human label first, then a classified row, then the
lowest id, so a set counts under one label and a reviewed copy is the one counted.

Language editions are a different case. PR Newswire publishes one release in several languages,
each at its own address on the same wire: each edition is a page, so each stays a placement, but
they are one underlying item (edition_links, which body_dups.item_groups joins). Translations share
almost no words, so the body fingerprints never link them; their titles share the names and figures
a translation keeps (Blokees, Wonder Festival, 90; Farizon, IAA Transportation). Two rows of one
wire are linked when they were discovered within EDITION_WINDOW_HOURS, are in different languages
(edition_language), were issued by the same issuer when both name one, and share at least
EDITION_MIN_ANCHORS such anchors that are neither common on that wire (seen on more than
EDITION_GENERIC_SHARE of its days) nor part of the issuer's name; one shared anchor is enough when both name the same
issuer. Links are joined strongest first, nearest in time on a tie, and a link that would put two
different titles in one language into one item is refused, since a release has one edition per
language: that is what keeps two releases of one issuer about one event apart. A title whose
language cannot be told is never linked.
"""
import datetime as dt
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from pipeline import store

# Shared widgets, keyed by the URL prefix of their rule in fetch_articles.FETCH_KEY_RULES. Each entry
# names the widget for the export (meta.shared_widgets, top_carrier).
SHARED_WIDGETS = {
    "https://newsroom.aap.com.au/releases/": {
        "id": "aap_release_widget",
        "name": "AAP press release pages",
        "network": "Australian Community Media",
        "network_short": "ACM",
        # The section type (sources/outlets.yaml) of the outlets that show the widget.
        "section_type": "aap_widget",
    },
}

SERIES_TITLE = re.compile(r"^(?P<base>.*\S)\s*\(\s*(?P<n>\d{1,2})\s*\)\s*$")

EDITION_WINDOW_HOURS = 36
EDITION_MIN_ANCHORS = 2
# An anchor seen on more than this share of the wire's days (and on more than EDITION_GENERIC_MIN_DAYS
# days) is common there (China, CGTN, Shanghai) and links nothing. Days, not titles, so that the
# editions of one release do not make their own names common.
EDITION_GENERIC_SHARE = 0.1
EDITION_GENERIC_MIN_DAYS = 4


def widget_of(url: Optional[str]) -> Optional[Dict]:
    """The shared widget a URL belongs to, as {"id", "name", "network", "prefix"}, or None."""
    from pipeline import fetch_articles
    for prefix, _param in fetch_articles.FETCH_KEY_RULES:
        if (url or "").startswith(prefix):
            w = dict(SHARED_WIDGETS.get(prefix) or {"id": "shared_widget", "name": prefix, "network": None})
            w["prefix"] = prefix
            return w
    return None


def _norm_title(title: Optional[str]) -> str:
    return " ".join((title or "").casefold().split())


def series_base(title: Optional[str]) -> Optional[str]:
    """The story title of a numbered series part ("Cina: campi e coltivazioni (2)" gives "cina: campi e
    coltivazioni"), or None for a title that is not a numbered part."""
    m = SERIES_TITLE.match(title or "")
    return _norm_title(m.group("base")) if m else None


def _day(row) -> str:
    keys = row.keys()
    if "day_of_record" in keys and row["day_of_record"]:
        return row["day_of_record"]
    return (row["discovered_at"] or "")[:10]


def placement_key(row) -> Tuple:
    """The key under which rows of one country count as one placement: the shared widget's fetch key,
    a numbered series' story, or the canonical address."""
    from pipeline import fetch_articles
    url = row["url"] or ""
    fk = fetch_articles.fetch_key(url)
    if fk:
        return ("widget", fk)
    base = series_base(row["title"])
    if base:
        return ("series", row["outlet_id"], base, _day(row))
    return ("url", store.canonical_url(url))


# One export asks for the sets many times. The cache holds the connection itself (it cannot be weakly
# referenced), so another connection never reads a result made for an earlier one that had the same id,
# and the counts and the connection's change counter below invalidate it when the data changes.
_cache: Dict = {}


def _fingerprint(conn) -> Tuple:
    r = conn.execute("SELECT COUNT(*), MAX(id) FROM articles WHERE gate_relevant=1").fetchone()
    c = conn.execute("SELECT COUNT(*), MAX(id) FROM classifications WHERE is_current=1").fetchone()
    h = conn.execute("SELECT COUNT(*), MAX(id) FROM human_reviews").fetchone()
    return (conn.total_changes, tuple(r), tuple(c), tuple(h))


def _cached(conn, name: str, fp: Tuple):
    hit = _cache.get(name)
    if hit and hit[0] is conn and hit[1] == fp:
        return hit[2]
    return None


def _sets(conn) -> Dict:
    """{"rep": {shadowed id: representative id}, "sets": [(kind, country, [ids], representative)]}."""
    fp = _fingerprint(conn)
    hit = _cached(conn, "sets", fp)
    if hit is not None:
        return hit
    groups: Dict[Tuple, List] = defaultdict(list)
    for r in conn.execute(
            """SELECT a.id, a.country, a.outlet_id, a.url, a.title, a.discovered_at, a.day_of_record,
                      c.category AS m_cat,
                      EXISTS(SELECT 1 FROM human_reviews h WHERE h.article_id=a.id) AS has_h
               FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
               WHERE a.gate_relevant=1"""):
        groups[(r["country"], placement_key(r))].append((r["id"], bool(r["has_h"]), bool(r["m_cat"])))
    rep: Dict[int, int] = {}
    sets = []
    for (country, key), members in groups.items():
        if len(members) < 2:
            continue
        lead = min(members, key=lambda m: (not m[1], not m[2], m[0]))[0]
        for m in members:
            if m[0] != lead:
                rep[m[0]] = lead
        sets.append((key[0], country, sorted(m[0] for m in members), lead))
    out = {"rep": rep, "sets": sets}
    _cache["sets"] = (conn, fp, out)
    return out


def shadowed(conn) -> Set[int]:
    """Ids of stored rows that are not placements of their own: each is counted through the row that
    stands for it (representative_of)."""
    return set(_sets(conn)["rep"])


def representative_of(conn) -> Dict[int, int]:
    """{shadowed id: id of the row counted for it}."""
    return dict(_sets(conn)["rep"])


def copies_of(conn) -> Dict[int, int]:
    """{representative id: number of stored rows it stands for, itself included}, for sets of two or more."""
    return {lead: len(ids) for _kind, _c, ids, lead in _sets(conn)["sets"]}


def carrier_id(row) -> str:
    """Who carried a placement, for the top carrier: a shared widget's id, else the outlet."""
    w = widget_of(row["url"])
    return w["id"] if w else row["outlet_id"]


def widget_outlets(outlets: List[Dict], widget: Dict) -> List[str]:
    """Ids of the active outlets that show a shared widget (a section feed of its section type)."""
    return sorted(o["id"] for o in outlets if o.get("active", True)
                  and any(f.get("type") == widget.get("section_type") for f in (o.get("section_feeds") or [])))


def carrier_outlets(outlets: List[Dict]) -> List[Dict]:
    """The shared widgets as carrier entries (id, name, network) beside the registry's outlets, so a top
    carrier credited to a widget is named, with the number of outlets that show it."""
    out = []
    for w in SHARED_WIDGETS.values():
        n = len(widget_outlets(outlets, w))
        name = "%s of %d %s mastheads" % (w["name"], n, w.get("network_short") or w["network"]) if n else w["name"]
        out.append({"id": w["id"], "name": name, "network": w["network"], "shared_widget": True})
    return out


# ---------------------------------------------------------------------------
# Language editions
# ---------------------------------------------------------------------------

_STOP = {
    "en": "the of and to for in on at with from by as its is are new an into".split(),
    "de": "der die das und mit für auf von im ist den dem des ein eine einen zur zum über bei".split(),
    "fr": "le la les des du et pour sur avec dans un une au aux est sa son ses par".split(),
    "es": "el la los las del y para con en un una por su sus se más al".split(),
    "it": "il lo la gli le del della dei di e per con nel nella un una alla al da".split(),
    "pt": "o os do da dos das para com em um uma no na ao pela pelo".split(),
    "nl": "de het een en van voor met op bij naar uit".split(),
    "pl": "i w z na do dla oraz się od po ponad przez".split(),
    "cs": "a v na pro s se z za do od který jako".split(),
    "sk": "a v na pre s sa z za do od ako".split(),
    "tr": "ve ile bir için de da bu".split(),
}
_CHARS = {
    "pl": "łąęśźżń", "cs": "řěů", "sk": "ľĺŕô", "de": "ßüö", "tr": "şğı", "es": "ñ¡¿",
    "pt": "ãõ", "fr": "èêœ«»ç",
}
_SCRIPTS = (("arab", re.compile(r"[؀-ۿ]")), ("cyrl", re.compile(r"[Ѐ-ӿ]")),
            ("cjk", re.compile(r"[぀-ヿ一-鿿]")), ("kore", re.compile(r"[가-힯]")),
            ("thai", re.compile(r"[฀-๿]")), ("grek", re.compile(r"[Ͱ-Ͽ]")),
            ("hebr", re.compile(r"[֐-׿]")), ("deva", re.compile(r"[ऀ-ॿ]")))
_WORD = re.compile(r"[^\W\d_]+")
_TOKEN = re.compile(r"[^\W_]+")
_YEAR = re.compile(r"^(19|20)\d\d$")
_POSSESSIVE = re.compile(r"['\u2019]s\b")


def edition_language(title: Optional[str]) -> Optional[str]:
    """A rough guess at a title's language: its script when it is not Latin, otherwise the language
    whose common words and letters it uses most; None when nothing tells."""
    t = title or ""
    for name, rx in _SCRIPTS:
        if rx.search(t):
            return name
    words = [w.lower() for w in _WORD.findall(_POSSESSIVE.sub("", t))]
    low = t.lower()
    best, score = None, 0
    for lang, stop in _STOP.items():
        s = sum(1 for w in words if w in stop) + 2 * sum(1 for ch in _CHARS.get(lang, "") if ch in low)
        if s > score:
            best, score = lang, s
    return best


def edition_anchors(title: Optional[str]) -> Set[str]:
    """The tokens of a title a translation keeps: figures other than years, and capitalised or upper
    case Latin names, lower cased."""
    out = set()
    for tok in _TOKEN.findall(title or ""):
        if tok.isdigit():
            if not _YEAR.match(tok):
                out.add(tok.lstrip("0") or "0")
        elif re.match(r"^[A-Z][A-Za-z]+$", tok) or re.match(r"^[A-Z0-9]{2,}$", tok):
            out.add(tok.lower())
    return out


def _ts(iso: Optional[str]) -> float:
    try:
        d = dt.datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()


def _wire_ids(conn) -> Set[str]:
    try:
        ids = {r[0] for r in conn.execute("SELECT id FROM outlets WHERE tier='distribution_wire'")}
    except Exception:  # noqa: BLE001  a database without the outlets table
        ids = set()
    if ids:
        return ids
    from pipeline import registry
    try:
        return registry.distribution_wire_ids(registry.load_outlets(validate=False))
    except Exception:  # noqa: BLE001
        return set()


def link_editions(rows: Iterable[Dict]) -> List[Tuple[int, int]]:
    """Edition links among the rows of one wire: dicts with id, title, author, discovered_at."""
    rs = []
    for r in rows:
        lang = edition_language(r.get("title"))
        if not lang:
            continue
        rs.append({"id": r["id"], "lang": lang, "anc": edition_anchors(r.get("title")),
                   "author": " ".join((r.get("author") or "").casefold().split()),
                   "auth": {w.lower() for w in _TOKEN.findall(r.get("author") or "")},
                   "title": _norm_title(r.get("title")), "t": _ts(r.get("discovered_at")),
                   "day": (r.get("discovered_at") or "")[:10], "raw": r.get("title") or ""})
    if len(rs) < 2:
        return []
    days = defaultdict(set)
    for r in rs:
        for a in r["anc"]:
            days[a].add(r["day"])
    span = len({r["day"] for r in rs})
    cap = max(EDITION_GENERIC_MIN_DAYS, int(EDITION_GENERIC_SHARE * span))
    generic = {a for a, d in days.items() if len(d) > cap}
    # A capitalised word the wire's titles also use in lower case is an ordinary word in title case
    # ("Overseas", "Growth"), not a name a translation keeps.
    lower = {w for r in rs for w in _WORD.findall(r["raw"]) if w.islower()}
    generic |= {a for a in days if not a.isdigit() and a in lower}
    rs.sort(key=lambda r: (r["t"], r["id"]))
    window = EDITION_WINDOW_HOURS * 3600
    cands = []
    for i, r in enumerate(rs):
        for s in rs[i + 1:]:
            if s["t"] - r["t"] > window:
                break
            if r["lang"] == s["lang"]:
                continue
            same_issuer = bool(r["author"] and s["author"])
            if same_issuer and r["author"] != s["author"]:
                continue
            shared = (r["anc"] & s["anc"]) - generic
            distinct = shared - r["auth"] - s["auth"]
            if not distinct or len(shared) < (1 if same_issuer else EDITION_MIN_ANCHORS):
                continue
            cands.append((-len(shared), s["t"] - r["t"], r["id"], s["id"]))
    if not cands:
        return []
    info = {r["id"]: r for r in rs}
    parent: Dict[int, int] = {}
    langs: Dict[int, Dict[str, str]] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    links = []
    for _neg, _gap, a, b in sorted(cands):
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        la = langs.get(ra) or {info[a]["lang"]: info[a]["title"]}
        lb = langs.get(rb) or {info[b]["lang"]: info[b]["title"]}
        if any(lang in la and la[lang] != title for lang, title in lb.items()):
            continue   # two different titles in one language: two releases, not two editions
        root, other = min(ra, rb), max(ra, rb)
        parent[other] = root
        merged = dict(la)
        merged.update(lb)
        langs[root] = merged
        langs.pop(other, None)
        links.append((a, b))
    return links


def edition_links(conn) -> List[Tuple[int, int]]:
    """Pairs of article ids that are language editions of one release on a distribution wire, for
    body_dups.item_groups to join into one underlying item."""
    fp = _fingerprint(conn)
    hit = _cached(conn, "editions", fp)
    if hit is not None:
        return list(hit)
    wires = _wire_ids(conn)
    links: List[Tuple[int, int]] = []
    if wires:
        marks = ",".join("?" * len(wires))
        by_outlet = defaultdict(list)
        for r in conn.execute("SELECT id, outlet_id, title, author, discovered_at FROM articles "
                              "WHERE gate_relevant=1 AND outlet_id IN (%s)" % marks, sorted(wires)):
            by_outlet[r["outlet_id"]].append(dict(r))
        for oid in sorted(by_outlet):
            links.extend(link_editions(by_outlet[oid]))
    _cache["editions"] = (conn, fp, links)
    return list(links)


# ---------------------------------------------------------------------------
# What the export discloses
# ---------------------------------------------------------------------------

def summary(conn, outlets: List[Dict]) -> Dict:
    """meta.shared_widgets and meta.duplicate_placements: how many stored rows each rule counts as one
    placement, overall and in state origin (its representative's current label), per country."""
    from pipeline import registry
    wire_ids = registry.distribution_wire_ids(outlets)
    s = _sets(conn)
    all_ids = sorted({i for _k, _c, ids, _l in s["sets"] for i in ids})
    meta = {}
    for i in range(0, len(all_ids), 900):
        part = all_ids[i:i + 900]
        for r in conn.execute(
                """SELECT a.id, a.outlet_id, a.url, c.category FROM articles a
                   LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
                   WHERE a.id IN (%s)""" % ",".join("?" * len(part)), part):
            meta[r["id"]] = dict(r)
    widgets: Dict[str, Dict] = {}
    dup = {k: {"sets": 0, "stored_rows": 0, "counted_placements": 0, "rows_not_counted": 0,
               "state_origin_rows": 0, "state_origin_counted": 0, "by_country": {}}
           for k in ("url", "series")}
    for kind, country, ids, lead in s["sets"]:
        cat_lead = (meta.get(lead) or {}).get("category")
        a_rows = sum(1 for i in ids if (meta.get(i) or {}).get("category") == "A")
        iso = registry.WORLDWIDE if (meta.get(lead) or {}).get("outlet_id") in wire_ids else country
        if kind == "widget":
            w = widget_of((meta.get(lead) or {}).get("url")) or {"id": "shared_widget", "name": "", "network": None, "prefix": ""}
            e = widgets.setdefault(w["id"], {"id": w["id"], "name": w["name"], "network": w["network"],
                                             "url_prefix": w["prefix"], "outlets": set(), "countries": {},
                                             "stored_rows": 0, "counted_placements": 0, "rows_not_counted": 0,
                                             "state_origin_rows": 0, "state_origin_counted": 0})
            e["outlets"].update((meta.get(i) or {}).get("outlet_id") for i in ids)
            target = [e, e["countries"].setdefault(iso, {"stored_rows": 0, "counted_placements": 0, "rows_not_counted": 0,
                                                          "state_origin_rows": 0, "state_origin_counted": 0})]
        else:
            d = dup[kind]
            d["sets"] += 1
            target = [d, d["by_country"].setdefault(iso, {"stored_rows": 0, "counted_placements": 0, "rows_not_counted": 0,
                                                          "state_origin_rows": 0, "state_origin_counted": 0})]
        for t in target:
            t["stored_rows"] += len(ids)
            t["counted_placements"] += 1
            t["rows_not_counted"] += len(ids) - 1
            t["state_origin_rows"] += a_rows
            t["state_origin_counted"] += 1 if cat_lead == "A" else 0
    shared = []
    for w in sorted(widgets.values(), key=lambda w: w["id"]):
        w["outlets"] = sorted(o for o in w["outlets"] if o)
        shared.append(w)
    # Widgets with nothing stored yet are listed too, so the disclosure does not depend on the data.
    for prefix, w in SHARED_WIDGETS.items():
        if w["id"] not in widgets:
            shared.append({"id": w["id"], "name": w["name"], "network": w["network"], "url_prefix": prefix,
                           "outlets": [], "countries": {}, "stored_rows": 0, "counted_placements": 0,
                           "rows_not_counted": 0, "state_origin_rows": 0, "state_origin_counted": 0})
    for w in shared:
        spec = next((v for v in SHARED_WIDGETS.values() if v["id"] == w["id"]), None)
        w["outlets_showing"] = widget_outlets(outlets, spec) if spec else w["outlets"]
        w["note"] = ("One release listing shown on several outlets of one network counts as one placement per "
                     "country, like a release on a distribution wire. Every outlet's copy stays stored; one copy "
                     "is counted for each release.")
    # Language editions: one underlying item for the editions of one release on a wire; each edition
    # stays a placement.
    links = edition_links(conn)
    parent: Dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in links:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups = defaultdict(set)
    for x in list(parent):
        groups[find(x)].add(x)
    ed_ids = sorted(parent)
    cats = {}
    for i in range(0, len(ed_ids), 900):
        part = ed_ids[i:i + 900]
        for r in conn.execute("SELECT article_id, category FROM classifications WHERE is_current=1 AND article_id IN (%s)"
                              % ",".join("?" * len(part)), part):
            cats[r[0]] = r[1]
    a_groups = [g for g in groups.values() if any(cats.get(i) == "A" for i in g)]
    editions = {
        "groups": len(groups), "rows": sum(len(g) for g in groups.values()),
        "state_origin_groups": len(a_groups),
        "state_origin_rows": sum(1 for g in a_groups for i in g if cats.get(i) == "A"),
        "window_hours": EDITION_WINDOW_HOURS,
        "note": ("Language editions of one release on a distribution wire are one underlying item; each edition "
                 "is its own page and stays a placement."),
    }
    dup["url"]["note"] = ("One page stored at two addresses (Adnkronos Immediapress, with and without the "
                          "distributor in the path) counts once.")
    dup["series"]["note"] = ("The numbered parts, (1) to (n), of one story at one outlet on one day (an Italpress "
                             "photo story, a Europa Press release split into parts) count as one placement.")
    return {"shared_widgets": shared,
            "duplicate_placements": {"same_page": dup["url"], "numbered_series": dup["series"],
                                     "language_editions": editions}}
