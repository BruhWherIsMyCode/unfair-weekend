# Ona's Calls

Unfair Weekend 2026 hackathon project: an avatar checks in with an older person, asks a few questions and guides face, arms and speech tasks. Measurements are compared with that person's own usual; the family receives one plain message.

The agreed design is **one Python backend**, with observations from the active task and interruptions that can change the conversation. A camera failure stops the current attempt before Claude chooses recovery or the next step.

## Start here

- [Architecture and complete example call](backend/ARCHITECTURE_PLAN.md): diagrams, module responsibilities, observations, interrupts and recovery.
- [Shared contract guide](docs/SHARED_CONTRACT.md): the current draft, ownership and compatibility status.
- [Project context and wording rules](docs/PROJECT_CONTEXT.md).
- [Agent rules and folder ownership](AGENTS.md).
- [Current iOS stage task](docs/tasks/agent1.md): a separate local mock scene, not proof of backend integration.

## What works

The runnable code in `backend/` is a local **Node Simli + ElevenLabs greeting experiment**. It has been live-tested and has mocked-provider tests. It is reference code to port into the planned Python application. It does not capture the parent's camera/microphone or run a check-in conversation.

The Python runtime, task analyzers, observation/interrupt handling, personal baselines, real family delivery and client integration are not implemented in this backend. Contract fixtures are synthetic; they are not measurement evidence. The iOS stage task specifies mocked behavior separately.

To run the greeting experiment, follow [backend setup](backend/README.md). To validate the proposed interfaces without API credits, follow [contract checks](backend/proposals/README.md).

## Submission

The event is 12–15 Nov 2026 in Vilnius; project/video submission is due Sat 14 Nov at 20:00. [Kickoff research](docs/archive/PROJECT_CONTEXT_2026-11-12.md) is retained as historical material, not the current implementation plan.
