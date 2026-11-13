"""Sentence measuring (pure) and the remote sentence task module."""
from __future__ import annotations

import asyncio

from analysis.baseline import make_baseline
from analysis.session import limits
from analysis.session.models import TaskContext
from analysis.speech.sentence_metric import compare_duration, measure_sentence, normalise
from analysis.tasks.stream_speech import StreamSpeechSentence

EXPECTED = "Today I am sitting by the window."


def words(text, start=0.0, word_s=0.3, gap_s=0.1, pause_before=None, pause_s=0.0):
    out, t = [], start
    for i, w in enumerate(text.split()):
        if pause_before == i:
            t += pause_s
        out.append({"text": w, "start": t, "end": t + word_s})
        t += word_s + gap_s
    return out


def test_normalise_lowercases_and_strips_punctuation():
    assert normalise("Today, I am SITTING by-the window!") == ["today", "i", "am", "sitting", "bythe", "window"]


def test_the_exact_sentence_has_no_errors():
    m = measure_sentence(EXPECTED, words(EXPECTED), EXPECTED)
    r = m["readings"]
    assert m["usable"] and r["word_error_rate"] == 0.0
    assert r["missing_words"] == r["extra_words"] == r["changed_words"] == []
    assert r["duration_s"] == 2.7 and r["longest_pause_s"] == 0.1 and r["words_per_min"] == 155.6
    assert r["heard"] == EXPECTED


def test_missing_changed_and_extra_words():
    m = measure_sentence(EXPECTED, words("Today I am sitting by window"))["readings"]
    assert m["missing_words"] == ["the"] and m["word_error_rate"] == round(1 / 7, 3)
    m = measure_sentence(EXPECTED, words("Today I am sitting by the widow"))["readings"]
    assert m["changed_words"] == [{"expected": "window", "heard": "widow"}] and m["word_error_rate"] == round(1 / 7, 3)
    m = measure_sentence(EXPECTED, words("Today I am really sitting by the window"))["readings"]
    assert m["extra_words"] == ["really"] and m["word_error_rate"] == round(1 / 7, 3)
    m = measure_sentence(EXPECTED, words("hello there"))["readings"]
    assert m["word_error_rate"] > 0.9 and len(m["changed_words"]) == 2 and len(m["missing_words"]) == 5


def test_lists_are_cut_at_ten_and_heard_at_200_chars():
    long = " ".join("word%d" % i for i in range(40))
    m = measure_sentence(EXPECTED, words(long), long * 3)["readings"]
    assert len(m["extra_words"]) <= 10 and len(m["changed_words"]) <= 10 and len(m["heard"]) == 200


def test_pauses_duration_and_rate():
    m = measure_sentence(EXPECTED, words(EXPECTED, start=1.0, pause_before=3, pause_s=2.0))["readings"]
    assert m["longest_pause_s"] == 2.1 and m["duration_s"] == 4.7  # the first word starts at 1.0: not counted
    one = measure_sentence(EXPECTED, [{"text": "Today", "start": 1.0, "end": 1.5}])["readings"]
    assert one["duration_s"] == 0.5 and one["longest_pause_s"] == 0.0


def test_no_words_is_unusable_with_nothing_heard():
    for empty in (None, [], [{"text": "  ", "start": 0, "end": 1}], [{"text": "hi", "start": None, "end": None}]):
        m = measure_sentence(EXPECTED, empty)
        assert m == {"usable": False, "reason": "nothing_heard", "readings": None}


def test_slower_than_her_usual_is_outside_and_faster_is_not():
    base = make_baseline([3.0, 3.1, 3.2, 3.0, 3.1], min_half_width=0.5, metric="x")
    assert compare_duration(5.0, base)["outside"] is True
    assert compare_duration(1.5, base)["outside"] is False  # faster is never outside
    assert compare_duration(3.1, base)["outside"] is False
    assert compare_duration(3.1, make_baseline([3.0], metric="x")) == {"usual_low": None, "usual_high": None,
                                                                       "outside": None}


# ---- the remote sentence task ------------------------------------------------------------------------------

def run_task(speech, baseline=None, level_samples=0):
    module = StreamSpeechSentence(EXPECTED)
    ctx = TaskContext("c1", "A1", "sentence", baseline or {"snapshot_id": "b", "ready": False, "sessions": 0, "needed": 5})
    observed = []

    async def gen():
        for i in range(level_samples):
            yield {"sequence": i, "captured_ms": i * 250, "format": "reading", "data": {"phase": "recording", "level": 0.1}}
        if speech is not None:
            yield {"sequence": level_samples, "captured_ms": 4000, "format": "speech", "data": speech}

    result = asyncio.run(module.run(ctx, gen(), observed.append))
    return module, result, observed


def ready_baseline():
    b = make_baseline([3.0, 3.1, 3.2, 3.0, 3.1], min_half_width=limits.SENTENCE_MIN_HALF_WIDTH_S, metric="x")
    b["snapshot_id"] = "b1"
    return b


def test_task_result_is_usable_with_a_reading_and_numbers_for_claude():
    speech = {"transcript": EXPECTED, "words": words(EXPECTED, pause_before=3, pause_s=2.5), "stt_error": None}
    module, result, observed = run_task(speech, ready_baseline(), level_samples=9)
    d = result.to_dict()
    assert d["outcome"] == "completed" and d["quality"]["status"] == "usable" and d["performance"] == "observed"
    r = d["reading"]
    assert r["metric_id"] == "speech_duration_v1" and r["unit"] == "s" and r["side"] is None
    assert r["value"] == 5.2 and r["outside"] is True and r["usual_high"] > 3.1
    details = module.attempt_details("A1")
    assert set(details) == {"heard", "word_error_rate", "missing_words", "changed_words", "extra_words",
                            "longest_pause_s", "words_per_min"} and details["longest_pause_s"] == 2.6
    assert observed and all(o.task == "sentence" and o.signal == "none" for o in observed)
    assert len({o.sequence for o in observed}) == len(observed)


def test_learning_gives_no_comparison():
    speech = {"transcript": EXPECTED, "words": words(EXPECTED), "stt_error": None}
    _, result, _ = run_task(speech)
    assert result.reading.outside is None and result.reading.usual_low is None


def test_stt_error_and_silence_are_unusable():
    _, result, _ = run_task({"transcript": None, "words": [], "stt_error": "stt_http_401"})
    assert result.quality.status == "unusable" and result.quality.reasons == ["speech_to_text_failed"]
    assert result.reading is None
    module, result, _ = run_task({"transcript": None, "words": [], "stt_error": None})
    assert result.quality.reasons == ["nothing_heard"] and module.attempt_details("A1") is None
    _, result, _ = run_task(None, level_samples=3)  # the input ended without a result
    assert result.quality.reasons == ["speech_result_missing"]
    _, result, _ = run_task("garbage")
    assert result.quality.status == "unusable"
