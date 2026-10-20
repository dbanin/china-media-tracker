"""The relay bundle on the public relay branch: sealed with the relay key when there is one, in clear
with every body left out when there is none, and never pushed with a body in clear (DECISIONS 7)."""
import gzip
import types

import pytest

from pipeline import bundle_crypto, config, relay, store

KEY = b"k" * 40
PAYWALLED_TEXT = "Only subscribers may read this paragraph about the port deal with China."
FREE_TEXT = "A free article body about China and trade, long enough to be a body."


def _seed(conn):
    items = [("https://blocked.test/free", "fetched", FREE_TEXT), ("https://blocked.test/paid", "paywalled", PAYWALLED_TEXT),
             ("https://blocked.test/queued", "queued", None)]
    for url, status, body in items:
        aid = store.insert_discovered(conn, {"url": url, "outlet_id": "at_kurier", "country": "AUT", "language": "de",
                                             "title": "China talks %s" % status, "status": status, "gate_relevant": 1,
                                             "gate_terms": ["China"]})
        if body:
            store.update_article(conn, aid, body_hash=store.save_body(store.url_hash(url), body), body_chars=len(body),
                                 fetched_at=store.utcnow())
    conn.commit()


@pytest.fixture
def src(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.delenv(bundle_crypto.KEY_ENV, raising=False)
    monkeypatch.delenv(bundle_crypto.KEY_FILE_ENV, raising=False)
    conn = store.connect(tmp_path / "relay.db")
    _seed(conn)
    return conn


def test_seal_round_trip_and_tamper_refused():
    data = gzip.compress(b"x" * 3_000_000)      # more than one keystream block
    blob = bundle_crypto.seal(data, KEY)
    assert bundle_crypto.is_sealed(blob) and data not in blob
    assert bundle_crypto.open_sealed(blob, KEY) == data
    # a fresh nonce each time: the same bundle never seals to the same bytes
    assert bundle_crypto.seal(data, KEY) != blob
    flipped = bytearray(blob)
    flipped[len(bundle_crypto.MAGIC) + 40] ^= 1
    with pytest.raises(bundle_crypto.BundleAuthError):
        bundle_crypto.open_sealed(bytes(flipped), KEY)
    with pytest.raises(bundle_crypto.BundleAuthError):
        bundle_crypto.open_sealed(blob[:-1], KEY)
    with pytest.raises(bundle_crypto.BundleAuthError):
        bundle_crypto.open_sealed(blob, b"w" * 40)
    with pytest.raises(bundle_crypto.BundleKeyError):
        bundle_crypto.open_sealed(blob, None)


def test_key_from_environment_or_file(tmp_path, monkeypatch, capsys):
    assert bundle_crypto.load_key({}) is None
    assert bundle_crypto.load_key({"RELAY_BUNDLE_KEY": "  %s\n" % ("a" * 32)}) == b"a" * 32
    # too short to be a real secret: not used, and the warning never shows it
    assert bundle_crypto.load_key({"RELAY_BUNDLE_KEY": "short-secret"}) is None
    assert "short-secret" not in capsys.readouterr().err
    f = tmp_path / "relay-bundle.key"
    assert bundle_crypto.load_key({"RELAY_BUNDLE_KEY_FILE": str(f)}) is None      # not created yet
    f.write_text("b" * 43 + "\n")
    f.chmod(0o600)
    assert bundle_crypto.load_key({"RELAY_BUNDLE_KEY_FILE": str(f)}) == b"b" * 43
    # the variable wins over the file
    assert bundle_crypto.load_key({"RELAY_BUNDLE_KEY": "c" * 32, "RELAY_BUNDLE_KEY_FILE": str(f)}) == b"c" * 32


def test_with_a_key_the_bundle_is_sealed_and_carries_the_bodies(src, tmp_path, monkeypatch):
    blob = relay.make_bundle(src, key=KEY)
    assert bundle_crypto.is_sealed(blob)
    assert PAYWALLED_TEXT.encode() not in blob and FREE_TEXT.encode() not in blob
    assert relay.open_bundle(blob, key=None) is None          # the runner without the secret skips it
    data = relay.open_bundle(blob, key=KEY)
    items = list(relay.iter_bundle(data))
    assert items[0]["bodies"] == "included"
    assert sum(1 for it in items if it.get("body")) == 2
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies_main")
    dst = store.connect(tmp_path / "main.db")
    counts = relay.ingest(dst, data)
    assert counts["inserted"] == 3 and counts["bodies"] == 2
    assert store.load_body(store.url_hash("https://blocked.test/paid")) == PAYWALLED_TEXT


def test_the_key_comes_from_the_environment(src, monkeypatch):
    monkeypatch.setenv(bundle_crypto.KEY_ENV, KEY.decode())
    blob = relay.make_bundle(src)
    assert bundle_crypto.is_sealed(blob)
    assert relay.open_bundle(blob) is not None


def test_without_a_key_no_body_and_no_paywalled_text_travels(src, tmp_path, monkeypatch):
    data = relay.make_bundle(src)                 # no key in the environment
    assert not bundle_crypto.is_sealed(data)
    raw = gzip.decompress(data).decode("utf-8")
    assert PAYWALLED_TEXT not in raw and FREE_TEXT not in raw
    items = list(relay.iter_bundle(data))
    assert items[0]["_type"] == "bundle_meta" and items[0]["bodies"] == "withheld"
    arts = {it["url"]: it for it in items if not it.get("_type")}
    assert not any("body" in it for it in arts.values())
    # the runner is told which bodies were left out, so it fetches them again where it can
    assert arts["https://blocked.test/free"]["body_withheld"] and arts["https://blocked.test/paid"]["body_withheld"]
    assert "body_withheld" not in arts["https://blocked.test/queued"]
    assert relay.bodies_in_clear(data) == 0
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies_main")
    dst = store.connect(tmp_path / "main.db")
    counts = relay.ingest(dst, data)
    assert counts["inserted"] == 3 and counts["bodies"] == 0


def test_a_body_in_clear_is_never_pushed(src, monkeypatch):
    def no_git(*a, **k):
        raise AssertionError("git was called")
    monkeypatch.setattr(relay, "_origin_url", no_git)
    monkeypatch.setattr(relay, "_scratch_git", no_git)
    clear = relay.build_bundle(src)               # bodies included, not sealed
    assert relay.bodies_in_clear(clear) == 2
    with pytest.raises(ValueError, match="in clear"):
        relay.push_bundle(clear)


def test_fetch_bundle_opens_a_sealed_bundle(src, monkeypatch):
    blob = relay.make_bundle(src, key=KEY)

    def fake_git(*args, check=True, input_bytes=None, env=None):
        return types.SimpleNamespace(returncode=0, stdout=blob if args[0] == "show" else b"")
    monkeypatch.setattr(relay, "_git", fake_git)
    monkeypatch.setenv(bundle_crypto.KEY_ENV, KEY.decode())
    data = relay.fetch_bundle()
    assert data is not None and list(relay.iter_bundle(data))[0]["bodies"] == "included"
    monkeypatch.delenv(bundle_crypto.KEY_ENV)
    assert relay.fetch_bundle() is None
