# Project context

Updated 13 Nov 2026. This is the current product context. [The architecture plan](../backend/ARCHITECTURE_PLAN.md) owns the detailed call flow; [the shared contract guide](SHARED_CONTRACT.md) identifies the interface source. The [12 Nov kickoff research](archive/PROJECT_CONTEXT_2026-11-12.md) is historical and does not override these decisions.

## 1. Product

An avatar calls an older person who lives alone, asks about medicine, feelings and anything unusual, then guides smile, arms and sentence tasks. The system compares usable measurements with that person's own earlier calls. The family receives one plain message and can call the person.

The MVP measures one requested task at a time. Periodic observations report its quality, progress and provisional measurements. Blocking observations or resource loss can stop an attempt so Claude can choose recovery, a retry, continuation with an incomplete check, or finish. There is no general background health analysis.

## 2. Claim and wording rules

- Describe a **change from the person's usual**, not an illness conclusion. Use wording such as "Ona's smile looks different from her usual. Call her now."
- The prohibited claims are listed in [AGENTS.md](../AGENTS.md). Apply them to the UI, README, pitch, video and displayed logs.
- Every alert ends with **"If she seems unwell, call 112."** Use the person's appropriate pronoun, including "he" for the Fedya stage scene.
- The avatar gives no health verdict. The goodbye is the same for every outcome when connected.
- Label acted sessions and identify mock/staged behavior. Technical feasibility and acted separation do not establish clinical performance.
- The product hands off to family; it does not automatically call emergency services. The stage app's 112 screen must not dial an emergency number.

## 3. Event and deadline

Unfair Weekend 2026, Vilnius, 12–15 Nov. Project/video submission: **Sat 14 Nov, 20:00**. Feature freeze target: **Sat 14 Nov, 12:00**. The event's build-during-the-event rule applies; declare rented providers and pre-existing research honestly.

## 4. Golden path

Operator starts call → parent answers → avatar asks questions → active task receives samples and emits observations → CallSession handles completion or interruption → Claude chooses the next allowed step → backend stores/delivers the family message → call closes.

One Python application contains the call/media/speech/delivery modules and the analysis/orchestration modules. They use local calls and in-process events. Client communication still needs explicit HTTP/SSE contracts. Camera/microphone availability and hang-up events remain active throughout the call.

The current iOS assignment is a local stage shell, defined in [its task](tasks/agent1.md). Which real parent client joins the backend is still open. An external messenger is not assumed to supply camera/audio access.

## 5. Data handling

The client sends task samples to our server while that task runs. Sentence audio goes to the task module; ordinary answers may go to ElevenLabs STT. Claude receives transcripts and structured measurements, not raw media, in this design. The old on-device-only privacy story no longer applies.

The proposed policy keeps samples temporarily for active analysis and deletes answer recordings after STT, with a five-minute upper bound. This policy is not implemented yet. Family evidence clips remain deferred (`clip_id: null`). Never commit people recordings, secrets or real patient data.

## 6. Rented components and current evidence

The Node greeting experiment has exercised real ElevenLabs TTS and Simli avatar playback. [Backend decisions](../backend/DECISIONS.md) record the observations and limitations. Avatar playback is separate from analysis and from Claude's conversation logic. Prepared voice/still playback is the planned fallback.

The Python backend, task analysis, interrupts, baselines, family delivery and end-to-end client integration remain unimplemented here. The shared fixtures are synthetic. Report each part as built, untested, mocked or not started; never infer implementation from a design document.

## 7. Measurement and evaluation

Agent 3 owns metric formulas/units, quality and performance checks, baselines and deterministic confirmation. Intermediate observations are not independent attempts. A change requires the agreed final-result rule; a missing input/baseline is not an ordinary result.

Use consented local input outside git and preserve `live`, `normal`, `acted` labels. Exclude acted and confirmed-change sessions from baseline updates. Validate normal, repeated outside, unusable, learning, interruption and cancellation paths. Counts must describe the actual version of the whole decision system.

## 8. Current open decisions

- The real parent client and how the stage scene demonstrates genuine backend output.
- Agent 3's review/publication of canonical interfaces in `contract/`.
- Validated task metrics, thresholds, sample rates and timings.
- Deployment host, final avatar mode, target language/voice and any external notification provider.

## 9. Milestones

The targets are a walking skeleton first, real measurements next, a complete joined call before adding extras, and fixes only after Saturday's noon freeze. [SHARED_CONTRACT.md](SHARED_CONTRACT.md) distinguishes these targets from working implementation. The stage scene remains defined by [the current task](tasks/agent1.md), not the older native-parent screen list.
