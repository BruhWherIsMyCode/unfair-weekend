# Prompt for Claude Design

Paste everything below the line into Claude Design. The result goes to Agent 1 (`docs/AGENT_1_IOS_APP.md`), who rebuilds it in SwiftUI.

---

Design a native iPhone app (iOS, portrait only, iPhone 15 size, 393×852 pt) for a hackathon prototype. Output one interactive HTML prototype with every screen and state below, laid out side by side and clickable in order, plus a short design-tokens sheet (colours with hex, type scale in pt, spacing, corner radii) so a developer can rebuild it in SwiftUI exactly. Use only SF Pro / system fonts and SF Symbols-style icons, no custom fonts.

## The product

An AI avatar video-calls an older parent who lives alone. In under two minutes it asks three check-in questions, then asks her to smile, raise both arms for 10 seconds and repeat one sentence. The phone compares her face, arms and speech with her own earlier calls. Her adult child then gets one plain message: "as usual", or "the left corner of her mouth is lower than usual, call her now" with a short clip.

One app, two roles on two phones:
- **Parent** (about 75, lives alone, not tech-savvy). Her only action is tapping "Answer". After that she never touches the screen.
- **Family** (her adult child, 40s). Reads one message and calls her if needed.

## Tone

Warm, calm and familiar, like a call from the family, not a hospital app. No clinical blue-and-white, no red sirens, no heart-rate graphics, no medical crosses. The parent side should feel like FaceTime made gentler. The family side should feel like a clear text message from a person.

## Hard rules for the parent's screens

- Very large type: captions at least 28 pt, buttons at least 22 pt semibold, touch targets at least 72 pt tall. High contrast (WCAG AAA for text).
- One action per screen at most. No menus, tabs, back buttons or settings on her side.
- Everything the avatar says is also on screen as a caption.
- **She is never shown a verdict.** No scores, numbers, ticks, crosses, green/red or "good job / something is wrong" on any parent screen. The task screens, the repeat screen and the goodbye look the same whatever happens.

## Hard rules for all wording

Never use the words "stroke", "detect", "diagnose", "predict", "medical", "health check", "safe" or "100%" anywhere. The product "notices a change from her usual" and "tells the family to call her". Use the sample copy below as written.

## Screens and states

Use "Ona" as her name and "Tomas" as the son. Caller name on her phone: "Grandson Lukas" (set up by her grandchild).

### Parent role

1. **Incoming call.** Full screen. Caller name large, a soft round avatar photo placeholder, the text "Video call". One very large green "Answer" button and a smaller, clearly separate "Not now" button. Should feel like a real incoming call.
2. **In call: avatar speaking.** The avatar fills most of the screen (use a friendly illustrated or photo placeholder of a middle-aged woman's face; it is a rented AI avatar). Her own camera picture small in a corner, mirrored. A large caption bar at the bottom: "Good morning, Ona! Did you take your medicine this morning?"
3. **In call: listening.** Same layout. The caption shows the question, and a calm "listening" indicator (gentle pulsing dots or a sound wave), no timer.
4. **Setup: put the phone down.** Before the tasks: an illustration of a phone standing upright on a table and a person sitting back about an arm and a half away. Caption: "Please stand the phone up on the table and sit back, so I can see you and your arms."
5. **Framing hint (overlay state).** Shown over the task screens when her arms or face are not in view: a soft banner "I can't see your arms yet. Sit back a little." Not alarming.
6. **Task: smile.** Her own camera picture becomes large, the avatar small. A simple pictogram of a smiling face and the caption "Please give me a big smile."
7. **Task: raise both arms.** Her camera large, a pictogram of a person holding both arms straight out in front, and a big, friendly 10-second countdown ring (show it at "7"). Caption: "Raise both arms and hold them there."
8. **Task: repeat a sentence.** The sentence in very large type in a card: "The sun is shining over the old bridge." Caption: "Please say this after me." Listening indicator.
9. **Repeat attempt.** The same task screen as 6 (smile), with the caption "Let's do that once more." Nothing else changes. It must not look like an error.
10. **Goodbye.** Avatar large, caption "Thank you, Ona. Talk to you this afternoon!", then the call ends. Identical after every call.

### Family role

11. **Messages feed.** A simple list of message cards, newest first, with time. Show all five kinds stacked so we can see them:
    - **As usual** (calm, neutral): "As usual. Medicine: taken. Ona says: 'I slept well.'" — 09:02
    - **Change noticed** (the only card that stands out, using a warm amber, not red): "The left corner of Ona's mouth is lower than usual. Call Ona now. If she seems unwell, call 112." Inline video clip thumbnail with a play button (10 s), and one large button "Call Ona". — 13:05
    - **Learning**: "Call completed. Still learning Ona's usual (3 of 5 calls)." with a small 3-of-5 progress mark. — yesterday
    - **Didn't finish**: "Ona answered but did not finish the check. Call to see how things are." and a "Call Ona" button.
    - **No answer**: "No answer at 18:00." and a "Call Ona" button.
12. **Alert detail.** Tapping the change-noticed card opens it full screen: the clip playing large, the same text, a very large "Call Ona" button, and below it the 112 line repeated in plain text. No clinical wording.

### Both roles

13. **Setup (first launch).** Step 1: "Whose phone is this?" with two big choices, "Mum's or Dad's phone" and "Family phone". Step 2 for the parent phone: her name, caller name shown on her phone. Step 2 for the family phone: her name and her phone number. Minimal, two short steps.
14. **Lab switch** (inside setup, for the demo team only, visually tucked away as a small "For testing" section): pick the person (Alex, Danya, Fedya) and the session label, a segmented control "Real / Normal / Acted".
15. **"ACTED" banner state.** Any parent in-call screen when the label is Acted: a fixed diagonal or top banner "ACTED – demo recording" that cannot be missed on video.
16. **Demo overlay** (for judges, opened with a hidden gesture, semi-transparent dark panel over the in-call screen, small monospace numbers, clearly a developer view). For each of Face, Arms, Speech: the live value, her usual range as a small bar with a marker, "outside" highlighted, attempt 1 and attempt 2. At the bottom: "Baseline: 12 calls" and "2 false alarms in 31 normal sessions". This is the only place numbers appear, and only on the demo overlay.

## Deliverables

- All 16 screens/states above, in order, in one clickable prototype.
- A tokens sheet: colours (hex), type scale (pt and weight), spacing scale, radii, button styles.
- Keep it buildable in SwiftUI with standard components: no web-only effects, no complex 3D.
