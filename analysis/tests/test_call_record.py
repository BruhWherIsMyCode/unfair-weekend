"""The live log lines and the call record shared by the PC server and the backend entry."""
from __future__ import annotations

import asyncio
import json

from analysis.session import call_record
from analysis.session.call_record import LiveLog, format_log_line
from analysis.tests.session_helpers import Rig


def lines_of(rig: Rig):
    return [l for l in (format_log_line(e, rig.session) for e in rig.session.log) if l is not None]


def test_an_interruption_line_names_the_cause_the_reasons_and_the_time():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(0.5)
        rig.module.emit("unusable", "framing_unusable", elapsed_ms=1000, reasons=["no_hand_on_your_left_side"])
        await rig.tick(2.4)
        await rig.task
        text = "\n".join(lines_of(rig))
        assert "attempt stopping" in text and "interrupted" in text
        assert "code=framing_unusable" in text and "framing unusable for" in text
        assert "reasons=no_hand_on_your_left_side" in text and "signal=framing_unusable" in text
        assert "at 1.0 s of the capture" in text
        assert "INTERRUPTED: framing_unusable" in text and "reasons: framing_unusable" in text

    asyncio.run(scenario())


def test_an_arms_stop_names_the_hand_phase():
    async def scenario():
        rig = Rig(tasks=("arms",), required_tasks=("arms",))
        await rig.start("arms")
        await rig.tick(0.5)
        rig.modules["arms"].emit("unusable", "framing_unusable", elapsed_ms=1000, reasons=["hand_not_found"])
        await rig.tick(2.4)
        await rig.task
        text = "\n".join(lines_of(rig))
        assert "task=arms" in text and "phase hold_left (left hand)" in text

    asyncio.run(scenario())


def test_arms_phase_names_follow_the_phase_table():
    assert call_record.arms_phase_name(500) == "settle"
    assert call_record.arms_phase_name(3000) == "hold_left (left hand)"
    assert call_record.arms_phase_name(7000) == "swap"
    assert call_record.arms_phase_name(9000) == "hold_right (right hand)"
    assert call_record.arms_phase_name(15500) == "past the window"


def test_a_stop_before_any_observation_says_so():
    async def scenario():
        rig = Rig()
        await rig.start()
        rig.session.handle_resource_event(
            {"event_id": "e1", "type": "resource.changed", "command_id": None, "resource": "camera",
             "state": "unavailable", "sequence": 1, "reason": "track_ended"})
        await rig.clock.settle()
        await rig.task
        text = "\n".join(lines_of(rig))
        assert "code=camera_unavailable" in text and "no observation yet" in text

    asyncio.run(scenario())


def test_an_unusable_result_shows_its_reasons_and_a_usable_one_does_not():
    async def scenario():
        rig = Rig()
        await rig.start()
        await rig.tick(1.0)
        rig.module.finish(rig.module.usable_result(0.01))
        await rig.clock.settle()
        await rig.task
        line = [l for l in lines_of(rig) if l.startswith("task smile")][0]
        assert "reasons" not in line and "INTERRUPTED" not in line

    asyncio.run(scenario())


def test_the_live_log_survives_a_failing_printer_and_keeps_every_entry():
    seen = []
    log = LiveLog(lambda e: (_ for _ in ()).throw(RuntimeError("printer broke")) if e["kind"] == "x" else seen.append(e))
    log.append({"kind": "x"})
    log.append({"kind": "y"})
    assert [e["kind"] for e in log] == ["x", "y"] and [e["kind"] for e in seen] == ["y"]


def test_the_record_is_written_and_an_unwritable_folder_is_reported(tmp_path):
    record = call_record.build_record("call_1", "p", "live", "fixed", True, {"status": "as_usual", "duration_s": 3},
                                      "text", [{"kind": "say"}], 0.0)
    assert record["timings"]["session_duration_s"] == 3 and record["session_log"] == [{"kind": "say"}]
    assert call_record.write_record(tmp_path / "runs", "call_1", record) is True
    assert json.loads((tmp_path / "runs" / "call_1.json").read_text("utf-8"))["call_id"] == "call_1"
    blocker = tmp_path / "f"
    blocker.write_text("x", "utf-8")
    messages = []
    assert call_record.write_record(blocker / "runs", "call_1", record, messages.append) is False
    assert messages and "could not write" in messages[0]
