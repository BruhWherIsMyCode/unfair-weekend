import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { createProviders, DEFAULT_TEXT, DemoError, providerConfig, SAMPLE_RATE } from './providers.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const STATIC = new Map([
  ['/', ['web/index.html', 'text/html; charset=utf-8']],
  ['/style.css', ['web/style.css', 'text/css; charset=utf-8']],
  ['/avatar.js', ['dist/avatar.js', 'text/javascript; charset=utf-8']],
]);

export function createDemoServer({ config = providerConfig(), fetchImpl = fetch, cacheDir = join(ROOT, '.cache', 'speech') } = {}) {
  const providers = createProviders({ config, cacheDir, fetchImpl });
  const recent = new Map();

  return createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Permissions-Policy', 'camera=(), microphone=()');
    res.setHeader('Content-Security-Policy', [
      "default-src 'none'", "script-src 'self'", "style-src 'self'",
      "connect-src 'self' https://*.simli.ai wss://*.simli.ai https://*.livekit.cloud wss://*.livekit.cloud",
      "media-src 'self' blob:", "worker-src 'self' blob:", "base-uri 'none'", "frame-ancestors 'none'",
    ].join('; '));

    const json = (status, data) => {
      res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(data));
    };

    try {
      const port = res.socket.localPort;
      if (![ `127.0.0.1:${port}`, `localhost:${port}` ].includes(req.headers.host)) {
        throw new DemoError('This demo accepts localhost requests only.', 403);
      }
      const url = new URL(req.url, `http://${req.headers.host}`);
      if (req.method === 'GET' && url.pathname === '/api/demo/status') {
        json(200, {
          simli_configured: Boolean(config.simliKey),
          voice_configured: Boolean(config.elevenLabsKey),
          default_text: DEFAULT_TEXT,
        });
        return;
      }
      if (req.method === 'GET' && STATIC.has(url.pathname)) {
        const [file, type] = STATIC.get(url.pathname);
        const body = await readFile(join(ROOT, file));
        res.writeHead(200, { 'Content-Type': type });
        res.end(body);
        return;
      }
      if (req.method !== 'POST' || !['/api/demo/speech', '/api/demo/session'].includes(url.pathname)) {
        throw new DemoError('Not found.', 404);
      }
      const origin = req.headers.origin;
      if ((origin && origin !== `http://${req.headers.host}`) || req.headers['sec-fetch-site'] === 'cross-site') {
        throw new DemoError('Open the demo on localhost to start a session.', 403);
      }
      if (req.headers['content-type']?.split(';')[0] !== 'application/json') {
        throw new DemoError('Use a JSON request body.', 415);
      }
      const limit = url.pathname.endsWith('/session') ? 3 : 10;
      const now = Date.now();
      const hits = (recent.get(url.pathname) || []).filter(time => now - time < 60000);
      if (hits.length >= limit) {
        res.setHeader('Retry-After', '60');
        throw new DemoError('Demo request limit reached. Wait one minute and try again.', 429);
      }
      hits.push(now);
      recent.set(url.pathname, hits);
      if (Number(req.headers['content-length']) > 4096) {
        req.resume();
        throw new DemoError('Request body is too large.', 413);
      }
      const chunks = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size <= 4096) chunks.push(chunk);
      }
      if (size > 4096) throw new DemoError('Request body is too large.', 413);
      let input;
      try {
        input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      } catch {
        throw new DemoError('The request body must contain JSON.', 400);
      }
      if (!input || typeof input !== 'object' || Array.isArray(input)) throw new DemoError('Use a JSON object.', 400);

      if (url.pathname === '/api/demo/session') {
        json(200, await providers.session());
        return;
      }
      const { audio, cached } = await providers.speech(input.text);
      res.writeHead(200, {
        'Content-Type': 'application/octet-stream',
        'Content-Length': audio.length,
        'X-Audio-Sample-Rate': SAMPLE_RATE,
        'X-Audio-Duration-Ms': Math.round(audio.length / (SAMPLE_RATE * 2) * 1000),
        'X-Audio-Cached': String(cached),
      });
      res.end(audio);
    } catch (error) {
      if (res.destroyed) return;
      json(error instanceof DemoError ? error.status : 500, {
        error: error instanceof DemoError ? error.message : 'The demo could not complete this request. Check the server setup.',
      });
    }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT || 3000);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be between 1 and 65535.');
  const server = createDemoServer();
  server.requestTimeout = 30000;
  server.headersTimeout = 10000;
  server.listen(port, '127.0.0.1', () => console.log(`Avatar demo: http://localhost:${port}`));
}
