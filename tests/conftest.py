"""Shared test setup.

Tests seed articles discovered seconds ago. With the session grace period in force
(config.LLM_SESSION_GRACE_DAYS) every one of them would be held for the session route and the model
stage would make no call, so the hold is off by default here. Tests of the hold set it themselves.

No test touches the network: a test that opens a socket to anything but this machine, or looks a
host up, fails (_no_network). And every test starts with no AAP release read in progress
or slow feed window (_fresh_run_state): a run left set up by an earlier test made a later
fetch_feeds.run read the real AAP listing page by page at the per domain rate, which took ten minutes.

sources/outlets.yaml is parsed once per version of the file rather than once per call
(_parsed_registry_once): the pure Python YAML parse of the registry takes several seconds, and the
export, discovery, fetch and classification stages each load it.
"""
import pickle
import socket
from pathlib import Path

import pytest

from pipeline import config, fetch_feeds, registry, section_pages


@pytest.fixture(autouse=True)
def _no_session_grace(monkeypatch):
    monkeypatch.setattr(config, "LLM_SESSION_GRACE_DAYS", 0)


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", None}


class NetworkBlocked(RuntimeError):
    """A test tried to reach the network. Not an OSError, so requests and urllib3 do not turn it
    into an ordinary connection failure that the code under test would handle quietly."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail any test that looks up a host or opens a socket to anything but this machine. The
    attempt raises NetworkBlocked where it happens and is recorded, so a test whose code swallows
    the exception (a broad except in a worker) still fails at teardown."""
    attempts = []
    real_connect, real_connect_ex, real_getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def _local(sock, address):
        return sock.family == getattr(socket, "AF_UNIX", object()) or (
            isinstance(address, tuple) and address and address[0] in _LOCAL_HOSTS)

    def _block(what):
        attempts.append(what)
        raise NetworkBlocked("test tried to reach the network: %s (use a fake fetcher or fixture)" % (what,))

    def connect(sock, address):
        return real_connect(sock, address) if _local(sock, address) else _block(address)

    def connect_ex(sock, address):
        return real_connect_ex(sock, address) if _local(sock, address) else _block(address)

    def getaddrinfo(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        return real_getaddrinfo(host, *args, **kwargs) if name in _LOCAL_HOSTS else _block(name)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield
    if attempts:
        pytest.fail("test tried to reach the network: %s" % ", ".join(map(str, attempts)), pytrace=False)


@pytest.fixture(autouse=True)
def _fresh_run_state(monkeypatch):
    """No AAP listing read (section_pages.aap_start) and no slow feed window (fetch_feeds.run) carried
    over from another test; both are module state a discovery run sets up and leaves behind."""
    monkeypatch.setattr(section_pages, "_aap", {"since": None, "listing": None})
    monkeypatch.setattr(fetch_feeds, "_slow_feeds", {"started": None, "deadline": None, "polled": {}})


_REGISTRY_CACHE = {}


@pytest.fixture(autouse=True)
def _parsed_registry_once(monkeypatch):
    """registry.load_outlets on the real registry file, parsed and validated once per version of the
    file (path, mtime, size) for the whole session; every call gets its own fresh copy. Other paths
    and tests that replace load_outlets themselves behave as before."""
    real = registry.load_outlets
    default_path = real.__defaults__[0]

    def load_outlets(path=default_path, validate=True):
        p = Path(path)
        if p != Path(default_path):
            return real(path, validate)
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
        entry = _REGISTRY_CACHE.get(key)
        if entry is None:
            entry = _REGISTRY_CACHE[key] = {"blob": pickle.dumps(real(path, validate=False)), "valid": False}
        outlets = pickle.loads(entry["blob"])
        if validate and not entry["valid"]:
            registry.validate_outlets(outlets)       # raises as the real loader would, every call
            entry["valid"] = True
        return outlets
    monkeypatch.setattr(registry, "load_outlets", load_outlets)
