"""Hand-test summary on made-up trials."""
from analysis.hand_summary import format_summary, summarise


def t(hand, mode, sink=0.0, height=0.5, turn=0.0, shake=0.01, quality="usable"):
    return {"hand": hand, "mode": mode, "quality": quality, "hand_sink": sink,
            "hand_height": height, "palm_turn": turn, "hand_shake": shake}


def test_weak_above_normal_separates():
    trials = [t("left", "normal", sink=s) for s in (0.00, 0.02, 0.04)]
    trials += [t("left", "weak", sink=s) for s in (0.2, 0.3, 1.0)]
    h = summarise(trials)["hands"]["left"]
    e = h["readings"]["hand_sink"]
    assert e["normal"]["min"] == 0.0 and e["normal"]["max"] == 0.04
    assert e["above"] == 3 and e["count"] == 3 and e["separates"]
    assert h["best"] == "hand_sink"
    assert h["verdict"] == "her LEFT hand: acted weak separates on hand_sink (3 of 3 above normal)"


def test_overlap_does_not_separate():
    trials = [t("right", "normal", sink=s) for s in (0.0, 0.1)]
    trials += [t("right", "weak", sink=s, height=0.5, turn=0.0, shake=0.01) for s in (0.05, 0.3)]
    h = summarise(trials)["hands"]["right"]
    assert h["readings"]["hand_sink"]["above"] == 1
    assert h["best"] is None
    assert h["verdict"] == "her RIGHT hand: acted weak does not separate"


def test_unusable_trials_and_other_hand_are_ignored():
    trials = [t("left", "normal", sink=0.0), t("left", "weak", sink=5.0, quality="unusable"),
              t("right", "weak", sink=9.0)]
    s = summarise(trials)["hands"]
    assert s["left"]["readings"]["hand_sink"]["count"] == 0
    assert s["left"]["verdict"].endswith("not enough data (need normal and acted weak trials)")


def test_below_normal_never_counts_as_above():
    trials = [t("left", "normal", sink=0.1), t("left", "weak", sink=-0.5)]
    assert summarise(trials)["hands"]["left"]["readings"]["hand_sink"]["above"] == 0


def test_best_reading_can_be_another_one():
    trials = [t("left", "normal", sink=0.0, turn=0.0), t("left", "normal", sink=0.1, turn=0.02)]
    trials += [t("left", "weak", sink=0.05, turn=0.5)]
    h = summarise(trials)["hands"]["left"]
    assert h["best"] == "palm_turn"


def test_format_has_both_hands_and_verdicts():
    text = format_summary(summarise([t("left", "normal"), t("left", "weak", sink=1.0)]))
    assert "her LEFT hand" in text and "her RIGHT hand" in text
    assert "not enough data" in text
