"""The people a demo backend can call. One configured person; no persistence yet."""

import os
from dataclasses import dataclass, field
from typing import Any

PRONOUNS = ("she", "he")


@dataclass(frozen=True, slots=True)
class Person:
    person_id: str
    name: str
    pronoun: str
    family_id: str
    label: str = "live"
    baselines: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.pronoun not in PRONOUNS:
            raise ValueError("pronoun must be she or he")
        if self.label not in ("live", "normal", "acted"):
            raise ValueError("label must be live, normal or acted")


class Directory:
    def __init__(self, people: list[Person]) -> None:
        self._people = {person.person_id: person for person in people}

    def get(self, person_id: str) -> Person | None:
        return self._people.get(person_id)

    def all(self) -> list[Person]:
        return list(self._people.values())

    @classmethod
    def from_environment(cls) -> "Directory":
        person = Person(
            person_id=os.environ.get("DEMO_PERSON_ID", "p_demo"),
            name=os.environ.get("DEMO_PERSON_NAME", "Ona"),
            pronoun=os.environ.get("DEMO_PERSON_PRONOUN", "she"),
            family_id=os.environ.get("DEMO_FAMILY_ID", "f_demo"),
            label=os.environ.get("DEMO_SESSION_LABEL", "live"),
        )
        return cls([person])
