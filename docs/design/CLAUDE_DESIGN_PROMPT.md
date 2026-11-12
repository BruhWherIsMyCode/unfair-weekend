# Prompt for Claude Design (v2: family app only)

v2, 12 Nov evening. The parent's side is now an existing video-call app (WhatsApp or similar, not final), so we design no parent screens. Our app is the **family app**: the adult child receives the statuses.

Paste everything below the line into Claude Design. The result goes to Agent 1 (`docs/AGENT_1_IOS_APP.md`), who rebuilds it in SwiftUI.

---

Design a native iPhone app (iOS 17, portrait only, iPhone 15 size, 393×852 pt) for a hackathon prototype. Output one interactive HTML prototype with every screen and state below, laid out side by side and clickable in order, plus a short design-tokens sheet (colours with hex, type scale in pt and weight, spacing, corner radii, button styles) so a developer can rebuild it in SwiftUI exactly. Use only SF Pro / system fonts, SF Symbols-style icons and standard iOS components (lists, sheets, navigation bar, segmented controls). No web-only effects, no custom fonts, no 3D.

## The product

An AI assistant video-calls an older parent who lives alone, a few times a day, through the video-call app she already uses. In under two minutes it asks three check-in questions (medicine, how she feels, anything unusual since the last call), then asks her to smile, raise both arms for 10 seconds and repeat one sentence. It compares her face, arms and speech with **her own earlier calls**. If something looks different from her usual twice in the same call, it tells her adult child to call her.

**This app is for the adult child** (40s, busy, at work, checks the phone between meetings). It answers one question at a glance: "Is Mum as usual today, or should I call her now?"

## Tone

Calm, warm and personal, like a short text from a sibling who just spoke to Mum. Not a hospital app and not a security alarm: no clinical blue-and-white, no red sirens, no heart-rate graphics, no medical crosses, no charts on the main screen. Most days the app has nothing urgent to say, and it should feel quiet. The one alert state must stand out clearly but not cause panic: use a warm amber, not red.

## Hard rules for all wording

- Never use the words "stroke", "detect", "diagnose", "predict", "medical", "health check", "safe", "risk" or "100%". The product "noticed a change from her usual" and "tells you to call her".
- Every alert ends with the line "If she seems unwell, call 112." Show it in plain text, never hidden.
- The app never tells the user what condition Mum has. It says what was seen and what to do.
- Use the sample copy below as written.

## Sample people

Her name: **Ona**, 78, Vilnius. The user: her son **Tomas**. Her photo: a soft round placeholder of an older woman.

## Screens and states

1. **Lock-screen notifications.** An iOS lock screen showing three notification examples from the app (name it "Ona's calls" for now):
   - "Ona: as usual. Medicine taken." (quiet)
   - "Call Ona now. The left corner of her mouth is lower than usual." (time-sensitive style)
   - "Ona didn't answer the 18:00 call."

2. **Home, as usual (the everyday state).** Top: Ona's photo and name, and one large status card: "As usual" with the time of the last call ("Today, 09:02 · 1 min 40 s") and her own words from the call: "Medicine: taken. 'I slept well, going to the market.'" Below: "Next call at 13:00". Below that, a short timeline of today's and yesterday's calls as small rows (time, one-word status). One secondary button: "Check in now" (starts a call to her immediately).

3. **Home, change noticed (the alert state).** The status card turns warm amber and becomes the only thing that matters on the screen: "Call Ona now", then "The left corner of her mouth is lower than usual. It looked the same when she tried again." Then a 10-second clip thumbnail with a play button, one very large "Call Ona" button (green, phone icon), and below it the line "If she seems unwell, call 112." in plain text.

4. **Alert detail.** Tapping the alert opens a full screen: the clip playing large; the same text; "Call Ona" button; the 112 line; and a small, plain "What was different" section that shows, for the sign that changed, her usual range as a simple horizontal bar and today's two tries as two dots outside it, labelled "Usual" / "Today, 1st try" / "Today, 2nd try". The other two signs listed as "Arms: as usual", "Speech: as usual". No numbers bigger than this, no medical terms.

5. **After calling her.** When he returns from the call, a small sheet asks: "How was Ona?" with three choices: "She's fine", "I'm going over", "I called 112". This only records what he did; it is not a verdict.

6. **Home, no answer.** Status card (neutral, not amber): "Ona didn't answer the 18:00 call." with buttons "Call Ona" and "Try the check-in again".

7. **Home, didn't finish.** Status card: "Ona answered but didn't finish the check. Call to see how things are." with "Call Ona".

8. **Home, still learning (first days).** Status card: "Call completed. Still learning Ona's usual (3 of 5 calls)." with a small 3-of-5 progress mark and one sentence explaining why: "After 5 calls the assistant knows how Ona usually looks and sounds, and can tell you when something is different."

9. **Check-in in progress.** After "Check in now": a calm live state "Calling Ona…", then "On the call with Ona · 0:48" with a small step indicator: Questions → Smile → Arms → Sentence. No video of her, no live numbers.

10. **Ona's usual.** A screen reached from her photo: how well the assistant knows her. "Learned from 23 calls since 2 Nov." For each of Face, Arms, Speech: a simple bar showing her usual range, which gets narrower as more calls come in (show a small "range on day 3 vs today" comparison). One line: "The more calls, the better it knows her usual." This is the product's memory made visible.

11. **Setup (first launch), 3 short steps.**
    - Step 1: "Who should the assistant call?" Her name, her photo, her phone number, how she calls you ("Tomas" / "son").
    - Step 2: "When should it call her?" Three time chips (09:00, 13:00, 18:00) and the call language.
    - Step 3: "What the assistant may do." A plain list with checkmarks: "Call Ona at these times", "Ask about medicine and how she feels", "Ask her to smile, raise her arms and say a sentence", "Send you a short clip if something looks different from her usual". Then a list with crosses: "Never tells Ona anything about her health", "Never calls an ambulance or anyone but you". A button "Ona has agreed to this" to finish.

12. **Demo panel (for judges and the team only).** Opened with a hidden gesture, a semi-transparent dark sheet, small monospace numbers, clearly a developer view. Contents: a segmented control for the session label "Real / Normal / Acted"; a person picker (Alex, Danya, Fedya); for the last call, per sign: value, usual range, attempt 1 and attempt 2, "outside" highlighted; at the bottom "Baseline: 12 calls" and "2 false alarms in 31 normal sessions".

13. **"ACTED" state.** Any home or alert screen when the last call was labelled Acted: a fixed banner "ACTED – demo recording" that cannot be missed on video, also burned into the clip thumbnail.

## Deliverables

- All 13 screens and states above, in order, in one clickable prototype.
- A tokens sheet: colours (hex, including the calm, neutral and amber status colours), type scale (pt and weight), spacing scale, radii, button styles.
- Everything buildable with standard SwiftUI components.
