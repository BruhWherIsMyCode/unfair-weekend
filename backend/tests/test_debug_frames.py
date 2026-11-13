"""DEBUG_FRAMES_DIR: a development call keeps every task frame and the family summary on disk, for replaying."""
import json

import pytest

from tests.client_sim import SimClient
from tests.test_call_service import KEY, Server, run_call


@pytest.mark.anyio
async def test_frames_and_summary_are_saved_only_when_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG_FRAMES_DIR", str(tmp_path))
    async with Server("scripted") as server:
        status = await run_call(server, SimClient(base=server.base, key=KEY))
    call_dir = tmp_path / status["call_id"]
    folders = sorted(p.name.split("_")[0] for p in call_dir.iterdir() if p.is_dir())
    assert folders == ["arms", "sentence", "smile"] or {"arms", "smile"} <= set(folders)
    arms = next(p for p in call_dir.iterdir() if p.name.startswith("arms_"))
    assert len(list(arms.glob("*.jpg"))) == 75
    summary = json.loads(next(call_dir.glob("summary_*.json")).read_text())
    assert summary["status"] == "incomplete"


@pytest.mark.anyio
async def test_nothing_is_saved_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DEBUG_FRAMES_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    async with Server("scripted") as server:
        await run_call(server, SimClient(base=server.base, key=KEY))
    assert list(tmp_path.iterdir()) == []
