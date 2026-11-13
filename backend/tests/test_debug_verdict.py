"""Camera debug: the screen's tries judged with Agent 3's call rules, and the family message they would lead to."""
import base64

import httpx
import pytest

from tests.client_sim import tiny_jpeg
from tests.test_call_service import KEY, Server

pytest.importorskip("analysis.session.rules", reason="Agent 3's analysis package is not on the path")

FRAME = {"sequence": 0, "captured_ms": 0, "data_base64": base64.b64encode(tiny_jpeg()).decode()}


class ScriptedArms:
    """Stands in for StreamArms: each run takes the next (left, right) hand_sink pair from `plan`; None = no hand."""

    def __init__(self, plan: list) -> None:
        self.plan = plan
        self.values: dict[str, dict[str, float]] = {}

    def definition(self):
        return {"capture": {"duration_ms": 15000, "sample_period_ms": 200, "format": "jpeg"}, "cues": []}

    def attempt_values(self, attempt_id):
        return dict(self.values.get(attempt_id, {}))

    async def run(self, context, samples, emit):
        async for _ in samples:
            pass
        pair = self.plan.pop(0)
        baseline = context.baseline or {}
        info = {"snapshot_id": baseline.get("snapshot_id") or "baseline_none", "sessions": 0, "needed": 3,
                "ready": bool(baseline.get("ready"))}
        if pair is None:
            return {"task": "arms", "attempt_id": context.attempt_id, "outcome": "completed",
                    "quality": {"status": "unusable", "reasons": ["left_hand_not_seen"]},
                    "performance": "not_observed", "baseline": info, "reading": None}
        left, right = pair
        self.values[context.attempt_id] = {"left": left, "right": right}
        reading = {"metric_id": "hand_sink_v1", "unit": "eye_distance", "value": left, "usual_low": None,
                   "usual_high": None, "outside": None, "side": None}
        if info["ready"]:
            high = baseline["hand_left"]["usual_high"]
            reading.update(usual_low=baseline["hand_left"]["usual_low"], usual_high=high, outside=left > high,
                           side="left" if left > high else None)
        return {"task": "arms", "attempt_id": context.attempt_id, "outcome": "completed",
                "quality": {"status": "usable", "reasons": []}, "performance": "observed", "baseline": info,
                "reading": reading}


async def tries(http, purposes: list[str]) -> dict:
    snapshot = {}
    for purpose in purposes:
        run = (await http.post("/v1/debug/runs", json={"task": "arms", "purpose": purpose})).json()
        await http.post(f"/v1/debug/runs/{run['run_id']}/samples", json=FRAME)
        snapshot = (await http.post(f"/v1/debug/runs/{run['run_id']}/finish")).json()
        assert snapshot["error"] is None, snapshot["error"]
    return snapshot["verdict"]


def with_plan(server, plan: list) -> None:
    module = ScriptedArms(plan)
    learning = {"snapshot_id": "baseline_p_demo_arms_0_0", "ready": False, "hand_left": {}, "hand_right": {}}
    server.app.state.debug.build = lambda task, mirrored, probe: (module, dict(learning))


@pytest.mark.anyio
async def test_a_hand_never_seen_twice_ends_the_check_without_an_arm_alert():
    async with Server("scripted") as server:
        with_plan(server, [None, None])
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            empty = (await http.get("/v1/debug/series/arms")).json()
            first = await tries(http, ["check"])
            second = await tries(http, ["check"])
    assert empty["state"] == "no_tries"
    assert first["state"] == "try_again" and "left hand not seen" in first["lines"][0]
    assert second["state"] == "settled" and second["status"] == "incomplete"
    assert "could not be finished" in second["family_message"] and second["family_message"].endswith("call 112.")
    assert [t["measured"] for t in second["tries"]] == [False, False]


@pytest.mark.anyio
async def test_without_a_usual_a_measured_try_cannot_alert():
    async with Server("scripted") as server:
        with_plan(server, [(0.1, 0.1)])
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            verdict = await tries(http, ["check"])
    assert verdict["status"] == "learning" and verdict["usual"]["source"] == "none"


@pytest.mark.anyio
async def test_practice_usual_then_two_sinking_tries_send_the_arm_alert():
    async with Server("scripted") as server:
        with_plan(server, [(0.02, 0.03), (0.03, 0.02), (0.025, 0.025), (0.5, 0.02), (0.6, 0.03), (0.02, 0.02)])
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            taught = await tries(http, ["teach", "teach", "teach"])
            once = await tries(http, ["check"])
            twice = await tries(http, ["check"])
            fresh = await tries(http, ["check"])
            reset = (await http.post("/v1/debug/series/arms/reset")).json()
    assert taught["practice_tries"] == 3 and taught["usual"]["source"] == "practice"
    assert once["state"] == "try_again" and once["tries"][0]["outside"] is True
    assert twice["status"] == "change_noticed" and "arm looks different" in twice["family_message"]
    assert fresh["status"] == "as_usual" and len(fresh["tries"]) == 1  # the alert ended that pretend call
    assert reset["practice_tries"] == 0 and reset["state"] == "no_tries"
