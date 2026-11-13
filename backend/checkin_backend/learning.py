"""Backend learning defaults and the spoken introduction from the call's frozen baselines."""

from typing import Any

DEFAULT_LEARNING_CALLS = 2


def introduction(session: Any) -> str | None:
    """Count the calls still needed by the least-trained required check, including this call."""
    if session is None or getattr(session, "label", None) == "acted":
        return None
    baselines = getattr(session, "baselines", {})
    required = getattr(session, "required_tasks", ())
    checks = [baselines[task] for task in required if task in baselines]
    if not checks or all(check.get("ready") for check in checks):
        return None
    needed = max(int(check["needed"]) for check in checks)
    remaining = max(max(0, int(check["needed"]) - int(check["sessions"])) for check in checks)
    call_word = "call" if needed == 1 else "calls"
    remaining_words = "call is" if remaining == 1 else "calls are"
    return (
        "Before we begin, this call helps me learn what is usual for you. "
        f"I need {needed} successful check-in {call_word} in total. "
        f"Including this one, {remaining} successful {remaining_words} still needed. "
        "Please do the checks as you normally would."
    )
