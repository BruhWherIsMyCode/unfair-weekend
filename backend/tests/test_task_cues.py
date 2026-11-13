import pytest

from checkin_backend import runner
from checkin_backend.fake_tasks import fake_modules
from checkin_backend.runner import CUES
from tests.client_sim import SimClient
from tests.test_call_service import KEY, Server, run_call


@pytest.mark.anyio
async def test_scripted_call_plays_now_smile_and_swap_during_their_captures(monkeypatch):
    # The simulated phone runs at a tenth of real time; scale the cue times the same way.
    scaled = {task: [{**cue, "at_ms": cue["at_ms"] // 10} for cue in cues] for task, cues in CUES.items()}
    monkeypatch.setattr(runner, "CUES", scaled)
    async with Server("scripted") as server:
        sim = SimClient(base=server.base, key=KEY)
        status = await run_call(server, sim)
    assert status["reason"] == "completed"
    order = [data["command"].get("line_id") or "capture:" + data["command"]["purpose"].get("task", "")
             for kind, data in sim.seen if kind == "command.execute"]
    smile, arms, sentence = order.index("capture:smile"), order.index("capture:arms"), order.index("capture:sentence")
    assert smile < order.index("smile_now") < arms
    assert arms < order.index("swap_hands") < sentence
    assert not [e for e in sim.seen if e[0] == "client_error"]


def test_fake_modules_carry_the_same_cues_as_the_analysis():
    modules = fake_modules()
    assert modules["smile"].definition()["cues"] == CUES["smile"] == [{"at_ms": 1500, "line_id": "smile_now"}]
    assert modules["arms"].definition()["cues"] == CUES["arms"] == [{"at_ms": 7000, "line_id": "swap_hands"}]
    assert modules["sentence"].definition()["cues"] == []
