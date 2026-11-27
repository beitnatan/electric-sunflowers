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
