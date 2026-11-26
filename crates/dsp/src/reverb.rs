//! Reverb: a sixteen-line feedback delay network (Jot and Chaigne 1991,
//! "Digital delay networks for designing artificial reverberators", AES 90th
//! Convention) with frequency-dependent absorption and slow delay modulation,
//! fed through a predelay, a high-pass and four series Schroeder allpasses
//! (Schroeder 1962).
//!
//! Why sixteen lines. The eight-line network had delays summing to 17100
//! samples, 0.39 s, so 0.39 modes per hertz: one mode every 2.6 Hz. At a low
//! frequency T60 of 2.2 s a mode is 1.0 Hz wide, so the modes were resolved
//! one by one and the tail rang at discrete frequencies (a metallic colour),
//! where a room's modes overlap several deep. The sixteen lines sum to 43460
//! samples (0.99 s, one mode per 1.0 Hz) and the T60 at DC is 1.6 s (a mode
//! 1.4 Hz wide), so neighbouring modes overlap. Six lines are modulated, which
//! smears the remaining mode peaks in time.
//!
//! Signal flow per sample, input (L, R):
//! 1. mono sum `(L + R) / 2` -> 220 Hz high-pass (RBJ, Q 0.7) -> 16 ms
//!    predelay -> allpasses 142/379/107/277 samples, g 0.65/0.62/0.60/0.58.
//! 2. Each line k is read at its delay `d_k` (lines 0, 3, 6, 9, 12, 15 at
//!    `d_k + 8 sin(2 pi f_j n / fs + 1.1 j)` with linear interpolation, see
//!    below) and low-passed by its absorption one-pole:
//!    `o_k = b_k v_k + a_k o_k[n-1]`.
//! 3. Feedback: Householder matrix `A = I - (2/N) 1 1^T` (N = 16), lossless
//!    and dense, so line k is written with `o_k - (2/16) sum(o)`, plus the
//!    diffused input times `0.5 sqrt(8/16)` (the input power of the eight-line
//!    network), plus the side signal `0.15 (L - R)` with a sign that
//!    alternates in pairs of lines (- - + + - - + + ...).
//! 4. Output taps, with `c = sqrt(4/8)`:
//!    `L = c (o0 - o2 + o4 - o6 + o8 - o10 + o12 - o14)`,
//!    `R = c (o1 - o3 + o5 - o7 + o9 - o11 + o13 - o15)`.
//!    Disjoint line sets per channel decorrelate the two outputs; there are no
//!    cross terms.
//!
//! Modulation. Modulated line j (j = 0..5, line k = 3 j) is read `8 sin(2 pi
//! f_j n / fs + 1.1 j)` samples later than its nominal delay (so the offset
//! lies in [-8, 8]), with `f_j` = 0.31, 0.43, 0.59, 0.71, 0.83, 0.97 Hz and
//! `n` the sample count since construction or `reset`, so the render is
//! deterministic. The read position is split into an integer `i` and a
//! fraction `f`; the output is `(1 - f) x[n - i] + f x[n - i - 1]`. The
//! shortest delay is 887 samples and the longest 5029 + 9 < 8192, so every
//! tap is inside its segment. The absorption design uses the nominal delay.
//!
//! Absorption design (Jot and Chaigne 1991, section 3). A line of `d` samples
//! must lose 60 dB in T60 seconds, so its gain per pass is
//! `g(T60) = 10^(-3 d / (T60 fs))`. With `gd = g(t60_dc)` and
//! `gn = g(t60_nyq)`, the one-pole
//! `H(z) = gd (1 - a) / (1 - a z^-1)`, `a = (gd - gn) / (gd + gn)`,
//! has `H(1) = gd` at DC and `H(-1) = gd (1 - a) / (1 + a) = gn` at Nyquist,
//! so every line decays at the same rate per second at both ends of the band.
//! Between them T60(f) follows `-3 d / (fs log10 |H(e^jw)|)`.
//!
//! Delay lines: base lengths 887, 1031, 1193, 1381, 1597, 1847, 2137, 2473,
//! 2861, 3109, 3371, 3659, 3967, 4297, 4651, 4999 (primes, 20-113 ms), each
//! plus a seed offset `(seed (d mod 7)) mod 31` in [0, 30]. All sixteen live
//! in one `f32` buffer of sixteen power-of-two segments; one write index is
//! shared and wrap is a mask. Loop filter state is `f64` (design section 4).
//!
//! Parameters that set the sound: the two T60s (1.6 s at DC, 0.8 s at
//! Nyquist in the mix), the delay lengths, the modulation depth and rates, the
//! allpass lengths and gains, the 16 ms predelay, the 220 Hz input high-pass
//! and the wet gain (`engine::mix::WET`).

use crate::biquad::{Biquad, BiquadCoeffs, DENORMAL_FLOOR};
use crate::delay::{DelayLine, SchroederAllpass};
use sfcore::SR_F;

/// Number of delay lines.
pub const N_LINES: usize = 16;
/// Base delay lengths in samples at 44.1 kHz (primes).
pub const BASE_DELAYS: [usize; N_LINES] = [
    887, 1031, 1193, 1381, 1597, 1847, 2137, 2473, 2861, 3109, 3371, 3659, 3967, 4297, 4651, 4999,
];
/// Series allpass lengths (samples) and gains.
const AP_LEN: [usize; 4] = [142, 379, 107, 277];
const AP_G: [f32; 4] = [0.65, 0.62, 0.60, 0.58];
/// Predelay in seconds.
const PREDELAY_S: f64 = 0.016;
/// Input high-pass corner (Hz) and Q.
const HP_HZ: f64 = 220.0;
const HP_Q: f64 = 0.7;
/// Gain of the diffused mono input into every line: `0.5 sqrt(8 / 16)`, the
/// input power of the eight-line network.
const IN_GAIN: f64 = 0.353_553_390_593_273_8;
/// Gain of the side signal `L - R` into the lines.
const SIDE_GAIN: f64 = 0.15;
/// Output tap gain `sqrt(4 / 8)`.
const TAP_GAIN: f64 = std::f64::consts::FRAC_1_SQRT_2;
/// Mix T60s: 1.6 s at DC, 0.8 s at Nyquist.
pub const T60_DC: f64 = 1.6;
pub const T60_NYQ: f64 = 0.8;
/// Modulated lines (every third line), their rates (Hz), and the depth in
/// samples. The phase of modulated line j is `1.1 j` rad.
pub const N_MOD: usize = 6;
pub const MOD_LINE: [usize; N_MOD] = [0, 3, 6, 9, 12, 15];
pub const MOD_HZ: [f64; N_MOD] = [0.31, 0.43, 0.59, 0.71, 0.83, 0.97];
pub const MOD_DEPTH: f64 = 8.0;
const MOD_PHASE_STEP: f64 = 1.1;

/// Delay of line `k` for `seed`: base plus `(seed (base mod 7)) mod 31`.
pub fn line_delay(k: usize, seed: u64) -> usize {
    let b = BASE_DELAYS[k % N_LINES];
    b + (seed.wrapping_mul((b % 7) as u64) % 31) as usize
}

/// Absorption one-pole `(b, a)` for a line of `d` samples:
/// `y = b x + a y[n-1]` with DC gain `gd` and Nyquist gain `gn` (module doc).
pub fn absorption(d: usize, fs: f64, t60_dc: f64, t60_nyq: f64) -> (f64, f64) {
    let gd = 10f64.powf(-3.0 * d as f64 / (t60_dc * fs));
    let gn = 10f64.powf(-3.0 * d as f64 / (t60_nyq * fs));
    let a = (gd - gn) / (gd + gn);
    (gd * (1.0 - a), a)
}

/// Sixteen-line feedback delay network reverb. See the module doc.
#[derive(Clone, Debug)]
pub struct Fdn16 {
    /// Sixteen segments of `seg` samples each; line k is `buf[k seg .. (k+1) seg]`.
    buf: Vec<f32>,
    seg: usize,
    mask: usize,
    /// Shared write index (masked on use).
    w: usize,
    /// Samples since construction or reset (the modulation clock).
    n: u64,
    delay: [usize; N_LINES],
    /// Modulation: radians per sample of modulated line j.
    mod_w: [f64; N_MOD],
    b: [f64; N_LINES],
    a: [f64; N_LINES],
    /// Absorption filter outputs, one per line.
    z: [f64; N_LINES],
    hp: Biquad,
    pre: DelayLine,
    pre_d: usize,
    ap: [SchroederAllpass; 4],
}

impl Fdn16 {
    /// A reverb at sample rate `fs` with line lengths offset by `seed` and
    /// the given T60s in seconds. Non-finite or non-positive values fall
    /// back to 44.1 kHz and the mix T60s.
    pub fn new(fs: f64, seed: u64, t60_dc: f64, t60_nyq: f64) -> Self {
        let ok = |v: f64, dflt: f64| if v.is_finite() && v > 0.0 { v } else { dflt };
        let fs = ok(fs, SR_F);
        let t60_dc = ok(t60_dc, T60_DC);
        let t60_nyq = ok(t60_nyq, T60_NYQ);
        let mut delay = [0usize; N_LINES];
        let mut b = [0.0; N_LINES];
        let mut a = [0.0; N_LINES];
        for k in 0..N_LINES {
            delay[k] = line_delay(k, seed);
            (b[k], a[k]) = absorption(delay[k], fs, t60_dc, t60_nyq);
        }
        // The farthest tap of a modulated read is `d + MOD_DEPTH` rounded up,
        // plus one sample for the interpolation.
        let need = delay.iter().copied().max().unwrap_or(1) + MOD_DEPTH as usize + 2;
        let seg = need.next_power_of_two();
        let pre_d = ((PREDELAY_S * fs).round() as usize).max(1);
        Fdn16 {
            buf: vec![0.0; seg * N_LINES],
            seg,
            mask: seg - 1,
            w: 0,
            n: 0,
            delay,
            mod_w: MOD_HZ.map(|f| std::f64::consts::TAU * f / fs),
            b,
            a,
            z: [0.0; N_LINES],
            hp: Biquad::new(BiquadCoeffs::highpass(fs, HP_HZ, HP_Q)),
            pre: DelayLine::new(pre_d),
            pre_d,
            ap: [0, 1, 2, 3].map(|i| SchroederAllpass::new(AP_LEN[i], AP_G[i])),
        }
    }

    /// Line delays in samples (nominal, without modulation).
    pub fn delays(&self) -> [usize; N_LINES] {
        self.delay
    }

    /// Samples per line segment (a power of two).
    pub fn segment(&self) -> usize {
        self.seg
    }

    /// Delay in samples at which line `k` is read at sample count `n`: the
    /// nominal delay, plus the modulation offset for the modulated lines.
    #[inline(always)]
    pub fn read_delay(&self, k: usize, n: u64) -> f64 {
        let d = self.delay[k] as f64;
        match k % 3 == 0 {
            true => {
                let j = k / 3;
                d + MOD_DEPTH * (self.mod_w[j] * n as f64 + MOD_PHASE_STEP * j as f64).sin()
            }
            false => d,
        }
    }

    /// One stereo sample in, the unscaled wet stereo sample out.
    #[inline(always)]
    fn tick(&mut self, l: f64, r: f64) -> [f64; 2] {
        // Input diffusion: high-pass, predelay, series allpasses.
        let x = self.hp.tick((l + r) * 0.5);
        self.pre.push(x as f32);
        let mut xf = self.pre.read_int(self.pre_d);
        for ap in self.ap.iter_mut() {
            xf = ap.tick(xf);
        }
        let x = xf as f64 * IN_GAIN;
        let side = (l - r) * SIDE_GAIN;

        // Read and absorb.
        let w = self.w;
        let mut sum = 0.0;
        for k in 0..N_LINES {
            let base = k * self.seg;
            let v = if k % 3 == 0 {
                let d = self.read_delay(k, self.n);
                let i = d as usize; // floor: d >= 879
                let f = d - i as f64;
                let a = self.buf[base + (w.wrapping_sub(i) & self.mask)] as f64;
                let b = self.buf[base + (w.wrapping_sub(i + 1) & self.mask)] as f64;
                a + f * (b - a)
            } else {
                self.buf[base + (w.wrapping_sub(self.delay[k]) & self.mask)] as f64
            };
            let o = self.b[k] * v + self.a[k] * self.z[k];
            self.z[k] = o;
            sum += o;
        }
        // Householder feedback plus input.
        let h = sum * (2.0 / N_LINES as f64);
        let wi = w & self.mask;
        for k in 0..N_LINES {
            let s = if k & 2 != 0 { side } else { -side };
            self.buf[k * self.seg + wi] = (self.z[k] - h + x + s) as f32;
        }
        self.w = w.wrapping_add(1);
        self.n += 1;

        let o = &self.z;
        let (mut yl, mut yr) = (0.0, 0.0);
        for p in 0..N_LINES / 2 {
            let sgn = if p % 2 == 0 { 1.0 } else { -1.0 };
            yl += sgn * o[2 * p];
            yr += sgn * o[2 * p + 1];
        }
        [TAP_GAIN * yl, TAP_GAIN * yr]
    }

    /// One stereo sample in, the unscaled wet stereo sample out.
    pub fn process(&mut self, x: [f32; 2]) -> [f32; 2] {
        let [yl, yr] = self.tick(x[0] as f64, x[1] as f64);
        [yl as f32, yr as f32]
    }

    /// Run the send buses through the reverb and add `wet` times the result
    /// into `out`. Processes the shortest of the four lengths. Flushes
    /// denormal filter state at the end of the block.
    pub fn process_block(&mut self, send: [&[f32]; 2], out: [&mut [f32]; 2], wet: f64) {
        let [sl, sr] = send;
        let [ol, or] = out;
        let n = sl.len().min(sr.len()).min(ol.len()).min(or.len());
        for i in 0..n {
            let [yl, yr] = self.tick(sl[i] as f64, sr[i] as f64);
            ol[i] = (ol[i] as f64 + wet * yl) as f32;
            or[i] = (or[i] as f64 + wet * yr) as f32;
        }
        self.flush_denormals();
    }

    /// Zero loop and input filter state below `DENORMAL_FLOOR`.
    pub fn flush_denormals(&mut self) {
        for z in self.z.iter_mut() {
            if z.abs() < DENORMAL_FLOOR {
                *z = 0.0;
            }
        }
        self.hp.flush_denormals();
    }

    /// Clear all state.
    pub fn reset(&mut self) {
        self.buf.fill(0.0);
        self.w = 0;
        self.n = 0;
        self.z = [0.0; N_LINES];
        self.hp.reset();
        self.pre.clear();
        for ap in self.ap.iter_mut() {
            ap.reset();
        }
    }
}
