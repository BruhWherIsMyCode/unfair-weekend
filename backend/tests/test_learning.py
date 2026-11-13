"""Learning uses the required checks' progress and respects the operator's threshold override."""

from types import SimpleNamespace

import pytest

from checkin_backend.learning import introduction
from checkin_backend.people import Person
from checkin_backend.runner import ensure_analysis_path, run_analysis_call


def session(*, smile=0, arms=0, needed=2, label="live"):
    return SimpleNamespace(
        label=label, required_tasks=("smile", "arms"),
        baselines={task: {"sessions": count, "needed": needed, "ready": count >= needed}
                   for task, count in (("smile", smile), ("arms", arms), ("sentence", 0))},
    )


def test_the_slowest_required_check_sets_the_remaining_count():
    text = introduction(session(smile=2, arms=1))
    assert "2 successful check-in calls in total" in text
    assert "Including this one, 1 successful call is still needed" in text


def test_unused_sentence_does_not_keep_a_ready_call_in_learning():
    assert introduction(session(smile=2, arms=2)) is None


def test_acted_and_scripted_calls_do_not_claim_to_train():
    assert introduction(session(label="acted")) is None
    assert introduction(SimpleNamespace()) is None
    assert introduction(None) is None


@pytest.mark.anyio
@pytest.mark.parametrize("configured, expected", [(None, 2), ("", 2), ("  ", 2), ("3", 3)])
async def test_backend_defaults_to_two_calls_and_keeps_an_explicit_override(monkeypatch, configured, expected):
    ensure_analysis_path()
    from analysis import backend_entry

    if configured is None:
        monkeypatch.delenv("UW_BASELINE_NEEDED", raising=False)
    else:
        monkeypatch.setenv("UW_BASELINE_NEEDED", configured)

    async def inspect(link, **kwargs):
        return {"needed": backend_entry.Settings(kwargs["env"], link.person.person_id).baseline_needed}

    monkeypatch.setattr(backend_entry, "run_backend_call", inspect)
    link = SimpleNamespace(person=Person("p_demo", "Ona", "she", "f_demo"))
    assert await run_analysis_call(link, "fake") == {"needed": expected}
