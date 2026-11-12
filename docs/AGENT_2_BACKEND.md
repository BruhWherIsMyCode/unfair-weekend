# Agent 2: backend and calls

You build everything that is not on the phone.

Read first, in this order: `PROJECT_CONTEXT.md` (§2, §4, §6, §7), then `SHARED_CONTRACT.md`. Where they disagree, `SHARED_CONTRACT.md` wins. Nothing described here exists yet.

## Mission

- Make the parent's phone ring, and notice when she does not answer.
- Give the app a face and a voice for the scripted lines (the rented parts).
- Turn the result of a call into one plain message and deliver it to the family.
- Keep the session log that produces the false-alarm count.

## You own

- `backend/`: the server, the avatar web page (if the live mode is used), message templates, the operator's trigger page, deployment.
- All accounts and API keys for rented services.

## You do not own

- The measuring and the decision to alert. The phone decides; you receive `SessionResult` and act on it. Do not re-judge the numbers.
- `contract/` (Agent 3) and `ios/` (Agents 1 and 3). The API in `SHARED_CONTRACT.md` §4 to §6 is the starting point. If you need it changed, open a GitHub issue titled `[to agent 3] ...` and tell your human (see `AGENTS.md`).

## First 90 minutes: three decisions

Write each outcome in `backend/DECISIONS.md` with what you actually tried. Mark anything you did not try as untested.

### 1. Avatar and voice

This is hand test 2 from context §7. Candidates are in context §6: Tavus, HeyGen LiveAvatar, Anam, Simli for the face; ElevenLabs for the voice. HeyGen and ElevenLabs sponsor the event, and using ElevenLabs puts us in line for its prize.

Find out, by trying a free tier:

- How long is the delay before it speaks?
- Can it be told to say an exact line? Our call is scripted; we do not want free chat.
- Does it speak the language the team picks? Lithuanian quality is unchecked.
- Can it run inside an iOS web view, or does it ship an iOS SDK?
- What are the free-tier limits? We need roughly 100 short calls over the weekend.

Then choose one of the three modes in `SHARED_CONTRACT.md` §6. Build `still` first whatever you choose: a photo plus ElevenLabs audio for each line. It is the fallback named in context §6, it needs no live connection during the call, and it already counts as an ElevenLabs project. [proposal] If the live avatar lags or is fragile, `clips` (pre-rendered avatar video per line) gives a face with no delay.

### 2. What Twilio is for

The team's plan says "calls through Twilio, or an alternative". My reading, all **[unverified]**, check before relying on it:

- The avatar services bring their own video transport. Twilio does not carry the avatar unless we bridge the two ourselves, and that is more than a weekend allows.
- Twilio can realistically do two things for us:
  - **The message to the family as an SMS** with a link to the clip. Trial accounts may only send to verified numbers and add a prefix to the text. Picture messages (MMS) are generally not available in Europe, hence a link.
  - **A real ring on a locked phone**, through the Twilio Voice SDK with Apple's CallKit and VoIP push. This needs a paid Apple Developer account and push credentials, and it is audio only.
- **[proposal]** Golden path: the ring is an event to the open app (`call.incoming`), and the family message goes both to the Family screen in the app and as a Twilio SMS. Treat the real ring as a stretch goal after M3.
- If Twilio sign-up, number purchase or sender rules cost more than an hour, stop. Alternatives: a Telegram bot (can send the clip itself), or the in-app Family screen alone. The demo works without SMS.

### 3. Where it runs

The phones must reach the backend over HTTPS from the venue wifi and from a phone hotspot. Venue network reliability is unchecked (context §6). Choose a tunnel from a laptop or a small cloud host, and keep the other as a backup. Post the base URL and the demo key to your human for Agents 1 and 3 as soon as `GET /health` answers.

## Stack [proposal]

Whatever you are fastest in. One process, one SQLite file. No queues, no microservices, no user accounts. Seed one family and the three teammates as persons.

## Order of work

1. **Tonight, for M1:**
   - `GET /health`, `POST /devices`, the event stream.
   - `POST /calls`, plus a trigger page with one button, "Call now", for the demo operator.
   - `POST /calls/{id}/answer` returning `still` mode, with audio for every line in `contract/script.<lang>.json`, generated once and cached. Lines containing `{name}` are generated per person.
   - `POST /calls/{id}/result` → write the message → `message.new` to the Family phone.
   - Agent 3 puts a curl script in `contract/fixtures/`. The whole flow must pass with it, without the app.
2. **Fri morning, for M2:** clip upload and `GET /clips/{id}`. No-answer timeout → `no_answer` message. `GET /stats`. Message templates with tests (below).
3. **Fri afternoon and evening, for M3:** SMS. The chosen avatar mode, with its measured delay written down.
4. **Sat morning:** survive a restart without losing data. A reset command that restores a clean demo state. An export of the session log as CSV, for the team's evidence numbers. A dry run on the hotspot.

## Message templates

You alone write the text the family reads. Keep all templates in one file per language so the wording can be reviewed in one place.

| `kind` | Text [proposal] |
|---|---|
| `as_usual` | "As usual. Medicine: taken. {name} says: '{feeling}'." |
| `change_noticed`, face | "The {left/right} corner of {name}'s mouth is lower than usual. Call {name} now. If she seems unwell, call 112." |
| `change_noticed`, arm | "{name}'s {left/right} arm sank more than usual while holding both arms up. Call {name} now. If she seems unwell, call 112." |
| `change_noticed`, speech | "{name}'s speech sounds different from usual. Call {name} now. If she seems unwell, call 112." |
| `learning` | "Call completed. Still learning {name}'s usual ({n} of {needed} calls)." |
| `incomplete` | "{name} answered but did not finish the check. Call to see how things are." |
| `no_answer` | "No answer at {time}." |

Rules, each backed by an automated test:

- Every `change_noticed` text ends with the 112 line. The pronoun in that line follows the person's setting; prefer the name elsewhere.
- No template contains "stroke", "detect", "diagnos", "predict", "medical device", "100%" or "safe". Messages describe what was seen and what to do, never a condition.
- An alert is sent only when `status` is `change_noticed` and at least one sign has `confirmed: true`. Anything else that claims to be an alert is rejected and logged.
- [proposal, for the team] If her answer to "anything unusual since the last call?" is not a no, lead the `as_usual` message with her words. Context §9 gives the reason for that question.

## Privacy and safety

- Clips show a person's face in a health context. Unguessable ids, no directory listing, deleted after the event.
- Store numbers and answers, not video. The only video you ever receive is an alert clip.
- Keys in environment variables, never in the repo. The repo will be public.
- The backend contacts the registered family only. It never calls an ambulance or anyone else (context §5, permission model).
- Sessions labelled `acted` stay labelled in the log, the stats and the export.

## Done means

- The curl flow passes against the deployed URL.
- A call that is not answered produces `no_answer` on the Family phone.
- An alert reaches the Family phone, in the app and by SMS if SMS was kept, within a few seconds, and the clip link plays.
- `still` mode works with the avatar service switched off.
- `backend/DECISIONS.md` says which rented parts are used, so the team can declare them on stage.

## Risks to raise early

- Free-tier limits running out on Saturday. Cache every generated line.
- Venue network. Rehearse on the hotspot before Saturday.
- Twilio sender rules for Lithuanian numbers. Unknown; test with a teammate's phone tonight or drop SMS.
