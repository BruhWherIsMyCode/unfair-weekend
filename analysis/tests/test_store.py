"""The baseline store: append after a call, load as the baselines a CallSession expects."""
from __future__ import annotations

import json

from analysis.pc.store import BaselineStore, calls_path

VALUES = {"corner_height_v1": 0.01, "arms.hand_left.hand_sink": 0.0, "arms.hand_right.hand_sink": 0.01}


def test_path_is_per_person_and_safe(tmp_path):
    assert calls_path("feodor", tmp_path).name == "feodor_calls.json"
    assert calls_path("a/b c", tmp_path).name == "a_b_c_calls.json"


def test_empty_store_loads_not_ready_baselines(tmp_path):
    b = BaselineStore("p", tmp_path).load_baselines()
    assert b["smile"]["ready"] is False and b["smile"]["sessions"] == 0
    assert b["arms"]["ready"] is False and b["arms"]["hand_left"]["ready"] is False and b["arms"]["sessions"] == 0
    assert b["smile"]["snapshot_id"] and b["arms"]["snapshot_id"]


def test_append_and_load_become_ready_after_five(tmp_path):
    store = BaselineStore("p", tmp_path)
    ids = []
    for i in range(5):
        assert store.append("live", VALUES) is True
        ids.append(store.load_baselines()["smile"]["snapshot_id"])
    b = store.load_baselines()
    assert b["smile"]["ready"] and b["smile"]["sessions"] == 5 and b["smile"]["usual_low"] < 0.01 < b["smile"]["usual_high"]
    assert b["arms"]["ready"] and b["arms"]["hand_left"]["ready"] and b["arms"]["hand_right"]["ready"]
    assert b["arms"]["hand_right"]["median"] == 0.01 and b["arms"]["sessions"] == 5
    assert len(set(ids)) == 5  # the snapshot id changes whenever the baseline does
    assert len(json.loads(store.path.read_text())["sessions"]) == 5


def test_acted_and_empty_values_are_never_stored(tmp_path):
    store = BaselineStore("p", tmp_path)
    assert store.append("acted", VALUES) is False
    assert store.append("live", {}) is False
    assert not store.path.exists()
    store.append("normal", VALUES)
    assert store.load_baselines()["smile"]["sessions"] == 1


def test_one_hand_missing_keeps_arms_not_ready(tmp_path):
    store = BaselineStore("p", tmp_path)
    for _ in range(5):
        store.append("live", {"corner_height_v1": 0.01, "arms.hand_left.hand_sink": 0.0})
    b = store.load_baselines()
    assert b["smile"]["ready"] and b["arms"]["hand_left"]["ready"] and not b["arms"]["hand_right"]["ready"]
    assert b["arms"]["ready"] is False and b["arms"]["sessions"] == 0
