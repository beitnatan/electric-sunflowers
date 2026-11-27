//! Vocal tract: a cascade formant synthesiser (D. H. Klatt, "Software for a
//! cascade/parallel formant synthesizer", JASA 67(3), 1980; D. H. Klatt and
//! L. C. Klatt, JASA 87(2), 1990).
//!
//! Signal path, all `f64`:
//! - Cascade of Klatt unity-DC two-pole resonators (dsp::resonator): F1-F3
//!   follow the articulation (targets per HOP frame, coefficients ramped
//!   linearly per sample, design 3.4); F4, F5 and up to four high
//!   resonances at 5.5, 6.6, 7.7, 8.8 kHz times the voice's formant scale
//!   are fixed per voice and designed once.
//! - Nasal pole-zero pair (Klatt 1980, RNP and RNZ) ahead of F1: a pole at
//!   270 Hz (bandwidth 100 Hz) and a zero, the FIR (1/a, -b/a, -c/a) of the
//!   Klatt resonator of the same centre and bandwidth. With the zero at
//!   the pole's centre and bandwidth the pair cancels; in a nasal the zero
//!   moves to the nasal's antiresonance. The zero's coefficients ramp per
//!   sample like F1-F3.
//! - RBJ high shelf, `shelf_db` (16 dB for tenor, alto and soprano) at
//!   5.2 kHz, Q 0.7, after the cascade.
//! - Frication: white noise through an RBJ constant-peak band-pass at the
//!   consonant's centre and bandwidth (Q = max(0.5, ff / bw)), added after
//!   the tract with gain 2.2. The band-pass designer clamps the centre at
//!   0.45 fs; that is the only clamp.
//! - DC blocker, pole 0.995.
//!
//! Sound-setting values: F4 = (3350 - 350 sf) fs, F5 = (3950 - 500 sf) fs,
//! B4 = 250 - 110 sf, B5 = 300 - 120 sf (sf = singer's-formant level, fs =
//! formant scale); high resonance bandwidths 420 + 0.05 f; F1-F3 bandwidths
//! set by the synth from breath and nasality.
//!
//! A parallel high-frequency branch was tried and rejected: it filled the
//! spectral valleys (/uw/ from -55..-82 dB to -41 dB) and cut vowel
//! distinctness from 13.6 to 10.6 dB.

use dsp::biquad::{Biquad, BiquadCoeffs};
use dsp::onepole::DcBlocker;
use dsp::resonator::{coeffs, Resonator};
use sfcore::{HOP, SR_F};

use crate::params::VoiceParams;
use crate::tuning::{
    FRICATION_GAIN, NASAL_POLE_BW_HZ, NASAL_POLE_HZ, NASAL_ZERO_BW_HZ, SHELF_HZ, SHELF_Q,
    VOICE_BAR_HZ, VOICE_BAR_Q,
};

/// Most high resonances above F5.
pub const MAX_HIGH: usize = 4;
/// Centres of the high resonances at formant scale 1, Hz.
const HIGH_HZ: [f64; MAX_HIGH] = [5500.0, 6600.0, 7700.0, 8800.0];
/// DC blocker pole.
pub const DC_POLE: f64 = 0.995;
/// Highest resonator centre as a fraction of the sample rate.
const MAX_CENTRE: f64 = 0.45;

/// Klatt coefficients with the centre clamped to 0.45 fs.
fn design(f: f64, bw: f64) -> (f64, f64, f64) {
    coeffs(f.min(SR_F * MAX_CENTRE), bw, SR_F)
}

/// Centre and bandwidth (Hz) of the three moving formants.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Formants {
    pub f: [f64; 3],
    pub bw: [f64; 3],
}

/// Number of fixed sections: F4, F5 and `MAX_HIGH` high resonances.
/// Unused high resonances are identity sections (a = 1, b = c = 0).
const N_FIXED: usize = 2 + MAX_HIGH;

/// Coefficients (A, B, C) of the nasal zero at centre `f` and bandwidth
/// `bw`: y = A x + B x1 + C x2, the inverse of the Klatt resonator.
fn zero_coeffs(f: f64, bw: f64) -> [f64; 3] {
    let (a, b, c) = design(f, bw);
    [1.0 / a, -b / a, -c / a]
}

/// The tract filters and their state.
pub struct Tract {
    /// F1-F3. Coefficients ramp per sample toward `target` by `step`
    /// for `left` more samples (the rule of Resonator::set_ramped, run
    /// here inside the block loop).
    moving: [Resonator; 3],
    step: [[f64; 3]; 3],
    target: [[f64; 3]; 3],
    left: u32,
    fixed: [Resonator; N_FIXED],
    /// Nasal pole (fixed), and the nasal zero: coefficients, ramp step,
    /// target, samples left, last two inputs.
    np: Resonator,
    nz: [f64; 3],
    nz_step: [f64; 3],
    nz_target: [f64; 3],
    nz_left: u32,
    nz_x: [f64; 2],
    /// Voice bar low-pass.
    bar: Biquad,
    /// Index of the last non-identity fixed section.
    last_fixed: usize,
    shelf: Biquad,
    fric: Biquad,
    /// Last two frication outputs, for the idle test.
    fric_y: [f64; 2],
    dc: DcBlocker,
    /// True after a reset: the next formant update jumps instead of ramping.
    fresh: bool,
    /// Frication output gain: `FRICATION_GAIN` times the voice's `cons_gain`.
    fric_gain: f64,
}

impl Tract {
    /// The tract of voice `p` with `n_high` (0-4) high resonances.
    pub fn new(p: &VoiceParams, n_high: usize) -> Tract {
        let (sf, fs) = (p.sf, p.fs);
        let n_high = n_high.min(MAX_HIGH);
        let mut fixed = [Resonator::default(); N_FIXED];
        for r in &mut fixed {
            r.set_coeffs((1.0, 0.0, 0.0));
        }
        fixed[0].set_coeffs(design((3350.0 - sf * 350.0) * fs, 250.0 - sf * 110.0));
        fixed[1].set_coeffs(design((3950.0 - sf * 500.0) * fs, 300.0 - sf * 120.0));
        for h in 0..n_high {
            let f = HIGH_HZ[h] * fs;
            fixed[2 + h].set_coeffs(design(f, (420.0 + 0.05 * f) * p.high_bw));
        }
        let mut np = Resonator::default();
        np.set_coeffs(design(NASAL_POLE_HZ, NASAL_POLE_BW_HZ));
        let rest = zero_coeffs(NASAL_POLE_HZ, NASAL_POLE_BW_HZ);
        Tract {
            np,
            nz: rest,
            nz_step: [0.0; 3],
            nz_target: rest,
            nz_left: 0,
            nz_x: [0.0; 2],
            bar: Biquad::new(BiquadCoeffs::lowpass(SR_F, VOICE_BAR_HZ, VOICE_BAR_Q)),
            moving: [Resonator::default(); 3],
            step: [[0.0; 3]; 3],
            target: [[0.0; 3]; 3],
            left: 0,
            fixed,
            last_fixed: 1 + n_high,
            shelf: Biquad::new(BiquadCoeffs::high_shelf(
                SR_F, SHELF_HZ, SHELF_Q, p.shelf_db,
            )),
            fric: Biquad::default(),
            fric_y: [0.0; 2],
            dc: DcBlocker::new(DC_POLE),
            fresh: true,
            fric_gain: FRICATION_GAIN * p.cons_gain,
        }
    }

    /// True after a reset, until `jump_formants` or `set_formants`: the
    /// F1-F3 coefficients are stale and must be set, not ramped from.
    pub fn is_fresh(&self) -> bool {
        self.fresh
    }

    /// Take F1-F3 coefficients for `now` at once (after a reset).
    pub fn jump_formants(&mut self, now: &Formants) {
        for q in 0..3 {
            self.moving[q].set_coeffs(design(now.f[q], now.bw[q]));
        }
        self.left = 0;
        self.fresh = false;
    }

    /// Take the nasal zero for `now` (centre Hz, bandwidth Hz) at once.
    pub fn jump_nasal_zero(&mut self, now: (f64, f64)) {
        self.nz = zero_coeffs(now.0, now.1);
        self.nz_target = self.nz;
        self.nz_left = 0;
    }

    /// Ramp the nasal zero to `next` (centre Hz, bandwidth Hz) over the
    /// next HOP samples.
    pub fn ramp_nasal_zero(&mut self, next: (f64, f64)) {
        let t = zero_coeffs(next.0, next.1);
        let k = 1.0 / HOP as f64;
        for ((st, &tq), &nq) in self.nz_step.iter_mut().zip(&t).zip(&self.nz) {
            *st = (tq - nq) * k;
        }
        self.nz_target = t;
        self.nz_left = HOP as u32;
    }

    /// Centre and bandwidth of the nasal zero at nasality `nas` (0-1) and
    /// antiresonance `fnz`: the pole's own at 0 (the pair cancels).
    pub fn nasal_zero_at(nas: f64, fnz: f64) -> (f64, f64) {
        (
            NASAL_POLE_HZ + nas * (fnz - NASAL_POLE_HZ),
            NASAL_POLE_BW_HZ + nas * (NASAL_ZERO_BW_HZ - NASAL_POLE_BW_HZ),
        )
    }

    /// Ramp F1-F3 to `next` over the next HOP samples: sample k of the
    /// ramp uses start + k (target - start) / HOP, the HOP-th the target
    /// exactly (as Resonator::set_ramped). The ramp is linear in (a, b,
    /// c); the stable triangle |c| < 1, |b| < 1 - c is convex, so every
    /// intermediate filter is stable, and a = 1 - b - c keeps unity DC gain
    /// along the path (design 3.4).
    pub fn ramp_formants(&mut self, next: &Formants) {
        let k = 1.0 / HOP as f64;
        for q in 0..3 {
            let r = &self.moving[q];
            let (a, b, c) = design(next.f[q], next.bw[q]);
            self.target[q] = [a, b, c];
            self.step[q] = [(a - r.a) * k, (b - r.b) * k, (c - r.c) * k];
        }
        self.left = HOP as u32;
    }

    /// `jump_formants(now)` if fresh, then `ramp_formants(next)`.
    pub fn set_formants(&mut self, now: &Formants, next: &Formants) {
        if self.fresh {
            self.jump_formants(now);
        }
        self.ramp_formants(next);
    }

    /// Frication band for this hop: centre `ff`, bandwidth `bw` Hz.
    pub fn set_frication(&mut self, ff: f64, bw: f64) {
        let q = (ff / bw).max(0.5) * crate::tuning::FRIC_Q_SCALE;
        self.fric.set_coeffs(BiquadCoeffs::bandpass(SR_F, ff, q));
    }

    /// Run `buf` through the cascade and the high shelf in place,
    /// advancing the F1-F3 ramp one step per sample.
    ///
    /// The sections run in groups of three (F1-F3 with their ramp; F4, F5,
    /// H1; H2-H4 and the shelf), one loop per group over the block, on
    /// local copies of coefficients and state. Each group's recursions stay
    /// in registers and the three sections of a group overlap in the
    /// pipeline.
    pub fn cascade_block(&mut self, buf: &mut [f64]) {
        self.run_nasal(buf);
        let k = (self.left as usize).min(buf.len());
        if k > 0 {
            let (head, tail) = buf.split_at_mut(k);
            self.run_moving_ramp(head);
            self.left -= k as u32;
            if self.left == 0 {
                for q in 0..3 {
                    let t = self.target[q];
                    (self.moving[q].a, self.moving[q].b, self.moving[q].c) = (t[0], t[1], t[2]);
                }
            }
            run_group(&mut self.moving, tail);
        } else {
            run_group(&mut self.moving, buf);
        }
        let (f_lo, f_hi) = self.fixed.split_at_mut(3);
        run_group(f_lo, buf);
        run_group(f_hi, buf);
        let mut shelf = self.shelf;
        for x in buf.iter_mut() {
            *x = shelf.tick(*x);
        }
        self.shelf = shelf;
    }

    /// The nasal zero then the nasal pole over `buf`, the zero's
    /// coefficients ramping per sample.
    fn run_nasal(&mut self, buf: &mut [f64]) {
        let (mut c, mut x) = (self.nz, self.nz_x);
        let (a, b, cc) = (self.np.a, self.np.b, self.np.c);
        let (mut y1, mut y2) = (self.np.y1, self.np.y2);
        let k = (self.nz_left as usize).min(buf.len());
        let d = self.nz_step;
        for (i, v) in buf.iter_mut().enumerate() {
            if i < k {
                c[0] += d[0];
                c[1] += d[1];
                c[2] += d[2];
            }
            let u = c[0] * *v + c[1] * x[0] + c[2] * x[1];
            x[1] = x[0];
            x[0] = *v;
            let y = a * u + b * y1 + cc * y2;
            y2 = y1;
            y1 = y;
            *v = y;
        }
        self.nz_left -= k as u32;
        self.nz = if self.nz_left == 0 { self.nz_target } else { c };
        self.nz_x = x;
        (self.np.y1, self.np.y2) = (y1, y2);
    }

    /// Crossfade `buf` toward its low-passed copy: y = (1 - vb) x + vb
    /// LP(x), vb ramping linearly from `vb.0` to `vb.1` over `buf`. The
    /// low-pass runs on every sample, so its state is continuous.
    pub fn voice_bar_block(&mut self, buf: &mut [f64], vb: (f64, f64)) {
        let n = buf.len().max(1) as f64;
        let dv = (vb.1 - vb.0) / n;
        let mut lp = self.bar;
        for (j, x) in buf.iter_mut().enumerate() {
            let w = vb.0 + dv * j as f64;
            let l = lp.tick(*x);
            *x = (1.0 - w) * *x + w * l;
        }
        self.bar = lp;
    }

    /// F1-F3 over `buf` with the coefficient ramp advancing each sample.
    fn run_moving_ramp(&mut self, buf: &mut [f64]) {
        let mut c = [[0.0f64; 3]; 3];
        let mut y = [[0.0f64; 2]; 3];
        for q in 0..3 {
            let r = &self.moving[q];
            c[q] = [r.a, r.b, r.c];
            y[q] = [r.y1, r.y2];
        }
        let d = self.step;
        for x in buf.iter_mut() {
            let mut v = *x;
            for q in 0..3 {
                c[q][0] += d[q][0];
                c[q][1] += d[q][1];
                c[q][2] += d[q][2];
                let o = c[q][0] * v + c[q][2] * y[q][1] + c[q][1] * y[q][0];
                y[q][1] = y[q][0];
                y[q][0] = o;
                v = o;
            }
            *x = v;
        }
        for q in 0..3 {
            let r = &mut self.moving[q];
            (r.a, r.b, r.c) = (c[q][0], c[q][1], c[q][2]);
            (r.y1, r.y2) = (y[q][0], y[q][1]);
        }
    }

    /// Current (a, b, c) of the three moving formants.
    pub fn moving_coeffs(&self) -> [(f64, f64, f64); 3] {
        [0, 1, 2].map(|q| (self.moving[q].a, self.moving[q].b, self.moving[q].c))
    }

    /// Output stage for one block: `tract` (cascade output) plus
    /// band-passed `fric` (noise times af) times 2.2, DC blocked, into
    /// `out`. `FRIC` selects the frication path at compile time; when off,
    /// `fric` is not read.
    #[inline(always)]
    pub fn finish_block<const FRIC: bool>(&mut self, tract: &[f64], fric: &[f64], out: &mut [f32]) {
        let (mut bp, mut dc) = (self.fric, self.dc);
        let mut fy = self.fric_y;
        let fg = self.fric_gain;
        for (j, (o, &t)) in out.iter_mut().zip(tract.iter()).enumerate() {
            let mut v = t;
            if FRIC {
                let y = bp.tick(fric[j]);
                fy = [y, fy[0]];
                v += y * fg;
            }
            *o = dc.tick(v) as f32;
        }
        (self.fric, self.dc, self.fric_y) = (bp, dc, fy);
    }

    /// True when the frication filter holds energy above 1e-7.
    pub fn frication_ringing(&self) -> bool {
        self.fric_y[0].abs() > 1e-7 || self.fric_y[1].abs() > 1e-7
    }

    /// Clear the frication filter.
    pub fn reset_frication(&mut self) {
        self.fric.reset();
        self.fric_y = [0.0; 2];
    }

    /// True when the cascade output and the frication filter have decayed
    /// below 1e-7.
    pub fn is_quiet(&self) -> bool {
        self.fixed[self.last_fixed].y1.abs() < 1e-7 && !self.frication_ringing()
    }

    /// Clear the resonators and the frication filter (silent frames); the
    /// next formant update jumps to its target.
    pub fn reset_resonators(&mut self) {
        for r in &mut self.moving {
            r.reset();
        }
        for r in &mut self.fixed {
            r.reset();
        }
        self.np.reset();
        self.nz_x = [0.0; 2];
        self.bar.reset();
        self.reset_frication();
        self.left = 0;
        self.fresh = true;
    }

    /// Clear every state word.
    pub fn reset(&mut self) {
        self.reset_resonators();
        self.shelf.reset();
        self.dc.reset();
    }

    /// Zero state words below the denormal floor (phrase boundaries).
    pub fn flush_denormals(&mut self) {
        for r in &mut self.moving {
            r.flush_denormals();
        }
        for r in &mut self.fixed {
            r.flush_denormals();
        }
        self.np.flush_denormals();
        self.bar.flush_denormals();
        self.shelf.flush_denormals();
        self.fric.flush_denormals();
    }

    /// DC gain of the cascade with the current coefficients (1 by
    /// construction of the Klatt resonator and the shelf's unity DC).
    pub fn cascade_dc_gain(&self) -> f64 {
        let mut g =
            (self.nz[0] + self.nz[1] + self.nz[2]) * self.np.a / (1.0 - self.np.b - self.np.c);
        for r in self.moving.iter().chain(self.fixed.iter()) {
            g *= r.a / (1.0 - r.b - r.c);
        }
        let c = self.shelf.coeffs();
        g * (c.b0 + c.b1 + c.b2) / (1.0 + c.a1 + c.a2)
    }
}

/// Three fixed sections (coefficients constant over the block) over `buf`
/// in one loop, state in locals: y = a x + c y2 + b y1 (Klatt).
#[inline(always)]
fn run_group(r: &mut [Resonator], buf: &mut [f64]) {
    let r: &mut [Resonator; 3] = match r.try_into() {
        Ok(g) => g,
        Err(_) => return,
    };
    let c = [0, 1, 2].map(|q| [r[q].a, r[q].b, r[q].c]);
    let mut y = [0, 1, 2].map(|q| [r[q].y1, r[q].y2]);
    for x in buf.iter_mut() {
        let mut v = *x;
        for q in 0..3 {
            let o = c[q][0] * v + c[q][2] * y[q][1] + c[q][1] * y[q][0];
            y[q][1] = y[q][0];
            y[q][0] = o;
            v = o;
        }
        *x = v;
    }
    for q in 0..3 {
        (r[q].y1, r[q].y2) = (y[q][0], y[q][1]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::params::voice_params;
    use song::Voice;

    fn tract() -> Tract {
        Tract::new(&voice_params(Voice::Baritone), 4)
    }

    /// With the zero at the pole's centre and bandwidth (nasality 0) the
    /// pair is the identity: an impulse comes out as an impulse.
    #[test]
    fn nasal_pair_cancels_at_nas_zero() {
        let mut t = tract();
        let mut buf = vec![0.0f64; 8192];
        buf[0] = 1.0;
        t.run_nasal(&mut buf);
        for (i, y) in buf.iter().enumerate() {
            let want = if i == 0 { 1.0 } else { 0.0 };
            assert!((y - want).abs() < 1e-9, "sample {i}: {y}");
        }
        // The same through a ramp to the same zero (what every non-nasal
        // frame does), with a noise-like input.
        let mut t = tract();
        let x: Vec<f64> = (0..4096)
            .map(|i| ((i * 7919 % 1009) as f64 / 504.5) - 1.0)
            .collect();
        let mut y = x.clone();
        for blk in y.chunks_mut(HOP) {
            t.ramp_nasal_zero(Tract::nasal_zero_at(0.0, 1600.0));
            t.run_nasal(blk);
        }
        let worst = x
            .iter()
            .zip(&y)
            .map(|(a, b)| (a - b).abs())
            .fold(0.0, f64::max);
        assert!(worst < 1e-9, "worst difference {worst}");
    }

    /// In a nasal the zero cuts its own frequency: n at 1600 Hz.
    #[test]
    fn nasal_zero_cuts_its_frequency() {
        let mut t = tract();
        t.jump_nasal_zero(Tract::nasal_zero_at(1.0, 1600.0));
        let w = 2.0 * std::f64::consts::PI * 1600.0 / SR_F;
        let mut y: Vec<f64> = (0..8192).map(|i| (w * i as f64).sin()).collect();
        t.run_nasal(&mut y);
        let rms = (y[4096..].iter().map(|v| v * v).sum::<f64>() / 4096.0).sqrt();
        assert!(rms < 0.1, "rms {rms} at the zero");
    }

    fn bar_gain_db(f: f64) -> f64 {
        let mut t = tract();
        let w = 2.0 * std::f64::consts::PI * f / SR_F;
        let n = 8192;
        let x: Vec<f64> = (0..n).map(|i| (w * i as f64).sin()).collect();
        let mut y = x.clone();
        for blk in y.chunks_mut(HOP) {
            t.voice_bar_block(blk, (1.0, 1.0));
        }
        let rms =
            |v: &[f64]| (v[n / 2..].iter().map(|a| a * a).sum::<f64>() / (n / 2) as f64).sqrt();
        20.0 * (rms(&y) / rms(&x)).log10()
    }

    /// At vb = 1 the voice bar passes the murmur and removes the rest.
    #[test]
    fn voice_bar_is_a_low_pass() {
        assert!(bar_gain_db(2000.0) < -25.0, "{}", bar_gain_db(2000.0));
        assert!(bar_gain_db(150.0).abs() < 2.0, "{}", bar_gain_db(150.0));
        // At vb = 0 the block is untouched, bit for bit.
        let mut t = tract();
        let x: Vec<f64> = (0..HOP).map(|i| (i as f64 * 0.37).sin()).collect();
        let mut y = x.clone();
        t.voice_bar_block(&mut y, (0.0, 0.0));
        assert_eq!(x, y);
    }
}
