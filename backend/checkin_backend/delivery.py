"""Family delivery: fixed templates, one stored message per call, message.new to family devices.

The backend alone writes the family text. A mid-call change message and the final summary share one message
ID, so family clients upsert by ID. `stored` means stored and pushed to connected family devices, not read.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .hub import DeviceHub
from .people import Person
from .store import NoStore, Store

THINGS = {"face": ("smile", "looks"), "arm": ("arm", "looks"), "speech": ("speech", "sounds")}
KINDS = ("as_usual", "change_noticed", "learning", "incomplete", "no_answer")
HANDOFF = "If {she} seems unwell, call 112."


@dataclass(frozen=True, slots=True)
class Message:
    message_id: str
    call_id: str
    person_id: str
    kind: str
    text: str
    phase: str
    clip_url: None
    created_at: str
    updated_at: str


def words(person: Person) -> dict[str, str]:
    if person.pronoun == "he":
        return {"name": person.name, "she": "he", "her": "his", "her_obj": "him"}
    return {"name": person.name, "she": "she", "her": "her", "her_obj": "her"}


def render(kind: str, person: Person, signs: list[dict] | None = None) -> str:
    w = words(person)
    handoff = HANDOFF.format(**w)
    if kind == "change_noticed":
        confirmed = [s for s in signs or [] if s.get("confirmed") is True]
        if not confirmed:
            raise ValueError("A change message requires a confirmed sign")
        sign = confirmed[0]["sign"]
        thing, verb = THINGS.get(sign, THINGS["face"])
        return f"{w['name']}'s {thing} {verb} different from {w['her']} usual. Call {w['her_obj']} now. {handoff}"
    if kind == "incomplete":
        return f"{w['name']}'s check-in could not be finished. Please call {w['her_obj']}. {handoff}"
    if kind == "no_answer":
        return f"{w['name']} did not answer the check-in call. Please call {w['her_obj']}. {handoff}"
    if kind == "learning":
        return (
            f"{w['name']}'s check-in is done. We are still learning {w['her']} usual, "
            "so there is nothing to compare yet."
        )
    if kind == "as_usual":
        return f"{w['name']}'s check-in today looked as usual."
    raise ValueError(f"Unknown message kind {kind}")


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class FamilyDelivery:
    def __init__(self, hub: DeviceHub, store: Store | None = None) -> None:
        self._hub = hub
        self._store = store if store is not None else NoStore()
        self._messages: dict[str, Message] = {}
        self._by_call: dict[str, str] = {}

    def publish(self, call_id: str, person: Person, kind: str, phase: str, signs: list[dict] | None = None,
                summary: dict | None = None) -> Message:
        if kind not in KINDS:
            raise ValueError(f"Unknown message kind {kind}")
        if phase not in ("mid_call", "final"):
            raise ValueError("phase must be mid_call or final")
        message_id = self._by_call.get(call_id)
        previous = self._messages.get(message_id) if message_id else None
        keep_change = previous is not None and previous.kind == "change_noticed"
        if keep_change:
            # A confirmed change is never replaced by a calmer final summary.
            kind = "change_noticed"
            text = previous.text
        else:
            text = render(kind, person, signs)
        stamp = now_iso()
        message = Message(
            message_id=message_id or f"msg_{uuid4().hex[:16]}",
            call_id=call_id,
            person_id=person.person_id,
            kind=kind,
            text=text,
            phase=phase,
            clip_url=None,
            created_at=previous.created_at if previous else stamp,
            updated_at=stamp,
        )
        self._messages[message.message_id] = message
        self._by_call[call_id] = message.message_id
        family_devices = self._hub.families_of(person.family_id)
        self._hub.send_many(family_devices, "message.new", asdict(message), event_id=f"{message.message_id}_{phase}")
        self._store.record_message(call_id, person.family_id, asdict(message), phase, summary, family_devices)
        return message

    def for_family(self, family_id: str, people: list[Person]) -> list[Message]:
        ids = {p.person_id for p in people if p.family_id == family_id}
        found = [m for m in self._messages.values() if m.person_id in ids]
        return sorted(found, key=lambda m: m.updated_at, reverse=True)

    def for_call(self, call_id: str) -> Message | None:
        message_id = self._by_call.get(call_id)
        if message_id is None:
            return None
        return self._messages.get(message_id)
