"""Which earlier video the family sees next to today's (checkin_backend/comparison.py).

The selection is a read-only query. These tests build a migrated database, drop its write-side triggers and foreign
keys (backend/storage/tests and analysis/tests cover those rules) and insert only the rows each case needs.
No videos are encoded: the query reads `attempt_clips` metadata only.
"""

import json

import httpx
import pytest

from checkin_backend.comparison import select_comparison
from checkin_backend.store import now_ms
from storage import apply_migrations, connect_database
from tests.test_call_service import KEY, Server

DAY = 86_400_000
NOW = now_ms()  # clip_metadata reports expiry against the real clock
TODAY = NOW - 60_000


class World:
    def __init__(self, path):
        self.db = connect_database(path)
        apply_migrations(self.db)
        self.db.execute("PRAGMA foreign_keys = OFF")
        for (name,) in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
            self.db.execute(f"DROP TRIGGER {name}")
        self.db.execute("INSERT INTO task_definitions VALUES ('def_smile', 'smile', 'v1', 'm', 'u', 'l', 'r', "
                        "'camera', 1000, '{}', 0)")
        self.db.execute("INSERT INTO task_definitions VALUES ('def_arms', 'arms', 'v1', 'm', 'u', 'l', 'r', "
                        "'camera', 1000, '{}', 0)")
        self.clips = 0

    def call(self, call_id, at, *, label="normal", status=None, signs=None, person="p_dad"):
        self.db.execute("INSERT INTO calls (call_id, person_id, label, state, end_reason, created_at_ms, "
                        "ring_deadline_at_ms, answered_at_ms, call_deadline_at_ms, ended_at_ms) "
                        "VALUES (?, ?, ?, 'ended', 'completed', ?, ?, ?, ?, ?)",
                        (call_id, person, label, at, at + 30_000, at + 5000, at + 185_000, at + 120_000))
        if status is not None:
            self.summary(call_id, status, at, signs=signs)

    def summary(self, call_id, status, at, *, phase="final", signs=None):
        revision = self.db.execute("SELECT coalesce(max(revision), 0) + 1 FROM session_summaries WHERE call_id = ?",
                                   (call_id,)).fetchone()[0]
        self.db.execute("INSERT INTO session_summaries VALUES (?, ?, ?, ?, ?, ?)",
                        (call_id, revision, status, phase, json.dumps({"signs": signs or []}), at + 100_000))

    def clip(self, call_id, task, at, *, attempt=None, label="normal", capture="finished", state="ready",
             expires=NOW + 7 * DAY):
        self.clips += 1
        attempt = attempt or f"a{self.clips}"
        clip_id = f"clip_{self.clips}"
        self.db.execute(
            "INSERT INTO attempt_clips (clip_id, call_id, attempt_id, command_id, task, session_label, capture_status, "
            "state, file_name, duration_ms, frame_count, byte_count, recorded_at_ms, created_at_ms, encoded_at_ms, "
            "expires_at_ms, error_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 6000, 30, ?, ?, ?, ?, ?, ?)",
            (clip_id, call_id, attempt, f"{call_id}.{attempt}", task, label, capture, state, f"{clip_id}.mp4",
             1000 if state == "ready" else None, at, at, at if state == "ready" else None, expires,
             "encoder_failed" if state == "failed" else None))
        return clip_id

    def learned(self, snapshot, call_id, attempt, task="smile", *, frozen_for=None, person="p_dad"):
        definition = f"def_{task}"
        self.db.execute("INSERT OR IGNORE INTO baseline_snapshots VALUES (?, ?, ?, 1, 1, 0, NULL, NULL, 0)",
                        (snapshot, person, definition))
        self.db.execute("INSERT INTO baseline_members VALUES (?, ?, ?, ?)",
                        (snapshot, f"{call_id}.{attempt}", call_id, definition))
        if frozen_for is not None:
            self.db.execute("INSERT OR IGNORE INTO session_baselines VALUES (?, ?, ?, ?)",
                            (frozen_for, task, definition, snapshot))

    def select(self, call_id="call_today"):
        return select_comparison(self.db, call_id, NOW)


@pytest.fixture()
def world(tmp_path):
    w = World(tmp_path / "comparison.sqlite3")
    yield w
    w.db.close()


FACE = [{"sign": "face", "confirmed": True, "side": "left"}]
ARM = [{"sign": "arm", "confirmed": True, "side": "right"}]


def alert(world, signs=FACE, label="normal"):
    world.call("call_today", TODAY, label=label, status="change_noticed", signs=signs)
    first = world.clip("call_today", "smile", TODAY + 1000, label=label)
    second = world.clip("call_today", "smile", TODAY + 9000, label=label)
    world.clip("call_today", "arms", TODAY + 20_000, label=label)
    return first, second


def test_learned_video_from_the_frozen_baseline_comes_first(world):
    alert(world)
    world.call("call_a", NOW - 3 * DAY, status="learning")
    world.call("call_b", NOW - 2 * DAY, status="as_usual")
    world.call("call_c", NOW - DAY, status="as_usual")
    frozen = world.clip("call_a", "smile", NOW - 3 * DAY, attempt="a1")
    world.clip("call_b", "smile", NOW - 2 * DAY, attempt="b1")
    world.clip("call_c", "smile", NOW - DAY, attempt="c1")  # newest, but never learned
    world.learned("snap_used", "call_a", "a1", frozen_for="call_today")
    world.learned("snap_other", "call_b", "b1")  # learned, but not in the snapshot today's call used

    result = world.select()

    assert result["task"] == "smile" and result["task_source"] == "alert_sign"
    assert [clip["task"] for clip in result["today"]] == ["smile", "smile"]
    assert result["earlier"]["clip_id"] == frozen
    assert result["earlier"]["video_url"] == f"/v1/clips/{frozen}/video"
    assert result["earlier_source"] == "baseline" and result["earlier_missing"] is None


def test_other_learned_attempts_before_recent_calls(world):
    alert(world)
    world.call("call_b", NOW - 2 * DAY, status="as_usual")
    world.call("call_c", NOW - DAY, status="as_usual")
    learned = world.clip("call_b", "smile", NOW - 2 * DAY, attempt="b1")
    world.clip("call_c", "smile", NOW - DAY, attempt="c1")
    world.learned("snap_other", "call_b", "b1")

    result = world.select()
    assert result["earlier"]["clip_id"] == learned and result["earlier_source"] == "baseline"


def test_without_a_learned_video_the_last_calm_call(world):
    alert(world)
    world.call("call_old", NOW - 5 * DAY, status="as_usual")
    world.call("call_new", NOW - DAY, status="as_usual")
    world.call("call_acted", NOW - DAY + 1000, label="acted", status="as_usual")
    world.call("call_changed", NOW - DAY + 2000, status="as_usual")
    world.summary("call_changed", "change_noticed", NOW - DAY + 2000, phase="mid_call")
    world.call("call_later", NOW + 1000, status="as_usual")
    world.call("call_other_person", NOW - DAY + 3000, status="as_usual", person="p_mum")
    world.clip("call_old", "smile", NOW - 5 * DAY)
    recent = world.clip("call_new", "smile", NOW - DAY)
    world.clip("call_new", "smile", NOW - DAY + 500, capture="interrupted")
    world.clip("call_new", "arms", NOW - DAY + 600)
    world.clip("call_acted", "smile", NOW - DAY + 1000, label="acted")
    world.clip("call_changed", "smile", NOW - DAY + 2000)
    world.clip("call_later", "smile", NOW + 1000)
    world.clip("call_other_person", "smile", NOW - DAY + 3000)

    result = world.select()
    assert result["earlier"]["clip_id"] == recent
    assert result["earlier_source"] == "recent_as_usual"


def test_task_follows_the_confirmed_sign_then_the_most_tries(world):
    alert(world, signs=ARM)
    assert world.select()["task"] == "arms"
    assert [clip["task"] for clip in world.select()["today"]] == ["arms"]

    world.call("call_unsigned", TODAY + 1, status="change_noticed")
    world.clip("call_unsigned", "arms", TODAY + 2)
    world.clip("call_unsigned", "smile", TODAY + 3)
    tie = world.select("call_unsigned")
    assert tie["task"] == "smile" and tie["task_source"] == "most_tries"

    world.call("call_nothing", TODAY + 4)
    empty = world.select("call_nothing")
    assert empty["task"] is None and empty["today"] == [] and empty["earlier_missing"] == "no_matching_source"


def test_missing_reasons(world):
    alert(world)
    assert world.select()["earlier_missing"] == "no_matching_source"

    world.call("call_calm", NOW - DAY, status="as_usual")
    assert world.select()["earlier_missing"] == "not_retained"

    world.clip("call_calm", "smile", NOW - 8 * DAY, expires=NOW - 1)
    result = world.select()
    assert result["earlier"] is None and result["earlier_missing"] == "expired"


def test_failed_and_queued_videos_are_not_shown_as_earlier(world):
    alert(world)
    world.call("call_calm", NOW - DAY, status="as_usual")
    world.clip("call_calm", "smile", NOW - DAY, state="failed")
    world.clip("call_calm", "smile", NOW - DAY + 1, state="queued")
    assert world.select()["earlier"] is None


def test_unknown_call(world):
    assert world.select("call_missing") is None


@pytest.mark.anyio
@pytest.mark.parametrize("stored", [True, False])
async def test_route(tmp_path, stored):
    options = {"database_path": str(tmp_path / "checkin.sqlite3")} if stored else {"database_path": ""}
    async with Server("scripted", **options) as server:
        async def idle(link):
            import asyncio
            await asyncio.Event().wait()
        server.app.state.desk.driver = idle
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            assert (await http.get("/v1/calls/call_missing/comparison")).status_code == 404
            await http.post("/v1/devices", json={"device_id": "parent", "role": "parent", "person_id": "p_demo"})
            call_id = (await http.post("/v1/calls", json={"person_id": "p_demo"})).json()["call_id"]
            response = await http.get(f"/v1/calls/{call_id}/comparison")
            assert response.status_code == 200
            body = response.json()
            assert body["call_id"] == call_id and body["task"] is None and body["today"] == []
            assert body["earlier"] is None
            assert body["earlier_missing"] == ("no_matching_source" if stored else "not_retained")
            assert response.headers["cache-control"] == "no-store"
            await http.post(f"/v1/calls/{call_id}/hangup")
