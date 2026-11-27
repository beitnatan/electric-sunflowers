# Browser build

`scripts/build-web.sh` builds `web/webrender.wasm` (1.4 MB) and copies the bundled songs to `web/songs/`. Serve `web/` with any static server.

## Page layers

The page edits one song object in three layers, each a view of the same JSON: (1) Lyrics: plain words, one line per sung line, `[chorus]` headers; the page splits words into syllables (a rule of thumb; hyphens, `*` and `~` written by hand are kept), writes a chord progression in the song's key and fills the song's sections; "Sing it" applies and renders. (2) Arrangement: key, tempo, band, singers, the arranger's note, and each section with its chords, energy, key change, tune and per-line fields. (3) JSON: the raw song, two-way synced; fields the forms do not show are kept. The stage (the song's title, key, meter, tempo, voice and liner note; the Render button, with the logo as the progress meter, whose 14 petals light as the render parts finish; seed, voice, audio and a follow-along lyric list) sits beside the layers on wide screens and becomes a dock at the bottom on narrow ones. Colours come from `assets/logo.svg`; the page follows the system light or dark setting. Edits are kept in the browser's local storage.

Putting lyrics in the song is a merge. Sections match by type and order; lines match by their words (longest common subsequence), so a line added or removed in the middle does not shift the rest. A line whose words did not change keeps everything, hand hyphens and phonemes included. A reworded line keeps its chords, tune and singer settings and loses its phonemes. New lines get chords from a progression in the key (or none). Instrumental sections not named in the lyrics box stay where they are. If the merge would drop lines, sections or phonemes, a review panel lists them and waits for confirmation.

Every edit is a named step in a history of 80 states (the lyrics box text is part of each state): Undo, Redo and a History list sit in a sticky bar over the layers, with Ctrl/Cmd+Z and Ctrl+Shift+Z or Ctrl+Y outside text fields. Typing in one field is one step. The page asks no confirmation for destructive actions; it offers Undo in a toast instead.

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
