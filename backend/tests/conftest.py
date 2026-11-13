"""Keep every test call away from the developer's own baseline folder and call records."""

import pytest


@pytest.fixture(autouse=True)
def isolated_analysis_files(tmp_path, monkeypatch):
    """Agent 3's backend entry stores baselines and call records under UW_DATA_DIR / UW_RUNS_DIR. A test that needs
    its own folders sets them again after this fixture."""
    monkeypatch.setenv("UW_DATA_DIR", str(tmp_path / "uw_data"))
    monkeypatch.setenv("UW_RUNS_DIR", str(tmp_path / "uw_runs"))
    monkeypatch.setenv("UW_CALL_LOG", "off")


@pytest.fixture(autouse=True)
def measuring_is_ready(monkeypatch):
    """The test environment has no mediapipe or model files: pretend Agent 3's self-check finds no problem, so
    CALL_RUNNER=auto/analysis tests (which inject their own frame readers) keep working. A test that needs real
    problems sets analysis.selfcheck.check again."""
    from checkin_backend.runner import ensure_analysis_path

    if not ensure_analysis_path():
        return
    from analysis import selfcheck

    monkeypatch.setattr(selfcheck, "check", lambda *args, **kwargs: [])
