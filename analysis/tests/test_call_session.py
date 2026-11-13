"""CallSession behaviour with a fake clock, fake adapter and a scripted task module. Written, not run.

Each test runs its own event loop with asyncio.run; time moves only through FakeClock.advance.
"""
from __future__ import annotations

import asyncio

from analysis.session.models import BaselineInfo, Quality, TaskResult
from analysis.tasks import stream_base
from analysis.tests.session_helpers import BASELINE, Rig, resource_event


def failed_result(rig: Rig) -> TaskResult:
    return TaskResult("smile", rig.module.context.attempt_id, "failed", Quality("unusable", ["analysis_error"]),
                      "uncertain", stream_base.baseline_info(BASELINE))


async def complete(rig: Rig, value: float) -> dict:
    await rig.start()
    await rig.tick(1.0)
    rig.module.finish(rig.module.usable_result(value))
    await rig.clock.settle()
    return await rig.task


# ---- interruption ---------------------------------------------------------------------------------

def test_camera_loss_mid_task_interrupts_once_with_no_reading():
    async def scenario():
        rig = Rig()
        aid = await rig.start()
        await rig.tick(1.0)
        assert rig.module.emit("usable") == "accepted"
        assert rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1)) == "applied"
        await rig.clock.settle()
        result = await rig.task
        assert result["outcome"] == "interrupted" and result["reading"] is None
        assert result["quality"] == {"status": "unusable", "reasons": ["camera_unavailable"]}
        assert result["interruption"]["code"] == "camera_unavailable"
        assert result["interruption"]["source_event_id"] == "e1"
        assert result["interruption"]["attempt_id"] == aid and result["interruption"]["recoverable"] is True
        # a repeated or newer event for the same failed resource does not interrupt again
        assert rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1)) == "duplicate"
        rig.session.handle_resource_event(resource_event("e2", "camera", "unavailable", 2))
        await rig.clock.settle()
        assert len(rig.kinds("attempt_stopping")) == 1 and len(rig.session.results) == 1
        assert [c for c in rig.adapter.calls if c[0] == "cancel_capture"] == [("cancel_capture", (aid, "task_interrupted"))]
        assert rig.module.cancelled is True
        # a late observation for the finished attempt is ignored and logged, and changes nothing
        assert rig.module.emit("usable") == "late"
        assert rig.kinds("observation_ignored")[-1]["reason"] == "late"
        assert rig.session.attempts[aid].state == "interrupted"

    asyncio.run(scenario())


def test_old_resource_event_cannot_overwrite_newer_availability():
    async def scenario():
        rig = Rig()
        assert rig.session.handle_resource_event(resource_event("e2", "camera", "available", 2)) == "applied"
        assert rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1)) == "stale"
        assert rig.session.resources["camera"].state == "available"

    asyncio.run(scenario())


def test_microphone_loss_does_not_stop_a_camera_task():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(0.5)
        rig.session.handle_resource_event(resource_event("m1", "microphone", "unavailable", 1))
        await rig.tick(0.5)
        assert not rig.task.done()
        rig.module.finish(rig.module.usable_result(0.01))
        await rig.clock.settle()
        assert (await rig.task)["outcome"] == "completed"

    asyncio.run(scenario())


def test_framing_unusable_for_two_continuous_seconds_interrupts():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(0.5)
        rig.module.emit("unusable", "framing_unusable")
        await rig.tick(2.4)
        result = await rig.task
        assert result["outcome"] == "interrupted"
        assert result["interruption"]["code"] == "framing_unusable"
        assert result["interruption"]["source_event_id"] == "obs_A1_0"

    asyncio.run(scenario())


def test_framing_unusable_for_one_and_a_half_seconds_then_good_does_not_interrupt():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(0.5)
        rig.module.emit("unusable", "framing_unusable")
        await rig.tick(1.5)
        rig.module.emit("usable", "none")
        await rig.tick(2.0)
        assert not rig.task.done()
        rig.module.finish(rig.module.usable_result(0.01))
        await rig.clock.settle()
        assert (await rig.task)["outcome"] == "completed"

    asyncio.run(scenario())


def test_no_first_sample_in_two_seconds_interrupts_as_no_samples():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(2.4, sample_every=None)
        result = await rig.task
        assert result["interruption"]["code"] == "no_samples"
        assert result["interruption"]["source_event_id"] is None  # a server timeout
        assert result["interruption"]["recoverable"] is True

    asyncio.run(scenario())


def test_no_acknowledgment_and_no_sample_in_five_seconds_interrupts():
    async def scenario():
        rig = Rig()
        rig.adapter.auto_ack_start = False
        await rig.start()
        await rig.tick(4.5, sample_every=None)
        assert not rig.task.done()
        await rig.tick(1.0, sample_every=None)
        assert (await rig.task)["interruption"]["code"] == "no_samples"

    asyncio.run(scenario())


def test_analyzer_silent_for_three_seconds_while_samples_arrive_is_analysis_stalled():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(3.5, sample_every=0.5)  # samples keep coming, the module says nothing
        result = await rig.task
        assert result["interruption"]["code"] == "analysis_stalled"

    asyncio.run(scenario())


def test_task_unperformable_signal_interrupts():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(0.5)
        rig.module.emit("usable", "task_unperformable")
        await rig.clock.advance(0.3)
        result = await rig.task
        assert result["interruption"]["code"] == "task_unperformable"

    asyncio.run(scenario())


def test_cancel_not_acknowledged_in_one_second_invalidates_the_attempt_locally():
    async def scenario():
        rig = Rig(auto_ack_cancel=False)
        await rig.start()
        await rig.tick(0.5)
        rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1))
        await rig.clock.advance(0.5)
        assert not rig.task.done()  # still waiting for the acknowledgment
        await rig.clock.advance(0.7)
        assert (await rig.task)["outcome"] == "interrupted"
        assert rig.kinds("cancel_ack_timeout")

    asyncio.run(scenario())


def test_routine_observations_do_not_resolve_the_task_and_send_neutral_progress_only():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(0.5)
        rig.module.emit("usable")
        rig.module.emit("usable")
        await rig.clock.settle()
        assert not rig.task.done()
        assert rig.adapter.progress and all(set(p) == {"event_id", "call_id", "attempt_id", "type", "elapsed_ms",
                                                       "phase", "input_quality", "hint"} for p in rig.adapter.progress)
        rig.module.finish(rig.module.usable_result(0.01))
        await rig.clock.settle()
        await rig.task

    asyncio.run(scenario())


# ---- budget and resources -------------------------------------------------------------------------

def test_fourth_attempt_is_refused_without_touching_the_client():
    async def scenario():
        rig = Rig()
        for _ in range(3):
            await rig.start()
            rig.module.finish(failed_result(rig))
            await rig.clock.settle()
            assert (await rig.task)["outcome"] == "failed"
        starts_before = len([c for c in rig.adapter.calls if c[0] == "start_capture"])
        refused = await rig.session.run_task("smile")
        assert refused["outcome"] == "failed" and refused["quality"]["reasons"] == ["attempt_budget_exhausted"]
        assert refused["attempt_id"].startswith("refused_")
        assert len(rig.session.attempts) == 3
        assert len([c for c in rig.adapter.calls if c[0] == "start_capture"]) == starts_before
        assert rig.kinds("task_refused")[-1]["reason"] == "attempt_budget_exhausted"

    asyncio.run(scenario())


def test_a_third_attempt_after_two_usable_readings_is_refused():
    async def scenario():
        rig = Rig()
        await complete(rig, 0.01)
        await complete(rig, 0.02)
        refused = await rig.session.run_task("smile")
        assert refused["quality"]["reasons"] == ["usable_readings_complete"]

    asyncio.run(scenario())


def test_unavailable_or_unknown_resource_blocks_a_new_attempt():
    async def scenario():
        for state in ("unavailable", "unknown"):
            rig = Rig(resources={"camera": state, "microphone": "available"})
            result = await rig.session.run_task("smile")
            assert result["quality"]["reasons"] == ["camera_unavailable"] and not rig.session.attempts
            assert rig.adapter.client_calls() == []

    asyncio.run(scenario())


def test_recovery_creates_a_new_attempt_with_the_repeat_line():
    async def scenario():
        rig = Rig()
        a1 = await rig.start()
        await rig.tick(0.5)
        rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1))
        await rig.clock.settle()
        assert (await rig.task)["outcome"] == "interrupted"
        recovery = asyncio.ensure_future(rig.session.recover_resource("camera"))
        await rig.clock.advance(3.0)
        rig.session.handle_resource_event(resource_event("e2", "camera", "available", 2, None))
        assert await recovery == "restored"
        assert "camera_unavailable" in rig.adapter.said
        a2 = await rig.start()
        assert a2 != a1 and rig.session.attempts[a1].state == "interrupted"
        assert rig.adapter.said[-1] == "repeat_smile" and "task_smile" in rig.adapter.said
        rig.module.finish(rig.module.usable_result(0.01))
        await rig.clock.settle()
        assert (await rig.task)["attempt_id"] == a2

    asyncio.run(scenario())


def test_recovery_wait_is_bounded_and_only_once_per_interruption():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(0.5)
        rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1))
        await rig.clock.settle()
        await rig.task
        recovery = asyncio.ensure_future(rig.session.recover_resource("camera"))
        await rig.clock.advance(10.5)
        assert await recovery == "unavailable"
        assert await rig.session.recover_resource("camera") == "unavailable"
        assert rig.adapter.said.count("camera_unavailable") == 1

    asyncio.run(scenario())


# ---- races, hang-up --------------------------------------------------------------------------------

def test_race_loss_before_commit_wins_over_a_usable_result():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(1.0)
        rig.module.finish(rig.module.usable_result(0.01))  # the result exists but is not committed yet
        rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1))
        await rig.clock.settle()
        result = await rig.task
        assert result["outcome"] == "interrupted" and result["reading"] is None
        assert [r.outcome for r in rig.session.records] == ["interrupted"]
        assert rig.kinds("late_result_ignored") or rig.kinds("race")

    asyncio.run(scenario())


def test_race_loss_after_commit_does_not_change_the_result():
    async def scenario():
        rig = Rig()
        result = await complete(rig, 0.01)
        assert result["outcome"] == "completed"
        rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1))
        await rig.clock.settle()
        assert rig.session.results == [result]
        assert rig.session.attempts[result["attempt_id"]].state == "completed"
        assert rig.kinds("race")[-1]["winner"] == "result"
        # but it affects later work
        assert (await rig.session.run_task("smile"))["quality"]["reasons"] == ["camera_unavailable"]

    asyncio.run(scenario())


def test_hang_up_cancels_and_makes_no_further_client_calls():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(1.0)
        before = list(rig.adapter.client_calls())
        rig.session.hang_up()
        await rig.clock.settle()
        result = await rig.task
        assert result["outcome"] == "cancelled" and result["quality"]["reasons"] == ["call_ended"]
        assert rig.adapter.client_calls() == before
        again = await rig.session.run_task("smile")
        assert again["outcome"] == "cancelled" and rig.adapter.client_calls() == before
        final = await rig.session.finish()
        assert "bye" not in rig.adapter.said and rig.adapter.client_calls() == before
        assert final["status"] == "incomplete"

    asyncio.run(scenario())


def test_hang_up_during_instruction_playback():
    async def scenario():
        rig = Rig()
        rig.adapter.say_gate = asyncio.Event()  # the instruction never finishes playing
        await rig.start()
        rig.session.hang_up()
        await rig.clock.settle()
        assert (await rig.task)["outcome"] == "cancelled"
        assert [c[0] for c in rig.adapter.client_calls()] == ["say"]

    asyncio.run(scenario())


# ---- confirmation and publishing -------------------------------------------------------------------

def test_two_outside_readings_publish_once_then_the_final_message_reuses_the_id():
    async def scenario():
        rig = Rig()
        first = await complete(rig, 0.11)
        assert first["reading"]["outside"] is True and rig.adapter.published == []
        second = await complete(rig, 0.09)
        assert second["reading"]["outside"] is True
        assert len(rig.adapter.published) == 1  # published before run_task returned
        mid = rig.adapter.published[0]
        assert mid["phase"] == "mid_call" and mid["summary"]["status"] == "change_noticed"
        assert mid["text"] == "Ona's smile looks different from her usual. Call her now. If she seems unwell, call 112."
        final = await rig.session.finish()
        assert final["status"] == "change_noticed"
        assert [p["phase"] for p in rig.adapter.published] == ["mid_call", "final"]
        assert len({p["message_id"] for p in rig.adapter.published}) == 1
        assert rig.session.message_id == "msg_demo"
        assert len(rig.kinds("change_confirmed")) == 1

    asyncio.run(scenario())


def test_outside_then_within_is_not_confirmed_and_never_published_mid_call():
    async def scenario():
        rig = Rig()
        await complete(rig, 0.11)
        await complete(rig, 0.01)
        assert rig.adapter.published == []
        final = await rig.session.finish()
        assert final["status"] == "as_usual"
        face = final["signs"][0]
        assert face["confirmed"] is False and len(face["attempts"]) == 2
        assert [p["phase"] for p in rig.adapter.published] == ["final"]

    asyncio.run(scenario())


def test_interrupted_attempt_never_counts_and_the_session_is_incomplete():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(0.5)
        rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1))
        await rig.clock.settle()
        await rig.task
        final = await rig.session.finish()
        assert final["status"] == "incomplete" and final["signs"] == []
        assert rig.session.baseline_update() == {}

    asyncio.run(scenario())


def test_learning_when_the_baseline_is_not_ready():
    async def scenario():
        rig = Rig(baselines={"smile": {"snapshot_id": "baseline_l", "ready": False, "sessions": 2, "needed": 5}})
        await rig.start()
        await rig.tick(0.5)
        rig.module.finish(rig.module.usable_result(0.01, {"snapshot_id": "baseline_l", "ready": False,
                                                          "sessions": 2, "needed": 5}))
        await rig.clock.settle()
        result = await rig.task
        assert result["reading"]["outside"] is None and result["reading"]["usual_low"] is None
        final = await rig.session.finish()
        assert final["status"] == "learning" and final["baseline_sessions"] == 2

    asyncio.run(scenario())


def test_acted_session_does_not_enter_the_baseline_but_live_does():
    async def scenario():
        for label, expected in (("acted", {}), ("live", {"fake_metric_v1": 0.01})):
            rig = Rig(label=label)
            await complete(rig, 0.01)
            assert rig.session.baseline_update() == expected

    asyncio.run(scenario())


# ---- v6: cues, event version, per-hand baseline ------------------------------------------------------------

class CueModule:
    """A scripted module whose definition carries cues."""

    def __init__(self, inner, cues):
        self.inner, self.cues, self.name = inner, cues, inner.name

    def definition(self):
        d = self.inner.definition()
        d["cues"] = self.cues
        return d

    async def run(self, context, samples, emit_observation):
        return await self.inner.run(context, samples, emit_observation)


def rig_with_cues(cues):
    rig = Rig()
    modules = dict(rig.session.modules)  # a copy: rig.module must keep pointing at the scripted module
    modules["smile"] = CueModule(rig.module, cues)
    rig.session.modules = modules
    return rig


def test_a_cue_is_played_at_its_offset_after_capture_started_without_blocking_samples():
    async def scenario():
        rig = rig_with_cues([{"at_ms": 2000, "line_id": "swap_hands"}])
        rig.adapter.say_gate = None
        aid = await rig.start()
        assert rig.adapter.said == ["task_smile"]
        await rig.tick(1.5, sample_every=0.5)
        assert "swap_hands" not in rig.adapter.said
        await rig.tick(1.0, sample_every=0.5)
        assert rig.adapter.said == ["task_smile", "swap_hands"]
        assert [e["line"] for e in rig.kinds("cue")] == ["swap_hands"]
        assert rig.session.attempts[aid].samples_received >= 4  # intake went on while the cue played
        rig.module.finish(rig.module.usable_result(0.01))
        await rig.clock.settle()
        assert (await rig.task)["outcome"] == "completed"
        assert rig.adapter.said.count("swap_hands") == 1

    asyncio.run(scenario())


def test_a_slow_cue_playback_does_not_block_samples_or_watchdogs():
    async def scenario():
        rig = rig_with_cues([{"at_ms": 500, "line_id": "swap_hands"}])
        aid = await rig.start()
        rig.adapter.say_gate = asyncio.Event()  # the cue line "plays" for a long time
        await rig.tick(1.0, sample_every=0.25)
        before = rig.session.attempts[aid].samples_received
        await rig.tick(1.0, sample_every=0.25)
        assert rig.session.attempts[aid].samples_received > before and rig.session.attempts[aid].is_active
        rig.adapter.say_gate.set()
        rig.module.finish(rig.module.usable_result(0.01))
        await rig.clock.settle()
        assert (await rig.task)["outcome"] == "completed"

    asyncio.run(scenario())


def test_a_failed_cue_playback_is_logged_and_never_fails_the_attempt():
    async def scenario():
        rig = rig_with_cues([{"at_ms": 500, "line_id": "swap_hands"}])
        await rig.start()
        rig.adapter.fail_say = True
        await rig.tick(1.5, sample_every=0.5)
        assert rig.kinds("cue_failed") and rig.kinds("cue_failed")[0]["error"] == "RuntimeError"
        rig.module.finish(rig.module.usable_result(0.01))
        await rig.clock.settle()
        assert (await rig.task)["outcome"] == "completed"

    asyncio.run(scenario())


def test_a_cue_is_not_played_when_the_attempt_ends_first():
    async def scenario():
        rig = rig_with_cues([{"at_ms": 3000, "line_id": "swap_hands"}])
        await rig.start()
        await rig.tick(1.0, sample_every=0.5)
        rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1))
        await rig.clock.settle()
        assert (await rig.task)["outcome"] == "interrupted"
        await rig.clock.advance(5.0)
        assert "swap_hands" not in rig.adapter.said

    asyncio.run(scenario())


def test_tasks_without_cues_play_nothing_extra():
    async def scenario():
        rig = Rig()
        await complete(rig, 0.01)
        assert rig.adapter.said == ["task_smile"] and rig.kinds("cue") == []

    asyncio.run(scenario())


def test_the_event_version_counts_interruptions():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(0.5)
        before = rig.session.event_version
        rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1))
        assert rig.session.event_version >= before + 1
        await rig.clock.settle()
        await rig.task
        assert rig.session.event_version >= before + 2  # the resource change and the interruption it caused

    asyncio.run(scenario())


def test_arms_baseline_update_stores_one_value_per_hand():
    from analysis.tests.agent_helpers import CallRig

    async def scenario():
        rig = CallRig(required_tasks=("arms",), sinks=({"left": 0.04},))
        await rig.greet()
        await rig.run_tool("run_task", {"task": "arms"})
        update = rig.session.baseline_update()
        assert set(update) == {"arms.hand_left.hand_sink", "arms.hand_right.hand_sink"}  # not "hand_sink_v1"
        assert abs(update["arms.hand_left.hand_sink"] - 0.04) < 1e-9 and abs(update["arms.hand_right.hand_sink"]) < 1e-9

        acted = CallRig(label="acted")
        await acted.greet()
        await acted.run_tool("run_task", {"task": "arms"})
        assert acted.session.baseline_update() == {}  # same eligibility rule as the smile

    asyncio.run(scenario())


def test_invalid_module_result_becomes_a_failed_attempt():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(0.5)
        bad = TaskResult("smile", "A_other", "failed", Quality("unusable", ["x"]), "uncertain",
                         BaselineInfo("baseline_t", 12, 5, True))
        rig.module.finish(bad)  # belongs to another attempt
        await rig.clock.settle()
        result = await rig.task
        assert result["outcome"] == "failed" and result["quality"]["reasons"] == ["invalid_result"]

    asyncio.run(scenario())
