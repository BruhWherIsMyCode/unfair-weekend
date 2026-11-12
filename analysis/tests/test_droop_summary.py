from __future__ import annotations

from analysis.droop_summary import format_summary, summarise


def trial(label, ch, cl=0.0, ss=0.0, quality="usable"):
    return {"label": label, "corner_height": ch, "corner_lift": cl,
            "smile_score": ss, "quality": quality}


def clean_trials():
    t = []
    for v in (-0.01, 0.0, 0.01, 0.005, -0.005):
        t.append(trial("normal", v))
    for v in (0.10, 0.12, 0.09):
        t.append(trial("acted_left", v))
    for v in (-0.10, -0.12, -0.09):
        t.append(trial("acted_right", v))
    return t


def test_clean_separation():
    s = summarise(clean_trials())
    v = s["metrics"]["corner_height"]["verdicts"]
    assert v["acted_left"]["verdict"] == "separates"
    assert v["acted_right"]["verdict"] == "separates"
    assert v["acted_left"]["wrong_side"] == 0
    assert abs(s["metrics"]["corner_height"]["normal_spread"] - 0.02) < 1e-9


def test_one_acted_left_inside_normal_overlaps():
    t = clean_trials() + [trial("acted_left", 0.0)]
    v = summarise(t)["metrics"]["corner_height"]["verdicts"]["acted_left"]
    assert v["verdict"] == "overlaps"
    assert v["wrong_side"] == 1


def test_unusable_trials_ignored():
    t = clean_trials() + [trial("acted_left", 0.0, quality="unusable")]
    s = summarise(t)
    assert s["metrics"]["corner_height"]["verdicts"]["acted_left"]["verdict"] == "separates"
    assert s["metrics"]["corner_height"]["labels"]["acted_left"]["count"] == 3


def test_best_metric_picks_clearly_better():
    t = []
    for i, v in enumerate((-0.01, 0.0, 0.01)):
        # corner_height separates cleanly; corner_lift overlaps; smile_score overlaps.
        t.append(trial("normal", v, cl=v * 10, ss=v * 10))
    t += [trial("acted_left", 0.2, cl=0.0, ss=0.0), trial("acted_right", -0.2, cl=0.0, ss=0.0)]
    s = summarise(t)
    assert s["best_metric"] == "corner_height"


def test_format_summary_is_text():
    text = format_summary(summarise(clean_trials()))
    assert "corner_height" in text
    assert "separates" in text
    assert "best metric" in text
