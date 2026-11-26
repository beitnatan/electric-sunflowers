# Browser build

`scripts/build-web.sh` builds `web/webrender.wasm` (1.4 MB) and copies the bundled songs to `web/songs/`. Serve `web/` with any static server.

Files: `index.html` (page, spinner, memory readout), `worker.js` (runs the engine off the page thread), `sunflower.js` (wasm glue, WAV writer), `crates/webrender` (the C interface; no wasm-bindgen).

## Threads and headers

GitHub's Pages documentation lists no way to set response headers (limits page: 1 GB site, 100 GB/month soft bandwidth, 10 builds/hour; nothing on headers). The open request for COOP/COEP is community discussion 13309. Without them `SharedArrayBuffer` is unavailable, so the build is single-threaded: rayon falls back to the calling thread. Output is deterministic. A threaded build would need nightly `build-std` with atomics and cross-origin isolation, for example the `coi-serviceworker` workaround (one extra reload on first visit); it is not done.

## Measured (Node 22, one thread, 4 cores, seed 1234)

| song | music | native 1 thread | wasm | native peak RSS | wasm linear memory |
|---|---|---|---|---|---|
| firelight-reel | 102 s | 3.7 s | 5.4 s | 333 MB | 327 MB |
| demo | 186 s | 7.2 s | 10.3 s | 584 MB | 576 MB |
| center-pivot | 246 s | 8.4 s | 12.6 s | 636 MB | 705 MB |

Estimate used by the page: 60 MB + 2.8 MB per second of music (fits all three within 6%). Wasm memory never shrinks, so the figure after a render is the peak. The page also holds the result (8 bytes per sample pair) and a WAV copy (4 bytes per pair) outside this figure.

Native versus wasm PCM, same seed: SNR 132 dB, max sample difference 7e-7. Not bit-identical (libm differs; wasm has no flush-to-zero).

## Deploy

`.github/workflows/pages.yml` builds this folder and publishes it to GitHub Pages on every trunk push that changes `web/`, `crates/`, the build script or `Cargo.lock`. Pull requests build and smoke-test it (`node web/smoke.mjs`) without deploying. One-time setting: repository Settings, Pages, Source: "GitHub Actions". The site URL appears on the deploy job.
