"""Check the review draft without contacting providers or reading credentials."""

import base64
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec


BACKEND = Path(__file__).resolve().parents[1]
PROPOSALS = BACKEND / "proposals"
contract = yaml.safe_load((PROPOSALS / "mvp.openapi.yaml").read_text())
script = json.loads((PROPOSALS / "script.en.json").read_text())
fixtures = json.loads((PROPOSALS / "fixtures.json").read_text())


def check_local_refs(value):
    """Only in-document JSON pointers may be resolved by the validator."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref":
                assert isinstance(child, str) and child.startswith("#/"), child
            check_local_refs(child)
    elif isinstance(value, list):
        for child in value:
            check_local_refs(child)


check_local_refs(contract)
validate_spec(contract)

validators = {}
for name, schema in contract["components"]["schemas"].items():
    Draft202012Validator.check_schema(schema)
    validators[name] = Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": contract["components"]},
        format_checker=FormatChecker(),
    )

for fixture in fixtures:
    errors = list(validators[fixture["schema"]].iter_errors(fixture["data"]))
    if fixture["valid"]:
        assert not errors, f"{fixture['name']}: {[e.message for e in errors]}"
    else:
        assert errors, f"Invalid fixture unexpectedly accepted: {fixture['name']}"

    if fixture["valid"] and fixture["schema"] == "TaskResult":
        result = fixture["data"]
        baseline = result["baseline"]
        if baseline["ready"]:
            assert baseline["sessions"] >= baseline["needed"]
        reading = result["reading"]
        if reading and baseline["ready"]:
            low, high, value = reading["usual_low"], reading["usual_high"], reading["value"]
            assert low <= high
            assert reading["outside"] == (value < low or value > high)

    if fixture["valid"] and fixture["schema"] == "CaptureCommand":
        command = fixture["data"]
        if command["kind"] == "capture":
            purpose = command["purpose"]
            expected = (
                contract["x-answer-capture"]
                if purpose["kind"] == "answer"
                else contract["x-task-definitions"][purpose["task"]]["capture"]
            )
            assert command["settings"] == expected, fixture["name"]

    if fixture["valid"] and fixture["schema"] == "TaskResult":
        result = fixture["data"]
        if result["interruption"]:
            assert result["interruption"]["attempt_id"] == result["attempt_id"]

    if fixture["valid"] and fixture["schema"] == "MediaSample":
        sample = fixture["data"]
        decoded = base64.b64decode(sample["data_base64"], validate=True)
        assert 0 < len(decoded) <= contract["x-media-policy"]["max_sample_bytes"]
        if sample["format"] == "pcm_s16le_16000_mono":
            assert len(decoded) % 2 == 0 and len(decoded) <= 8000

assert not any(path.startswith("/internal/") for path in contract["paths"])
assert set(contract["components"]["securitySchemes"]) == {"DemoKey"}
for schema_name in contract["x-sse"]["events"].values():
    assert schema_name.removeprefix("#/components/schemas/") in validators

spoken_ids = contract["components"]["schemas"]["SpokenLine"]["enum"]
assert set(script) == set(spoken_ids) | {"sentence"}
for text in script.values():
    assert isinstance(text, str) and text
    assert "{" not in text.replace("{name}", "")
    assert "}" not in text.replace("{name}", "")
    for forbidden in ("detects a stroke", "diagnoses", "predicts", "medical device", "100%", "safe"):
        assert forbidden not in text.lower(), forbidden

for task, definition in contract["x-task-definitions"].items():
    assert definition["instruction"] in script
    assert definition["repeat"] in script
    validators["CaptureSettings"].validate(definition["capture"])
    assert definition["observation_period_ms"] == contract["x-interrupt-policy"]["observation_period_ms"]
    if "expected_text_key" in definition:
        assert script[definition["expected_text_key"]] in script[definition["instruction"]]
validators["CaptureSettings"].validate(contract["x-answer-capture"])

table = "\n".join([
    "| Line ID | Exact spoken text (`{name}` is substituted) |",
    "|---|---|",
    *(f"| `{key}` | {text} |" for key, text in script.items()),
])
document = (BACKEND / "ARCHITECTURE_PLAN.md").read_text()
actual = document.split("<!-- script-table:start -->\n", 1)[1].split(
    "\n<!-- script-table:end -->", 1
)[0]
assert actual == table, "Script table drifted from script.en.json"

positive = sum(f["valid"] for f in fixtures)
print(f"OpenAPI valid; {len(validators)} schemas; {positive} valid and "
      f"{len(fixtures) - positive} invalid fixtures checked; script table matches.")
