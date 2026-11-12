# Agent 2: calls, media and delivery

Updated 13 Nov 2026. You build modules in **one Python backend**, alongside Agent 3's imported analysis/orchestration modules. Read [project context](PROJECT_CONTEXT.md), [shared contract guide](SHARED_CONTRACT.md) and [the full architecture](../backend/ARCHITECTURE_PLAN.md).

Work on `agent-2/backend`, folder `backend/`. Agent 3 owns `analysis/` and `contract/`; Agent 1 owns `ios/App/`. Follow [AGENTS.md](../AGENTS.md) and coordinate shared boundary changes through Agent 3.

## Own

- Application entrypoint, health/configuration and deployment.
- Call IDs, ring/answer/no-answer, client events, deadlines and connection cleanup.
- Playback/capture commands, acknowledgments, cancellation and neutral progress.
- Bounded active-task sample ingress, ordering and routing to the current attempt.
- ElevenLabs STT/TTS adapters, prepared line cache, Simli session setup and voice/still fallback.
- Fixed family templates, one stored message per call, family feed/events and delivery records.

## Collaborate through local interfaces

CallSession, Claude, task modules, baselines and final-result rules belong to Agent 3. They call the call/speech/delivery modules directly. There is no second service, inter-service key, generic HTTP action executor or Node-to-Python callback seam.

CallSession owns conversation state; call service owns connection/command state. Resource loss must reach CallSession immediately. Media ingress bounds the stream and reports input loss; it does not reinterpret readings or decide a health finding. Parent samples go only to the active attempt.

## Existing implementation

[backend/README.md](../backend/README.md) runs the local Node greeting experiment. It has live-tested Simli/ElevenLabs integration, an audio cache, session limits and mocked-provider tests. Port those adapters into Python rather than deploying Node as another required service. Do not claim the Python application exists because the experiment works.

## Build order

1. Agree on the [current contract draft](../backend/proposals/README.md) and complete publication with Agent 3.
2. One Python entrypoint: health, registration/event stream, trigger, answer/decline and no-answer handling.
3. Fixed-line playback with actual completion, answer recording/STT and fallback voice.
4. One active smile task: incremental samples, periodic neutral progress, immediate camera loss and cancellation. Join it to Agent 3's attempt handling before arms/speech.
5. Store/deliver one family message, including mid-call publication and final update by the same ID. Retained clips and external SMS are separate later work.

The real parent client is not yet selected/joined. The current iOS task is a stage mock; do not present it as an end-to-end backend test.

## Verification and completion

Keep provider keys server-side. Run build/tests for the experiment and schema/fixture checks for the draft. For the Python implementation, prove one joined call, a missing-resource interrupt, bounded cancellation, a fresh retry, no stale result after cancellation and one family message. Record what is mocked or untested.

Every alert uses a fixed template and ends with the appropriate-pronoun 112 handoff. The avatar gives no result to the parent. `stored`/provider acceptance is not proof a family member read a message. Update [backend decisions](../backend/DECISIONS.md) with actual provider/deployment evidence.
