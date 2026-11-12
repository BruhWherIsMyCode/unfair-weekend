from __future__ import annotations

from analysis import config
from analysis.baseline import (
    add_session,
    baseline_for,
    baseline_values,
    compare,
    load_person,
    make_baseline,
)


def test_fewer_than_needed_is_learning():
    b = make_baseline([0.0, 0.01, 0.02, 0.0])
    assert b["ready"] is False
    assert b["status"] == "learning"
    assert b["usual_low"] is None and b["usual_high"] is None


def test_steady_values_respect_minimum_half_width():
    b = make_baseline([0.05] * 6, min_half_width=0.02)
    assert b["ready"] is True
    assert abs(b["usual_high"] - 0.07) < 1e-9
    assert abs(b["usual_low"] - 0.03) < 1e-9


def test_metric_default_half_width_from_config():
    b = make_baseline([0.0] * 6, metric="smile_score")
    assert abs(b["usual_high"] - config.MIN_HALF_WIDTH["smile_score"]) < 1e-9


def _ready():
    return make_baseline([0.0] * 6, min_half_width=0.02)


def test_value_above_range_is_outside_left():
    c = compare(0.10, _ready())
    assert c["outside"] is True
    assert c["side"] == "left"
    assert abs(c["outside_by"] - 0.08) < 1e-9


def test_value_below_range_is_outside_right():
    c = compare(-0.05, _ready())
    assert c["outside"] is True
    assert c["side"] == "right"
    assert abs(c["outside_by"] - 0.03) < 1e-9


def test_value_inside_is_not_outside():
    c = compare(0.01, _ready())
    assert c["outside"] is False
    assert c["side"] is None
    assert c["outside_by"] == 0


def test_learning_never_outside():
    c = compare(5.0, make_baseline([0.0, 0.0]))
    assert c["outside"] is False and c["side"] is None


def test_acted_sessions_never_enter_baseline(tmp_path):
    path = tmp_path / "p.json"
    for _ in range(5):
        add_session(path, {"corner_height": 0.0}, "normal")
    for _ in range(5):
        add_session(path, {"corner_height": 0.5}, "acted")
    add_session(path, {"corner_height": 0.01}, "live")
    person = load_person(path)
    assert len(person["sessions"]) == 11
    vals = baseline_values(person, "corner_height")
    assert len(vals) == 6
    assert 0.5 not in vals
    b = baseline_for(person, "corner_height")
    assert b["ready"] and b["sessions"] == 6
    assert b["usual_high"] < 0.1


def test_load_missing_person_is_empty(tmp_path):
    assert load_person(tmp_path / "none.json") == {"sessions": []}
