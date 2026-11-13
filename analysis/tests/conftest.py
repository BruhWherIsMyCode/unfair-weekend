"""Shared pytest fixtures for the analysis tests."""
from __future__ import annotations

import pytest

from analysis.session import limits


@pytest.fixture
def two_readings(monkeypatch):
    """The two-reading confirmation rule (limits.CONFIRM_FROM_ONE_READING off): the same task outside twice, or the
    face and an arm on the same side. The demo default since v12b confirms from one reading."""
    monkeypatch.setattr(limits, "CONFIRM_FROM_ONE_READING", False)


@pytest.fixture
def alert_mid_call(monkeypatch):
    """A confirmed change is told mid-call (limits.CHANGE_ALERT_AT_END off). The demo default since v12b tells it
    when the call finishes."""
    monkeypatch.setattr(limits, "CHANGE_ALERT_AT_END", False)
