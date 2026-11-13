# Parent call screens: motion

Source: the Claude Design file "Client Call Video" (`client-call-video.html`, readable source in
`unpacked/client-video.jsx`). The written motion spec was not supplied with the task, so this file is
taken from the design's own code. Replace it with the original text if that differs.

In the design video, every step starts at a fixed time. In the app, every step starts when
`model.screen` changes (see `ios/ParentCallKit/UI_BINDING.md`). Only the durations and curves below
carry over.

## Three curves

| Name | Curve | CSS equivalent | Default length | Used for |
|---|---|---|---|---|
| enter | ease-out cubic, `1 - (1 - t)^3` | `cubic-bezier(.33, 1, .68, 1)` | 0.6 s | things appearing: card, self-view, End, Done text |
| pop | ease-out back, overshoot 1.70158 | `cubic-bezier(.34, 1.56, .64, 1)` | 0.5 s | the Done check mark |
| glide | ease-in-out cubic | `cubic-bezier(.65, 0, .35, 1)` | 1.0 s | screen crossfades, card content, self-view size, avatar settle |

Subtitle words fade with ease-in-out sine, computed per frame (see below).

## Rules

- Crossfades only. Nothing slides in. The video's small entrance offsets (card 40, self-view 60,
  panel 12, Done text 14) are dropped in the app.
- Two loops only: the Accept button pulse with its halo while ringing, and the Listening bars.
- The avatar does not "breathe" after it settles.

## Steps

| Step | Trigger in the app | Motion |
|---|---|---|
| Ringing | `.ringing` | Accept scales `1 + 0.06 * max(0, sin(5t))`. Halo ring from 88 to 88 × 1.7, opacity 1 → 0, period 1.11 s, 2 pt `rgba(43,162,70,.8)` |
| Accept tap | tap | Accept button scale 0.9 for 0.3 s |
| Answer | `.ringing` → `.inCall` | Ring screen fades out, glide 0.6 s. Call fades in, glide 0.7 s. Avatar scale 1.08 → 1.0, glide 0.7 s |
| Self-view and End appear | `.inCall` | opacity 0 → 1, enter 0.7 s, 0.3 s after the call fades in |
| Card appears | first non-nil `card` | card and top bar opacity 0 → 1, enter 0.7 s |
| Card content changes | card identity changes (line id, question number, task name) | crossfade, glide 0.5 s. The card never goes empty |
| Subtitles | `.assistant` / `.question` | whole text laid out first. Word i: opacity 0 → 1 over 0.7 s, ease-in-out sine, from `subtitle.wordStart(i)` |
| Listening | `listening == true` | 5 bars, height `10 + 18 × (0.5 + 0.5 sin(7t + 1.3i))`, width 5, gap 4, `#7FD08F` |
| Timer ring | `.running` | progress follows the clock to `endsAt`, no easing. `.finished`: ring and check turn `#7FD08F` |
| Self-view grows | `selfViewLarge` | 150 × 216 → 200 × 266 and the card lifts 44 pt, glide 0.8 s, both ways |
| Progress dots | `stage` changes | width 10 ↔ 28 and colour, glide 0.5 s |
| Done | `.done` | call fades out, glide 0.6 s. Done fades in, glide 0.6 s, 0.2 s later. Check circle scale 0 → 1 and stroke draws, pop 0.6 s, at +0.5 s. Text opacity, enter 0.7 s, at +0.9 s |
