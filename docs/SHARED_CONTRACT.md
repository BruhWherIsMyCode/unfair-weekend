# Shared contract guide

Updated 13 Nov 2026. Agent 3 owns canonical interface publication. This guide replaces the kickoff on-device Swift seam, the two-service proposal and completed-task-only uploads. The agreed architecture is **one Python backend with periodic observations from the active task and explicit interruptions**.

## 1. Sources and status

| Subject | Current source | Status |
|---|---|---|
| Full flow and module responsibilities | [Architecture plan](../backend/ARCHITECTURE_PLAN.md) | Agreed architecture direction; runtime not built |
| HTTP/SSE additions and shared task payloads | [OpenAPI 0.2 draft](../backend/proposals/mvp.openapi.yaml) | Review draft; not implemented endpoints |
| Exact fixed lines | [English script](../backend/proposals/script.en.json) | Draft `mvp-en-2`, including resource recovery |
| Valid/invalid consumer examples | [Fixtures and checker](../backend/proposals/README.md) | Synthetic; schema checks pass |
| Canonical published contract | `contract/`, owned by Agent 3 | Not published on main yet |
| Client stage behavior | [Agent 1 task](tasks/agent1.md) | Local mock scene; separate from real integration |

The checked-in draft is the single current shape to review and mock against until Agent 3 publishes the agreed copy in `contract/`. Do not maintain a second handwritten set of schemas here. After publication, update these links and remove duplicate draft authority. Draft timings, input limits and metrics are not evaluated protocols.

## 2. Application and ownership

Agent 2 owns `backend/`: application entrypoint, call lifecycle, media ingress, STT/TTS/avatar adapters, templates and family delivery. Agent 3 owns `analysis/` and `contract/`: imported Python task modules, CallSession, Claude tool loop, baselines, rules and shared interfaces. Both sets of modules run inside one application; internal HTTP callbacks and service keys are unnecessary.

Agent 1 owns `ios/App/` under its [current task](tasks/agent1.md). The old `ios/CheckInCore/` measuring package is no longer required. Which real client provides parent media remains open; do not treat the stage mock as that client.

## 3. Event and task semantics

- An active task receives timestamped samples during capture. Video uses proposed JPEG samples; sentence audio uses PCM chunks. Ordinary question answers may use short file uploads for STT.
- `TaskObservation` reports quality/progress/provisional metrics about once per second, plus material quality changes. CallSession updates state/logs; routine updates do not invoke Claude.
- Client `resource.changed` reports camera/microphone availability immediately. Server watchdogs detect missing input or a stalled analyzer even if no observation arrives.
- CallSession applies the interrupt rule, stops the attempt and resolves `run_task` once with an interrupted `TaskResult`. Claude receives that tool result and current state before choosing recovery or a next step.
- Recovery creates a new attempt. Obsolete input/results cannot revive a stopped attempt. Interrupted/partial measurements do not count toward confirmation or baseline updates.
- `task.progress` is a neutral parent projection; `command.cancel` stops client work. Parent progress contains no health verdict or baseline comparison.
- Final usable readings, not periodic observations, drive the deterministic repeat/confirmation rule. Missing baselines or failed tasks remain explicit.

Exact fields, required/null values, enums, errors and replay rules live in the draft artifact and [plan](../backend/ARCHITECTURE_PLAN.md), not a competing prose interface.

## 4. Existing v0 integration jobs

The initial API jobs remain part of the golden path: health, device registration/events, call trigger/answer/decline, family feed, summaries and statistics. They are not implemented by the greeting experiment. The draft specifies the new client-events, answer upload, speech and active-task sample/finish routes; Agent 3 still needs to complete/publish the baseline v0 endpoint schemas alongside them.

The original `Message`, `Stats`, call and avatar payload sketches remain available in the [historical v0 reference](archive/SHARED_CONTRACT_2026-11-12.md) for that publication work. Its old architecture and measurement transport do not override the current guide or draft.

`SessionResult` retains the person, session label, duration, answers, signs, status and baseline counts. The current draft explicitly permits null comparison fields while learning and uses `clip_id: null` until retained evidence is agreed. `Message` keeps the family text from backend templates and a message ID; consumers must upsert that ID when a final summary follows a mid-call change. `stored` is not evidence that a family member read the message.

Legacy `/result` compatibility and the local publisher must use one persistence/deduplication operation. The server is the summary author; the parent client does not independently submit another verdict. A retained alert clip and active-task measurement input are different features.

## 5. Parallel implementation

Use shared fixtures for fake client commands, periodic observations, camera-loss interruptions and final results. Agent 3 reviews/publishes contract changes before independent network consumers implement them. Generate/derive types from that same source. No broker, separate analysis server or mandatory Swift core package is needed.

## 6. Avatar and script

Simli/ElevenLabs have been live-tested for one greeting in the Node experiment. The Python port, real playback acknowledgment and still-image/voice fallback remain work to do. Browser/web-view JavaScript does not require a separate Node backend.

The script is fixed. Claude selects allowed steps, repeats and bounded resource recovery. A `say` finishes after actual playback, not TTS generation or Simli's first speaking event. Questions/tasks, recovery captions and the unchanged goodbye are specified by the script source and example flow.

## 7. Privacy and evidence

Samples leave the parent client for active server analysis; the old on-device-only claim is removed. Temporary media lifetime/deletion is a proposed implementation requirement, not a verified current behavior. No people recordings, patient data or secrets in git. Family clips are deferred.

## 8. Compatibility and unresolved work

Review draft 0.2 rather than implementing superseded 0.1 callbacks. Shared changes include incremental task input, observations, interrupted results, resource events, cancellation/progress and two recovery lines. Actual formulas/thresholds, client behavior, deployment and runtime performance remain to be validated.

## 9. Milestones and honest status

Targets from kickoff: walking skeleton Thursday night, real measurements Friday noon, joined call and provider decisions Friday evening, feature freeze Saturday noon, submission Saturday 20:00. These are targets, not completed milestones. The [root README](../README.md) and [backend status](../backend/README.md) state what actually works.

## 10. Rules for every agent

Follow [AGENTS.md](../AGENTS.md) and [context wording rules](PROJECT_CONTEXT.md#2-claim-and-wording-rules). Describe changes from the person's usual; every alert ends with the appropriate-pronoun 112 handoff. Keep session labels and mocked/acted status visible, give no health verdict to the parent, keep recordings/secrets out of git, and complete one golden path before extras.
