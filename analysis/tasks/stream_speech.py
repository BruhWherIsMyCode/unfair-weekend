"""The sentence task for the PC version, on a speech result from the client (v8).

The client records her saying the sentence, sends it to ElevenLabs speech-to-text itself (with a single-use token from
the server) and sends only the transcript and the word timings to the server, never audio. The attempt's samples are:

    format "reading"  a keep-alive from the client while it records and transcribes: {"phase", "level"}. They keep the
                      session's sample and analysis watchdogs satisfied and give about one observation per second.
    format "speech"   the ONE result: {"transcript", "words", "duration_recorded_s", "stt_error"}. It ends the task.

The code turns the word timings into numbers (speech/sentence_metric.py). The primary number `duration_s` (metric
speech_duration_v1, unit s) is compared with her own usual: outside = longer than her usual high only. The other
numbers (word_error_rate, pauses, ...) are kept per attempt for the call agent (`attempt_details`), not for the rule.

Quality: unusable with "speech_to_text_failed" on a speech-to-text error, "nothing_heard" without words.
The in-process stub (stream_sentence.py) stays for the old tests.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from analysis.session.models import Metric, Quality, Reading, TaskContext, TaskObservation, TaskResult
from analysis.speech import sentence_metric
from analysis.tasks import stream_base

METRIC_ID = sentence_metric.METRIC_ID
UNIT = sentence_metric.UNIT
OBSERVATION_PERIOD_MS = 1000
EXPECTED_LINE = "sentence"  # contract/script.en.json: the sentence she is asked to repeat

_FACT_KEYS = ("heard", "word_error_rate", "missing_words", "changed_words", "extra_words", "longest_pause_s",
              "words_per_min")


class StreamSpeechSentence:
    name = "sentence"

    def __init__(self, expected_text: Optional[str] = None) -> None:
        if expected_text is None:
            from analysis.session.agent_tools import load_script

            expected_text = load_script()[EXPECTED_LINE]
        self.expected_text = expected_text
        self._details: Dict[str, Dict[str, Any]] = {}  # attempt_id -> the numbers for the call agent

    def definition(self) -> Dict[str, Any]:
        return stream_base.build_definition("sentence", METRIC_ID, UNIT)

    def attempt_details(self, attempt_id: str) -> Optional[Dict[str, Any]]:
        """The speech numbers of a usable attempt (what Claude sees next to the reading), else None."""
        d = self._details.get(attempt_id)
        return dict(d) if d is not None else None

    def _result(self, context: TaskContext, outcome: str, quality: Quality, performance: str,
                reading: Optional[Reading] = None) -> TaskResult:
        return TaskResult("sentence", context.attempt_id, outcome, quality, performance,
                          stream_base.baseline_info(context.baseline), reading, None)

    def final_result(self, context: TaskContext, speech: Optional[Dict[str, Any]]) -> TaskResult:
        if speech is None:
            return self._result(context, "completed", Quality("unusable", ["speech_result_missing"]), "uncertain")
        if speech.get("stt_error"):
            return self._result(context, "completed", Quality("unusable", ["speech_to_text_failed"]), "uncertain")
        measured = sentence_metric.measure_sentence(self.expected_text, speech.get("words"), speech.get("transcript"))
        if not measured["usable"]:
            return self._result(context, "completed", Quality("unusable", [measured["reason"]]), "uncertain")
        readings = measured["readings"]
        value = float(readings["duration_s"])
        cmp = sentence_metric.compare_duration(value, context.baseline)
        reading = Reading(METRIC_ID, UNIT, value, cmp["usual_low"], cmp["usual_high"], cmp["outside"], None)
        self._details[context.attempt_id] = {k: readings[k] for k in _FACT_KEYS}
        return self._result(context, "completed", Quality("usable", []), "observed", reading)

    async def run(self, context: TaskContext, samples: AsyncIterator[Dict[str, Any]],
                  emit_observation: Callable[[Any], Any]) -> TaskResult:
        sequence = 0
        next_emit_ms = 0
        elapsed = 0
        speech: Optional[Dict[str, Any]] = None

        def observe(ms: int, quality: Quality, metrics: List[Metric], phase: str = "capturing") -> None:
            nonlocal sequence
            emit_observation(TaskObservation(
                "obs_%s_%d" % (context.attempt_id, sequence), context.call_id, context.attempt_id, "sentence",
                sequence, max(0, min(int(ms), 15000)), phase, quality, metrics, "none"))
            sequence += 1

        async for sample in samples:
            if context.cancel_event.is_set():
                return self._result(context, "cancelled", Quality("unusable", ["cancelled"]), "uncertain")
            ms = int(sample["captured_ms"])
            elapsed = max(elapsed, ms)
            if sample.get("format") == "speech":
                data = sample.get("data")
                speech = data if isinstance(data, dict) else {"stt_error": "malformed_result"}
                break
            if ms >= next_emit_ms:  # a keep-alive from the client: she is being recorded / transcribed
                next_emit_ms = (ms // OBSERVATION_PERIOD_MS + 1) * OBSERVATION_PERIOD_MS
                observe(ms, Quality("pending", []), [])
        observe(elapsed, Quality("pending", []), [], phase="finalizing")
        return self.final_result(context, speech)
