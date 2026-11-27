# Report B: soprano squeak, final /iy/, "rail" (demo "Every Harbor", seed 1234)
Status: INCOMPLETE (stopped on the usage limit). Worktree $SCRATCH/dwB and $SCRATCH/tgtB kept. Scratch diff: $SCRATCH/reportB.patch (env-gated knobs XM/XLO/XHI/XF2/XF3/XBW in synth.rs, XL* in articulation.rs, a VOICE_DUMP control-track dump; default env = trunk sound). Scripts: $SCRATCH/anB (an.py frame stats, engvow.py and ref_i.py per-vowel harmonic profiles vs VocalSet, tail.py word endings, voc_word.py LPC on vocadito, asr.py/asr2.py Whisper small.en, rend.sh). Renders: $SCRATCH/rB/{baritone,soprano,sA..sG,sEL,bL}.
A binary with /iy/ F1 340 was being built to $SCRATCH/anB/stems_iy340 (unmeasured); The worktree phoneme.rs is restored to 270; stems_iy340 holds the 340 build (unmeasured).

## Ranking (listener impact)
1. Soprano front vowels at high f0 (items 1, 2, and the soprano half of 3): one cause, F2/F3 of /iy/ and /ey/ too high for sung female vowels; /ey/ is heard as /iy/ ("name" -> "Knee", "same" -> "Scene", "pale" -> "Please"/"clear", "rail" -> "real").
2. "rail"/"pale", both voices: [i] offglide before coda /l/ plus a short, clear (high-F2) /l/: heard as "real" (both voices) and "play" (baritone).
3. Baritone final /iy/ of "carry"/"ferry": whistle-like [i] (H1 plus one isolated F2 partial); smaller.

## Item 1/2, soprano: the squeak
Notes (notes.json): "ferry" /r iy/ MIDI 71, 494 Hz, 0.284 s, after /f eh/ at 370 Hz (pitch glide 378 -> 494 Hz over 0.15 s, inside the /r/); "carry" chorus MIDI 77, 698 Hz, 0.284 s; verse 3 MIDI 70, 466 Hz, 0.861 s. Not 800-1000 Hz.
Engine /iy/ (soprano): F1 302 floored to 1.06 f0 (524 at 494 Hz, 740 at 698), F2 2794, F3 3672, F4 (3350-350 sf) fs = 3980 (B4 222), F5 4667, shelf 16 dB at 5.2 kHz.
Harmonics, dB re strongest (median of sustained frames; VocalSet female long tones, 9 singers, as reference):

    vowel f0 bin       H1  H2  H3  H4  H5  H6  H7  H8
    i 420-550 engine    0 -25 -35 -38 -35 -18 -25 -12
    i 420-550 VocalSet  0 -27 -22 -15 -19 -30 -30 -35
    i 550-750 engine    0 -24 -29 -22 -16  -9 -25 -44
    i 550-750 VocalSet  0 -23 -14 -23 -32 -37 -43 -49
    e 420-550 engine    0 -23 -30 -27 -20 -21 -23 -30
    e 420-550 VocalSet  0 -19 -11 -14 -21 -23 -27 -32

Finding: (a) F1 on H1 is NOT the fault: real sopranos have H1 25 dB over H2 on /i/ too. (b) is: the partial nearest the F2 (2.8 kHz) or the F3+F4 pair (3.7-4.0 kHz) stands 20-25 dB above the singers' (H8 = 3.95 kHz at 494 Hz sits on F4; H4 = 2.79 kHz at 698 Hz sits on F2), with 1.5-2.5 kHz 12-23 dB short. One loud high partial over a sine fundamental is the squeak; vibrato (0.42 st) swings it 9 dB (-9..-18 dB on the 466 Hz note). Bandwidth widening (B2, B3 x2 above 350 Hz, run sD) changed nothing (<1 dB). (c) mip tables, (d) pitch, (e) /r/: not the cause by these numbers (f0 within 2 Hz of control after the glide; /r/ F3 only dips to 2013 Hz soprano-scaled, it is not a squeak); (c) not separately measured.
Cause: articulation.rs Scale::of applies fs 1.22 to F2 and F3 of every vowel (params.rs soprano fs 1.22); no vowel modification with f0 exists (synth.rs `formants`, line ~239, only floors F1). Sung female /i/ F2 is about 2.0-2.1 kHz, /e/ about 1.7-1.9 kHz (from where VocalSet's strongest upper harmonic falls).
Candidate (synth.rs `formants`, env knobs): m = M * smoothstep(F0_LO, F0_HI, f0); F2' = F2 - m * max(F2 - F2R * fs, 0); F3' = F3 - m * max(F3 - F3R * fs, 0).

    run  M     LO-HI    F2R   F3R  i 420-550 H8  i 550-750 H6  J(female, demo only)  max|E-T|  3.15k band E-T
    base -     -        -     -    -12           -9            5.65                  11.4      +11.4
    sE   0.85  300-500  1600  2500 -36           -30           4.91                  9.9       -2.7
    sF   1.00  300-500  1700  2500 -38           -32           4.76                  9.9       -2.5
    sB   0.85  350-650  1600  2500 -24           -30           -                     -         -

Open H1-H2 per bin (350-400 Hz) E-T: +1.65 -> +1.61 (sE): unchanged. Remaining gap: 1.5-2 kHz still 10-14 dB short on /i/ at 420-550 Hz (source slope or F1-F2 valley; unmeasured). Whisper (small.en, lines): base soprano "turning clear", "Knee", "Scene"; sE: "turning clear", "real life" (no change seen on the 3 lines finished; word cuts not finished). Vowel probe (vow) and soundgate pitch: NOT run (soundgate built in tgtB; vow needs `cargo build -p voice --examples`).

## Item 2, baritone final /iy/
"ferry" 196 Hz, "carry" 277 Hz (chorus) and 185 Hz (verse 3). F1 /iy/ 270 (Peterson-Barney; Hillenbrand male 342); at 277 Hz it is floored to 294. Spectrum at 277 Hz: H1 0, H2 -24, H3-H7 -35..-44, H8 (2.2 kHz, on F2) -23: the same H1-plus-one-partial structure as the soprano, 20 dB weaker. r -> iy: F3 2112 -> 2974 Hz in 100 ms, F2 1550 -> 2270 in 80 ms; no offglide; vowel about 200 ms. Whisper hears "carry" in both chorus and verse 3 (baritone), "ferry" as "very" (the /f/: the other analyst's item). A real singer's unstressed "-ry" (vocadito 20 "merry", f0 240): F1 450-490, F2 2150-2250, 120 ms: [i] lowered toward [ɪ]. Candidate /iy/ F1 340: binary built, not measured.

## Item 3, "rail" (r ey l, one 1.335 s note; next line's /m/ starts 0.11 s after t1 with no pause)
Engine, baritone (165 Hz): /r/ F3 min 2013 (target mix 0.75*1650+0.25*2600=1887), F2 1446; [e] hold 450/2020/2600 for 1.0 s; [i] tail 180 ms at 340/2207/2777 (TAIL_MAX); coda /l/ 66 ms (0.055 x 1.2), F2 reaches 1340 only at its end (target 0.75*1050+0.25*2210), level -8 to -14 dB under the vowel median (av 0.45 x phrase fade 0.6), then /m/. "pale" (147 Hz): same tail and /l/, then silence. Soprano (415 Hz): [e] 504/2464/3172, tail to 381/2690, /l/ F2 to 1520.
Whisper: "rail" -> "real" in both voices (line "on the real light/life"); baritone "pale" word -> "play"; "same" -> "same"; soprano "pale" -> "Please"/"clear", "name" -> "Knee", "same" -> "Scene".
Real singer (vocadito 40, female, "tail" 288 Hz and "sale" 192 Hz, LPC): [e] F1 550-600, F2 2000-2250; offglide at most +100-200 Hz in F2, F1 flat; then an F2 fall over 200-250 ms to the /l/ (F2 1230-1370), /l/ 50-200 ms at -3 to -8 dB, released. The engine moves the opposite way (F2 up to [i], F1 down 110 Hz) for 180 ms, then a 66 ms /l/: "real".
Cause: articulation.rs plan_syllables, nucleus branch (diphthong tail clamp(vlen*0.3, TAIL_MIN, TAIL_MAX)) ignores the coda; consonant_segments Sonorant pull 0.25 toward the vowel; phoneme.rs L dur 0.055, av 0.45 used in codas too; no VC transition for liquids. Also ey0 450/2020/2600 is nearer Hillenbrand /ih/ (427/2034/2684) than /e/ (476/2089/2691).
Candidate (env XL=1, run bL baritone): coda /l/ 0.12 s, pull 0.10, av 0.6; ey glide 30%; 150 ms linear vowel-to-l transition. Measured "rail": F2 2020 -> 1155 over 27.62-27.78, /l/ F2 1155 for 120 ms; level dip -9 to -13 dB (deeper than real -3..-8: av 0.6 too low with the fade; use 0.8). Whisper on bL/sEL: not finished.

## Draft specification (UNVERIFIED where marked)
S1 High-pitch front-vowel modification (synth.rs `VoiceSynth::formants`; store p.fs in VoiceSynth):
  consts VMOD_MAX 0.85, VMOD_F0_LO 300.0, VMOD_F0_HI 500.0, VMOD_F2_REF 1600.0, VMOD_F3_REF 2500.0 (Hz, unscaled, times fs).
  m = VMOD_MAX * smoothstep(VMOD_F0_LO, VMOD_F0_HI, f0); f2 -= m*max(f2 - VMOD_F2_REF*fs, 0); f3 -= m*max(f3 - VMOD_F3_REF*fs, 0); before the wobble. Bit-identical below 300 Hz (baritone, bass, tenor untouched except above 300 Hz).
  Accept (soprano demo seed 1234, engvow.py): /i/ H6..H8 at 420-550 Hz and H5..H6 at 550-750 Hz within 6 dB of VocalSet (measured sE: met); voice_target J (female) <= 5.0 demo-only (sE 4.91); open H1-H2 per bin within 0.5 dB of before (met); [unverified] vow soprano >= 10.3 dB, soundgate pitch >= 0.99 within 50 cents, Whisper hears "name"/"same" not "knee"/"scene" in >= 1 of 2. Stop rule: if J or the profile targets fail at these values, try VMOD_MAX 1.0 with F2_REF 1700 (sF) once, then stop and report.
S2 Coda /l/ (articulation.rs, phoneme.rs): CODA_L 0.12 s in cons_dur (Sonorant, coda, L); coda-L pull 0.10 (onset keeps 0.25); coda-L av 0.80 (CODA_L_AV); a nucleus followed by coda /l/ moves its later diphthong targets PRE_L_GLIDE 0.3 of the way from the first; VL transition VL_TRANSITION 0.15 s, linear, ending at the /l/ target, limited to vlen*VC_SHARE. Accept: "rail"/"pale" /l/ F2 in the control track <= 1200 Hz (baritone) for >= 80 ms; dip 3-8 dB under the vowel before the transition; Whisper "rail" not "real" in 1 of 2 voices [unverified]; Helmholtz/gate unaffected (voice only). Stop rule: one pass at these values; report numbers.
S3 [unverified] /iy/ F1 270 -> 340 (phoneme.rs VOWELS) and ey0 450 -> 480 (Hillenbrand): measure baritone vow (>= 12.8 dB floor) and the carry profile before adopting; rebaseline the gate in its own commit.

## Not determined
Whisper on the candidates (machine contended; 3 lines only), vow probe, soundgate pitch, center-pivot material for J, mip-table discontinuity (c), alto (fs 1.16, same rule likely applies), the 1.5-2 kHz deficit on soprano /i/. A rerun of asr2.py on rB/sE, rB/sEL, rB/bL and the vow/pitch tools settles the first three.
