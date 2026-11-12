import { build } from 'esbuild';

await build({
  entryPoints: ['web/avatar.js'],
  outfile: 'dist/avatar.js',
  bundle: true,
  format: 'esm',
  platform: 'browser',
  target: 'es2022',
  minify: true,
  logLevel: 'info',
});
