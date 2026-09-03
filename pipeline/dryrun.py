"""Dry run of the whole pipeline on fixtures. No network, no API calls.

Builds a throwaway database in a temporary directory from
tests/fixtures/dryrun/feed.xml and the HTML pages it links, runs the gate,
extraction, the rules classifier, the LLM stage with the fake client, the
rollups and the export, and prints the counts.

  python -m pipeline.dryrun
"""
import json
import shutil
import tempfile
from pathlib import Path

import feedparser

from pipeline import config, classify_llm, classify_rules, export, extract, gate, store

FIXTURES = config.ROOT / "tests" / "fixtures" / "dryrun"


def run(out_dir: Path = None, keep: bool = False) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="tracker-dryrun-"))
    db = tmp / "tracker.db"
    bodies = tmp / "bodies"
    raw = tmp / "raw_html"
    export_dir = out_dir or (tmp / "docs_data")
    saved = (config.DB_PATH, config.BODIES_DIR, config.RAW_HTML_DIR)
    config.DB_PATH, config.BODIES_DIR, config.RAW_HTML_DIR = db, bodies, raw
    try:
        conn = store.connect(db)
        outlet = {"id": "xx_fixture", "name": "Fixture Daily", "country": "ITA", "language": "en", "tier": "national_daily",
                  "feeds": ["fixture://feed.xml"], "active": True, "notes": "dry run"}
        store.sync_outlets(conn, [outlet])
        parsed = feedparser.parse(str(FIXTURES / "feed.xml"))
        counts = {"items": 0, "relevant": 0, "fetched": 0, "paywalled": 0}
        for entry in parsed.entries:
            counts["items"] += 1
            title = entry.get("title", "")
            relevant, terms = gate.check(title, entry.get("summary", ""), "en")
            aid = store.insert_discovered(conn, {"url": entry.link, "outlet_id": outlet["id"], "country": "ITA", "language": "en",
                                                 "title": title, "summary": entry.get("summary", ""), "status": "queued" if relevant else "gated_out",
                                                 "gate_relevant": 1 if relevant else 0, "gate_terms": terms})
            if not relevant or aid is None:
                continue
            counts["relevant"] += 1
            page = FIXTURES / entry.link.split("/")[-1]
            html = page.read_text(encoding="utf-8") if page.exists() else ""
            ex = extract.extract(html, entry.link)
            pw, why = extract.detect_paywall(html, ex["text"])
            h = store.url_hash(entry.link)
            raw_path = store.save_raw_html(h, html)
            if pw:
                store.update_article(conn, aid, status="paywalled", fail_reason=why, fetched_at=store.utcnow(), raw_html_path=raw_path)
                counts["paywalled"] += 1
            else:
                store.update_article(conn, aid, status="fetched", body_hash=store.save_body(h, ex["text"] or ""), body_chars=len(ex["text"] or ""),
                                     fetched_at=store.utcnow(), raw_html_path=raw_path, author=ex["author"])
                counts["fetched"] += 1
        conn.commit()
        counts["rules"] = classify_rules.run(conn, "dryrun")
        counts["llm"] = classify_llm.run(conn, "dryrun", client=classify_llm.DryRunClient(answer=lambda body: "B" if "spokesperson" in body else "C"))
        counts["export"] = export.run(conn, "dryrun", export_dir=export_dir)
        counts["categories"] = {r[0]: r[1] for r in conn.execute("SELECT category, COUNT(*) FROM classifications WHERE is_current=1 GROUP BY category")}
        conn.close()
        return counts
    finally:
        config.DB_PATH, config.BODIES_DIR, config.RAW_HTML_DIR = saved
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
