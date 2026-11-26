// Smoke test of the wasm build: render a bundled song and check the PCM.
// Usage: node web/smoke.mjs   (after scripts/build-web.sh)
import { load } from './sunflower.js';
import fs from 'node:fs';

const dir = new URL('.', import.meta.url);
const m = await load(fs.readFileSync(new URL('webrender.wasm', dir)));
const json = fs.readFileSync(new URL('songs/firelight-reel.json', dir), 'utf8');
const secs = m.probe(json, 1234, 0);
const r = m.render(json, 1234, 0);
let peak = 0, sumsq = 0;
for (let i = 0; i < r.l.length; i++) {
  const v = r.l[i];
  if (!Number.isFinite(v)) throw new Error('non-finite sample at ' + i);
  peak = Math.max(peak, Math.abs(v)); sumsq += v * v;
}
const rms = Math.sqrt(sumsq / r.l.length);
const ok = Math.abs(r.l.length / r.rate - secs) < 5 && peak > 0.5 && peak < 1 && rms > 0.05;
console.log(`${r.meta.title}: ${secs.toFixed(1)} s, peak ${peak.toFixed(3)}, rms ${rms.toFixed(3)}, memory ${(m.memoryBytes() / 1048576).toFixed(0)} MB`);
if (!ok) { console.error('smoke test failed'); process.exit(1); }
