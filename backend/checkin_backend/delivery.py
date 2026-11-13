"""Family delivery: fixed templates, one stored message per call, message.new to family devices.

The backend alone writes the family text. A mid-call change message and the final summary share one message
ID, so family clients upsert by ID. `stored` means stored and pushed to connected family devices, not read.

A change message may carry `clip_url`: the camera frames of both tries at the task that showed the change, kept
in memory only (never on disk, never in the database) and dropped after CLIP_TTL_S.
"""

import base64
import logging
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from .hub import DeviceHub
from .logging_config import log_event
from .people import Person
from .store import NoStore, Store

log = logging.getLogger(__name__)

THINGS = {"face": ("smile", "looks"), "arm": ("arm", "looks"), "speech": ("speech", "sounds")}
KINDS = ("as_usual", "change_noticed", "concern_raised", "learning", "incomplete", "no_answer")
HANDOFF = "If {she} seems unwell, call 112."
CONCERN_REASONS = {
    "said_unwell": "said {she} does not feel well",
    "asked_for_help": "asked for help",
    "confused_answers": "seemed confused in {her} answers",
    "reading_far_outside": "one check was far from {her} usual",
    "other": "something worried the assistant",
}


@dataclass(frozen=True, slots=True)
class Message:
    message_id: str
    call_id: str
    person_id: str
    kind: str
    text: str
    phase: str
    clip_url: str | None
    created_at: str
    updated_at: str


def words(person: Person) -> dict[str, str]:
    if person.pronoun == "he":
        return {"name": person.name, "she": "he", "her": "his", "her_obj": "him"}
    return {"name": person.name, "she": "she", "her": "her", "her_obj": "her"}


def render(kind: str, person: Person, signs: list[dict] | None = None, concern: dict | None = None) -> str:
    w = words(person)
    handoff = HANDOFF.format(**w)
    if kind == "concern_raised":
        reason = CONCERN_REASONS.get((concern or {}).get("reason"), CONCERN_REASONS["other"]).format(**w)
        return (
            f"{w['name']} said or showed something during today's check-in that needs a look ({reason}). "
            f"Please call {w['her_obj']}. {handoff}"
        )
    if kind == "change_noticed":
        confirmed = [s for s in signs or [] if s.get("confirmed") is True]
        if not confirmed:
            raise ValueError("A change message requires a confirmed sign")
        things = [THINGS.get(s["sign"], THINGS["face"]) for s in confirmed]
        if len(things) == 1:
            thing, verb = things[0]
        else:  # the face and an arm confirmed each other: "Ona's smile and arm look different ..."
            thing = " and ".join(t for t, _ in things)
            verb = "look" if all(v == "looks" for _, v in things) else "seem"
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


CLIP_TTL_S = 30 * 60
MAX_CLIP_FRAMES = 160
DEFAULT_PERIOD_MS = 200


@dataclass(frozen=True, slots=True)
class Clip:
    clip_id: str
    call_id: str
    period_ms: int
    frames: tuple[bytes, ...]
    created: float

    def as_json(self) -> dict:
        return {"clip_id": self.clip_id, "format": "jpeg", "period_ms": self.period_ms,
                "frames": [base64.b64encode(frame).decode("ascii") for frame in self.frames]}


class ClipShelf:
    """In-memory alert clips. A family client fetches one with the demo key; it expires after CLIP_TTL_S."""

    def __init__(self, ttl_s: float = CLIP_TTL_S, clock=monotonic) -> None:
        self._ttl_s = ttl_s
        self._clock = clock
        self._clips: dict[str, Clip] = {}

    def put(self, call_id: str, attempts: list[list[tuple[int, bytes]]]) -> Clip | None:
        self._expire()
        frames: list[bytes] = []
        gaps: list[int] = []
        for attempt in attempts:
            ordered = sorted(attempt, key=lambda item: item[0])
            frames.extend(data for _, data in ordered)
            gaps.extend(b[0] - a[0] for a, b in zip(ordered, ordered[1:]) if b[0] > a[0])
        if not frames:
            return None
        if len(frames) > MAX_CLIP_FRAMES:
            step = len(frames) / MAX_CLIP_FRAMES
            frames = [frames[int(i * step)] for i in range(MAX_CLIP_FRAMES)]
            gaps = [int(g * step) for g in gaps]
        period = int(statistics.median(gaps)) if gaps else DEFAULT_PERIOD_MS
        clip = Clip(f"clip_{uuid4().hex[:16]}", call_id, max(40, min(period, 1000)), tuple(frames), self._clock())
        self._clips[clip.clip_id] = clip
        return clip

    def get(self, clip_id: str) -> Clip | None:
        self._expire()
        return self._clips.get(clip_id)

    def _expire(self) -> None:
        now = self._clock()
        for clip_id in [c.clip_id for c in self._clips.values() if now - c.created > self._ttl_s]:
            del self._clips[clip_id]


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class FamilyDelivery:
    def __init__(self, hub: DeviceHub, store: Store | None = None) -> None:
        self._hub = hub
        self._store = store if store is not None else NoStore()
        self._messages: dict[str, Message] = {}
        self._by_call: dict[str, str] = {}
        self._sent: dict[tuple[str, str], int] = {}
        self.clips = ClipShelf()

    def publish(
        self,
        call_id: str,
        person: Person,
        kind: str,
        phase: str,
        signs: list[dict] | None = None,
        concern: dict | None = None,
        summary: dict | None = None,
        clip: list[list[tuple[int, bytes]]] | None = None,
    ) -> Message:
        if kind not in KINDS:
            raise ValueError(f"Unknown message kind {kind}")
        if phase not in ("mid_call", "final"):
            raise ValueError("phase must be mid_call or final")
        message_id = self._by_call.get(call_id)
        previous = self._messages.get(message_id) if message_id else None
        # A later change summary (more confirmed signs, e.g. the arm after the face) re-renders the change text.
        keep_change = previous is not None and previous.kind == "change_noticed" and kind != "change_noticed"
        # A concern is replaced only by a confirmed change, never by a calmer summary.
        keep_concern = previous is not None and previous.kind == "concern_raised" and kind != "change_noticed"
        if keep_change or keep_concern:
            # A confirmed change is never replaced by a calmer final summary.
            kind = previous.kind
            text = previous.text
        else:
            text = render(kind, person, signs, concern)
        # Keep the first clip: the final summary of a change call carries the same evidence as the mid-call one.
        clip_url = previous.clip_url if previous is not None else None
        if clip_url is None and kind == "change_noticed" and clip:
            shelved = self.clips.put(call_id, clip)
            clip_url = f"/v1/clips/{shelved.clip_id}" if shelved else None
        stamp = now_iso()
        message = Message(
            message_id=message_id or f"msg_{uuid4().hex[:16]}",
            call_id=call_id,
            person_id=person.person_id,
            kind=kind,
            text=text,
            phase=phase,
            clip_url=clip_url,
            created_at=previous.created_at if previous else stamp,
            updated_at=stamp,
        )
        self._messages[message.message_id] = message
        self._by_call[call_id] = message.message_id
        sent = self._sent.get((message.message_id, phase), 0)
        self._sent[(message.message_id, phase)] = sent + 1
        suffix = "" if sent == 0 else f"_{sent + 1}"  # a second mid-call publish must not reuse the first event ID
        family_devices = self._hub.families_of(person.family_id)
        self._hub.send_many(family_devices, "message.new", asdict(message),
                            event_id=f"{message.message_id}_{phase}{suffix}")
        self._store.record_message(call_id, person.family_id, asdict(message), phase, summary, family_devices)
        log_event(log, logging.INFO, "family.message_published", call_id=call_id, message_id=message.message_id,
                  kind=kind, phase=phase, connected_devices=len(family_devices), persistence_queued=self._store.enabled)
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
