# Engine design (Rust rewrite)

Status: implemented (waves 0-5 of section 13). The work was done on the branch `rust-cleanup` (base commit 29c5c08), which this repository does not contain; the rewrite's commits are in trunk history from 50ade40. Deviations from the text as first written:

- FFT: `dsp::fft` wraps `realfft`/`rustfft` (section 5.10) instead of a hand-written Stockham transform.
- Memory: peak RSS for the demo is above the section 10 aims (measured figures in section 10). Speed meets them.
- The Helmholtz analysis (Goertzel magnitudes, the note and phrase sweeps) lives in `crates/instruments/tests/helmholtz/mod.rs`, shared by the test and the example, so the library does not depend on rayon.
- Seeds are `u64` end to end: CLI, `engine::render`, `compose::prepare`, the stems example.

Scope: everything after Claude's JSON reply: validation, composition, arrangement, synthesis, mixing, export, and the CLI.
The JS prototype (`src/engine.js`) stays in the repo as a separate product and a source of ideas. No Rust code refers to it.

## 1. Goals and non-goals

Goals

1. Same instruments, same musical behaviour, same character. Detail may change; every change is measured by the sound gate (section 12).
2. Code that names its algorithms. Each sound model is a small typed unit with a doc comment that gives the algorithm, the source, and the parameters that set the sound.
3. One code path. No sequential/threaded twins, no feature-gated capture paths, no parity shims. `RAYON_NUM_THREADS=1` is the sequential path, and the output is bit-identical at any thread count.
4. Speed and memory are aims, not gates: roughly 2.5 s threaded and 9 s on one thread for the demo, and less memory than today (at design time: 6.3 s, 16.2 s, 0.89 GB, 0.72 GB). The gate fails only on a gross regression (+50%). Clean code comes before the last seconds.
5. Typed data end to end. After the JSON boundary there are no strings in the render path except lyric text and titles, and no panics on user input.

Non-goals

- Sample parity with the JS or with the current Rust output. Every random draw moves (new generator, per-event streams); each song becomes a different take from the same distribution.
- Real-time operation. The renderer is offline. Real-time idioms (denormal hygiene, smoothing, block processing, no allocation in inner loops) are used where they make offline code faster or cleaner, not to meet a deadline.
- New instruments, key changes, melismas, rubato. Out of scope (CLAUDE.md open issue 6).
- New crates are allowed: the workspace is vendored (vendor/). The FFT uses `realfft`/`rustfft`.

## 2. Crate layout

```
sfcore       math, fp (denormals), random (Rng, stream tags), constants
song         typed song model, loose-JSON boundary (wire + Repair), chords, pitch classes, Phoneme, G2P, JSON schema,
             note-event types (events.rs) shared by arrange, instruments, voice and engine
dsp          generic primitives: biquad, onepole, resonator, delay, allpass, smoother, stochastic processes,
             fft, convolution, dynamics, pan, FDN reverb
instruments  sound models: plucked string, guitar sympathetic strings, body IR, bowed string, drum voices
voice        singing voice: parameters per voice type, phoneme acoustics, articulation, LF source, formant tract
compose      form, timeline, rhythm (text setting), pitch (melody Viterbi), melody profile, prepare
arrange      pure event planners: guitar, bass, harp, drums, choir, lines, violin, harmony guitar, vocal singers
engine       tracks and strips, stems, render task graph, mixer
export       Ogg Vorbis, FLAC, WAV writers
songwriter   styles and forms, prompt, schema use, Claude clients
sunflower    CLI
notation     lead-sheet engraving to SVG (song, compose, arrange)
studio       desktop app (eframe), library, playback, mixer
settings     user settings file (`$XDG_CONFIG_HOME/electric-sunflowers/config.toml`)
webrender    browser entry for the engine (wasm, C interface)
soundgate    measurement tool (analysis only; depends on no engine crate)
```

Dependency graph (arrows point at dependencies):

```
sunflower -> engine, songwriter, export, settings, song, sfcore, rayon, clap, anyhow
engine    -> arrange, compose, voice, instruments, dsp, song, sfcore
arrange   -> compose, song, sfcore            (no dsp: arrangement produces events, not audio)
voice     -> dsp, song, sfcore
instruments -> dsp, song, sfcore
compose   -> song, sfcore
songwriter -> song, sfcore, ureq, tempfile
dsp       -> sfcore, rayon, realfft, rustfft
export    -> sfcore, vorbis_rs, flacenc, rayon
soundgate -> hound, serde_json, clap          (own FFT; the instrument must not change with the thing measured)
notation  -> song, compose, arrange
settings  -> song, songwriter, serde, toml
studio    -> engine, compose, arrange, export, songwriter, notation, settings, eframe, rodio
webrender -> engine, song, sfcore
```

Changes against today, and why:

- New `song` crate. Compose, songwriter, arrange and engine all need the song vocabulary (Mode, Meter, SectionKind, DrumKit, Voice, Band). Today it exists as strings in seven places and as two `Band` structs bridged by `sunflower/style_glue.rs`. Songwriter should not depend on composition algorithms, so the model gets its own crate. `style_glue.rs` is deleted.
- New `instruments` crate. `dsp` today mixes generic primitives with product sound models (pluck, violin, body) and the product track table (`mix.rs`). Primitives stay in `dsp`; sound models move to `instruments`; the track table and mixer move to `engine`.
- `arrange` loses its `dsp` dependency. `gen_guitar`, `gen_bass`, `gen_harp`, `gen_drums` interleave planning and synthesis on one random stream, so nothing can be parallelised or tested without audio. Arrange returns events; engine renders them with instruments. The violin, harmony-guitar and vocal-singer plans move from `engine/band.rs` and `engine/vocals.rs` into `arrange`.
- Phonetic acoustics (formant targets, consonant table, loci) move from `compose::phonetics` to `voice::phoneme`. The `Phoneme` enum itself and G2P live in `song`, because the JSON boundary parses ARPAbet.
- `VoiceParams` (Rd, tilt, jitter, formant scales) move from `compose::voices` to `voice::params`. Compose keeps only `Voice` and its range, via `song`.
- `soundgate` is new (section 12).
- `voice` stays separate from `instruments`: it has its own control layer (articulation) and is the largest model.

## 3. Core abstractions

### 3.1 Sample type, rate, blocks

- Audio buffers: `f32`. Coefficients and recursive state: `f64` where precision matters (section 6).
- `sfcore::SR = 44_100` stays the rendering rate. Every coefficient constructor takes `fs: f64`, so a model may run oversampled internally later.
- No global processing block. Each model has its own control rate, named in its module: voice `HOP = 64` (689 Hz frames), violin `CONTROL = 16`, compressor gain update 16. Within a control period, parameters ramp per sample (3.4).
- Stems use `STEM_BLOCK = 4096` frames. The mixer uses `MIX_BLOCK = 1024` frames.

### 3.2 Processors

A processor is a struct with its state and a `tick`:

```rust
pub struct Biquad { c: BiquadCoeffs, s1: f64, s2: f64 }
impl Biquad {
    pub fn tick(&mut self, x: f64) -> f64;
    pub fn process(&mut self, buf: &mut [f32]);
    pub fn reset(&mut self);
    pub fn flush_denormals(&mut self);       // zero state below 1e-25
}
```

Every processor exposes `tick`; the ones used over buffers also expose `process(&mut [f32])`. There is no dynamic-dispatch processor trait: chains are concrete (`Cascade<const N: usize>`, a fixed tract struct) so the compiler inlines them. A small trait `Process { fn process(&mut self, buf: &mut [f32]); }` exists only for the channel strip, whose EQ list is data.

### 3.3 Notes, instruments, voices

- A note event is plain data in seconds and fractional MIDI. The types live in `song::events` so that the producer (`arrange`) and the consumers (`instruments`, `voice`, `engine`) share them without depending on each other:
  `PluckNote { t0, t1, midi: f32, vel: f32 }`, `StringNote { t, stop, string: u8, midi: u8, vel }` (guitar, one list per string), `BowNote { t0, t1, midi, vel, vibrato: bool }`, `DrumHit { t, kind: DrumKind, vel, pan }`, `VocalNote { t0, t1, midi, phones, amp, stress, phrase_start, phrase_end, grace }`, `Singer { voice: Voice, style: SingStyle, notes: Vec<VocalNote>, pan, offset }`.
- An instrument renders events into a buffer: `fn render(&self, notes: &[Note], seed: u64, out: &mut [f32])` (mono, dense slice over the song) or a stereo pair. `engine` binds each track's events to an instrument and a preset and moves the result into a stem. Instruments own every synthesis choice (pick position, brightness, release); arrange never sets synthesis parameters.
- Parameter presets are `const` values of plain structs with `Default` (for example `PluckParams::GUITAR`, `BASS`, `HARP`, `HG_LEAD`, `HG_ARP`). No `Option` fields that mean "use the default"; no zero sentinels.
- A voice is `voice::VoiceSynth`: a `GlottalSource`, a `Tract`, a noise generator. It renders one phrase at a time from `ControlTracks` into a caller slice. `voice::render_phrases(notes, voice, settings, seed, len, emit)` splits the notes into phrases (no gap of 0.3 s or more), builds control tracks for each phrase's frame window only (0.7 s lead, 0.5 s tail, clipped so windows never overlap), renders it into a scratch buffer and calls `emit(start, samples)`; the engine adds that to the stem with pan gains.

### 3.4 Parameter smoothing

- `dsp::smoother::Ramp { value, step, left }`: linear ramp to a target over n samples. Used for control-rate to audio-rate interpolation.
- Voice tract: resonator coefficients are designed once per hop and ramped linearly per sample. For a two-pole resonator `y = a x + b y1 + c y2` the stable region in the (b, c) plane is a triangle, which is convex, so the linear path between two stable designs is stable. Klatt's unity-DC `a = 1 - b - c` is linear in (b, c) and ramps with them.
- Violin: bow velocity, friction slope, and both delay lengths ramp per sample across each 16-sample control period.
- Compressor: gain in dB computed every 16 samples, linearly interpolated in linear gain per sample.
- One-pole smoothing of a control track uses `OnePole::from_tau`. The zero-phase forward-backward smoother (`filtfilt` on a frame track) is `dsp::onepole::zero_phase_smooth` (one track) or `zero_phase_smooth_lanes` (N equal-length tracks in one interleaved loop; unequal lengths are an error) and is used only on precomputed control tracks.

### 3.5 Buffers and ownership

- `engine::stem::SparseBuf { len: usize, blocks: Vec<Option<Box<[f32; STEM_BLOCK]>>> }`. A block is allocated on first write. Reads of an absent block are zeros.
- `Stem = Mono(SparseBuf) | Stereo([SparseBuf; 2])`. Mono stems: lead, harmony, bass, and every bodied instrument before convolution. Stereo: doubles, choir, drums, and the bodied tracks after convolution.
- `SparseBuf::add_at(start: isize, src: &[f32], gain: f32)` clips negative starts; there is no negative-index emulation.
- Scratch buffers (per phrase, per note) are owned by the task and reused through a `Scratch` struct passed by `&mut`. Inner loops never allocate.
- Filters over a `SparseBuf` process present blocks; after a present block, following absent blocks are allocated and processed while the filter state holds energy above 1e-12, then the state is reset and absent blocks are skipped.
- Measured occupancy (4096-frame blocks with signal above 1e-5) on the demo: doubles 30%, harmony 45%, choir 46%, hg 58%, drums 60%, violin 75%, lead 82%, bass 82%, harp 83%, guitar 100%.

### 3.6 Tracks, strips, song

```rust
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum TrackId { Lead, Doubles, Harmony, Choir, Guitar, HarmonyGuitar, Bass, Drums, Harp, Violin }

pub struct Strip {
    pub label: &'static str,
    pub gain: f32, pub pan: f32, pub send: f32,
    pub band: Option<BandPart>,          // None: always on
    pub eq: &'static [EqBand],           // EqBand { kind: EqKind, f: f64, q: f64, db: f64 }
    pub body: Option<BodyMount>,         // BodyMount { body: Body, seed_offset: u32, trim: f32 }
    pub comp: Option<CompSpec>,          // lead, harmony
    pub slapback: Option<Slapback>,      // lead
}
pub const STRIPS: [Strip; 10];            // indexed by TrackId as usize; values in section 5.8
```

- `ProcessedStem { audio: Stem, level: f32 }` is the channel-strip output: EQ and compression applied, `level = 0.1 / gated_rms` applied at pan time (folded into pan gain; the compressor threshold is shifted by `-20 log10(level)` so compressing before scaling equals the current order exactly).
- `Stems { len, tracks: [Option<ProcessedStem>; 10], slapback: Option<SparseBuf> }` is the cache that makes band toggles a re-sum only.
- A song after the boundary is `song::Song` (section 7). `compose::prepare` returns `Prepared { form, timeline, melody, key_shift, voice }`.

### 3.7 Threading

- rayon only. `sfcore::fp::init_pool(threads: Option<usize>)` builds the global pool with a `start_handler` that enables flush-to-zero and denormals-are-zero on each worker. `engine::render` calls `sfcore::fp::flush_denormals()` on the calling thread.
- Render is one `rayon::scope`. Tasks:
  - voice: lead; harmony; doubles take 1; doubles take 2; choir part S, A, T, B (each renders its 3 singers in order into a part stem);
  - band: guitar; harmony guitar; bass; drums; harp; violin.
  Each task runs instrument render, then body convolution (if any), then the channel strip, then stores its `ProcessedStem`. The choir stem is the sum of the four part stems in part order, done when the last part finishes.
- Body convolution parallelises inside its task over contiguous block ranges (3.9).
- Determinism: every random stream is derived from (seed, tag, index) (3.8), never shared between tasks; every sum has a fixed order (singers in plan order, parts S A T B, tracks in `TrackId` order, convolution ranges in time order). Output is bit-identical at any thread count, which a test asserts.
- Progress: `trait Progress: Sync { fn advance(&self, done: usize, total: usize); }` with an `AtomicUsize` count of finished tasks.

### 3.8 Random numbers

- One generator: `sfcore::random::Rng`, xoshiro128++ (Blackman and Vigna 2018), seeded through SplitMix64 (Steele, Lea, Flood 2014).
- Streams: `Rng::stream(seed: u64, tag: Tag)` and `Rng::event(seed, tag, index: u64)`. `Tag` is a `u64` built at compile time by `const fn tag(name: &str) -> Tag` (FNV-1a over bytes). Each crate declares its tags as consts next to their use (`const GUITAR_STRUM: Tag = tag("guitar.strum");`). No central enum, no `format!` tags, no UTF-16.
- Methods: `next_u32`, `uniform() -> f64 in [0,1)`, `bipolar() -> f64 in [-1,1)`, `gauss()` (Marsaglia polar method, second deviate cached), `fill_bipolar(&mut [f32])`, `pick<T>(&[T])`.
- Per-event streams (guitar stroke k, choir singer (part, i), drum hit k) make each event independent of render order and of every other event's draw count.

### 3.9 Memory model

Whole-song stems, block-sparse, are kept; the mix is streamed.

- Per-track loudness normalisation, the bus-compressor threshold and peak normalisation need whole-song statistics, and band toggles re-sum cached stems. Note-at-a-time rendering into stems is simple and parallel. Streaming the render itself is not worth its complexity for an offline renderer.
- Streamed: the mixer (1024-frame blocks produce final L/R only; no full-length main or send buses), voice phrases (scratch), drum hits (render straight into the stem), convolution (per-range buffers).
- Estimate for the demo: 11.1 channel-equivalents of occupied stem blocks x 33 MB = 0.37 GB, plus 66 MB of output, less transient buffers. Target 0.50 GB threaded, 0.40 GB single thread.

## 4. Numeric policy

- Buffers: `f32`. Final mix: `f32`.
- Biquads, Klatt resonators, one-poles with poles near 1 (low cutoffs, long time constants): `f64` coefficients and state. A 30 Hz high-pass at 44.1 kHz has poles within 4e-3 of the unit circle; `f32` coefficient quantisation there moves the corner audibly and raises the noise floor.
- Oscillator phases and phase increments: `f64` (a song is 8M samples; `f32` phase drifts in cents).
- Waveguide and FDN delay lines: `f32`. Loop filters in them: `f64` state.
- FFT: `f32` interleaved complex data, twiddles computed in `f64` and stored as `f32`. Expected round-trip error for n = 65536 is about 1e-6 relative (noise floor near -120 dB), below audibility and below the 24-bit export floor; a unit test asserts it.
- Sums of squares (RMS, LTAS): `f64` per block.
- No JS semantics: `f64::round` for rounding, `f64::max/min`, `exp2` for `mtof`. One seconds-to-sample rule: `sfcore::time::sample_at(t) = (t * SR).round() as isize`.
- Denormals: flush-to-zero and denormals-are-zero on every render thread (x86_64 MXCSR bits 15 and 6, `0x8040`; aarch64 FPCR.FZ), set by `sfcore::fp`. Measured: after exact-zero input, the current `f64` Direct Form I biquad state settles near 1.4e-321 and cycles there forever at 70.7 ns/sample against 4.1 ns with FTZ/DAZ; the demo mix EQ drops from 3.50 s to 1.74 s. As a second guard (for targets without FTZ, e.g. a future WASM build), every recursive filter has `flush_denormals()` which zeroes state below 1e-25, called at stem-block and phrase boundaries.
- NaN: no `partial_cmp().unwrap()` on data; sorts use `total_cmp`. Engine asserts finite output in debug builds and in tests.

## 5. Sound models

Each model: algorithm, source, parameters that set the sound. Values are the current shipped values unless the change list (section 11) says otherwise.

### 5.1 Voice source (`voice::glottal`)

- Algorithm: Liljencrants-Fant glottal flow derivative, parameterised by Rd (Fant, Liljencrants, Lin 1985; Fant 1995 for Ra, Rk, Rg from Rd). Epsilon by fixed-point iteration, alpha by bisection for zero net flow. Tables `D` (derivative, RMS-normalised) and `G` (normalised flow).
- Band limiting: `D` is stored as a mip set of 2048-point single-cycle tables, one per half-octave of f0 from 55 Hz to 1760 Hz. Each level has harmonics above SR/2 / f0_max(level) removed by FFT. Playback reads the two levels that bracket f0 and blends them linearly by position within the half-octave (no clicks when vibrato crosses a level). `G` stays a single table (it only modulates breath noise). Source: the mip-mapped wavetable method, Massie 1998 ("Wavetable sampling synthesis", in Kahrs and Brandenburg, Applications of DSP to Audio and Acoustics).
- Tables are built lazily per Rd step: `static LF: [OnceLock<LfTable>; N_RD]` (legacy) and `static LF_CAPPED` (below), index `round(Rd * 40)`.
- Loudness blend: lax and tense tables mixed by `w = clamp((av - 0.42) / 0.6, 0, 1)`.
- Per-period jitter and shimmer; two cascaded one-pole filters. Legacy source (`fa_hz <= 0`; alto, soprano): both at `tilt * 1.25 * tilt_scale` Hz. Capped source (`fa_hz > 0`; baritone, bass, tenor): the first is the return-phase filter at `Fa(w) = fa_hz * 2^(w - 0.5)` Hz (louder is brighter), set once per hop from the av at the hop start; the second stays at the tilt corner.
- Parameters: per voice type Rd, tilt, `fa_hz`, jitter, shimmer (voice::params), `rd_scale`, `tilt_scale`.
- Baritone Rd was raised from 1.15 (a tense source with H1-H2 near 0 dB) to 1.7; real male singers measure +4 to +5 dB (vocadito). A laxer source lowers the voiced level, and each stem is normalised to a target loudness, so the noise consonants rose by the same amount. `lax_trim(rd)` scales the source by `1 + 0.82 (rd - 1.15)` above Rd 1.15 (x1.45 at 1.7) to keep the voiced-to-noise balance.
- Capped tables (`lf_table_capped`, `LfParams::from_rd_capped`). Rk, Rg, tp, te are computed as above, with Fant's Ra in the Rg formula; then `ta = min(Ra, RA_CAP)`, `RA_CAP = 0.008`; epsilon and alpha follow (zero net flow). Measured gap on sustained notes (15 male-range vocadito singers, 1/3-octave harmonic envelope re the harmonic power in 315-1000 Hz, `tools/voice_target.py`): the baritone was 8 to 9 dB under the singers at 1.6 and 2 kHz and 9 to 12 dB under at 4 to 8 kHz; its H1-H2 was 3.0 to 3.4 dB, the singers' 4.9. Cause: Fant's regression ties Ra to Rd, so the lax tables that give the open quotient also give a return-phase corner `f0 / (2 pi Ra)` of 200 to 330 Hz at f0 = 110 Hz, about 5 dB per octave of extra tilt above it. The singer's-formant cluster (sf 0.45) and the 16 dB shelf then compensated in two places and left holes at 1.6 to 2 kHz and 4 kHz. The capped family removes the tie: the return phase is its own parameter (`fa_hz`). At Rd 2.05 the capped table has more than 6 dB more level at harmonic 16 relative to harmonic 1 (unit test `capped_table_is_brighter_and_both_have_zero_mean`).
- Open-vowel H1-H2. The pooled H1-H2 median is confounded: the real tracks sing at a median f0 of 144 Hz, the engine lead at 192 Hz, and the vowel inventories differ, so it is not a like-for-like target (it read 4.9 dB for the singers and 3.25 for the legacy baritone, but the legacy source was the one that was off). The target is the median H1-H2 over open-vowel frames: the strongest of the first 8 harmonics is harmonic 3 or higher, and f0 <= 200 Hz (`open_h12` in `tools/voice_target.py`). Singers: 2.4 dB [-5.3, 4.5] (729 frames). Legacy baritone: 4.7 dB [3.8, 5.8]. Capped source at rd 1.9: 2.0 dB. Feasible range 0.9 to 3.9 dB. For information the tool also prints, per f0 bin (100-150, 150-200 Hz), the slope of harmonic level against log2 k below 1.5 kHz (singers -9.9, -8.4 dB/oct) and H1 minus the strongest harmonic below 1.5 kHz (singers 0.0 dB median).
- Baritone fit (`tools/voice_target.py`, target `tools/targets/voice_vocadito_male.json`; engine = demo and center-pivot lead stems at seed 1234, frames pooled). Stage 1, harmonic region, J1 = RMS of E - T over 630 Hz to 3.15 kHz with shelf 8, `hf_bed` 0, `cons_gain` 1: `rd` in {1.5 .. 2.3} chosen for open H1-H2 nearest 2.4 dB (1.9: 2.03 dB; the values at 1.5, 1.7, 2.1, 2.3 are -0.13, 1.02, 2.90, 3.66), then `fa_hz` {700 .. 2800}, `sf`, `tilt` by lowest J1 among candidates with vowel distance >= 12.8 dB and open H1-H2 in [0.9, 3.9]; two rounds, from fa 1400, sf 0.15, tilt 6000. Result: `fa_hz` 1400, `sf` 0.15, `tilt` 6000, `rd` 1.9 (J1 3.34 dB; `fa_hz` 700 and 2800 give 4.14 and 3.66, `sf` and `tilt` are flat within 0.4 dB). Stage 2, noise region: for each `shelf_db` in {4, 8, 12, 16}, `hf_bed` bisected until the mean of E at 5, 6.3 and 8 kHz is within 1 dB of the singers'; the gain needed is 0.2 for every shelf. Peak-flr (6 dB, singers 8) does not separate the shelves, so the tie went to the smaller |E - T| at 4 kHz; a trial of `hf_hz` 3000 moved 4 kHz by under 1 dB and put the vowel distance below 12.8, so it was dropped; the choice is `shelf_db` 16, `hf_bed` 0.2, `hf_hz` 3800. The 4 kHz deficit is a tract matter (no resonance between F5 near 3.9 kHz and 5.5 kHz) and is open. The bass figures in this section were measured at `shelf_db` 12 and `hf_hz` 3000 and are not measured again. Stage 3: `cons_gain` bisected to a mean consonant prominence of 11.8 dB (whole-stem minus vowel envelope at 5, 6.3, 8 kHz) within 1 dB: 2.74 (12.3 dB; singers 12.9, 9.4, 13.2). Result against acceptance (not met): J 3.29 dB (aim <= 3; legacy 7.25), max |E - T| 6.5 dB at 800 Hz (+6.5) and 4 kHz (-5.4) (aim <= 5), open H1-H2 2.03 dB (met). Vowel distance 12.99 dB (legacy 13.46; the 12.8 floor of the fit is met). Per f0 bin 100-150 and 150-200 Hz: slope -10.8 and -6.8 dB/oct (singers -9.9, -8.4); H1 below the strongest harmonic -4.0 and -6.4 dB (singers 0.0): the engine's strongest harmonic sits above H1 more often than the singers' (information only, not fitted). Bass: the baritone's `fa_hz`, `shelf_db`, `hf_bed`, `hf_hz` and `cons_gain`; its own `sf` 0.35 and `tilt` 3200; `rd` 1.9, the value of {1.3 .. 2.1} whose open H1-H2 on the center-pivot bass render is nearest 2.4 dB (-0.69, 0.57, 1.72, 2.71, 3.51; 7683 open frames); J 3.31 dB, max 5.97 dB.
- Tenor fit, a reduced form of the baritone's (same male target and open rule: harmonic 3 or higher, f0 <= 200 Hz; same material, with `--voice tenor`). Stage 1 at shelf 8, `hf_bed` 0, `cons_gain` 1, `hf_hz` 3800, tilt 6000, `sf` 0.6 (legacy): `rd` of {1.0, 1.3, 1.6, 1.9} nearest the singers' open H1-H2 of 2.37 dB at `fa_hz` 1400 (-3.99, -1.67, 0.25, 1.84 dB): 1.9; `fa_hz` of {1000, 1400, 2000} by lowest J1 (4.56, 5.00, 5.52 dB): 1000. Stage 2 at shelf 12: `hf_bed` bisected to the singers' mean at 5, 6.3, 8 kHz within 1.5 dB (0: -13.4 dB, 0.6: +6.9, 0.397: +3.4, 0.317: +1.5): 0.3173. Stage 3: `cons_gain` bisected to the consonant prominence 11.86 dB within 1.5 dB (0.5: -8.5 dB, 4.0: -0.05, 3.3: -0.7): 3.3. Result, legacy to fitted: J 7.69 to 4.24 dB (aim <= 4, not met), J1 5.77 to 4.68, max |E - T| 13.2 (5 kHz) to 6.9 (800 Hz), open H1-H2 -3.24 to 1.96 dB (met), consonant prominence 11.21 to 11.17 dB, vowel distance 12.83 to 13.23 dB. E - T per band 630 Hz .. 8 kHz, legacy: 1.0 7.1 4.1 5.4 -4.3 -0.7 6.6 10.4 -3.7 -13.2 -11.6 -11.0; fitted: 0.7 6.9 3.6 4.5 -5.7 -2.6 3.8 6.3 -3.7 1.9 1.5 4.6. Demo lead pitch within 50 cents: 0.946 to 0.994 (octave errors 8 to 0).
- Alto and soprano keep the legacy source (`fa_hz` 0) and now use `rd` 1.6 and `b1_scale` 2.0 (before: `rd` 1.25 and 1.05, `b1_scale` 1.0). Cause: with B1 at 67-73 Hz and F1 near 2 f0, the cascade alone puts H1 10-19 dB under H2 on a, e and o at f0 250-450 Hz. Real singers keep H1 within 1 dB of the strongest harmonic (VocalSet female long tones, 9 singers). The target is the female set (22 vocadito tracks of average pitch >= MIDI 58; `tools/targets/voice_vocadito_female.json`, open rule harmonic 2 or higher and f0 <= 450 Hz). The open H1-H2 statistic depends on f0 and selects on its own outcome, so compare it per f0 bin (250-300, 300-350, 350-400 Hz). Seed 1234, demo and center-pivot:

  | voice | preset | open H1-H2 dB | bin E-T dB | J dB | vowel distance dB | demo pitch / octave errors |
  |---|---|---|---|---|---|---|
  | alto | legacy | -8.52 | -5.74 | 5.09 | 13.73 | 0.916 / 11 |
  | alto | rd 1.6, b1 2 (chosen) | -3.43 | +0.43 | 4.67 | 13.15 | 1.000 / 0 |
  | soprano | legacy | -15.08 | -6.65 | 6.16 | 12.53 | 0.940 / 9 |
  | soprano | rd 1.6, b1 2 (chosen) | -6.88 | +2.09 | 4.90 | 10.82 | 1.000 / 0 |

  The soprano's vowel distance falls by 1.7 dB. The fallback `b1_scale` 1.5 gives 11.36 dB; it is kept for the ear. The female target's open H1-H2 is -2.53 dB and its consonant prominence 13.33 dB. Earlier fits (stage 1 of the reduced fit, capped source) found no `rd` on the old source that brought open H1-H2 within 1.5 dB of the singers'; the B1 width was not varied then.

### 5.2 Voice tract (`voice::tract`)

- Algorithm: cascade formant synthesiser (Klatt 1980; Klatt and Klatt 1990). Resonators are Klatt's unity-DC two-pole sections.
- Five formants F1-F5 plus up to four fixed high resonances at 5.5, 6.6, 7.7, 8.8 kHz times the voice formant scale, in series. F4, F5 and the high resonances are constant per voice and are designed once, not per hop.
- High shelf `shelf_db` (16 dB for every voice but the tenor, 12 dB) at 5.2 kHz (RBJ shelf) after the cascade.
- Frication: white noise through an RBJ band-pass at the consonant's centre and bandwidth, added after the tract, gain 2.2.
- Voice bar (`VOICE_BAR_HZ` 350, Q 0.7). In the closure of a voiced stop or affricate (/b d g jh/; not the flap) the control track `vb` is 1 (smoothed 4 ms, zero-phase). After the cascade, the shelf and the HF bed, the voiced signal is crossfaded to a low-passed copy of itself, y = (1 - vb) x + vb LP(x), LP an RBJ second-order low-pass run on every sample so its state is continuous; at vb = 0 the block is untouched bit for bit. Frication, burst and aspiration enter after or ahead of this stage as before (the frication path is added after it). Reason: with the tract closed, sound reaches the air only through the walls of the throat and cheeks, which pass little above about 400 Hz. The closure's F1 is also min(F1, 250 Hz) (`CLOSED_F1_HZ`, the closed tract's lowest resonance); the burst keeps the old formants.
- Nasal pole-zero pair (Klatt 1980, RNP and RNZ), in series ahead of F1: a pole at 270 Hz, bandwidth 100 Hz (`NASAL_POLE_HZ`, `NASAL_POLE_BW_HZ`) and the zero, the FIR (1/a, -b/a, -c/a) of the Klatt resonator of its own centre and bandwidth. Outside nasals the zero has the pole's centre and bandwidth and the pair cancels: on a non-nasal passage (/s aa t k ae f p ih sh hh uw th/, baritone, 3.5 s, 154350 samples) the output with and without the pair is bit identical (difference 0.0, f32 output; the unit test `nasal_pair_cancels_at_nas_zero` holds the impulse response to 1e-9). In a nasal the zero moves to the nasal's antiresonance, m 1000 Hz, n 1600 Hz, ng 2500 Hz (Fujimura 1962), bandwidth 200 Hz, by the `nas` weight: FNZ = 270 + nas (FNZ target - 270), the bandwidth likewise from 100 to 200. The control track `fnz` carries the target (270 outside nasals) and is smoothed with `nas` (20 ms), so the zero follows the nasal's timing. The zero's coefficients ramp per sample over the hop like F1-F3. The nasal's B1-B3 widening stays.
- DC blocker, pole 0.995.
- Aspiration (white noise times `ah * 0.8`) and breath noise (white noise low-passed at 2.6 kHz by a one-pole, modulated by glottal flow) enter the cascade with the source. CLAUDE.md says aspiration is also low-passed; the code does not. The rewrite keeps the code's behaviour and fixes CLAUDE.md; low-passing aspiration is a later, ear-gated change (section 11).
- F1 is floored at 1.06 f0.
- Coefficient update per hop (64 samples) with per-sample ramps (3.4). Silent frames skip synthesis and reset resonator state.
- Rejected, recorded: a parallel high-frequency branch filled the spectral valleys (/uw/ from -55..-82 dB to -41 dB) and cut vowel distinctness from 13.6 to 10.6 dB.
- Issue 22 switches in `voice::tuning`, all neutral unless stated: `VoiceParams::hf_bed` (0.2 for baritone and bass, 0.3173 for the tenor, 0 for alto and soprano) adds voiced hiss, white noise shaped by the glottal flow and high-passed at `VoiceParams::hf_hz` (3800 Hz for baritone and bass, 3800 for the others), after the cascade. `VoiceParams::cons_gain` (2.74 for baritone and bass, 3.3 for the tenor, 1 for alto and soprano) multiplies the frication output (bursts included) and the aspiration term; breath noise and the bed are not scaled. A high-passed copy of the source pulse did the same and sounded buzzy, so the bed carries noise. `VFRIC_MOD` (0.85, on) modulates the noise of voiced fricatives (/dh v z zh/) by the glottal flow. `FRIC_Q_SCALE` (1, off) narrows the frication band. `NAS_B2_HZ` and `NAS_B3_HZ` (170, 220) set the nasal F2 and F3 bandwidths. `BW_SCALE` (neutral) scales the formant bandwidths and measured no gain. `VIB_RATE_VAR`, `VIB_DEPTH_VAR` and `VIB_WOBBLE2` (0.10, 0.45, 0.04) vary vibrato per note; they are set by judgment, not calibrated.
- Baritone HF bed: with the capped source and no bed, the harmonic envelope at 5, 6.3 and 8 kHz averaged 13.9 dB under the singers'. The bed (`hf_bed` 0.2, `hf_hz` 3800, shelf 16 dB) brings that mean within 1 dB (0.3 dB over); harmonic-peak minus inter-harmonic median at 5 to 8 kHz is 6 dB (singers 8, legacy 7 to 10). The bed raises the vowels but not the consonants: with `cons_gain` 1 the consonant prominence at 5, 6.3, 8 kHz fell from 17.6, 18.1, 17.6 dB to about 5 dB (singers 12.9, 9.4, 13.2); `cons_gain` 2.74 restores it to 12, 14, 11 dB. The lead's active RMS moved by -0.06 to -0.23 dB against the legacy baritone (demo -22.56 dBFS, center-pivot -22.78).
- Measured against 17 male vocadito tracks (`tools/voicecmp.py`, `tools/voiceenv.py`): vowels are 10 dB low at 4 kHz and 30 to 50 dB low at 5 to 8 kHz, so every noise consonant is an isolated bar; its median rise over its surroundings is 30 dB (real 14 dB). `tools/voicescan.py` compares a lead stem with its planned segments (static outside a planned consonant, missing consonants, holes, clicks); the stems example writes `phones.json` and `plan.json` for it.

### 5.3 Articulation (`voice::articulation`, `voice::controls`)

- Algorithm: synthesis by rule with targets and transitions (Holmes, Mattingly, Shearme 1964; Klatt 1987). CV formant transitions over 50 ms from the consonant locus (locus equations, Delattre, Liberman, Cooper 1955). F1 damping during aspiration.
- Pipeline: `plan_segments(notes, params) -> Vec<(Span, Segment)>`, `rasterise(&segments, frames) -> ControlTracks`, `shape_dynamics` (per-note swell, phrase-end fade), `pitch_track` (scoop, grace, one-sided glide), `Vibrato::add` (delayed smoothstep onset, rate wobble), pitch drift (a leaky second-order random walk, `dsp::stochastic::RandomWalk`).
- Per-phrase windows: `controls::Articulation::phrase` builds the tracks for one phrase's frame window, not the song. It plans the phrase's notes and every earlier note that still writes frames at or after the window's start (at least the note before the phrase, whose pause and breath lead in), so a window clipped to start inside the previous phrase keeps that phrase's sounding notes. Where the next phrase's 0.7 s lead would reach back before the previous phrase's end, `synth::phrase_spans` moves the boundary to the middle of the pause before the next onset, so the zero-phase smoothers and the pitch glide restart on flat tracks, not mid-note. The vibrato phase and the drift walk run on from one window to the next, over rendered frames only; frames between windows advance neither.
- Rules kept: flapping of unstressed intervocalic /t d/; onset compression to 45% of the inter-onset interval (less on fast notes, below); coda compression; diphthong glides; a breath segment before phrases.
- Stops: closure with voicing 0.145 (`CLOSURE_VOICING`; voiced stops only, see the voice bar in 5.2), 12 ms burst at gain 0.8, 24 ms aspiration for voiceless onsets. The alternative stop model (closure murmur, soft bursts) measured worse and is deleted, not switched off.
- /ey/ targets ey0 [450, 2020, 2600] and ey1 [340, 2210, 2780] (Hillenbrand et al. 1995).
- Consonant duration scale per voice: bass 1.15, baritone 1.2, tenor 1.25, alto 1.35, soprano 1.4. Voiced "th" af 0.05.
- Fast notes. The consonant rules were set for slow singing. On `songs/firelight-reel.json` (144 bpm, one syllable per eighth, 204 ms notes) the owner heard the words smear. Vowel time per note (the interval from t0 to min(next t0, t1 + 0.05 s) less the planned consonant segments in it; `tools/artic_check.py DIR` on a stems render, seed 1234):

      song          median   25th pct   10th pct   under 80 ms   share med, 10th
      reel          148 ms   115 ms     73 ms      15%           0.69  0.43
      demo          762 ms   226 ms     185 ms     0%            0.88  0.64
      center-pivot  490 ms   240 ms     161 ms     0%            0.85  0.52

  Rate weight w = clamp((0.32 - T) / 0.10, 0, 1), T the note's inter-onset interval (its duration at a phrase or song end): 0 from 0.32 s, 1 from 0.22 s down. At w: consonant scale times (1 - 0.35 w) (burst and flap fixed); onset share the phrasing's less 0.17 w (0.45 - 0.17 w flowing); coda share 0.40 - 0.18 w; longest CV transition (50 - 20 w) ms, velar (80 - 30 w), VC (45 - 15 w), share limits unchanged; diphthong and sonorant-nucleus tail at least (50 - 20 w) ms. Onset durations and share take the weight of the interval the onset sits in (the gap from the previous onset; the first note its own); coda, transitions and tail the note's own. No new random draws. A song whose notes all have T >= 0.32 s renders as before, bit for bit. After:

      song          median   25th pct   10th pct   under 80 ms   share med, 10th
      reel          168 ms   147 ms     114 ms     7%            0.77  0.59
      demo          762 ms   232 ms     197 ms     0%            0.88  0.68
      center-pivot  490 ms   242 ms     165 ms     0%            0.85  0.53

  The reel's notes still under 80 ms are its sixteenths (104 ms intervals), past what the rule reaches. Lead pitch on the reel: 175/194 notes within 50 cents, median 8.6 cents, before; 190/194, 7.0 after. Segments under 15 ms other than bursts on the reel rose from 16 to 42 (mostly compressed closures and the aspiration of a voiceless stop after a sixteenth, which the fixed 12 ms burst leaves at under 1 ms); none vanished. Demo and center-pivot have 53 and 99 notes with w > 0, so their leads change slightly.
- Consonant articulation (November 2026). The owner: "the articulation on the reel is just not good." The isolated lead of `songs/firelight-reel.json` and `songs/center-pivot.json` (seed 1234) was measured (Whisper small.en word error per sung line and which word-initial consonants survive; the level of each planned consonant segment against the vowel next to it). Findings, before the change: word-initial stops survive in 24-32% of words, nasals in 27-33%, glides in 25-33%; the commonest error is the consonant heard as nothing. Voiced stop closures (/b d g jh/) had voicing 0.1 through the full cascade with the vowel's formants, so the closure was a quieter vowel: its minimum was 16-17.5 dB under the vowel before, and its 0.5-5 kHz band only 26 dB under the next vowel. Nasals had voicing 0.55 through F1-F3 with wider B2 and B3 only: above 1.5 kHz the murmur was 11 dB under the next vowel on the reel (a real murmur has a nasal formant near 250-300 Hz, an antiresonance and is 20-30 dB weak above 1 kHz). Liquids and glides (voicing 0.65-0.78) dipped only 2-4 dB. Voiceless closures on the reel were cut by the rate weight to about 35 ms and reached only -32 dB (median; 42% stayed above -30 dB); in the slow song they reached -44 dB. Four changes:
  1. Voice bar: `Segment::Closure` carries `bar`, rasterised into the track `vb`; the synth low-passes the voiced signal at 350 Hz (5.2). Closure voicing 0.1 -> 0.145 (by bisection on the closure's 80-500 Hz band against the next vowel: 0.10 gave -16.0 and -15.8 dB (reel, centre pivot), 0.16 gave -14.4 and -12.4 dB but a 0.5-5 kHz band of -31.98 dB on the reel, short of the -32 dB limit by 0.02; 0.13 gave -15.1 and -14.0 dB; 0.145 gave -14.9 and -13.1 dB with 0.5-5 kHz bands of -32.3 and -38.0 dB). F1 in the closure at most 250 Hz.
  2. Nasal pole-zero pair (5.2): `Consonant::fnz`, `Segment::Nasal::zero`, track `fnz`. Nasal voicing kept at 0.55, 0.55, 0.5 (the 80-500 Hz target was met at 0.55; the alternatives 0.45 and 0.65 were not run).
  3. Liquid and glide voicing: l 0.72 -> 0.45, r 0.78 -> 0.60, w 0.65 -> 0.50, y 0.65 -> 0.50 (dips of about 7, 4.4, 6 and 6 dB under the vowel).
  4. Fast voiceless closures: `MIN_VL_CLOSURE` 0.045 s. After the onset compression, a voiceless stop's closure shorter than min(0.045, its unweighted length) is lengthened to it and the onset starts earlier by the deficit (`Syllable::onset_extra`), while the onset as a whole takes at most `PROTECT_ONSET_SHARE` 0.40 of the inter-onset interval (the cap rises to that for the note, never falls below the rate-weighted share). A coda stop is held the same way within `PROTECT_CODA_SHARE` 0.30 of the span from vowel start to coda end, the time coming from the vowel. When the cap does not allow it the closure keeps what fits. An earlier form, which added the deficit before the compression, left it scaled away again (median 35.1 -> 37.2 ms; 31 of 52 reel closures under 45 ms). Now the reel's median closure is 45.0 ms, 17 of 52 stay under 45 ms (12 under 35 ms) and centre pivot has 7 of 44 (unchanged, cluster compression). The raised caps cost vowel time: on a 0.2 s interval the vowel keeps at least half of it (55% before).
  Sources: Klatt 1980 (nasal pole and zero, antiresonator form); Fujimura 1962 (nasal antiresonances); the voice bar's 350 Hz corner is a judgment from the wall-radiation argument above. Evaluation (`eval_artic.sh`, lead alone; voiceless closure: median minimum level against the vowel before / share above -30 dB; voiced closure and nasal: band levels against the next vowel):

      row                                     reel before   reel after   cp before   cp after
      voiceless closure min (dB)                  -31.9         -35.9       -44.4       -44.5
      voiceless closure above -30 dB              42%           25.5%       11%         9%
      voiced closure 80-500 Hz (dB)               -13.9         -14.7       -15.0       -13.1
      voiced closure 0.5-5 kHz (dB)               -25.9         -33.4       -25.7       -38.0
      nasal 80-500 Hz (dB)                        -4.5          +1.0        +2.4        +8.5
      nasal 1.5-5 kHz (dB)                        -10.8         -17.1       -19.0       -21.5
      Whisper word error, voice                   0.875         0.755       1.318       1.453
      word-initial stops kept                     8/25=0.32     12/25=0.48  9/38=0.24   15/38=0.39
      word-initial nasals kept                    3/9=0.33      5/9=0.56    3/11=0.27   7/11=0.64
      word-initial glides kept                    3/12=0.25     6/12=0.50   8/24=0.33   10/24=0.42
      word-initial liquids kept                   9/15=0.60     9/15=0.60   8/18=0.44   11/18=0.61
      word-initial fricatives kept                31/58=0.53    33/58=0.57  21/30=0.70  19/30=0.63
      SingMOS-Pro, lead                           2.54          2.71        2.67        2.88
      Audiobox CE, lead                           3.95          4.22        5.12        5.19

  The voiceless closure rows on the reel (n 43 before, 51 after) are not exactly comparable: closures lengthened past the 15 ms cut of `segdepth.py` joined the sample. The changes still miss: the reel's voiceless closure (median -35.9 dB against -36, 25.5% above -30 dB against 25%), the reel's nasal 1.5-5 kHz band (-17.1 dB against -20), and centre pivot's word error (1.453 against 1.318; both are above 1, the sign of inserted words; the sub+del rate per reference word, the measure to prefer, is 0.535 and did not move between the two versions: voice 0.500 and 0.535, mix 0.674 to 0.696 on the reel and 0.606 on centre pivot, for reel and centre pivot). The vowel probe stays at 12.98 dB (vowels are untouched).
- Types: `Phoneme` (`#[repr(u8)]`, from `song`), `ConsClass { Sonorant, Nasal, Fricative, Aspirate, Stop, Affricate }`, `Locus { At([f64; 3]), Velar }`, `voiced: bool`. Tables are `const` arrays indexed by `Phoneme as usize`.
- Vibrato and drift (set by judgment, the reference set has too few long notes to calibrate them): `VIB_RATE_VAR` 0.10, `VIB_DEPTH_VAR` 0.45 and `VIB_WOBBLE2` 0.04 vary each note's vibrato rate, depth and rate wobble; baritone `vib_depth` 0.20 (was 0.30) and bass 0.16 (was 0.22). `controls::DRIFT` is (0.000173, 0.985, 0.995, 0.30) (was (0.004, 0.985, 0.998, 0.12)). The walk's unclamped deviation is step times sqrt((1 + a b) / ((1 - a^2) (1 - a b) (1 - b^2))) with a = 0.985, b = 0.995, about 578 x step, so step 0.000173 gives 0.10 semitone (measured 0.097 in a 400000-step test, 0.16 percent at the clamp); the limit 0.30 is 3 deviations. The old walk (step 0.004, b 0.998) had a deviation of about 4 semitones against a 0.12 clamp, so its output sat at the clamp, a two-level offset rather than a drift; the pitch error tracked the limit (11 cents at 0.12, 18 at 0.20, 29 at 0.30). Measured: slow (below 2 Hz) non-vibrato spread W 4.37 cents median (was 5.2), lead median |cents| P 6.4 (was 11.0; target P <= 14.0). The engine was 5.2 cents before; real singers on 5 long segments show about 10 cents (a weak reference).

### 5.4 Plucked string (`instruments::pluck`)

- Algorithm: extended Karplus-Strong (Karplus and Strong 1983; Jaffe and Smith 1983). The loop runs in the velocity domain (a displacement loop differentiated at the output produced a spike every period).
- Loss: one-pole loop filter; its pole is reduced until |H(f0)| >= sqrt(rho), and loop gain is set so the fundamental decays to T60 (loop-filter design after Valimaki, Huopaniemi, Karjalainen, Janosy 1996).
- Tuning: exact one-pole phase delay subtracted; first-order Thiran allpass for the fractional delay, delta in [0.5, 1.5) (Thiran 1971; Laakso et al. 1996).
- Two polarisations: +1.4 and -0.35 cents, the second at amplitude 0.42 and 0.62 T60, summed without coupling.
- Excitation: triangular pick shape with apex at the pick position, circular moving average for finger width, differentiated, circular one-pole for pick release, DC removed, loaded by one warm-up pass through the loop.
- Options: high-passed pick noise; tension pitch glide (allpass coefficient ramped per sample, glide 5 cents); release damping from `release_start`; early stop when the per-period peak falls 74 dB; linear attack ramp; 64-sample end fade.
- High-note level trim: gain times `min(1, L/60)` where L is the loop length in samples (attenuates notes above 735 Hz). Kept and documented.
- Phases are explicit loops (warm-up, attack, sustain, release, fade), so inner loops have no per-sample branches.
- Presets: guitar damping 0.18, harp 0.16, bass 0.5 (bass brightness 0.12); harp T60 `6 * (98 / f)^0.5` s; per-note amp, T60 and pick position come from the event.

### 5.5 Guitar (`instruments::guitar`)

- Six monophonic strings: the next pluck or a changed note on a string stops the previous one (with damping).
- Sympathetic strings: six open-string Karplus-Strong loops (E2 A2 D3 G3 B3 E4) excited by 1.2% of the guitar output. One pass over the output with six ring buffers, branch wrap instead of `%`; blocks where the input is silent and all loops are below -120 dB are skipped.
- Parameters: `GuitarTuning` consts (damping 0.18, attack 1.0, glide 5 cents, sympathetic 1.0).

### 5.6 Bodies (`instruments::body`)

- Algorithm: stochastic modal synthesis of a stereo body impulse response from a measured magnitude curve (commuted synthesis lineage: Smith 1993; Karjalainen and Valimaki 1993). Curves: 1/12-octave `BODY_CURVES` measured from University of Iowa MIS recordings by `tools/extract_curves.py`; guitar with a steel-string correction; harp derived from guitar (no harp reference).
- Modes spaced max(3 Hz, 1.1% f) with +-45% jitter up to 13 kHz; Q ramps q_lo..q_hi over 200 Hz-12.8 kHz; amplitude sqrt(T df / tau) so energy density follows the curve; Gaussian amplitude and random phase. Each mode is a second-order recursive oscillator `y = 2 r cos(w) y1 - r^2 y2`, cut at 9.5 tau (82 dB). Accumulation in `f64`. Unit energy per channel; 10% linear tail fade.
- Fixed low modes (wave 3): below `F_CROSS` = 300 Hz the modes come from the body's own stream, the same for every seed; the independent right draw is the left's amplitude at phase + pi/2 (see stereo coherence below). 300 Hz because the 3 Hz spacing floor holds to 273 Hz, so lower 1/3 octaves hold only 6-20 modes. Above 300 Hz the right channel draws its own amplitude and phase from the seed.
- Stable band levels (wave 3): overlapping random modes interfere, so a free draw scattered about 3 dB per 1/3 octave at every frequency, from seed to seed. Modes are summed in 1/12-octave groups (the curve's resolution, anchored at 300 Hz), merged upward until a group holds at least 3 modes (wave 4: a 1-mode group has 2 real unknowns against 2 constraints, so the step and DC removal left rounding residue that the energy scaling raised by about 1e16; this hit the lowest guitar and harp modes and the partial group under 13 kHz); each group's complex amplitudes are moved by the least weighted change that removes its onset step and its DC, then each channel of the group is scaled to its expected energy. Without the step and DC removal each group's random onset leaks a 1/f skirt above it and a shelf below it. Std over 32 seeds, largest 1/3-octave band from 80 Hz: guitar 5.3 -> 1.2 dB, harp 5.9 -> 1.5, violin 8.0 -> 1.4 (from 160 Hz; it has no modes below 180 Hz). Mean level changes, from the removed skirts: guitar -13 dB at 80 Hz and -6 dB at 100 Hz; harp -10 and -5 dB there and -4 to -7.5 dB from 3 to 13 kHz; violin -3 to -12 dB from 8 to 13 kHz; elsewhere within 2 dB. The IR now follows its curve within 0.8 dB per 1/3 octave above 300 Hz. `crates/instruments/examples/bodyspread.rs` prints the table.
- Stereo coherence (wave 6): independent channel draws gave left/right correlation 0 at every frequency (stems, demo seed 1234: guitar 0.01, harp -0.05, harmony guitar -0.18), a diffuse image that cancels in mono. A real instrument under a stereo pair is one source, most coherent at low frequencies. With z = a e^(j ph) the mode's complex amplitude, the left keeps z_L and the right is `z_R = rho z_L + sqrt(1 - rho^2) z_I`; z_I is the right channel's own draw above `F_CROSS` and j z_L (the old quadrature) below it. `rho(f)` = 1 up to `COH_LO_HZ` = 100 Hz, falling linearly in log frequency to `COH_MIN` = 0.35 at `COH_HI_HZ` = 1 kHz, 0.35 above (`body::coherence`). The draws and their order are unchanged, so the left channel is bit-identical before the group step; the step and DC removal and the energy scaling act on z_R as before. Corners 150 Hz and 2 kHz were tried first and left the guitar stem at 0.87 (its energy is below 400 Hz). Whole-file stem correlation, seed 1234, before to after: demo guitar 0.01 to 0.77, harp -0.05 to 0.60, harmony guitar -0.18 to 0.21, violin 0.40 to 0.64; center-pivot guitar 0.06 to 0.77, violin 0.30 to 0.57. Mono sum, (L+R)/2 against the mean of L and R, 1/3-octave mean: 100-400 Hz guitar -2.8 to -0.3 dB, harp -2.5 to +0.1, violin -2.7 to -0.7; 1-4 kHz guitar -3.6 to -1.6, harp -4.4 to -1.7, violin -5.6 to -3.0. The right channel is a new random realisation of the same expected spectrum: guitar IR over 16 seeds, right minus left mean 1/3-octave level, 100 Hz to 8 kHz, worst 0.70 dB (800 Hz); single-seed stem bands move by a few dB (worst: guitar +2.0 dB at 1 kHz, harp -2.4 dB at 400 Hz).
- Curve extrapolation beyond the measured range: 12 dB/octave both ends.
- Level trim per body (today's `refPeak` divisors, empirical): guitar 5.4, harp 7.3, violin 2.4. Lives in `Body::spec()` with a doc line.
- Harp check against a recording, and why it does not validate the curve (`tools/fetch_harp.sh`, `engine/examples/harpnotes.rs`, `tools/harp_compare.py`, `tools/harp_curve.py`): the only open, isolated-note harp set found is the VSCO 2 CE concert (pedal) harp (CC0; small room, spaced pair plus close microphones), notes G3-F6. A concert harp is not a Celtic or lap (lever) harp: it has a larger soundboard and higher string tension, so it is brighter and rings longer. Nothing in the code or the styles says which harp the engine models. Measured through the guitar curve's analysis (partial levels 20-370 ms after the onset, times k, partials 12 dB over their surroundings), the engine's rendered harp is darker than that concert harp, by 5-10 dB from 500 Hz to 1.2 kHz and by 5-20 dB or more from 3 kHz up (relative to 300 Hz-1.2 kHz), and its ring time is 1.3-4 times shorter below 800 Hz (room tails included). Those gaps are what one expects of a lever harp, so they are not errors. The body row and the T60 preset are unchanged. To validate the row, use isolated notes of the intended instrument (a nylon or gut lever harp, or a wire-strung harp), put them in ref/harp/ as KSHarp_<note>_*.wav and run `harp_compare.py`; `harp_curve.py CORRECTION ALPHA` then writes a row moved by ALPHA times the correction.
- Guitar row refit to steel-string recordings (`tools/guitar_curve.py`, `tools/targets/guitar_guitarset.json`): the Iowa-derived row came from soft nylon notes. Against the median 1/12-octave long-term spectrum of 180 GuitarSet steel-string accompaniment recordings (mono microphone, normalised to the 100-400 Hz band power), the demo's guitar stem was 18 dB low at 1 kHz, 26-34 dB low from 2 to 5 kHz and 15-24 dB low from 95 to 160 Hz. The string loop is linear, so a body-curve correction equals an excitation correction; only the guitar row changed (harp row, string model and channel strips are as before). Rule: per band, row += clamp(T - E, -6, +36) dB, with E the mean over the 8 gate seeds; then up to two passes of row += 0.7 clamp(T - E, -12, 12); peak renormalised to 0 dB, rounded to 0.1 dB. After 3 passes the residual is within 2.3 dB from 190 Hz to 8 kHz; the worst band is +3.3 dB at 170 Hz (limit 3 dB, not met), then -2.8 dB at 90 Hz (below the 100 Hz band edge) and -2.2 dB at 718 Hz. The harp row still derives from the old guitar row.
- Violin row refit to solo violin recordings (`tools/body_curve.py --body violin`, `tools/targets/violin_expressive.json`): against the median long-term spectrum of 17 takes of the Expressive Solo Violin set (Zenodo 5765676, CC BY 4.0, X/Y pair at 1 m; 1/12-octave bands in 1/3-octave windows, relative to the 300-3000 Hz power), the demo's violin stem was within about 5 dB from 400 Hz to 5 kHz except a dip at 1-1.25 kHz (-5 to -11 dB), and fell away above: -7 dB at 6.3 kHz, -15 at 8 kHz, -21 at 10 kHz. Rule: row += clamp(T - E, -6, +20) dB for bands from 250 Hz (below that the reference holds only room noise; those bands keep their shape), E the mean over the 8 gate seeds; then two passes of row += 0.7 clamp(T - E, -12, 12) on 4 seeds (1234 2718 1 7); peak renormalised to 0 dB, rounded to 0.1 dB. After the three passes the largest |T - E| over 300 Hz-10 kHz is +3.1 dB at 678 Hz (limit 3 dB, not met; that band moved little from pass to pass), then +1.8 dB at 1.28 kHz and -1.2 dB at 10.2 kHz; the rest is within 1.3 dB. Cross-check, `songs/center-pivot.json` seed 1234, violin stem against T over 300 Hz-10 kHz: RMS 6.6 dB to 1.8 dB, worst band +20.7 dB at 9.7 kHz to +3.9 dB at 3.0 kHz. The bands below 250 Hz are not fitted.
- API: `Body::{Guitar, Harp, Violin}`, `Body::impulse_response(self, rng) -> StereoIr` (spectra precomputed, 5.10). Do not shorten the IR: the 0.32-0.40 s modal tail is part of the body sound.

### 5.7 Bowed string (`instruments::violin`)

- Algorithm: digital waveguide bowed string (McIntyre, Schumacher, Woodhouse 1983; Smith 1986), friction by the STK bow table (Cook and Scavone, STK `BowTable`): reflection `(|dv * slope + 0.001| + 0.75)^-4` clipped to 1, slope = 5 - 4 * pressure.
- Two delay lines split at the bow point; nut reflection -1; bridge reflection -0.985 times a one-pole (pole 0.2). Loop tuning: nut delay + bridge delay + bridge-filter phase delay = SR / f0.
- Bow position tracks the note (beta 0.13-0.15, 60 ms glide). Bow force 0.6 plus a pitch-dependent term plus attack and re-bow accents. Bow speed with swell and attack.
- Four Ornstein-Uhlenbeck processes (speed, pressure, position, vibrato rate); vibrato with delay and ramp; pitch wander (random walk); bow noise.
- Stroke planner, separate from the string: `plan_strokes(notes, rng) -> Vec<Phrase>` splits long notes, places bow changes, slides and direction.
- Fractional delay reads: 4-point Lagrange (third order) instead of linear. Linear interpolation is a fraction-dependent low-pass, so vibrato currently modulates loop loss and brightness. Lagrange raises high-frequency loop gain; the bridge pole or the 0.985 reflection is re-tuned so the Helmholtz sweep holds (section 12) and the spectrum stays within the gate.
- Control rate 16 samples with per-sample ramps of speed, slope and delay lengths.
- Output taps the incoming bridge wave (documented).
- Measured today (JS): 213/216 single notes and 174/174 phrase notes hold Helmholtz motion; violin scores 0.47-0.74 as violin. The filtered-sawtooth predecessor scored 0.01. Rust, wave 5 (BRIDGE_POLE 0.35, reflection 0.985): 212/216 single notes, 119/120 phrase notes.
- Fast notes (planner only; the string is unchanged). The owner heard the fast bowing of `songs/firelight-reel.json` (144 bpm, eighths of 208 ms) as iffy, the slow bowing as fine. Two causes. The planner slid (`SLIDE_TAU` 70 ms) into half of the leaps of 5 semitones or more whatever the note length, so 30 to 85 ms segments on other semitones sat between the notes: 46 pitch segments for 29 written notes in the first 7.2 s. And a run without a gap was one bow, since a bow changed only on long notes or by the 0.4 draw; a fiddler bows a reel a note or two to the bow. Rules: (1) no slide into a note when it or the note before is shorter than `SLIDE_MIN_DUR` 0.35 s; (2) inside a phrase a note shorter than `FAST_NOTE` 0.30 s takes a new bow (accent, direction reversed) when `FAST_BOW_MIN` 0.16 s has passed since the last bow change, and is slurred otherwise: eighths take a bow each, sixteenths go two to a bow. The random draws are made as before, so slow passages keep their takes.
- Measured by `tools/fiddle_check.py` on the reel's violin stem, seed 1234, notes from `examples/violin_notes.rs`; 117 notes under 0.3 s (112 written):

        fast notes (< 0.3 s)                 before         after
        settled share (30 cents), median     0.838          0.842
          quartiles                          0.789-0.842    0.816-0.865
        time to settle, median / 90th        34 / 188 ms    32 / 41 ms
        never settled                        16/117         0/117
        sounding frames periodicity < 0.7    3.1%           3.8%
        note level sd                        2.15 dB        2.07 dB
        pitch segments / written notes       150/112        128/112
        first 7.2 s: segments / written      46/29          33/29

  The settled share cannot reach 1: a 30 ms frame straddles the note boundary for 15 ms at each end of a 208 ms note. The periodicity share rose because every eighth now has a bow change. Slow notes are unchanged (settled 0.976/0.975, 0.2%/0.3% under 0.7). Helmholtz 212/216 and 119/120, unchanged. The demo's violin changes (18 of its 90 violin notes are under 0.3 s), so its mix checksum is re-pinned.

### 5.8 Drums (`instruments::drums`, `instruments::drum_data`)

- `DrumKind { Kick, Snare, Rim, Tap, Swish { dur }, Hat, Shaker, Tom { hz }, Ride }`, rendered in place into the stereo stem with pan gains. A `DrumKit::None` song produces no drum stem.
- Voices. Kick, Snare, Rim, Hat, Tom and Ride are modal (below). Tap, Swish and Shaker are recipes (Brushes, Shaker).
- Finding. The first drums were recipes: a kick sine swept 119 to 47 Hz, a 188 Hz sine plus high-passed noise for the snare, six square oscillators for the ride, high-passed noise for the hat. Every hit of a kind was one sound scaled by velocity. Against a real acoustic kit they differ in three ways: (1) a real drum is a dense set of decaying modes with a broad spectrum (the snare is within 12 dB from 250 Hz to 4 kHz; the kick has body from 100 Hz to 3 kHz); (2) brightness follows velocity (kick +7 dB at 2 kHz and +12 dB at 4 kHz from soft to hard; a brush tap gets darker when hard); (3) no two hits are alike.
- Reference: DRSKit 2.1 (DrumGizmo team, drumgizmo.org), CC BY 4.0, 13-channel 32-bit float at 44.1 kHz. Only statistics enter the repository (`drum_data.rs`, `tools/targets/drums_drskit.json`), generated by `tools/drum_target.py ref`. Mapping: Kick = Kdrum_without_contact, Snare = Snare, Rim = Snare_rim, Tap = Snare_whisker (brush tap), Swish = Snare_circle_whisker (brush swirl), Hat = Hihat_closed, Ride = Ride_tip, Tom = Tom2. Shaker has no reference. Tap and Swish were measured but are not modelled from the kit (see Brushes below).
- Perspective. Cymbals and brushes (Hat, Ride, Tap, Swish, Rim) use the mean of the overhead pair (channels 6 and 7). Kick, Snare and Tom use the average of the energy fractions of the overhead pair and of the close microphone (Kick channel 5, Snare 10, Tom2 12), each normalised to its own total; their decays, strong modes and attack come from the close microphone.
- Statistics, per voice and velocity layer (samples sorted by `power`, split in three; the hard layer takes the remainder). Onset: 10% of the peak of |x|, less 32 samples.
  - Band curve: energy of the first 0.6 s in 1/3-octave bands 31.5 Hz to 16 kHz (28 bands), dB relative to the hit's total (the sum of the bands); mean of the dB values over the layer.
  - Decay: per octave band 63 Hz to 16 kHz (9 bands), the time t20 for the band's energy envelope (FFT band mask, 5 ms frames, 3-frame smoothing) to fall 20 dB from its peak, mean over the samples that fall 20 dB within 3 s; a band with none takes the nearest valid band. Conversion: energy falls as exp(-2 t / tau) for an amplitude time constant tau, so tau = t20 / ln 10. (The task's `t20 / (2 ln 10)` is the time constant of the energy; the modes need the amplitude's, twice that.)
  - Level: mean over samples of 20 log10 of the RMS of the first 0.3 s, less the hard layer's. Soft/medium: kick -15.5/-5.8 dB, snare -15.3/-4.5, rim -11.3/-3.9, hat -18.8/-4.5, tom -14.6/-5.0, ride -19.3/-7.9.
  - Attack: median time from the onset to the peak of |x| within 50 ms.
  - Strong modes (medium layer; Kick, Snare, Tom, Ride, Rim): up to 4 peaks of the mean long-window spectrum (first 1 s, each sample normalised to its total) below 600 Hz (ride 1 kHz) that stand 8 dB above its 1/3-octave mean. Level: energy within +-4 bins of the peak over the total; tau from the decay of a narrow band (+-max(3 Hz, 4% f)), median over the samples. Found: snare 4, ride 4, tom 4 (one at -61 dB), kick and rim none. The tom's fundamental, `TOM_F0`, is the strongest peak of that spectrum between 60 and 300 Hz: 79.4 Hz.
  - Swish (sustained, 4 samples): the band curve of the middle half of each sample, one layer.
- The model. A hit is a stochastic modal impulse response, as in 5.6 but with a decay time per frequency and no curve fit loop. Modes from 30 Hz to 16 kHz, spaced max(4 Hz, 1.5% f) with +-45% jitter of the spacing (about 335 modes). A mode at f is `a r^i sin(w i + ph)`, `r = exp(-1 / (tau fs))`, run by the recursive oscillator of 5.6 for 7 tau (or the length of the voice). tau(f) is the layer's octave table interpolated in log tau over log f, held at the ends. `a = sqrt(T(f) df / tau(f)) |z|`: T the band curve's power density (curve dB less 10 log10 of the band width, interpolated in dB over log f, held beyond the end bands), df the mode's own spacing, z = g e^(j ph) with g standard normal and ph uniform. Before the random modes are drawn, each strong mode's power (its level against the hit's total) is subtracted from the curve's power in the 1/3-octave band that contains it, floored 12 dB under the band: the curve already holds the mode's energy. Then the strong modes are added with their tau and an amplitude fixed by their level (energy p of the hit's total, so p / (1 - sum p) of the random modes' expected energy), random phase, no random amplitude. Onset: a raised-cosine ramp of the layer's attack time (at least 0.5 ms), then DC removal with a zero-sum 5 ms pulse; the last 10 ms fade linearly. Each variant is scaled so that the RMS of its first 0.3 s is the layer's level times `REF_RMS`. Voice length (`hit_len`): 7 of the longest tau among the octave bands whose curve is above -35 dB in any layer, at most 3 s, at least the old voice's length: kick 0.93 s, snare 0.53, rim 0.34, hat 0.47, tom 3.0, ride 3.0.
- Tom `hz`: the curve and the strong modes move along the frequency axis by hz / `TOM_F0`; the decay table stays at its absolute frequencies. One pool per distinct hz, sharing the tom's mode table (one drum, retuned).
- Velocity layers. Soft, medium and hard are built separately. A hit at velocity v blends the two layers around it linearly in amplitude, centres at v = 0.25, 0.6, 0.95, clamped at the ends, and is scaled by v as before. `REF_RMS` fixes the hard layer's first-0.3-s RMS at unit velocity to the old voice's (measured at v = 0.95 over 8 hits, divided by 0.95: kick 0.3709, snare 0.1898, rim 0.06223, hat 0.07553, tom 0.4538, ride 0.05536), so a hit at v = 0.95 keeps its old level (all within 0.01 dB) and the kit keeps its balance; softer hits are now quieter than v alone makes them, by the layer levels.
- Variants. 4 impulse responses per voice and layer. A mode's complex amplitude in variant k is `0.9 z_kit + sqrt(1 - 0.81) z_k`: `z_kit` from a stream fixed per voice (the same for every seed, as the fixed low modes of a body), `z_k` from the seed. Mode frequencies come from the fixed stream. The draws do not depend on the layer, so the layers of one variant are coherent and a blend is a morph. A hit takes its variant from its own event stream (`Rng::event(seed, DRUM_HIT, k)`), one draw made last, over the three variants other than the previous hit's of its voice (`pick_variant`). Pools are built once per render, lazily per voice and layer, held in `DrumScratch` (which carries the seed); no allocation per hit afterwards. Build time on this machine (4 cores, one thread): kick 0.07 s, snare 0.11, rim 0.08, hat 0.12, tom 0.13 per tuning, ride 0.64 (all three layers); 1.4 s for every voice. The demo builds kick, snare, hat, ride and its toms.
- Brushes (Tap, Swish): the earlier recipes, not modal. The modal versions (a tap from the Snare_whisker statistics; a swish as noise convolved with a 30 ms modal impulse response from the Snare_circle_whisker curve) sounded like bursts of static in the demo (1:17 to 1:27); the owner chose the earlier recipes by ear, and they were put back as they were, with the same draws, so that a hit is sample-identical to the one before the modal change. Tap, 0.25 s: head = 186 Hz sine x 0.35 + noise band-passed 190 Hz Q 6 x 1.6 + 330 Hz Q 5 x 0.9, decay 0.07, 2 ms attack; snare wires = noise band-passed 3.8 kHz Q 0.9 x 0.75, decay 0.1, 4 ms attack. Swish, `dur` (default 0.5, clamped to 0.02 to 4 s): noise band-passed 3.6 kHz Q 0.7, high shelf -6 dB at 7 kHz, x 0.2, plus noise band-passed 200 Hz Q 4 x 0.25, times the envelope `sin(pi x)^1.5 (0.6 + 0.4 |sin(2 pi x)|)` over x in [0, 1) (the swell, and the stroke modulation of the swirl). The sine is the magic circle oscillator (Gordon and Smith 1985); a hit fills its noise buffers in the order wires, head 1, head 2 (tap) or 3.6 kHz, 200 Hz (swish) from the hit's own stream. These two have no layers and no variants: they do not change with velocity except by the gain. Their reference statistics stay in `tools/targets/drums_drskit.json` (and `tools/drum_target.py ref` writes them), but `drum_data.rs` holds no table for them and `eng` leaves them out.
- Shaker. Noise band-passed 6.2 kHz Q 1.3, 12 ms attack, decay 0.045 s, x 1.4.
- Removed from the modal voices: the PolyBLEP squares and the ride cluster, the kick sweep, and the per-kind noise recipes. What stays of the old building blocks are the exponential decay, the linear attack and the sine oscillator, for the tap and the shaker, and the swish's noise filters.
- Checks: `crates/instruments/examples/drumhits.rs` writes 8 hits per kind at v = 0.25, 0.6, 0.95 (`--time` prints the pool build times); `tools/drum_target.py eng DIR --target tools/targets/drums_drskit.json` compares them with the target. To regenerate the data: `tools/drum_target.py ref KIT_DIR --json tools/targets/drums_drskit.json --rust crates/instruments/src/drum_data.rs`.
- Measured, seed 1234, 8 hits per cell (curve RMS against the target over the bands from 63 Hz to 12.5 kHz where the target is above -35 dB; worst band; decay ratio engine / target per octave band):
  - Curve RMS (dB), soft/medium/hard: kick 1.9/1.7/1.7, snare 2.9/2.1/2.2, rim 2.5/2.2/2.3, hat 2.1/1.8/1.7, ride 1.3/1.2/1.4, tom at 85 Hz 2.9/2.4/2.2, tom at 110 Hz 3.7/3.3/3.1 (the modal tap, 2.1/2.2/2.1, and swish, 3.7, were withdrawn). Worst band: within 6 dB except snare (+7.0 at 315 Hz, +6.3 at 125 Hz, +6.0 at 315 Hz; +7.8, +8.0, +7.0 before the strong modes' power was taken from the curve), rim soft (-7.2 at 160 Hz) and tom at 110 Hz (+7.6, +7.9, +8.5 at 100 Hz). The tom's excess is not at a strong mode; its cause is not found.
  - Decay ratio inside 0.6 to 1.6 for 154 of 166 octave cells. Outside: kick 125 Hz 0.55 and 0.56; rim 125 Hz 1.69 and 1.65; ride 250 Hz 1.88 (soft) and 2.03 (hard), 500 Hz 0.41 (soft); tom at 85 Hz 63 Hz 0.23 and 0.23, 125 Hz 2.06; tom at 110 Hz 125 Hz 0.32 and 0.59. The low bands hold few modes (not measured further); a hit length of 3 s did not move the 63 Hz tom band.
  - Soft-to-hard change of the 2 kHz and 4 kHz octave-band share, engine less target: kick +2.4 and +2.4 dB, snare -1.2 and -0.7, rim +0.8 and -0.3, hat -0.3 and -0.3, ride -1.1 and -0.6, tom at 85 Hz -1.0 and -1.4, tom at 110 Hz -0.9 and -2.4: all within 3 dB.
  - Variation, snare at v = 0.6, 8 hits: the largest lag-0 normalised cross-correlation of consecutive hits is 0.87. With 4 variants, hits two or more apart can be identical; the requirement is that consecutive hits differ.
  - Whole song, demo at seed 1234 (drum stem active RMS, peak; mix active RMS, peak 0.89 always): old drums 0.0831 (-21.6 dBFS), 1.11; mix -16.2 dBFS. Modal drums without the clip: 0.0859, 2.05; mix -20.3. With the soft clip: 0.0799 (-22.0), 1.12; mix 0.1208 (-18.4). The real kit's transients are as sharp as the model's (crest factor of the first 0.3 s: kick 6.9-8.0, snare 7-8, tom 4.9-7.2; the model's kick 7.2; the old sine kick 2.8). Render 2.72 s to 2.62 s (stems example), demo command 4.3-4.9 s to 4.3-4.5 s. No NaN or infinity in any stem.

### 5.9 Bass

- Root-fifth line with approach notes (arrange). Rendered as a dark pluck (brightness 0.12, damping 0.5) plus a sine sub layer (30 ms attack, 0.7 k s decay, 2600-sample release ramp), k = `guitar::BASS_ENV_K` = 0.44, which also scales the bass pluck's T60 (the bass preset only). The sine is a recursive oscillator; the decay is a one-multiply recursion. Each onset adds a thump, the string bass's kick: a sine that falls from 1.5 f0 to f0 in 15 ms and decays with a 70 ms time constant (level 0.7 v), plus a finger click of white noise low-passed at 1.4 kHz with a 6 ms decay (level 0.35 v), 300 ms long at most; the click draws from `Rng::event(seed, bass.thump, k)`.
- Fit to real pizzicato (`tools/bass_target.py`, `tools/targets/bass_iowa.json`; University of Iowa MIS double bass pizzicato mf and ff, 92 notes for the spectrum, 42 mf notes for the envelope). Reference envelope (5 ms energy frames, 4-frame smoothing; onset at 2% of the file's maximum): peak 35 ms after the onset, 10 dB down at 0.16 s, 20 dB down at 0.50 s (medians). The engine's notes (`crates/instruments/examples/bassnotes.rs`: MIDI 34, 38, 41, 43, 46 at velocity 0.8 through `render_bass`, no strip) fell 20 dB in 1.11 s. k was bisected in [0.15, 1] until the median time to -20 dB was 0.50 s within 0.05 s: k = 0.44 gives peak 15 ms, -10 dB 0.20 s, -20 dB 0.505 s. The peak stays earlier than the reference's: it is set by the thump and the pluck, not by the sub layer's attack. Spectrum: the stem was within 4 dB of the reference (first 0.5 s of each note, unit RMS, relative to 40-400 Hz) from 63 to 200 Hz, but 5-10 dB high at 250-500 Hz, 10-14 dB high at 630-800 Hz and 16-23 dB high from 1 to 2.5 kHz. The strip's LP 2000 Hz (Q 0.7) became LP 450 Hz, Q 0.6 (5.13). Corner chosen from {350, 450, 550, 700, 900} Hz by the RMS of E - T over 200 Hz-3.15 kHz, E the mean of the bass stems of the demo and center-pivot at seed 1234 with the new envelope: 2000 Hz (old) 15.7 dB; 350, 4.6; 450, 4.1; 550, 5.3; 700, 7.3; 900, 9.5 (the stems with the old low-pass, the filter swapped in their power spectrum; the 450 Hz render measures the same 4.14 dB). E - T at 450 Hz: within 4 dB from 63 Hz to 2 kHz except +5.4 dB at 250 Hz and +7.0 at 315 Hz; -8 dB at 3.15 kHz and falling above (the reference's finger and string noise). Guards, seed 1234, demo / center-pivot: bass stem active RMS 0.150 to 0.144 / 0.150 to 0.145, peak 0.78 to 0.76 / 0.78 to 0.74; mix peak 0.89 in both; no NaN or infinity.

### 5.10 FFT and convolution (`dsp::fft`, `dsp::conv`)

- FFT: typed wrappers over `rustfft` (mixed-radix Cooley-Tukey, radix 4/8 butterflies, AVX or SSE kernels chosen at run time) and `realfft` (a real transform of size n from a complex transform of n/2 plus the split step; Sorensen, Jones, Heideman, Burrus 1987). Interleaved `f32` complex, caller-owned scratch, length mismatches returned as errors. The hand-written Stockham transform first planned here was not built.
- Convolution: overlap-add (Stockham 1966). `StereoIr::new(l, r)` precomputes both half spectra once. Per block: one forward real FFT, two complex products, two inverse real FFTs (1.5 full-size transforms per block, down from 2). FFT size n = next power of two >= 4 * IR length. All-zero input blocks are skipped.
- Parallel: rayon over contiguous ranges of blocks; each range writes its span plus the IR tail to a private buffer; ranges are added in time order.
- Target: <= 0.5 ms per 65536-point complex transform on one core of this machine (i7-9750H), against 2.4 ms today.

### 5.11 Dynamics (`dsp::dynamics`)

- `PeakDetector { attack, release }`: branching one-pole on |x|. `GainComputer { thr_db, ratio, knee_db }`: quadratic soft knee (Giannoulis, Massberg, Reiss 2012, JAES). `Compressor { det, gc, link: Mono | StereoMax }`, gain updated every 16 samples, linearly interpolated per sample.
- Lead and harmony: 3:1, attack 8 ms, release 150 ms, knee 6 dB, threshold 1 dB above the 0.1 target level. Bus: 2.2:1, 20/300 ms, knee 10 dB, threshold bus RMS + 5 dB, stereo linked by max(|L|, |R|).

- Crest limiter (`dsp::dynamics::crest_limit`), run once over the whole mix after the bus compressor: stereo linked, offline, look-ahead W = 5 ms (221 samples). Required gain r[n] = min(1, c / max(|L[n]|, |R[n]|)); m[n] = min of r over [n, n + W] (monotonic deque, O(n)); release s[n] = min(m[n], s[n-1] + (1 - s[n-1]) k), k = 1 - exp(-1 / (0.080 fs)), s[-1] = 1; attack g[n] = mean of s over [n - W, n] (indices before 0 count as 1). Each s[j] in that window is at most r[n], so g[n] <= r[n] and no sample passes c. Gain math in `f64`, sequential, so the result is the same at any thread count. With no sample over c it does nothing, bit for bit. c is 12 dB over the active RMS of the mix (see 5.13).

### 5.12 Reverb and slapback

- `Fdn16`: feedback delay network (Jot and Chaigne 1991). Sixteen lines, primes 887-4999 samples (sum 43460, 0.99 s) plus a seed offset `(seed (d mod 7)) mod 31`, in one contiguous power-of-two buffer. Householder feedback I - (2/N) 1 1^T. Per-line one-pole absorption from two T60s (1.6 s at DC, 0.8 s at Nyquist). Four series Schroeder allpasses (Schroeder 1962), lengths 142, 379, 107, 277, g 0.58-0.65. 16 ms predelay; 220 Hz high-pass (Biquad) on the input; input gain 0.5 sqrt(8/16) into every line; side signal 0.15 (L - R) with the sign alternating in pairs of lines. Outputs L = c (o0 - o2 + o4 - ... - o14), R = c (o1 - o3 + ... - o15), c = sqrt(4/8); no cross terms. Lines 0, 3, 6, 9, 12, 15 are read at d_k + 8 sin(2 pi f_j n / fs + 1.1 j), f_j = 0.31, 0.43, 0.59, 0.71, 0.83, 0.97 Hz, n the sample count since construction, linear interpolation; the other lines read integer delays. `process_block(send: [&[f32]; 2], out: [&mut [f32]; 2], wet)`; sequential, so identical at any thread count.
- Finding. The eight-line `Fdn8` (1433-2903 samples, sum 17100, 0.39 s; T60 2.2 s at DC) had a mode every 2.6 Hz, each 1.0 Hz wide at the low-frequency T60: the modes stood apart and the tail rang at discrete frequencies (metallic), with no modulation to blur them. Its impulse response had an 11.3 dB modal spread about the 1/3-octave trend (a diffuse field gives about 5.6 dB).
- Wet. `WET = 0.55 * 10^((E_old - E_new)/20)` rounded to 2 decimals, E the total energy of the both-channels impulse response at wet 1.0: 0.55 * 10^((4.82 + 0.45)/20) = 1.01.
- Measurement: `cargo run --release -p dsp --example reverbir -- PREFIX` (impulse on both send channels, seed 1234, wet 1.0, 4 s), then `python3 -I tools/reverb_check.py PREFIX-both.wav`. Echo density after Abel and Huang 2006 (20 ms window); modal spread over 200 Hz-2 kHz of the left channel from 0.3 to 2.3 s.

      measure                          old (Fdn8)   new (Fdn16)
      T60 125 Hz (s)                   2.20         1.64
      T60 250 Hz                       2.19         1.66
      T60 500 Hz                       2.20         1.67
      T60 1 kHz                        2.18         1.60
      T60 2 kHz                        2.08         1.53
      T60 4 kHz                        1.85         1.38
      T60 8 kHz                        1.44         1.11
      echo density reaches 0.9 (ms)    109.6        143.9
      echo density 30/60/100/200 ms    0/.38/.82/.97  .06/.64/.85/1.01
      modal sd about trend (dB)        11.25        7.69
      peaks > 10 dB per 100 Hz         2.44         0.89
      L/R tail correlation             -0.142       0.006
      total energy (dB)                4.82         -0.45

- Targets: modal peaks under half the old count, T60 at 500 Hz in 1.2-1.7 s and |correlation| < 0.3 are met. Missed: echo density reaches 0.9 later than before (the density is higher at 30-100 ms but stays just under 0.9 until 144 ms); the modal spread is 7.7 dB, not 5.0-6.2; T60 at 4 kHz is 1.38 s, not 0.8-1.3 (the absorption one-pole reaches 0.8 s only near Nyquist). Constants were not tuned against these.
- Slapback (lead only): 340 ms feedback comb, feedback 0.22 through a 3.2 kHz low-pass (Biquad), level 0.07, added to main and send buses.

### 5.13 Mix

- Per track: EQ cascade (TDF-II biquads), gated loudness `active_rms` (2048-sample blocks above 5% of the loudest block; kept unweighted, see section 11), compressor where set, level to 0.1.
- Pan: mono tracks equal-power `(cos, sin)` of `(pan + 1) pi / 4`; stereo tracks the balance law (near channel at unity, a cos/sin part of the far channel folded across). One `dsp::pan` module.
- Strip values (gain, pan, send; EQ):
  - lead 1.0, 0, 0.20; HP 90/0.7, PK 250/1.0/-1.5, PK 2900/1.0/+1.5; comp; slapback
  - doubles 0.30, 0, 0.34; HP 140, HS 6000/-6
  - harmony 0.40, 0.28, 0.32; HP 130, HS 6500/-5; comp
  - choir 0.36, 0, 0.50; HP 120, LP 6500
  - guitar 0.62, -0.20, 0.16; HP 70, PK 115/0.9/+3, HS 9500/-2; body guitar
  - harmony guitar 0.36, 0.45, 0.28; HP 120, HS 9000/-2; body guitar, seed offset 17
  - bass 0.50, 0, 0.04; LP 450/0.6, PK 85/1.0/+2
  - drums 0.42, 0, 0.14; HP 30
  - harp 0.36, 0.34, 0.40; HP 75, PK 180/0.8/+1.5; body harp
  - violin 0.34, -0.40, 0.42; HP 190, HS 2800/+7, HS 7000/+5; body violin
- Block mixer, 1024 frames: clear main and send; add each enabled stem in `TrackId` order with pan gain times strip gain times level; add slapback; run `Fdn16`; write L/R; accumulate bus power (every sample above -80 dB).
- Drums: soft clip `y = c tanh(x / c)` at c = 12 times the target level, after the level normalisation (`Strip::clip`, `track::DRUM_CLIP`), for the transients of the modal drums; no other strip has one and a strip without it is untouched.
- Then the bus compressor pass, the crest limiter (5.11) and peak normalisation to 0.89 (sample peak). The 0.89 target stays. The limiter ceiling is `mix::CREST_DB` = 12 dB over the active RMS of the mix (both channels pooled, same 2048-sample, 5% rule as the strips). Before it, only 0.03 to 0.05% of the samples lay more than 12 dB over the active RMS, so a few peaks set the loudness: active RMS -16.1 dBFS (old drums demo), -18.2 (modal drums demo), -16.9 (center-pivot). After it every mix has crest 12 dB and active RMS near -13 dBFS (measured numbers: see the commit message).

## 6. What is deleted

- `sfcore::js` (JS number semantics, `f32r`, JS `round/min/max/pow`), `sfcore::v8math`, the `v8` feature, `V8_EXACT`, `core/examples/{mathprobe,wideprobe,mathbench}.rs`, `core/tests/parity_math.rs`, the `libm` dependency if unused.
- `sfcore::rng` (mulberry32, UTF-16 FNV `hash_str`, `rng_for`), the xorshift generator inside `voice::synth`.
- `sfcore::tuning::Tuning` as a mutable bag. Live values become named consts in their modules; dead switches go: `vf.legacy`, `vf.trans`, `vf.vbar`, `vf.nlp`, `vf.b1x`, `hfg`, `choir_vowel` as a string.
- All parity tests and data: `crates/*/tests/parity_*.rs`, `engine/tests/threaded_parity.rs`, `tests/parity/`, `ref/parity/`, the `engine/capture_raw` feature and `RenderedSong::raw_tracks`.
- Twins and aliases: `render_song`/`render_song_threaded`, `mix`/`mix_threaded`, `mix_song`/`mix_song_threaded`, `process_track`/`process_tracks_threaded`, `render_choir`/`render_choir_threaded` and `CHUNK`, `run_bq`/`run_bq_into`, `make_fft`, `ks_pluck`, the `--sequential` flag.
- Option bags and JS defaults: `PluckOpts`, `VoiceOpts` with `or_falsy/or_null/resolve_rng`, `dsp::or_default/truthy`, `ViolinNote.vib: Option<f64>`, `knee: Option<f64>`, `hf_gain`, `breath_amt`, `VoiceParams.oq`.
- String keys: `TrackSpec.key`, `track_index`, `eq_for`, `body_of`, `Body::from_name`, section/mode/meter/guitar/drums/break-lead strings, `seg_kind`, `cons_kind`.
- Dead code: the non-legacy stop branch; `hy1`; the `r.next() < 0.0` climax-strum draw; NaN-path and discarded draws in `lines.rs`; unused `_lead`, `_song`; the reverb no-op branch; negative-index loops in bass and pluck; `qual_key_order`; the U+2669 replacement; `song_direction`'s placeholder draws; `prof.tess.inst`/`prof.shape.inst` draws made only to keep stream order; compose pass 2 in `prepare` (after the equivalence test); `style_glue.rs`; `_assert_track_count`; per-track wrappers in `engine/band.rs`.
- `dsp::noise::noise_buf` (replaced by `Rng::fill_bipolar` into scratch).
- `hound` from `export` (a direct WAV writer replaces it; `soundgate` keeps `hound` for reading).

## 7. The model-JSON boundary

- `song::wire::WireSong`: serde structs with `#[serde(default)]` and small lenient deserialisers (chords as array or comma/pipe string; tempo as number or numeric string; section type aliases).
- `song::wire::normalize(w: WireSong) -> Result<(Song, Vec<Repair>), SongError>`.
  - `SongError` (hard): not JSON; no section with a parseable lyric line; no parseable chord anywhere.
  - `Repair` (soft, listed, never silent): `DefaultedField(&'static str)`, `ClampedTempo { from, to }`, `DroppedChord { section, bar, symbol }`, `UnknownChord { symbol }` (bar left without that chord), `PhonemeFallback { section, line, syllable }` (G2P for one syllable when its ARPAbet group is missing or unparseable; the whole line falls back only when the group count cannot be aligned), `MissingRepeatSource { section }`, `ModeFromKey`.
- Fixes at the boundary: case-insensitive mode suffix (`m`, `min`, `minor`) in the key; `bass` accepted as a voice; strings truncated by chars.
- After normalise, the song is fully typed and every value is in range. The render path has no `Result` and no panics on data; invariants use `debug_assert!`.
- `song::schema::json_schema()` generates the reply schema's enum lists from the same enums, so schema and parser cannot disagree.
- The CLI prints repairs as warnings and writes them beside the raw JSON on `write`.

## 8. Composition and arrangement (kept algorithms)

- Rhythm (`compose::rhythm::set_text`): Viterbi over a monotone alignment of syllables to metric-grid onsets (an explicit-duration model: state is the onset slot, transition is the gap), with perturb-and-MAP noise (Papandreou and Yuille 2011). Weights in a named `RhythmWeights` const; integer gaps in half-slot units; one fallback that quantises to the grid.
- Pitch (`compose::pitch::pitch_line`): second-order HMM Viterbi over (previous, current) pitch pairs; emissions for chord-tone fit, contour target, reference line, cadence, continuity; transitions for interval size, tritone, leap recovery (gap fill, Meyer 1956), anti-wobble; perturb-and-MAP noise. `PitchWeights` const; `Cadence` enum; emissions precomputed into a matrix; flat back-pointer array.
- One `compose_line(&LineSpec, rng) -> LineMelody` for vocal and instrumental lines. The melody cache key includes the chords.
- Instrumental lead lines. `compose_instrumental` sets an instrumental section without a written tune one line per two written bars, 4-6 notes each: slow and sparse, for airs and ballads. The engine does not guess that a style wants a dance tune; it once did (a `dance` module keyed on style names) and the module is gone. Dance breaks are a writing decision: the writer gives the section a `tune` (schema 3, `docs/schema-3.md`, "Break tunes"), `compose::written` plays it as written (note values to beats, repeat to fill, cut at the end) and `place` fixes its octave after the transposition is known. Such notes carry `InstNote::written`; the violin plays them at 0.7 on the beat and 0.6 off it, with vibrato only on notes of a beat or more.
- Compose once in the written key, then transpose. An equivalence test over 100 seeds and all keys proves pass 1 plus `key_shift` equals pass 2 before pass 2 is deleted. The octave alignment rounds half up (`(x + 0.5).floor()`), which is the current rule.
- Guitar voicing: exhaustive fretboard search with the current cost terms; `PcSet(u16)` bitmask; memoised by (pcs, bass); returns frets and notes.
- Choir voicing: exhaustive minimal voice-leading SATB search (current costs).
- Counter-line: greedy first-species-style choice (current costs). Fills: descending scalar runs.
- Shared helpers: `PcSet::tones_in(lo, hi)`, `fold_octave`, `Sec::beats`, `Sec::is_repeat_lift`, `Timeline::len_samples`.

## 8a. Writer, arranger, player

Three parts, joined by two files.

- Writer: Claude. Its output is the song JSON (section 7). Every writing decision is in it.
- Arranger: `compose::prepare` then `arrange::arrange`, run by `engine::arrange_song(song, seed, VoiceChoice)`. It turns the song into note events in seconds and makes every random draw of the arrangement. It renders no audio.
- Player: `engine::play(&Performance, &dyn Progress)`: the instruments, the voice and the channel strips, giving `Stems`; `engine::mix` then mixes them. It reads nothing but the performance and makes no writing or arranging decision. `render_with` is `arrange_song` followed by `play`, with the same output as before the split.

The file between arranger and player is the performance (`sunflower arrange`, `sunflower play`; by convention `x.arrangement.json`). One JSON object, `engine::Performance`, version 1 (`engine::PERFORMANCE_VERSION`):

- `version`: 1. `play` in the CLI refuses any other value. A change to a field here, or to an event type of `song::events`, raises the version.
- `seed`: the song seed; it keys every player stream.
- `end`: timeline end in seconds; the stem length is `len_samples(end)`.
- `choir_key`: sample spans `[start, end]` of the choir's word lines, for the ducker; empty without choir lines.
- `band`: the song's band switches. The mixer gates stems with them; the player ignores them.
- `arrangement`: `arrange::Arrangement`: `guitar` (six lists of `StringNote`), `bass`, `harp` (`PluckNote`), `drums` (`DrumHit`, or null without a kit), `violin` (`BowNote`), `harmony_guitar` (`lead`, `arp`) and `vocals` (`lead`, `lead_b`, `harmony`, `doubles`, `choir`; each `Singer` with `voice`, `style`, `notes`, `pan`, `offset`).

Field names are the snake_case names of the structs; enums are spelled as in the song JSON (`"baritone"`, `"phrasing": {"delivery": "flowing", ...}`), phonemes as lower-case ARPAbet, and a drum kind as a string (`"Kick"`) or a one-key object (`{"Swish": {"dur": 0.4}}`). Times are `f64` seconds and levels `f32`. The file round-trips exactly: `serde_json` has `float_roundtrip` on, so `f32` and `f64` survive the text, and a test plays a parsed performance and compares every stem sample with a direct render.

### The arranger pass (optional)

Claude may act as a second-pass arranger. The rule-based arranger arranges first; Claude then reads the song, the writer's arranging note (schema 3 `arranging`, `docs/schema-3.md` section 8; free text, which the rule-based arranger ignores) and a compact view of the arrangement, and returns edits. Nothing is enumerated in advance: the guidance is prose. The invariant holds: the engine makes no model call. The call lives in `songwriter::arranger` and the CLI; its result is saved as the arrangement file (`<stem>.arrangement.json`, version 1, as `sunflower arrange` writes it) and, if the edits change the mix, the mix sidecar (`<stem>.mix.json`), and playing those files is deterministic. The raw reply is kept as `<stem>.arranger.json`.

- `engine::arranger` (pure; no model call, no random draw of its own). `view(song, prepared, performance)` gives the text: the meter and tempo, the key after transposition (tonic pitch class, and its MIDI note above middle C), the band, the sections (kind, bars, energy, key, and whether the harmony, the doubles and the choir sing there: the choir as `pad`, `words (unison|block)` or `off`; a part the band leaves out is not shown), and per bar its chords, the lyric line that begins there and each part's events in beats: `BEAT+LENGTH MIDI VELOCITY` for bass, violin and harmony guitar lead, `BEAT MIDI VELOCITY` for the harp (its notes ring), `BEAT KIND VELOCITY` for drums (`Tom(hz)`, `Swish(beats)`), `BEAT DIRECTION STRINGS VELOCITY` for the rhythm guitar (the bar's pattern as `arrange::guitar::bar_strokes` gives it, velocity after the section level; a bar equal to the one before reads `as bar N`; `damp` after a muted chop), and for the lead `#N "syllable" BEAT+LENGTH MIDI` from the composed lead (`Comp::lead`, length the written duration). `N` numbers the syllables of the lead's own lines from 0 in song order (the choir's written lines have none); a melisma's later notes read `#N~`. Bars are the played bars (`Form::bars`); an event belongs to the bar of its onset taken to the nearest 1/24 beat, so a drum hit that jitter moved 4 ms before a downbeat still belongs to the bar that begins there. Every part is editable except the harmony guitar's arpeggio.
- `edit_schema()` is the reply's JSON schema (closed objects): `{"edits": [EDIT], "mix": {"tracks": {NAME: {"gain_db"}}, "duck_db"}, "summary"}` where each EDIT is one of eight closed kinds (`anyOf`):
  - `{"part": "bass"|"violin"|"harmony_guitar", "from_bar", "to_bar", "notes": [{beat, len, midi, vel[, vibrato]}]}`, the harp the same with `{beat, midi, vel}`, and `"drums"` with `{beat, drum, vel[, hz, len]}` (`drum` a `song::events::DrumKind` name: Kick, Snare, Rim, Tap, Swish, Hat, Shaker, Tom, Ride). The edit replaces the part's events in bars `[from_bar, to_bar)` with its notes, whose beats count from the start of `from_bar`.
  - `{"part": "guitar", "from_bar", "to_bar", "strokes": [{beat, dir: "down"|"up", strings, damp?, vel}]}`: one bar's strum pattern, set for every bar of the range (beats count from the start of each bar, 0 up to the bar's length). `strings`: `all`, `bass` (strings 0-2 of the voicing, the low three), `treble` (strings 3-5), or one picked note: `low` (the lowest sounding string), `alt` (the next above it), `g`, `b`, `e` (string 3, 4, 5 or the nearest sounding below). A boom-chuck is `bass` on the beat and `treble` off it. The voicing is `arrange::guitar::voicing` of the chord sounding at the stroke's beat.
  - `{"part": "lead", "from_bar", "to_bar", "notes": [{syllable, beat, len, midi}]}`: see below.
  - `{"part": "harmony", "sections": [SECTION], "on", "interval"?}`, `{"part": "doubles", "sections", "on"}` and `{"part": "choir", "sections", "mode": "off"|"pad"|"unison"|"block"}`. A section is its number in the view (`s3` is `3`). Intervals: `third_above`, `third_below`, `sixth_above`, `sixth_below`, `fifth_above`, `fifth_below`; without `interval` the rules' own (`harmony_line`).
- `apply(song, prepared, performance, edits)` validates and applies the edits in order, converting beats to seconds with the song's `Timeline` (so rubato and the closing ritard hold), and returns the repairs as text lines, the validated mix sidecar value and the summary. It takes `prepared` by `&mut`: only a lead edit changes it. Note edits replace events directly. Guitar, lead and section edits are collected, and once every edit is read the parts they reach are planned again: the guitar by `arrange::guitar::plan_with` (the edited bars' strokes; every other bar as the rules had it, so the damping across chord changes stays right), the vocals by `arrange::vocals::plan(song, prepared, seed, Some(&overrides))` from the edited lead, with `performance.choir_key` made again (the word sections of the choir key the ducker like the writer's choir lines). With no guitar, lead or section edit the performance is left as it was; `plan(.., None)` and `plan(.., Some(&Overrides::default()))` are the same.
  - Repairs: a bar range outside the song is dropped, or clamped to the song with the beats shifted to match; a note outside its edit's span, with no beat, no midi or an unknown drum is dropped; velocity is clamped to 0..1; a pitch outside the instrument's range (bass 28-60, harp 36-96, violin 55-100, harmony guitar 40-88) moves by octaves into it; a missing length (1 beat), velocity (0.7) or tom pitch (120 Hz) takes a default; an edit to a part that is off in the band is dropped (harp, harmony guitar, bass, violin, drums; `harmonies`, `doubles` and `choir` for the section edits; the rhythm guitar and the lead always play); a mix track that does not exist is dropped and gains and `duck_db` are clamped to the mixer's ranges. A stroke with a beat outside the bar, an unknown direction or unknown `strings` is dropped (a missing `strings` is `all`). A section that does not exist is dropped; an unknown interval is the rules' own; a choir `off` or `pad` on a section with the writer's own choir line is skipped (only `unison` or `block` may change it), and `unison` or `block` on a section without words is skipped. Drum hits take the rule-based arranger's pan for their kind; the violin vibrato defaults to on for notes of a beat or more; a harp note's end is its onset, as the planner has it.
  - Guitar strokes are drawn as the planner draws them: stroke j of bar b from `Rng::event(seed, GUITAR_STROKE, b << 8 | j)` (onset +-6 ms, velocity x 0.92-1.08, per-string strum spread 9 ms down and 7 ms up), through the same `stroke_events` the rule-based pattern uses (a unit test holds `plan` bit-identical to the planner before the refactor, for every pattern and meter). `damp` stops each struck string 60 ms after its own onset (`CHOP_TIME`). An edited last bar strums like any bar (the rules' slow 28 ms spread is for their own closing stroke).
  - Lead edits change the composed lead in beats, before any arrangement: a `syllable` is a number from the view; the syllables of an edit are those whose first note begins in `[from_bar, to_bar)`. The edit must list every one of them once, in order, each over one to `MELISMA_MAX_NOTES` (4) notes in a row, onsets strictly rising, and no other syllable. Otherwise the whole edit is rejected with a repair line (missing, extra or out of order; "words are never dropped, added or reordered") and nothing changes. A syllable with more notes than 4 loses the extra; a pitch is moved by octaves into the singer's range +-3 semitones (repair); the length is cut to end where the next note begins. A syllable given a different number of notes is laid out again like a schema-2 melisma: its whole phoneme string (the onset and the first vowel of the first note, the coda and the last vowel of the last) is the merge of its old notes, then `compose::form::expand_melismas` splits it: onset consonants on the first note, the vowel held (a diphthong glides only on the last note), the coda on the last; the later notes are continuations (`Syllable::is_continuation`, sung legato). The first note keeps the syllable's phrase start, the last its phrase end, stress and grace (first note only). The lines' `syls` and each note's `i` are made again, the notes are timed by `compose::prepare::time_notes`, and `compose_second` derives the other singer's notes again. The harmony, doubles and choir derive from the edited lead because the vocals are planned again from it. Left as composed: `FormLine::rh` and `pitches` (nothing reads them after composition), and the full score's choir staves (`notation::full` plans the choir by the rules).
  - Section overrides (`arrange::vocals::Overrides`): `harmony` per section `{on, interval}` (the rule is: lifted solo lines; an `on` section harmonises every solo line in it), `doubles` per section (the rule is: repeated lifted sections), `choir` per section (`off`: no pad and no words; `pad`: the /aa/ pad even where the rules would not sing it; `unison`/`block`: the section's lead words as choir line notes, `choir::line_notes_with`, the lead still singing; the /aa/ pad stays off there). `arrange::vocals::harmony_interval` picks each note a chord tone at the interval's semitone distances (third 4 then 3, sixth 9 then 8, fifth 7 then 6), else a tone of the chord's local scale, else the first distance. A section with no entry is as the rules have it.
- `songwriter::arranger` builds the prompt and makes the one call through `trait Claude` (the retry of `ClaudeApi` and the usage record `Generation` are the write path's: both go through `call_json`). The prompt explains the view and every edit kind, the principle (the arranger of a live folk band: keep what works, change what the note or the style asks for; energy, groove, and the kick and bass drive a dance song; the lead vocal may be shaped in phrasing and melody, never its words, and the tune must stay the tune), the style's label, `drive` and `break_tune` phrases, and the writer's note, then the view. `sunflower rearrange --note` adds the producer's guidance as a separate section after the writer's note.
- CLI. `write --arrange` runs the pass after the song is written and validated (a failed pass leaves the rule-based arrangement, with a warning); `rearrange` runs it on an existing song. Both save the files above and play the edited performance (`engine::play`). `<stem>.render.json` gains `arranger`: the summary, the call's usage record, the arrangement path, the count of edits applied, the repairs and the producer's note. A render with no arranger pass is the same as before: `render_with` is unchanged. The `arranger_view` example (`//crates/engine:arranger_view`) prints the view of a song.

## 9. Testing without the JS

Unit tests state physics or mathematics, not reference dumps:

- Biquad: magnitude at design points (LP/HP -3.01 dB at f for Q = 0.7071; peaking gain at f; shelf gain at DC/Nyquist) within 0.05 dB; impulse response decays; state flush.
- One-pole: time constant (63.2% step at tau) within 1%. Klatt resonator: unity DC gain; peak at f within 1%; -3 dB bandwidth within 5%.
- FFT: against an O(n^2) DFT for n in {4, 8, 16, ..., 4096}, relative error < 1e-5; round trip < 1e-6 for n = 65536; real FFT against complex FFT.
- Convolution: against direct convolution on random input and a random 3000-tap IR, error < 1e-5 of peak; linearity; thread-count invariance.
- Pluck: f0 by autocorrelation within 2 cents from E2 to E6; fundamental T60 within 10% of the request; no DC; peak bounded.
- Violin: Helmholtz sweep (MIDI 55-90, velocities 0.4/0.6/0.85, two seeds): fundamental > 0.35 x second harmonic, subharmonic and 1.5 f0 < 0.1 x fundamental (Goertzel), >= 208/216.
- LF source: zero net flow; Rd to Rg identity; band-limited table has no energy above SR/2 at the level's top f0 (< -90 dB).
- FDN: energy decay slope matches the design T60 at low frequency within 10%; stable for 60 s.
- Compressor: static curve equals the gain computer; knee continuity.
- Dynamics and pan: equal-power law sums to unit power.
- Compose/arrange properties: stressed syllables land on slots with weight >= 0.5 at a rate above a threshold; tonic cadences end on the tonic; ranges respected; same seed gives identical output; a `same` chorus repeats pitches exactly; the demo normalises with zero repairs.
- Engine: finite output; peak 0.89 within 1e-3; `RAYON_NUM_THREADS=1` equals the default thread count bit for bit; each stem present or absent as the band says.
- Export: WAV header fields; FLAC decodes (via `flacenc` verification or length check); Ogg non-empty; 16-bit dither statistics.

The sound gate (section 12) compares whole renders.

## 10. Performance targets and plan

Today (default build, i7-9750H 6C/12T): 6.3 s threaded, 16.2 s single, 0.89 / 0.72 GB. Profile: mix `compute_track` 23.9%, `synth_voice` 23.5%, FFT 19.0%, pluck 8.8%, `active_rms` 3.5%, pan 2.5%, controls 2.0%, guitar 2.0%, FDN 1.9%, violin 1.3%.

Targets for the demo, measured as `sunflower demo --seed 1234 -o x.wav` (WAV, so the encoder is excluded): threaded <= 2.5 s, single thread <= 9 s, peak RSS <= 0.50 GB threaded, <= 0.40 GB single thread.

Measured at wave 5 close (gate run w5-final, min of 3): 2.16 s threaded, 6.53 s single thread, 647 MB and 491 MB peak RSS. Speed meets the targets; memory does not. The sparse stem cache holds 22,515 present 4096-frame blocks (guitar 4002, harp 3430, violin 3180, hg 2428, drums 2392, choir 1826, bass 1649, lead 1605, doubles 1132, harmony 871), 369 MB, plus 66 MB of output; the threaded excess is transient per-task render and convolution buffers.

Plan, in order of measured or estimated gain:

1. FTZ/DAZ on every render thread. Measured: mix 4.44 s to 2.78 s single thread.
2. Release profile: `lto = "fat"`, `codegen-units = 1`, `debug = 1` kept for profiles. Estimated 5-15%.
3. FFT and convolution (5.10): 3.4 s to about 0.8 s single thread; threaded, each body convolution runs right after its own track and in parallel ranges.
4. Task graph (3.7): the critical path becomes guitar render plus guitar body (about 1.1 s) instead of choir (4 wide) plus a barrier plus the convolutions.
5. Voice: constant resonators designed once; fixed-size arrays; lax/tense tables interleaved per index; frication by span. Estimated 1.3-1.6x on 3.8 s.
6. Pluck: phase-split loops, `f32` delay line, scratch reuse. Estimated 1.5-2x on 1.4 s.
7. Fused channel strip (cascade, block statistics and sparse skip in one pass) and block mixer. Estimated mix 2.78 s to about 1.2 s single thread, and about 100 MB less.
8. Memory: sparse stems, render-into for voices and drums, no full-length buses, no zero drum stem.
9. Export: FLAC frames encoded in parallel (+2.0 s to about +0.3 s over WAV). Vorbis stays serial.

## 11. Deliberate sound changes

In scope for the rewrite:

| Change | Expected effect | Check |
|---|---|---|
| New RNG and per-event streams | each seed is a different take; same distributions | gate LTAS tolerance for re-seeding waves |
| No `f32` rounding inside feedback loops | trajectories move; inaudible alone | gate; Helmholtz |
| FTZ/DAZ | none (below 1e-38) | gate 0.5 dB |
| TDF-II biquads, `f64` state | none (differences near -140 dB) | gate 0.5 dB |
| Band-limited LF tables | removes aliasing on alto and soprano; baritone unchanged below the fold | vowel distance; gate on lead, harmony |
| Per-sample ramps of tract coefficients | removes hop-rate steps; burst timing stays on the frame grid | vowel distance; gate |
| Compressor gain interpolation | removes zipper steps; no level change | gate |
| Violin Lagrange reads, per-sample control ramps, re-tuned loss | vibrato no longer modulates brightness; slightly brighter tone | Helmholtz >= 208/216; gate on violin |
| PolyBLEP ride | smoother above 5 kHz; if dull, raise the ping, do not keep aliasing | gate on drums, 5-16 kHz bands |
| Mode suffix parse, `bass` voice, per-syllable phoneme fallback | correct handling of hand-written JSON | unit tests |
| Compose once, transpose | none (proved by test) | equivalence test |
| TPDF dither at 16 bits | noise at about -96 dBFS in 16-bit WAV/FLAC only | export test |

Later, each gated by the owner's ear and the Python ear tools:

- Low-pass the aspiration noise at 2.6 kHz (what CLAUDE.md says); decide by ear and Whisper word error.
- Vowel modification at high pitch for alto and soprano (open issue 1).
- Klatt nasal pole-zero pair for /m n ng/.
- Slow delay modulation in the FDN.
- Coupling between the two pluck polarisations at the bridge.
- True-peak (4x oversampled) limiter or a -1.5 dBFS ceiling before lossy export.
- K-weighted loudness for track normalisation (needs re-derived strip gains).
- Use the melody profile's instrumental tessitura and contour for break melodies; honour the form plan's break and tag roles.
- Viterbi counter-line; per-string fret tracking for slides and hammer-ons.

## 12. The sound gate

A rough "about right" check that runs here without PANNs or Whisper. The owner's ear stays the judge; the gate catches regressions before the owner listens.

Components:

- `crates/soundgate` (binary `soundgate`): reads WAV (via `hound`), has its own radix-2 `f64` FFT, depends on no engine crate.
  - `soundgate ltas <dir>`: for each `*.wav` in `<dir>`, 1/3-octave long-term average spectrum over IEC 61260 centres 25 Hz-16 kHz (29 bands), power averaged over active 2048-sample blocks (blocks above 5% of the loudest), in dB relative to the file's total active power; plus gated RMS (dBFS), active-block fraction, sample peak, a NaN/inf count. Writes `<dir>/ltas.json`.
  - `soundgate mean <out.json> <dir>...`: summarises one `ltas.json` (and `lead.pitch.json`) per seed: per file and band the mean over seeds of the band level (each seed relative to its own active power, floored at -60 dB) and the seed-to-seed sample std; mean and std of gated RMS and active fraction; the peak range; the NaN/inf count; lead pitch pooled over seeds.
  - `soundgate compare-mean <base.json> <new.json> [--bands]`: the take-robust comparison of two summaries (rules below). Prints a table per file (worst band, delta, base sd, allowance), exits non-zero on failure.
  - `soundgate compare <base.json> <new.json> [--tol-mid DB] [--tol-edge DB]`: per-seed comparison of two `ltas.json`: max |delta| over 100 Hz-10 kHz and over the other bands; active fraction within 15 points; mix gated RMS within 1.5 dB; mix peak 0.89 +- 0.002; zero NaN/inf.
  - `soundgate pitch <wav> <notes.json>`: YIN f0 (de Cheveigne and Kawahara 2002) over the middle 60% of each note of 150 ms or longer; the fraction within 50 cents of the note's MIDI pitch and the count of octave errors. `soundgate pitch-compare` compares two reports.
- `crates/engine/examples/stems.rs --seed S --out DIR`: renders the demo song and writes each processed stem (post EQ, compression and level normalisation; pre pan) as 32-bit float WAV named by track, `mix.wav`, `notes.json` (lead notes: t0, t1, midi) and `render.json` (wall time per stage).
- `crates/voice/examples/vow.rs`: mean spectral distance between ten sustained vowels, 200 Hz-2.5 kHz, baritone. Prints `mean vowel distance(200-2.5k) dB X`.
- `crates/instruments/examples/helmholtz.rs`: the sweep of section 9. Prints `stable N/216`.
- `scripts/gate.sh [--strict] [--against DIR] [--capture-baseline] [--targets] LABEL`: renders the stems for 8 seeds (1234, 2718, 1, 7, 42, 99, 314, 1618: 8 takes of the demo song) into `out/gate/LABEL/sS/`, measures and summarises them, compares the summary with `tests/soundgate/baseline-mean/ltas-mean.json` (or `DIR/ltas-mean.json` with `--against`), runs the vowel and Helmholtz probes, times the demo threaded and on one thread (3 runs each), appends a row to `tests/soundgate/perf.tsv`, prints PASS/FAIL per check. WAVs are kept for seeds 1234 and 2718 only.

Why a multi-seed mean: each seed is a different take (other melody notes, other arrangement detail). A per-seed LTAS delta between two engines mixes the engine change with the take change, and on sparse stems one take differs from another by up to 20 dB in a band. The mean of 8 takes has a third of the take noise, and the seed-to-seed std says how much of a delta the takes alone explain.

Mean-gate rules (default mode; `soundgate::mean`):

| Check | Pass |
|---|---|
| LTAS per stem and mix, 100 Hz-10 kHz | \|mean delta\| <= max(3 dB, min(2 x base sd, 6 dB)) |
| LTAS outside 100 Hz-10 kHz | \|mean delta\| <= max(6 dB, min(2 x base sd, 9 dB)) |
| Mean active-block fraction per file | within 15 points |
| Mean gated RMS per file | within 1.5 dB |
| Mix peak, every seed | 0.89 +- 0.002; no NaN/inf in any file |
| Lead pitch, pooled over seeds | fraction within 50 cents >= base - 0.03; octave errors per seed <= base + 1 |
| Vowel distance | 12.8-14.0 dB (13.2-13.6 is the good reference; outside it, report) |
| Helmholtz | >= 208/216 |
| Thread invariance | `sha256` of the demo WAV equal threaded and at `RAYON_NUM_THREADS=1` |
| Render time, peak RSS | reported against the previous pass row; fail only above +50% of the first pass row (the perf baseline). Section 10's figures are aims, not requirements. |

The allowance rule. The difference of two 8-take means of one engine has std s/2 (s the seed-to-seed std), so 2s is about 4 standard errors of the null difference, and a shift smaller than two takes already differ by is not a change of character. The fixed 3/6 dB keeps stable bands from failing on sub-audible drift. The cap (6 dB mid, 9 dB edge) covers bands whose takes are bimodal (a take has notes in the band or not), where s is large and 2s would admit any shift: in the wave-3 baseline-mean, harmony 160 Hz has s = 9.6 dB (2s = 19.3), doubles 125 Hz 7.2, guitar 100 Hz 6.5; 19 of 210 stem bands in 100 Hz-10 kHz had 2s > 6 dB, none outside it had 2s > 9 dB. A robust spread (median absolute deviation) was rejected: on bimodal bands it is either larger than s (harmony 160 Hz, 14.1 dB) or the majority's spread (guitar 100 Hz, 0.6 dB). Mean gated RMS 1.5 dB holds: the largest seed-to-seed RMS std in the baseline is 0.48 dB (harmony_guitar; mix 0.32), a null std of at most 0.24 dB for the difference of two means. Because the seeds are fixed, an engine that renders the same takes compares at delta 0 plus its own change; a composition change that alters the takes can move a bimodal band by more than the cap and then needs a listen and a re-baseline.

`--strict`: for refactors that must not change the samples. In addition to the mean gate, seeds 1234 and 2718 are compared one by one against `tests/soundgate/baseline/sS/` (or `--against DIR`'s `sS`) with `soundgate compare` at 0.5 dB in 100 Hz-10 kHz and 1 dB elsewhere, and per-seed pitch (fraction within 50 cents >= base - 0.03, octave errors <= base + 2).

Baselines: `tests/soundgate/baseline-mean/` (`ltas-mean.json`, `vow.txt`, `helmholtz.txt`) for the mean gate and `tests/soundgate/baseline/sS/` (`ltas.json`, `lead.pitch.json`, `notes.json`, `render.json` of seeds 1234 and 2718) for `--strict`; JSON and text only, committed; audio stays in `out/` (ignored by git). `scripts/gate.sh --capture-baseline LABEL` writes both from one run. The first baseline was captured from commit 29c5c08 (release profile with LTO); the multi-seed baselines were captured at wave 3.

Re-baselining. The rule was: the baseline is replaced only after the owner listens, in its own commit. For this rewrite the owner waived it: renders are sent to him for listening at each sound-changing wave, and he asked for a rough about-right check, not a gate on each baseline change. A wave that changes the sound on purpose re-captures the baselines at wave close, from the tree with the whole wave merged, in its own commit, and the renders go to the owner.

## 13. Rewrite plan

Waves run in order; units within a wave run in parallel with disjoint files. The workspace builds and the gate passes at the end of every wave. A unit that changes a public API keeps the old signature as a thin shim over the new code unless it owns every caller; the last wave deletes the shims. Each unit removes `sfcore::js` and `sfcore::rng` use from the files it owns.

- Wave 0: the sound gate and its baseline.
- Wave 1: foundations and primitives (sfcore; dsp filters, dynamics, pan; dsp delay and stochastic processes; FFT and convolution; `song` crate).
- Wave 2: typed model migration across all consumers; instruments (pluck, body, guitar sympathetic; violin; drums); FDN reverb; export.
- Wave 3: voice source and tract; compose internals; arrange as events with band rendering on instruments; songwriter internals.
- Wave 4: engine (tracks, stems, task graph, strips, mixer) and CLI; voice articulation.
- Wave 5: delete the JS linkage and shims; update CLAUDE.md.

The unit briefs are kept with the plan in the orchestration output.

Units (ownership is disjoint within a wave):

| Wave | Unit | Owns |
|---|---|---|
| 0 | gate: soundgate crate, probes, gate script, baseline, release profile | `crates/soundgate/`, `crates/engine/examples/stems.rs`, `crates/dsp/examples/helmholtz.rs`, `scripts/gate.sh`, `tests/soundgate/`, root `Cargo.toml`, `.gitignore` |
| 0 | scaffold: empty modules and crates | `crates/core/src/lib.rs` + new empty modules, `crates/dsp/src/lib.rs` + new empty modules, `crates/song/`, `crates/instruments/` |
| 1 | sfcore: math, fp (FTZ), random, time; FTZ wiring | `crates/core/src/{math,fp,random,time}.rs`, `crates/engine/src/render.rs`, `crates/sunflower/src/main.rs` |
| 1 | dsp filters, smoother, dynamics, pan (old API as shims) | `crates/dsp/src/{biquad,onepole,resonator,smoother,filter,dynamics,pan}.rs`, `crates/dsp/tests/filters.rs` |
| 1 | dsp delay lines, allpasses, stochastic processes, noise | `crates/dsp/src/{delay,stochastic,noise}.rs`, `crates/dsp/tests/delay.rs` |
| 1 | FFT and convolution (old API as shims) | `crates/dsp/src/{fft,conv}.rs`, `crates/dsp/tests/fft.rs`, `crates/dsp/examples/fftbench.rs` |
| 1 | song crate | `crates/song/` |
| 2 | typed model migration of all consumers | `crates/{compose,arrange,voice,songwriter,sunflower}/`, `crates/engine/` |
| 2 | plucked string, guitar sympathetic strings, bodies | `crates/instruments/src/{pluck,body,guitar}.rs`, their tests and bench |
| 2 | bowed string | `crates/instruments/src/violin.rs`, `crates/instruments/examples/helmholtz.rs`, its tests |
| 2 | drum voices and FDN reverb | `crates/instruments/src/drums.rs`, `crates/dsp/src/reverb.rs`, their tests |
| 2 | export | `crates/export/` |
| 3 | voice source, tract, synth, phrase API | `crates/voice/` |
| 3 | compose internals, new RNG | `crates/compose/` |
| 3 | arrange as events, band rendering on instruments | `crates/arrange/`, `crates/engine/src/{band,vocals,render,lib}.rs` |
| 3 | songwriter internals | `crates/songwriter/` |
| 4 | engine: tracks, stems, task graph, strips, mixer; CLI | `crates/engine/`, `crates/dsp/src/mix.rs`, `crates/sunflower/` |
| 4 | voice articulation | `crates/voice/` |
| 5 | delete the JS linkage and shims; CLAUDE.md | everything that remains, `CLAUDE.md` |
