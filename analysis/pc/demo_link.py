"""Offline demo of the PC version: the server logic and a scripted fake client in ONE process over MemoryTransport.

    python -m analysis.pc.demo_link                       # fixed-order driver
    python -m analysis.pc.demo_link --driver fake-claude  # a scripted fake Claude that sees the numbers
    python -m analysis.pc.demo_link --speech              # v8: spoken answers + the sentence task, fake ElevenLabs

No camera, no mediapipe, no network, no real Claude: a FakeClock, fake readings and temp folders for the baseline store
and the call records. Prints every link message in one line, then each call's summary and family text. Exit code 0
when both calls ended as expected, else 1.

`ScriptedClient` (the fake client) and `DemoAgent` (the fake Claude) are also used by the tests.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from analysis.face_metric import FrameReading
from analysis.hand_metric import HandFrameReading
from analysis.pc import link
from analysis.pc.remote_tasks import hand_to_dict, smile_to_dict
from analysis.pc.server import PcServer
from analysis.pc.store import BaselineStore
from analysis.session.claude_client import FakeClaudeClient, tool_use_response
from analysis.session.clock import FakeClock
from analysis.speech import elevenlabs
from analysis.tasks.stream_arms import hold_windows, phase_at

ANSWERS = {"q_medicine": "Yes, after breakfast.", "q_feeling": "I feel fine, thank you.",
           "q_unusual": "Nothing unusual."}
NUMB_ANSWERS = dict(ANSWERS, q_feeling="I feel numb on one side.")


def seed_store(store: BaselineStore, count: int = 6, speech: bool = False) -> None:
    """Made-up usual values so the demo calls compare against a ready baseline. speech=True adds how long she
    usually takes for the sentence (about 3 s)."""
    for i in range(count):
        values = {"corner_height_v1": 0.005 + 0.002 * (i % 3), "arms.hand_left.hand_sink": 0.0,
                  "arms.hand_right.hand_sink": 0.0}
        if speech:
            values["sentence.duration_s"] = 3.0 + 0.1 * (i % 3)
        store.append("live", values)


# ---- fake ElevenLabs (no network): the server mints tokens, the scripted client transcribes ---------------------

SENTENCE = "Today I am sitting by the window."


def timed_words(text: str, word_s: float = 0.3, gap_s: float = 0.1, pause_before: Optional[int] = None,
                pause_s: float = 0.0) -> List[Dict[str, Any]]:
    """ElevenLabs-shaped words for `text`: each word word_s long, gap_s apart, plus one longer pause before word
    number `pause_before` (0-based)."""
    out: List[Dict[str, Any]] = []
    t = 0.0
    for i, word in enumerate(text.split()):
        if pause_before == i:
            t += pause_s
        out.append({"text": word, "type": "word", "start": round(t, 3), "end": round(t + word_s, 3), "logprob": -0.1})
        t += word_s + gap_s
    return out


class FakeElevenLabs:
    """Stands in for the ElevenLabs service: `server_http` is for the server (token mint, needs the key header),
    `stt_http(words)` for the client (speech to text, must carry a token in the query and NO key header)."""

    def __init__(self) -> None:
        self.minted = 0
        self.stt_calls: List[Dict[str, Any]] = []
        self.violations: List[str] = []  # anything that would break the key/token rules

    def server_http(self, method: str, url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float):
        if not url.endswith("/v1/single-use-token/batch_scribe") or "xi-api-key" not in headers:
            self.violations.append("bad token request: %s" % url)
        self.minted += 1
        return 200, {}, json.dumps({"token": "sutkn_demo_%d" % self.minted}).encode()

    def stt_http(self, text: str, words: List[Dict[str, Any]]):
        def http(method: str, url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float):
            if "token=sutkn_demo_" not in url:
                self.violations.append("speech-to-text without a token: %s" % url)
            if any(k.lower() == "xi-api-key" for k in headers):
                self.violations.append("the API key was sent by the client")
            self.stt_calls.append({"url": url, "bytes": len(body or b"")})
            return 200, {}, json.dumps({"language_code": "en", "text": text, "words": words}).encode()

        return http


# ---- one line per link message -------------------------------------------------------------------------

_KEYS = ("line_id", "question_id", "attempt_id", "task", "reason", "code", "camera", "camera_reason", "text",
         "timeout_s", "transcript", "protocol", "sequence", "captured_ms", "purpose", "ref", "error", "stt_error",
         "duration_recorded_s")


def format_message(direction: str, message: Dict[str, Any]) -> str:
    kind = message.get("type")
    fields = []
    for key in _KEYS:
        if key in message and key != "type":
            value = message[key]
            fields.append("%s=%s" % (key, (value[:40] + "...") if isinstance(value, str) and len(value) > 40 else value))
    if kind == "reading":
        data = message.get("data")
        fields.append("data=%s" % ("null" if data is None else ("usable" if data.get("usable") else "unusable")
                                   if "usable" in data else data.get("phase")))
    if kind == "speech_result":
        fields.append("words=%d" % len(message.get("words") or []))
    if kind == "answer" and message.get("words") is not None:
        fields.append("words=%d" % len(message["words"]))
    if kind == "say" and message.get("audio_pcm16k_b64"):
        fields.append("audio=%d chars" % len(message["audio_pcm16k_b64"]))
    if kind == "call_result":
        fields.append("status=%s" % message["summary"]["status"])
    return "%s %-15s %s" % (direction, kind, " ".join(fields))


# ---- the fake client -----------------------------------------------------------------------------------

class ScriptedClient:
    """Plays the client's part: answers `say`/`listen`, streams fake readings for each capture, and can block the
    camera during the first arms attempt. Time moves only through the FakeClock, which this loop advances."""

    def __init__(self, transport: link.Transport, clock: FakeClock, show: Optional[Callable[[str], Any]] = None,
                 max_time_s: float = 2000.0, speech: bool = False, eleven: Optional[FakeElevenLabs] = None) -> None:
        self.endpoint = link.Endpoint(transport)
        self.clock = clock
        self.show = show
        self.max_time_s = max_time_s
        self.speech = speech  # v8: a microphone; answers and the sentence go through the fake ElevenLabs
        self.eleven = eleven or FakeElevenLabs()
        self.capture: Optional[Dict[str, Any]] = None
        self._token_for: Dict[str, Dict[str, Any]] = {}  # ref -> what to transcribe when the token arrives
        self._answers: Dict[str, str] = {}
        self._sentences: Sequence[Dict[str, Any]] = ({},)  # per sentence attempt: pause_before / pause_s
        self.token_errors: List[str] = []
        self.said_words: List[List[str]] = []
        self.sent: List[Dict[str, Any]] = []
        self.received: List[Dict[str, Any]] = []
        self.results: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self._pending: List["asyncio.Future"] = []  # the one outstanding recv()
        self._restore_at: Optional[float] = None  # fake time when the covered camera is uncovered again

    async def send(self, type_: str, **fields: Any) -> None:
        message = await self.endpoint.send(type_, **fields)
        self.sent.append(message)
        if self.show is not None and not (type_ == "reading" and message["sequence"] % 10 != 0):
            self.show(format_message("client ->", message))

    async def _next(self, pending: List["asyncio.Future"]) -> Optional[Dict[str, Any]]:
        """The next server message, advancing fake time while nothing arrives."""
        while True:
            await self.clock.settle()
            if pending[0].done():
                message = pending[0].result()
                pending[0] = asyncio.ensure_future(self.endpoint.recv())
                return message
            assert self.clock.now() < self.max_time_s, "the scripted call did not finish in time"
            if self._restore_at is not None and self.clock.now() >= self._restore_at:
                self._restore_at = None
                await self.send("status", camera="available", camera_reason=None, microphone="available",
                                text="going fine")
            await self.clock.advance(0.1)

    async def connect(self, camera: str = "available", microphone: str = "available") -> None:
        speech = self.speech
        await self.send("hello", protocol=2 if speech else 1, client="pc", person_id="demo",
                        capabilities={"camera": True, "microphone": speech,
                                      "measures": ["smile", "arms"] + (["sentence"] if speech else []),
                                      "speech": speech})
        await self.send("status", camera=camera, camera_reason=None, microphone=microphone, text="going fine")

    async def run_call(self, answers: Optional[Dict[str, str]] = None, corners: Sequence[float] = (0.01,),
                       sinks: Sequence[Dict[str, float]] = ({},), block_camera_in_first_arms: bool = False,
                       restore_after_s: float = 2.0, sentences: Sequence[Dict[str, Any]] = ({},)
                       ) -> Dict[str, Any]:
        """One whole call from start_call to call_result. Returns the call_result message."""
        answers = ANSWERS if answers is None else answers
        pending = self._pending
        if not pending:
            pending.append(asyncio.ensure_future(self.endpoint.recv()))
        await self.send("start_call")
        attempts: Dict[str, int] = {}
        self.capture = None
        self._answers = answers
        self._sentences = sentences
        blocked_once = False
        while True:
            capture = self.capture
            if capture is not None:
                await self._stream_one(capture)
                if capture["task"] == "sentence":
                    pass  # ends with the speech_result (see _on_token)
                elif capture["i"] >= capture["n"]:
                    await self.send("capture_ended", attempt_id=capture["attempt_id"])
                    self.capture = None
                elif (block_camera_in_first_arms and not blocked_once and capture["task"] == "arms"
                      and capture["i"] * 200 >= 4000):
                    blocked_once = True
                    self._restore_at = self.clock.now() + restore_after_s
                    await self.send("status", camera="unavailable", camera_reason="camera_blocked",
                                    microphone="available", text="camera blocked")
                    self.capture = None  # a covered lens: no more readings
                await self.clock.advance(0.2)
                message = await self._poll(pending)
            else:
                message = await self._next(pending)
            if message is None:
                continue
            self.received.append(message)
            if self.show is not None:
                self.show(format_message("<- server", message))
            kind = message["type"]
            if kind == "say":
                await self.send("said", line_id=message["line_id"])
            elif kind == "listen":
                qid = message["question_id"]
                if self.speech:  # speak: ask for a token, then transcribe with it (fake ElevenLabs)
                    ref = "ans_%s_%d" % (qid, len(self._token_for))
                    self._token_for[ref] = {"purpose": "answer", "question_id": qid}
                    await self.send("stt_token_request", purpose="answer", ref=ref)
                else:
                    await self.send("answer", question_id=qid, transcript=answers.get(qid))
            elif kind == "stt_token":
                await self._on_token(message)
            elif kind == "start_capture":
                task = message["task"]
                n_before = attempts.get(task, 0)
                attempts[task] = n_before + 1
                settings = message["settings"]
                self.capture = {"attempt_id": message["attempt_id"], "task": task, "i": 0,
                                "n": settings["duration_ms"] // settings["sample_period_ms"] + 1,
                                "period": settings["sample_period_ms"],
                                "phases": [tuple(p) for p in message.get("phases") or []],
                                "corner": corners[min(n_before, len(corners) - 1)],
                                "sink": dict(sinks[min(n_before, len(sinks) - 1)]),
                                "sentence": dict(sentences[min(n_before, len(sentences) - 1)]),
                                "asked": False, "phase": "recording"}
                await self.send("capture_started", attempt_id=message["attempt_id"])
            elif kind == "cancel_capture":
                self.capture = None
                await self.send("cancel_ack", attempt_id=message["attempt_id"])
            elif kind == "error":
                self.errors.append(message)
            elif kind == "call_result":
                self.results.append(message)
                return message

    async def _on_token(self, message: Dict[str, Any]) -> None:
        """A single-use token arrived: transcribe with the fake ElevenLabs and send the answer / speech result."""
        job = self._token_for.get(message["ref"])
        if job is None:
            return
        if not message.get("token"):
            self.token_errors.append(str(message.get("error")))
            if job["purpose"] == "answer":
                await self.send("answer", question_id=job["question_id"], transcript=None, words=[],
                                stt_error=message.get("error"))
            return
        if job["purpose"] == "answer":
            text = self._answers.get(job["question_id"])
            words = timed_words(text) if text else []
            result = elevenlabs.transcribe(b"RIFF....fake wav", message["token"], "en",
                                           self.eleven.stt_http(text or "", words))
            await self.send("answer", question_id=job["question_id"], transcript=result["text"] or None,
                            words=result["words"], stt_error=None)
            return
        cap = self.capture  # the sentence
        if cap is None or cap["task"] != "sentence":
            return
        cfg = cap["sentence"]
        words = timed_words(SENTENCE, pause_before=cfg.get("pause_before"), pause_s=cfg.get("pause_s", 0.0))
        result = elevenlabs.transcribe(b"RIFF....fake wav", message["token"], "en", self.eleven.stt_http(SENTENCE, words))
        self.said_words.append([w["text"] for w in result["words"]])
        await self.send("speech_result", attempt_id=cap["attempt_id"], transcript=result["text"],
                        words=result["words"], duration_recorded_s=round(words[-1]["end"] + 1.5 + 0.5, 3),
                        stt_error=None)
        await self.send("capture_ended", attempt_id=cap["attempt_id"])
        self.capture = None

    async def _poll(self, pending: List["asyncio.Future"]) -> Optional[Dict[str, Any]]:
        await self.clock.settle()
        if pending[0].done():
            message = pending[0].result()
            pending[0] = asyncio.ensure_future(self.endpoint.recv())
            return message
        return None

    async def _stream_one(self, c: Dict[str, Any]) -> None:
        ms = c["i"] * c["period"]
        data: Optional[Dict[str, Any]]
        if c["task"] == "sentence":  # keep-alive while "recording", then ask for a token after about 4 s of speech
            if c["i"] * c["period"] >= 4000 and not c["asked"]:
                c["asked"], c["phase"] = True, "transcribing"
                ref = "sen_%s" % c["attempt_id"]
                self._token_for[ref] = {"purpose": "sentence"}
                await self.send("stt_token_request", purpose="sentence", ref=ref)
            data = {"phase": c["phase"], "level": 0.2}
        elif c["task"] == "smile":
            smiling = ms >= 1500
            h = c["corner"] if smiling else 0.0
            data = smile_to_dict(FrameReading(usable=True, unit=100.0, below_left=1.0 + h / 2, below_right=1.0 - h / 2,
                                              corner_height=h, smile_strength=0.9 if smiling else 0.1,
                                              timestamp_ms=ms))
        else:
            phase = phase_at(ms, c["phases"]) if c["phases"] else None
            side = phase[3] if phase else None
            if side is None:
                data = None  # settle / swap: the client does not measure
            else:
                start, end = hold_windows(c["phases"])[side]
                height = 0.5 + (c["sink"].get(side, 0.0) if ms >= (start + end) / 2 else 0.0)
                data = hand_to_dict(HandFrameReading(usable=True, face_ok=True, unit=100.0, height=height,
                                                     palm_width_ratio=1.0, timestamp_ms=ms))
        await self.send("reading", attempt_id=c["attempt_id"], sequence=c["i"], captured_ms=ms, task=c["task"],
                        data=data)
        c["i"] += 1

    async def close(self) -> None:
        for fut in self._pending:
            fut.cancel()
        await asyncio.gather(*self._pending, return_exceptions=True)
        await self.endpoint.close()


# ---- the fake Claude -------------------------------------------------------------------------------------------

class DemoAgent:
    """A scripted 'Claude' that makes the normal choices from what the tool results say. It sees the numbers,
    repeats a task when `needs_another_reading`, and calls alert_family once when she says she feels numb."""

    def __init__(self, speech: bool = False) -> None:
        self.speech = speech  # also run the sentence task (v8)
        self.queue: List[Callable[[], Dict[str, Any]]] = []
        self.started = False
        self.transcripts: Dict[str, str] = {}
        self.seen_numbers: List[Dict[str, Any]] = []
        self.alerted = False

    @staticmethod
    def _last_result(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if len(messages) < 2 or not isinstance(messages[-1]["content"], list):
            return None
        block = messages[-1]["content"][0]
        names = {b["id"]: b["name"] for b in messages[-2]["content"] if b["type"] == "tool_use"}
        payload = json.loads(block["content"])
        payload["_tool"] = names.get(block["tool_use_id"])
        return payload

    def __call__(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        last = self._last_result(messages)
        if not self.started:
            self.started = True
            self.queue = [lambda: tool_use_response("say", {"line_id": "greet"})]
            for q in ("q_medicine", "q_feeling", "q_unusual"):
                self.queue.append(lambda q=q: tool_use_response("ask", {"question_id": q}))
                self.queue.append(lambda q=q: tool_use_response("record_answer", {"question_id": q,
                                                                                  "value": self._value(q)}))
            self.queue.append(lambda: tool_use_response("run_task", {"task": "smile"}))
            self.queue.append(lambda: tool_use_response("run_task", {"task": "arms"}))
            if self.speech:
                self.queue.append(lambda: tool_use_response("run_task", {"task": "sentence"}))
            self.queue.append(lambda: tool_use_response("finish_call", {}))
        elif last is not None:
            self._react(last)
        return self.queue.pop(0)()

    def _value(self, q: str) -> str:
        text = self.transcripts.get(q, "")
        return ("yes" if text else "unclear") if q == "q_medicine" else text[:100]

    def _react(self, last: Dict[str, Any]) -> None:
        tool = last.get("_tool")
        if tool == "ask":
            self.transcripts[last["question_id"]] = last.get("transcript", "")
            if last["question_id"] == "q_feeling" and "numb" in last.get("transcript", "").lower() and not self.alerted:
                self.alerted = True
                self.queue.insert(0, lambda: tool_use_response(
                    "alert_family", {"reason": "said_unwell", "note": "She said she feels numb on one side."}))
        elif tool == "run_task":
            if last.get("reading"):
                self.seen_numbers.append({"task": last["task"], **last["reading"]})
            if last.get("needs_another_reading") and not last.get("skip") and last.get("attempts_left", 0) > 0:
                self.queue.insert(0, lambda t=last["task"]: tool_use_response("run_task", {"task": t}))


# ---- the demo ----------------------------------------------------------------------------------------------------

async def run_demo(driver: str, out: Callable[[str], Any] = print, speech: bool = False) -> int:
    clock = FakeClock()
    agents: List[DemoAgent] = []
    eleven = FakeElevenLabs()

    def new_client() -> FakeClaudeClient:
        agents.append(DemoAgent(speech))  # a fresh fake Claude per call
        return FakeClaudeClient(agents[-1], clock)

    with tempfile.TemporaryDirectory(prefix="uw_demo_") as tmp:
        data_dir, runs_dir = Path(tmp) / "data", Path(tmp) / "runs"
        extra: Dict[str, Any] = {}
        if speech:  # fake ElevenLabs, a made-up key that exists only in this dict (never in the real environment)
            extra = {"http": eleven.server_http, "environ": {elevenlabs.KEY_ENV: "demo-key-not-real"},
                     "inline_blocking": True}
        server = PcServer("demo", "Ona", "api" if driver == "fake-claude" else "off", True, "live", data_dir,
                          runs_dir, clock, out, client_factory=new_client, **extra)
        seed_store(server.store, speech=speech)
        server_side, client_side = link.MemoryTransport.pair()
        server_task = asyncio.ensure_future(server.handle_connection(server_side))
        client = ScriptedClient(client_side, clock, out, speech=speech, eleven=eleven)
        await client.connect()
        out("\n##### Call 1: a normal check-in%s" % (" (she says she feels numb on one side)"
                                                   if driver == "fake-claude" else ""))
        first = await client.run_call(NUMB_ANSWERS if driver == "fake-claude" else ANSWERS)
        if speech:
            out("\n##### Call 2: she pauses for a long time in the middle of the sentence, both times")
            second = await client.run_call(ANSWERS, sentences=({"pause_before": 3, "pause_s": 2.5},))
        else:
            out("\n##### Call 2: her left hand sinks in both arms attempts")
            second = await client.run_call(ANSWERS, sinks=({"left": 0.4},))
        await client.close()
        await asyncio.gather(server_task, return_exceptions=True)

        for number, result in ((1, first), (2, second)):
            out("\n== call %d result: %s" % (number, result["summary"]["status"]))
            out(json.dumps(result["summary"], indent=2))
            out("family text: %s" % result["family_text"])
        if driver == "fake-claude":
            out("\n== numbers the fake Claude saw: %s" % json.dumps([n for a in agents for n in a.seen_numbers]))
        expected = ("concern_raised" if driver == "fake-claude" else "as_usual", "change_noticed")
        got = (first["summary"]["status"], second["summary"]["status"])
        ok = got == expected
        if speech:
            sentence_signs = [s for r in (first, second) for s in r["summary"]["signs"] if s["sign"] == "speech"]
            out("\nspeech: %d tokens minted, %d speech-to-text requests, key/token violations: %s; sentence readings: %s"
                % (eleven.minted, len(eleven.stt_calls), eleven.violations or "none",
                   [[a["value"] for a in s["attempts"]] for s in sentence_signs]))
            ok = ok and not eleven.violations and len(sentence_signs) == 2 and eleven.minted == len(eleven.stt_calls)
        out("\nexpected statuses %s, got %s -> %s" % (expected, got, "OK" if ok else "MISMATCH"))
        return 0 if ok else 1


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Offline demo of the PC version (server + scripted client).")
    p.add_argument("--driver", choices=("fixed", "fake-claude"), default="fixed")
    p.add_argument("--speech", action="store_true",
                   help="spoken answers and the sentence task through a fake ElevenLabs (no network)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return asyncio.run(run_demo(args.driver, speech=args.speech))


if __name__ == "__main__":
    sys.exit(main())
