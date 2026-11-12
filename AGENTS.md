# AGENTS.md

Rules for every AI agent working in this repo. Read all of it before your first change.

## What this repo is

A hackathon project for Unfair Weekend 2026 (Vilnius, 12–15 Nov). An AI avatar video-calls an older person who lives alone, asks a few check-in questions, and checks face, arms and speech against her own usual. Her family gets one plain message.

Submission deadline: **Sat 14 Nov, 20:00**. The event rule is "build it here": all code in this repo is written during the event.

Three agents build it in parallel. Each has a brief, a branch and its own folders.

## Which agent are you

Your human tells you your number. If nobody told you, ask. Do not guess.

| Agent | Brief | Branch | Folders you may edit |
|---|---|---|---|
| 1, iOS app | `docs/AGENT_1_IOS_APP.md` | `agent-1/ios-app` | `ios/App/` |
| 2, backend and calls | `docs/AGENT_2_BACKEND.md` | `agent-2/backend` | `backend/` |
| 3, connector | `docs/AGENT_3_CONNECTOR.md` | `agent-3/connector` | `contract/`, `ios/CheckInCore/` |

Humans own `main`, `docs/`, this file, the README and every other file at the repo root.

Before you write code, read in this order:

1. `docs/PROJECT_CONTEXT.md`. If it is not in the repo, ask your human for it.
2. `docs/SHARED_CONTRACT.md`
3. Your own brief.

## Branch rules

1. **Work only on your own branch.** To start, or to pick it up again:

   ```bash
   git fetch origin
   git switch agent-1/ios-app 2>/dev/null || git switch -c agent-1/ios-app origin/main
   ```

   Use your own branch name from the table.

2. **Never commit or push to `main`, and never to another agent's branch.** Never force-push.

3. **Commit small, push after every commit.** Work that exists only on one laptop is lost when the laptop dies, and your human cannot see it.

4. **Take other agents' work only from `main`.** At the start of each block of work, and whenever your human says something you depend on has landed:

   ```bash
   git fetch origin
   git merge origin/main
   ```

   Merge, do not rebase: your branch is already pushed. Do not merge another agent's branch directly unless your human tells you to.

5. **Your work reaches `main` through a pull request.** Open one as soon as a piece works, not at the end:

   ```bash
   gh pr create --base main --fill
   ```

   In the description say what works, what is mocked, what is untested and how to try it. A human merges it, or tells you to merge it. Keep pull requests small and frequent.

6. **Before opening a pull request:** merge `origin/main` into your branch, and make sure your part builds and its tests pass.

7. **`main` must always run.** It is what gets demoed, filmed and submitted. If something you merged breaks `main`, fixing that comes before any other work.

8. **Conflicts.** If you edit only your own folders there should be none. If a conflict appears in a file that is not yours, stop and tell your human. Do not resolve it by overwriting the other side.

Agent 3: the contract files and the mock session must be on `main` within the first hour. The other two agents are blocked until then.

If two agents run on the same machine, give each its own working copy so they do not switch branches under each other:

```bash
git worktree add ../unfair-weekend-agent-2 agent-2/backend
```

## When you need something from another agent

Do not edit their folder. Open a GitHub issue and tell your human:

```bash
gh issue create --title "[to agent 3] add baseline_needed to SessionResult" --body "Who asks, what, why"
```

Changes to the API, the events or the script lines always go to Agent 3.

## Rules for all work

These repeat `docs/SHARED_CONTRACT.md` §10, because they matter most.

- **Wording.** Never write "detects a stroke", "diagnoses", "predicts", "medical device", "100%" or "safe" anywhere a user, judge or reader can see it. The product notices a change from her usual and tells the family to call her. Every alert ends with "If she seems unwell, call 112."
- **No secrets in the repo.** Keys go in environment variables or an untracked `.env`. This repo becomes public at submission.
- **No recordings of people in the repo.** No video, audio or photos of teammates or testers, and no real patient data from anywhere. Saved measurement numbers are fine.
- **Keep ignore rules local.** Put a `.gitignore` inside your own folder instead of editing one at the root.
- **Golden path first.** One call, end to end, before anything else. No screen, endpoint or button the golden path does not use.
- **Honest status.** Tell your human what is built, what is built but untested, what is mocked and what is not started. Say the same in pull requests.
- **Commit messages.** Plain words, what changed and why, prefixed with the area: `ios:`, `core:`, `contract:`, `backend:`.

## Milestones

See `docs/SHARED_CONTRACT.md` §9. In short: a walking skeleton Thursday night, real measuring by Friday 12:00, feature freeze Saturday 12:00.
