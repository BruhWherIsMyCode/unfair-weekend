# Agent 1: iOS stage app

Updated 13 Nov 2026. The current implementation assignment is [docs/tasks/agent1.md](tasks/agent1.md). That task owns the stage scene, screens, controls, local assets and behavior. The [design export](design/export/README.md) owns its appearance. This brief replaces the obsolete native-parent screen list and mandatory `CheckInCore` dependency.

Read [project context](PROJECT_CONTEXT.md), [shared contract guide](SHARED_CONTRACT.md), then the current task. Work on `agent-1/ios-app`, folder `ios/App/`.

## Scope

The assigned app is a local family/operator stage shell. Its mocked alert is not produced by the real backend unless a later task explicitly adds that integration. Preserve the stage task's controls, actual phone-call behavior and fake emergency-call behavior. Identify mock/acted behavior honestly.

The real parent capture client is still an integration decision. Do not assume WhatsApp/Telegram access, implement the former two-role app or wait for an obsolete Swift measuring package.

## Future real integration

When assigned, implement the published client/backend contract: parent playback acknowledgments, active-task samples, immediate resource changes, neutral progress and cancellation; or family message upserts by ID. [The architecture plan](../backend/ARCHITECTURE_PLAN.md) describes that flow. Server-side measurements and Claude orchestration are Agent 3 modules inside one Python backend.

The current stage task remains the authority for this app's work until its scope is changed. This documentation cleanup does not replace its mock with networking or change its design.

## Rules

- Show approved backend text verbatim when a real feed is connected.
- Follow [context §2](PROJECT_CONTEXT.md#2-claim-and-wording-rules) and the task's pronoun-specific copy.
- No people media in git; keep local demo assets ignored.
- Report simulator, device, mock and real-backend verification separately.
