// Render worker: keeps the wasm module off the page's thread, so the
// spinner and the timer keep moving while the engine runs.
import { load } from './sunflower.js';

let m = null;
const post = (msg, transfer) => self.postMessage(msg, transfer || []);

self.onmessage = async (e) => {
  const d = e.data;
  try {
    if (d.type === 'init') {
      m = await load(d.url, {
        progress: (done, total) => post({ type: 'progress', done, total, mem: m.memoryBytes() }),
      });
      post({ type: 'ready', mem: m.memoryBytes() });
    } else if (d.type === 'probe') {
      post({ type: 'probed', seconds: m.probe(d.json, d.seed, d.voice) });
    } else if (d.type === 'render') {
      const r = m.render(d.json, d.seed, d.voice);
      post({ type: 'done', l: r.l, r: r.r, rate: r.rate, meta: r.meta, mem: m.memoryBytes() },
           [r.l.buffer, r.r.buffer]);
    }
  } catch (err) {
    post({ type: 'error', message: String(err.message || err), fatal: !!err.fatal });
  }
};
