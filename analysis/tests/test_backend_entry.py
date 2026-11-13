"""analysis.backend_entry.run_backend_call: whole calls on a fake link, the real final-result code of the smile and arms
tasks on made-up readings, a temporary baseline store. No mediapipe, no network, no Claude. Real clock, but the modules
answer at once, so a call takes well under a second."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from analysis import backend_entry
from analysis.pc.store import BaselineStore
from analysis.session import limits
from analysis.session.adapters import FakeAdapter
from analysis.tasks import stream_arms, stream_smile
from analysis.tasks.stream_arms import StreamArms, hold_windows
from analysis.tasks.stream_smile import StreamSmile
from analysis.tests.agent_helpers import FakeHands, FakeSmile

PERSON = "p_t"
SEED = ({"corner_height_v1": 0.0, "arms.hand_left.hand_sink": 0.0, "arms.hand_right.hand_sink": 0.0},
        {"corner_height_v1": 0.01, "arms.hand_left.hand_sink": 0.01, "arms.hand_right.hand_sink": 0.01})


class Link(FakeAdapter):
    """A FakeAdapter that also has what the backend's ClientLink has besides the CallAdapter methods."""

    def __init__(self, label: str = "live") -> None:
        super().__init__(person_name="Ona")
        self.call_id = "call_t1"
        self.person = SimpleNamespace(person_id=PERSON, name="Ona", label=label)

    def attach(self, session: Any) -> None:
        self.session = session

    def initial_resources(self) -> Dict[str, str]:
        return {"camera": "available", "microphone": "available"}


class InstantSmile:
    """The real smile final-result code on made-up frames, answering at once. corners: value per attempt."""

    name = "smile"

    def __init__(self, corners=(0.01,)) -> None:
        self.corners, self.attempts = list(corners), 0

    def definition(self) -> Dict[str, Any]:
        return StreamSmile().definition()

    async def run(self, context: Any, samples: Any, emit: Any) -> Any:
        fake = FakeSmile()
        fake.corner = self.corners[min(self.attempts, len(self.corners) - 1)]
        self.attempts += 1
        return stream_smile.final_result(context, [fake(None, ts) for ts in range(0, 5000, 200)])


class InstantArms:
    def __init__(self, sinks=({},)) -> None:
        self.sinks, self.attempts, self._values = list(sinks), 0, {}

    name = "arms"

    def definition(self) -> Dict[str, Any]:
        return StreamArms().definition()

    def attempt_values(self, attempt_id: str) -> Dict[str, float]:
        return dict(self._values.get(attempt_id, {}))

    async def run(self, context: Any, samples: Any, emit: Any) -> Any:
        hands = FakeHands(self.sinks[min(self.attempts, len(self.sinks) - 1)])
        self.attempts += 1
        frames = {side: [hands(None, ts, side) for ts in range(start, end, 200)]
                  for side, (start, end) in hold_windows().items()}
        result, values = stream_arms.final_result(context, frames)
        if values:
            self._values[context.attempt_id] = values
        return result


def seeded_env(tmp_path: Path, **extra: str) -> Dict[str, str]:
    store = BaselineStore(PERSON, tmp_path / "data", 2)
    for values in SEED:
        assert store.append("normal", values) is True
    env = {"UW_DATA_DIR": str(tmp_path / "data"), "UW_RUNS_DIR": str(tmp_path / "runs"), "UW_BASELINE_NEEDED": "2",
           "CALL_DRIVER": "fixed"}
    env.update(extra)
    return env


def modules(corners=(0.01,), sinks=({},)) -> Dict[str, Any]:
    return {"smile": InstantSmile(corners), "arms": InstantArms(sinks)}


def run(link: Link, env: Dict[str, str], mods: Dict[str, Any] | None = None, lines: List[str] | None = None) -> Dict:
    sink = lines if lines is not None else []
    return asyncio.run(backend_entry.run_backend_call(link, mods if mods is not None else modules(), env=env,
                                                      out=sink.append))


def usual(tmp_path: Path) -> Dict[str, Any]:
    return BaselineStore(PERSON, tmp_path / "data", 2).load_baselines()


# ---- settings ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,value", [("CALL_DRIVER", "gpt"), ("UW_CLAUDE_BACKEND", "bedrock"),
                                        ("UW_MIRRORED", "yes"), ("UW_BASELINE_NEEDED", "0"),
                                        ("UW_BASELINE_NEEDED", "-3"), ("UW_BASELINE_NEEDED", "five"),
                                        ("UW_CALL_LOG", "loud")])
def test_an_invalid_value_is_a_clear_error_before_the_call_starts(name, value, tmp_path):
    link = Link()
    with pytest.raises(ValueError, match=name):
        run(link, seeded_env(tmp_path, **{name: value}))
    assert link.calls == []  # nothing was said, nothing published


def test_defaults_and_the_documented_values():
    s = backend_entry.Settings({}, "p_x")
    assert (s.mode, s.claude_backend, s.safety_floor, s.mirrored, s.baseline_needed) == ("fixed", "api", True, False, None)
    assert (s.person_key, s.data_dir, s.call_log) == ("p_x", None, True)
    s = backend_entry.Settings({"UW_CLAUDE": "on"}, "p_x")
    assert s.mode == "claude"  # the existing switch, when CALL_DRIVER is not set
    s = backend_entry.Settings({"UW_CLAUDE": "on", "CALL_DRIVER": "fixed", "UW_SAFETY_FLOOR": "off",
                                "UW_MIRRORED": "on", "UW_BASELINE_NEEDED": "3", "UW_BASELINE_PERSON": "feodor",
                                "UW_DATA_DIR": "/x/data", "UW_CALL_LOG": "off"}, "p_x")
    assert (s.mode, s.safety_floor, s.mirrored, s.baseline_needed, s.person_key) == ("fixed", False, True, 3, "feodor")
    assert s.data_dir == Path("/x/data") and s.call_log is False


def test_the_default_modules_are_new_per_call_and_have_no_sentence():
    a, b = backend_entry.default_modules(True), backend_entry.default_modules(False)
    assert set(a) == {"smile", "arms"} and a["smile"] is not b["smile"] and a["arms"] is not b["arms"]
    assert a["smile"].mirrored is True and a["arms"].mirrored is True and b["smile"].mirrored is False
    assert limits.REQUIRED_TASKS == ("smile", "arms")


# ---- whole calls -------------------------------------------------------------------------------------------------

def test_a_normal_call_with_a_ready_usual_is_as_usual_and_adds_her_values(tmp_path):
    env = seeded_env(tmp_path)
    assert usual(tmp_path)["smile"]["ready"] is True
    link, lines = Link(), []
    summary = run(link, env, lines=lines)
    assert summary["status"] == "as_usual" and summary["baseline_needed"] == 2
    assert [p["phase"] for p in link.published] == ["final"]
    assert "swap_hands" not in link.said  # the instant module never reaches the 5 s cue
    after = usual(tmp_path)
    assert after["smile"]["sessions"] == 3 and after["arms"]["sessions"] == 3
    record = json.loads((tmp_path / "runs" / "call_t1.json").read_text("utf-8"))
    assert record["summary"]["status"] == "as_usual" and record["person"] == PERSON
    assert record["family_text"].endswith("looked as usual.")
    assert lines and all(line.startswith("[call call_t1] ") for line in lines)
    assert any("task smile" in line for line in lines) and any("family message" in line for line in lines)


def test_the_baseline_person_and_data_dir_come_from_the_environment(tmp_path):
    env = seeded_env(tmp_path, UW_BASELINE_PERSON="feodor")
    for values in SEED:
        BaselineStore("feodor", tmp_path / "data", 2).append("normal", values)
    assert run(Link(), env)["status"] == "as_usual"
    assert BaselineStore("feodor", tmp_path / "data", 2).load_baselines()["smile"]["sessions"] == 3
    assert usual(tmp_path)["smile"]["sessions"] == 2  # the person id's own file was not touched


def test_a_learning_usual_says_learning_and_still_collects_values(tmp_path):
    env = {"UW_DATA_DIR": str(tmp_path / "data"), "UW_RUNS_DIR": str(tmp_path / "runs"), "UW_BASELINE_NEEDED": "2",
           "CALL_DRIVER": "fixed"}
    summary = run(Link(), env)
    assert summary["status"] == "learning" and summary["baseline_sessions"] == 0
    assert BaselineStore(PERSON, tmp_path / "data", 2).load_baselines()["smile"]["sessions"] == 1


def test_two_outside_smiles_publish_a_change_and_nothing_is_added(tmp_path):
    env = seeded_env(tmp_path)
    link = Link()
    summary = run(link, env, modules(corners=(0.11, 0.11)))
    assert summary["status"] == "change_noticed"
    assert [p["phase"] for p in link.published] == ["mid_call", "final"]
    assert usual(tmp_path)["smile"]["sessions"] == 2 and usual(tmp_path)["arms"]["sessions"] == 2


def test_one_outside_reading_then_inside_is_as_usual_but_adds_nothing(tmp_path):
    env = seeded_env(tmp_path)
    summary = run(Link(), env, modules(corners=(0.11, 0.01)))
    assert summary["status"] == "as_usual"
    assert usual(tmp_path)["smile"]["sessions"] == 2  # a call with an outside reading is not a normal call


def test_a_call_labelled_acted_adds_nothing(tmp_path):
    env = seeded_env(tmp_path)
    summary = run(Link(label="acted"), env)
    assert summary["label"] == "acted"
    assert usual(tmp_path)["smile"]["sessions"] == 2 and usual(tmp_path)["arms"]["sessions"] == 2


def test_the_safety_floor_can_be_turned_off_by_the_environment(tmp_path):
    env = seeded_env(tmp_path, UW_SAFETY_FLOOR="off")
    link = Link()
    run(link, env, modules(corners=(0.11, 0.11)))
    assert [p["phase"] for p in link.published] == ["final"]  # nothing mid-call


def test_the_live_log_can_be_turned_off(tmp_path):
    lines: List[str] = []
    run(Link(), seeded_env(tmp_path, UW_CALL_LOG="off"), lines=lines)
    assert lines == []
    assert (tmp_path / "runs" / "call_t1.json").exists()  # the record is still written


# ---- failures never reach the backend ----------------------------------------------------------------------------

def test_a_crash_inside_run_call_gives_the_session_result(tmp_path, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("driver bug")

    monkeypatch.setattr(backend_entry, "run_call", boom)
    summary = run(Link(), seeded_env(tmp_path))
    assert summary["status"] == "incomplete"
    record = json.loads((tmp_path / "runs" / "call_t1.json").read_text("utf-8"))
    assert any(e["kind"] == "call_crashed" for e in record["session_log"])


def test_a_failing_baseline_store_is_logged_not_raised(tmp_path, monkeypatch):
    class Broken:
        def load_baselines(self):
            return {}

        def append(self, label, values):
            raise OSError("disk full")

    monkeypatch.setattr(backend_entry, "make_baseline_source", lambda *a, **k: Broken())
    summary = run(Link(), seeded_env(tmp_path))
    assert summary["status"] in ("learning", "incomplete", "as_usual")
    record = json.loads((tmp_path / "runs" / "call_t1.json").read_text("utf-8"))
    assert any(e["kind"] == "baseline_store_failed" for e in record["session_log"])


def test_an_unwritable_runs_dir_is_not_an_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", "utf-8")
    env = seeded_env(tmp_path, UW_RUNS_DIR=str(blocker / "runs"))  # a folder under a file cannot be created
    assert run(Link(), env)["status"] == "as_usual"


# ---- driver selection --------------------------------------------------------------------------------------------

def test_claude_through_the_subscription_removes_the_api_key_and_passes_the_backend(tmp_path, monkeypatch):
    seen: Dict[str, Any] = {}

    async def fake_run_call(session, adapter, mode=None, client=None, script=None, agent_clock=None, backend="api"):
        seen.update(mode=mode, backend=backend, key=backend_entry.os.environ.get("ANTHROPIC_API_KEY"))
        return session.build_session_result().to_dict()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setattr(backend_entry, "run_call", fake_run_call)
    run(Link(), seeded_env(tmp_path, CALL_DRIVER="claude", UW_CLAUDE_BACKEND="subscription"))
    assert seen == {"mode": "claude", "backend": "subscription", "key": None}
    run(Link(), seeded_env(tmp_path, CALL_DRIVER="fixed", UW_CLAUDE_BACKEND="subscription"))
    assert seen["mode"] == "fixed"


def test_the_api_backend_keeps_the_key_in_place(tmp_path, monkeypatch):
    seen: Dict[str, Any] = {}

    async def fake_run_call(session, adapter, mode=None, client=None, script=None, agent_clock=None, backend="api"):
        seen.update(mode=mode, backend=backend, key=backend_entry.os.environ.get("ANTHROPIC_API_KEY"))
        return session.build_session_result().to_dict()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setattr(backend_entry, "run_call", fake_run_call)
    run(Link(), seeded_env(tmp_path, CALL_DRIVER="claude"))
    assert seen == {"mode": "claude", "backend": "api", "key": "not-a-real-key"}
