"""The client's brain as a PURE state machine: no cv2, no asyncio, no threads, no real clock reads.

It maps what happens (server messages, local measurements, key presses, the passage of time) to
(1) the messages the client must send, collected in an outbox (`pop_outgoing()`), and
(2) what the window shows (`view()`): connection, the line being said, what the camera sees, the timer, the answer
    box and the result card.

Time comes from an injected `clock` (a callable returning seconds); tests pass a fake one.

Capture: on `start_capture` it sends `capture_started` at once and starts a timer. `capture_due()` says when the next
frame must be measured (one per sample period, frames in between are dropped, captured_ms = real elapsed time);
`reading_done()` queues the `reading`. At the end of the window it sends `capture_ended`; on `cancel_capture` it sends
`cancel_ack`. For `arms` the expected hand of each frame comes from the phases the server sent.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from analysis.session import limits

STATUS_WORDS = {
    "as_usual": "Looked as usual",
    "change_noticed": "Something looked different from her usual",
    "concern_raised": "Something needs a look",
    "learning": "Still learning her usual",
    "incomplete": "The check-in could not be finished",
}
ANSWER_PROMPT = "Type your answer, Enter to send, Esc = no answer"
STATUS_PERIOD_S = 1.0
LOCAL_HINT_TTL_S = 2.0
SERVER_HINT_TTL_S = 4.0
MIN_SAY_S = 1.5
WORDS_PER_S = 2.5
SENTENCE_RESULT_GRACE_S = limits.SENTENCE_RESULT_GRACE_S


def wrap_text(text: str, width: int) -> List[str]:
    """Word wrap to at most `width` characters per line (a longer word gets its own line)."""
    lines: List[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = word if not current else current + " " + word
    if current:
        lines.append(current)
    return lines


def say_duration_s(text: str) -> float:
    """Text-only mode: how long a line counts as 'spoken'."""
    return max(MIN_SAY_S, len(text.split()) / WORDS_PER_S)


@dataclass
class CaptureFrame:
    attempt_id: str
    task: str
    captured_ms: int
    sequence: int
    expected_side: Optional[str]
    phase: Optional[str]


@dataclass
class _Capture:
    attempt_id: str
    task: str
    started_at: float
    duration_ms: int  # when the capture ends by itself; for a spoken sentence: recording + transcription budget
    period_ms: int
    phases: List[Tuple[str, int, int, Optional[str]]]
    cue_at_ms: Optional[int]
    next_due_ms: int = 0
    sequence: int = 0
    speech: bool = False  # the spoken sentence: ends with a speech_result, not with the timer


@dataclass
class _Answer:
    question_id: str
    deadline: float
    text: str = ""


@dataclass
class View:
    connection: str
    saying: str
    hint: str
    timer: str
    answer_prompt: Optional[str]
    answer_text: str
    result_title: Optional[str]
    result_text: Optional[str]
    in_call: bool
    listening: str = ""  # "Listening..." / "Working out what you said..." while a spoken answer is recorded
    input_level: float = 0.0  # 0..1, the live microphone level bar
    heard: str = ""  # the transcript of her last spoken answer


FALLBACK_ERRORS = ("speech_not_configured",)  # (and any microphone_* error): the answer goes back to typing
MAX_WORDS_SENT = 200
LISTEN_RESULT_GRACE_S = 18.0  # after the listen window the worker still records the tail and transcribes


class ClientState:
    def __init__(self, person_id: str, clock: Callable[[], float] = time.monotonic, tts: bool = False,
                 auto_start: bool = False, speech: bool = False, play_audio: bool = False) -> None:
        self.person_id = person_id
        self._clock = clock
        self.tts = tts  # True: client.py speaks lines and reports line_finished(); False: text-only timing
        self.auto_start = auto_start
        self.speech = speech  # the client has a microphone and the speech module (protocol v2)
        self.speech_broken = False  # set when speech failed for good (no key on the server, no microphone): typing
        self.play_audio = play_audio  # True: client.py plays `say` audio and reports line_finished()
        self.heard = ""
        self.input_level = 0.0
        self.speech_phase = ""  # "recording" / "transcribing" (written by the speech worker thread)
        self._microphone = "available" if speech else "unavailable"
        self._speech_jobs: List[Tuple[Any, ...]] = []
        self._audio: List[Tuple[str, bytes]] = []
        self._listen_q: Optional[str] = None  # the question being answered by voice
        self._listen_timeout_s = 0.0
        self._listen_deadline = 0.0
        self.connected = False
        self.in_call = False
        self.quit = False
        self.saying = ""
        self.error_text = ""
        self.capture: Optional[_Capture] = None
        self.answer: Optional[_Answer] = None
        self.result_title: Optional[str] = None
        self.result_text: Optional[str] = None
        self._camera = ("unavailable", "camera_lost")
        self._local_hint: Tuple[str, float] = ("", -1e9)
        self._server_hint: Tuple[str, float] = ("", -1e9)
        self._outbox: List[Tuple[str, Dict[str, Any]]] = []
        self._speech: List[Tuple[str, str]] = []
        self._said_due: List[Tuple[float, str]] = []
        self._last_status = -1e9

    # ---- outbox / speech --------------------------------------------------------------------------------

    def _send(self, type_: str, **fields: Any) -> None:
        self._outbox.append((type_, fields))

    def pop_outgoing(self) -> List[Tuple[str, Dict[str, Any]]]:
        out, self._outbox = self._outbox, []
        return out

    def take_speech(self) -> List[Tuple[str, str]]:
        """Lines the client program must speak now (only when tts is on)."""
        out, self._speech = self._speech, []
        return out

    def line_finished(self, line_id: str) -> None:
        """The speaker finished a line: tell the server."""
        self._send("said", line_id=line_id)

    # ---- inputs ---------------------------------------------------------------------------------------------

    def on_connection(self, connected: bool) -> None:
        self.connected = connected
        if connected:
            self.error_text = ""
            speech = self.speech
            self._send("hello", protocol=2 if speech else 1, client="pc", person_id=self.person_id,
                       capabilities={"camera": True, "microphone": speech,
                                     "measures": ["smile", "arms"] + (["sentence"] if speech else []),
                                     "speech": speech})
        else:
            self.in_call = False
            self.capture = None
            self.answer = None
            self._said_due = []
            self._speech_jobs = []
            self._audio = []
            self._listen_q = None

    def set_microphone(self, state: str) -> None:
        """"available" / "unavailable" (reported in the 1 s status)."""
        self._microphone = state

    def set_input_level(self, level: float) -> None:
        self.input_level = max(0.0, min(1.0, float(level)))

    def take_speech_jobs(self) -> List[Tuple[Any, ...]]:
        """Jobs for the speech worker: ("listen", question_id, timeout_s) or ("sentence", attempt_id, max_s)."""
        out, self._speech_jobs = self._speech_jobs, []
        return out

    def take_audio(self) -> List[Tuple[str, bytes]]:
        """Line audio to play now (raw PCM 16 kHz mono int16); report each with line_finished() when it ends."""
        out, self._audio = self._audio, []
        return out

    def _use_speech(self) -> bool:
        return self.speech and not self.speech_broken

    def speech_answer_done(self, result: Dict[str, Any]) -> None:
        """The worker finished a spoken answer."""
        question_id = result["question_id"]
        if self._listen_q != question_id:
            return  # the question is over (hang-up, timeout): a late result is dropped
        self._listen_q = None
        error = result.get("stt_error")
        if error and (error in FALLBACK_ERRORS or str(error).startswith("microphone_")):
            self.speech_broken = True
            self._microphone = "unavailable"
            self.answer = _Answer(question_id, self._clock() + self._listen_timeout_s)  # back to typing
            return
        self.heard = result.get("transcript") or ""
        self._send("answer", question_id=question_id, transcript=result.get("transcript"),
                   words=list(result.get("words") or [])[:MAX_WORDS_SENT], stt_error=error)

    def speech_sentence_done(self, result: Dict[str, Any]) -> None:
        """The worker finished the sentence: send the result, then end the capture."""
        cap = self.capture
        if cap is None or not cap.speech or cap.attempt_id != result["attempt_id"]:
            return
        self._finish_sentence(cap, result.get("transcript"), list(result.get("words") or []),
                              float(result.get("duration_recorded_s") or 0.0), result.get("stt_error"))

    def _finish_sentence(self, cap: _Capture, transcript: Optional[str], words: List[Dict[str, Any]],
                         duration_s: float, error: Optional[str]) -> None:
        self.heard = transcript or ""
        self._send("speech_result", attempt_id=cap.attempt_id, transcript=transcript, words=words[:MAX_WORDS_SENT],
                   duration_recorded_s=duration_s, stt_error=error)
        self._send("capture_ended", attempt_id=cap.attempt_id)
        self.capture = None

    def keepalive(self) -> Dict[str, Any]:
        """The data of a sentence capture's `reading`: it only tells the server the client is recording."""
        return {"phase": self.speech_phase or "recording", "level": round(self.input_level, 3)}

    def set_camera(self, state: str, reason: Optional[str]) -> None:
        self._camera = (state, reason)

    def set_local_hint(self, text: str) -> None:
        self._local_hint = (text, self._clock())

    def on_server_message(self, msg: Dict[str, Any]) -> None:
        kind = msg.get("type")
        now = self._clock()
        if kind == "hello_ok":
            if self.auto_start:
                self.auto_start = False
                self.request_start()
        elif kind == "say":
            self.saying = msg["text"]
            self.heard = ""
            audio = self._decode_audio(msg.get("audio_pcm16k_b64")) if self.play_audio else None
            if audio:
                self._audio.append((msg["line_id"], audio))  # the ElevenLabs voice; `said` follows its playback
            elif self.tts:
                self._speech.append((msg["line_id"], msg["text"]))
            else:
                self._said_due.append((now + say_duration_s(msg["text"]), msg["line_id"]))
        elif kind == "listen":
            timeout_s = float(msg["timeout_s"])
            self.heard = ""
            if self._use_speech():
                self._listen_q = msg["question_id"]
                self._listen_timeout_s = timeout_s
                self._listen_deadline = now + timeout_s + LISTEN_RESULT_GRACE_S  # a stuck worker must not hang the call
                self.speech_phase = "recording"
                self._speech_jobs.append(("listen", msg["question_id"], timeout_s))
                self.answer = None
            else:
                self.answer = _Answer(msg["question_id"], now + timeout_s)
        elif kind == "start_capture":
            self._start_capture(msg, now)
        elif kind == "cancel_capture":
            if self.capture is not None and self.capture.attempt_id == msg["attempt_id"]:
                self.capture = None
            self._send("cancel_ack", attempt_id=msg["attempt_id"])
        elif kind == "stt_token":
            pass  # delivered to the speech worker by client.py
        elif kind == "progress":
            self._server_hint = (msg["text"], now)
        elif kind == "call_result":
            summary = msg.get("summary") or {}
            self.in_call = False
            self.capture = None
            self.answer = None
            self.result_title = STATUS_WORDS.get(summary.get("status"), "The check-in is over")
            self.result_text = msg["family_text"]
        elif kind == "error":
            self.error_text = "%s: %s" % (msg["code"], msg["text"])
            if msg["code"] == "call_in_progress":
                self.error_text = ""

    @staticmethod
    def _decode_audio(b64: Any) -> Optional[bytes]:
        if not isinstance(b64, str) or not b64:
            return None
        try:
            return base64.b64decode(b64, validate=True)
        except (ValueError, TypeError):
            return None

    def _start_capture(self, msg: Dict[str, Any], now: float) -> None:
        settings = msg["settings"]
        phases = [(p[0], int(p[1]), int(p[2]), p[3]) for p in msg.get("phases") or []]
        cues = msg.get("cues") or []
        duration_ms = int(settings.get("duration_ms", 5000))
        spoken = msg["task"] == "sentence" and self._use_speech()
        if spoken:  # record up to duration_ms, then transcribe: the capture ends with the speech_result
            self.speech_phase = "recording"
            self._speech_jobs.append(("sentence", msg["attempt_id"], duration_ms / 1000.0))
            duration_ms += int(SENTENCE_RESULT_GRACE_S * 1000)
        self.capture = _Capture(msg["attempt_id"], msg["task"], now, duration_ms,
                                int(settings.get("sample_period_ms", 200)), phases,
                                int(cues[0]["at_ms"]) if cues else None, speech=spoken)
        self._send("capture_started", attempt_id=msg["attempt_id"])

    # ---- capture ------------------------------------------------------------------------------------------------

    def capture_elapsed_ms(self) -> Optional[int]:
        return None if self.capture is None else int((self._clock() - self.capture.started_at) * 1000)

    @staticmethod
    def _phase_at(cap: _Capture, ms: int) -> Tuple[Optional[str], Optional[str]]:
        for name, start, end, side in cap.phases:
            if start <= ms < end:
                return name, side
        return None, None

    def capture_due(self) -> Optional[CaptureFrame]:
        """The frame to measure now, or None. Frames in between are dropped, not queued."""
        cap = self.capture
        if cap is None:
            return None
        ms = int((self._clock() - cap.started_at) * 1000)
        if ms >= cap.duration_ms or ms < cap.next_due_ms:
            return None
        cap.next_due_ms = ms + cap.period_ms
        phase, side = self._phase_at(cap, ms)
        return CaptureFrame(cap.attempt_id, cap.task, ms, cap.sequence, side, phase)

    def reading_done(self, frame: CaptureFrame, data: Optional[Dict[str, Any]]) -> None:
        cap = self.capture
        if cap is None or cap.attempt_id != frame.attempt_id:
            return  # cancelled while measuring
        self._send("reading", attempt_id=frame.attempt_id, sequence=frame.sequence, captured_ms=frame.captured_ms,
                   task=frame.task, data=data)
        cap.sequence += 1

    # ---- keys -------------------------------------------------------------------------------------------------------

    def request_start(self) -> None:
        if not self.connected or self.in_call:
            return
        self.in_call = True
        self.result_title = self.result_text = None
        self.saying = ""
        self.error_text = ""
        self._send("start_call")

    def request_hang_up_or_quit(self) -> None:
        if self.in_call:
            self._send("hang_up")
            self.in_call = False
            self.capture = None
            self.answer = None
            self._said_due = []
            self._listen_q = None
            self._speech_jobs = []
        else:
            self.quit = True

    def key_char(self, ch: str) -> bool:
        """A typed character. True when it went into the answer box."""
        if self.answer is None:
            return False
        if len(self.answer.text) < 200:
            self.answer.text += ch
        return True

    def key_backspace(self) -> bool:
        if self.answer is None:
            return False
        self.answer.text = self.answer.text[:-1]
        return True

    def key_enter(self) -> bool:
        if self.answer is None:
            return False
        text = self.answer.text.strip()
        self._send("answer", question_id=self.answer.question_id, transcript=text or None)
        self.answer = None
        return True

    def key_escape(self) -> bool:
        if self.answer is None:
            return False
        self._send("answer", question_id=self.answer.question_id, transcript=None)
        self.answer = None
        return True

    # ---- time -------------------------------------------------------------------------------------------------------

    def tick(self) -> None:
        now = self._clock()
        due = [x for x in self._said_due if x[0] <= now]
        if due:
            self._said_due = [x for x in self._said_due if x[0] > now]
            for _, line_id in due:
                self._send("said", line_id=line_id)
        if self.answer is not None and now >= self.answer.deadline:
            self._send("answer", question_id=self.answer.question_id, transcript=None)
            self.answer = None
        if self._listen_q is not None and now >= self._listen_deadline:  # the worker is stuck: the call goes on
            self._send("answer", question_id=self._listen_q, transcript=None, stt_error="speech_timeout")
            self._listen_q = None
        cap = self.capture
        if cap is not None and (now - cap.started_at) * 1000 >= cap.duration_ms:
            if cap.speech:  # no result in time: report it, so the server never waits for ever
                self._finish_sentence(cap, None, [], 0.0, "speech_timeout")
            else:
                self._send("capture_ended", attempt_id=cap.attempt_id)
                self.capture = None
        if self.connected and now - self._last_status >= STATUS_PERIOD_S:
            self._last_status = now
            state, reason = self._camera
            self._send("status", camera=state, camera_reason=reason, microphone=self._microphone,
                       text=self._hint(now) or "waiting")

    # ---- what the window shows ----------------------------------------------------------------------------------------

    def _hint(self, now: float) -> str:
        text, at = self._local_hint
        if text and now - at <= LOCAL_HINT_TTL_S:
            return text
        text, at = self._server_hint
        if text and now - at <= SERVER_HINT_TTL_S:
            return text
        return ""

    def _timer(self, now: float) -> str:
        cap = self.capture
        if cap is None or cap.speech:
            return ""
        ms = int((now - cap.started_at) * 1000)
        text = "%d s left" % max(0, int((cap.duration_ms - ms + 999) // 1000))
        if cap.cue_at_ms is not None and ms < cap.cue_at_ms:
            text += "  |  swap in %d s" % ((cap.cue_at_ms - ms + 999) // 1000)
        return text

    def view(self) -> View:
        now = self._clock()
        if not self.connected:
            connection = "NOT CONNECTED - trying again"
        elif self.in_call:
            connection = "CONNECTED - in a call"
        else:
            connection = "CONNECTED - press SPACE to start a call"
        saying = self.saying or self.error_text
        listening = ""
        if self._listen_q is not None or (self.capture is not None and self.capture.speech):
            listening = "Working out what you said..." if self.speech_phase == "transcribing" else "Listening..."
        return View(connection, saying, self._hint(now) or ("" if self.capture is None else "going fine"),
                    self._timer(now), ANSWER_PROMPT if self.answer is not None else None,
                    self.answer.text if self.answer is not None else "", self.result_title, self.result_text,
                    self.in_call, listening, self.input_level if listening else 0.0, self.heard)
