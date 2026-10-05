"""Shared test setup.

Tests seed articles discovered seconds ago. With the session grace period in force
(config.LLM_SESSION_GRACE_DAYS) every one of them would be held for the session route and the model
stage would make no call, so the hold is off by default here. Tests of the hold set it themselves.
"""
import pytest

from pipeline import config


@pytest.fixture(autouse=True)
def _no_session_grace(monkeypatch):
    monkeypatch.setattr(config, "LLM_SESSION_GRACE_DAYS", 0)
