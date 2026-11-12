# Task-based MVP contract draft

Status: **review draft, not implemented**. Owner of the eventual canonical files: Agent 3. These files stay under `backend/` because Agent 2 cannot edit `contract/` or the human-owned docs. The [architecture plan](../ARCHITECTURE_PLAN.md) explains the flow, module owners, timing, decision rules and fixed example call.

- `mvp.openapi.yaml`: the proposed HTTP additions, SSE payload, task definitions and result schemas. `x-provider` identifies which of the two services serves an operation; callers use that provider's configured base URL. It is not one combined deployed API.
- `script.en.json`: exact script text, version `mvp-en-1` in the OpenAPI metadata. All IDs match the shared draft. The `sentence` entry is expected input, not an extra avatar line.
- `fixtures.json`: synthetic valid and invalid examples. Numeric metrics are illustrative and have no evaluated interpretation. No people media, provider responses or patient data are included.
- `requirements.txt`: pinned optional tooling for validating this proposal, separate from application dependencies.

Validate from the repo root:

```bash
python3 -m venv backend/.cache/contract-venv
backend/.cache/contract-venv/bin/pip install -r backend/proposals/requirements.txt
backend/.cache/contract-venv/bin/python backend/scripts/check_contracts.py
```

After installation the check is local: it validates OpenAPI 3.1, schema examples, important null/error cases, task capture settings and the script table in the plan. Only in-document references are permitted. It does not read credentials, call providers or prove that an unimplemented provider satisfies the draft. Generate consumer types from the canonical artifact when Agent 3 publishes it; do not maintain a separate set of handwritten wire types now.

Changes requiring review against shared v0:

1. New task/answer capture routes, client commands and server callbacks; task upload is distinct from retained alert evidence.
2. Mid-call result publication and final-summary updates to the same family message ID. Family clients upsert by ID; `stored` does not mean read or externally delivered.
3. Null `usual_low`, `usual_high`, `outside` when learning; missing baseline never becomes an ordinary comparison.
4. No retained family clip in this first draft (`clip_id: null`); temporary task media has authenticated access and a five-minute maximum lifetime.
5. Task-only, turn-based capture. Background observations, continuous STT and barge-in are deferred. The question/recording/capture windows and replay limits are proposals, not evaluated timings.

Before integration, Agent 3 reviews these changes and moves the agreed schemas, script and fixtures into `contract/`; consumers then switch to that source. Canonical versions must not drift between folders. Metric formulas and thresholds, the real parent-client choice, and Agent 3's permitted analysis folder still need the named owners' decisions. Existing `/api/demo/*` greeting routes remain separate and do not implement this contract.
