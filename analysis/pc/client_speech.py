"""The client's speech jobs: record, ask the server for a single-use token, transcribe, hand back the result.

`SpeechRunner` is plain synchronous code with everything injected (recorder, transcriber, token request), so tests run
it with a fake microphone and a fake ElevenLabs. client.py runs its jobs in a worker thread.

    listen(question_id, timeout_s)   record (at most timeout_s, stop after 1.5 s of silence) -> transcribe -> an
                                     `answer` result. No speech heard -> transcript None WITHOUT calling ElevenLabs.
    sentence(attempt_id, max_s)      the same for the sentence task -> a `speech_result` result.

A failure (no microphone, no token, speech-to-text error) never raises: the result carries `stt_error` with a short
plain word and the call goes on. The API key is never here: only the single-use token the server hands out.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from analysis.session import limits
from analysis.speech.elevenlabs import SpeechError


class SpeechRunner:
    def __init__(self, record: Callable[..., Any], transcribe: Callable[..., Dict[str, Any]],
                 request_token: Callable[[str, str], Tuple[Optional[str], Optional[str]]], language: str = "en",
                 on_level: Optional[Callable[[float], Any]] = None, on_phase: Optional[Callable[[str], Any]] = None
                 ) -> None:
        """record(max_s, silence_s, start_timeout_s, on_level) -> mic.Recording; transcribe(wav, token, language)
        -> {"text", "words", ...}; request_token(purpose, ref) -> (token, error) (blocks for at most
        limits.STT_TOKEN_WAIT_S)."""
        self._record = record
        self._transcribe = transcribe
        self._request_token = request_token
        self.language = language
        self.on_level = on_level
        self.on_phase = on_phase
        self._tokens = 0
        self.speech_broken = False  # no key on the server or no microphone: the client falls back to typed answers
        self.microphone_problem: Optional[str] = None

    def _phase(self, name: str) -> None:
        if self.on_phase is not None:
            self.on_phase(name)

    def _recognise(self, purpose: str, ref: str, max_s: float, start_timeout_s: float
                   ) -> Tuple[Optional[str], List[Dict[str, Any]], Optional[str], float]:
        """-> (transcript or None, words, stt_error or None, seconds recorded)."""
        from analysis.pc.mic import MicrophoneUnavailable

        self._phase("recording")
        try:
            rec = self._record(max_s, limits.SPEECH_SILENCE_S, start_timeout_s, self.on_level)
        except MicrophoneUnavailable as exc:
            self.microphone_problem = exc.reason
            self.speech_broken = True
            return None, [], "microphone_%s" % exc.reason, 0.0
        if not rec.speech_heard or not rec.wav:
            return None, [], None, float(rec.duration_s)  # nobody spoke: ElevenLabs is not called
        self._phase("transcribing")
        token, error = self._request_token(purpose, ref)
        if not token:
            if error == "speech_not_configured":
                self.speech_broken = True
            return None, [], error or "no_token", float(rec.duration_s)
        try:
            result = self._transcribe(rec.wav, token, self.language)
        except SpeechError as exc:
            return None, [], ("stt_http_%d" % exc.status) if exc.status else "stt_unreachable", float(rec.duration_s)
        except Exception as exc:  # never take the call down
            return None, [], "stt_failed_%s" % type(exc).__name__, float(rec.duration_s)
        text = str(result.get("text") or "").strip()
        return (text or None), list(result.get("words") or []), None, float(rec.duration_s)

    def listen(self, question_id: str, timeout_s: float) -> Dict[str, Any]:
        self._tokens += 1
        transcript, words, error, _ = self._recognise("answer", "ans_%s_%d" % (question_id, self._tokens),
                                                      float(timeout_s), limits.SPEECH_ANSWER_START_S)
        return {"kind": "answer", "question_id": question_id, "transcript": transcript, "words": words,
                "stt_error": error}

    def sentence(self, attempt_id: str, max_s: float) -> Dict[str, Any]:
        self._tokens += 1
        transcript, words, error, seconds = self._recognise("sentence", "sen_%s_%d" % (attempt_id, self._tokens),
                                                            float(max_s), limits.SPEECH_SENTENCE_START_S)
        return {"kind": "speech_result", "attempt_id": attempt_id, "transcript": transcript, "words": words,
                "duration_recorded_s": round(seconds, 3), "stt_error": error}
