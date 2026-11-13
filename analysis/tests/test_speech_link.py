"""Speech on the PC version: protocol v2 messages, single-use tokens, the sentence task end to end, required tasks per
capability, the client's speech jobs and state. Fake ElevenLabs (no network), fake microphone, fake clock."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

from analysis.pc import link
from analysis.pc.client_speech import SpeechRunner
from analysis.pc.client_state import ClientState
from analysis.pc.demo_link import ANSWERS, DemoAgent, FakeElevenLabs, ScriptedClient, seed_store, timed_words
from analysis.pc.mic import MicrophoneUnavailable, Recording
from analysis.pc.remote_adapter import RemoteAdapter
from analysis.pc.server import PcServer
from analysis.pc.store import BaselineStore
from analysis.session import limits
from analysis.session.claude_client import FakeClaudeClient
from analysis.session.clock import FakeClock
from analysis.speech.elevenlabs import SpeechError

KEY = "test-key-should-never-leak-123"
ENV = {"ELEVENLABS_API_KEY": KEY}


def msg(type_, **fields):
    m = {"type": type_, "call_id": None, "seq": 1}
    m.update(fields)
    return m


def hello(speech=True):
    return msg("hello", protocol=2 if speech else 1, client="pc", person_id="p",
               capabilities={"camera": True, "microphone": speech, "measures": ["smile", "arms"], "speech": speech})


WORDS = [{"text": "Today", "start": 0.1, "end": 0.5, "logprob": -0.1}, {"text": "I", "start": None, "end": None,
                                                                         "logprob": None}]


# ---- protocol v2 validation --------------------------------------------------------------------------------

def test_v2_messages_validate_and_bad_ones_do_not():
    ok_client = [hello(), hello(False), msg("stt_token_request", purpose="answer", ref="r1"),
                 msg("stt_token_request", purpose="sentence", ref="r2"),
                 msg("answer", question_id="q", transcript="hi", words=WORDS, stt_error=None),
                 msg("answer", question_id="q", transcript=None, words=[], stt_error="speech_not_configured"),
                 msg("answer", question_id="q", transcript="v1 client has neither"),
                 msg("speech_result", attempt_id="A1", transcript="x", words=WORDS, duration_recorded_s=4.2,
                     stt_error=None),
                 msg("speech_result", attempt_id="A1", transcript=None, words=[], duration_recorded_s=0)]
    for m in ok_client:
        assert link.validate_client_message(m) is None, m
    ok_server = [msg("stt_token", ref="r1", token="sutkn_1", error=None),
                 msg("stt_token", ref="r1", token=None, error="speech_not_configured"),
                 msg("say", line_id="greet", text="Hi", audio_pcm16k_b64="QUJD"), msg("say", line_id="g", text="Hi")]
    for m in ok_server:
        assert link.validate_server_message(m) is None, m
    for bad in (msg("stt_token_request", purpose="everything", ref="r"), msg("stt_token_request", purpose="answer"),
                msg("speech_result", attempt_id="A1", transcript="x", words="no", duration_recorded_s=1),
                msg("speech_result", attempt_id="A1", transcript="x", words=[{"text": 1}], duration_recorded_s=1),
                msg("speech_result", attempt_id="A1", transcript="x", words=[], duration_recorded_s="long"),
                msg("answer", question_id="q", transcript="x", words=[{"start": 1}])):
        assert link.validate_client_message(bad), bad
    assert link.validate_server_message(msg("stt_token", ref="r"))  # token and error are required keys
    assert link.validate_server_message(msg("say", line_id="g", text="Hi", audio_pcm16k_b64=5))
    assert link.validate_server_message(msg("say", line_id="g", text="Hi",
                                            audio_pcm16k_b64="A" * (link.MAX_AUDIO_B64_CHARS + 1)))


# ---- tokens ------------------------------------------------------------------------------------------------------

def make_server(tmp_path, environ=ENV, eleven=None, **kw):
    eleven = eleven or FakeElevenLabs()
    server = PcServer("t", "Ona", "off", True, "live", tmp_path / "data", tmp_path / "runs", FakeClock(),
                      lambda s: None, http=eleven.server_http, environ=environ, inline_blocking=True, **kw)
    return server, eleven


async def connected(server, speech=True):
    s, c = link.MemoryTransport.pair()
    task = asyncio.ensure_future(server.handle_connection(s))
    await c.send(hello(speech))
    ok = await asyncio.wait_for(c.recv(), 2)
    return task, c, ok


async def ask_token(c, ref, purpose="answer"):
    await c.send(msg("stt_token_request", purpose=purpose, ref=ref))
    reply = await asyncio.wait_for(c.recv(), 2)
    assert reply["type"] == "stt_token" and reply["ref"] == ref
    return reply


def test_token_flow_gives_a_token_and_never_the_key(tmp_path):
    async def scenario():
        server, eleven = make_server(tmp_path)
        task, c, ok = await connected(server)
        reply = await ask_token(c, "r1")
        assert reply["token"] == "sutkn_demo_1" and reply["error"] is None and KEY not in json.dumps([ok, reply])
        await c.close()
        await asyncio.wait_for(task, 2)
        assert eleven.minted == 1 and eleven.violations == []

    asyncio.run(scenario())


def test_without_a_key_the_token_is_null_with_speech_not_configured(tmp_path):
    async def scenario():
        server, eleven = make_server(tmp_path, environ={})
        task, c, _ = await connected(server)
        reply = await ask_token(c, "r1")
        assert reply["token"] is None and reply["error"] == "speech_not_configured" and eleven.minted == 0
        await c.close()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_only_one_token_in_flight(tmp_path):
    async def scenario():
        server, eleven = make_server(tmp_path)
        task, c, _ = await connected(server)
        await c.send(msg("stt_token_request", purpose="answer", ref="a"))
        await c.send(msg("stt_token_request", purpose="answer", ref="b"))
        replies = [await asyncio.wait_for(c.recv(), 2) for _ in range(2)]
        by_ref = {r["ref"]: r for r in replies}
        assert by_ref["a"]["token"] and by_ref["b"]["token"] is None and by_ref["b"]["error"] == "token_busy"
        assert eleven.minted == 1
        reply = await ask_token(c, "c")  # the first one is done: the next goes through
        assert reply["token"]
        await c.close()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_ten_tokens_per_call_and_the_count_resets_for_a_new_call(tmp_path):
    async def scenario():
        server, eleven = make_server(tmp_path)
        task, c, _ = await connected(server)
        for i in range(limits.STT_TOKENS_PER_CALL):
            assert (await ask_token(c, "r%d" % i))["token"]
        over = await ask_token(c, "r_over")
        assert over["token"] is None and over["error"] == "token_limit" and eleven.minted == 10
        server._new_adapter(link.Endpoint(link.MemoryTransport.pair()[0]))  # a new call starts
        assert (await ask_token(c, "again"))["token"]
        await c.close()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_a_failing_mint_reports_a_short_error_without_the_key(tmp_path):
    async def scenario():
        def broken(method, url, headers, body, timeout):
            return 401, {}, ('{"detail":"bad key %s"}' % KEY).encode()

        server = PcServer("t", "Ona", "off", True, "live", tmp_path / "d", tmp_path / "r", FakeClock(),
                          lambda s: None, http=broken, environ=ENV, inline_blocking=True)
        task, c, _ = await connected(server)
        reply = await ask_token(c, "r1")
        assert reply["token"] is None and reply["error"] == "token_failed_401" and KEY not in json.dumps(reply)
        await c.close()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


# ---- the adapter: answers with words, line audio ---------------------------------------------------------------

class FakeSession:
    def __init__(self):
        self.modules, self.notes, self.hung_up = {}, [], False

    def note(self, kind, **fields):
        self.notes.append(kind)

    def hang_up(self):
        self.hung_up = True


class FakeFamily:
    async def publish(self, summary, phase, message_id=None):
        return {"message_id": "m", "delivery": "x"}


def adapter_rig(**kw):
    s, c = link.MemoryTransport.pair()
    clock = FakeClock()
    adapter = RemoteAdapter(link.Endpoint(s), {"greet": "Hello, {name}.", "q_feeling": "How?"}, "Ona", clock,
                            FakeFamily(), **kw)
    adapter.session = FakeSession()
    return adapter, c, clock


def test_an_answer_with_words_reaches_listen_and_the_words_are_kept():
    async def scenario():
        adapter, client, clock = adapter_rig()
        task = asyncio.ensure_future(adapter.listen("q_feeling", 8.0))
        await clock.settle()
        assert (await client.recv())["type"] == "listen"
        await adapter.dispatch(msg("answer", question_id="q_feeling", transcript="I feel fine", words=WORDS,
                                   stt_error=None))
        await clock.settle()
        assert task.result() == "I feel fine" and adapter.answer_words["q_feeling"] == WORDS

    asyncio.run(scenario())


def test_a_speech_to_text_failure_is_noted_and_the_answer_is_none():
    async def scenario():
        adapter, client, clock = adapter_rig()
        task = asyncio.ensure_future(adapter.listen("q_feeling", 8.0))
        await clock.settle()
        await adapter.dispatch(msg("answer", question_id="q_feeling", transcript=None, words=[], stt_error="stt_http_401"))
        await clock.settle()
        assert task.result() is None and "speech_to_text_failed" in adapter.session.notes

    asyncio.run(scenario())


def test_the_answer_wait_includes_the_grace_for_recording_and_transcribing():
    async def scenario():
        adapter, client, clock = adapter_rig(answer_grace_s=limits.SPEECH_ANSWER_GRACE_S)
        task = asyncio.ensure_future(adapter.listen("q_feeling", 8.0))
        await clock.settle()
        await clock.advance(8.0 + 5.0)
        await clock.settle()
        assert not task.done()  # a v1 client would have been given up on here
        await adapter.dispatch(msg("answer", question_id="q_feeling", transcript="late but fine", words=[]))
        await clock.settle()
        assert task.result() == "late but fine"

    asyncio.run(scenario())


def test_say_carries_audio_only_for_lines_that_have_it():
    async def scenario():
        adapter, client, clock = adapter_rig(line_audio={"greet": "QUJD"})
        for line in ("greet", "q_feeling"):
            asyncio.ensure_future(adapter.say(line))
        await clock.settle()
        first, second = await client.recv(), await client.recv()
        assert first["audio_pcm16k_b64"] == "QUJD" and "audio_pcm16k_b64" not in second
        assert link.validate_server_message(first) is None and link.validate_server_message(second) is None

    asyncio.run(scenario())


def test_the_speech_result_is_the_one_sample_after_the_keep_alives():
    async def scenario():
        adapter, client, clock = adapter_rig()
        await adapter.start_capture("A1", "smile", {})  # makes the queue; the task name does not matter here
        gen = adapter.samples("A1")
        for i in range(3):
            await adapter.dispatch(msg("reading", attempt_id="A1", sequence=i, captured_ms=i * 250, task="sentence",
                                       data={"phase": "recording", "level": 0.1}))
        await adapter.dispatch(msg("speech_result", attempt_id="A1", transcript="x", words=WORDS,
                                   duration_recorded_s=4.25, stt_error=None))
        await adapter.dispatch(msg("capture_ended", attempt_id="A1"))
        got = [s async for s in gen]
        assert [s["format"] for s in got] == ["reading"] * 3 + ["speech"]
        assert got[-1]["sequence"] == 3 and got[-1]["captured_ms"] == 4250
        assert got[-1]["data"] == {"transcript": "x", "words": WORDS, "duration_recorded_s": 4.25, "stt_error": None}
        await adapter.dispatch(msg("speech_result", attempt_id="A1", transcript="late", words=[], duration_recorded_s=1))
        assert "speech_result_ignored" in adapter.session.notes

    asyncio.run(scenario())


# ---- whole calls ---------------------------------------------------------------------------------------------------

async def play(tmp_path, client_speech=True, environ=ENV, seed=True, calls=({},), agent=None, **server_kw):
    clock, out = FakeClock(), []
    eleven = FakeElevenLabs()
    kw = dict(http=eleven.server_http, environ=environ, inline_blocking=True)
    kw.update(server_kw)
    if agent is not None:
        kw["client_factory"] = lambda: FakeClaudeClient(agent, clock)
    server = PcServer("t", "Ona", "api" if agent is not None else "off", True, "live", tmp_path / "data",
                      tmp_path / "runs", clock, out.append, **kw)
    if seed:
        seed_store(server.store, speech=True)
    s, c = link.MemoryTransport.pair()
    task = asyncio.ensure_future(server.handle_connection(s))
    client = ScriptedClient(c, clock, speech=client_speech, eleven=eleven)
    await client.connect()
    results = [await client.run_call(**kw) for kw in calls]
    await client.close()
    await asyncio.wait_for(task, 5)
    return server, client, results, out, eleven


def record(tmp_path, result):
    return json.loads((tmp_path / "runs" / ("%s.json" % result["call_id"])).read_text("utf-8"))


def kinds(rec, kind):
    return [e for e in rec["session_log"] if e["kind"] == kind]


def test_a_spoken_call_with_a_normal_sentence_ends_as_usual_and_stores_the_duration(tmp_path):
    server, client, [result], out, eleven = asyncio.run(play(tmp_path))
    s = result["summary"]
    assert s["status"] == "as_usual" and [x["sign"] for x in s["signs"]] == ["face", "arm", "speech"]
    assert s["signs"][2]["attempts"][0]["value"] == 2.7 and s["signs"][2]["attempts"][0]["outside"] is False
    assert [m["task"] for m in client.received if m["type"] == "start_capture"] == ["smile", "arms", "sentence"]
    assert any(m["type"] == "speech_result" for m in client.sent)
    assert all(not m.get("audio") for m in client.sent)  # the client never sends audio
    stored = json.loads(server.store.path.read_text())["sessions"][-1]["values"]
    assert stored["sentence.duration_s"] == 2.7 and "speech_duration_v1" not in stored
    assert eleven.minted == 4 and len(eleven.stt_calls) == 4 and eleven.violations == []  # 3 answers + the sentence
    heard_line = [line for line in out if line.startswith("task sentence")][0]
    assert "word error rate 0.0" in heard_line and "longest pause" in heard_line and "words/min" in heard_line


def test_the_key_never_travels_over_the_link_or_into_the_record(tmp_path):
    server, client, [result], out, eleven = asyncio.run(play(tmp_path))
    everything = json.dumps([client.sent, client.received, out, record(tmp_path, result)], default=str)
    assert KEY not in everything


@pytest.mark.usefixtures("two_readings")
def test_a_long_pause_both_times_is_a_confirmed_speech_change_published_mid_call(tmp_path):
    server, client, [result], out, eleven = asyncio.run(
        play(tmp_path, calls=({"sentences": ({"pause_before": 3, "pause_s": 2.5},)},)))
    s = result["summary"]
    assert s["status"] == "change_noticed"
    sign = [x for x in s["signs"] if x["sign"] == "speech"][0]
    assert sign["confirmed"] is True and sign["side"] is None and len(sign["attempts"]) == 2
    assert all(a["outside"] and a["value"] > a["usual_high"] for a in sign["attempts"])
    assert "speech" in result["family_text"] and "call 112" in result["family_text"]
    rec = record(tmp_path, result)
    assert [p["phase"] for p in kinds(rec, "published")] == ["mid_call", "final"]
    assert len(json.loads(server.store.path.read_text())["sessions"]) == 6  # a confirmed change is not stored


@pytest.mark.usefixtures("two_readings")
def test_one_long_pause_then_a_normal_sentence_is_not_confirmed(tmp_path):
    server, client, [result], out, eleven = asyncio.run(
        play(tmp_path, calls=({"sentences": ({"pause_before": 3, "pause_s": 2.5}, {})},)))
    s = result["summary"]
    sign = [x for x in s["signs"] if x["sign"] == "speech"][0]
    assert s["status"] == "as_usual" and sign["confirmed"] is False
    assert [a["outside"] for a in sign["attempts"]] == [True, False]


def test_a_faster_sentence_is_never_outside(tmp_path):
    # an unusually long usual makes today's 2.7 s "faster than usual"
    async def scenario():
        clock, out = FakeClock(), []
        eleven = FakeElevenLabs()
        server = PcServer("t", "Ona", "off", True, "live", tmp_path / "data", tmp_path / "runs", clock, out.append,
                          http=eleven.server_http, environ=ENV, inline_blocking=True)
        for i in range(6):
            server.store.append("live", {"corner_height_v1": 0.005 + 0.002 * (i % 3), "arms.hand_left.hand_sink": 0.0,
                                         "arms.hand_right.hand_sink": 0.0, "sentence.duration_s": 9.0 + 0.1 * (i % 3)})
        s, c = link.MemoryTransport.pair()
        task = asyncio.ensure_future(server.handle_connection(s))
        client = ScriptedClient(c, clock, speech=True, eleven=eleven)
        await client.connect()
        result = await client.run_call()
        await client.close()
        await asyncio.wait_for(task, 5)
        return result

    result = asyncio.run(scenario())
    sign = [x for x in result["summary"]["signs"] if x["sign"] == "speech"][0]
    assert sign["attempts"][0]["value"] < sign["attempts"][0]["usual_low"] and sign["attempts"][0]["outside"] is False
    assert result["summary"]["status"] == "as_usual"


def test_required_tasks_follow_the_capabilities(tmp_path):
    def tasks_of(client_speech, environ, sub):
        _, client, [result], out, _ = asyncio.run(play(tmp_path / sub, client_speech=client_speech, environ=environ))
        entry = kinds(record(tmp_path / sub, result), "required_tasks")[0]
        return entry, client, result, out

    entry, client, _, _ = tasks_of(True, ENV, "a")
    assert entry["tasks"] == ["smile", "arms", "sentence"] and entry["speech"] is True

    entry, client, result, out = tasks_of(True, {}, "b")  # speech client, no key on the server
    assert entry["tasks"] == ["smile", "arms"] and entry["speech"] is False and entry["server_key"] is False
    assert [m["task"] for m in client.received if m["type"] == "start_capture"] == ["smile", "arms"]
    assert client.token_errors and set(client.token_errors) == {"speech_not_configured"}
    assert any("ELEVENLABS_API_KEY is not set" in line for line in out)

    entry, client, result, _ = tasks_of(False, ENV, "c")  # a v1 client (no speech) on a server with a key
    assert entry["tasks"] == ["smile", "arms"] and entry["client_speech"] is False
    assert result["summary"]["status"] == "as_usual"


def test_a_v1_client_still_works_and_gets_a_v1_hello_ok(tmp_path):
    async def scenario():
        server, _ = make_server(tmp_path)
        task, c, ok = await connected(server, speech=False)
        assert ok["type"] == "hello_ok" and ok["protocol"] == 1
        await c.close()
        await asyncio.wait_for(task, 2)
        task, c, ok = await connected(server, speech=True)
        assert ok["protocol"] == 2
        await c.close()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_the_call_agent_sees_the_speech_numbers_in_run_task(tmp_path):
    seen = []

    class Agent(DemoAgent):
        def _react(self, last):
            if last.get("_tool") == "run_task" and last.get("task") == "sentence":
                seen.append(last)
            super()._react(last)

    server, client, [result], out, _ = asyncio.run(play(tmp_path, agent=Agent(speech=True)))
    assert len(seen) == 1
    facts = seen[0]
    assert set(facts["speech"]) == {"heard", "word_error_rate", "missing_words", "changed_words", "extra_words",
                                    "longest_pause_s", "words_per_min"}
    assert facts["speech"]["word_error_rate"] == 0.0 and facts["reading"]["unit"] == "s"
    assert facts["reading"]["outside_usual"] is False and facts["reading"]["side"] is None
    assert result["summary"]["status"] == "as_usual"


# ---- the client: speech jobs ---------------------------------------------------------------------------------------

class Rec:
    def __init__(self, heard=True, seconds=4.0):
        self.heard, self.seconds, self.calls = heard, seconds, []

    def __call__(self, max_s, silence_s, start_timeout_s, on_level):
        self.calls.append((max_s, silence_s, start_timeout_s))
        return Recording(b"WAV" if self.heard else None, self.seconds, self.heard, "silence" if self.heard else "no_speech")


def runner(rec=None, transcribe=None, token=("tok", None)):
    tokens, transcribed = [], []

    def request_token(purpose, ref):
        tokens.append((purpose, ref))
        return token

    def fake_transcribe(wav, tok, language):
        transcribed.append((wav, tok, language))
        return transcribe(wav, tok, language) if transcribe else {"text": " Hello there ", "words": WORDS}

    return SpeechRunner(rec or Rec(), fake_transcribe, request_token, "en"), tokens, transcribed


def test_nobody_speaking_never_calls_elevenlabs_or_asks_for_a_token():
    r, tokens, transcribed = runner(Rec(heard=False))
    out = r.listen("q_feeling", 8.0)
    assert out["transcript"] is None and out["stt_error"] is None and out["words"] == []
    assert tokens == [] and transcribed == []


def test_a_spoken_answer_is_recorded_with_the_listen_settings_and_transcribed():
    rec = Rec()
    r, tokens, transcribed = runner(rec)
    out = r.listen("q_feeling", 8.0)
    assert rec.calls == [(8.0, limits.SPEECH_SILENCE_S, limits.SPEECH_ANSWER_START_S)] and limits.SPEECH_SILENCE_S == 1.5
    assert out == {"kind": "answer", "question_id": "q_feeling", "transcript": "Hello there", "words": WORDS,
                   "stt_error": None}
    assert tokens[0][0] == "answer" and transcribed == [(b"WAV", "tok", "en")]


def test_the_sentence_is_recorded_for_twelve_seconds_at_most_and_reports_the_duration():
    rec = Rec(seconds=5.5)
    r, tokens, _ = runner(rec)
    out = r.sentence("A3", 12.0)
    assert rec.calls == [(12.0, 1.5, limits.SPEECH_SENTENCE_START_S)] and tokens[0][0] == "sentence"
    assert out["kind"] == "speech_result" and out["attempt_id"] == "A3" and out["duration_recorded_s"] == 5.5
    assert out["transcript"] == "Hello there"


def test_failures_become_stt_error_and_never_raise():
    r, _, _ = runner(token=(None, "speech_not_configured"))
    out = r.listen("q", 8.0)
    assert out["stt_error"] == "speech_not_configured" and out["transcript"] is None and r.speech_broken is True

    def http_401(wav, tok, language):
        raise SpeechError("x", 401, "bad")

    r, _, _ = runner(transcribe=http_401)
    assert r.listen("q", 8.0)["stt_error"] == "stt_http_401"

    def boom(wav, tok, language):
        raise RuntimeError("secret detail")

    r, _, _ = runner(transcribe=boom)
    assert r.sentence("A1", 12.0)["stt_error"] == "stt_failed_RuntimeError"

    def no_mic(*a):
        raise MicrophoneUnavailable("permission_denied")

    r, tokens, _ = runner(rec=no_mic)
    out = r.listen("q", 8.0)
    assert out["stt_error"] == "microphone_permission_denied" and r.speech_broken and tokens == []
    r, _, _ = runner(token=(None, "token_busy"))
    out = r.listen("q", 8.0)
    assert out["stt_error"] == "token_busy" and r.speech_broken is False


# ---- the client: state ---------------------------------------------------------------------------------------------

class T:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def speech_state(**kw):
    clock = T()
    s = ClientState("p", clock, speech=True, **kw)
    s.on_connection(True)
    return s, clock, s.pop_outgoing()


def srv(type_, **f):
    d = {"type": type_, "call_id": "c", "seq": 1}
    d.update(f)
    return d


def test_a_speech_client_announces_protocol_2_and_its_microphone():
    s, clock, out = speech_state()
    t, fields = out[0]
    assert t == "hello" and fields["protocol"] == 2 and fields["capabilities"]["speech"] is True
    assert fields["capabilities"]["microphone"] is True and fields["capabilities"]["measures"] == ["smile", "arms", "sentence"]
    assert link.validate_client_message(msg(t, **fields)) is None
    s.tick()
    assert [f for t, f in s.pop_outgoing() if t == "status"][0]["microphone"] == "available"


def test_listen_becomes_a_speech_job_and_the_result_becomes_an_answer_with_words():
    s, clock, _ = speech_state()
    s.on_server_message(srv("listen", question_id="q_feeling", timeout_s=8.0))
    assert s.take_speech_jobs() == [("listen", "q_feeling", 8.0)] and s.answer is None
    assert s.view().listening == "Listening..."
    s.speech_phase = "transcribing"
    assert s.view().listening.startswith("Working out")
    s.speech_answer_done({"kind": "answer", "question_id": "q_feeling", "transcript": "I am fine", "words": WORDS,
                          "stt_error": None})
    (t, f), = s.pop_outgoing()
    assert t == "answer" and f["transcript"] == "I am fine" and f["words"] == WORDS and f["stt_error"] is None
    assert link.validate_client_message(msg(t, **f)) is None
    assert s.view().heard == "I am fine" and s.view().listening == ""


def test_a_stale_speech_result_is_dropped_and_a_stuck_worker_does_not_hang_the_call():
    s, clock, _ = speech_state()
    s.request_start()
    s.on_server_message(srv("listen", question_id="q1", timeout_s=8.0))
    s.request_hang_up_or_quit()  # she hung up while the answer was being recorded
    s.pop_outgoing()
    s.speech_answer_done({"kind": "answer", "question_id": "q1", "transcript": "late", "words": [], "stt_error": None})
    assert s.pop_outgoing() == []
    s.on_server_message(srv("listen", question_id="q2", timeout_s=8.0))
    clock.t += 8.0 + 18.0 + 0.1
    s.tick()
    out = [(t, f) for t, f in s.pop_outgoing() if t == "answer"]
    assert out == [("answer", {"question_id": "q2", "transcript": None, "stt_error": "speech_timeout"})]


def test_speech_not_configured_sends_the_same_question_back_to_typing():
    s, clock, _ = speech_state()
    s.on_server_message(srv("listen", question_id="q1", timeout_s=8.0))
    s.take_speech_jobs()
    s.speech_answer_done({"kind": "answer", "question_id": "q1", "transcript": None, "words": [],
                          "stt_error": "speech_not_configured"})
    assert s.answer is not None and s.answer.question_id == "q1" and s.view().answer_prompt
    assert [t for t, _ in s.pop_outgoing() if t == "answer"] == []
    s.tick()
    assert [f for t, f in s.pop_outgoing() if t == "status"][0]["microphone"] == "unavailable"
    s.key_char("o")
    s.key_char("k")
    s.key_enter()
    assert s.pop_outgoing()[0][1]["transcript"] == "ok"
    s.on_server_message(srv("listen", question_id="q2", timeout_s=8.0))  # later questions are typed
    assert s.take_speech_jobs() == [] and s.answer is not None


def test_the_sentence_capture_sends_keep_alives_then_the_result_then_ends():
    s, clock, _ = speech_state()
    s.on_server_message(srv("start_capture", attempt_id="A3", task="sentence",
                            settings={"duration_ms": 12000, "sample_period_ms": 250}, cues=[]))
    assert s.take_speech_jobs() == [("sentence", "A3", 12.0)]
    assert [t for t, _ in s.pop_outgoing()] == ["capture_started"]
    clock.t += 0.3
    due = s.capture_due()
    assert due is not None and due.task == "sentence"
    s.speech_phase = "recording"
    s.set_input_level(0.4)
    s.reading_done(due, s.keepalive())
    (t, f), = s.pop_outgoing()
    assert t == "reading" and f["data"] == {"phase": "recording", "level": 0.4} and link.validate_client_message(msg(t, **f)) is None
    clock.t += 14.0  # longer than the 12 s window: the capture is still open (the transcription budget)
    s.tick()
    assert s.capture is not None
    s.speech_sentence_done({"kind": "speech_result", "attempt_id": "A3", "transcript": "x", "words": WORDS,
                            "duration_recorded_s": 5.5, "stt_error": None})
    out = [t for t, _ in s.pop_outgoing()]
    assert out[-2:] == ["speech_result", "capture_ended"] and s.capture is None


def test_a_sentence_without_a_result_ends_with_a_timeout_error_after_the_budget():
    s, clock, _ = speech_state()
    s.on_server_message(srv("start_capture", attempt_id="A3", task="sentence",
                            settings={"duration_ms": 12000, "sample_period_ms": 250}, cues=[]))
    s.pop_outgoing()
    clock.t += 12.0 + limits.SENTENCE_RESULT_GRACE_S + 0.1
    s.tick()
    sent = {t: f for t, f in s.pop_outgoing()}
    assert sent["speech_result"]["stt_error"] == "speech_timeout" and sent["speech_result"]["transcript"] is None
    assert "capture_ended" in sent and s.capture is None


def test_say_audio_is_decoded_for_playback_and_said_follows_the_playback():
    s, clock, _ = speech_state(play_audio=True)
    pcm = b"\x01\x00\x02\x00\x03\x00"
    s.on_server_message(srv("say", line_id="greet", text="Hello", audio_pcm16k_b64=base64.b64encode(pcm).decode()))
    assert s.take_audio() == [("greet", pcm)] and s.pop_outgoing() == []  # not said yet
    s.line_finished("greet")
    assert s.pop_outgoing() == [("said", {"line_id": "greet"})]
    s.on_server_message(srv("say", line_id="bye", text="Bye", audio_pcm16k_b64="@@not base64@@"))
    assert s.take_audio() == []  # undecodable audio: the v7 text-only behaviour
    clock.t += 5
    s.tick()
    assert ("said", {"line_id": "bye"}) in s.pop_outgoing()


def test_without_the_audio_flag_audio_is_ignored():
    s, clock, _ = speech_state()
    s.on_server_message(srv("say", line_id="greet", text="Hello", audio_pcm16k_b64="QUJD"))
    assert s.take_audio() == []
    clock.t += 5
    s.tick()
    assert ("said", {"line_id": "greet"}) in s.pop_outgoing()


def test_a_client_without_speech_keeps_typing(tmp_path):
    clock = T()
    s = ClientState("p", clock)
    s.on_connection(True)
    s.pop_outgoing()
    s.on_server_message(srv("listen", question_id="q", timeout_s=8.0))
    assert s.answer is not None and s.take_speech_jobs() == [] and s.view().listening == ""


def test_tts_lines_cache_is_reused_and_a_failure_degrades_to_text(tmp_path):
    from analysis.pc.tts_lines import lines_to_speak, prepare_line_audio

    script = {"greet": "Hello, {name}.", "bye": "Bye, {name}.", "sentence": "Today I am sitting by the window.",
              "_comment": "x"}
    assert lines_to_speak(script, "Ona") == {"greet": "Hello, Ona.", "bye": "Bye, Ona."}
    calls, out = [], []

    def synth(text):
        calls.append(text)
        return text.encode() * 2

    audio = prepare_line_audio(script, "Ona", "voice1", tmp_path, synth, out.append)
    assert set(audio) == {"greet", "bye"} and base64.b64decode(audio["greet"]) == b"Hello, Ona." * 2
    assert (tmp_path / "voice1" / "greet.pcm").exists() and len(calls) == 2
    assert prepare_line_audio(script, "Ona", "voice1", tmp_path, synth, out.append) == audio and len(calls) == 2
    prepare_line_audio(script, "Eve", "voice1", tmp_path, synth, out.append)  # another name: spoken again
    assert len(calls) == 4

    def failing(text):
        raise SpeechError("nope", 401, "bad")

    assert prepare_line_audio(script, "Ona", "other_voice", tmp_path, failing, out.append) == {}
    assert any("HTTP 401" in line for line in out)
    huge = prepare_line_audio({"greet": "x"}, "Ona", "v3", tmp_path, lambda t: b"a" * 600_000, out.append)
    assert huge == {} and any("too long" in line for line in out)
