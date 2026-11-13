"""The call agent's own words (say_text, line_id agent_remark), the smile capture window, a whole call where a scripted
fake Claude uses say_own + ask_followup, and the runner's choice between the analysis and the scripted call.

No network: ElevenLabs is a mock transport (or absent), Claude is a scripted fake, the phone is the client simulator.
"""

import httpx
import pytest

from checkin_backend import runner
from checkin_backend.config import Settings
from checkin_backend.runner import ensure_analysis_path
from checkin_backend.speech import SpeechGateway
from tests.client_sim import SimClient
from tests.test_call_service import KEY, Server, run_call

SUMMARY = {"person_id": "p_demo", "label": "live", "duration_s": 0, "answers": {}, "signs": [], "status": "incomplete",
           "baseline_sessions": 0, "baseline_needed": 5, "clip_id": None}


def own_words_driver(*texts):
    """A call driver that greets, says each text as the agent's own words and publishes an incomplete final."""

    async def driver(link):
        await link.say("greet")
        for text in texts:
            await link.say_text(text)
        await link.say("bye")
        await link.publish(dict(SUMMARY), "final")
        return dict(SUMMARY)

    return driver


@pytest.mark.anyio
async def test_say_text_sends_an_agent_remark_command_with_the_text_and_audio(monkeypatch):
    monkeypatch.setattr(runner, "run_scripted_call", own_words_driver("Oh, I'm sorry to hear that."))
    async with Server("scripted") as server:
        sim = SimClient(base=server.base, key=KEY)
        status = await run_call(server, sim)
    assert sim.said == ["greet", "agent_remark", "bye"]
    command = sim.said_commands[1]
    assert command["kind"] == "say" and command["line_id"] == "agent_remark"
    assert command["text"] == "Oh, I'm sorry to hear that."  # the caption is the given text
    assert command["audio_path"].startswith("/v1/calls/") and "/speech/" in command["audio_path"]
    assert command["duration_ms"] > 0 and "video_path" not in command  # no clip is generated during the call
    assert status["reason"] == "completed"


@pytest.mark.anyio
async def test_without_a_key_the_own_line_is_caption_only_like_other_uncached_lines(monkeypatch):
    monkeypatch.setattr(runner, "run_scripted_call", own_words_driver("Let's keep this short and easy."))
    speech = SpeechGateway(api_key="")
    async with Server("scripted", speech=speech) as server:
        sim = SimClient(base=server.base, key=KEY)
        await run_call(server, sim)
    assert sim.said_commands[1]["line_id"] == "agent_remark"
    assert sim.said_commands[1]["text"] == "Let's keep this short and easy."
    prepared = await speech.prepare("Let's keep this short and easy.")
    assert prepared.voiced is False and prepared.audio  # silence of the estimated length plays under the caption


@pytest.mark.anyio
async def test_with_a_key_the_voice_is_generated_on_the_fly_and_cached_by_text(monkeypatch, tmp_path):
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=bytes(3200))  # 0.1 s of pcm_16000

    speech = SpeechGateway(api_key="test-key", cache_dir=tmp_path, transport=httpx.MockTransport(respond))
    monkeypatch.setattr(runner, "run_scripted_call", own_words_driver("Take your time.", "Take your time."))
    async with Server("scripted", speech=speech) as server:
        sim = SimClient(base=server.base, key=KEY)
        await run_call(server, sim)
    texts = [c["text"] for c in sim.said_commands if c["line_id"] == "agent_remark"]
    assert texts == ["Take your time.", "Take your time."]
    own_requests = [r for r in requests if b"Take your time." in r.content]
    assert len(own_requests) == 1  # the second identical line came from the cache


@pytest.mark.anyio
async def test_a_speech_error_becomes_playback_failed_like_say(monkeypatch):
    from checkin_backend.link import PlaybackFailed

    seen = []

    async def driver(link):
        try:
            await link.say_text("Hello there.")
        except PlaybackFailed as exc:
            seen.append(str(exc))
        await link.publish(dict(SUMMARY), "final")
        return dict(SUMMARY)

    speech = SpeechGateway(api_key="test-key", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="no")))
    monkeypatch.setattr(runner, "run_scripted_call", driver)
    async with Server("scripted", speech=speech) as server:
        sim = SimClient(base=server.base, key=KEY)
        await run_call(server, sim)
    assert seen and sim.said == []


def test_the_smile_capture_is_six_seconds_everywhere():
    assert runner.CAPTURE_SETTINGS["smile"]["duration_ms"] == 6000
    from checkin_backend.fake_tasks import CAPTURE, WINDOW_MS

    assert CAPTURE["smile"]["duration_ms"] == 6000 and WINDOW_MS["smile"] == 6000


# ---- a whole call where the fake Claude uses say_own and ask_followup -------------------------------------------

ensure_analysis_path()
analysis_calls = pytest.importorskip("tests.test_analysis_calls", reason="Agent 3's analysis package is not available")


@pytest.mark.anyio
async def test_a_whole_call_with_say_own_and_ask_followup_through_the_real_stack(monkeypatch, tmp_path):
    from analysis.session import run_call as run_call_module
    from analysis.session.claude_client import FakeClaudeClient, tool_use_response

    data = analysis_calls.install(monkeypatch, tmp_path)
    monkeypatch.setenv("CALL_DRIVER", "claude")
    script = [tool_use_response("say", {"line_id": "greet"}),
              tool_use_response("ask", {"question_id": "q_medicine"}),
              tool_use_response("say_own", {"text": "Thank you for telling me."}),
              tool_use_response("record_answer", {"question_id": "q_medicine", "value": "unclear"}),
              tool_use_response("ask", {"question_id": "q_feeling"}),
              tool_use_response("ask_followup", {"question_id": "q_feeling", "text": "What is bothering you most?"}),
              tool_use_response("record_answer", {"question_id": "q_feeling", "value": "tired"}),
              tool_use_response("ask", {"question_id": "q_unusual"}),
              tool_use_response("record_answer", {"question_id": "q_unusual", "value": "nothing"}),
              tool_use_response("run_task", {"task": "smile"}),
              tool_use_response("say_own", {"text": "One more little exercise."}),
              tool_use_response("run_task", {"task": "arms"}),
              tool_use_response("finish_call", {})]
    client = FakeClaudeClient(script)
    monkeypatch.setattr(run_call_module, "build_client", lambda backend: client)
    async with Server("analysis") as server:
        sim = SimClient(base=server.base, key=KEY, time_scale=0.05)
        status = await run_call(server, sim, timeout=90)
    assert status["reason"] == "completed" and status["summary"]["status"] == "as_usual"
    remarks = [c["text"] for c in sim.said_commands if c["line_id"] == "agent_remark"]
    assert remarks == ["Thank you for telling me.", "What is bothering you most?", "One more little exercise."]
    order = ["greet", "q_medicine", "agent_remark", "q_feeling", "agent_remark", "q_unusual", "setup_phone",
             "task_smile", "smile_now", "agent_remark", "task_arms", "bye"]
    cursor = -1
    for line in order:  # the lines come in this order (other lines may sit between them)
        cursor = sim.said.index(line, cursor + 1)
    assert client.requests and "say_own" in [t["name"] for t in client.requests[0]["tools"]]
    del data


# ---- the runner's choice between the analysis and the scripted call --------------------------------------------------

def stub_check(monkeypatch, problems):
    ensure_analysis_path()
    from analysis import selfcheck

    monkeypatch.setattr(selfcheck, "check", lambda *args, **kwargs: list(problems))


def test_auto_uses_the_analysis_when_the_selfcheck_finds_nothing(monkeypatch):
    stub_check(monkeypatch, [])
    shown = []
    driver = runner.choose_driver("auto", report=shown.append)
    assert driver is not runner.run_scripted_call and shown == []


def test_auto_prints_the_problems_in_one_block_and_uses_the_scripted_call(monkeypatch):
    stub_check(monkeypatch, ["mediapipe cannot be imported. Fix: uv pip install -r analysis/requirements.txt",
                             "the face model file is missing. Fix: python -m analysis.get_models"])
    shown = []
    driver = runner.choose_driver("auto", report=shown.append)
    assert driver is runner.run_scripted_call
    assert len(shown) == 1  # one block, not one line per problem
    block = shown[0]
    assert "MEASURING IS NOT READY" in block and "mediapipe cannot be imported" in block
    assert "the face model file is missing" in block and "scripted call" in block


def test_analysis_with_problems_refuses_at_start_up_with_the_block(monkeypatch):
    stub_check(monkeypatch, ["mediapipe cannot be imported. Fix: uv pip install -r analysis/requirements.txt"])
    with pytest.raises(RuntimeError) as raised:
        runner.choose_driver("analysis")
    assert "MEASURING IS NOT READY" in str(raised.value) and "mediapipe cannot be imported" in str(raised.value)


def test_analysis_without_problems_and_the_other_runners_are_unchanged(monkeypatch):
    stub_check(monkeypatch, [])
    assert runner.choose_driver("analysis") is not runner.run_scripted_call
    assert runner.choose_driver("scripted") is runner.run_scripted_call
    stub_check(monkeypatch, ["a problem"])
    assert runner.choose_driver("fake") is not runner.run_scripted_call  # fake modules need no mediapipe
    assert runner.choose_driver("scripted") is runner.run_scripted_call


def test_the_app_refuses_to_start_with_runner_analysis_and_problems(monkeypatch):
    from checkin_backend.app import create_app

    stub_check(monkeypatch, ["the hand model file is missing. Fix: python -m analysis.get_models"])
    with pytest.raises(RuntimeError):
        create_app(Settings(demo_key=KEY, call_runner="analysis"), speech=SpeechGateway(api_key=""))
