"""concern_raised family messages, the runner's path helper and the script copy. No network, no analysis needed."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from checkin_backend import runner
from checkin_backend.delivery import KINDS, FamilyDelivery, render
from checkin_backend.hub import Device, DeviceHub
from checkin_backend.people import Person

REASON_WORDS = {
    "said_unwell": "said {she} does not feel well",
    "asked_for_help": "asked for help",
    "confused_answers": "seemed confused in {her} answers",
    "reading_far_outside": "one check was far from {her} usual",
    "other": "something worried the assistant",
}


def person(pronoun: str = "she") -> Person:
    return Person("p_demo", "Ona", pronoun, "f_demo")


def desk() -> tuple[DeviceHub, FamilyDelivery]:
    hub = DeviceHub()
    hub.register(Device("fam", "family", None, "f_demo"))
    return hub, FamilyDelivery(hub)


def events(hub: DeviceHub) -> list:
    return list(hub._history["fam"])


@pytest.mark.parametrize("pronoun,she,her,her_obj", [("she", "she", "her", "her"), ("he", "he", "his", "him")])
def test_concern_text_is_pronoun_aware_has_the_reason_and_ends_with_the_handoff(pronoun, she, her, her_obj):
    for reason, words in REASON_WORDS.items():
        text = render("concern_raised", person(pronoun), None, {"reason": reason, "note": "private note"})
        assert text == (
            f"Ona said or showed something during today's check-in that needs a look "
            f"({words.format(she=she, her=her)}). Please call {her_obj}. If {she} seems unwell, call 112."
        )
        assert "private note" not in text


def test_an_unknown_or_missing_reason_reads_as_other():
    for concern in (None, {}, {"reason": "invented"}):
        assert "(something worried the assistant)" in render("concern_raised", person(), None, concern)


def test_the_new_kind_is_known_and_unknown_kinds_still_raise():
    assert "concern_raised" in KINDS
    _, delivery = desk()
    with pytest.raises(ValueError, match="Unknown message kind"):
        delivery.publish("call_1", person(), "alarm", "final")
    with pytest.raises(ValueError, match="Unknown message kind"):
        render("alarm", person())


def test_a_concern_is_kept_against_a_calmer_final_summary():
    hub, delivery = desk()
    concern = {"raised_by": "agent", "reason": "asked_for_help", "note": "x"}
    first = delivery.publish("call_1", person(), "concern_raised", "mid_call", [], concern)
    assert first.kind == "concern_raised" and "asked for help" in first.text
    for calmer in ("as_usual", "learning", "incomplete"):
        final = delivery.publish("call_1", person(), calmer, "final", [], None)
        assert final.kind == "concern_raised" and final.text == first.text and final.message_id == first.message_id
    assert delivery.for_call("call_1").kind == "concern_raised"
    assert len(events(hub)) == 4


def test_only_a_confirmed_change_replaces_a_concern_and_then_stays():
    _, delivery = desk()
    concern = {"reason": "said_unwell"}
    delivery.publish("call_1", person(), "concern_raised", "mid_call", [], concern)
    signs = [{"sign": "arm", "confirmed": True}]
    change = delivery.publish("call_1", person(), "change_noticed", "mid_call", signs, concern)
    assert change.kind == "change_noticed" and "arm looks different" in change.text
    later = delivery.publish("call_1", person(), "concern_raised", "final", [], concern)
    assert later.kind == "change_noticed" and later.text == change.text  # a change is never downgraded
    assert delivery.publish("call_1", person(), "as_usual", "final").kind == "change_noticed"


def test_a_later_change_summary_names_every_confirmed_sign():
    # acted phone run: the face confirmed mid-call, the arm later; the final message still named only the smile
    _, delivery = desk()
    first = delivery.publish("call_1", person(), "change_noticed", "mid_call", [{"sign": "face", "confirmed": True}])
    assert "Ona's smile looks different" in first.text
    signs = [{"sign": "face", "confirmed": True}, {"sign": "arm", "confirmed": True}]
    final = delivery.publish("call_1", person(), "change_noticed", "final", signs)
    assert final.message_id == first.message_id and final.text.startswith("Ona's smile and arm look different")
    assert final.text.endswith("If she seems unwell, call 112.")
    assert delivery.publish("call_1", person(), "as_usual", "final").text == final.text  # still never calmer


def test_a_concern_after_nothing_or_after_a_calm_message_is_published():
    _, delivery = desk()
    assert delivery.publish("c1", person(), "as_usual", "mid_call").kind == "as_usual"
    assert delivery.publish("c1", person(), "concern_raised", "final", [], {"reason": "other"}).kind == "concern_raised"


def test_a_second_publish_in_the_same_phase_gets_its_own_event_id():
    hub, delivery = desk()
    delivery.publish("c1", person(), "concern_raised", "mid_call", [], {"reason": "other"})
    delivery.publish("c1", person(), "change_noticed", "mid_call", [{"sign": "face", "confirmed": True}])
    delivery.publish("c1", person(), "change_noticed", "final", [{"sign": "face", "confirmed": True}])
    ids = [event.event_id for event in events(hub)]
    message_id = delivery.for_call("c1").message_id
    assert ids == [f"{message_id}_mid_call", f"{message_id}_mid_call_2", f"{message_id}_final"]


@pytest.mark.anyio
async def test_the_link_passes_the_concern_to_the_family_message():
    from checkin_backend.link import ClientLink

    hub, delivery = desk()
    link = ClientLink(call_id="call_1", person=person("he"), hub=hub, speech=None, delivery=delivery)
    summary = {"status": "concern_raised", "signs": [], "concern": {"raised_by": "agent", "reason": "confused_answers",
                                                                       "note": "mixed up the day"}}
    await link.publish(summary, "mid_call")
    message = delivery.for_call("call_1")
    assert message.kind == "concern_raised"
    assert message.text.endswith("(seemed confused in his answers). Please call him. If he seems unwell, call 112.")
    await link.publish({"status": "incomplete", "signs": []}, "final")
    assert delivery.for_call("call_1").kind == "concern_raised"


# ---- the runner finds analysis without PYTHONPATH --------------------------------------------------------------------

def test_the_path_helper_adds_the_repo_root_when_analysis_is_missing(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / "analysis").mkdir(parents=True)
    (root / "analysis" / "__init__.py").write_text("", "utf-8")
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args):
        if name == "analysis":
            return object() if str(root) in sys.path else None
        return real_find_spec(name, *args)

    clean = [p for p in sys.path if p != str(runner.REPO_ROOT)]
    monkeypatch.setattr(sys, "path", clean)
    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(runner, "REPO_ROOT", root)
    assert runner.ensure_analysis_path() is True
    assert sys.path[0] == str(root)
    assert runner.ensure_analysis_path() is True  # already importable now: nothing more is added
    assert sys.path.count(str(root)) == 1


def test_the_path_helper_does_nothing_without_an_analysis_folder(monkeypatch, tmp_path):
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "analysis" else real_find_spec(name, *a))
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    before = list(sys.path)
    assert runner.ensure_analysis_path() is False
    assert sys.path == before


def test_the_path_helper_leaves_an_importable_analysis_alone(monkeypatch):
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: object() if name == "analysis" else real_find_spec(name, *a))
    before = list(sys.path)
    assert runner.ensure_analysis_path() is True
    assert sys.path == before


def test_the_repo_root_is_the_parent_of_backend():
    assert (runner.REPO_ROOT / "backend" / "checkin_backend").is_dir()


# ---- the script copy -------------------------------------------------------------------------------------------------

CONTRACT_SCRIPT = runner.REPO_ROOT / "contract" / "script.en.json"
BACKEND_SCRIPT = Path(runner.__file__).with_name("data") / "script.en.json"


@pytest.mark.skipif(not CONTRACT_SCRIPT.exists(), reason="../contract is not in this checkout")
def test_the_backend_script_is_a_byte_identical_copy_of_the_published_contract_script():
    assert BACKEND_SCRIPT.read_bytes() == CONTRACT_SCRIPT.read_bytes()


def test_the_backend_script_has_the_one_hand_phone_lines():
    script = json.loads(BACKEND_SCRIPT.read_text("utf-8"))
    for line in ("setup_phone", "task_arms", "repeat_arms", "swap_hands"):
        assert line in script
    assert "table" not in script["setup_phone"] and "both arms" not in script["task_arms"]
