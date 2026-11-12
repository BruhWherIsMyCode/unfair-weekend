# Agent 3: analysis and orchestration

Updated 13 Nov 2026. The filename stays stable for existing links. You own **Python modules imported into the same backend application**, plus canonical shared contracts. The former Swift connector, on-device measuring and separate analysis HTTP service are superseded.

Read [project context](PROJECT_CONTEXT.md), [shared contract guide](SHARED_CONTRACT.md), then [the full architecture](../backend/ARCHITECTURE_PLAN.md). Work on `agent-3/connector`, folders `analysis/` and `contract/` under [AGENTS.md](../AGENTS.md).

## Own

- CallSession: authoritative answers, attempts, observation snapshots, event handling and decisions.
- ClaudeAgent/tool loop and fixed-script fallback.
- Task definitions, processing, periodic observations and final results for smile/arms/sentence.
- Interrupt rules, attempt cancellation/finalization, stale-result handling and recovery budget.
- Personal baselines, versioned metrics, comparison arithmetic, deterministic confirmation and evidence counts.
- Publication of shared schemas, script, fixtures and compatibility rules in `contract/`.

Agent 2 owns call/media/speech/avatar/delivery adapters in `backend/`. Agent 1's current task owns a local stage app. Do not implement another HTTP service or assume a real parent client is already connected.

## Boundary and tool behavior

Review [draft 0.2](../backend/proposals/README.md); publish the agreed copy in `contract/` and update its entry links. Module calls are local Python interfaces. Network contracts belong only at client/backend boundaries.

`task_module.run(context, samples, emit_observation)` consumes the active attempt's bounded input and produces one terminal `TaskResult`. It emits `TaskObservation` periodically and on material quality changes. CallSession records updates and applies interruption rules; routine observations do not invoke Claude.

Camera/microphone loss is an immediate client event. Missing input or analyzer updates has a server watchdog. On a blocking condition, stop the attempt and resolve the pending tool with an interrupted result before invoking Claude again. Recovery starts a new attempt; late events cannot revive the old one. A hang-up cancels without another conversational turn.

Tools are `say`, `ask`, `record_answer`, `run_task`, `recover_resource`, `finish_call`, as specified by the plan. `recover_resource` uses the fixed line and bounded wait. The tool handler applies the deterministic final-result rule and publishes confirmed changes through Agent 2's delivery module; model output cannot cancel confirmation.

## Task definitions to establish

| Task | Initial input proposal | Definition still needed |
|---|---|---|
| Smile | JPEG samples over 5 seconds | Face framing, observable performance, metric formula/unit, baseline comparison |
| Arms | JPEG samples: 2 seconds to raise, 10 to hold | Required visibility, timing/coverage, drift feature and normalization |
| Sentence | PCM chunks over up to 12 seconds | Assessable utterance, audio quality, speech feature and comparison |

Sampling/reporting rates and durations are engineering starting values, not measured performance. The [kickoff research](archive/PROJECT_CONTEXT_2026-11-12.md) contains older candidate ideas; test and document actual formulas before implementing numeric results. Do not present a provisional feature or unvalidated score as an illness probability. Preserve the person's own left/right semantics and test orientation.

Separate execution, input quality, performance and reading. An arm lowering in a clear recording can be a usable measurement; unavailable input cannot. Partial/interrupted observations never count as independent confirmation attempts or baseline data. Missing baseline produces null comparisons and learning, not an ordinary result.

## Build order and verification

1. Review/publish the boundary schemas, script and fixtures; use the same shapes in mocks and providers.
2. Prove one measurement on consented local input kept outside git, including an unusable case; do not assume a candidate feature separates acted behavior.
3. Implement attempt state and pure confirmation/status rules; exercise learning, repeats, interruption, hang-up and stale results.
4. Join CallSession/tools to Agent 2's local modules. Prove camera loss while a task waits, bounded stop, recovery and a new attempt, then a final family message.
5. Add remaining tasks and baseline/evidence collection. Preserve session labels and exclude acted/confirmed-change sessions from learning.

The measured-change rule requires two usable final readings for the same sign, metric version and frozen baseline. A provisional outside observation or technical resource failure alone does not confirm a change. Additional inferred alert categories are outside this fixed-script MVP.

## Open work and honest status

The Python runtime, analyzers, baselines and interrupt behavior are not implemented in this merged backend work. Schema-valid fixtures are synthetic. The real parent client, actual metric performance, deployment and final provider configuration remain work to verify. The stage app's hidden trigger is mocked behavior, not evidence that analysis generated its alert.
