"""Session payloads against contract/fixtures.json and, when available, the OpenAPI schemas. Written, not run.

The fixture round trip needs nothing but the standard library. The JSON Schema check is skipped unless
jsonschema, referencing and PyYAML are importable.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from analysis.session.models import (ResourceEvent, SessionResult, TaskInterrupt, TaskObservation, TaskResult)
from analysis.tests.session_helpers import Rig, resource_event

CONTRACT = Path(__file__).resolve().parents[2] / "contract"
FIXTURES = json.loads((CONTRACT / "fixtures.json").read_text("utf-8"))

PARSERS = {
    "TaskObservation": TaskObservation.from_dict,
    "TaskInterrupt": TaskInterrupt.from_dict,
    "TaskResult": TaskResult.from_dict,
    "SessionResult": SessionResult.from_dict,
}


def parser_for(fixture):
    if fixture["schema"] in PARSERS:
        return PARSERS[fixture["schema"]]
    if fixture["schema"] == "ClientEvent" and fixture["data"].get("type") == "resource.changed":
        return ResourceEvent.from_dict
    return None


COVERED = [f for f in FIXTURES if parser_for(f) is not None]


def test_the_published_copy_equals_the_draft():
    """Only fixtures.json is still pinned byte for byte to backend/proposals. Two files are intentionally newer or
    different now: contract/script.en.json (the one-hand phone script, published 0.3; the backend's own copy is pinned
    to it by a backend test) and mvp.openapi.yaml (the proposals copy was relabelled 'Historical 0.2' by Agent 2)."""
    draft = Path(__file__).resolve().parents[2] / "backend" / "proposals"
    assert (CONTRACT / "fixtures.json").read_bytes() == (draft / "fixtures.json").read_bytes()


def test_the_published_script_is_the_one_hand_phone_script():
    script = json.loads((CONTRACT / "script.en.json").read_text("utf-8"))
    assert "swap_hands" in script and "right hand" in script["setup_phone"]
    assert "both arms" not in script["task_arms"] and "table" not in script["setup_phone"]
    draft = json.loads((Path(__file__).resolve().parents[2] / "backend" / "proposals" / "script.en.json")
                       .read_text("utf-8"))
    assert "swap_hands" not in draft  # the draft is the older text; the published script is intentionally newer


def test_fixture_coverage_is_not_empty():
    assert len(COVERED) >= 30


@pytest.mark.parametrize("fixture", [f for f in COVERED if f["valid"]], ids=lambda f: f["name"])
def test_valid_fixtures_round_trip_exactly(fixture):
    assert parser_for(fixture)(fixture["data"]).to_dict() == fixture["data"]


@pytest.mark.parametrize("fixture", [f for f in COVERED if not f["valid"]], ids=lambda f: f["name"])
def test_invalid_fixtures_are_rejected(fixture):
    with pytest.raises(ValueError):
        parser_for(fixture)(fixture["data"]).to_dict()


# ---- what the session itself produces ------------------------------------------------------------

def session_outputs():
    """Run a short call and collect every dict the session produced."""
    async def scenario():
        rig = Rig()
        out = {"TaskObservation": [], "TaskInterrupt": [], "TaskResult": [], "SessionResult": []}
        await rig.start()
        await rig.tick(1.0)
        rig.module.emit("usable")
        out["TaskObservation"] = [o.to_dict() for o in rig.session.attempts[rig.attempt_id].observations]
        rig.session.handle_resource_event(resource_event("e1", "camera", "unavailable", 1))
        await rig.clock.settle()
        out["TaskResult"].append(await rig.task)
        out["TaskInterrupt"].append(out["TaskResult"][0]["interruption"])
        rig.session.handle_resource_event(resource_event("e2", "camera", "available", 2, None))
        for value in (0.11, 0.09):
            await rig.start()
            await rig.tick(1.0)
            rig.module.finish(rig.module.usable_result(value))
            await rig.clock.settle()
            out["TaskResult"].append(await rig.task)
        out["SessionResult"].append(await rig.session.finish())
        out["SessionResult"].append(rig.adapter.published[0]["summary"])
        return out

    return asyncio.run(scenario())


def test_session_outputs_pass_the_model_validation_and_the_status_rules():
    out = session_outputs()
    assert [r["outcome"] for r in out["TaskResult"]] == ["interrupted", "completed", "completed"]
    for name, parser in PARSERS.items():
        for d in out[name]:
            assert parser(d).to_dict() == d
    assert out["SessionResult"][0]["status"] == "change_noticed"
    assert out["SessionResult"][0]["signs"][0]["confirmed"] is True


def schema_validator(name):
    jsonschema = pytest.importorskip("jsonschema")
    pytest.importorskip("referencing")
    yaml = pytest.importorskip("yaml")
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    uri = "https://contract.invalid/mvp.openapi.yaml"
    doc = yaml.safe_load((CONTRACT / "mvp.openapi.yaml").read_text("utf-8"))
    registry = Registry().with_resource(uri, Resource(contents=doc, specification=DRAFT202012))
    return jsonschema.Draft202012Validator({"$ref": "%s#/components/schemas/%s" % (uri, name)}, registry=registry)


def test_session_outputs_validate_against_the_openapi_schemas():
    out = session_outputs()
    for name, items in out.items():
        validator = schema_validator(name)
        for d in items:
            validator.validate(d)


def test_our_models_agree_with_the_schemas_on_every_covered_fixture():
    for fixture in COVERED:
        name = "ClientResourceEvent" if fixture["schema"] == "ClientEvent" else fixture["schema"]
        validator = schema_validator(name)
        accepted_by_schema = validator.is_valid(fixture["data"])
        try:
            parser_for(fixture)(fixture["data"]).to_dict()
            accepted_by_models = True
        except ValueError:
            accepted_by_models = False
        assert accepted_by_models == accepted_by_schema == fixture["valid"], fixture["name"]
