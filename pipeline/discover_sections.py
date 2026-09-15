"""Find press release, sponsored and partner content sections that editorial RSS leaves out.

State placements often live in a press release or branded content section that is not
indexed alongside editorial coverage, so a feed driven design finds none of them. This
probe visits each active outlet's homepage under the usual politeness rules (robots.txt,
one request per domain every three seconds, identifying user agent), looks for links to
such sections in the outlet's own language, and for each section looks for a feed.

Results are written as they arrive to a JSON file, so a long run survives interruption,
and merged into sources/outlets.yaml only with --apply:
  release_sections  {searched_on, status, sections}   recorded for every searched outlet
  section_feeds     [{url, kind, section}]            feeds the collector then polls

Usage:
  python -m pipeline.discover_sections probe [--limit N] [--only id,id] [--out data/sections_probe.json]
  python -m pipeline.discover_sections apply [--out data/sections_probe.json]
"""
import argparse
import datetime as dt
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from pipeline import config, registry, store

MAX_SECTIONS_PER_OUTLET = 4
FEED_SUFFIXES = ("feed", "rss", "feed/", "rss.xml")

# Link text or URL path naming a section. Matched case insensitively against the anchor text
# and against the URL path with separators turned into spaces.
KIND_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("press_release", re.compile(
        r"\b(press[ -]?releases?|news[ -]?releases?|media[ -]?releases?|pr[ -]?newswire|business[ -]?wire|globe[ -]?newswire|"
        r"ein[ -]?presswire|accesswire|newsfile|communiqu[eé]s?( de presse)?|comunicados?( de prensa| de imprensa)?|notas? de prensa|"
        r"comunicati( stampa)?|comunicato stampa|pressemitteilung(en)?|persberichten?|pressmeddelanden?|tiedotteet|"
        r"press ?release|пресс[- ]?релиз\w*|بيانات? صحفي\w*|siaran pers|komunikat(y)? prasow\w*|tiskov[aáé] zpr[aá]v\w*|"
        r"basın b[uü]lteni|δελτ[ιί]α τ[υύ]που|immediapress|prnewswire|xinhua silk road)\b", re.I)),
    ("sponsored", re.compile(
        r"\b(sponsored( content| stories| posts?)?|paid (post|content|partner)|advertorial|native advertising|branded content|"
        r"brand ?studio|contenu(s)? sponsoris[eé]s?|publi[- ]?reportages?|publirreportajes?|contenidos? patrocinados?|"
        r"conte[uú]dos? patrocinados?|publieditorial|contenuti sponsorizzati|contenuto sponsorizzato|redazionale|"
        r"gesponsert|sonderver[oö]ffentlichung(en)?|anzeige|advertentie|gesponsord|sponsrat|спецпроект\w*|на правах рекламы|"
        r"محتوى (مدفوع|برعاية)|konten bersponsor|artikel bersponsor|konten sponsor|reklam içerik|sponsorlu i[cç]erik|"
        r"cairorcs studio|telegraph spotlight|china watch)\b", re.I)),
    ("partner", re.compile(
        r"\b(partner(ed)? content|partner stories|in partnership( with)?|content partner|contenu partenaire|contenido de socios|"
        r"conte[uú]do (de )?parceiros?|partnerinhalte?|special (report|supplement|feature)s? (by|from)|supplements?|"
        r"media partner|studio partners?)\b", re.I)),
]

_lock = threading.Lock()


def classify_link(text: str, href: str) -> Optional[str]:
    """The section kind a link names, or None. Press release beats sponsored beats partner."""
    path = urlsplit(href).path
    haystack = "%s %s" % (text or "", re.sub(r"[-_/.]+", " ", path))
    for kind, rx in KIND_PATTERNS:
        if rx.search(haystack):
            return kind
    return None


def outlet_homepage(outlet: Dict, conn=None) -> Optional[str]:
    """The site the outlet publishes on: the most common article host seen in the database,
    otherwise the first feed's host with feed subdomains removed."""
    if conn is not None:
        row = conn.execute("SELECT url FROM articles WHERE outlet_id=? ORDER BY id DESC LIMIT 1", (outlet["id"],)).fetchone()
        if row:
            p = urlsplit(row["url"])
            return "%s://%s/" % (p.scheme or "https", p.netloc)
    feeds = outlet.get("feeds") or []
    if not feeds:
        return None
    p = urlsplit(feeds[0])
    host = re.sub(r"^(feeds?|rss|xml\d*|rssfeeds|syndication)\.", "www.", p.netloc)
    return "%s://%s/" % (p.scheme or "https", host)


def normalize_section(url: str) -> Optional[str]:
    """Trim a link to the section it names: ".../advertorial/some-story-slug" becomes ".../advertorial".
    Returns None when the link is a single article rather than a section."""
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    for i, seg in enumerate(segs):
        if classify_link("", "/" + seg):
            segs = segs[:i + 1]
            break
    else:
        # The anchor text named the section; the URL must still look like an index, not a story.
        if segs and (segs[-1].count("-") >= 4 or len(segs[-1]) > 60 or re.search(r"\d{5,}", segs[-1])):
            return None
    if len(segs) > 3:
        return None
    return "%s://%s/%s" % (parts.scheme or "https", parts.netloc, "/".join(segs))


def section_links(html: str, base: str) -> List[Dict]:
    """Links on the page that name a section, same site only, first occurrence per URL."""
    from lxml import html as lhtml
    try:
        doc = lhtml.fromstring(html)
    except Exception:
        return []
    base_host = urlsplit(base).netloc.lower().replace("www.", "")
    out, seen = [], set()
    for a in doc.iter("a"):
        href = a.get("href")
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = urljoin(base, href).split("#")[0]
        host = urlsplit(url).netloc.lower().replace("www.", "")
        if not host or not (host == base_host or host.endswith("." + base_host) or base_host.endswith("." + host)):
            continue
        text = " ".join((a.text_content() or "").split())[:80]
        kind = classify_link(text, url)
        if kind:
            url = normalize_section(url)
        if kind and url and url not in seen:
            seen.add(url)
            out.append({"url": url, "kind": kind, "label": text or urlsplit(url).path})
    order = {"press_release": 0, "sponsored": 1, "partner": 2}
    out.sort(key=lambda s: order[s["kind"]])
    return out


def feed_links(html: str, base: str) -> List[str]:
    """Feeds the page declares with <link rel="alternate" type="application/rss+xml">."""
    out = []
    for m in re.finditer(r"<link\b[^>]*>", html or "", re.I):
        tag = m.group(0)
        if re.search(r"rel=[\"']?alternate", tag, re.I) and re.search(r"type=[\"']?application/(rss|atom)\+xml", tag, re.I):
            h = re.search(r"href=[\"']([^\"']+)", tag, re.I)
            if h:
                out.append(urljoin(base, h.group(1)))
    return out


def _feed_ok(url: str) -> bool:
    from pipeline import fetch_articles
    from pipeline.feeds_util import fetch_feed
    allowed, _ = fetch_articles.can_fetch(url)
    if not allowed:
        return False
    fetch_articles._rate_wait(fetch_articles.domain_of(url))
    return fetch_feed(url)["ok"]


def probe_outlet(outlet: Dict, homepage: Optional[str]) -> Dict:
    from pipeline import fetch_articles
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    rec = {"id": outlet["id"], "searched_on": today, "homepage": homepage, "sections": [], "status": "none_found"}
    if not homepage:
        rec["status"] = "unreachable"
        return rec
    allowed, why = fetch_articles.can_fetch(homepage)
    if not allowed:
        rec["status"] = "blocked" if why == "robots_disallowed" else "unreachable"
        rec["reason"] = why
        return rec
    html, status, err = fetch_articles.get_page(homepage)
    if html is None:
        rec["status"] = "unreachable"
        rec["reason"] = err
        return rec
    existing = set(outlet.get("feeds") or [])
    for sec in section_links(html, homepage)[:MAX_SECTIONS_PER_OUTLET]:
        feed = None
        reachable = False
        ok_page, _ = fetch_articles.can_fetch(sec["url"])
        if ok_page:
            page, _, _ = fetch_articles.get_page(sec["url"])
            reachable = page is not None
            candidates = feed_links(page or "", sec["url"])
            if not candidates:
                root = sec["url"].rstrip("/") + "/"
                candidates = [root + s for s in FEED_SUFFIXES[:2]]
            for c in candidates[:3]:
                if c in existing:
                    continue
                if _feed_ok(c):
                    feed = c
                    break
        rec["sections"].append({"url": sec["url"], "kind": sec["kind"], "label": sec["label"], "feed": feed, "reachable": reachable})
    if any(s["reachable"] for s in rec["sections"]):
        rec["status"] = "found"
    return rec


def _load(path) -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def probe(out_path, limit: Optional[int] = None, only: Optional[List[str]] = None, workers: int = 16,
          redo: bool = False) -> Dict:
    outlets = [o for o in registry.load_outlets() if o.get("active")]
    if only:
        outlets = [o for o in outlets if o["id"] in set(only)]
    results = _load(out_path)
    todo = [o for o in outlets if redo or o["id"] not in results]
    if limit:
        todo = todo[:limit]
    conn = None
    try:
        conn = store.connect()
    except Exception:
        conn = None
    homes = {o["id"]: outlet_homepage(o, conn) for o in todo}
    if conn is not None:
        conn.close()
    counts = {"outlets": len(todo), "found": 0, "none_found": 0, "blocked": 0, "unreachable": 0, "feeds": 0}

    def one(o):
        try:
            rec = probe_outlet(o, homes[o["id"]])
        except Exception as exc:  # one site must never stop the probe
            rec = {"id": o["id"], "searched_on": dt.datetime.now(dt.timezone.utc).date().isoformat(),
                   "homepage": homes[o["id"]], "sections": [], "status": "unreachable", "reason": "exception:%s" % type(exc).__name__}
        with _lock:
            results[o["id"]] = rec
            counts[rec["status"]] += 1
            counts["feeds"] += sum(1 for s in rec["sections"] if s.get("feed"))
            tmp = str(out_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(results, fh, ensure_ascii=False, indent=1)
            import os
            os.replace(tmp, out_path)
            done = sum(counts[k] for k in ("found", "none_found", "blocked", "unreachable"))
            if done % 25 == 0:
                print("probed %d of %d: %s" % (done, len(todo), json.dumps(counts)), flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))
    print("probe finished:", json.dumps(counts), flush=True)
    return counts


def apply(out_path) -> Dict:
    """Merge probe results into sources/outlets.yaml."""
    results = _load(out_path)
    outlets = registry.load_outlets()
    changed = {"outlets": 0, "section_feeds": 0}
    for o in outlets:
        rec = results.get(o["id"])
        if not rec:
            continue
        o["release_sections"] = {
            "searched_on": rec["searched_on"], "status": rec["status"],
            "sections": [{"url": s["url"], "kind": s["kind"], "feed": s.get("feed"), "label": s.get("label") or ""}
                         for s in rec["sections"] if s.get("reachable", True)],
        }
        # A section with a feed is polled on the feed; one without is polled on its index page.
        feeds = [{"url": s["feed"] or s["url"], "kind": s["kind"], "section": s["url"], "type": "rss" if s.get("feed") else "page"}
                 for s in rec["sections"] if s.get("reachable", True)]
        if feeds:
            o["section_feeds"] = feeds
            changed["section_feeds"] += len(feeds)
        else:
            o.pop("section_feeds", None)
        changed["outlets"] += 1
    registry.validate_outlets(outlets)
    registry.save_outlets(outlets)
    print("applied:", json.dumps(changed))
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["probe", "apply"])
    ap.add_argument("--out", default=str(config.ROOT / "data" / "sections_probe.json"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "probe":
        probe(args.out, limit=args.limit, only=args.only.split(",") if args.only else None, workers=args.workers, redo=args.redo)
    else:
        apply(args.out)


if __name__ == "__main__":
    main()
