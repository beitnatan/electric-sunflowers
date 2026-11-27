# Voice word list: warmth and timbre

Measurement only; the voice is not tuned. Commit of the measured engine: cd1092db (demo-faults-fix) plus the tools of this branch.

## Method

- `tools/ttswords.json`: 87 items, hand-written ARPAbet per syllable (CMU symbols, no stress digits) and the index of the stressed syllable. The song crate holds no pronunciation dictionary (only a letter-rule fallback), so none was reused. Groups: 12 hVd vowels, fricatives, affricates, nasals, liquids, glides, stops (onset and coda), clusters, 9 demo trouble words, 20 Rainbow Passage and 11 Harvard sentence words (108 syllables).
- `tools/make_wordlist_song.py`: one schema-3 song per voice, one word per line, 4/4 at 60 bpm, `tune` hints fix every note at one pitch (baritone MIDI 55, tenor 60, alto 65, soprano 70), band all off. The guitar pattern has no "none" value, so a guitar stem exists; only `lead.wav` is measured. The rhythm model sets the length of each note (0.45 to 2.5 s).
- Render: `stems --seed 1234 --song out/wordlist/V.json --voice V --out out/wordlist/V`; then `python3 tools/warmth.py out/wordlist/{baritone,tenor,alto,soprano}`. Needs numpy, scipy, soundfile.
- Vowel frames: per sung syllable, the longest part of the note outside the planned consonant segments (`plan.json`, 50 ms margin), at least 0.25 s, middle 60%. Glides and liquids (l r w y) are not in the plan, so they count as vowel. `tools/voice_target.py analyse` gained a `min_dur` argument (default 0.5, unchanged) for this.
- Reference: `tools/targets/voice_vocadito_male.json` (baritone, tenor) and `_female.json` (alto, soprano); statistics only. The reference has no HNR or noise flatness; those are printed for the engine only.

## Table

```
voice       tilt   cent  H1-H2   5-8k%      J  pkflr    HNR  flat  formD   warm timbre
ref-male    15.8    532    2.4   0.066      -   18.9      -     -      -              
baritone    17.3    503    1.5   0.037    3.6   23.3   18.1  -8.1  0.138     83     60
tenor       16.0    491   -2.3   0.041    4.2   26.9   17.6  -6.3  0.121     74     50
ref-female   16.9    612   -2.5   0.030      -   28.4      -     -      -              
alto        19.2    396   -6.5   0.001   15.4   25.5   18.8 -22.8  0.163     32     40
```

Reference rows come from the target JSON: tilt, centroid and 5-8 kHz share from its harmonic band levels (`vowel_h`), H1-H2 from `open_h12`, pkflr from `peak_flr`.

## Measures

- tilt: 10 log10 of harmonic power in the 200-800 Hz bands over that in the 2-5 kHz bands (1/3-octave bands of voice_target; high tilt = warm).
- cent: power-weighted centroid of the band centres 200 Hz-10 kHz (Hz).
- H1-H2: median over open-vowel frames (strongest of the first 8 harmonics is K or higher; f0 limit of the target, raised to 300 Hz for the tenor and 500 Hz for the soprano, whose pitch is above it).
- 5-8k%: harmonic power in the 5, 6.3, 8 kHz bands over the total, per cent.
- J: RMS over 630 Hz-8 kHz of engine minus reference band level (voice_target `compare`).
- pkflr: harmonic peak minus inter-harmonic median, mean dB over 200 Hz-5 kHz (voice_target `peak_flr`).
- HNR: median over frames of 10 log10(r/(1-r)), r the normalised autocorrelation peak within +-3 semitones of the note.
- flat: median spectral flatness (dB, 0 = white) of inter-harmonic bins below 8 kHz.
- formD: mean over the hVd words of hypot((F1-T1)/T1, (F2-T2)/T2); F1, F2 by 12-14 pole LPC on 30 ms frames, median over the word's middle 60%; T from Hillenbrand et al. 1995 means (men for baritone and tenor, women for alto and soprano; values typed from memory, rounded). LPC is unreliable above 400 Hz f0 (alto, soprano): read formD there as an upper bound.

## Scores

Each component scores `100 * clip(1 - |d| / s, 0, 1)` for distance d from the reference. Scales s: tilt 10 dB, centroid 0.5 octave (d = log2 ratio), H1-H2 8 dB, share 10 dB (d = dB ratio of shares), J 10 dB, pkflr 12 dB, formD 0.30 (d = formD). warmth = mean of tilt, centroid, H1-H2, share. timbre = mean of J, pkflr, formD. Scales are judgment.

Because each voice sings one fixed pitch, the harmonic comb leaves 1/3-octave bands empty; their level is interpolated in dB over log frequency before tilt, centroid, share and J are computed (`fill`). At the soprano's 466 Hz everything below 400 Hz is held flat. This makes the soprano low-frequency numbers weak.

## Results

soprano     14.1    641   -3.9   0.006   17.4   33.2   23.7 -20.6  0.177     69     34
tilt: dB, 200-800 Hz vs 2-5 kHz harmonic power; cent: Hz; H1-H2: open vowels, dB; 5-8k%: share of harmonic power; J: dB; pkflr: dB; HNR, flat: dB (no reference); formD: relative F1/F2 distance
-- baritone: components tilt 85 centroid 84 h12 89 share 76 J 64 peak_flr 63 formant 54
   worst words (warmth, timbre, mean): arch 21/33/27; light 23/32/27; pot 24/32/28; hood 23/33/28; hod 25/33/29; who'd 32/28/30; strong 35/28/31; gold 33/33/33
-- tenor: components tilt 98 centroid 77 h12 42 share 79 J 58 peak_flr 34 formant 60
   worst words (warmth, timbre, mean): arch 17/13/15; light 17/13/15; pot 20/13/16; hod 20/20/20; who'd 30/22/26; juice 30/25/28; smooth 30/26/28; glue 29/27/28
-- alto: components tilt 78 centroid 0 h12 50 share 0 J 0 peak_flr 75 formant 46
   worst words (warmth, timbre, mean): colors 0/24/12; water 4/22/13; birch 0/28/14; harbor 0/29/14; hood 0/29/14; beautiful 0/30/15; harvard 0/31/16; who'd 0/32/16
-- soprano: components tilt 71 centroid 87 h12 83 share 33 J 0 peak_flr 60 formant 41
   worst words (warmth, timbre, mean): juice 0/26/13; glue 0/27/14; smooth 0/28/14; hayed 0/29/14; hoed 0/32/16; sheet 0/35/17; sing 0/35/17; spring 0/36/18

Last line:
`SCORES baritone warmth 83 timbre 60 | tenor warmth 74 timbre 50 | alto warmth 32 timbre 40 | soprano warmth 69 timbre 34 |
`

## Reading

- Baritone is the closest to its reference (warmth 83); its largest gaps are J (3.6 dB) and the pkflr (23.3 vs 18.9: cleaner than the singers, HNR 18 dB).
- Tenor: H1-H2 is 4.7 dB under the male reference at higher pitch, pkflr 8 dB cleaner.
- Alto: centroid 396 Hz against 612 and 5-8 kHz share 0.001% against 0.030%: the voice is dark at the top; J 15.4 dB. Consistent with CLAUDE.md (alto and soprano on the old source, unmeasured cause).
- Soprano: J 17.4 dB, pkflr 33 vs 28, share 0.006 vs 0.030; formants are least reliable here.
- Noise floor flatness differs by 15 dB between low voices (-6 to -8) and high voices (-21 to -23): the female voices have a far more tonal (less white) floor.

## Lowest-scoring words

The word score is the mean of its warmth and timbre parts against the same overall reference; with a few harmonics per word it is noisy, and vowel quality alone moves it (a back vowel is darker than the pooled reference). Read it as a ranking of dark or noisy words, not an absolute grade. The lists above give warmth/timbre/mean per word. Common to the low voices: arch, light, pot, hod, hood, who'd (back and low vowels, `l`-final and stop-final words). Common to the female voices: back and rounded vowels (who'd, hood, canoe, juice, glue, smooth) and r-coloured words (birch, harbor, harvard, water, colors).

## Decisions where the spec was silent

- Hand-written ARPAbet (no dictionary in the repo); stress on the first syllable except canoe.
- Pitch fixed by `tune` hints; the engine did not transpose any voice (notes.json shows 55, 60, 65, 70).
- Word score and per-voice rows are against one pooled reference, not a per-vowel reference.
- hVd vowel targets typed from memory (see above).

## Tuning against SingMOS-Pro and Audiobox (alto, soprano)

The first tuning (hf_bed, jitter, shimmer, rd, fa_hz, shelf_db) raised the word-list scores and lowered SingMOS-Pro on the demo lead (alto 3.18 to 2.75, soprano 3.09 to 2.34). The word-list score is our own proxy and rewards noise. It is now a screen only: a setting stays when SingMOS-Pro and PQ hold. Baseline for every row: origin/demo-faults-fix, demo lead, seed 1234. Listeners: `tools/listen_all.py` (SingMOS-Pro, Audiobox PQ, wav2vec2 phoneme hits); `tools/iy_profile.py` (sung /iy/ harmonics against VocalSet); vow = `crates/voice/examples/vow.rs`; per-bin open H1-H2 = `voice_target.py eng` against the vocadito female target. SingMOS differences under 0.05 are inside run-to-run judgment; each row is one render.

### Soprano /iy/ (vowel modification, synth.rs)

Voice-tune did not cause the soprano /iy/ loss: the baseline already had 8/17. The cause is the front-vowel modification of cd1092db (VMOD_MAX 0.85, F2 reference 1600 Hz times fs 1.22 = 1950 Hz), which moves F2 down by 800 Hz at 500 Hz f0. Soprano demo lead:

```
run                    iy hits  SingMOS   PQ   wl SingMOS  i 420-550 H6 H7 H8   i 550-750 H5 H6   vow
VocalSet                  -        -       -        -        -30 -30 -35          -32 -37          -
off (VMOD 0)            13/17    3.35    7.29     2.84      -17 -25 -12          -16  -9         10.74
VMOD 0.4                13/17    3.29    7.20     2.83      -31 -24 -26          -20 -20         10.77
VMOD 0.6                13/17    3.27    7.21     2.81      -34 -22 -31          -21 -25         10.78
0.85, F2 1600 (before)   8/17    3.09    7.22     2.79      -35 -28 -36          -24 -30         10.69
0.85, F2 1700, F3 2800  11/17    3.22    7.21     2.82      -37 -30 -28          -26 -23         10.75
0.85, F2 1900           13/17    3.29    7.26     2.81      -29 -23 -33          -19 -26         10.71
1.0,  F2 1700            8/17    3.13    7.20     2.76      -33 -32 -38          -26 -32         10.70
1.0,  F2 2000 (chosen)  16/17    3.33    7.27     2.82      -26 -26 -34          -19 -27         10.73
```

Chosen: VMOD_MAX 1.0 and F2 reference 2000 Hz (unscaled; F3 reference 2500 unchanged). SingMOS +0.24, PQ +0.05, /iy/ hits 8/17 to 16/17, H6-H8 of /i/ at 420-550 Hz within 6 dB of VocalSet (+4, +4, +1), the squeak of VMOD 0 (H6 -17, H8 -12) stays gone. Open: at 550-750 Hz the profile is 13 and 10 dB above VocalSet at H5 and H6 (H4 -12 against -23), where the old setting was 8 and 7 dB above (my frame method; reportB reports it as met). That is the part to listen to on high /i/ notes; fall back to 0.85/1700/2800 or VMOD 0.4 if it squeaks. Alto notes above 300 Hz pass through the same code (alto demo SingMOS 3.32 to 3.27, PQ +0.02, /iy/ 11/17 to 17/17). Baritone is bit-identical; tenor moves by 0.02 SingMOS.

### Alto and soprano source sweep (one parameter at a time, from the baseline)

Screen: word-list warmth/timbre and vow (13.08 alto, 10.69 soprano, limit 0.3 dB). Gate: demo SingMOS (no fall over 0.05), PQ (no fall over 0.1), per-bin open H1-H2 (within 0.5 dB of the baseline), word-list SingMOS.

```
voice    setting                warm tim   vow    SingMOS   PQ   wl SingMOS  verdict
alto     baseline                32   40  13.08    3.32    6.78    2.61
alto     shelf_db 18/20/24     33/33/36 40  13.08     -       -      -       warmth +4 at most
alto     hf_bed 0.05            32   54  13.10     -       -       -        screened out by the 0.1 row
alto     hf_bed 0.1             42   56  13.09    2.73    6.94    2.51      SingMOS -0.59
alto     hf_bed 0.2             56   54  13.03    2.47    6.98    2.50      SingMOS -0.85
alto     rd 1.4 / 2.0           32/35 44/32 12.51/13.62 -    -       -      no gain
alto     fa_hz 4000             52   46  12.34     -       -       -        vow -0.74
alto     fa_hz 2500             46   47  12.36    3.43    6.94    2.68      vow -0.72
alto     breath 0.12 / 0.22     30/35 46/35 13.20/12.92 -    -       -      no gain
alto     jitter 0.005, shimmer 0.06  32 40/39 13.05/13.06 - -      -        no effect
alto     fa_hz 4000, rd 2.0     50   43  12.74     -       -       -        vow -0.34
alto     fa_hz 4000, rd 2.1     50   42  12.83    3.46    6.95    2.68      H1-H2 -0.7 to -1.0
alto     fa_hz 6000, rd 2.3     53   39  12.98    3.49    6.98    2.68      all held
alto     fa_hz 4000, rd 2.4     49   39  13.07    3.54    6.97    2.66      all held (chosen)
soprano  baseline                69   34  10.69    3.09    7.22    2.79
soprano  shelf_db 20 / 24      72/74 34/35 10.69/10.68 -/3.09 -/7.23 -/2.78 warmth +5
soprano  hf_bed 0.1             75   41  10.70    2.73    7.17    2.78      SingMOS -0.36
soprano  hf_bed 0.2             84   44  10.69    2.53    7.11    2.72      SingMOS -0.56
soprano  rd 1.3 / 2.0           61/67 30/33 10.66/11.37 -  -       -      no gain
soprano  fa_hz 4000             45   21  10.60     -       -       -        worse
soprano  breath 0.12            69   39  10.60    3.08    7.19    2.76      held, timbre +5 only
soprano  breath 0.16            69   45  10.49    3.02    7.12    2.67      SingMOS -0.07, wl -0.12
soprano  breath 0.22            70   42  10.37     -       -       -        vow -0.32
soprano  jitter 0.004 / shimmer 0.045  68/69 34/36 10.76/10.73 - -   -      no gain
soprano  breath 0.14, shelf 24  75   43  10.53    3.06    7.15    2.70      wl SingMOS -0.09
```

Findings. Noise is what the word-list proxy rewards and SingMOS punishes: hf_bed 0.1 costs 0.4 to 0.6 SingMOS in both voices. The alto's gain comes from the capped source (fa_hz 4000, a one-pole in place of the return phase, as for the tenor) with rd raised to 2.4, which restores the vowel distance and the per-bin open H1-H2 (deltas +0.29, +0.04, +0.10 dB) that fa_hz alone lowers. The soprano has no setting that raises warmth or timbre by 8 points with all gates held: the best held rows are breath 0.12 (timbre +5) and shelf_db 24 (warmth +5); soprano stays at the baseline source and noise. Accepted: alto fa_hz 4000, rd 2.4.

### Before and after (demo lead, seed 1234; word list, same seed)

```
voice      wl warm/tim   SingMOS   PQ     wl SingMOS  vow     phoneme hits f r l iy ey ih
baritone   83/60 same    3.21 same 7.22   2.55        12.94   2/4 11/30 8/14 15/17 15/15 5/15 (unchanged)
tenor      74/50 same    2.89->2.91 6.99  2.70        13.04   2/4 10/30 8/14 17/17 12/15->13/15 6/15
alto       32/40->50/40  3.32->3.55 6.78->7.00 2.61->2.70 13.08->13.07  1/4->2/4 6->12/30 10->8/14 11->17/17 8->5/15 6->4/15
soprano    69/34->71/36  3.09->3.33 7.22->7.27 2.79->2.82 10.69->10.73 2/4->2/4 3->2/30 4/14 8->16/17 5->4/15 4/15
```

Alto warmth +18 (timbre unchanged), SingMOS +0.23, PQ +0.22, per-bin open H1-H2 within 0.3 dB. Soprano changes only through the /iy/ modification. Not done: Whisper word error, listening by ear (the alto's brighter source and the soprano's /iy/ at 550-750 Hz need it).
