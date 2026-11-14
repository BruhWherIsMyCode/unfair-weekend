"""Real MP4 encoding of drawn colour frames, HTTP retrieval and a restarted application. No people/providers."""

import asyncio
from contextlib import closing
import json
import os
import shutil
import subprocess
import threading

import httpx
import pytest

from checkin_backend import attempt_clips as videos
from checkin_backend.config import Settings
from checkin_backend.app import create_app
from checkin_backend.store import now_ms
from fastapi.testclient import TestClient
from storage import MIGRATIONS, apply_migrations, connect_database
from tests.test_call_service import KEY, Server, run_call
from tests.client_sim import SimClient

CAPTURE = {"format": "jpeg", "duration_ms": 1000, "prepare_ms": 0, "sample_period_ms": 200}


@pytest.fixture(scope="module")
def colour_jpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real MP4 tests need ffmpeg/libx264 and ffprobe")
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x48",
        "-frames:v", "1", "-threads", "1", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
    ], capture_output=True, check=True, timeout=5)
    return result.stdout


async def idle_driver(link):
    await asyncio.Event().wait()


async def answered_call(server, http):
    server.app.state.desk.driver = idle_driver
    await http.post("/v1/devices", json={"device_id": "parent", "role": "parent", "person_id": "p_demo"})
    response = await http.post("/v1/calls", json={"person_id": "p_demo"})
    assert response.status_code == 201
    call_id = response.json()["call_id"]
    assert (await http.post(f"/v1/calls/{call_id}/answer")).status_code == 200
    return call_id, server.app.state.desk.links[call_id]


async def capture(link, attempt, frame, *, task="smile", finish=True):
    await link.start_capture(attempt, task, CAPTURE)
    for sequence, elapsed in enumerate((0, 200, 800)):
        link.on_sample(attempt, sequence, elapsed, "jpeg", frame)
    if finish:
        link.on_finish(attempt, 2, 1000)


def test_playback_holds_source_frames_across_timestamp_gaps():
    assert videos.playback_frames(((800, b"B"), (0, b"A")), 1200) == b"AAAABB"


def test_archive_is_explicit_and_needs_database(monkeypatch):
    monkeypatch.setenv("DEMO_KEY", KEY)
    monkeypatch.delenv("CLIP_STORAGE_DIR", raising=False)
    monkeypatch.delenv("CLIP_RETENTION_DAYS", raising=False)
    assert Settings.from_environment().clip_storage_dir is None
    with pytest.raises(ValueError, match="requires DATABASE_PATH"):
        Settings(demo_key=KEY, clip_storage_dir="/tmp/test-clips")
    for days in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="CLIP_RETENTION_DAYS"):
            Settings(demo_key=KEY, clip_retention_days=days)


def test_upgrade_preserves_existing_calls_database(tmp_path):
    old = tmp_path / "old_migrations"
    old.mkdir()
    shutil.copy(MIGRATIONS / "0001_initial.sql", old)
    with closing(connect_database(tmp_path / "checkin.sqlite3")) as db:
        assert apply_migrations(db, old) == ["0001_initial.sql"]
        db.execute("INSERT INTO families VALUES ('old_family', 0)")
        assert apply_migrations(db) == ["0002_attempt_clips.sql"]
        assert db.execute("SELECT family_id FROM families").fetchone()[0] == "old_family"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.anyio
@pytest.mark.parametrize("label", ["live", "normal", "acted"])
async def test_videos_are_separate_playable_authenticated_and_survive_restart(tmp_path, colour_jpeg, monkeypatch, label):
    monkeypatch.setenv("DEMO_SESSION_LABEL", label)
    database = tmp_path / "checkin.sqlite3"
    root = tmp_path / "clips"
    options = {"database_path": str(database), "clip_storage_dir": str(root)}
    async with Server("scripted", **options) as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            call_id, link = await answered_call(server, http)
            await capture(link, "smile_1", colour_jpeg)
            link.on_finish("smile_1", 2, 1000)  # A retried finish must not create another file.
            await capture(link, "smile_2", colour_jpeg)
            await capture(link, "arm_left", colour_jpeg, task="arm_left", finish=False)
            await link.cancel_capture("arm_left", "task_interrupted")
            link.release_frames()
            await server.app.state.desk.attempt_clips.flush()
            response = await http.get(f"/v1/calls/{call_id}/clips")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert (await http.get(f"/v1/calls/{call_id}/clips", headers={"X-Demo-Key": "wrong"})).status_code == 401
            clips = response.json()["clips"]
            assert [(c["attempt_id"], c["capture_status"]) for c in clips] == [
                ("smile_1", "finished"), ("smile_2", "finished"), ("arm_left", "interrupted")]
            assert all(c["state"] == "ready" and c["session_label"] == label for c in clips)
            assert all(c["frame_count"] == 3 and c["duration_ms"] == 1000 for c in clips)
            assert len({c["clip_id"] for c in clips}) == 3
            assert all("file_name" not in c for c in clips)
            downloaded = await http.get(clips[0]["video_url"])
            assert downloaded.status_code == 200 and downloaded.headers["content-type"] == "video/mp4"
            assert downloaded.headers["cache-control"] == "no-store"
            assert b"ftyp" in downloaded.content[:32]
            video_bytes = downloaded.content
            partial = await http.get(clips[0]["video_url"], headers={"Range": "bytes=0-31"})
            assert partial.status_code == 206 and partial.content == video_bytes[:32]
            assert (await http.get(clips[0]["video_url"], headers={"X-Demo-Key": "wrong"})).status_code == 401
            assert (await http.get("/v1/calls/missing/clips")).status_code == 404
            assert server.app.state.store.failures == 0

    files = list(root.iterdir())
    assert len(files) == 3 and all(path.suffix == ".mp4" for path in files)
    if os.name == "posix":
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in files)
    info = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(files[0]),
    ], capture_output=True, check=True, timeout=5).stdout)
    stream = info["streams"][0]
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
    assert float(info["format"]["duration"]) == pytest.approx(1.0, abs=.05)
    assert int(stream["nb_frames"]) == 5

    async with Server("scripted", **options) as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            restored = (await http.get(f"/v1/calls/{call_id}/clips")).json()["clips"]
            assert [c["clip_id"] for c in restored] == [c["clip_id"] for c in clips]
            assert (await http.get(restored[0]["video_url"])).content == video_bytes
            assert (await http.get(f"/v1/calls/{call_id}")).json()["stored"] is True


@pytest.mark.anyio
async def test_encoder_failure_does_not_block_call_or_report(tmp_path, colour_jpeg, monkeypatch):
    def broken_encoder(self, job):
        raise RuntimeError("test failure")
    monkeypatch.setattr(videos.AttemptClips, "_encode", broken_encoder)
    async with Server("scripted", database_path=str(tmp_path / "db.sqlite3"),
                      clip_storage_dir=str(tmp_path / "clips")) as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            call_id, link = await answered_call(server, http)
            await capture(link, "failed_attempt", colour_jpeg)
            # Publication is independent of the encoder; no video-ready event or duplicate message is needed.
            await link.publish({"status": "as_usual", "signs": []}, "final")
            await server.app.state.desk.attempt_clips.flush()
            [clip] = (await http.get(f"/v1/calls/{call_id}/clips")).json()["clips"]
            assert clip["state"] == "failed" and clip["error_code"] == "encode_failed"
            assert clip["video_url"] is None
            assert (await http.get(f"/v1/clips/{clip['clip_id']}/video")).status_code == 409
            assert (await http.get(f"/v1/calls/{call_id}")).json()["message"]["kind"] == "as_usual"
            assert (await http.get("/v1/health")).status_code == 200
            assert server.app.state.store.failures == 0


@pytest.mark.anyio
async def test_expiry_deletes_file_and_keeps_metadata(tmp_path, colour_jpeg, monkeypatch):
    async with Server("scripted", database_path=str(tmp_path / "db.sqlite3"),
                      clip_storage_dir=str(tmp_path / "clips")) as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            call_id, link = await answered_call(server, http)
            await capture(link, "expiring", colour_jpeg)
            archive = server.app.state.desk.attempt_clips
            await archive.flush()
            [row] = await server.app.state.store.call_clips(call_id)
            monkeypatch.setattr(videos, "now_ms", lambda: row["expires_at_ms"] + 1)
            monkeypatch.setattr("checkin_backend.app.now_ms", lambda: row["expires_at_ms"] + 1)
            assert (await http.get(f"/v1/clips/{row['clip_id']}/video")).status_code == 410
            await archive.cleanup()
            await server.app.state.store.flush()
            assert list((tmp_path / "clips").iterdir()) == []
            [clip] = (await http.get(f"/v1/calls/{call_id}/clips")).json()["clips"]
            assert clip["state"] == "expired" and clip["video_url"] is None


@pytest.mark.anyio
async def test_disabled_recording_keeps_existing_protocol_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(videos.shutil, "which", lambda name: None)
    async with Server("scripted") as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            call_id, _ = await answered_call(server, http)
            assert (await http.get(f"/v1/calls/{call_id}/clips")).json()["clips"] == []
            assert (await http.get("/v1/clips/video_missing/video")).status_code == 404
            assert server.app.state.desk.attempt_clips is None


def test_enabled_recording_refuses_missing_encoder(tmp_path, monkeypatch):
    monkeypatch.setattr(videos.shutil, "which", lambda name: None)
    app = create_app(Settings(demo_key=KEY, call_runner="scripted", database_path=str(tmp_path / "db.sqlite3"),
                              clip_storage_dir=str(tmp_path / "clips")))
    with pytest.raises(ValueError, match="ffmpeg"):
        with TestClient(app):
            pass


@pytest.mark.anyio
async def test_complete_http_call_records_tasks_without_delaying_family_message(tmp_path, colour_jpeg, monkeypatch):
    monkeypatch.setattr("tests.client_sim.tiny_jpeg", lambda: colour_jpeg)
    async with Server("scripted", database_path=str(tmp_path / "db.sqlite3"),
                      clip_storage_dir=str(tmp_path / "clips")) as server:
        status = await run_call(server, SimClient(base=server.base, key=KEY))
        assert status["state"] == "ended" and status["message"]["kind"] == "incomplete"
        await server.app.state.desk.attempt_clips.flush()
        recordings = await server.app.state.store.call_clips(status["call_id"])
        assert [(c["task"], c["state"], c["capture_status"]) for c in recordings] == [
            ("smile", "ready", "finished"), ("arms", "ready", "finished")]
        assert server.app.state.store.failures == 0


@pytest.mark.anyio
async def test_pending_encoding_is_visible_and_does_not_hold_up_requests(tmp_path, colour_jpeg, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = videos.AttemptClips._encode

    def waiting_encoder(self, job):
        started.set()
        if not release.wait(5):
            raise RuntimeError("Test encoder was not released")
        return original(self, job)

    monkeypatch.setattr(videos.AttemptClips, "_encode", waiting_encoder)
    async with Server("scripted", database_path=str(tmp_path / "db.sqlite3"),
                      clip_storage_dir=str(tmp_path / "clips")) as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            call_id, link = await answered_call(server, http)
            try:
                await capture(link, "pending", colour_jpeg)
                assert await asyncio.to_thread(started.wait, 2)
                [clip] = (await http.get(f"/v1/calls/{call_id}/clips")).json()["clips"]
                assert clip["state"] == "queued" and clip["video_url"] is None
                response = await http.get(f"/v1/clips/{clip['clip_id']}/video")
                assert response.status_code == 202 and response.headers["retry-after"] == "1"
                assert (await http.get("/v1/health")).status_code == 200
                # A blocked encoder cannot grow its pending buffer without bound.
                for i in range(videos.MAX_PENDING_CLIPS + 1):
                    await capture(link, f"queued_{i}", colour_jpeg)
                rows = await server.app.state.store.call_clips(call_id)
                assert len([r for r in rows if r["error_code"] == "encoder_busy"]) == 1
            finally:
                release.set()
            await server.app.state.desk.attempt_clips.flush()


@pytest.mark.anyio
async def test_restart_marks_unfinished_job_failed_and_removes_partial_output(tmp_path, colour_jpeg):
    root = tmp_path / "clips"
    options = {"database_path": str(tmp_path / "db.sqlite3"), "clip_storage_dir": str(root)}
    clip_id = "video_" + "a" * 32
    async with Server("scripted", **options) as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            call_id, link = await answered_call(server, http)
            await link.start_capture("stopped", "smile", CAPTURE)
            stamp = now_ms()
            server.app.state.store.record_clip_queued(
                clip_id=clip_id, call_id=call_id, attempt_id="stopped", task="smile", session_label="live",
                capture_status="finished", duration_ms=1000, frame_count=1, recorded_at_ms=stamp,
                created_at_ms=stamp, expires_at_ms=stamp + 60_000,
            )
            await server.app.state.store.flush()
            # Simulate interruption between file creation and its metadata commit.
            (root / (clip_id + "_stopped.part")).write_bytes(b"partial output")
            (root / (clip_id + ".mp4")).write_bytes(b"unpublished output")
    async with Server("scripted", **options) as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            [clip] = (await http.get(f"/v1/calls/{call_id}/clips")).json()["clips"]
            assert clip["state"] == "failed" and clip["error_code"] == "process_stopped"
            assert list(root.iterdir()) == []
            assert (await http.get(f"/v1/clips/{clip_id}/video")).status_code == 409
