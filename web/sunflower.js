// Glue for webrender.wasm: song JSON in, stereo Float32Array out.
// Runs in a Web Worker (or Node). One thread; no SharedArrayBuffer.

const enc = new TextEncoder();
const dec = new TextDecoder();

export const VOICES = ['auto', 'bass', 'baritone', 'tenor', 'alto', 'soprano'];

export async function load(source, hooks = {}) {
  let x; // exports, set after instantiation
  const env = { sf_progress: (done, total) => hooks.progress && hooks.progress(done, total) };
  const imports = { env };
  let inst;
  if (source instanceof ArrayBuffer || ArrayBuffer.isView(source)) {
    inst = (await WebAssembly.instantiate(source, imports)).instance;
  } else {
    const res = await fetch(source);
    if (WebAssembly.instantiateStreaming) {
      inst = (await WebAssembly.instantiateStreaming(res, imports)).instance;
    } else {
      inst = (await WebAssembly.instantiate(await res.arrayBuffer(), imports)).instance;
    }
  }
  x = inst.exports;
  const mem = () => new Uint8Array(x.memory.buffer);
  const text = (p, n) => dec.decode(mem().subarray(p, p + n));
  const seedParts = (seed) => {
    const s = BigInt.asUintN(64, BigInt(seed));
    return [Number(s & 0xffffffffn), Number(s >> 32n)];
  };
  const withJson = (json, f) => {
    const bytes = enc.encode(json);
    const p = x.sf_alloc(bytes.length);
    mem().set(bytes, p);
    try { return f(p, bytes.length); } finally { x.sf_dealloc(p, bytes.length); }
  };
  const lastError = () => text(x.sf_error_ptr(), x.sf_error_len());
  const wrap = (f) => {
    try { return f(); }
    catch (e) { // a panic traps; the instance is dead after it
      const msg = lastError();
      const err = new Error(msg || String(e));
      err.fatal = true;
      throw err;
    }
  };
  return {
    memoryBytes: () => x.memory.buffer.byteLength,
    // Song length in seconds. Throws on a bad song.
    probe(json, seed = 1234, voice = 0) {
      const [lo, hi] = seedParts(seed);
      const s = wrap(() => withJson(json, (p, n) => x.sf_probe(p, n, lo, hi, voice)));
      if (s < 0) throw new Error(lastError());
      return s;
    },
    // Returns {l, r, rate, meta}. l and r are copies, so the module's
    // buffers are released at once (its memory stays at the peak).
    render(json, seed = 1234, voice = 0) {
      const [lo, hi] = seedParts(seed);
      const rc = wrap(() => withJson(json, (p, n) => x.sf_render(p, n, lo, hi, voice)));
      if (rc !== 0) throw new Error(lastError());
      const n = x.sf_samples();
      const view = (ch) => new Float32Array(x.memory.buffer, x.sf_channel_ptr(ch), n).slice();
      const l = view(0), r = view(1);
      const meta = JSON.parse(text(x.sf_meta_ptr(), x.sf_meta_len()));
      x.sf_release();
      return { l, r, rate: meta.rate, meta };
    },
  };
}

// 16-bit stereo WAV from two channels.
export function wav16(l, r, rate) {
  const n = l.length, buf = new ArrayBuffer(44 + n * 4), v = new DataView(buf);
  const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, 'RIFF'); v.setUint32(4, 36 + n * 4, true); w(8, 'WAVEfmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 2, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 4, true);
  v.setUint16(32, 4, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 4, true);
  const q = (s) => Math.max(-32768, Math.min(32767, Math.round(s * 32767)));
  for (let i = 0, o = 44; i < n; i++, o += 4) { v.setInt16(o, q(l[i]), true); v.setInt16(o + 2, q(r[i]), true); }
  return new Blob([buf], { type: 'audio/wav' });
}
