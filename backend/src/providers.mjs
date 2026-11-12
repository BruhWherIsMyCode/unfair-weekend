import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

export const DEFAULT_TEXT = 'Hello. I am here for a short check-in. Take a moment to get comfortable.';
export const SESSION_LIMIT_SECONDS = 90;
export const SAMPLE_RATE = 16000;
const MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * 40;

export class DemoError extends Error {
  constructor(message, status = 502) {
    super(message);
    this.status = status;
  }
}

export function providerConfig(env = process.env) {
  return {
    simliKey: env.SIMLI_API_KEY?.trim() || env.SMILI_API_KEY?.trim() || '',
    elevenLabsKey: env.ELEVENLABS_API_KEY?.trim() || '',
    faceId: env.SIMLI_FACE_ID?.trim() || 'bf13113a-355a-4d97-8b90-4edccd1da7b2',
    voiceId: env.ELEVENLABS_VOICE_ID?.trim() || 'JBFqnCBsd6RMkjVDRZzb',
    modelId: env.ELEVENLABS_MODEL_ID?.trim() || 'eleven_flash_v2_5',
  };
}

export function validateText(value) {
  if (typeof value !== 'string' || !value.trim() || value.trim().length > 300) {
    throw new DemoError('Enter between 1 and 300 characters.', 400);
  }
  return value.trim();
}

async function providerFetch(fetchImpl, url, options, provider) {
  let response;
  try {
    response = await fetchImpl(url, { ...options, signal: AbortSignal.timeout(25000) });
  } catch {
    throw new DemoError(`${provider} could not be reached. Try again shortly.`);
  }
  // Provider error bodies can include request data. Return a status without forwarding them.
  if (!response.ok) {
    await response.body?.cancel();
    throw new DemoError(`${provider} returned HTTP ${response.status}. Check the key and account allowance.`);
  }
  return response;
}

async function readAudio(response) {
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > MAX_AUDIO_BYTES) {
      throw new DemoError('The generated line exceeds the 40-second demo limit. Try a shorter line.');
    }
    chunks.push(chunk);
  }
  const audio = Buffer.concat(chunks);
  if (audio.length === 0 || audio.length % 2 !== 0 || audio.subarray(0, 4).toString() === 'RIFF') {
    throw new DemoError('ElevenLabs returned an unexpected audio format.');
  }
  return audio;
}

export function createProviders({ config, cacheDir, fetchImpl = fetch }) {
  const pending = new Map();

  async function speech(text) {
    text = validateText(text);
    if (!config.elevenLabsKey) throw new DemoError('Add ELEVENLABS_API_KEY to backend/.env, then restart.', 503);
    const hash = createHash('sha256')
      .update(JSON.stringify([config.voiceId, config.modelId, text, 'pcm_16000']))
      .digest('hex');
    const file = join(cacheDir, `${hash}.pcm`);
    try {
      const audio = await readFile(file);
      return { audio, cached: true };
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    if (pending.has(hash)) return pending.get(hash);
    const generating = (async () => {
      const response = await providerFetch(fetchImpl,
        `https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(config.voiceId)}?output_format=pcm_16000`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'xi-api-key': config.elevenLabsKey },
          body: JSON.stringify({ text, model_id: config.modelId }),
        }, 'ElevenLabs');
      const audio = await readAudio(response);
      await mkdir(cacheDir, { recursive: true, mode: 0o700 });
      const temporary = `${file}.${randomUUID()}.tmp`;
      await writeFile(temporary, audio, { mode: 0o600 });
      await rename(temporary, file);
      return { audio, cached: false };
    })();
    pending.set(hash, generating);
    try {
      return await generating;
    } finally {
      pending.delete(hash);
    }
  }

  async function session() {
    if (!config.simliKey) throw new DemoError('Add SIMLI_API_KEY to backend/.env, then restart.', 503);
    const response = await providerFetch(fetchImpl, 'https://api.simli.ai/compose/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-simli-api-key': config.simliKey },
      body: JSON.stringify({
        faceId: config.faceId,
        handleSilence: true,
        maxSessionLength: SESSION_LIMIT_SECONDS,
        maxIdleTime: 20,
        audioInputFormat: 'pcm16',
      }),
    }, 'Simli');
    const data = await response.json();
    if (typeof data.session_token !== 'string' || !data.session_token) {
      throw new DemoError('Simli did not return a session token.');
    }
    return { session_token: data.session_token, max_seconds: SESSION_LIMIT_SECONDS };
  }

  return { speech, session };
}
