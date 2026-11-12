# Backend decisions

## 2026-11-12: small Simli + ElevenLabs prototype

The user selected a separate parent app during the architecture discussion and requested a small Simli experiment before implementing the backend golden path. This prototype is an independent browser page under `backend/`; it does not modify the shared contract or another agent's folders.

- Candidate avatar: Simli. Its API accepts speech audio and returns lip-synced video and audio. We supply speech, rather than using a free conversation model.
- Candidate voice: ElevenLabs text-to-speech. Use 16 kHz PCM and cache each generated line locally. The default test line is a standalone greeting, not a new contract script line.
- Client: pinned `simli-client` 3.2.0, bundled locally. Temporary session tokens are minted server-side. No API keys reach the page.
- Cost controls: prepare the audio first; request a 90-second session limit and 20-second idle limit; stop after the line or when leaving the page; cap token and speech request frequency.
- What was actually tried: read the current official SDK/API documentation and installed SDK implementation; installed dependencies and bundled the browser client; ran the local server and inspected the page in Chromium; exercised real ElevenLabs and Simli requests using local environment credentials.
- Verification: `npm run build` passed; all seven `npm test` cases passed with mocked providers; `npm audit --omit=dev` reported no vulnerabilities; `git diff --check` passed. Chromium displayed the missing-key setup state with Play disabled, no console warnings or errors, and no horizontal overflow at an emulated 390-pixel viewport. Mobile emulation does not verify iPhone media playback.
- Live observations: the first greeting's ElevenLabs preparation took 1.14 seconds; the first rendered avatar frame arrived 2.31 seconds after the connection step began; Simli's first speaking event arrived 0.08 seconds after sending audio. The next greeting reused cached speech in 0.01 seconds and rendered its first frame in 2.91 seconds, with a speaking event after 0.13 seconds. Both reached the page's automatic completion state. These are individual Chromium observations, not a benchmark or an audio/listening assessment; a speaking event is not an end-to-end audio latency measurement.
- Untested: subjective avatar and speech quality, target-language performance, simultaneous iPhone camera capture, and iOS web-view playback.
- Not selected yet: final avatar mode, family notification provider, or deployment host. The still-image/audio fallback required by the brief remains part of the later golden path.

Sources checked on 2026-11-12: [Simli SDK](https://docs.simli.com/api-reference/javascript), [Simli pricing](https://www.simli.com/pricing), [ElevenLabs speech](https://elevenlabs.io/docs/api-reference/text-to-speech/convert).

## 2026-11-12: proposed adaptive conversation architecture

The user requested a reviewable plan in which Claude leads the conversation while audio/video analyzers run in parallel and publish relevant observations. [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) proposes a coordinator for each call, pushed observation events, bounded Claude tools, command acknowledgments, and explicit interruption/notification rules. Its initial on-device assumption was superseded by the late-evening shared-contract change: analysis runs on the server and Agent 3 owns analysis and the Claude orchestrator. Agent 2 supplies the call/media/notification backend.

Status: design proposal only. The existing greeting demo is unchanged. The Claude conversation loop, analyzers, mid-call commands, notification handlers, and Speech Engine integration have not been implemented or tested. The user requested no pull requests for these experiments; the prototype PR was closed and further work stays on the backend branch unless instructed otherwise.

Clarification after the user's review: CallSession and session coordinator are one logical component. ActionExecutor is a name for ordinary tool-handler functions, not a required separate service. Tasks share one protocol with task-specific rules and readings. The initial `run_task` tool can await a final structured result while progress updates remain internal to CallSession and the app. Execution completion, usable capture, and the measured finding are independent facts.
