"""Evidence quotes are cut to about forty words at export, on word boundaries, with the matched
phrase kept whole; the database keeps the whole quote."""
import json
import re

from pipeline import export, store

LONG = " ".join("word%d" % i for i in range(120))


def _cost(text):
    return sum(0.5 if export._NO_SPACE_CHAR.match(m.group()) else 1.0 for m in export._SNIPPET_TOKEN.finditer(text))


def test_a_short_quote_is_left_alone():
    q = "Xinhua said the talks were frank and constructive."
    assert export._cap_quote(q) == q
    assert export._cap_quote(None) is None and export._cap_quote("") == ""


def test_a_long_quote_keeps_its_opening_words_on_word_boundaries():
    out = export._cap_quote(LONG)
    assert out.startswith("word0 word1 ") and out.endswith(" " + export._ELLIPSIS)
    assert _cost(out) <= export.QUOTE_MAX_WORDS
    words = out[:-2].split()
    assert words == ["word%d" % i for i in range(len(words))], "whole words only, in order"
    assert len(words) >= export.QUOTE_MAX_WORDS - 2


def test_a_stored_span_cut_inside_a_word_drops_the_partial_word():
    # classify_rules marks an edge cut inside a token with the ellipsis right against the text
    span = export.classify_rules.SPAN_MARK + "rd0 " + LONG[6:]
    out = export._cap_quote(span)
    assert out.startswith(export._ELLIPSIS + " word1 ")
    assert "rd0" not in out


def test_the_matched_phrase_stays_whole_and_centred(monkeypatch):
    text = " ".join(["lead%d" % i for i in range(80)] + ["BEIJING, Sept. 2 (Xinhua) -- talks"] +
                    ["tail%d" % i for i in range(80)])
    monkeypatch.setattr(export, "_match_offset",
                        lambda pid, span: (lambda m: (m.start(), m.end()))(re.search(r"\(Xinhua\)", span)))
    out = export._cap_quote(text, "xinhua_dateline")
    assert "(Xinhua)" in out and _cost(out) <= export.QUOTE_MAX_WORDS
    assert out.startswith(export._ELLIPSIS) and out.endswith(export._ELLIPSIS)
    assert "lead79" in out and "tail0" in out


def test_scripts_without_spaces_count_a_character_as_half_a_word():
    q = "新华社北京电" * 30            # 180 characters, 90 words by the snippet rule
    out = export._cap_quote(q)
    assert _cost(out) <= export.QUOTE_MAX_WORDS and out.endswith(export._ELLIPSIS)
    assert out.startswith("新华社北京电")


def test_build_articles_and_the_audit_files_publish_the_capped_quote(tmp_path):
    conn = store.connect(tmp_path / "q.db")
    aid = store.insert_discovered(conn, {"url": "https://x.test/long", "outlet_id": "it_x", "country": "ITA", "language": "it",
                                         "title": "China story", "status": "fetched", "gate_relevant": 1})
    store.insert_classification(conn, aid, "llm", "B", 0.7, evidence_quote=LONG, model_version="m")
    conn.commit()
    outlets = [{"id": "it_x", "name": "X", "country": "ITA", "language": "it", "feeds": [], "tier": "national", "active": True}]
    entry = export.build_articles(conn, outlets=outlets)["ITA"][0]
    assert entry["evidence_quote"] == export._cap_quote(LONG) != LONG
    export.write_audit_files(conn, tmp_path / "audit")
    line = json.loads(next((tmp_path / "audit").glob("articles-*.jsonl")).read_text().splitlines()[0])
    assert line["evidence_quote"] == export._cap_quote(LONG)
    # the database keeps the whole quote
    assert conn.execute("SELECT evidence_quote FROM classifications WHERE article_id=?", (aid,)).fetchone()[0] == LONG


def test_a_match_longer_than_the_cap_falls_back_to_the_opening_words(monkeypatch):
    monkeypatch.setattr(export, "_match_offset", lambda pid, span: (0, len(span)))
    out = export._cap_quote(LONG, "state_entity_then_sponsored_en")
    assert out == export._cap_quote(LONG) and _cost(out) <= export.QUOTE_MAX_WORDS
