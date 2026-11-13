# Shared contract

Published by Agent 3 from backend/proposals draft 0.2.0 on 13 Nov 2026, unchanged.

Files: `mvp.openapi.yaml`, `script.en.json`, `fixtures.json` (copies of `backend/proposals/`).

## Proposed deltas, NOT applied, to agree with the team

1. **Arms task.** The owner decided the person holds the phone in one hand, so the arms task is one free hand at a time held beside the face (the existing `analysis/hand_metric.py`). The script lines `setup_phone`, `task_arms` and `repeat_arms` still say "phone on a table" and "both arms". The lines and the arms capture window need a team decision before the arms task is built.
2. **Sentence module is not implemented in this version.** It is a registered stub that returns a schema-valid `failed` result with the reason `not_implemented_in_v5`. (The arms module is real since v6, see delta 3.)
3. **Arms task, one hand at a time (v6).** The arms task is built: one `arms` task (the Task enum is unchanged), one 12 s capture window (duration 12000, 200 ms JPEG samples, unchanged), two halves: 0-1 s settle, 1-5 s her LEFT hand held beside her face, 5-7 s swap, 7-8 s settle, 8-12 s her RIGHT hand. Proposed script changes:
   - New line `swap_hands`: "Now swap: hold the phone in your other hand and raise the free one." Played mid-task, 5 s into the capture.
   - `setup_phone` becomes "Hold the phone in front of your face with one hand."
   - `task_arms` becomes "When the timer starts, raise your free hand beside your face and hold it until I say swap."
   - `repeat_arms` becomes "Let's try once more. Raise your free hand beside your face and hold it until I say swap."
4. **Task definition field `cues`.** A task definition may carry `"cues": [{"at_ms": 5000, "line_id": "swap_hands"}]`: lines the call plays at that offset after capture started, without blocking sample intake. Arms has one; smile and sentence have none.
5. **Adapter `listen(question_id, timeout_s)`.** Returns the transcript of her answer after a question line played (speech-to-text on Agent 2's side), or nothing when nothing was heard within the timeout (8 s). Needed so the call driver can record her answers.
6. **Arms baseline is per hand.** The arms task is compared against a baseline for each hand: `hand_left` and `hand_right` (reading `hand_sink`, stored as `arms.hand_left.hand_sink` and `arms.hand_right.hand_sink`). The Reading reports the hand that is further outside, with `side` that hand. Two outside readings confirm only on the same direction and the same side.
7. **PC link protocol v1 (v7).** A client program measures and a server program decides, joined by a websocket (`analysis/pc/protocol.md`). Client to server: `hello`, `start_call`, `status`, `capture_started`, `reading` (one per measured frame, `format` "reading": the client's frame reading instead of a JPEG), `capture_ended`, `cancel_ack`, `said`, `answer`, `hang_up`. Server to client: `hello_ok`, `say`, `listen`, `start_capture` (with `cues` and, for arms, `phases`), `cancel_capture`, `progress`, `call_result`, `error`. A later phone client can either measure itself and send `reading`, or send pictures to a measuring program.
8. **SessionResult status `concern_raised` and field `concern` (v7).** New status when the call agent tells the family something worries it (tool `alert_family`), and an optional summary field `concern` `{"raised_by": "agent", "reason", "note"}` with reason one of `said_unwell`, `asked_for_help`, `confused_answers`, `reading_far_outside`, `other` and a note of at most 200 characters. Status order: `change_noticed` > `concern_raised` > `incomplete` > `learning` > `as_usual`. Family text: "<name> said or showed something during today's check-in that needs a look (<reason in plain words>). Please call her. If she seems unwell, call 112."
9. **Numbers to the call agent (v7).** The call agent now sees today's reading from `run_task` (`value`, `usual_low`, `usual_high`, `outside_usual`, `distance`, `side`, `unit`) and `baseline_ready`. Confirmation and the family message stay in code.
10. **Required tasks are smile and arms (v7).** The sentence task has no measuring yet; the speech build makes it required again.
