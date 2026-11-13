# Shared contract

Published by Agent 3 from backend/proposals draft 0.2.0 on 13 Nov 2026, unchanged.

Files: `mvp.openapi.yaml`, `script.en.json`, `fixtures.json` (copies of `backend/proposals/`).

## Proposed deltas, NOT applied, to agree with the team

1. **Arms task.** The owner decided the person holds the phone in one hand, so the arms task is one free hand at a time held beside the face (the existing `analysis/hand_metric.py`). The script lines `setup_phone`, `task_arms` and `repeat_arms` still say "phone on a table" and "both arms". The lines and the arms capture window need a team decision before the arms task is built.
2. **Arms and sentence modules are not implemented in this version.** They are registered stubs that return a schema-valid `failed` result with the reason `not_implemented_in_v5`. Only the smile task measures.
