# Earlier check-in / Today

Status: proposal and interactive design only. No runtime, shared contract, database or iOS changes. The HTML uses animated illustrations and fictional dates; it contains no recordings. Open `index.html` directly in a browser.

## Capability

When the family opens an alert, show a short clip from an earlier check-in beside the task that prompted today's message. The family can see the same person doing the same task on two occasions, then call them. The earlier clip illustrates one source of their usual; several eligible readings still establish the measurement baseline.

Use **Earlier check-in** and **Today** in the app. “Training” suggests model training; this application learns a person's measurement range. Use “Change from his usual” for the message. Keep the existing appropriate-pronoun 112 handoff at the end of every alert.

## What exists and what this adds

- `backend/checkin_backend/link.py` collects task frames and selects alert evidence. `delivery.py` currently publishes one `clip_url`, containing available attempts at a confirmed task.
- `ClipShelf` keeps alert frames in memory for 30 minutes. A restart loses them. This does not preserve an earlier learning recording across days.
- The family app's `FrameClipView` plays that frame sequence. `AlertView` provides the report, green call button and existing secondary actions. The phone can keep downloaded alert frames in its local history.
- `baseline_snapshots`, `session_baselines` and `baseline_members` describe numeric baseline provenance. A reusable earlier video is a new capability.

The proposal adds an optional reference clip and explicit attempt metadata. It does not change the rules that decide whether to send an alert.

## Family experience

1. Open the existing amber Family alert. Its existing wording and urgency remain.
2. In the report, see two equally sized, equally framed panels: **Earlier check-in / Fri 13 Nov · 09:00** and **Today / Sat 14 Nov · 13:01**. A small “Smile check” label identifies the shared task.
3. Tap **Play together**. Both clips start at their own task start; a shared control pauses, replays and scrubs them. There is no autoplay, audio or endless loop.
4. If today's task has a second attempt, use **Try 1 / Try 2**. The earlier reference stays fixed. A single-attempt alert has a single panel label and no second-try claim. Cross-task confirmation must retain each task's own attempt count.
5. Tap the expand control for a larger comparison. A short explanation says: “His usual is learned from several check-ins. This is one earlier example.”
6. **Call Fedya** stays available in every state. Watching a clip is optional. “He's fine” records a family action and never updates a baseline.

Both panels use aspect-fit rendering, the same orientation and the same task-relative clock. The expanded smile view applies the same closer framing to both sources and keeps each complete face visible. Do not mirror, stretch time, crop a mouth or hand, or add image adjustments that exaggerate a difference. If one clip ends sooner, hold its last frame and mark it **Ended** while the other finishes. Scrubbing respects each source's original timestamps. A paired view supports comparison; it does not ask the family to interpret measurement values.

## Visual fit and checkable design contract

The reference is `ios/App/OnasCalls/Theme.swift`, `HomeView.swift`, `AlertView.swift` and `FrameClipView.swift` at `e0b1138`. Preserve the cream `#F6F3EE` page, ink `#1F1B16`, amber alert `#FCE8C4`, green action `#237A3A`, system type, rounded media corners and brown back navigation. The comparison replaces the single clip area. The existing report and actions stay in the same journey.

The HTML review canvas puts the actual report first. Outside the phone, reviewers can choose a state and switch between the report and larger comparison. At 390×844 and 360×800 it becomes a full-width app preview; at 1440×1000 it shows the phone with design notes. Controls need a visible keyboard focus and 44 px touch targets. All illustrated media and every phone action are explicitly marked as demonstration behavior. Motion starts only after a play action; the reduced-motion preference removes portrait movement while retaining explicit timeline controls.

| State | Trigger | Visible response and next action |
| --- | --- | --- |
| Ready | Both sources resolve | Dates, matching task, shared playback; call remains available |
| Loading | Clips are being fetched | Stable media frames, loading labels, playback disabled; call available |
| No earlier clip | None was retained, or it expired | Clear earlier placeholder; today's clip plays independently |
| Partial | Today's clip is unavailable | Earlier example remains labelled; report and call action remain |
| Error | Fetch/decode fails | Clear error, retry control, unchanged report and call action |
| Permission | Parent did not allow keeping a reference | “Earlier clip wasn't kept”; family cannot grant that permission for the parent |

## Reference selection and invariants

Agent 3 supplies eligibility and provenance. Backend code must not infer either from a friendly family message.

1. Match the same `person_id`, task and task-definition version; for arms also match the same anatomical hand and capture orientation.
2. Select only a completed, usable attempt that was accepted into the **frozen baseline used for today's call**. Its source timestamp must precede this call.
3. Exclude acted, interrupted, cancelled, unusable and confirmed-change attempts. An acted current session is allowed for the labelled demonstration; an acted earlier session is never a reference.
4. For the first version, choose the newest matching retained eligible attempt. Freeze this reference and its provenance when publishing the alert. Do not choose a clip by whichever creates the most visual contrast.
5. A ready numeric baseline can have no retained video. Absence of reference media does not imply “still learning”, change the alert kind, delay publication or create a second notification.
6. Preserve original attempt boundaries. The existing merged frame list does not provide adequate boundaries for the proposed Try 1 / Try 2 UI. Add boundaries at collection time; never guess them from the clip's duration.
7. Baseline snapshots remain immutable. A family's dismissal, playback or call action does not create a new numeric reading.

## Proposed interface, for Agent 3 review

Extend a family message with an optional `comparison` object. These names are illustrative and are **not published wire schemas**:

```json
{
  "comparison": {
    "task": "smile",
    "hand": null,
    "baseline_snapshot_id": "baseline_sample",
    "reference": {
      "state": "available",
      "clip_id": "reference_sample",
      "source_call_id": "earlier_call_sample",
      "source_attempt_id": "earlier_attempt_sample",
      "recorded_at": "2026-11-13T09:00:00+02:00",
      "task_definition_id": "smile_definition_sample",
      "duration_ms": 5000,
      "expires_at": "2026-11-14T13:25:00+02:00"
    },
    "current": [
      {"attempt_id": "today_try_1", "clip_id": "current_1_sample", "duration_ms": 5000},
      {"attempt_id": "today_try_2", "clip_id": "current_2_sample", "duration_ms": 5000}
    ]
  }
}
```

The example dates describe a future retained-reference policy, not today's 30-minute shelf. A missing reference has a stable reason (`not_retained`, `expired`, `consent_missing`, `no_matching_source`) and null media fields. The media response needs source timestamps or relative per-frame timestamps; the current median-period merged clip is insufficient for exact alignment. Keep the old `clip_url` for existing clients. Upsert the same `message_id` on final summary updates. A late reference must not downgrade or resend the alert.

Use the existing authenticated media route only if it can enforce person/family ownership as well as the demo key. An opaque clip ID alone is not authorization. Broader deployment needs family-scoped authorization; the current shared demo key is insufficient for separate real families. Stale history retains the report when media expires and marks each source's state independently.

## Media lifecycle and permissions

Keeping a normal call's video beyond active analysis changes the project's proposed temporary-media policy. The parent must explicitly agree to retaining a short reference and showing it to their family. Keep that permission separate from accepting a check-in call. Revocation deletes the server reference and removes it from future responses; downloaded copies require client deletion handling and already-exported copies cannot be recalled.

For a hackathon-only implementation, use an explicit demo opt-in, one bounded reference per task/hand in process memory, and the existing 30-minute lifetime from capture. Train and demonstrate within that window; recapture when expired. Do not extend expiry on reads. Do not quietly persist video or claim the earlier clip survives a restart. This bounds at-rest complexity but still needs the agreed opt-in and per-frame/person provenance.

A later product version needs an agreed retention period, revocation behavior on family devices, storage limits, encrypted storage and authenticated retrieval before it can show a reference recorded days earlier. Do not implement that broader retention policy as part of the HTML request. Nothing is uploaded by this prototype, and no people recordings belong in git.

## Smallest implementation and ownership

1. **Agent 3:** agree and publish the comparison extension and fixtures; expose an eligible source attempt from the frozen baseline, task/hand identity and actual current attempt IDs.
2. **Agent 2:** bounded opt-in reference shelf, expiry/revocation, source-aware retrieval and optional comparison attachment. Publish the alert immediately even when comparison preparation fails.
3. **Agent 1:** paired frame player, source labels, shared controls, actual attempt tabs and the missing-media states. Keep the call action visible and existing clients compatible.
4. Rehearse one eligible learning source followed by a labelled acted call, within the demo shelf's lifetime. Show the same earlier source beside both current attempts. Preserve only measurement evidence and synthetic fixtures in git.

No new screen on the parent call, numeric scoring panel, automatic emergency call, speech measurement claim or baseline-update rule is needed for this feature. The HTML's **Call 112** button only opens a labelled preview and never dials.

## Acceptance and handoff

- A joined call selects an eligible reference from its frozen baseline and displays the correct matching task/hand beside current evidence.
- Wrong-person, wrong-hand, obsolete-definition, acted-reference and cancelled sources cannot become references. Missing or expired media leaves the original alert intact.
- Both actual attempts are inspectable when present; an alert confirmed across different tasks does not invent a repeat.
- Shared playback handles unequal lengths and timestamp gaps; one failed source does not disable the other or the call action.
- Permission denial and revocation work without restarting the call; old clients still consume `clip_url`.
- Phone and keyboard checks cover 390×844, 360×800 and 1440×1000, with visible focus and reduced motion.

**Handoff: ready for design review; implementation needs Agent 3's boundary review and an agreed opt-in/retention policy.** Recommended first slice: smile comparison in the 30-minute demonstration window. Persistent reference retention and per-hand comparison follow after that slice works.

## Prototype verification

Checked the standalone file in Chromium at 1440×1000, 390×844 and 360×800. Playback, pause, scrubbing, replay, attempt selection, larger comparison, report/family navigation, six content states, retry and demonstration dialogs passed. Checked keyboard focus, product touch targets, primary text/action contrast, reduced motion and a longer profile name. No external requests or browser script errors occurred. Rendered screenshots and the ECC evidence record are kept locally under `backend/.cache/comparison-design/`. Native iOS behavior and real-media handling remain untested for this proposed feature.
