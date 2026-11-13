const { useComposition, CompositionStage, Easing, animate, clamp } = window;

const MOTION = {
  enter: (from, to, start, dur = 0.6) => animate({ from, to, start, end: start + dur, ease: Easing.easeOutCubic }),
  pop: (from, to, start, dur = 0.5) => animate({ from, to, start, end: start + dur, ease: Easing.easeOutBack }),
  glide: (from, to, start, dur = 1) => animate({ from, to, start, end: start + dur, ease: Easing.easeInOutCubic }),
};

const W = 440, H = 956;
const PHONE_PATH = 'M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25c1.1.37 2.3.57 3.6.57a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1C10.6 21 3 13.4 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1z';
const FONT = "-apple-system,BlinkMacSystemFont,'SF Pro Text','SF Pro Display',system-ui,sans-serif";
const MONO = "ui-monospace,'SF Mono',Menlo,monospace";
const abs = (s) => ({ position: 'absolute', ...s });

function PhoneIcon({ size, rotate }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="#FFFFFF" style={{ transform: rotate ? 'rotate(135deg)' : 'none' }}><path d={PHONE_PATH} /></svg>;
}

function StatusBar({ time }) {
  return (
    <div style={abs({ left: 0, right: 0, top: 0, height: 62, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 34px 0 52px', boxSizing: 'border-box', fontSize: 17, fontWeight: 600, color: '#FFFFFF', zIndex: 5 })}>
      <span>{time}</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <span style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 11 }}>{[4, 6, 8, 11].map(h => <span key={h} style={{ width: 3, height: h, borderRadius: 1, background: '#FFFFFF' }} />)}</span>
        <span style={{ width: 26, height: 12, border: '1.5px solid #FFFFFF', borderRadius: 4, boxSizing: 'border-box', padding: 1.5, display: 'flex' }}><span style={{ width: '70%', background: '#FFFFFF', borderRadius: 1.5 }} /></span>
      </span>
    </div>
  );
}

function Words({ text, start, span, T }) {
  const words = text.split(' ');
  const step = span / words.length;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', columnGap: 9, rowGap: 4, fontSize: 32, fontWeight: 700, lineHeight: 1.2, letterSpacing: -0.5 }}>
      {words.map((w, i) => {
        const p = animate({ from: 0, to: 1, start: start + i * step, end: start + i * step + 0.7, ease: Easing.easeInOutSine })(T);
        return <span key={i} style={{ opacity: p }}>{w}</span>;
      })}
    </div>
  );
}

function Listening({ T, color = '#7FD08F' }) {
  const hs = [0, 1, 2, 3, 4].map(i => 10 + 18 * (0.5 + 0.5 * Math.sin(T * 7 + i * 1.3)));
  return (
    <div style={{ width: 96, height: 96, borderRadius: 48, flex: 'none', background: 'rgba(127,208,143,.16)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, height: 30 }}>{hs.map((h, i) => <span key={i} style={{ width: 5, height: h, borderRadius: 3, background: color }} />)}</div>
      <span style={{ fontSize: 13, fontWeight: 600, color }}>Listening</span>
    </div>
  );
}

function TimerRing({ T, start, secs, dur }) {
  const p = clamp((T - start) / dur, 0, 1);
  const left = Math.max(0, Math.ceil(secs * (1 - p) - 0.001));
  const C = 263.9;
  return (
    <div style={{ width: 96, height: 96, flex: 'none', position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <svg width="96" height="96" viewBox="0 0 96 96" style={abs({ inset: 0, transform: 'rotate(-90deg)' })}>
        <circle cx="48" cy="48" r="42" fill="none" stroke="rgba(255,255,255,.18)" strokeWidth="7" />
        <circle cx="48" cy="48" r="42" fill="none" stroke={p >= 1 ? '#7FD08F' : '#F2B25C'} strokeWidth="7" strokeLinecap="round" strokeDasharray={C} strokeDashoffset={C * p} />
      </svg>
      {p >= 1
        ? <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#7FD08F" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" style={{ position: 'relative' }}><polyline points="5,12.5 10,17 19,7" /></svg>
        : <span style={{ fontSize: 40, fontWeight: 700, fontVariantNumeric: 'tabular-nums', position: 'relative' }}>{left}</span>}
    </div>
  );
}

function Incoming({ T, C }) {
  const out = 1 - MOTION.glide(0, 1, C.Answer + 0.35, 0.6)(T);
  const title = 1, btns = 1;
  const ringing = T < C.Answer;
  const pulse = ringing ? 1 + 0.06 * Math.max(0, Math.sin(T * 5)) : 1;
  const press = T >= C.Answer && T < C.Answer + 0.3 ? 0.9 : 1;
  const halo = ringing ? (T * 0.9) % 1 : 1;
  return (
    <div style={abs({ inset: 0, opacity: out, background: 'linear-gradient(160deg,#2F2B42 0%,#2B2C3D 55%,#30395A 100%)' })}>
      <div style={abs({ left: 0, right: 0, top: 124, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, opacity: title, transform: `translateY(${(1 - title) * 16}px)` })}>
        <div style={{ fontSize: 21, color: 'rgba(255,255,255,.55)' }}>video call</div>
        <div style={{ fontSize: 34, fontWeight: 700, letterSpacing: -0.5, color: '#FFFFFF' }}>Daily check-in</div>
      </div>
      <div style={abs({ left: 44, right: 44, bottom: 72, display: 'flex', justifyContent: 'space-between', opacity: btns, transform: `translateY(${(1 - btns) * 30}px)` })}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, color: '#FFFFFF' }}>
          <div style={{ width: 88, height: 88, borderRadius: 44, background: '#D12A24', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><PhoneIcon size={38} rotate /></div>
          <span style={{ fontSize: 19 }}>Decline</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, color: '#FFFFFF', position: 'relative' }}>
          <div style={abs({ left: 44 - 44 * (1 + halo * 0.7), top: 44 - 44 * (1 + halo * 0.7), width: 88 * (1 + halo * 0.7), height: 88 * (1 + halo * 0.7), borderRadius: '50%', border: '2px solid rgba(43,162,70,.8)', opacity: ringing ? 1 - halo : 0, boxSizing: 'border-box' })} />
          <div style={{ width: 88, height: 88, borderRadius: 44, background: '#2BA246', display: 'flex', alignItems: 'center', justifyContent: 'center', transform: `scale(${pulse * press})` }}><PhoneIcon size={38} /></div>
          <span style={{ fontSize: 19 }}>Accept</span>
        </div>
      </div>
    </div>
  );
}

function Touch({ T, at, x, y }) {
  const a = MOTION.enter(0, 1, at - 0.5, 0.4)(T) * (1 - MOTION.enter(0, 1, at + 0.25, 0.3)(T));
  const r = MOTION.enter(0, 1, at, 0.5)(T);
  return (
    <>
      <div style={abs({ left: x - 30, top: y - 30, width: 60, height: 60, borderRadius: 30, background: 'rgba(255,255,255,.45)', opacity: a, transform: `scale(${1 - 0.15 * r})` })} />
      <div style={abs({ left: x - 30, top: y - 30, width: 60, height: 60, borderRadius: 30, border: '2px solid rgba(255,255,255,.7)', opacity: (1 - r) * (T >= at ? 1 : 0), transform: `scale(${1 + r * 1.4})`, boxSizing: 'border-box' })} />
    </>
  );
}

function Call({ T, C }) {
  const inn = MOTION.glide(0, 1, C.Answer + 0.35, 0.7)(T);
  const doneOut = 1 - MOTION.glide(0, 1, C.Done, 0.6)(T);
  const avatarZoom = 1.08 - 0.08 * inn + 0.02 * Math.sin(T * 0.4);
  const cardIn = MOTION.enter(0, 1, C.Intro + 0.2, 0.7)(T);
  const pip = MOTION.enter(0, 1, C.Intro + 0.5, 0.7)(T);
  const taskGrow = MOTION.glide(0, 1, C.Smile, 0.8)(T) * (1 - MOTION.glide(0, 1, C.Sentence, 0.8)(T));
  const pipW = 150 + 50 * taskGrow, pipH = 216 + 50 * taskGrow;
  const order = ['Intro', 'Question', 'Smile', 'Arms', 'Sentence'];
  let cur = 0; order.forEach((k, i) => { if (T >= C[k]) cur = i; });
  const dotIdx = Math.max(0, cur - 1);
  const panel = (key, next, render) => {
    const a = MOTION.glide(0, 1, C[key] - 0.25, 0.5)(T) * (next ? 1 - MOTION.glide(0, 1, C[next] - 0.25, 0.5)(T) : 1);
    return <div key={key} style={abs({ inset: 24, display: 'flex', alignItems: 'center', gap: 18, opacity: a, transform: `translateY(${(1 - a) * 12}px)` })}>{render()}</div>;
  };
  const label = (t) => <div style={{ fontSize: 17, fontWeight: 600, color: 'rgba(255,255,255,.6)' }}>{t}</div>;
  const big = (t) => <div style={{ fontSize: 34, fontWeight: 700, lineHeight: 1.15, letterSpacing: -0.6 }}>{t}</div>;
  const sub = (t) => <div style={{ fontSize: 21, color: 'rgba(255,255,255,.8)' }}>{t}</div>;
  return (
    <div style={abs({ inset: 0, opacity: inn * doneOut, color: '#FFFFFF' })}>
      <div style={abs({ inset: 0, transform: `scale(${avatarZoom})`, background: 'repeating-linear-gradient(135deg,#3B3530 0 10px,#433C36 10px 20px)' })}>
        <div style={abs({ left: 0, right: 0, top: 300, textAlign: 'center', fontFamily: MONO, fontSize: 12, color: '#B7AFA2' })}>live avatar, full screen</div>
      </div>
      <div style={abs({ top: 76, left: 20, right: 20, display: 'flex', alignItems: 'center', justifyContent: 'space-between', opacity: cardIn })}>
        <div style={{ height: 44, padding: '0 18px', borderRadius: 22, background: 'rgba(32,28,24,.7)', display: 'flex', alignItems: 'center', gap: 10, fontSize: 17, fontWeight: 600 }}><span style={{ width: 10, height: 10, borderRadius: 5, background: '#F2B25C' }} />Daily check-in</div>
        <div style={{ height: 44, padding: '0 16px', borderRadius: 22, background: 'rgba(32,28,24,.7)', display: 'flex', alignItems: 'center', gap: 6 }}>
          {[0, 1, 2, 3].map(i => <span key={i} style={{ width: i === dotIdx ? 28 : 10, height: 10, borderRadius: 5, background: i < dotIdx ? '#7FD08F' : i === dotIdx ? '#F2B25C' : 'rgba(255,255,255,.3)' }} />)}
        </div>
      </div>
      <div style={abs({ left: 16, right: 16, bottom: 300 + 44 * taskGrow, height: 220, borderRadius: 30, background: 'rgba(32,28,24,.88)', opacity: cardIn, transform: `translateY(${(1 - cardIn) * 40}px)`, overflow: 'hidden' })}>
        {panel('Intro', 'Question', () => <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 10 }}>{label('Assistant')}<Words T={T} start={C.Intro + 0.5} span={3.6} text="Hello, Fedya. I will ask three questions, then show you three small things." /></div>)}
        {panel('Question', 'Smile', () => <><div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 10 }}>{label('Question 1 of 3')}<Words T={T} start={C.Question + 0.4} span={1.8} text="Have you taken your medicine today?" /></div><Listening T={T} /></>)}
        {panel('Smile', 'Arms', () => <><div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>{label('Step 1 of 3')}{big('Smile wide')}{sub('Look at the screen')}</div><TimerRing T={T} start={C.Smile + 0.6} secs={5} dur={4} /></>)}
        {panel('Arms', 'Sentence', () => <><div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>{label('Step 2 of 3')}{big('Raise both arms')}{sub('Keep them up')}</div><TimerRing T={T} start={C.Arms + 0.6} secs={10} dur={6} /></>)}
        {panel('Sentence', null, () => <><div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>{label('Step 3 of 3')}<div style={{ fontSize: 30, fontWeight: 700, lineHeight: 1.2, letterSpacing: -0.5 }}>“The sun is shining over Vilnius today.”</div>{sub('Say it out loud')}</div><Listening T={T} /></>)}
      </div>
      <div style={abs({ left: 20, bottom: 56, width: pipW, height: pipH, borderRadius: 24, overflow: 'hidden', background: 'repeating-linear-gradient(135deg,#5A5047 0 8px,#655A50 8px 16px)', boxShadow: '0 0 0 2px rgba(255,255,255,.25)', opacity: pip, transform: `translateY(${(1 - pip) * 60}px)` })}>
        <div style={abs({ left: 0, right: 0, top: '46%', textAlign: 'center', fontFamily: MONO, fontSize: 11, color: '#D6CEC2' })}>you</div>
      </div>
      <div style={abs({ right: 28, bottom: 64, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, opacity: pip })}>
        <div style={{ width: 96, height: 96, borderRadius: 48, background: '#D63B32', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><PhoneIcon size={40} rotate /></div>
        <span style={{ fontSize: 20, fontWeight: 600 }}>End</span>
      </div>
    </div>
  );
}

function Done({ T, C }) {
  const a = MOTION.glide(0, 1, C.Done + 0.2, 0.6)(T);
  const check = MOTION.pop(0, 1, C.Done + 0.5, 0.6)(T);
  const txt = MOTION.enter(0, 1, C.Done + 0.9, 0.7)(T);
  return (
    <div style={abs({ inset: 0, opacity: a, background: '#2A2622', color: '#FFFFFF' })}>
      <div style={abs({ left: 0, right: 0, top: 250, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 36, textAlign: 'center' })}>
        <div style={{ width: 120, height: 120, borderRadius: 60, background: 'rgba(127,208,143,.16)', display: 'flex', alignItems: 'center', justifyContent: 'center', transform: `scale(${check})` }}>
          <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="#7FD08F" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><polyline points="5,12.5 10,17 19,7" strokeDasharray="22" strokeDashoffset={22 * (1 - clamp(check, 0, 1))} /></svg>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, opacity: txt, transform: `translateY(${(1 - txt) * 14}px)` }}>
          <div style={{ fontSize: 40, fontWeight: 700, letterSpacing: -0.8 }}>Thank you</div>
          <div style={{ fontSize: 24, color: 'rgba(255,255,255,.75)' }}>Next call at 18:00</div>
        </div>
      </div>
    </div>
  );
}

const CAPTIONS = [
  ['Ring', '1  Incoming call', 'System CallKit screen. Accept pulses while ringing.'],
  ['Answer', '2  Accept', 'Crossfade into the call, 0.6 s.'],
  ['Intro', '3  Avatar explains', 'Live avatar full screen. Subtitles follow the voice word by word.'],
  ['Question', '4  Question', 'Subtitles, then the Listening indicator while she answers.'],
  ['Smile', '5  Task: smile', 'Fixed instruction with a 5 s timer. Self view grows.'],
  ['Arms', '6  Task: raise arms', 'Fixed instruction with a 10 s timer.'],
  ['Sentence', '7  Task: sentence', 'Fixed sentence, Listening indicator.'],
  ['Done', '8  Finished', 'Check mark, thank you, next call time.'],
];

function Caption({ T, C, end }) {
  let idx = 0; CAPTIONS.forEach(([k], i) => { if (T >= C[k]) idx = i; });
  const [, title, note] = CAPTIONS[idx];
  return (
    <div style={abs({ left: 0, right: 0, bottom: 34, display: 'flex', justifyContent: 'center' })}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 20, padding: '12px 22px', borderRadius: 14, background: 'rgba(255,255,255,.06)', fontFamily: MONO, color: '#B7AFA2' }}>
        <span style={{ fontSize: 22, fontWeight: 700, color: '#F3EEE6' }}>{title}</span>
        <span style={{ fontSize: 18 }}>{note}</span>
        <span style={{ fontSize: 18, color: '#7D756A', fontVariantNumeric: 'tabular-nums' }}>{(T - C[CAPTIONS[idx][0]]).toFixed(1)} s</span>
      </div>
    </div>
  );
}

function Piece() {
  const { T, CUES: C, authoredTotal: end } = useComposition();
  const dark = Math.max(1 - MOTION.enter(0, 1, 0, 0.5)(T), MOTION.glide(0, 1, end - 0.6, 0.6)(T));
  const scale = 0.9, lift = -30;
  const clock = T < C.Smile ? '13:00' : T < C.Done ? '13:01' : '13:02';
  return (
    <div data-screen-label={`t=${Math.floor(T)}s`} style={{ position: 'absolute', inset: 0, background: '#1C1916', fontFamily: FONT, overflow: 'hidden' }}>
      <Caption T={T} C={C} end={end} />
      <div style={abs({ left: 960 - W / 2, top: 540 - H / 2, width: W, height: H, transform: `translateY(${lift}px) scale(${scale})`, transformOrigin: '50% 50%' })}>
        <div style={abs({ inset: 0, borderRadius: 62, overflow: 'hidden', background: '#2A2622', boxShadow: '0 0 0 11px #0E0D0B, 0 0 0 12px #4A443D, 0 60px 120px rgba(0,0,0,.5)' })}>
          <Call T={T} C={C} />
          <Incoming T={T} C={C} />
          <Done T={T} C={C} />
          <div style={abs({ inset: 0, background: '#000', opacity: dark, zIndex: 4 })} />
          <StatusBar time={clock} />
          <div style={abs({ top: 13, left: 157, width: 126, height: 37, borderRadius: 19, background: '#000', zIndex: 6 })} />
          <div style={abs({ bottom: 8, left: 147, width: 146, height: 5, borderRadius: 3, background: '#FFFFFF', zIndex: 6 })} />
          <Touch T={T} at={C.Answer} x={W - 44 - 44} y={H - 72 - 31 - 44 - 12} />
        </div>
      </div>
    </div>
  );
}

function ClientVideo() {
  const [t, setTweak] = window.useTweaks(window.TWEAK_DEFAULTS);
  return (
    <>
      <CompositionStage width={1920} height={1080} scenes={window.OM_SCENES} playback={window.OM_PLAYBACK} bg="#1C1916">
        <Piece />
      </CompositionStage>
      <window.TweaksPanel>
        <window.TweakToggle label="Motion editor" value={t.motionEditor} onChange={(v) => setTweak('motionEditor', v)} />
      </window.TweaksPanel>
    </>
  );
}

window.ClientVideo = ClientVideo;
