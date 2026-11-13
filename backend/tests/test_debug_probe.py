import base64

import httpx
import pytest

from checkin_backend.debug_probe import DebugUnavailable, FrameProbe, plain
from tests.client_sim import tiny_jpeg
from tests.test_call_service import KEY, Server

pytest.importorskip("analysis.session.models", reason="Agent 3's analysis package is not on the path")

JPEG = tiny_jpeg()


class FakeArms:
    """Stands in for StreamArms: reports every frame to the probe, one observation per sample, then a result."""

    def __init__(self, probe: FrameProbe) -> None:
        self.probe = probe

    def definition(self):
        return {"capture": {"duration_ms": 15000, "sample_period_ms": 200, "format": "jpeg"},
                "cues": [{"at_ms": 6500, "line_id": "swap_hands"}]}

    async def run(self, context, samples, emit):
        count = 0
        async for sample in samples:
            count += 1
            self.probe._keep({"task": "arms", "ms": sample["captured_ms"], "face": True,
                              "hands": [{"side": "her_right", "eye_widths": 1.5}], "expected": "left"})
            emit({"elapsed_ms": sample["captured_ms"], "quality": {"status": "unusable",
                                                                   "reasons": ["wrong_hand_use_your_left_hand"]}})
        return {"task": "arms", "attempt_id": context.attempt_id, "outcome": "completed", "frames": count,
                "baseline": context.baseline}


@pytest.mark.anyio
async def test_debug_run_reports_frames_observations_and_result():
    async with Server("scripted") as server:
        desk = server.app.state.debug
        desk.build = lambda task, mirrored, probe: (FakeArms(probe), {"snapshot_id": "b1", "ready": False})
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            started = await http.post("/v1/debug/runs", json={"task": "arms"})
            assert started.status_code == 201
            run = started.json()
            assert run["task"] == "arms" and run["cues"][0]["line_id"] == "swap_hands"
            frame = {"sequence": 0, "captured_ms": 0, "data_base64": base64.b64encode(JPEG).decode()}
            first = (await http.post(f"/v1/debug/runs/{run['run_id']}/samples", json=frame)).json()
            await http.post(f"/v1/debug/runs/{run['run_id']}/samples", json={**frame, "sequence": 1, "captured_ms": 200})
            done = (await http.post(f"/v1/debug/runs/{run['run_id']}/finish")).json()
            gone = await http.post("/v1/debug/runs/dbg_nope/finish")
            denied = await httpx.AsyncClient(base_url=server.base).post("/v1/debug/runs", json={"task": "arms"})
    assert first["run_id"] == run["run_id"]
    assert done["done"] and done["error"] is None
    assert done["result"]["frames"] == 2 and done["result"]["baseline"]["snapshot_id"] == "b1"
    assert done["frame"]["hands"][0]["side"] == "her_right"
    assert [o["quality"]["reasons"] for o in done["observations"]] == [["wrong_hand_use_your_left_hand"]] * 2
    assert gone.status_code == 404 and denied.status_code == 401


@pytest.mark.anyio
async def test_debug_without_the_analysis_is_a_plain_503():
    async with Server("scripted") as server:
        def missing(task, mirrored, probe):
            raise DebugUnavailable("Agent 3's analysis is not installed here (numpy).")

        server.app.state.debug.build = missing
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            response = await http.post("/v1/debug/runs", json={"task": "smile"})
            wrong = await http.post("/v1/debug/runs", json={"task": "sentence"})
    assert response.status_code == 503 and response.json()["code"] == "analysis_unavailable"
    assert wrong.status_code == 400


def test_plain_makes_numbers_json_safe():
    assert plain({"a": (1.0, float("nan")), "b": [True, None, "x"]}) == {"a": [1.0, None], "b": [True, None, "x"]}
