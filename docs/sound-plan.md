# Sound plan: method, references, verdicts, open specifications

Written in November 2026 at the end of the work that moved the engine from "judged against itself" to "judged against real recordings". It is for whoever continues: the method that worked, what the owner's ear decided, and the next changes specified with numbers. `docs/engine-design.md` has each model as built; this file has what to do next and how.

## 1. Method

1. Measure the engine's stem and a real recording of the same kind of source with the same analysis. Never compare against a figure from memory or against the engine's own baseline alone.
2. Find the cause in the model, not in the mix. A correction that only an equaliser could supply is a sign the model's data is wrong (the guitar row, the drum recipes).
3. Fit by a stated rule with acceptance numbers and a stop rule, then report the result whether or not it passes. Do not tune constants to pass.
4. Check on material the fit did not see (another song, another key).
5. Send a render. The owner's ear decides; it has overruled the numbers twice (section 4).

Traps met, each of which produced a wrong conclusion once:

- A pooled statistic across corpora that differ in pitch and vowel set (pooled H1-H2: 4.9 dB real against 0.2 dB engine, while like-for-like open vowels gave 2.4 against 2.0). Condition on what differs, or use matched material.
- A reference that is the wrong kind of instrument (soft nylon classical guitar notes for a strummed steel-string; time-averaged anechoic single notes for a part played in rhythm). Check what the reference is before trusting it.
- A statistic that removes the thing that matters (within-note drift after detrending hid whole notes going 29 cents off pitch). Add a guard on the plain quantity (median pitch error).
- A parameter that changes nothing is saturation (the drift walk sat on its clamp). Work the statistics from the code.
- A level rule that matches RMS while the crest factor triples (modal kick: peak 7 to 8 times the 0.3 s RMS, as a real kick; the sine kick was 2.8). The mix needed a limiter, not the drum a change.
- Agents' "after" renders can be stale; re-render with the final build before sending.

## 2. Reference sets (statistics only are committed; all CC BY 4.0 or free)

| Source | Reference | Where the statistics are | Tool |
|---|---|---|---|
| Steel-string guitar | GuitarSet, 180 mono-microphone accompaniment tracks (Zenodo 3371780) | `tools/targets/guitar_guitarset.json` | `tools/guitar_curve.py`, `tools/body_curve.py` |
| Male voice | vocadito, 15 tracks with average pitch <= MIDI 55 (Zenodo 5578807) | `tools/targets/voice_vocadito_male.json` | `tools/voice_target.py` |
| Female voice | vocadito, 22 tracks with average pitch >= MIDI 58 | `tools/targets/voice_vocadito_female.json` | `tools/voice_target.py` |
| Drums | DRSKit 2.1, DrumGizmo (drumgizmo.org), overheads and close microphones | `tools/targets/drums_drskit.json` | `tools/drum_target.py` |
| Violin | Expressive Solo Violin, 17 takes, X/Y pair at 1 m (Zenodo 5765676) | `tools/targets/violin_expressive.json` | `tools/body_curve.py --body violin` |
| String bass | University of Iowa MIS, double bass pizzicato mf and ff | `tools/targets/bass_iowa.json` | `tools/bass_target.py` |
| Sustained vowels, vibrato | VocalSet, 20 trained singers, long tones on five vowels (Zenodo 1193957) | section 3 below (not yet a target file) | none yet |

The recordings are not in the repository. A cloud session can fetch them again (Zenodo and drumgizmo.org are reachable; the Iowa files are AIFF, convert with ffmpeg). No harp reference of the right kind (nylon or gut lever harp) is known.

## 3. Measured facts worth keeping

Voice (male, sustained notes; 1/3-octave harmonic envelope relative to the 315-1000 Hz harmonic power):

    band Hz   630 800 1000 1250 1600 2000 2500 3150 4000 5000 6300 8000
    singers    -6 -13  -15  -19  -16  -16  -24  -28  -31  -36  -34  -37

- Open-vowel H1-H2 (strongest of the first 8 harmonics is the 3rd or higher, f0 <= 200 Hz): 2.4 dB, quartiles -5.3 and 4.5.
- Slope of harmonic level against log2 of harmonic number below 1.5 kHz: -9.9 dB/octave at f0 100-150 Hz, -8.4 at 150-200.
- Above 4 kHz a sung vowel is noise more than harmonics (peak minus inter-harmonic floor about 8 dB).
- Consonants stand 9 to 13 dB above the vowel envelope at 5 to 8 kHz.
- In half of the sustained frames the fundamental is the strongest harmonic. The engine's baritone: 7%. Not explained; it may be the vowel set (the tracks are Spanish, Tagalog, Catalan). VocalSet's per-vowel long tones can settle it (5.1).

Vibrato and drift, trained singers (VocalSet long tones, 1.5 s windows; extent is the peak deviation):

    kind             rate Hz            extent cents   slow drift (<2 Hz) cents
    male forte       4.9 [4.8 5.0]      65 [51 73]     5.7
    male pp          4.8 [4.6 5.0]      32 [9 50]      6.7
    male straight    4.6                5 [3 27]       4.7
    female forte     5.5 [5.2 6.1]      69 [61 78]     11.1
    female pp        5.1 [4.9 6.1]      42 [34 46]     5.5
    female straight  4.5                6 [3 8]        3.1

  Per-singer median rate spans 3.8 to 6.2 Hz. Untrained folk singers (vocadito, 5 long segments only): extent about 11 cents, 40% nearly straight. The engine's baritone after the pitch fix: rate 5.1 Hz, extent about 22 cents, slow drift 4.4 cents, median pitch error 6.4 cents. These sit between "straight" and "trained pp", which suits a folk voice. A style could select a vibrato kind from this table.

Mix: 0.03 to 0.05% of a mix's samples lie more than 12 dB over its active RMS; the crest limiter at 12 dB brings every mix to about -13.1 dBFS active RMS at peak 0.89.

Reverb (by design; see 5.12 of the design for the measured numbers once the 16-line network is in): 8 lines summing to 0.39 s give a mode every 2.6 Hz against a mode width of 1 Hz at T60 2.2 s.

Fast notes: on a 204 ms note the old consonant rules left the vowel a median of 148 ms and under 80 ms on 15% of notes. The fiddle slid into half of all leaps of 5 semitones or more whatever the note length, and never changed bow inside a run.

## 4. The owner's verdicts by ear

- Modal brush tap and swish: bursts of static. The earlier brush recipes are back, sample for sample. Reason in the numbers: a soft brush tap is bristle noise at 1 to 4 kHz, quiet in a real kit, but the drums stem is brought to a target loudness, so a brush-only passage was raised to full level.
- Modal kick, snare, hat, ride (full kit): better than the oscillator recipes. Kept.
- Fiddle on fast notes: iffy. Fixed by the bowing rules (no slides around notes under 0.35 s; a bow per fast note). Not yet heard after the fix.
- Voice at 144 beats per minute with a syllable on every eighth: off; the tempo is too fast for the voice. Consonants now shorten on notes under 0.32 s, and the writer's prompt limits the syllable rate. Not yet heard after the fix.
- Not yet judged by ear at all: the guitar row, the baritone source, the pitch drift, the stereo coherence, the limiter, the reverb, the violin row, the bass, the tenor, alto and soprano fits.

## 5. Open specifications, in order of expected audible gain

### 5.1 Baritone vowels against matched vowels (800 Hz excess, the fundamental)
The baritone is 6.5 dB high at 800 Hz and its fundamental is rarely the strongest harmonic. Both may be the vowel set of the reference. Procedure:
1. Make `tools/targets/vowels_vocalset_male.json`: for each of the five vowels, from VocalSet male long tones (forte and pp), the harmonic envelope in 1/3-octave bands relative to the 315-1000 Hz harmonic power, H1-H2, and the share of frames in which the fundamental is the strongest harmonic. Verify the pitch tracker first: the long tones measured near 345 Hz median, which may be an octave error.
2. Render the engine's sustained /aa/, /eh/ or /ey/, /iy/, /ow/, /uw/ at the same pitches (the `vow` example holds the vowels; add WAV output) and compare vowel by vowel.
3. If /aa/ and /ow/ are high at 630-1000 Hz by more than 3 dB: widen B1 (today 60 + 80 breath Hz) by a factor fitted on those two vowels in {1.0, 1.3, 1.6, 2.0}; guard: vowel distance (the `vow` probe) at least 12.8 dB.
4. If the fundamental's share is low on every vowel: raise harmonic 1 of the capped tables by a fitted gain in {0, 2, 4, 6} dB (multiply bin 1 of the table's spectrum when the mip levels are built; it is exact and costs nothing at run time). Guard: open-vowel H1-H2 stays within 1.5 dB of the matched-vowel target.

Measured on 18 November 2026 (baritone, demo and Center Pivot pooled, against vocadito male; `VoiceParams::b1_scale` multiplies B1 = 60 + 80 breath Hz, 68 Hz for the baritone):

    setting                                  H1 below strongest     H1 strongest   J      vowel distance
                                             f0 100-150 / 150-200   share
    singers                                  0.0 / 0.0              0.52
    current (b1 1)                           -3.9 / -6.5            0.07           3.28   12.98
    b1 2, fa 700, tilt 3800, bed .08, sh 10  0.0 / -2.7             0.26           4.3    12.32
    b1 3, same                               0.0 / -0.2             0.47           3.9    11.98

  With b1 1 one harmonic near F1 stands 8.8 dB over the next at f0 200-260 Hz (singers 3.4): a narrow F1 rings on a harmonic. B1 does not move the +6 dB at 800 Hz (likely the vowel set). The wider B1 lifts 1 to 3 kHz against the 315-1000 Hz norm, so the source is darkened with it (fa_hz 700, tilt 3800); the noise bed falls to 0.08 and `cons_gain` to 0.9-1.0 (consonant prominence 11.7 dB, singers 11.9; 2.74 compensated a bed that was too strong). Open H1-H2 stays 2.3-2.4 dB. Cost: the vow probe falls 0.7 to 1.0 dB, below the gate's 12.8 floor; that probe rewards narrow formants. Renders A (current), B (b1 2), C (b1 3) went to the owner; the choice is by ear.

### 5.2 Tract resonance near 4.5 kHz
The baritone is 5.4 dB low at 4 kHz. The fixed sections put F5 at (3950 - 500 sf) Hz and the next resonance at 5.5 kHz; a uniform 17.5 cm tract has resonances at 3.5, 4.5, 5.5 kHz. Add a per-voice base for F5 (legacy 3950) and try {3950, 4200, 4500} with F4 fixed; measure the 3150, 4000 and 5000 Hz bands of the vowel envelope; accept the value that brings 4 kHz within 3 dB without moving 2.5 and 3.15 kHz by more than 2 dB. If none does, the deficit is the harmonic level above F4 and the remedy is the shelf corner (5.2 kHz; try 4.2 kHz), not the tract.

### 5.3 Fast notes, remaining
- Sixteenths (104 ms): 7% of the reel's notes still have under 80 ms of vowel at full rate weight. Either a second step of the rate weight for T under 0.14 s (consonant scale 0.5, onset share 0.22, coda share 0.15) or, better, the writer avoids words with clusters on sixteenths (prompt).
- The stop burst keeps its 12 ms while the stop shrinks, which squeezed one aspiration to 0.2 ms. Scale only the part after the burst, and give aspiration a floor of 8 ms or drop it.
- Fiddle: with a bow per eighth, 3.8% of sounding frames have periodicity under 0.7 (3.1% before). If the owner hears scratch on the bow changes, shorten `BOW_DIRECTION_TAU` (12 ms) or lower `REBOW_ACCENT` on fast notes; measure with `tools/fiddle_check.py`.

### 5.4 Drums, remaining
- Snare 6 to 7 dB high in the 125 and 315 Hz bands; tom low band decays too fast (ratio 0.2 to 0.3 at 63 Hz); 12 of 166 octave decay cells are outside 0.6 to 1.6, all in low bands with few modes. Low bands need explicit strong modes with their own decay, taken from the close microphone (the tom's fundamental failed the 8 dB peak test and was left out: lower that threshold to 5 dB for the fundamental).
- If the owner wants brushes from a real kit after all: the fault was level, not spectrum. Model the brush voices from DRSKit again but set each layer's level from the reference (soft layer 22 dB under hard) and exclude brush-only passages from the stem's loudness normalisation by normalising the drums stem on its loudest 20% of blocks.

### 5.5 Loudness
The crest limiter gives -13.1 dBFS active RMS. If a loudness standard is wanted, replace the active-RMS measure by K-weighted integrated loudness (ITU-R BS.1770) and set the ceiling from a target of -14 LUFS; keep the look-ahead limiter. The drums strip's soft clip (12 times the target level) may then be removed: measure the mix with and without it.

### 5.6 Harp
No reference. The harp row derives from the old (nylon) guitar row, so by the guitar's finding it is likely dark above 1 kHz. The owner judges it fine. Leave it until a lever-harp set is found; then use `tools/body_curve.py` as for the violin.

### 5.7 Timing feel
Onsets carry uniform jitter of +-4 ms, independent per hit. Real ensemble timing is correlated: a slow tempo drift (about 1% over several bars) shared by the band, plus per-instrument offsets (bass a few ms behind the kick, strummed strings spread), plus small independent jitter (sd 5 to 10 ms for a folk band). Specify from a reference with onsets (GuitarSet has annotated onsets against a click) before changing anything.

### 5.8 Higher voices
After the tenor, alto and soprano fits (design 5.1-5.2): intelligibility at high pitch. When f0 exceeds F1, singers raise F1 to f0 (the floor at 1.06 f0 exists) and widen the jaw, which also raises F2 for back vowels. Measure with VocalSet female long tones per vowel as in 5.1.

### 5.9 The gate
`scripts/gate.sh` compares the engine with its own earlier output. Its baseline predates every change of November 2026 and must be captured again on the owner's machine (Bazel). Then add the reference comparisons as gate checks, so a regression against the real recordings fails the gate: `tools/body_curve.py check` (guitar, violin), `tools/voice_target.py eng --target` (J and open H1-H2 per voice), `tools/drum_target.py eng`, `tools/bass_target.py eng`, `tools/reverb_check.py`, `tools/fiddle_check.py`, `tools/artic_check.py`. All need numpy only.

### 5.10 Composition
The rhythm model shortened stressed syllables in a slow waltz ("mile" 0.31 s in `songs/center-pivot.json`, where the tune gives a stress per bar). A stressed syllable on a downbeat should not be shorter than the unstressed one that follows. This is in `compose` (the rhythm DP), not in the sound models.
