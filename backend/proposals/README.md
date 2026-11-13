# Historical contract draft 0.2

**Superseded transport, 13 Nov 2026:** the [new architecture decision](../ARCHITECTURE_PLAN.md) runs analyzers on the real parent client. Structured observations go to the server; the server owns policy, clocks, aggregation and final `TaskResult`. Do not implement this draft's JPEG/PCM upload routes or assume its local observation interface is the new client ingress. Agent 3 must publish a revised start/observation/stop/drain contract, including sufficient feature data for server aggregation. This folder retains 0.2 unchanged in shape for historical fixture checks; passing them does not validate the new protocol. Storage is specified separately in [DATABASE_SCHEMA.md](../DATABASE_SCHEMA.md).

Status: **review draft, not implemented**. Agent 3 owns eventual canonical publication in `contract/`. Drafts stay under `backend/` to respect Agent 2's folder permissions. The [architecture plan](../ARCHITECTURE_PLAN.md) explains the complete call, periodic observations, interruption rules and fixed example script.

Version `0.2.0-draft` supersedes 0.1: **one Python backend**, incremental input during the active task, periodic observations and early interruption results. The previous internal Node/Python HTTP routes, service key and completed-task media-fetch callbacks are removed. The Node greeting experiment remains runnable and has not yet been ported.

- `mvp.openapi.yaml`: actual client/backend HTTP additions and SSE events, plus shared schemas for local task observations, interrupts, final results and family summaries. Local module calls do not become RPC endpoints.
- `script.en.json`: exact script `mvp-en-2`, retaining earlier lines and adding `camera_unavailable` / `microphone_unavailable`. The `sentence` entry is expected input, not an extra avatar line.
- `fixtures.json`: synthetic valid/invalid examples, including camera loss/recovery, provisional observations, interrupted results, client cancellation and neutral UI progress. Tiny PCM data is generated silence for payload checks, not a person recording. Feature values have no evaluated interpretation.
- `requirements.txt`: optional pinned validation tools, separate from application dependencies.

Validate from the repo root:

```bash
python3 -m venv backend/.cache/contract-venv
backend/.cache/contract-venv/bin/pip install -r backend/proposals/requirements.txt
backend/.cache/contract-venv/bin/python backend/scripts/check_contracts.py
```

After installation this check is local: OpenAPI 3.1, schema examples, null/error/interruption cases, capture registry, sample encoding and script-table consistency. Only in-document references are allowed. It does not read credentials or call providers. Passing it does not prove runtime interruption, cancellation, media decoding, client compatibility or analyzer performance.

Review changes with Agent 3 and the client owner before implementation:

1. Module boundaries are local Python interfaces. Remove draft 0.1's internal-service API; client/network contracts still need one canonical shared artifact.
2. Active video tasks post timestamped JPEG frames; sentence tasks post PCM chunks. Whole-task MP4 uploads cannot produce observations before the task ends. Input rates, codec limits and watchdog timings are engineering starting values to test on the real client.
3. Task modules report `TaskObservation` periodically; CallSession applies interrupt policy. Camera/microphone availability is a separate immediate client event. Routine observations do not invoke Claude; an interruption resolves the pending tool before the next model request.
4. `TaskResult` adds required nullable `interruption` and the `interrupted` outcome. Interrupted attempts have no final reading, do not count as confirmation and cannot enter the baseline. Recovery starts a new attempt.
5. Client SSE adds cancellation and neutral progress. Progress excludes metrics/baseline verdicts. Replayed commands must not execute twice; obsolete samples/results cannot revive an old attempt.
6. The two recovery script IDs and `recover_resource(resource)` local tool make the bounded recovery branch concrete.
7. Carry forward the 0.1 proposals for review: null comparisons while learning, no retained family clip yet, mid-call publication/final update of one family message ID. Family consumers upsert by ID; stored does not mean read.

Agent 3 should publish the agreed schemas/script/fixtures in `contract/`, then consumers use that source and derive their types. Do not maintain divergent canonical copies. [AGENTS.md](../../AGENTS.md) assigns the imported analysis modules to `analysis/`; actual metric definitions and the real parent client still need their named owners. Existing `/api/demo/*` greeting routes do not implement this draft.
