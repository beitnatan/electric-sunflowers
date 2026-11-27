//! Articulation: sung notes to a timed plan of typed segments, by synthesis
//! by rule with targets and transitions (Holmes, Mattingly and Shearme
//! 1964; Klatt 1987). `crate::controls` turns the plan into frame tracks.
//!
//! Rules, in the order `plan_segments` applies them:
//!
//! - Syllable split: onset consonants, nucleus (first through last vowel),
//!   coda consonants. A note with no vowel sings /aa/, the choir vowel, and
//!   all its phonemes are coda.
//! - Durations: `cons_dur` times the voice's consonant scale (bass 1.15,
//!   baritone 1.2, tenor 1.25, alto 1.35, soprano 1.4).
//! - Flapping: a single /t/ or /d/ onset of an unstressed, non-phrase-initial
//!   note, after a note with no coda and less than 150 ms of gap, becomes
//!   the flap /dx/ (20 ms closure, not scaled).
//! - Onset compression: onset consonants start before the note and take at
//!   most 45% of the inter-onset interval (300 ms for the first note).
//! - Coda: ends at the note end, or when the next onset starts less than 30
//!   ms after it, at that onset (not before t0 + 60 ms); takes at most 40%
//!   of the note.
//! - CV transition: after a stop or affricate onset, F1-F3 move from the
//!   locus to the vowel over min(50 ms, 40% of the vowel) in 5 steps, eased
//!   1 - (1 - a)^1.6, with voicing rising from 0.7 to 1 (locus theory,
//!   Delattre, Liberman and Cooper 1955).
//! - Diphthongs and sonorant nuclei: the first target holds, the later
//!   targets share a tail of 30% of the vowel, clamped to 50-180 ms.
//! - Stops: closure (voicing 0.1 when voiced), a 12 ms burst at 0.7 onset
//!   or 0.4 coda times `BURST_GAIN` with 0.12 aspiration, and for voiceless
//!   onsets aspiration 0.4 with F1 damping (b1x 320 Hz) to the end of the
//!   consonant. Closure and aspiration scale with the consonant; the burst
//!   does not. Burst and closure formants sit 60% of the way to the locus.
//! - Affricates: closure, an 8 ms burst at 0.6, then frication (voicing 0.3
//!   when voiced); all three scale.
//! - Sonorants and nasals in onset or coda: their targets pulled 25% toward
//!   the vowel.
//! - Pauses: silence carrying the next nucleus's formants; before a phrase
//!   after more than 400 ms, a 240 ms breath ending 40 ms before the onset.
//!
//! Fast notes. The rules above were set for slow singing; on a 204 ms note
//! (144 bpm, one syllable per eighth) they left the vowel a median 148 ms
//! and 10% of notes under 73 ms. A note's rate weight is w = clamp((0.32 -
//! T) / 0.10, 0, 1), with T its inter-onset interval (its duration when it
//! ends a phrase or the song): 0 from T = 0.32 s (the rules above, bit for
//! bit), 1 from T = 0.22 s down. At weight w:
//!
//! - the consonant scale is the voice's times (1 - 0.35 w) (the stop burst
//!   and the flap keep their fixed lengths);
//! - the onset share is the phrasing's share less 0.17 w (0.45 - 0.17 w for
//!   flowing delivery), the coda share 0.40 - 0.18 w;
//! - the longest CV transition is (50 - 20 w) ms, after a velar (80 - 30 w)
//!   ms; the longest VC transition (45 - 15 w) ms; their share limits stay;
//! - a diphthong or sonorant-nucleus tail is at least (50 - 20 w) ms.
//!
//! The onset consonants sit in the previous note's interval, so their
//! duration and share use the weight of that interval (the gap from the
//! previous onset); the first note's onset uses its own weight. Coda,
//! transitions and tail use the note's own weight.

use std::ops::Range;

use sfcore::math::clamp;
use song::events::VocalNote;
use song::Phoneme;

use crate::params::VoiceParams;
use crate::phoneme::{consonant, vowel_formants, ConsClass, Consonant, Locus};
use crate::phrasing::PhrasingParams;
use crate::synth::VoiceSettings;
use crate::tuning::BURST_GAIN;

/// F1-F3 in Hz.
pub type Formants3 = [f64; 3];

/// A time span in seconds.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Span {
    pub t0: f64,
    pub t1: f64,
}

impl Span {
    pub fn len(&self) -> f64 {
        self.t1 - self.t0
    }
}

/// One articulation target. Every kind carries its F1-F3 (voice-scaled),
/// since the tract keeps filtering through noise and silence. Levels are
/// absolute (note amplitude folded in).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Segment {
    /// Voiced vowel (also each step of a CV transition).
    Vowel { f: Formants3, av: f64 },
    /// Liquid or glide.
    Sonorant { f: Formants3, av: f64 },
    /// Nasal murmur: voicing with widened bandwidths and the nasal
    /// antiresonance `zero` (Hz).
    Nasal { f: Formants3, av: f64, zero: f64 },
    /// Frication noise at centre `ff`, bandwidth `fbw`, over optional voicing.
    Fricative {
        f: Formants3,
        av: f64,
        af: f64,
        ff: f64,
        fbw: f64,
    },
    /// Aspiration noise through the tract; `b1x` Hz of extra F1 bandwidth.
    Aspiration { f: Formants3, ah: f64, b1x: f64 },
    /// Stop or affricate release: frication band plus aspiration.
    Burst {
        f: Formants3,
        af: f64,
        ff: f64,
        fbw: f64,
        ah: f64,
    },
    /// Oral closure; `av` is the voicing that leaks through (voiced
    /// stops). `bar` marks a voice bar: the voiced signal is low-passed
    /// (`tuning::VOICE_BAR_HZ`) while it lasts.
    Closure {
        f: Formants3,
        av: f64,
        bar: bool,
        hold: bool,
    },
    /// Audible in-breath before a phrase.
    Breath { f: Formants3 },
    /// Nothing sounds; the formants move toward the next nucleus.
    Silence { f: Formants3, hold: bool },
}

impl Segment {
    pub fn formants(&self) -> Formants3 {
        match *self {
            Segment::Vowel { f, .. }
            | Segment::Sonorant { f, .. }
            | Segment::Nasal { f, .. }
            | Segment::Fricative { f, .. }
            | Segment::Aspiration { f, .. }
            | Segment::Burst { f, .. }
            | Segment::Closure { f, .. }
            | Segment::Breath { f }
            | Segment::Silence { f, .. } => f,
        }
    }
}

/// Default nucleus of a note without a vowel: /aa/, the choir vowel.
pub const DEFAULT_NUCLEUS: Phoneme = Phoneme::Aa;
/// Onset room of the first note, s.
pub const FIRST_ONSET: f64 = 0.3;
/// Share of the note the coda may take.
pub const CODA_SHARE: f64 = 0.4;
/// Longest VC transition (vowel toward a coda consonant's locus), s, its
/// largest share of the vowel, and how far toward the locus it moves.
const VC_TRANSITION: f64 = 0.045;
const VC_SHARE: f64 = 0.3;
const VC_DEPTH: f64 = 0.7;
/// Pull of an onset /r/ toward the vowel: none, so F3 reaches its dip.
const R_PULL: f64 = 0.0;
/// Pull of other sonorants and nasals toward the vowel.
const SONORANT_PULL: f64 = 0.25;
/// Coda /l/: duration (s), pull toward the vowel and voicing level.
const CODA_L: f64 = 0.12;
const CODA_L_PULL: f64 = 0.10;
const CODA_L_AV: f64 = 0.80;
/// A nucleus before a coda /l/ moves its later diphthong targets only this
/// far from the first (the offglide stays small).
const PRE_L_GLIDE: f64 = 0.3;
/// Longest linear transition from the vowel to a coda /l/, s (also limited
/// to `VC_SHARE` of the vowel).
const VL_TRANSITION: f64 = 0.15;

/// Longest CV transition, s (locus to vowel).
pub const CV_TRANSITION: f64 = 0.05;
/// Longest CV transition after a velar, s. A back place needs a longer, visible
/// F2 and F3 glide from a high, merged start (the velar pinch), or the stop
/// is heard as /t/ (issue 22).
pub const VELAR_CV_TRANSITION: f64 = 0.08;
/// Velar burst band centre, Hz (every vowel context; bandwidth is in the table).
const VELAR_BURST_HZ: f64 = 1800.0;
/// Steps of a CV transition.
const CV_STEPS: usize = 5;
/// Stop burst length, s (not scaled per voice).
pub const STOP_BURST: f64 = 0.012;
/// Aspiration after a voiceless onset stop, s (before voice scaling).
pub const STOP_ASPIRATION: f64 = 0.024;
/// Affricate burst length, s.
pub const AFFRICATE_BURST: f64 = 0.008;
/// Coda nasal length, s.
pub const CODA_NASAL: f64 = 0.085;
/// Longest gap after which a /t d/ onset still flaps, s.
const FLAP_GAP: f64 = 0.15;
/// Breath: starts this long before the next onset and ends `BREATH_END`
/// before it, s; only after a pause longer than `BREATH_PAUSE`.
const BREATH_START: f64 = 0.28;
const BREATH_END: f64 = 0.04;
const BREATH_PAUSE: f64 = 0.4;
/// Tract formants of the breath (an open, neutral tract), Hz.
const BREATH_FORMANTS: Formants3 = [620.0, 1200.0, 2400.0];
/// Shortest closure of a voiceless stop at any rate, s (or its unweighted
/// length when that is shorter). The rate weight alone would cut it to
/// 35 ms in a fast song, and the closure then stays above -30 dB.
pub const MIN_VL_CLOSURE: f64 = 0.045;
/// Voicing in a voiced closure, as a share of the note's amplitude.
pub const CLOSURE_VOICING: f64 = 0.145;
/// Voicing that leaks through a voiceless closure within a line (a share
/// of the note's amplitude) and how long it lasts, s.
pub const CLOSURE_LEAK: f64 = 0.03;
pub const LEAK_TIME: f64 = 0.025;
/// Highest F1 in a voiced closure, Hz: the closed tract's lowest
/// resonance.
pub const CLOSED_F1_HZ: f64 = 250.0;
/// Largest share of the inter-onset interval the onset takes when a
/// voiceless stop's closure is lengthened to `MIN_VL_CLOSURE`.
pub const PROTECT_ONSET_SHARE: f64 = 0.40;
/// Largest share of the vowel-to-coda-end span a coda takes when a
/// voiceless stop's closure is lengthened to `MIN_VL_CLOSURE`.
pub const PROTECT_CODA_SHARE: f64 = 0.30;
/// Segments shorter than this are dropped, s.
const MIN_SEGMENT: f64 = 1e-4;
/// Inter-onset interval from which the slow-singing rules hold unchanged, s.
pub const RATE_SLOW: f64 = 0.32;
/// Interval below `RATE_SLOW` over which the rate weight rises from 0 to 1, s.
pub const RATE_RAMP: f64 = 0.10;
/// Share of the voice's consonant scale removed at rate weight 1.
const FAST_CONS_CUT: f64 = 0.35;
/// Onset share of the interval removed at rate weight 1.
const FAST_ONSET_CUT: f64 = 0.17;
/// Coda share of the note removed at rate weight 1.
const FAST_CODA_CUT: f64 = 0.18;
/// Longest CV transition removed at rate weight 1, s.
const FAST_CV_CUT: f64 = 0.02;
/// Longest velar CV transition removed at rate weight 1, s.
const FAST_VELAR_CV_CUT: f64 = 0.03;
/// Longest VC transition removed at rate weight 1, s.
const FAST_VC_CUT: f64 = 0.015;
/// Shortest diphthong or sonorant-nucleus tail at rate weight 0, s.
const TAIL_MIN: f64 = 0.05;
/// Longest diphthong or sonorant-nucleus tail, s.
const TAIL_MAX: f64 = 0.18;
/// Shortest tail removed at rate weight 1, s.
const FAST_TAIL_CUT: f64 = 0.02;

/// Rate weight of a note whose inter-onset interval is `t` s: 0 at and
/// above `RATE_SLOW`, 1 at and below `RATE_SLOW - RATE_RAMP`, linear between.
pub fn rate_weight(t: f64) -> f64 {
    clamp((RATE_SLOW - t) / RATE_RAMP, 0.0, 1.0)
}

/// Inter-onset interval of note `k`: to the next onset, or the note's
/// duration when it ends a phrase or the song.
fn interval(notes: &[VocalNote], k: usize) -> f64 {
    let n = &notes[k];
    match notes.get(k + 1) {
        Some(nn) if !nn.phrase_start => nn.t0 - n.t0,
        _ => n.t1 - n.t0,
    }
}

/// Kind of a nucleus target.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NucKind {
    Vowel,
    Sonorant,
    Nasal,
}

/// One formant target of a nucleus (unscaled).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct NucTarget {
    pub f: Formants3,
    pub kind: NucKind,
    /// Relative voicing: 1 for vowels, the table value for sonorants and
    /// nasals.
    pub av: f64,
    /// Nasal antiresonance, Hz (nasals only).
    pub fnz: f64,
}

impl NucTarget {
    fn segment(&self, f: Formants3, amp: f64) -> Segment {
        let av = amp * self.av;
        match self.kind {
            NucKind::Vowel => Segment::Vowel { f, av },
            NucKind::Sonorant => Segment::Sonorant { f, av },
            NucKind::Nasal => Segment::Nasal {
                f,
                av,
                zero: self.fnz,
            },
        }
    }
}

const DEFAULT_TARGET: NucTarget = NucTarget {
    f: match vowel_formants(DEFAULT_NUCLEUS) {
        Some(f) => f,
        None => [730.0, 1090.0, 2440.0],
    },
    kind: NucKind::Vowel,
    av: 1.0,
    fnz: 0.0,
};

/// The formant targets of a nucleus: a diphthong gives its two vowel
/// targets, a sonorant or nasal inside the nucleus its own; other
/// consonants none. Never empty: the fallback is /aa/.
pub fn nuc_targets(nu: &[Phoneme]) -> Vec<NucTarget> {
    let mut out = Vec::with_capacity(nu.len().max(1) + 1);
    for &p in nu {
        if let Some(pair) = p.diphthong_targets() {
            out.extend(
                pair.iter()
                    .filter_map(|&v| vowel_formants(v))
                    .map(|f| NucTarget {
                        f,
                        kind: NucKind::Vowel,
                        av: 1.0,
                        fnz: 0.0,
                    }),
            );
        } else if let Some(f) = vowel_formants(p) {
            out.push(NucTarget {
                f,
                kind: NucKind::Vowel,
                av: 1.0,
                fnz: 0.0,
            });
        } else if let Some(c) = consonant(p) {
            let kind = match c.class {
                ConsClass::Sonorant => NucKind::Sonorant,
                ConsClass::Nasal => NucKind::Nasal,
                _ => continue,
            };
            out.push(NucTarget {
                f: c.formants,
                kind,
                av: c.av,
                fnz: c.fnz,
            });
        }
    }
    if out.is_empty() {
        out.push(DEFAULT_TARGET);
    }
    out
}

/// A consonant's nominal duration in seconds, before voice scaling: stops
/// are closure plus the 12 ms burst plus 24 ms aspiration when voiceless in
/// an onset; affricates closure plus 8 ms burst plus frication; a coda
/// nasal 85 ms; others their table duration. Vowels have none.
pub fn cons_dur(p: Phoneme, coda: bool) -> f64 {
    let Some(c) = consonant(p) else {
        return 0.0;
    };
    match c.class {
        ConsClass::Stop => {
            c.closure
                + STOP_BURST
                + if coda || c.voiced {
                    0.0
                } else {
                    STOP_ASPIRATION
                }
        }
        ConsClass::Affricate => c.closure + AFFRICATE_BURST + c.fric_dur,
        ConsClass::Nasal if coda => CODA_NASAL,
        ConsClass::Sonorant if coda && p == Phoneme::L => CODA_L,
        _ => c.dur,
    }
}

/// Seconds a voiceless stop's closure falls short of `MIN_VL_CLOSURE` (or
/// its unweighted length `c.closure * cs` when that is shorter), given the
/// stop's duration `dur` after rate weight and compression. Zero for every
/// other consonant.
fn closure_deficit(ph: Phoneme, cs: f64, dur: f64, coda: bool) -> f64 {
    match consonant(ph) {
        Some(c) if c.class == ConsClass::Stop && !c.voiced => {
            let have = c.closure * dur / cons_dur(ph, coda);
            (MIN_VL_CLOSURE.min(c.closure * cs) - have).max(0.0)
        }
        _ => 0.0,
    }
}

/// Grant `deficits` (seconds of closure extension), at most `room` in all
/// (in proportion when it does not fit).
fn grant(deficits: &[f64], room: f64) -> Vec<f64> {
    let total: f64 = deficits.iter().sum();
    let k = if total > room.max(0.0) && total > 0.0 {
        room.max(0.0) / total
    } else {
        1.0
    };
    deficits.iter().map(|d| d * k).collect()
}

/// One note's syllable after splitting, flapping and onset timing.
#[derive(Clone, Debug)]
pub struct Syllable {
    pub onset: Vec<Phoneme>,
    pub nucleus: Vec<Phoneme>,
    pub coda: Vec<Phoneme>,
    /// Onset consonant durations, s (voice-scaled, before compression).
    pub onset_dur: Vec<f64>,
    /// Coda consonant durations, s (voice-scaled, before compression).
    pub coda_dur: Vec<f64>,
    /// Seconds added after compression to each onset consonant (a
    /// voiceless stop's closure, held at `MIN_VL_CLOSURE`).
    pub onset_extra: Vec<f64>,
    /// Onset compression factor, <= 1.
    pub onset_scale: f64,
    /// Time the first onset consonant starts, s (<= `vowel_start`).
    pub onset_start: f64,
    /// Time the nucleus starts, s (the note's `t0` with `lead_in` 1.0;
    /// later with a smaller `lead_in`, which pushes part of the onset
    /// consonants' scaled span past the written onset).
    pub vowel_start: f64,
    /// Nucleus formant targets (unscaled), never empty.
    pub targets: Vec<NucTarget>,
    /// Rate weight of the note's own interval (coda, transitions, tail).
    pub rate_w: f64,
    /// Rate weight of the interval the onset sits in (onset durations and
    /// share).
    pub onset_rate_w: f64,
}

/// Split `ph` into onset consonants, nucleus (first vowel through last
/// vowel, with anything between) and coda consonants. With no vowel the
/// nucleus is /aa/ and every phoneme is coda.
pub fn split_ph(ph: &[Phoneme]) -> (Vec<Phoneme>, Vec<Phoneme>, Vec<Phoneme>) {
    let first = ph.iter().position(|p| p.is_vowel());
    let last = ph.iter().rposition(|p| p.is_vowel());
    match (first, last) {
        (Some(i0), Some(i1)) => (
            ph[..i0].to_vec(),
            ph[i0..=i1].to_vec(),
            ph[i1 + 1..].to_vec(),
        ),
        _ => (Vec::new(), vec![DEFAULT_NUCLEUS], ph.to_vec()),
    }
}

/// Per-note syllables of `notes` for voice `p`: split, durations, flapping,
/// onset compression against `ph.onset_share`, and the onset/vowel span
/// placed by `ph.lead_in`; the rate rules of the module doc.
pub fn syllables(notes: &[VocalNote], p: &VoiceParams, ph: &PhrasingParams) -> Vec<Syllable> {
    syllables_by(notes, p, ph, rate_weight)
}

/// `syllables` with rate weight `weight` of an interval in seconds.
fn syllables_by(
    notes: &[VocalNote],
    p: &VoiceParams,
    ph: &PhrasingParams,
    weight: impl Fn(f64) -> f64,
) -> Vec<Syllable> {
    let cs = p.cons_scale;
    let mut syl: Vec<Syllable> = notes
        .iter()
        .enumerate()
        .map(|(k, n)| {
            let (onset, nucleus, coda) = split_ph(&n.phones);
            let onset_len = onset.len();
            let rate_w = weight(interval(notes, k));
            let onset_rate_w = if k > 0 {
                weight(n.t0 - notes[k - 1].t0)
            } else {
                rate_w
            };
            let cs_on = cs * (1.0 - FAST_CONS_CUT * onset_rate_w);
            let cs_co = cs * (1.0 - FAST_CONS_CUT * rate_w);
            let onset_dur = onset
                .iter()
                .map(|&ph| cons_dur(ph, false) * cs_on)
                .collect();
            let coda_dur = coda.iter().map(|&ph| cons_dur(ph, true) * cs_co).collect();
            let targets = nuc_targets(&nucleus);
            Syllable {
                onset,
                nucleus,
                coda,
                onset_dur,
                coda_dur,
                onset_extra: vec![0.0; onset_len],
                onset_scale: 1.0,
                onset_start: n.t0,
                vowel_start: n.t0,
                targets,
                rate_w,
                onset_rate_w,
            }
        })
        .collect();

    for k in 1..notes.len() {
        let (n, prev) = (&notes[k], &notes[k - 1]);
        let single_td = matches!(syl[k].onset.as_slice(), [Phoneme::T | Phoneme::D]);
        if single_td
            && !n.stress
            && !n.phrase_start
            && syl[k - 1].coda.is_empty()
            && n.t0 - prev.t1 < FLAP_GAP
        {
            syl[k].onset = vec![Phoneme::Dx];
            syl[k].onset_dur = vec![cons_dur(Phoneme::Dx, false)];
        }
    }

    for k in 0..notes.len() {
        let n = &notes[k];
        let d: f64 = syl[k].onset_dur.iter().sum();
        let avail = if k > 0 {
            (n.t0 - notes[k - 1].t0) * (ph.onset_share - FAST_ONSET_CUT * syl[k].onset_rate_w)
        } else {
            FIRST_ONSET
        };
        let s = if d > avail && d > 0.0 { avail / d } else { 1.0 };
        syl[k].onset_scale = s;
        // Onset consonants of scaled length `dd` span [t0 - lead_in dd,
        // t0 + (1 - lead_in) dd]; the vowel starts at the span end.
        let dd = d * s;
        // A voiceless stop's compressed closure is lengthened to
        // `MIN_VL_CLOSURE` while the onset as a whole takes at most
        // `PROTECT_ONSET_SHARE` of the interval (the cap rises to that for
        // this note only, and never falls below the rate-weighted share);
        // the extension starts the onset earlier.
        let want: Vec<f64> = syl[k]
            .onset
            .iter()
            .zip(&syl[k].onset_dur)
            .map(|(&q, &dq)| closure_deficit(q, cs, dq * s, false))
            .collect();
        let cap = avail.max(if k > 0 {
            (n.t0 - notes[k - 1].t0) * PROTECT_ONSET_SHARE
        } else {
            0.0
        });
        syl[k].onset_extra = grant(&want, cap - dd);
        let ee: f64 = syl[k].onset_extra.iter().sum();
        syl[k].onset_start = n.t0 - ph.lead_in * dd - ee;
        syl[k].vowel_start = n.t0 + (1.0 - ph.lead_in) * dd;
        let dd = dd + ee;
        // The vowel may not be pushed past half the note: on a short note
        // (or the first note's long onset) it would start after the note
        // ends. The span keeps length `dd`. Inactive at lead_in 1.0.
        let shift = syl[k].vowel_start - n.t0;
        let cap = 0.5 * (n.t1 - n.t0).max(0.0);
        if shift > cap {
            syl[k].vowel_start = n.t0 + cap;
            syl[k].onset_start = syl[k].vowel_start - dd;
        }
    }
    syl
}

/// Voice scaling of formants: F1 by `f1s`, F2 and F3 by `fs`.
#[derive(Clone, Copy)]
struct Scale {
    f1s: f64,
    fs: f64,
}

impl Scale {
    fn of(&self, f: Formants3) -> Formants3 {
        [f[0] * self.f1s, f[1] * self.fs, f[2] * self.fs]
    }

    /// Where the CV transition of stop or affricate `c` starts, before
    /// vowel `vf` (scaled). Velar: F1 250 Hz, F2 and F3 merged high (F2 the
    /// larger of 2500 Hz and 1.05 vowel F2, F3 100 Hz above it, at least
    /// 2550 Hz).
    fn locus(&self, c: &Consonant, vf: Formants3) -> Formants3 {
        match c.locus {
            Locus::At(loc) => self.of(loc),
            Locus::Velar => [
                250.0 * self.f1s,
                2500.0f64.max(vf[1] * 1.05),
                2550.0f64.max(vf[1] * 1.05 + 100.0),
            ],
        }
    }
}

/// Segment list under construction; drops spans shorter than 0.1 ms.
struct Plan<'a> {
    segs: &'a mut Vec<(Span, Segment)>,
}

impl Plan<'_> {
    fn put(&mut self, t0: f64, t1: f64, seg: Segment) {
        if t1 > t0 + MIN_SEGMENT {
            self.segs.push((Span { t0, t1 }, seg));
        }
    }
}

/// The timed segments of `notes` sung by voice `p` (settings already
/// applied), sorted by start time. See the module doc for the rules.
pub fn plan_segments(
    notes: &[VocalNote],
    p: &VoiceParams,
    settings: &VoiceSettings,
) -> Vec<(Span, Segment)> {
    let mut out = Vec::with_capacity(notes.len() * 8);
    plan_syllables(
        notes,
        &syllables(notes, p, &settings.phrasing),
        0..notes.len(),
        p,
        settings,
        &mut out,
    );
    out
}

/// The segments of notes `range` (the pause after the last one reaches
/// the next note's onset, which may lie outside `range`) into `out`,
/// cleared first. `syl` from `syllables` for all of `notes`.
pub fn plan_syllables(
    notes: &[VocalNote],
    syl: &[Syllable],
    range: Range<usize>,
    p: &VoiceParams,
    settings: &VoiceSettings,
    out: &mut Vec<(Span, Segment)>,
) {
    out.clear();
    let sc = Scale {
        f1s: p.f1s,
        fs: p.fs,
    };
    let mut plan = Plan { segs: out };
    let range = range.start.min(notes.len())..range.end.min(notes.len()).min(syl.len());

    for k in range {
        let (n, s) = (&notes[k], &syl[k]);
        let amp = n.amp as f64;
        let vt = &s.targets;
        let vf0 = sc.of(vt[0].f);
        let vfl = sc.of(vt[vt.len() - 1].f);
        let next = notes.get(k + 1).zip(syl.get(k + 1));
        // The next note continues this vowel (a melisma): no pause between.
        let next_legato = next.is_some_and(|(nn, _)| nn.legato);

        // Onset consonants.
        let mut t = s.onset_start;
        for ((&ph, &d), &e) in s.onset.iter().zip(&s.onset_dur).zip(&s.onset_extra) {
            let d = d * s.onset_scale + e;
            consonant_segments(&mut plan, &sc, ph, t, t + d, e, vf0, amp, false, !n.phrase_start);
            t += d;
        }
        let locus = s
            .onset
            .last()
            .and_then(|&ph| consonant(ph))
            .filter(|c| {
                matches!(
                    c.class,
                    ConsClass::Stop | ConsClass::Affricate | ConsClass::Fricative
                )
            })
            .map(|c| sc.locus(c, vf0));

        // Coda timing.
        let mut coda_end = n.t1;
        if let Some((_, ns)) = next {
            if ns.onset_start < n.t1 + 0.03 {
                coda_end = ns.onset_start.min((s.vowel_start + 0.06).max(n.t1));
            }
            if next_legato {
                coda_end = ns.onset_start.max(s.vowel_start);
            }
        }
        let d2: f64 = s.coda_dur.iter().sum();
        let w = s.rate_w;
        let lim = (coda_end - s.vowel_start) * (CODA_SHARE - FAST_CODA_CUT * w);
        let s2 = if d2 > lim && d2 > 0.0 { lim / d2 } else { 1.0 };
        // A voiceless coda stop's closure is held at `MIN_VL_CLOSURE` the
        // same way, within `PROTECT_CODA_SHARE` of the span; the time
        // comes from the vowel.
        let want: Vec<f64> = s
            .coda
            .iter()
            .zip(&s.coda_dur)
            .map(|(&q, &dq)| closure_deficit(q, p.cons_scale, dq * s2, true))
            .collect();
        let span = coda_end - s.vowel_start;
        let coda_cap = lim.max(span * PROTECT_CODA_SHARE);
        let coda_extra = grant(&want, coda_cap - d2 * s2);
        let coda_start = coda_end - d2 * s2 - coda_extra.iter().sum::<f64>();

        // CV transition from the locus.
        let vlen = coda_start - s.vowel_start;
        let mut n_start = s.vowel_start;
        if let (Some(loc), NucKind::Vowel) = (locus, vt[0].kind) {
            let velar = s
                .onset
                .last()
                .and_then(|&ph| consonant(ph))
                .is_some_and(|c| c.locus == Locus::Velar);
            let tt = if velar {
                VELAR_CV_TRANSITION - FAST_VELAR_CV_CUT * w
            } else {
                CV_TRANSITION - FAST_CV_CUT * w
            }
            .min(vlen * 0.4);
            for j in 0..CV_STEPS {
                let a = (j as f64 + 0.5) / CV_STEPS as f64;
                let e = 1.0 - (1.0 - a).powf(1.6);
                let f = [0, 1, 2].map(|q| loc[q] + (vf0[q] - loc[q]) * e);
                let t0 = s.vowel_start + tt * j as f64 / CV_STEPS as f64;
                let t1 = s.vowel_start + tt * (j as f64 + 1.0) / CV_STEPS as f64;
                plan.put(
                    t0,
                    t1,
                    Segment::Vowel {
                        f,
                        av: amp * (0.7 + 0.3 * a),
                    },
                );
            }
            n_start = s.vowel_start + tt;
        }

        // Nucleus: one target, or a hold and a glide through the rest.
        if vt.len() == 1 {
            plan.put(n_start, coda_start, vt[0].segment(sc.of(vt[0].f), amp));
        } else {
            let tail = clamp(vlen * 0.3, TAIL_MIN - FAST_TAIL_CUT * w, TAIL_MAX);
            let each = tail / (vt.len() - 1) as f64;
            plan.put(
                n_start,
                coda_start - tail,
                Segment::Vowel {
                    f: sc.of(vt[0].f),
                    av: amp,
                },
            );
            let pre_l = s.coda.first() == Some(&Phoneme::L);
            for (j, v) in vt.iter().enumerate().skip(1) {
                let a = coda_start - tail + (j - 1) as f64 * each;
                let mut f = sc.of(v.f);
                if pre_l {
                    f = [0, 1, 2].map(|q| vf0[q] + (f[q] - vf0[q]) * PRE_L_GLIDE);
                }
                plan.put(a, a + each, v.segment(f, amp));
            }
        }

        // VC transition: the last of the vowel moves toward the locus of a
        // coda stop, affricate or fricative (the mirror of the CV
        // transition), so a final consonant is heard in the vowel too.
        let vc = s.coda.first().and_then(|&ph| consonant(ph)).filter(|c| {
            matches!(
                c.class,
                ConsClass::Stop | ConsClass::Affricate | ConsClass::Fricative
            )
        });
        if let (Some(c), Some(NucKind::Vowel)) = (vc, vt.last().map(|v| v.kind)) {
            let loc = sc.locus(c, vfl);
            let tt = (VC_TRANSITION - FAST_VC_CUT * w).min(vlen * VC_SHARE);
            let t_a = coda_start - tt;
            for j in 0..CV_STEPS {
                let a = (j as f64 + 0.5) / CV_STEPS as f64;
                let e = a.powf(1.6);
                let f = [0, 1, 2].map(|q| vfl[q] + (loc[q] - vfl[q]) * e * VC_DEPTH);
                let t0 = t_a + tt * j as f64 / CV_STEPS as f64;
                let t1 = t_a + tt * (j as f64 + 1.0) / CV_STEPS as f64;
                plan.put(t0, t1, Segment::Vowel { f, av: amp });
            }
        }

        // VL transition: a linear move from the vowel to a coda /l/'s target.
        if let (Some(c), Some(NucKind::Vowel)) = (
            s.coda
                .first()
                .filter(|&&ph| ph == Phoneme::L)
                .and_then(|&ph| consonant(ph)),
            vt.last().map(|v| v.kind),
        ) {
            let lt = sc.of(c.formants);
            let end = [0, 1, 2].map(|q| lt[q] * (1.0 - CODA_L_PULL) + vfl[q] * CODA_L_PULL);
            let tt = VL_TRANSITION.min(vlen * VC_SHARE);
            let t_a = coda_start - tt;
            for j in 0..CV_STEPS {
                let a = (j as f64 + 1.0) / CV_STEPS as f64;
                let f = [0, 1, 2].map(|q| vfl[q] + (end[q] - vfl[q]) * a);
                let t0 = t_a + tt * j as f64 / CV_STEPS as f64;
                let t1 = t_a + tt * (j as f64 + 1.0) / CV_STEPS as f64;
                plan.put(t0, t1, Segment::Vowel { f, av: amp });
            }
        }

        // Coda consonants.
        t = coda_start;
        for ((&ph, &d), &e) in s.coda.iter().zip(&s.coda_dur).zip(&coda_extra) {
            let d = d * s2 + e;
            consonant_segments(
                &mut plan,
                &sc,
                ph,
                t,
                t + d,
                e,
                vfl,
                amp,
                true,
                next.is_some_and(|(nn, _)| !nn.phrase_start),
            );
            t += d;
        }

        // Pause until the next onset: silence, or a breath before a phrase.
        let next_on = next.map_or(coda_end + 0.5, |(_, ns)| ns.onset_start);
        if next_on > coda_end + 0.01 && !next_legato {
            let nf = next.map_or(vfl, |(_, ns)| sc.of(ns.targets[0].f));
            let phrase = next.is_some_and(|(nn, _)| nn.phrase_start);
            let hold = next.is_some() && !phrase;
            if phrase && next_on - coda_end > BREATH_PAUSE && settings.breath_pauses {
                plan.put(coda_end, next_on - BREATH_START, Segment::Silence { f: nf, hold });
                plan.put(
                    next_on - BREATH_START,
                    next_on - BREATH_END,
                    Segment::Breath {
                        f: sc.of(BREATH_FORMANTS),
                    },
                );
                plan.put(next_on - BREATH_END, next_on, Segment::Silence { f: nf, hold });
            } else {
                plan.put(coda_end, next_on, Segment::Silence { f: nf, hold });
            }
        }
    }

    // Stable: equal starts keep emission order.
    plan.segs.sort_by(|a, b| a.0.t0.total_cmp(&b.0.t0));
}

/// Segments of consonant `ph` over t0..t1 next to vowel `vf` (scaled).
/// `extra` seconds of the span (after compression) belong to the closure
/// of a voiceless stop (`closure_extra`).
#[allow(clippy::too_many_arguments)]
fn consonant_segments(
    plan: &mut Plan,
    sc: &Scale,
    ph: Phoneme,
    t0: f64,
    t1: f64,
    extra: f64,
    vf: Formants3,
    amp: f64,
    coda: bool,
    hold: bool,
) {
    let Some(c) = consonant(ph) else {
        return;
    };
    match c.class {
        ConsClass::Sonorant | ConsClass::Nasal => {
            let t = sc.of(c.formants);
            let coda_l = coda && ph == Phoneme::L;
            let pull = if ph == Phoneme::R {
                R_PULL
            } else if coda_l {
                CODA_L_PULL
            } else {
                SONORANT_PULL
            };
            let f = [0, 1, 2].map(|i| t[i] * (1.0 - pull) + vf[i] * pull);
            let av = amp * if coda_l { CODA_L_AV } else { c.av };
            let seg = if c.class == ConsClass::Nasal {
                Segment::Nasal { f, av, zero: c.fnz }
            } else {
                Segment::Sonorant { f, av }
            };
            plan.put(t0, t1, seg);
        }
        ConsClass::Fricative => {
            let av = if c.voiced { amp * c.vv } else { 0.0 };
            let loc = sc.locus(c, vf);
            plan.put(
                t0,
                t1,
                Segment::Fricative {
                    f: [0, 1, 2].map(|i| loc[i] * 0.6 + vf[i] * 0.4),
                    av,
                    af: c.af * amp,
                    ff: c.ff,
                    fbw: c.bw,
                },
            );
        }
        ConsClass::Aspirate => plan.put(
            t0,
            t1,
            Segment::Aspiration {
                f: vf,
                ah: 0.5 * amp,
                b1x: 0.0,
            },
        ),
        ConsClass::Stop | ConsClass::Affricate => {
            let loc = sc.locus(c, vf);
            let cf = [0, 1, 2].map(|i| loc[i] * 0.6 + vf[i] * 0.4);
            // Closure and (affricate) burst keep their nominal share of the
            // consonant after voice scaling and compression.
            let k = (t1 - t0 - extra) / cons_dur(ph, coda);
            let cl = t0 + c.closure * k + extra;
            // A voiced closure (not a flap) is a voice bar: F1 at the
            // closed tract's resonance, the signal low-passed.
            let bar = c.voiced && !c.flap;
            let mut cc = cf;
            if bar {
                cc[0] = cc[0].min(CLOSED_F1_HZ);
            }
            let av = if c.voiced { amp * CLOSURE_VOICING } else { 0.0 };
            if hold && !c.voiced {
                // Within a line a voiceless closure keeps the tract alive and
                // leaks a little low-passed voicing at its start.
                let le = (t0 + LEAK_TIME).min(cl);
                let leak = amp * CLOSURE_LEAK;
                plan.put(t0, le, Segment::Closure { f: cc, av: leak, bar: true, hold });
                plan.put(le, cl, Segment::Closure { f: cc, av: 0.0, bar: false, hold });
            } else {
                plan.put(t0, cl, Segment::Closure { f: cc, av, bar, hold });
            }
            if c.class == ConsClass::Stop {
                let ff = match c.locus {
                    Locus::At(_) => c.ff,
                    Locus::Velar => VELAR_BURST_HZ,
                };
                let af = if coda { 0.4 } else { 0.7 } * amp * BURST_GAIN;
                plan.put(
                    cl,
                    cl + STOP_BURST,
                    Segment::Burst {
                        f: cf,
                        af,
                        ff,
                        fbw: c.bw,
                        ah: 0.12 * amp,
                    },
                );
                if !coda && !c.voiced {
                    plan.put(
                        cl + STOP_BURST,
                        t1,
                        Segment::Aspiration {
                            f: vf,
                            ah: 0.4 * amp,
                            b1x: 320.0,
                        },
                    );
                }
            } else {
                let b1 = cl + AFFRICATE_BURST * k;
                plan.put(
                    cl,
                    b1,
                    Segment::Burst {
                        f: cf,
                        af: 0.6 * amp,
                        ff: c.ff,
                        fbw: c.bw,
                        ah: 0.0,
                    },
                );
                let av = if c.voiced { amp * 0.3 } else { 0.0 };
                plan.put(
                    b1,
                    t1,
                    Segment::Fricative {
                        f: vf,
                        av,
                        af: c.af * amp,
                        ff: c.ff,
                        fbw: c.bw,
                    },
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::params::voice_params;
    use song::Voice;

    fn s(v: &str) -> Phoneme {
        Phoneme::from_symbol(v).expect("known symbol")
    }
    fn ph(v: &[&str]) -> Vec<Phoneme> {
        v.iter().map(|x| s(x)).collect()
    }
    fn note(t0: f64, t1: f64, phones: &[&str], stress: bool) -> VocalNote {
        VocalNote {
            t0,
            t1,
            midi: 52.0,
            phones: ph(phones),
            amp: 1.0,
            stress,
            phrase_start: false,
            phrase_end: false,
            grace: None,
            legato: false,
            expr: Default::default(),
        }
    }

    #[test]
    fn split_basic_cvc() {
        let (on, nu, co) = split_ph(&ph(&["k", "ae", "t"]));
        assert_eq!((on, nu, co), (ph(&["k"]), ph(&["ae"]), ph(&["t"])));
    }

    #[test]
    fn split_without_vowel_sings_aa() {
        let (on, nu, co) = split_ph(&ph(&["s", "t"]));
        assert!(on.is_empty());
        assert_eq!(nu, vec![Phoneme::Aa]);
        assert_eq!(co, ph(&["s", "t"]));
        let (_, nu, _) = split_ph(&[]);
        assert_eq!(nu, vec![Phoneme::Aa]);
    }

    #[test]
    fn split_vowel_run_and_cluster() {
        let (on, nu, co) = split_ph(&ph(&["s", "t", "aa", "ih", "n"]));
        assert_eq!(
            (on, nu, co),
            (ph(&["s", "t"]), ph(&["aa", "ih"]), ph(&["n"]))
        );
    }

    #[test]
    fn cons_dur_rules() {
        assert!((cons_dur(Phoneme::T, false) - (0.045 + 0.012 + 0.024)).abs() < 1e-12);
        assert!((cons_dur(Phoneme::T, true) - (0.045 + 0.012)).abs() < 1e-12);
        assert!((cons_dur(Phoneme::D, false) - (0.05 + 0.012)).abs() < 1e-12);
        assert!((cons_dur(Phoneme::Ch, false) - (0.04 + 0.008 + 0.07)).abs() < 1e-12);
        assert_eq!(cons_dur(Phoneme::N, true), 0.085);
        assert_ne!(cons_dur(Phoneme::N, false), 0.085);
        assert_eq!(cons_dur(Phoneme::Aa, false), 0.0);
    }

    #[test]
    fn nucleus_targets() {
        let t = nuc_targets(&ph(&["ae"]));
        assert_eq!(t.len(), 1);
        assert_eq!(t[0].kind, NucKind::Vowel);
        let t = nuc_targets(&ph(&["ay"]));
        assert_eq!(t.len(), 2);
        assert!(t.iter().all(|x| x.kind == NucKind::Vowel));
        let t = nuc_targets(&ph(&["ey"]));
        assert_eq!(
            t.iter().map(|x| x.f).collect::<Vec<_>>(),
            vec![[450.0, 2020.0, 2600.0], [340.0, 2210.0, 2780.0]]
        );
        let t = nuc_targets(&[]);
        assert_eq!(Some(t[0].f), vowel_formants(Phoneme::Aa));
    }

    /// Spans of the segments inside [t0, t1).
    fn inside(plan: &[(Span, Segment)], t0: f64, t1: f64) -> Vec<(Span, Segment)> {
        plan.iter()
            .filter(|(sp, _)| sp.t0 >= t0 - 1e-9 && sp.t0 < t1)
            .cloned()
            .collect()
    }

    /// A legato (melisma continuation) note after a gap: no pause between
    /// the notes, no onset segments, and the vowel runs to its onset; the
    /// same notes not marked legato get the pause.
    #[test]
    fn legato_continuation_has_no_pause_or_onset() {
        let settings = VoiceSettings::default();
        let p = voice_params(Voice::Baritone);
        let mut notes = [
            note(0.5, 0.9, &["s", "ay"], true),
            note(1.0, 1.4, &["aa"], false),
        ];
        let is_silence = |g: &Segment| matches!(g, Segment::Silence { .. });
        let plan = plan_segments(&notes, &p, &settings);
        assert!(inside(&plan, 0.9, 1.0).iter().any(|(_, g)| is_silence(g)));
        notes[1].legato = true;
        let plan = plan_segments(&notes, &p, &settings);
        assert!(!plan
            .iter()
            .any(|(sp, g)| is_silence(g) && sp.t0 < 1.4 && sp.t0 >= 0.5));
        // The first note's vowel reaches the continuation's onset.
        let reach = plan
            .iter()
            .filter(|(sp, g)| matches!(g, Segment::Vowel { .. }) && sp.t0 < 1.0)
            .map(|(sp, _)| sp.t1)
            .fold(0.0, f64::max);
        assert!((reach - 1.0).abs() < 1e-9, "vowel reaches {reach}");
        // Nothing but vowel segments starts inside the continuation.
        assert!(inside(&plan, 1.0, 1.4)
            .iter()
            .all(|(_, g)| matches!(g, Segment::Vowel { .. })));
    }

    /// "the RIV-er ci-ty": the unstressed /t/ of "ty" after an open "ci"
    /// flaps to a 20 ms voiced closure, unscaled, with a burst and no
    /// aspiration.
    #[test]
    fn unstressed_intervocalic_t_flaps() {
        let settings = VoiceSettings::default();
        for voice in [Voice::Bass, Voice::Baritone, Voice::Soprano] {
            let p = voice_params(voice);
            let notes = [
                note(0.5, 0.9, &["dh", "ax"], false),
                note(1.0, 1.4, &["r", "ih"], true),
                note(1.5, 1.9, &["v", "er"], false),
                note(2.0, 2.4, &["s", "ih"], true),
                note(2.5, 2.9, &["t", "iy"], false),
            ];
            let syl = syllables(&notes, &p, &PhrasingParams::default());
            assert_eq!(syl[4].onset, vec![Phoneme::Dx], "{voice:?}");
            let plan = plan_segments(&notes, &p, &settings);
            let on = inside(&plan, syl[4].onset_start, notes[4].t0);
            let clos: Vec<_> = on
                .iter()
                .filter(|(_, g)| matches!(g, Segment::Closure { .. }))
                .collect();
            assert_eq!(clos.len(), 1);
            assert!(
                (clos[0].0.len() - 0.02).abs() < 1e-9,
                "{voice:?} closure {}",
                clos[0].0.len()
            );
            assert!(
                matches!(clos[0].1, Segment::Closure { av, .. } if av > 0.0),
                "flap closure is voiced"
            );
            assert!(on.iter().any(|(_, g)| matches!(g, Segment::Burst { .. })));
            assert!(!on
                .iter()
                .any(|(_, g)| matches!(g, Segment::Aspiration { .. })));

            // Stressed, the same /t/ is a full voiceless stop.
            let mut stressed = notes.clone();
            stressed[4].stress = true;
            let syl = syllables(&stressed, &p, &PhrasingParams::default());
            assert_eq!(syl[4].onset, vec![Phoneme::T]);
            assert_eq!(syl[4].onset_scale, 1.0);
            let plan = plan_segments(&stressed, &p, &settings);
            let on = inside(&plan, syl[4].onset_start, stressed[4].t0);
            let cs = p.cons_scale;
            let len = |pred: fn(&Segment) -> bool| -> f64 {
                on.iter()
                    .filter(|(_, g)| pred(g))
                    .map(|(sp, _)| sp.len())
                    .sum()
            };
            let clos = len(|g| matches!(g, Segment::Closure { .. }));
            let burst = len(|g| matches!(g, Segment::Burst { .. }));
            let asp = len(|g| matches!(g, Segment::Aspiration { b1x, .. } if *b1x > 0.0));
            assert!((clos - 0.045 * cs).abs() < 1e-9, "{voice:?} closure {clos}");
            assert!((burst - STOP_BURST).abs() < 1e-9, "{voice:?} burst {burst}");
            let want = (0.045 + STOP_BURST + STOP_ASPIRATION) * cs - 0.045 * cs - STOP_BURST;
            assert!(
                (asp - want).abs() < 1e-9,
                "{voice:?} aspiration {asp} want {want}"
            );
            // The CV transition follows the release.
            let cv = inside(&plan, stressed[4].t0, stressed[4].t0 + CV_TRANSITION);
            assert_eq!(
                cv.iter()
                    .filter(|(_, g)| matches!(g, Segment::Vowel { .. }))
                    .count(),
                CV_STEPS
            );
        }
    }

    /// A /t/ after a coda consonant, or across a long gap, does not flap.
    #[test]
    fn t_after_coda_or_gap_does_not_flap() {
        let p = voice_params(Voice::Baritone);
        let notes = [
            note(0.5, 0.9, &["s", "ih", "n"], true),
            note(1.0, 1.4, &["t", "iy"], false),
        ];
        assert_eq!(
            syllables(&notes, &p, &PhrasingParams::default())[1].onset,
            vec![Phoneme::T]
        );
        let notes = [
            note(0.5, 0.9, &["s", "ih"], true),
            note(1.2, 1.6, &["t", "iy"], false),
        ];
        assert_eq!(
            syllables(&notes, &p, &PhrasingParams::default())[1].onset,
            vec![Phoneme::T]
        );
    }

    /// On a fast note a voiceless stop keeps a closure of `MIN_VL_CLOSURE`
    /// when the onset share has room; at rate weight 0 nothing changes.
    #[test]
    fn fast_voiceless_closure_keeps_its_floor() {
        let settings = VoiceSettings::default();
        let p = settings.apply(voice_params(Voice::Baritone));
        let closure_of = |gap: f64| {
            let notes = [
                note(0.5, 0.5 + gap, &["aa"], true),
                note(0.5 + gap, 0.5 + 2.0 * gap, &["t", "aa"], true),
            ];
            let plan = plan_segments(&notes, &p, &settings);
            // Within a line the closure is the leak span plus the rest.
            plan.iter()
                .filter(|(sp, g)| matches!(g, Segment::Closure { .. }) && sp.t0 > 0.5)
                .map(|(sp, _)| sp.len())
                .sum::<f64>()
        };
        // Interval 0.25 s: rate weight 0.7; the cut closure is 40.8 ms.
        assert!((closure_of(0.25) - MIN_VL_CLOSURE).abs() < 1e-9);
        // Interval 0.5 s: weight 0, the table closure times the scale.
        assert!((closure_of(0.5) - 0.045 * p.cons_scale).abs() < 1e-9);
    }

    /// Onsets take at most 45% of the inter-onset interval on slow notes,
    /// 28% on fast ones (a 120 ms interval has rate weight 1); an onset
    /// with a voiceless stop may take 40% when its closure needs the time.
    #[test]
    fn onset_compression() {
        let p = voice_params(Voice::Soprano);
        let share = PhrasingParams::default().onset_share;
        for (gap, want, onset) in [
            (0.12, share - FAST_ONSET_CUT, ["s", "z", "r", "aa"]),
            (0.12, PROTECT_ONSET_SHARE, ["s", "t", "r", "aa"]),
            (0.40, share, ["s", "t", "r", "aa"]),
        ] {
            let notes = [
                note(0.5, 0.6, &["aa"], true),
                note(0.5 + gap, 0.6 + gap, &onset, true),
            ];
            let syl = syllables(&notes, &p, &PhrasingParams::default());
            let used = notes[1].t0 - syl[1].onset_start;
            assert!(syl[1].onset_scale < 1.0);
            assert!((used - gap * want).abs() < 1e-12, "{gap}: {used}");
        }
    }

    /// Notes "t ae n" every `ioi` s, each lasting to the next onset.
    fn tan_run(ioi: f64) -> Vec<VocalNote> {
        (0..6)
            .map(|k| {
                let t0 = 1.0 + k as f64 * ioi;
                note(t0, t0 + ioi, &["t", "ae", "n"], k % 2 == 0)
            })
            .collect()
    }

    /// The rate weight is 0 from 0.32 s, 1 up to 0.22 s, and continuous.
    #[test]
    fn rate_weight_is_continuous() {
        assert_eq!(rate_weight(0.5), 0.0);
        assert_eq!(rate_weight(RATE_SLOW), 0.0);
        assert_eq!(rate_weight(0.1), 1.0);
        assert!((rate_weight(RATE_SLOW - RATE_RAMP) - 1.0).abs() < 1e-12);
        for t in [RATE_SLOW, RATE_SLOW - RATE_RAMP] {
            for e in [1e-7, -1e-7] {
                assert!(
                    (rate_weight(t + e) - rate_weight(t)).abs() < 2e-6,
                    "{t} {e}"
                );
            }
        }
        // The plan follows: just under 0.32 s it is the slow plan, closely.
        let p = voice_params(Voice::Baritone);
        let st = VoiceSettings::default();
        let a = plan_segments(&tan_run(RATE_SLOW), &p, &st);
        let b = plan_segments(&tan_run(RATE_SLOW - 1e-7), &p, &st);
        assert_eq!(a.len(), b.len());
        for (x, y) in a.iter().zip(&b) {
            assert!((x.0.t0 - y.0.t0).abs() < 1e-5 && (x.0.t1 - y.0.t1).abs() < 1e-5);
        }
    }

    /// At a 0.5 s interval the plan is the plan with every weight 0.
    #[test]
    fn slow_notes_plan_as_weight_zero() {
        let st = VoiceSettings::default();
        for voice in [Voice::Bass, Voice::Baritone, Voice::Soprano] {
            let p = voice_params(voice);
            let mut notes = tan_run(0.5);
            notes[3].phones = ph(&["k", "ay", "t", "s"]);
            notes[4].phones = ph(&["s", "t", "r", "ow", "l", "d"]);
            let mut a = Vec::new();
            let mut b = Vec::new();
            let s1 = syllables(&notes, &p, &st.phrasing);
            let s0 = syllables_by(&notes, &p, &st.phrasing, |_| 0.0);
            plan_syllables(&notes, &s1, 0..notes.len(), &p, &st, &mut a);
            plan_syllables(&notes, &s0, 0..notes.len(), &p, &st, &mut b);
            assert_eq!(a, b, "{voice:?}");
        }
    }

    /// At a 0.2 s interval a stop-onset, nasal-coda syllable keeps its
    /// onset and coda within the fast shares and the vowel at least 55% of
    /// the interval (half, with the closure floor).
    #[test]
    fn fast_notes_keep_the_vowel() {
        let st = VoiceSettings::default();
        for voice in [
            Voice::Bass,
            Voice::Baritone,
            Voice::Tenor,
            Voice::Alto,
            Voice::Soprano,
        ] {
            let p = voice_params(voice);
            let notes = tan_run(0.2);
            let syl = syllables(&notes, &p, &st.phrasing);
            let plan = plan_segments(&notes, &p, &st);
            for k in 1..notes.len() - 1 {
                let (t0, t1) = (notes[k].t0, notes[k + 1].t0);
                assert_eq!(syl[k].rate_w, 1.0);
                let onset = notes[k].t0 - syl[k].onset_start;
                let on_share = st.phrasing.onset_share - FAST_ONSET_CUT;
                let on_cap = on_share.max(PROTECT_ONSET_SHARE);
                assert!(onset <= 0.2 * on_cap + 1e-12, "{voice:?} onset {onset}");
                let coda: f64 = plan
                    .iter()
                    .filter(|(sp, g)| {
                        matches!(g, Segment::Nasal { .. }) && sp.t0 >= t0 && sp.t0 < t1
                    })
                    .map(|(sp, _)| sp.len())
                    .sum();
                let coda_room = syl[k + 1].onset_start - syl[k].vowel_start;
                assert!(
                    coda > 0.0
                        && coda
                            <= coda_room * (CODA_SHARE - FAST_CODA_CUT).max(PROTECT_CODA_SHARE)
                                + 1e-12,
                    "{voice:?} coda {coda}"
                );
                let cons: f64 = plan
                    .iter()
                    .filter(|(_, g)| !matches!(g, Segment::Vowel { .. }))
                    .map(|(sp, _)| (sp.t1.min(t1) - sp.t0.max(t0)).max(0.0))
                    .sum();
                let vowel = (t1 - t0) - cons;
                // 55% before the closure floor; its raised onset and coda caps
                // (0.40 and 0.30) leave at least half the interval.
                assert!(vowel >= 0.50 * 0.2, "{voice:?} note {k} vowel {vowel}");
            }
        }
    }

    /// `lead_in` moves the vowel start by `(1 - lead_in) * D`, where `D` is
    /// the onset consonants' scaled span (uncompressed here); `onset_start`
    /// moves the other way, by `lead_in * D`, so the span keeps length `D`.
    #[test]
    fn lead_in_moves_the_vowel_start() {
        let p = voice_params(Voice::Tenor);
        let notes = [
            note(1.0, 1.6, &["s", "t", "aa"], true),
            note(2.0, 2.6, &["b", "aa"], true),
        ];
        let default = PhrasingParams::default();
        let syl0 = syllables(&notes, &p, &default);
        assert_eq!(syl0[0].vowel_start, notes[0].t0);
        let d: f64 = syl0[0].onset_dur.iter().sum::<f64>() * syl0[0].onset_scale;

        for lead_in in [0.6, 0.0, 1.0] {
            let ph = PhrasingParams { lead_in, ..default };
            let syl = syllables(&notes, &p, &ph);
            let dd: f64 = syl[0].onset_dur.iter().sum::<f64>() * syl[0].onset_scale;
            assert!(
                (dd - d).abs() < 1e-9,
                "onset compression must not depend on lead_in"
            );
            assert!(
                (syl[0].vowel_start - (notes[0].t0 + (1.0 - lead_in) * dd)).abs() < 1e-9,
                "lead_in {lead_in}"
            );
            assert!(
                (syl[0].onset_start - (notes[0].t0 - lead_in * dd)).abs() < 1e-9,
                "lead_in {lead_in}"
            );
        }
    }

    /// A breath precedes a phrase after a long pause, only when enabled.
    #[test]
    fn breath_before_phrase() {
        let p = voice_params(Voice::Baritone);
        let mut b = note(3.0, 3.5, &["aa"], true);
        b.phrase_start = true;
        let notes = [note(0.5, 1.0, &["aa"], true), b];
        let on = plan_segments(&notes, &p, &VoiceSettings::default());
        assert!(on.iter().any(|(sp, g)| matches!(g, Segment::Breath { .. })
            && (sp.t1 - (3.0 - BREATH_END)).abs() < 1e-9));
        let off = plan_segments(
            &notes,
            &p,
            &VoiceSettings {
                breath_pauses: false,
                ..VoiceSettings::default()
            },
        );
        assert!(!off.iter().any(|(_, g)| matches!(g, Segment::Breath { .. })));
    }

    /// Segments are sorted, finite and non-empty for odd input.
    #[test]
    fn plan_is_sorted_and_finite() {
        let p = voice_params(Voice::Alto);
        let notes = [
            note(0.0, 0.05, &["s", "t", "r", "ng", "k", "s"], false),
            note(0.05, 0.06, &[], false),
            note(0.06, 2.0, &["ch", "ay", "l", "d", "z"], true),
            note(2.0, 2.0, &["jh", "ey", "m"], false),
        ];
        let plan = plan_segments(&notes, &p, &VoiceSettings::default());
        assert!(!plan.is_empty());
        assert!(plan.windows(2).all(|w| w[0].0.t0 <= w[1].0.t0));
        for (sp, g) in &plan {
            assert!(sp.t0.is_finite() && sp.t1 > sp.t0);
            assert!(g.formants().iter().all(|f| f.is_finite() && *f > 0.0));
        }
    }
}
