"""Shared pytest fixtures for the analysis tests."""
from __future__ import annotations

import pytest

from analysis.session import limits


@pytest.fixture
def two_readings(monkeypatch):
    """The two-reading confirmation rule (limits.CONFIRM_FROM_ONE_READING off): the same task outside twice, or the
    face and an arm on the same side. The demo default since v12b confirms from one reading."""
    monkeypatch.setattr(limits, "CONFIRM_FROM_ONE_READING", False)
