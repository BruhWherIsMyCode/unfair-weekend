# Backend decisions and evidence

Current as of 13 Nov 2026. [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) owns the full flow. Superseded on-device, two-service and whole-task-upload proposals are available in Git history; they are not current implementation instructions.

## Current architecture

- **One Python backend.** Agent 2 owns call/media/speech/delivery modules; Agent 3 owns imported analysis/orchestration modules and canonical contracts. There is no internal HTTP service boundary.
- **One active measurement task per call.** Task modules emit periodic observations; client resource events are immediate. CallSession applies interrupt rules, stops affected work and resolves the pending tool before Claude's next decision.
- **A fresh attempt after interruption.** Routine observations update state without a model turn. Partial/interrupted observations cannot confirm a change or enter the baseline. Late events cannot revive a stopped attempt.
- **Fixed script with bounded recovery.** English draft `mvp-en-2` includes camera/microphone recovery lines; one-second observations and JPEG/PCM input rates are engineering proposals, not evaluated protocols.
- **Explicit confirmation and delivery.** Two usable final outside readings under the agreed rule trigger a fixed family message. A final summary updates the same call/message ID. Technical failure is not a measured change.
- **One draft interface source.** [proposals/](proposals/README.md) contains review draft 0.2, exact lines and synthetic fixtures. Agent 3 still needs to review/publish the complete canonical contract in `contract/`.

The Python runtime, task analysis, baselines, interrupts, family delivery and real client integration are **not implemented** by this work. The current iOS stage task remains a separate mock. The user has requested a PR and merge for the prototype, plan and documentation cleanup.

## Runnable provider experiment

The existing browser experiment uses Node, `simli-client` 3.2.0 and ElevenLabs TTS. It is reference code to port, not another required production service. It plays a supplied short line; it does not request camera/microphone access or run a check-in.

- Raw speech input to Simli: signed PCM16, 16 kHz, mono. TTS lines are cached locally by text/voice/model/format.
- API keys stay server-side; the browser receives a temporary Simli session token.
- Session requests are capped at 90 seconds with a 20-second idle limit. The page ends after estimated speech duration plus an allowance, or when hidden. This does not prove the actual playback acknowledgment required for task timing.
- The localhost server limits request size/rate and static file access. Local credentials, recordings and generated files are ignored.

## Recorded verification

On 12 Nov the prototype build, seven mocked-provider tests and dependency audit passed. Chromium verified setup state, console output and layout at an emulated 390-pixel viewport. Mobile emulation is not iPhone media verification.

Two real greeting trials used local provider credentials. The first prepared ElevenLabs audio in 1.14 seconds and rendered the first avatar frame 2.31 seconds after connection began. A replay reused cached audio in 0.01 seconds and rendered its first frame in 2.91 seconds. Simli's first speaking event followed audio submission by 0.08/0.13 seconds. Both reached automatic completion. These are individual observations, not benchmarks, subjective listening assessments or end-to-end voice-latency measurements.

The current draft checker validates OpenAPI, 39 valid and 25 invalid synthetic fixtures, task settings, sample encoding and script-table consistency. It does not prove runtime streaming, interruption, cancellation or measurement performance.

Provider references checked on 12 Nov: [Simli SDK](https://docs.simli.com/api-reference/javascript), [Simli pricing](https://www.simli.com/pricing), [ElevenLabs speech](https://elevenlabs.io/docs/api-reference/text-to-speech/convert). These links are dated evidence, not a fresh pricing/availability check.

## Remaining verification and decisions

Actual iPhone/web-view playback, simultaneous camera capture, voice/language quality, real playback acknowledgments, sampling/measurement quality, interruption latency, deployment and family delivery remain untested. The real parent client, final avatar mode, target voice/language, optional external notification provider and consented family-clip retention policy remain open. Prepared voice/still fallback is planned but not built in this experiment.
