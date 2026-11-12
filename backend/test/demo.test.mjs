import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { request } from 'node:http';
import { test } from 'node:test';
import { createDemoServer } from '../src/server.mjs';
import { createProviders, DemoError, providerConfig } from '../src/providers.mjs';

const config = providerConfig({ SIMLI_API_KEY: 'simli-test-secret', ELEVENLABS_API_KEY: 'voice-test-secret' });

async function fixture(t, fetchImpl, env = config) {
  const cacheDir = await mkdtemp(join(tmpdir(), 'avatar-test-'));
  const server = createDemoServer({ config: env, fetchImpl, cacheDir });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await rm(cacheDir, { recursive: true, force: true });
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  return {
    base,
    cacheDir,
    post: (path, body, headers = {}) => fetch(`${base}${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body),
    }),
  };
}

test('speech uses the required PCM format and caches repeated lines across server instances', async t => {
  let calls = 0;
  const upstream = async (url, options) => {
    calls += 1;
    assert.equal(new URL(url).searchParams.get('output_format'), 'pcm_16000');
    assert.equal(options.headers['xi-api-key'], config.elevenLabsKey);
    assert.equal(JSON.parse(options.body).text, 'Hello, Ona.');
    return new Response(Buffer.alloc(32000), { headers: { 'Content-Type': 'audio/pcm' } });
  };
  const { post, cacheDir } = await fixture(t, upstream);
  const first = await post('/api/demo/speech', { text: 'Hello, Ona.' });
  assert.equal(first.status, 200);
  assert.equal(first.headers.get('x-audio-duration-ms'), '1000');
  assert.equal(first.headers.get('x-audio-cached'), 'false');
  assert.equal((await first.arrayBuffer()).byteLength, 32000);
  const second = await post('/api/demo/speech', { text: 'Hello, Ona.' });
  assert.equal(second.headers.get('x-audio-cached'), 'true');
  const restarted = createProviders({ config, fetchImpl: upstream, cacheDir });
  assert.equal((await restarted.speech('Hello, Ona.')).cached, true);
  assert.equal(calls, 1);
});

test('parallel identical speech requests consume one generation', async t => {
  let calls = 0;
  const { post } = await fixture(t, async () => {
    calls += 1;
    await new Promise(resolve => setTimeout(resolve, 20));
    return new Response(Buffer.alloc(3200));
  });
  const results = await Promise.all(Array.from({ length: 3 }, () => post('/api/demo/speech', { text: 'Hello.' })));
  assert.deepEqual(results.map(result => result.status), [200, 200, 200]);
  assert.equal(calls, 1);
});

test('Simli requests use server-controlled avatar and session limits, and have a request cap', async t => {
  let calls = 0;
  const { post } = await fixture(t, async (url, options) => {
    calls += 1;
    assert.equal(url, 'https://api.simli.ai/compose/token');
    assert.equal(options.headers['x-simli-api-key'], config.simliKey);
    const body = JSON.parse(options.body);
    assert.equal(body.faceId, config.faceId);
    assert.equal(body.maxSessionLength, 90);
    assert.equal(body.maxIdleTime, 20);
    assert.equal(body.audioInputFormat, 'pcm16');
    return Response.json({ session_token: 'short-lived-test-token' });
  });
  for (let i = 0; i < 3; i += 1) {
    const response = await post('/api/demo/session', { faceId: 'other-face', maxSessionLength: 3600 });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { session_token: 'short-lived-test-token', max_seconds: 90 });
  }
  const limited = await post('/api/demo/session', {});
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get('retry-after'), '60');
  assert.equal(calls, 3);
});

test('invalid input, cross-origin requests, and oversized bodies never reach paid providers', async t => {
  const { base, post } = await fixture(t, () => assert.fail('Provider must not be called'));
  assert.equal((await post('/api/demo/speech', { text: '' })).status, 400);
  assert.equal((await post('/api/demo/speech', { text: 'x'.repeat(301) })).status, 400);
  assert.equal((await post('/api/demo/speech', null)).status, 400);
  assert.equal((await post('/api/demo/session', {}, { Origin: 'https://other.example' })).status, 403);
  // Node's fetch replaces Host, so send the rebinding attempt through the HTTP client.
  const rejectedHost = await new Promise((resolve, reject) => {
    const req = request(`${base}/api/demo/session`, {
      method: 'POST', headers: { Host: 'other.example', 'Content-Type': 'application/json' },
    }, response => {
      response.resume();
      resolve(response.statusCode);
    });
    req.on('error', reject);
    req.end('{}');
  });
  assert.equal(rejectedHost, 403);
  assert.equal((await post('/api/demo/speech', { text: 'x'.repeat(5000) })).status, 413);
  const malformed = await fetch(`${base}/api/demo/speech`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{',
  });
  assert.equal(malformed.status, 400);
  const form = await fetch(`${base}/api/demo/session`, { method: 'POST', body: '{}' });
  assert.equal(form.status, 415);
});

test('provider errors omit upstream response bodies and credentials', async t => {
  const { post } = await fixture(t, async () => new Response(`Rejected ${config.elevenLabsKey}`, { status: 401 }));
  const response = await post('/api/demo/speech', { text: 'Hello.' });
  assert.equal(response.status, 502);
  const body = await response.text();
  assert.match(body, /ElevenLabs returned HTTP 401/);
  assert.doesNotMatch(body, /voice-test-secret|Rejected/);
});

test('missing keys are explicit and static routes never serve env files', async t => {
  const { base, post } = await fixture(t, () => assert.fail('Provider must not be called'), providerConfig({}));
  const status = await fetch(`${base}/api/demo/status`);
  const data = await status.json();
  assert.equal(data.simli_configured, false);
  assert.equal(data.voice_configured, false);
  assert.equal((await post('/api/demo/session', {})).status, 503);
  assert.equal((await post('/api/demo/speech', { text: 'Hello.' })).status, 503);
  assert.equal((await fetch(`${base}/.env`)).status, 404);
  assert.equal((await fetch(`${base}/src/server.mjs`)).status, 404);
});

test('wrong or oversized audio is rejected before caching', async t => {
  const cacheDir = await mkdtemp(join(tmpdir(), 'avatar-audio-test-'));
  t.after(() => rm(cacheDir, { recursive: true, force: true }));
  for (const audio of [Buffer.from('RIFFxxxx'), Buffer.alloc(3), Buffer.alloc(0), Buffer.alloc(1280002)]) {
    const providers = createProviders({ config, cacheDir, fetchImpl: async () => new Response(audio) });
    await assert.rejects(providers.speech('Hello.'), DemoError);
  }
});
