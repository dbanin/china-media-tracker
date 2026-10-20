"""registry.load_outlets parses each version of the registry file once (path, mtime, size), with the C
YAML loader where PyYAML has it, and hands every caller its own copy. The 2026-10-17 audit measured the
pure Python parse at 2.5 s, four times an export, 40 percent of a steady export."""
import os

import pytest
import yaml

from pipeline import config, registry


def _copy(tmp_path, name="outlets.yaml"):
    p = tmp_path / name
    p.write_text(config.OUTLETS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return p


def test_c_loader_used_when_available():
    if hasattr(yaml, "CSafeLoader"):
        assert registry._YAML_LOADER is yaml.CSafeLoader
    with open(config.OUTLETS_PATH, encoding="utf-8") as fh:
        text = fh.read()
    assert yaml.load(text, Loader=registry._YAML_LOADER) == yaml.safe_load(text)


def test_parsed_once_per_version_and_every_caller_gets_its_own_copy(tmp_path, monkeypatch):
    p = _copy(tmp_path)
    calls = []
    real = yaml.load
    monkeypatch.setattr(yaml, "load", lambda *a, **k: calls.append(1) or real(*a, **k))
    a = registry.load_outlets(p)
    b = registry.load_outlets(p)
    assert len(calls) == 1 and a == b and a is not b
    a[0]["id"] = "changed"
    assert registry.load_outlets(p)[0]["id"] == b[0]["id"] != "changed"
    # a new version of the file is parsed again
    text = p.read_text(encoding="utf-8")
    p.write_text(text + "\n", encoding="utf-8")
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 10 ** 9))
    registry.load_outlets(p)
    assert len(calls) == 2


def test_an_invalid_registry_raises_on_every_call(tmp_path):
    p = _copy(tmp_path, "bad.yaml")
    outlets = yaml.safe_load(p.read_text(encoding="utf-8"))
    outlets.append(dict(outlets[0]))            # a duplicate id
    p.write_text(yaml.safe_dump(outlets, allow_unicode=True), encoding="utf-8")
    for _ in range(2):
        with pytest.raises(ValueError):
            registry.load_outlets(p)
    assert len(registry.load_outlets(p, validate=False)) == len(outlets)
