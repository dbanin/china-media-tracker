"""scripts/db_restore.sh, scripts/db_snapshot.sh and scripts/relay.sh, run for real against a fake gh.

Each script is copied into a temporary repository layout and run with bash. gh is a small fake on
PATH that serves a release from a directory and can be told to fail, so nothing reaches the network.
What these pin (CI audit, 2026-10-17): a cached database with a hot journal is rolled back and kept;
a torn one is set aside for the snapshot; a snapshot that exists but cannot be read fails the step
after three tries instead of falling back to the seed; the public snapshot goes through the scrubber;
the whole database and the bodies are published only encrypted, and come back on a cache miss; the
relay stops by the wall clock, runs under caffeinate, and falls back from SSH to HTTPS.
"""
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import textwrap

import pytest

from pipeline import config

ROOT = config.ROOT
KEY = "k" * 43
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("openssl") is None,
                                reason="needs bash and openssl")

FAKE_GH = r"""#!/bin/bash
# A release served from $FAKE_GH/assets. Files in $FAKE_GH tell it to fail.
echo "$*" >> "$FAKE_GH/calls"
[ "$1" = release ] || exit 3
cmd="$2"; shift 2
case "$cmd" in
  view)
    if [ -e "$FAKE_GH/norelease" ]; then echo "release not found" >&2; exit 1; fi
    if [ -e "$FAKE_GH/viewfail" ]; then echo "HTTP 502: Bad Gateway" >&2; exit 1; fi
    [ -d "$FAKE_GH/assets" ] || { echo "release not found" >&2; exit 1; }
    if [ "${2:-}" = "--json" ]; then ls "$FAKE_GH/assets"; fi
    exit 0 ;;
  download)
    name=""; dir="."
    while [ $# -gt 0 ]; do case "$1" in -p) name="$2"; shift 2;; -D) dir="$2"; shift 2;; *) shift;; esac; done
    if [ -e "$FAKE_GH/dlfail" ]; then echo x >> "$FAKE_GH/dl_attempts"; echo "HTTP 502" >&2; exit 1; fi
    cp "$FAKE_GH/assets/$name" "$dir/$name" ;;
  create) mkdir -p "$FAKE_GH/assets" ;;
  upload) cp "$2" "$FAKE_GH/assets/" ;;
  *) exit 3 ;;
esac
"""

FAKE_SCRUB = '''"""Stands in for pipeline/snapshot_scrub.py: copies the database with every author blanked."""
import shutil, sqlite3, sys
shutil.copyfile(sys.argv[1], sys.argv[2])
c = sqlite3.connect(sys.argv[2])
c.execute("UPDATE articles SET author = NULL")
c.commit()
'''


def _exe(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _repo(tmp_path, name, scripts=("db_restore.sh", "db_snapshot.sh"), scrubber=False):
    repo = tmp_path / name
    (repo / "scripts").mkdir(parents=True)
    for s in scripts:
        shutil.copy(ROOT / "scripts" / s, repo / "scripts" / s)
    if scrubber:
        (repo / "pipeline").mkdir()
        (repo / "pipeline" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "pipeline" / "snapshot_scrub.py").write_text(FAKE_SCRUB, encoding="utf-8")
    (repo / "data").mkdir()
    return repo


@pytest.fixture
def gh(tmp_path):
    root = tmp_path / "gh"
    root.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "gh", FAKE_GH)
    return root


def _env(tmp_path, gh_root, key=None, **extra):
    env = dict(os.environ)
    env.pop("RELAY_BUNDLE_KEY", None)
    env.update(PATH="%s:%s" % (tmp_path / "bin", env.get("PATH", "")), FAKE_GH=str(gh_root), RETRY_SLEEP="0",
               PYTHON=sys.executable, GIT_DIR=str(tmp_path / "no-git"), TMPDIR=str(tmp_path))
    env.pop("RUNNER_TEMP", None)
    if key:
        env["RELAY_BUNDLE_KEY"] = key
    env.update(extra)
    return env


def _run(repo, script, env):
    return subprocess.run(["bash", str(repo / "scripts" / script)], cwd=str(repo), env=env, capture_output=True,
                          text=True, timeout=120)


def _make_db(path, rows=50, author="A. Writer"):
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, author TEXT, body TEXT)")
    conn.executemany("INSERT INTO articles(author, body) VALUES (?, ?)", [(author, "x" * 1000)] * rows)
    conn.commit()
    conn.close()


def _authors(path):
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT author FROM articles")}
    finally:
        conn.close()


def _bodies(repo, n=3):
    for i in range(n):
        d = repo / "data" / "bodies" / ("%02x" % i)
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%02x.txt.gz" % i)).write_bytes(b"body %d" % i)


# ---------------------------------------------------------------------------
# db_restore.sh: the cache
# ---------------------------------------------------------------------------

def test_a_cached_database_with_a_hot_journal_is_rolled_back_and_kept(tmp_path, gh):
    # A writer killed mid transaction: a tiny page cache spills changed pages into the file, then the
    # process dies without committing, leaving the hot journal beside it.
    killed = textwrap.dedent("""
        import os, sqlite3, sys
        c = sqlite3.connect(sys.argv[1], isolation_level=None)
        c.execute("PRAGMA journal_mode=DELETE"); c.execute("PRAGMA cache_size=10"); c.execute("PRAGMA cache_spill=1")
        c.execute("BEGIN"); c.execute("UPDATE articles SET author='torn', body=hex(randomblob(900))")
        os._exit(0)
    """)
    for n, sqlite_cli in enumerate(("sqlite3", str(tmp_path / "no-sqlite3"))):   # the CLI, then python
        repo = _repo(tmp_path, "r%d" % n)
        db = repo / "data" / "tracker.db"
        _make_db(db, rows=2000)
        before = db.read_bytes()
        subprocess.run([sys.executable, "-c", killed, str(db)], check=True)
        assert (repo / "data" / "tracker.db-journal").exists() and db.read_bytes() != before
        if n == 0:
            # What the cache passed on before it kept the journal: the file alone is torn.
            shutil.copy(db, tmp_path / "alone.db")
            assert sqlite3.connect(str(tmp_path / "alone.db")).execute("PRAGMA quick_check").fetchone()[0] != "ok"
        _bodies(repo)
        r = _run(repo, "db_restore.sh", _env(tmp_path, gh, SQLITE3=sqlite_cli))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "from cache, quick_check ok" in r.stdout
        assert _authors(db) == {"A. Writer"}              # the transaction was rolled back
        assert not (repo / "data" / "tracker.db-journal").exists()
    assert not (gh / "calls").exists()                    # nothing asked of the release


def test_a_torn_cached_database_is_set_aside_for_the_snapshot(tmp_path, gh):
    repo = _repo(tmp_path, "r")
    (repo / "data" / "tracker.db").write_bytes(b"SQLite format 3\x00" + b"\x07" * 9000)
    (repo / "data" / "tracker.db-journal").write_bytes(b"\x00" * 600)
    (gh / "assets").mkdir()
    _make_db(tmp_path / "snap.db", author="from snapshot")
    subprocess.run("gzip -c %s > %s" % (tmp_path / "snap.db", gh / "assets" / "tracker.db.gz"), shell=True, check=True)
    r = _run(repo, "db_restore.sh", _env(tmp_path, gh))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fails PRAGMA quick_check" in r.stdout and "restored from the public snapshot" in r.stdout
    assert _authors(repo / "data" / "tracker.db") == {"from snapshot"}
    assert not (repo / "data" / "tracker.db-journal").exists()   # the torn file's journal never meets the new one
    assert "no article bodies" in r.stdout


# ---------------------------------------------------------------------------
# db_restore.sh: the snapshot, and no silent fall back to the seed
# ---------------------------------------------------------------------------

def test_a_snapshot_that_cannot_be_downloaded_fails_after_three_tries(tmp_path, gh):
    repo = _repo(tmp_path, "r")
    (gh / "assets").mkdir()
    (gh / "assets" / "tracker.db.gz").write_bytes(b"x")
    (gh / "dlfail").touch()
    r = _run(repo, "db_restore.sh", _env(tmp_path, gh))
    assert r.returncode != 0
    assert (gh / "dl_attempts").read_text().count("x") == 3
    assert "could not be downloaded" in r.stdout and "seed" not in r.stdout
    assert not (repo / "data" / "tracker.db").exists()


def test_a_release_that_cannot_be_read_fails_rather_than_use_the_seed(tmp_path, gh):
    repo = _repo(tmp_path, "r")
    (gh / "assets").mkdir()
    (gh / "viewfail").touch()
    r = _run(repo, "db_restore.sh", _env(tmp_path, gh))
    assert r.returncode != 0 and "not falling back to the seed" in r.stdout
    assert (gh / "calls").read_text().count("release view") == 3


def test_no_release_at_all_starts_fresh(tmp_path, gh):
    repo = _repo(tmp_path, "r")
    (gh / "norelease").touch()
    r = _run(repo, "db_restore.sh", _env(tmp_path, gh))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "a fresh one will be created" in r.stdout
    assert (gh / "calls").read_text().count("release view") == 1      # "not found" is an answer, not a failure


def test_a_damaged_snapshot_fails_the_step(tmp_path, gh):
    repo = _repo(tmp_path, "r")
    (gh / "assets").mkdir()
    (tmp_path / "junk.db").write_bytes(b"not a database at all" * 200)
    subprocess.run("gzip -c %s > %s" % (tmp_path / "junk.db", gh / "assets" / "tracker.db.gz"), shell=True, check=True)
    r = _run(repo, "db_restore.sh", _env(tmp_path, gh))
    assert r.returncode != 0 and "fails PRAGMA quick_check" in r.stdout
    assert not (repo / "data" / "tracker.db").exists()


# ---------------------------------------------------------------------------
# db_snapshot.sh, and the round trip
# ---------------------------------------------------------------------------

def test_snapshot_with_the_key_publishes_scrubbed_and_encrypted_copies(tmp_path, gh):
    src = _repo(tmp_path, "src", scrubber=True)
    _make_db(src / "data" / "tracker.db")
    _bodies(src)
    r = _run(src, "db_snapshot.sh", _env(tmp_path, gh, key=KEY))
    assert r.returncode == 0, r.stdout + r.stderr
    assets = sorted(p.name for p in (gh / "assets").iterdir())
    assert assets == ["bodies.tar.gz.enc", "tracker-full.db.gz.enc", "tracker.db.gz"]
    # nothing readable in the encrypted assets, and the key never in the output
    for name in ("bodies.tar.gz.enc", "tracker-full.db.gz.enc"):
        blob = (gh / "assets" / name).read_bytes()
        assert blob.startswith(b"Salted__") and b"A. Writer" not in blob and b"body 0" not in blob
    assert KEY not in r.stdout + r.stderr
    subprocess.run("gunzip -c %s > %s" % (gh / "assets" / "tracker.db.gz", tmp_path / "pub.db"), shell=True, check=True)
    assert _authors(tmp_path / "pub.db") == {None}         # the public copy went through the scrubber
    assert _authors(src / "data" / "tracker.db") == {"A. Writer"}   # never the working database

    # A cache miss with the key: the whole database and the bodies come back.
    dst = _repo(tmp_path, "dst")
    r = _run(dst, "db_restore.sh", _env(tmp_path, gh, key=KEY))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "restored from the encrypted whole snapshot" in r.stdout
    assert _authors(dst / "data" / "tracker.db") == {"A. Writer"}
    assert (dst / "data" / "bodies" / "02" / "02.txt.gz").read_bytes() == b"body 2"
    assert "3 files added" in r.stdout

    # A cache hit with a whole database but bodies already there keeps both and asks for nothing more.
    calls = (gh / "calls").read_text()
    r = _run(dst, "db_restore.sh", _env(tmp_path, gh, key=KEY))
    assert r.returncode == 0 and "from cache" in r.stdout and (gh / "calls").read_text() == calls

    # Without the key: the public scrubbed copy, with a warning, and no bodies.
    bare = _repo(tmp_path, "bare")
    r = _run(bare, "db_restore.sh", _env(tmp_path, gh))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "public scrubbed snapshot is used" in r.stdout and "are not restored" in r.stdout
    assert _authors(bare / "data" / "tracker.db") == {None}

    # A wrong key is a failure, not a quiet fall back.
    wrong = _repo(tmp_path, "wrong")
    r = _run(wrong, "db_restore.sh", _env(tmp_path, gh, key="w" * 43))
    assert r.returncode != 0 and "could not be decrypted" in r.stdout
    assert not (wrong / "data" / "tracker.db").exists()


def test_bodies_restored_on_a_cache_miss_never_replace_a_file_already_there(tmp_path, gh):
    src = _repo(tmp_path, "src", scrubber=True)
    _make_db(src / "data" / "tracker.db")
    _bodies(src)
    assert _run(src, "db_snapshot.sh", _env(tmp_path, gh, key=KEY)).returncode == 0
    dst = _repo(tmp_path, "dst")
    (dst / "data" / "tracker.db").write_bytes(b"torn" * 100)
    (dst / "data" / "bodies" / "00").mkdir(parents=True)
    (dst / "data" / "bodies" / "00" / "00.txt.gz").write_bytes(b"newer")
    r = _run(dst, "db_restore.sh", _env(tmp_path, gh, key=KEY))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dst / "data" / "bodies" / "00" / "00.txt.gz").read_bytes() == b"newer"
    assert (dst / "data" / "bodies" / "01" / "01.txt.gz").read_bytes() == b"body 1"
    assert "2 files added, 3 in all" in r.stdout


def test_snapshot_without_the_key_never_publishes_the_whole_database_or_bodies(tmp_path, gh):
    src = _repo(tmp_path, "src", scrubber=True)
    _make_db(src / "data" / "tracker.db")
    _bodies(src)
    r = _run(src, "db_snapshot.sh", _env(tmp_path, gh, key="short"))     # a short key counts as none
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.name for p in (gh / "assets").iterdir()) == ["tracker.db.gz"]
    assert "RELAY_BUNDLE_KEY is not set" in r.stdout


def test_snapshot_without_the_scrubber_warns(tmp_path, gh):
    src = _repo(tmp_path, "src")
    _make_db(src / "data" / "tracker.db")
    r = _run(src, "db_snapshot.sh", _env(tmp_path, gh))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NOT scrubbed" in r.stdout


def test_a_database_failing_its_check_is_never_published(tmp_path, gh):
    src = _repo(tmp_path, "src", scrubber=True)
    (src / "data" / "tracker.db").write_bytes(b"SQLite format 3\x00" + b"\x07" * 9000)
    r = _run(src, "db_snapshot.sh", _env(tmp_path, gh, key=KEY))
    assert r.returncode != 0 and "no snapshot is published" in r.stdout
    assert not (gh / "assets").exists()


# ---------------------------------------------------------------------------
# relay.sh
# ---------------------------------------------------------------------------

FAKE_PY = """#!/bin/bash
# Stands in for the relay's python: records how it was called, then behaves as $FAKE_PY_MODE says.
echo "args=$* collector=${TRACKER_COLLECTOR:-} rewrite=${GIT_CONFIG_KEY_0:-none} keyfile=${RELAY_BUNDLE_KEY_FILE:-}" >> "$FAKE_PY_LOG"
case "${FAKE_PY_MODE:-ok}:$5" in
  sleep:*) exec sleep 60 ;;
  fail:run) exit 1 ;;
esac
exit 0
"""


def _relay_setup(tmp_path, origin_url):
    repo = _repo(tmp_path, "repo", scripts=("relay.sh",))
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *a], cwd=str(repo),
                                    check=True, capture_output=True)
    (repo / "README").write_text("x", encoding="utf-8")
    git("init", "--quiet")
    git("add", "README")
    git("commit", "--quiet", "-m", "c")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    if origin_url == "local":
        bare = tmp_path / "origin.git"
        subprocess.run(["git", "clone", "--quiet", "--bare", str(repo), str(bare)], check=True, capture_output=True)
        origin_url = str(bare)
    git("remote", "add", "origin", origin_url)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _exe(bin_dir / "fakepy", FAKE_PY)
    _exe(bin_dir / "caffeinate", '#!/bin/bash\necho "$*" >> "$FAKE_PY_LOG.caffeinate"\n')
    (tmp_path / "home").mkdir()
    return repo


def _relay(tmp_path, repo, mode="ok", wall="2700", protocols="file"):
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("GIT_"):
            env.pop(k)
    env.update(PATH="%s:%s" % (tmp_path / "bin", env.get("PATH", "")), HOME=str(tmp_path / "home"),
               TRACKER_RELAY_WORKTREE=str(tmp_path / "wt"), TRACKER_RELAY_PYTHON=str(tmp_path / "bin" / "fakepy"),
               TRACKER_RELAY_WALL_SECONDS=wall, TRACKER_RELAY_POLL_SECONDS="1", TRACKER_RELAY_SSH="false",
               GIT_ALLOW_PROTOCOL=protocols, FAKE_PY_LOG=str(tmp_path / "py.log"), FAKE_PY_MODE=mode)
    r = subprocess.run(["bash", str(repo / "scripts" / "relay.sh")], env=env, capture_output=True, text=True, timeout=120)
    log = "".join(p.read_text() for p in (repo / "logs").glob("relay-*.log"))
    calls = (tmp_path / "py.log").read_text().splitlines() if (tmp_path / "py.log").exists() else []
    return r, log, calls


def test_relay_runs_over_ssh_and_pushes_again_over_https_when_the_run_fails(tmp_path):
    repo = _relay_setup(tmp_path, "local")                # reachable: the probe passes
    r, log, calls = _relay(tmp_path, repo, mode="fail")
    assert r.returncode == 0, r.stderr
    assert len(calls) == 2, log
    assert "args=-X faulthandler -m pipeline.relay run --budget-minutes 30" in calls[0]
    assert "collector=self_hosted" in calls[0] and "rewrite=none" in calls[0]
    assert "keyfile=%s" % (tmp_path / "home" / ".config" / "china-media-tracker" / "relay-bundle.key") in calls[0]
    assert "pipeline.relay bundle" in calls[1] and "rewrite=url.https://github.com/.insteadOf" in calls[1]
    assert "pushing the bundle again over https" in log and log.rstrip().endswith("status 0")
    assert (tmp_path / "wt" / ".git").exists()


def test_relay_uses_https_from_the_start_when_ssh_cannot_reach_origin(tmp_path):
    repo = _relay_setup(tmp_path, "git@github.com:example/none.git")   # ssh and https both refused here
    r, log, calls = _relay(tmp_path, repo)
    assert r.returncode == 0, r.stderr
    assert "cannot be reached over ssh; this run uses https" in log
    assert len(calls) == 1 and "pipeline.relay run" in calls[0]
    assert "rewrite=url.https://github.com/.insteadOf" in calls[0]


def test_relay_is_stopped_by_the_wall_clock_and_held_awake(tmp_path):
    repo = _relay_setup(tmp_path, "local")
    r, log, calls = _relay(tmp_path, repo, mode="sleep", wall="2")
    assert r.returncode == 0, r.stderr
    assert "wall-clock stop" in log and log.rstrip().endswith("status 124")
    assert len(calls) == 1                                # no second push after a stop
    caff = (tmp_path / "py.log.caffeinate").read_text().split()
    assert caff[:2] == ["-i", "-w"] and caff[2].isdigit()


# ---------------------------------------------------------------------------
# db_pull.sh: the local copy prefers the whole database
# ---------------------------------------------------------------------------

def _published(tmp_path, gh, key=KEY):
    src = _repo(tmp_path, "src", scrubber=True)
    _make_db(src / "data" / "tracker.db")
    _bodies(src)
    assert _run(src, "db_snapshot.sh", _env(tmp_path, gh, key=key)).returncode == 0


def _pull(tmp_path, gh, name, key=None, keyfile=None):
    repo = _repo(tmp_path, name, scripts=("db_pull.sh",))
    _make_db(repo / "data" / "tracker.db", rows=1, author="local")
    home = tmp_path / ("home-" + name)
    if keyfile is not None:
        (home / ".config" / "china-media-tracker").mkdir(parents=True)
        (home / ".config" / "china-media-tracker" / "relay-bundle.key").write_text(keyfile + "\n", encoding="utf-8")
    env = _env(tmp_path, gh, key=key, HOME=str(home))
    env.pop("RELAY_BUNDLE_KEY_FILE", None)
    r = _run(repo, "db_pull.sh", env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert KEY not in r.stdout + r.stderr
    assert [_authors(p) for p in (repo / "data").glob("tracker.db.local-*.bak")] == [{"local"}]
    return repo, r.stdout


def test_pull_prefers_the_whole_database_with_the_key_from_the_relay_file_or_the_environment(tmp_path, gh):
    _published(tmp_path, gh)
    repo, out = _pull(tmp_path, gh, "file", keyfile=KEY)
    assert "from the whole database (decrypted)" in out
    assert _authors(repo / "data" / "tracker.db") == {"A. Writer"}
    repo, out = _pull(tmp_path, gh, "env", key=KEY)
    assert "from the whole database (decrypted)" in out and _authors(repo / "data" / "tracker.db") == {"A. Writer"}


def test_pull_falls_back_to_the_scrubbed_copy_and_says_why(tmp_path, gh):
    _published(tmp_path, gh)
    repo, out = _pull(tmp_path, gh, "nokey")
    assert "no relay key" in out and "public scrubbed copy" in out and "no authors or summaries" in out
    assert _authors(repo / "data" / "tracker.db") == {None}
    repo, out = _pull(tmp_path, gh, "short", keyfile="short")
    assert "no relay key" in out and _authors(repo / "data" / "tracker.db") == {None}
    repo, out = _pull(tmp_path, gh, "wrong", key="w" * 43)
    assert "could not be decrypted" in out and _authors(repo / "data" / "tracker.db") == {None}


def test_pull_says_when_the_release_has_no_whole_copy(tmp_path, gh):
    _published(tmp_path, gh, key=None)               # published before the secret existed
    repo, out = _pull(tmp_path, gh, "early", key=KEY)
    assert "has no whole copy" in out and _authors(repo / "data" / "tracker.db") == {None}
