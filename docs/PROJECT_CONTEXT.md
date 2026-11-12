# Project context: AI check-in video call for an elderly parent, with a stroke-signs check

Written 12 Nov 2026 (kickoff evening) for the team of three and for the AI assistant that will structure the project.
Everything here is research, design and pitch preparation. **No code exists yet.** Nothing in this file has been built or tested.

How to read the labels:
- **[full text]** = the source was read in full.
- **[abstract]** = only the summary of the paper was read.
- **[home page]** = only the company's own home page was read; figures are the company's own claims.
- **[unverified]** = reasoning or memory, not checked against a source.
- **[proposal]** = a suggestion for the team to accept, change or drop.

---

## 1. The product in one paragraph

An AI avatar video-calls an elderly parent who lives alone, several times a day. In under two minutes it asks a few check-in questions (medicine taken, how she feels), then asks her to smile, raise both arms for 10 seconds and repeat a sentence. The software measures her face, arms and speech and compares them with **her own earlier days**. Her adult child then gets a message: "as usual", or "the left corner of her mouth is lower than usual, call her now", with a short clip.

- **User:** an older person living alone. She only has to answer the call.
- **Payer / second user:** her adult child (family plan). At scale: councils, insurers, care homes.
- **Why it exists:** it covers the days nobody calls her. People who live alone reach hospital later after a stroke (figures in section 9).
- **What is ours:** the measuring, the personal baseline and the decision to alert. The avatar and the voice are rented services, and we say so.

## 2. Claim rules (apply to the UI, the README, the video and the pitch)

These come from the organisers' guides, the Kilo track brief and EU medical-device law (section 8).

Never say:
- "detects a stroke", "diagnoses", "predicts", "medical device", "100%", "safe".

Say instead:
- "It checks the three signs an ambulance crew checks (face, arm, speech), against her usual, and tells the family to call her."
- "It noticed a change. She looks or sounds different from her usual."
- "It does not catch every stroke. It ends the day when nobody noticed."
- "We show it is technically possible. Clinical testing with a stroke unit is the next step."

Must be present in the product:
- Every alert message ends with a hand-off line: **"If she seems unwell, call 112."**
- The avatar never tells her a health verdict.
- The demo and video state which parts are real, which use acted or sample data, and which are unfinished.
- Only our own or consented recordings. No real patient data.

## 3. The event

**Unfair Weekend 2026** (unfairweekend.com), a "vibecoding" hackathon at ISM University, Gedimino pr. 7, Vilnius.

Schedule:
- Thu 12 Nov, 18:00: kickoff, build starts.
- Sat 14 Nov, 20:00: **project and video demo due, submitted through Devpost.**
- Sat 14 Nov, 20:30: public vote opens on /voting. The five most-voted teams also pitch.
- Sun 15 Nov, from 12:00: finalists pitch live.

How finalists are chosen: pre-judges review every submission (the video) and pick the finalists. The stage comes only after that. So **the video is what gets us into the final.**

Rules (the site's "6 quick rules" and Terms, read 12 Nov):
- "Build it here": start from scratch at the event and disclose pre-existing work. Ours to disclose: research, design and pitch notes only.
- Teams of up to 6.
- The team is responsible that the project, demo and materials are lawful and respect privacy.
- Follow published rules on third-party assets, AI tools, open-source licences and attribution.
- No rule bans health projects. The organisers list healthcare among the fields worth building in.

Judging criteria, in the site's own words (weights unknown):
1. **Technical Execution:** "Does it work, and does the build show real technical effort?"
2. **Product Design:** "Easy to use, and a complete product, not just a tech demo?"
3. **Potential Impact:** "Does it solve a real problem for a clear group of people?"
4. **Originality:** "Is the idea creative and different from what already exists?"

Judges: Vytautas Butkus (Vinted, product), Miks Mikelsons (Mistral), Domantas Patinskas (Kilo, business), Karolis Valaika (CyberCare, AI labs), Eigirdas Žemaitis (ISM).
Pre-judges: Vaida Bitvinske (organiser), Domas Bitvinskas (Supercorp), Kipras Vaicekauskas (Practica Capital), Eigirdas Žemaitis.

Prizes: 1st €4,000 + $10k AWS credits; 2nd €2,000; 3rd €1,000. Special prizes in reach: **Kilo Co-Found track**, **Best ElevenLabs project**, **Best Demo**. HeyGen and ElevenLabs are sponsors.

### What the organisers say wins (their "how to win" guide)
- "A narrow, complete workflow beats a broad product full of dead buttons." Pick one golden path and build it end to end first.
- "Do not bury the hard part in an architecture diagram. Put it in the demo."
- One listed kind of real difficulty: "making a system recognize uncertainty and hand control to a human". That is our product.
- Evidence ladder, strongest first: a real user repeatedly used it → **a real user completed the core workflow** → pilot commitment → ... → "friends said the idea sounded cool".
- "Imagine the strongest 60 seconds of your demo first", then build the minimum that makes it real.
- "Test the product with someone who has not heard the pitch. Stay quiet. Watch where they hesitate."
- "Prepare a backup recording without replacing the live demo."
- Listed ways to lose: "Invisible Technical Work" (hard work the judges never see) and "Fake Enterprise" (claiming hospitals as customers without understanding the regulations).

### What the organisers say about products like ours ("what to build" guide)
"A therapist, symptom checker, legal adviser, or financial adviser can cause real harm while still looking convincing in a demo. If you work near a high-stakes decision, make uncertainty visible, cite sources, limit what the product claims to do, and design a clear handoff to a qualified human."

### Kilo Co-Found track brief
Kilo wants AI-first consumer products where an agent owns an ongoing job, and names family and ageing as a category. A strong prototype shows:
- a specific user with a recurring, painful job;
- an agent that acts, not one that only answers;
- persistent memory that visibly improves personalisation (ours: her baseline);
- a clear permission and safety model: what the agent may do alone and what it asks first;
- at least one integration or realistic data source;
- a measurable outcome beyond engagement;
- a credible path to revenue (subscription, family plan);
- a retention loop: why it is more useful after 30, 90 and 365 days (ours: the baseline gets better with every call).

Kilo is **not** looking for: "Medical diagnosis or treatment claims without proper clinical safeguards", "Generic AI chatbots with health branding", "Wellness advice without measurable outcomes".
Kilo also says: "Health data is sensitive. Use fictional or consented sample data during the hackathon, and say clearly in your demo which parts are real, which use sample data and which are unfinished."

## 4. The golden path (build this first, end to end)

1. The parent's device rings with an incoming video call. The caller name is set by her grandchild.
2. She answers. The avatar greets her by name.
3. Scripted check-in questions: Did you take your medicine? How do you feel? Anything unusual since the last call?
4. Three tasks. The avatar shows each one first:
   - smile;
   - raise both arms and hold for 10 seconds;
   - repeat one set sentence.
5. The software measures each task and compares with her stored baseline.
6. If a sign is outside her usual, the avatar asks her to repeat that task. **An alert needs the sign twice in the same call.**
7. The child's phone receives one plain message:
   - "As usual" with the check-in answers; or
   - "The left corner of her mouth is lower than usual. Call her now. If she seems unwell, call 112." plus a short clip.
8. If she does not answer: the child gets "no answer".

Whole call: under two minutes.

### In scope
- One call screen in the browser, the three tasks, the baseline, the decision, the message to the child.
- A visible count of false alarms across normal sessions.

### Out of scope
- Real phone dialling, scheduling, a native app, a dashboard.
- Free conversation with the avatar beyond the scripted questions.
- Any other medical condition. One measured sign beats five claimed ones.
- A real WhatsApp or Telegram integration. The channel may be staged (our own call screen), and we say so. A program cannot place a WhatsApp video call today: Meta's business calling is voice only, with video listed as planned (docs read 10 Nov).

## 5. The measuring: what to build [proposal, untested]

No public stroke model exists (Hugging Face searched 10 Nov). Build from general parts and compare each person with herself.

**Face**
- Google MediaPipe Face Landmarker: 478 face points and 52 expression scores, runs in the browser.
- Measure the mouth corners during the smile: height of the left and right corner against the eye line, scaled by the distance between the eyes. Also compare the left and right smile scores.
- [unverified] Whether this picks up a one-sided droop clearly has not been tested. **This is the first thing to test tonight.**

**Arms**
- A body-pose tracker (MediaPipe Pose is the obvious candidate, [unverified]) for wrists, elbows, shoulders.
- Measure whether one wrist sinks relative to the other during the 10 seconds.
- Practical problem: she cannot raise both arms while holding the phone, and a phone held close does not show the arms. The device must stand on something, and she must sit back. Solve this in the design (the avatar asks her to put the phone down) or the arm task fails with real users.

**Speech**
- She repeats one set sentence. Transcribe it and count how far the transcript is from the expected words, against her own usual.
- [unverified] Modern speech-to-text is trained to understand unclear speech and may "repair" a slur, so the transcript alone may show nothing. Consider also timing (how long the sentence takes, pauses). Test with an acted slur early.
- Speech is the weakest of the three signs in the research (section 8).

**Baseline and decision**
- Store each session's numbers per person. The baseline is her usual range from earlier sessions.
- Flag a sign when it falls outside that range; ask her to repeat; alert only if it happens twice.
- Keep the decision a plain, explainable rule. Show the numbers on screen in the demo: this is the "visible hard part".
- [proposal] Run the measuring in her browser so video does not leave the device except for the alert clip. This gives a simple privacy story.

**Lesson from the research for our own testing:** in the hospital study (section 8) one model learned to recognise white clothes and hospital beds instead of the patients. Record normal and acted sessions in the same place, same light, same clothes, or the numbers mean nothing.

**Permission and safety model [proposal]** (Kilo asks for one)
- The agent may do alone: call at the agreed times, ask the questions, measure, store the numbers, send "as usual", send an alert with a clip.
- The agent never does: tell her a diagnosis, call an ambulance, contact anyone outside the family.
- Agreed at setup by her and her family: who receives messages, and that an alert includes a clip.

## 6. Rented parts (declare them on stage)

Facts from the companies' own home pages, read 10 Nov. None was tried.
- **Real-time avatars:** Tavus (avatars that "see, hear, respond", developer API, free start); HeyGen LiveAvatar (bring your own voice stack; HeyGen is an event sponsor); Anam (1.24 s median reply by its own benchmark); Simli (from $0.009 per minute, free start).
- **Voice:** ElevenLabs Conversational AI ("90+ languages"; JS, React, Python SDKs). Using it puts us in line for the ElevenLabs prize.
- **Not checked:** reply delay in practice, Lithuanian quality, free-tier limits, venue network reliability.

Fallback if the avatar service fails or lags: the same call with a still face or photo and a voice.

## 7. Evidence to produce this weekend

**Own numbers**
- False alarms across about 30 normal sessions of the three of us. This is real data and is the headline number.
- Detection across about 30 sessions with an acted droop and slur. **Label it "acted" everywhere.**

**Real-user test, Friday afternoon** (only once the call works end to end)
- One teammate, capped at one hour. 3–5 people aged about 60+, ideally a senior professor at ISM.
- Find them through a mentor or the front desk. Do **not** ask Eigirdas Žemaitis (judge and pre-judge) or the organiser (pre-judge).
- Introduce it as "a student prototype, not a health check". Give no health verdict.
- Ask on camera for permission to show the clip.
- Record: did they answer unaided? did they finish the three tasks? how long did it take? one quote.
- This is rung 2 of the organisers' evidence ladder, and it answers "will older people use it?" inside the video, where the pre-judges cannot ask.

**Two hand tests, not yet reported done**
1. Google's public Face Landmarker demo: mimic a one-sided droop. Do the left and right mouth scores clearly differ?
2. One avatar service's free demo: how long is the reply delay, and does it speak the language we need?

## 8. Medical and legal reality (read before writing any claim)

**What holds**
- Face, arm and speech (FAST) is the check ambulance crews use. A first-aid review pooled it at about 86% of strokes caught (Cureus 2020, PMID 33312787; keyword-read only; the review rates its own evidence as moderate to very low certainty).
- A phone camera can pick up these signs in patients. Ou et al., Neural Regeneration Research 2025, PMID 38767488 **[full text]**: 132 stroke patients and 121 healthy people filmed on mobile phones in hospital; best model caught 86.3% of patients and wrongly flagged about 1 in 5 healthy people (specificity 78.9%). The authors call the sample small.
- FAST.AI app (American Heart Association newsroom, 2023): about 270 stroke patients in Bulgaria; face asymmetry found in "nearly 100%", arm weakness in more than two thirds, speech module not validated. A conference abstract, not peer-reviewed; neurologists ran the tests.

**Where real life differs**
- A stroke is confirmed only by a brain scan in hospital. Software sees outward signs, not the stroke.
- About 1 in 7 strokes (14.1% of 736 patients) shows no face, arm or speech sign; they bring balance or vision trouble instead (Stroke 2017, PMID 28082668 [abstract]).
- About 1 in 5 suspected strokes is something else: seizure, migraine and others (Eur J Emerg Med 2019, PMID 29727304 [abstract]).
- Every study found compares known patients in hospital with healthy people. None found on daily checks of healthy old people at home, and none using a personal baseline. The searches were narrow, so this is "not found", not "does not exist". That part is ours and untested.
- Strokes are rare and the calls are daily, so nearly every alert will be false [unverified reasoning]. This is tolerable only because an alert costs one phone call from a child, not an ambulance.
- We have no patient recordings. Ours is tuned on an acted droop.
- Do **not** cite PMID 40210979 (stroke from face images): retracted.

**Law**
- EU Medical Device Regulation 2017/745: software intended to diagnose, monitor or predict disease is a medical device. The "intended purpose" is whatever the maker's own materials and statements say. Duties start when a product is placed on the market.
- Our reading, not legal advice: a weekend prototype is not on the market; a real product would need certification. This is why the wording in section 2 matters.

## 9. Pitch material

**Problem figures**
- Canadian stroke registry, 10,048 patients: 22.8% lived alone. Of those living alone, 28.3% arrived within 2.5 hours, against 40.0% of the others; 8.0% against 14.0% got the clot-busting drug (Stroke 2014, PMID 25139877 [abstract]).
- 23.1% of strokes are found on waking (Europe 18.5%); 37 studies, 196,744 people (Neurol Res 2026, PMID 40721293 [abstract]). This is the reason for a morning call.
- About 3 in 10 people with a mini-stroke or minor stroke did not seek help within 24 hours (Oxford, 1,000 patients; Stroke 2010, PMID 20395614 [abstract]). This is the reason for the "anything unusual since the last call?" question.
- Denmark, 479 patients: living alone roughly halves the odds of arriving early (Brain Behav 2021, PMID 34087953 [abstract]).

**"Will older people use it?" figures**
- Eurostat 2025, Lithuanians aged 65–74: 48.6% make phone or video calls over the internet (37.1% in 2021); 65.2% use the internet. No official figure exists for 75+.

**The four criteria, mapped**
- Technical Execution: the measuring runs live; the false-alarm count is on screen; rented parts are declared. Weak point: the detection evidence is acted.
- Product Design: she only answers; the avatar shows the tasks; one plain message. Weak point: untested on an older person until Friday.
- Potential Impact: the living-alone and found-on-waking figures.
- Originality: the stroke-signs check inside a routine call, against her own baseline.

**Demo scene (video and stage)**
- The same teammate takes two calls.
- Call 1, normal: the child's phone shows "as usual".
- Call 2, acted droop and slurred sentence: the avatar asks him to repeat; the alert with the clip arrives on the second phone, shown on the big screen.
- Open with the droop moment, not with the check-in questions.
- Say aloud: the droop is acted, the avatar and voice are rented, the measuring is ours and runs live.
- Include the Friday clip of a real older person taking the call, if it was done.

**Prepared answers**
- *"Old people will not talk to a bot."* "Many will not. In a Korean rollout of AI voice calls, people completed about three calls in three months. Its authors blame calls that were hard to hear, repetitive, robotic and without memory. Ours remembers her, has a face, is set up by her family and takes under two minutes. On Friday we tried it with N people over 60 and M finished unaided." (Use real numbers only.) "It need not be everyone. For those who answer, it ends the day when nobody noticed."
- *"It sees two minutes of her day."* Several calls a day, a morning call, and questions about the hours since the last call.
- *"You are not medics."* "Correct. We show it is technically possible; clinical testing with a stroke unit is the next step."
- *"This exists."* "The check-in calls exist and are sold. The stroke-signs check against her own baseline inside the call is ours; we did not find it elsewhere."
- *"How big is this?"* Family plan on stage; councils, insurers and care homes for scale. Naver's CLOVA CareCall says it serves about 50,000 seniors through more than 150 institutions in Korea (press report of the company's own statement).
- *"False alarms?"* The sign must appear twice in one call; show our own count; an alert costs one phone call.

**Leave out of the pitch**
- The Korean study as proof that older people accept AI calls. It shows the opposite (BMC Geriatrics 2026, PMID 41606503 [full text]: 2,896 people aged 65+, about 3 completed calls each, average call 29 seconds).
- "Companies charge for it, so demand is proven", and any customer counts.
- Any claim about hospitals or clinics as customers.

## 10. What already exists (for judge answers only)

- **Check-in calls sold to families [home page]:** AvenoraCall ($29.99 / $49.99 a month), inTouch (EUR 29.90), AloneAssist ($14.99–$24.99), JoyCalls ($9.99–$29.99), TalkedToday ($29, pre-launch). None mentions stroke on its home page.
- **Institution-paid:** Naver CLOVA CareCall (Korea), Hyodol (AI doll, Korea), ElliQ (robot, US agencies), MyndYou (US health plans).
- **Avatar companions:** EverFriends (video avatars for seniors, no user numbers); care.coach (avatar operated by live humans).
- **Hackathon stroke camera apps on Devpost:** Cognivi (TreeHacks 2026), StrokeDx (2025), CareSense, Lazarus. None listed as a winner.
- **An avatar call with a stroke-signs check against a personal baseline:** not found. Search was limited, so "not found", not "does not exist".

## 11. Open decisions for the team

1. Who owns what. [proposal] Three tracks:
   - **A. Measuring:** face, arms, speech, baseline, decision rule, false-alarm count.
   - **B. The call:** call screen, avatar and voice integration, scripted flow, the "repeat that" step.
   - **C. Family side and proof:** the message and clip to the child's phone, the Friday real-user test, the video, the Devpost submission, the pitch.
2. Which avatar service and which voice service, after the delay test.
3. Language of the call in the demo. Lithuanian quality of the avatar and voice is unchecked.
4. What device the parent uses in the demo, and how it stands so the arms are visible.
5. How to gather public votes on Saturday evening (a second route into the final).

## 12. Suggested order of work [proposal]

- **Thu evening:** the two hand tests; agree the golden path and the owners; get a rough end-to-end call running, even with a still face.
- **Fri morning:** the three measures against a baseline; the alert message.
- **Fri afternoon:** real-user test; start recording normal and acted sessions.
- **Sat morning:** fix what Friday broke; finish the counts.
- **Sat afternoon:** record the video; submit on Devpost well before 20:00.
- **Sat evening:** public vote. **Sun:** rehearse the two-call scene; keep a backup recording.
