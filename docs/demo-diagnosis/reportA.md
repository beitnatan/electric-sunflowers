# Report A: baritone /f/, "riv" heard as /ay/, pauses between words (demo "Every Harbor", seed 1234, baritone)

Status: incomplete. The analyst stopped early on the usage limit. Item 1 is solved and its fix is tested on the metrics; Whisper on the fix did not finish. Item 2 has a probable cause but no ear proxy confirms it. Item 3 has a best reading only.

Kept in the scratch area of the cloud session that wrote this (may be gone): the worktree dwA (trunk ca1e4f27 plus scratch knobs), the build directory tgtA, scripts in toolsA (ctdump.rs, hfrel.py, fmeas.py, wcut3.py, dips.py), renders in rA/{base,f05,f08,r1,u07}. The diff is reportA.patch in this directory: it adds environment knobs SF_F, SF_V, SF_FDUR, SF_R, SF_RDUR and SF_UNREL in crates/voice/src/articulation.rs; a render with no knob set is byte-identical to trunk. The web page defaults to seed 1234 and the song's own voice (the baritone), so these stems match what the owner heard.

Rank by how much a listener notices: item 1, then item 3, then item 2.

## Item 1: /f/ is heard as /s/ (cause found, fix tested on the metrics)

Level is the fricative relative to the following vowel, per-sample power, in dB. The real /f/ and /s/ are from vocadito, located with Whisper word timestamps.

    source              broad   1-3k   3-5k   5-8k
    real /f/ for        -35.0  -24.5   -8.5   +0.3
    real /f/ fired      -19.4   -8.3   +7.1   +5.9
    real /f/ off        -17.9  -11.3   +8.3   +3.4
    real /s/, 6 words   median -9 (-6..-17)          5-8k median +24
    engine /f/ fer       -3.9   -4.6  +16.3  +20.8
    engine /f/ four      -6.5   +2.1  +21.9  +23.7
    engine /s/           about 0 dB broadband        5-8k +29..+30
    candidate f05 fer   -17.3  -12.8   +3.4   +5.0
    candidate f05 four  -19.7   -5.3   +9.7   +8.4

- Spectral shape: real /f/ is flat in 1/3 octaves, with the 1.25 kHz band 3 to 9 dB under the strongest band. The engine's 1.25 kHz band is 15 to 21 dB under, with its peak at 6 to 8 kHz: the shape of an /s/. f05 is -7 to -11 dB at 1.25 kHz, then flat.
- Level: all four engine /f/ (n1, n5, n7, n128) are -3.9 to -7.1 dB broadband against a real median of about -19. They sit only 4 to 6 dB under the engine's /s/; real /f/ sits 10 to 20 dB under /s/.
- Whisper on the base render: "half past four" became "has passed slowly"; "four" became "S"; "ferry" became "Saaaarly" or "very"; "for good" became "for you".
- Whisper on f05: not run; the job was cut off.
- Cause: phoneme.rs:219, `fric(5500, 7000, 0.24, ...)`. The band-pass Q is 0.79 at 5.5 kHz (tract.rs:222), and its output is scaled by FRICATION_GAIN 2.2 times cons_gain 2.74 (tract.rs:149). The result is about 15 dB too loud overall and 17 dB too loud at 5-8 kHz.
- What is fine: the 96 ms duration and the labiodental F2 locus of 1100 Hz with its transition.
- Not measured: the edges. SMOOTH_FRICATION is 2 ms, so they are abrupt.

SPEC 1
- In phoneme.rs:219, set /f/ to ff `F_FF = 3000.0`, bw `F_BW = 6000.0` (Q 0.5), af `F_AF = 0.05`; duration 0.08 and the labiodental locus stay.
- Acceptance, measured with toolsA/hfrel.py and fmeas.py:
  - every /f/ is -15 to -23 dB broadband against its vowel;
  - the 5-8 kHz band is +0 to +12 dB;
  - the 1.25 kHz band is within 10 dB of the strongest band;
  - Whisper (toolsA/wcut3.py) puts no "s" in place of the "f" in "half past four" or "four";
  - the gate passes; vowel distance and pitch must be unchanged.
- Stop rule: if Whisper still hears /s/, try F_AF 0.035 once and report.
- Same pattern, not specified (owner's ear first):
  - /v/ (5000/6000/0.16, measured -7 to -11 dB) probably wants 3000/6000/0.035.
  - /s/ is about 9 dB too loud broadband; its 3-5 kHz band is 15 dB high because bw 3500 is too wide.
  - /sh/ is +19.6 dB at 1-3 kHz against a real -15 to +2 ("shown").

## Item 2: "riv" heard as /ay/ (probable cause, not proven)

- Data path: correct. n42, n103 and n148 are `r ih`, followed by `v er`. Each "riv" note is 0.861 s at MIDI 62 (D4, 294 Hz), the top of the baritone's range.
- Vowel target: exact. The control tracks hold /ih/ at 430/2010/2600 Hz for 0.70 s. There is no glide, and the F1 floor at 1.06 f0 is inactive.
- Steady-part spectrum: H1 0 dB, H2 -8, H7 (2.06 kHz) -8 to -13, H9 -9 to -15. That is an /ih/.
- Transition (the fault):
  - Before "riv" comes "the" /ax/ (560/1250/2420 Hz) at B3. The /r/ lasts only 72 ms.
  - Its target is pulled 25% toward the vowel (articulation.rs:801), and the smoothing then leaves the realised F3 minimum at 2014 Hz (F2 1475). An /r/ needs F3 near 1600.
  - The audio carries almost no energy near 1.5-1.7 kHz in the /r/ frames. The listener gets [ax] going to [ih] with a weak /r/: F1 560 to 430 and F2 1250 to 2010 in about 80 ms, which is an [aI]-like glide.
- Whisper: hears "River" in every cut, so its language prior makes it useless as a test here.
- Candidate r1: no pull, F2 1150, F3 1550, 80 ms. Rendered, not measured.
- Overlap with the other analyst: "rail" and "ferry"/"carry"/"every" use the same /r/ onset path.

SPEC 2 (draft, unverified)
- In `consonant_segments`, Sonorant branch, give /r/ a pull of `R_PULL = 0.0` instead of 0.25.
- In phoneme.rs:197, set /r/ to `[420, 1150, 1550]` Hz and duration 0.075.
- Acceptance:
  - every onset /r/ has its F3 minimum at or below 1700 Hz, and F2 at or below 1350 Hz for at least 25 ms;
  - F2 reaches 1900 within 60 ms of the vowel start;
  - the gate passes and vowel distance is unchanged.
- Stop rule: one render. If the owner still hears /ay/, test the pitch instead: re-sing "riv" at MIDI 55 to separate pitch (H2 at 588 Hz next to F1 430) from the /r/.

## Item 3: pauses between words (best reading)

What the composition does:
- Every note gap within a line is 4 ms; there is no compose or arrange gap rule at work.
- The default phrasing (flowing delivery, released endings) does not shorten any note.
- The released fade applies only to phrase-final notes.
- At line ends the gap is 108 ms at 11 of 21 lines, with silence and no breath (a breath needs a gap over 0.4 s). There are 3 gaps of 396 ms and 6 of 1.262 s.

Silences within lines: in the control tracks, the only fully silent runs are voiceless stop closures of 10 to 52 ms. The longest: half|past 52 ms; past|four 46 ms; salt|still 45 ms; er|stood 44 ms. There is no hole of 100 ms or more.

Level contour within notes, median, dB against the note middle, from 10% to 90% of the note:

    engine  -0.2 -0.1 0.1 0.1 0.1 0.0 -0.3 -0.9 -5.4
    real     2.2  1.5 0.2 0.4 0.3 -0.2 -0.3 -0.9 -2.0

There is no per-word swell. The engine's vowel ends earlier and more steeply.

Dips at note boundaries (5 ms RMS minimum against the quieter neighbouring vowel):

    set            n    p10/p50/p90 dB        run < -20 dB p50/p90/max   dips < -40 dB
    engine words  116   -47.1 / -8.5 / -0.6   0 / 45 / 75 ms             19%
    engine sylls   29   -23.0 / -8.3 / -1.1   0 / 23 / 40 ms              3%
    vocadito      270   -27.1 / -6.9 / -1.8   0 / 20 / 105 ms             1%

- Vocadito's noise floor is -38 to -53 dB, so -40 dB dips are reachable there.
- Median dip by boundary type:
  - within the real 6-15 dB: vowel to liquid -9.6, vowel to nasal -9.3, vowel to voiced fricative -9.8, nasal to vowel -9.9;
  - far deeper: vowel to stop -41, stop to fricative -62, fricative to stop -67, nasal to stop -46.
- The voiceless closures are digital silence, -70 to -95 dB, because the synth skips quiet frames and resets the tract (synth.rs:276).
- Candidate u07 (a coda-final stop before a consonant onset left unreleased: no burst, closure 0.7x): almost no effect. The share of dips under -40 dB went from 19% to 17%, and the p90 run from 45 to 42 ms. Whisper and the gate were not run.

Cause, best reading (not proven by ear, not measured in the mix):
- (a) Voiceless closures are true silence, 20-52 ms at -70 to -95 dB. Real closures sit at about -30 to -40 dB; 19% of the engine's word boundaries go under -40 dB against 1% for real singers.
- (b) Voiceless fricatives are about 10 dB too loud and cut the words apart.
- (c) Every coda stop is released with a burst.
- Side note: the doubles sing 13 and 21 ms late (arrange/src/vocals.rs:266), so every burst is tripled in the mix. Not measured.

SPEC 3 (draft, unverified)
- (a) Within a line (next note not phrase_start), do not skip or reset the tract during a voiceless Closure or Silence. Add `CLOSURE_LEAK = 0.03 x amp` of voicing, low-passed like a voice bar, for the first `LEAK_TIME = 0.025 s` of the closure.
- (b) Apply SPEC 1, then a separate /s/ and /sh/ level fix (not specified here).
- (c) Optional: unreleased coda-final stops before a consonant onset, as in the SF_UNREL logic of the patch, at closure scale 0.7.
- Acceptance, measured with toolsA/dips.py:
  - word dips under -40 dB at most 5%;
  - p90 run under -20 dB at most 30 ms;
  - vowel-to-stop median dip between -25 and -40 dB;
  - voiceless stops still heard as voiceless by Whisper ("past", "keeps", "car");
  - Helmholtz, pitch and vowel distance unchanged.
- Stop rule: one render with (a) alone and one with (a) plus (c), both sent to the owner. Do not tune CLOSURE_LEAK.

## Not determined
- Whisper on f05 and f08: the job was cut off.
- r1's F3 and its effect: not measured. No proxy separates "riv" from "rive".
- Which item-3 mechanism the owner means: an A/B listen of base against (a) and against (c), or measuring the dips on the mix instead of the dry lead, would settle it.
- Not run: voice_target J, the gate, and the doubles' effect.
