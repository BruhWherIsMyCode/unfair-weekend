import { LogLevel, SimliClient } from 'simli-client';

const byId = id => document.getElementById(id);
const play = byId('play');
const stop = byId('stop');
const line = byId('line');
const video = byId('avatar-video');
const audio = byId('avatar-audio');
let configured = false;
let current = null;

function status(message, error = false) {
  byId('status').textContent = message;
  byId('status').classList.toggle('error', error);
}

function idleControls() {
  play.disabled = !configured;
  stop.disabled = true;
  line.disabled = false;
}

async function post(path, body, signal) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Request failed (HTTP ${response.status}).`);
  }
  return response;
}

async function endSession(message = 'Session ended. You can play another line.', error = false) {
  const run = current;
  current = null;
  if (run) {
    run.abort.abort();
    for (const timer of run.timers) clearTimeout(timer);
    try { await run.client?.stop(); } catch { /* The server may already have ended the session. */ }
  }
  video.srcObject = null;
  audio.srcObject = null;
  byId('placeholder').hidden = false;
  byId('stage-label').textContent = 'Session ended';
  idleControls();
  status(message, error);
}

function later(run, callback, milliseconds) {
  run.timers.push(setTimeout(() => { if (current === run) callback(); }, milliseconds));
}

play.addEventListener('click', async () => {
  if (current || !line.value.trim()) return;
  const run = { abort: new AbortController(), client: null, timers: [], firstSpeech: null };
  current = run;
  play.disabled = true;
  stop.disabled = false;
  line.disabled = true;
  for (const id of ['voice-time', 'video-time', 'speech-time']) byId(id).textContent = '—';
  later(run, () => void endSession('Session reached the 90-second demo limit.'), 90000);
  try {
    status('Preparing your line with ElevenLabs…');
    const voiceStart = performance.now();
    const speech = await post('/api/demo/speech', { text: line.value }, run.abort.signal);
    const pcm = new Uint8Array(await speech.arrayBuffer());
    const duration = Number(speech.headers.get('X-Audio-Duration-Ms'));
    byId('voice-time').textContent = `${((performance.now() - voiceStart) / 1000).toFixed(2)} s${speech.headers.get('X-Audio-Cached') === 'true' ? ' · cached' : ''}`;
    if (current !== run) return;

    status('Connecting the live avatar…');
    byId('stage-label').textContent = 'Connecting';
    const videoStart = performance.now();
    const session = await (await post('/api/demo/session', {}, run.abort.signal)).json();
    if (current !== run) return;
    run.client = new SimliClient(session.session_token, video, audio, null, LogLevel.CRITICAL);
    let sentAt = null;
    run.client.on('speaking', () => {
      if (current !== run || run.firstSpeech !== null || sentAt === null) return;
      run.firstSpeech = performance.now();
      byId('speech-time').textContent = `${((run.firstSpeech - sentAt) / 1000).toFixed(2)} s`;
      byId('stage-label').textContent = 'Speaking';
      status('The avatar is speaking. If you cannot hear it, press Play in the audio controls.');
      // End after the known PCM duration, allowing a little time for playback to drain.
      later(run, () => void endSession('Greeting complete. You can play it again.'), duration + 2000);
    });
    run.client.on('error', () => {
      if (current === run) void endSession('Simli ended the session. Check the key, avatar access, and account allowance.', true);
    });
    run.client.on('stop', () => {
      if (current === run) void endSession('The avatar connection closed. You can try again.');
    });
    await run.client.start();
    if (current !== run) return;
    byId('video-time').textContent = `${((performance.now() - videoStart) / 1000).toFixed(2)} s`;
    byId('placeholder').hidden = true;
    byId('stage-label').textContent = 'Connected';
    // Only the audio supplied to Simli is played; the source PCM is not played separately.
    void audio.play().catch(() => status('Press Play in the audio controls to enable sound.'));
    status('Sending the greeting to Simli…');
    sentAt = performance.now();
    for (let offset = 0; offset < pcm.length; offset += 6000) {
      run.client.sendAudioData(pcm.subarray(offset, offset + 6000));
    }
    later(run, () => {
      if (run.firstSpeech === null) void endSession('No speech arrived from Simli within 20 seconds. Try again.', true);
    }, 20000);
  } catch (error) {
    if (current !== run) return;
    await endSession(error instanceof Error ? error.message : 'The avatar could not connect. Try again.', true);
  }
});

stop.addEventListener('click', () => void endSession());
document.addEventListener('visibilitychange', () => {
  if (document.hidden && current) void endSession('Session ended because the page was hidden.');
});
window.addEventListener('pagehide', () => { if (current) void endSession(); });

try {
  const response = await fetch('/api/demo/status');
  if (!response.ok) throw new Error('The backend is not available. Refresh to try again.');
  const config = await response.json();
  byId('simli-status').textContent = config.simli_configured ? 'Key configured' : 'Key needed';
  byId('voice-status').textContent = config.voice_configured ? 'Key configured' : 'Key needed';
  line.value = config.default_text;
  configured = config.simli_configured && config.voice_configured;
  byId('setup').hidden = configured;
  idleControls();
  status(configured ? 'Ready. Play a greeting to start a short live session.' : 'Add the missing keys to try the live avatar.');
} catch (error) {
  status(error.message, true);
}
