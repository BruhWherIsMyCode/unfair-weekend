"""The people a demo backend can call. One configured person; no persistence yet.

The call person's profile (name, pronoun, age, a few short facts) lives in `data/person.json` next to this package
(override the path with the environment variable DEMO_PERSON_FILE). The file's values win; DEMO_PERSON_NAME and
DEMO_PERSON_PRONOUN only fill a field the file lacks, or stand in when the file is missing.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PRONOUNS = ("she", "he")
PERSON_FILE = Path(__file__).resolve().parent / "data" / "person.json"
DEFAULT_NAME = "Ona"  # last resort, only when the profile file is missing
DEFAULT_PRONOUN = "she"

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Person:
    person_id: str
    name: str
    pronoun: str
    family_id: str
    label: str = "live"
    baselines: dict[str, Any] = field(default_factory=dict)
    age: int | None = None
    about: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.pronoun not in PRONOUNS:
            raise ValueError("pronoun must be she or he")
        if self.label not in ("live", "normal", "acted"):
            raise ValueError("label must be live, normal or acted")


def load_profile(path: Path) -> dict[str, Any]:
    """The validated profile file as a dict with only the keys it has. A broken file raises ValueError."""
    where = str(path)
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{where} is not valid JSON ({exc.msg} at line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{where} must hold one JSON object")
    profile: dict[str, Any] = {}
    if "name" in data:
        name = data["name"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where}: name must be a non-empty string")
        profile["name"] = name.strip()
    if "pronoun" in data:
        if data["pronoun"] not in PRONOUNS:
            raise ValueError(f"{where}: pronoun must be she or he")
        profile["pronoun"] = data["pronoun"]
    if "age" in data:
        age = data["age"]
        if isinstance(age, bool) or not isinstance(age, int) or age <= 0:
            raise ValueError(f"{where}: age must be a positive whole number")
        profile["age"] = age
    if "about" in data:
        about = data["about"]
        if not isinstance(about, list) or not all(isinstance(item, str) for item in about):
            raise ValueError(f"{where}: about must be a list of strings")
        profile["about"] = tuple(item.strip() for item in about if item.strip())
    return profile


class Directory:
    def __init__(self, people: list[Person]) -> None:
        self._people = {person.person_id: person for person in people}

    def get(self, person_id: str) -> Person | None:
        return self._people.get(person_id)

    def all(self) -> list[Person]:
        return list(self._people.values())

    @classmethod
    def from_environment(cls) -> "Directory":
        override = os.environ.get("DEMO_PERSON_FILE")
        path = Path(override) if override else PERSON_FILE
        env_name = os.environ.get("DEMO_PERSON_NAME")
        env_pronoun = os.environ.get("DEMO_PERSON_PRONOUN")
        if path.is_file():
            profile = load_profile(path)  # a broken file stops the start-up here, never mid-call
            for label, env_value, key in (("DEMO_PERSON_NAME", env_name, "name"),
                                          ("DEMO_PERSON_PRONOUN", env_pronoun, "pronoun")):
                if env_value is not None and key in profile and env_value != profile[key]:
                    log.warning("%s=%s is ignored: %s sets the %s to %s", label, env_value, path, key, profile[key])
            source = f"from {_shown(path)}"
        else:
            profile = {}
            source = f"from the environment (no profile file at {path})"
        name = profile.get("name") or env_name or DEFAULT_NAME
        pronoun = profile.get("pronoun") or env_pronoun or DEFAULT_PRONOUN
        if not name.strip():
            raise ValueError("DEMO_PERSON_NAME must not be empty")
        person = Person(
            person_id=os.environ.get("DEMO_PERSON_ID", "p_demo"),
            name=name,
            pronoun=pronoun,
            family_id=os.environ.get("DEMO_FAMILY_ID", "f_demo"),
            label=os.environ.get("DEMO_SESSION_LABEL", "live"),
            age=profile.get("age"),
            about=profile.get("about", ()),
        )
        age = f", {person.age}" if person.age else ""
        log.info("person: %s (%s%s) %s", person.name, person.pronoun, age, source)
        return cls([person])


def _shown(path: Path) -> str:
    """`data/person.json` for the shipped file, else the path as given."""
    return "data/person.json" if path == PERSON_FILE else str(path)
