"""The operator can require Claude and prevent every automatic switch to a scripted call. No providers."""
import logging

import pytest

from checkin_backend.app import create_app
from checkin_backend.config import Settings
from checkin_backend.delivery import FamilyDelivery
from checkin_backend import runner
from checkin_backend.speech import SpeechGateway
from tests.client_sim import SimClient
from tests.test_call_service import KEY, Server, run_call
from tests import test_analysis_calls as analysis_calls


@pytest.mark.parametrize("value,expected", [("", True), ("on", True), ("off", False), (" OFF ", False)])
def test_environment_setting(value, expected, monkeypatch):
    monkeypatch.setenv("DEMO_KEY", KEY)
    monkeypatch.setenv("CALL_DRIVER", "claude")
    monkeypatch.setenv("UW_CLAUDE_FALLBACK", value)
    assert Settings.from_environment().claude_fallback is expected


@pytest.mark.parametrize("driver", ["", "fixed"])
def test_strict_mode_rejects_a_startup_downgrade_to_fixed(driver, monkeypatch):
    monkeypatch.setenv("DEMO_KEY", KEY)
    monkeypatch.setenv("CALL_DRIVER", driver)
    monkeypatch.delenv("UW_CLAUDE", raising=False)
    monkeypatch.setenv("UW_CLAUDE_FALLBACK", "off")
    with pytest.raises(ValueError, match="requires CALL_DRIVER=claude"):
        Settings.from_environment()


def test_strict_mode_supports_the_existing_claude_switch(monkeypatch):
    monkeypatch.setenv("DEMO_KEY", KEY)
    monkeypatch.delenv("CALL_DRIVER", raising=False)
    monkeypatch.setenv("UW_CLAUDE", "on")
    monkeypatch.setenv("UW_CLAUDE_FALLBACK", "off")
    assert Settings.from_environment().claude_fallback is False


def test_invalid_value_is_rejected_before_startup(monkeypatch):
    monkeypatch.setenv("DEMO_KEY", KEY)
    monkeypatch.setenv("UW_CLAUDE_FALLBACK", "yes")
    with pytest.raises(ValueError, match="UW_CLAUDE_FALLBACK must be on or off"):
        Settings.from_environment()


def test_explicit_scripted_runner_cannot_bypass_the_setting():
    with pytest.raises(ValueError, match="CALL_RUNNER=scripted"):
        Settings(demo_key=KEY, call_runner="scripted", claude_fallback=False)
    with pytest.raises(ValueError, match="CALL_RUNNER=scripted"):
        runner.choose_driver("scripted", allow_fallback=False)


def test_missing_analysis_does_not_substitute_a_script_at_startup(monkeypatch):
    monkeypatch.setattr(runner, "measuring_problems", lambda: ["analysis unavailable"])
    with pytest.raises(RuntimeError, match="scripted call is disabled"):
        create_app(Settings(demo_key=KEY, claude_fallback=False), speech=SpeechGateway(api_key=""))


@pytest.mark.anyio
@pytest.mark.parametrize("with_concern", [False, True])
async def test_strict_claude_failure_closes_the_phone_call_without_scripted_questions(
    monkeypatch, tmp_path, caplog, with_concern
):
    from analysis.session import run_call as driver
    from analysis.session.claude_client import FakeClaudeClient
    from analysis.tests.test_turn_plan import plan

    analysis_calls.install(monkeypatch, tmp_path)
    caplog.set_level(logging.INFO, logger="checkin_backend.session_logging")
    monkeypatch.setenv("CALL_DRIVER", "claude")
    script = [RuntimeError("provider failed")]
    if with_concern:
        script.insert(0, plan("ask", {"question_id": "q_medicine"}, greet=True))
        script.insert(1, plan("ask", {"question_id": "q_feeling"},
                              concern={"reason": "asked_for_help", "note": "She asked for help."}))
    client = FakeClaudeClient(script)
    monkeypatch.setattr(driver, "build_client", lambda backend: client)
    publications = []
    original = FamilyDelivery.publish

    def publish(self, *args, **kwargs):
        publications.append((args, kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(FamilyDelivery, "publish", publish)
    async with Server("analysis", claude_fallback=False) as server:
        sim = SimClient(base=server.base, key=KEY, time_scale=0.01)
        status = await run_call(server, sim)
    assert status["reason"] == "connection_lost" and status["state"] == "ended"
    assert status["message"]["kind"] == ("concern_raised" if with_concern else "incomplete")
    assert "q_unusual" not in sim.said and "task_smile" not in sim.said and "task_arms" not in sim.said
    assert sim.said[-1] == "bye" and len(publications) == 1
    events = [record.getMessage() for record in caplog.records]
    assert "session.agent_failed" in events
    assert "session.agent_fallback" not in events and "session.fallback_step" not in events
    if with_concern:
        assert status["message"]["text"].endswith("If she seems unwell, call 112.")
