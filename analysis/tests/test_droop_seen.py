"""Tests for the mismatch exclusion and the new summary wording. Written, not run yet."""
from __future__ import annotations

from analysis.droop_summary import format_summary, summarise
from analysis.guide import verdict_lines


def trial(label, ch, matches=None, quality="usable"):
    t = {"label": label, "corner_height": ch, "corner_lift": 0.0, "smile_score": 0.0,
         "quality": quality}
    if matches is not None:
        t["matches_asked"] = matches
    return t


def base_trials():
    t = [trial("normal", v) for v in (-0.01, 0.0, 0.01, 0.005, -0.005)]
    t += [trial("acted_left", v, "yes") for v in (0.10, 0.12, 0.09)]
    t += [trial("acted_right", v, "yes") for v in (-0.10, -0.12, -0.09)]
    return t


def test_mismatched_acted_trials_are_excluded_from_the_verdict():
    # Two "acted left" trials where the wrong side was lifted: they would make it "overlap".
    t = base_trials() + [trial("acted_left", -0.08, "no"), trial("acted_left", 0.0, "no")]
    s = summarise(t)
    left = s["metrics"]["corner_height"]["verdicts"]["acted_left"]
    assert left["verdict"] == "separates"
    assert left["count"] == 3
    assert s["metrics"]["corner_height"]["labels"]["acted_left"]["count"] == 3
    assert s["excluded"] == {"acted_left": 2, "acted_right": 0}


def test_excluded_line_is_printed_and_picked_up_for_the_screen():
    t = base_trials() + [trial("acted_left", -0.08, "no"), trial("acted_left", 0.0, "no")]
    text = format_summary(summarise(t))
    assert "2 acted-left trials did not look like a left droop (excluded)" in text
    assert "acted-right trials did not look" not in text
    lines = verdict_lines(text, "face")
    assert any(ln.endswith("(excluded)") for ln in lines)


def test_old_rows_without_the_column_count_as_matching():
    t = [trial("normal", v) for v in (-0.01, 0.0, 0.01)]
    t += [trial("acted_left", 0.10), trial("acted_left", 0.11, ""), trial("acted_right", -0.1)]
    s = summarise(t)
    assert s["metrics"]["corner_height"]["labels"]["acted_left"]["count"] == 2
    assert s["excluded"] == {"acted_left": 0, "acted_right": 0}
    assert "(excluded)" not in format_summary(s)


def test_a_normal_trial_is_never_excluded():
    t = base_trials() + [trial("normal", 0.02, "no")]
    assert summarise(t)["metrics"]["corner_height"]["labels"]["normal"]["count"] == 6


def test_summary_wording_has_no_double_negative():
    text = format_summary(summarise(base_trials()))
    assert "(3 of 3 acted trials beyond normal, on the correct side)" in text
    assert "not beyond normal" not in text
    # an overlap: 2 of 3 beyond
    t = base_trials() + [trial("acted_left", 0.0, "yes")]
    assert "(3 of 4 acted trials beyond normal, on the correct side)" in format_summary(summarise(t))
