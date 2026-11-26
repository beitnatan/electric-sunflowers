//! Feed-forward compressor (design section 5.11).
//!
//! - `PeakDetector`: branching one-pole on |x| (Giannoulis, Massberg, Reiss,
//!   "Digital Dynamic Range Compressor Design - A Tutorial and Analysis",
//!   JAES 60(6), 2012, the "branching" peak detector, applied to the
//!   linear level). Coefficient exp(-1 / (t fs)) for attack and release.
//! - `GainComputer`: static curve with a quadratic soft knee (same paper,
//!   eq. 4), as gain reduction in dB for an input level in dB. Threshold,
//!   ratio and knee width set the curve.
//! - `Compressor`: the detector runs every sample; every `GAIN_PERIOD` (16)
//!   samples the gain computer turns the detector level into a gain, and the
//!   gain is interpolated linearly (in linear gain) across the period that
//!   produced it, so there are no zipper steps and no added lag. `Link`
//!   selects mono or stereo linking by max(|L|, |R|).
//!
//! Level in dB and gain in linear come from `sfcore::math::gain_to_db` and
//! `db_to_gain`; silence reads `DB_FLOOR` (-200 dB).

use sfcore::math::{db_to_gain, gain_to_db, one_pole_coeff_tau};

/// Samples between gain-computer updates.
pub const GAIN_PERIOD: usize = 16;

/// Pole k = exp(-1 / (t fs)) = 1 - `one_pole_coeff_tau`; t <= 0 or
/// non-finite gives 0 (instant).
fn pole(t: f64, fs: f64) -> f64 {
    1.0 - one_pole_coeff_tau(if t.is_finite() { t } else { 0.0 }, fs)
}

/// Branching one-pole envelope follower on the rectified signal.
#[derive(Clone, Copy, Debug, Default)]
pub struct PeakDetector {
    pub attack_coef: f64,
    pub release_coef: f64,
    pub env: f64,
}

impl PeakDetector {
    /// Attack and release time constants in seconds.
    pub fn new(attack: f64, release: f64, fs: f64) -> Self {
        PeakDetector {
            attack_coef: pole(attack, fs),
            release_coef: pole(release, fs),
            env: 0.0,
        }
    }

    /// Feed |x| (already rectified); returns the envelope.
    #[inline(always)]
    pub fn tick(&mut self, a: f64) -> f64 {
        let k = if a > self.env {
            self.attack_coef
        } else {
            self.release_coef
        };
        self.env = k * self.env + (1.0 - k) * a;
        self.env
    }

    pub fn reset(&mut self) {
        self.env = 0.0;
    }
}

/// Static compression curve with a quadratic soft knee.
#[derive(Clone, Copy, Debug)]
pub struct GainComputer {
    pub thr_db: f64,
    pub ratio: f64,
    pub knee_db: f64,
}

impl GainComputer {
    /// Gain in dB (<= 0) for input level `level_db`. With o = level - thr
    /// and s = 1 - 1/ratio: 0 below the knee, -s (o + W/2)^2 / (2 W) inside
    /// it, -s o above. Ratio below 1 is treated as 1; knee <= 0 is hard.
    pub fn gain_db(&self, level_db: f64) -> f64 {
        let s = 1.0 - 1.0 / self.ratio.max(1.0);
        let w = self.knee_db.max(0.0);
        let o = level_db - self.thr_db;
        if 2.0 * o > w {
            -s * o
        } else if w > 0.0 && 2.0 * o > -w {
            let q = o + w / 2.0;
            -s * q * q / (2.0 * w)
        } else {
            0.0
        }
    }
}

/// Channel linking.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Link {
    /// One channel, detector on |x|.
    Mono,
    /// Two channels, one detector on max(|L|, |R|), one gain for both.
    StereoMax,
}

/// Feed-forward compressor: detector, gain computer, interpolated gain.
#[derive(Clone, Copy, Debug)]
pub struct Compressor {
    pub det: PeakDetector,
    pub gc: GainComputer,
    pub link: Link,
    /// Linear gain at the end of the last period.
    gain: f64,
}

impl Compressor {
    pub fn new(det: PeakDetector, gc: GainComputer, link: Link) -> Self {
        Compressor {
            det,
            gc,
            link,
            gain: 1.0,
        }
    }

    /// Detector level of the last sample fed, in dB.
    pub fn level_db(&self) -> f64 {
        gain_to_db(self.det.env)
    }

    fn period_gain(&self) -> f64 {
        db_to_gain(self.gc.gain_db(self.level_db()))
    }

    pub fn reset(&mut self) {
        self.det.reset();
        self.gain = 1.0;
    }

    /// Compress one channel in place (`link` is ignored).
    pub fn process_mono(&mut self, x: &mut [f32]) {
        for chunk in x.chunks_mut(GAIN_PERIOD) {
            for v in chunk.iter() {
                self.det.tick((*v as f64).abs());
            }
            let (g0, g1) = (self.gain, self.period_gain());
            let d = (g1 - g0) / chunk.len() as f64;
            for (j, v) in chunk.iter_mut().enumerate() {
                *v = (*v as f64 * (g0 + d * (j + 1) as f64)) as f32;
            }
            self.gain = g1;
        }
    }

    /// Compress two channels in place with one linked gain (max of |L|, |R|).
    /// Lengths may differ; the shorter one bounds the linked part and the
    /// rest of the longer channel is left unchanged.
    pub fn process_stereo(&mut self, l: &mut [f32], r: &mut [f32]) {
        let n = l.len().min(r.len());
        for (cl, cr) in l[..n]
            .chunks_mut(GAIN_PERIOD)
            .zip(r[..n].chunks_mut(GAIN_PERIOD))
        {
            for (a, b) in cl.iter().zip(cr.iter()) {
                self.det.tick((*a as f64).abs().max((*b as f64).abs()));
            }
            let (g0, g1) = (self.gain, self.period_gain());
            let d = (g1 - g0) / cl.len() as f64;
            for (j, (a, b)) in cl.iter_mut().zip(cr.iter_mut()).enumerate() {
                let g = g0 + d * (j + 1) as f64;
                *a = (*a as f64 * g) as f32;
                *b = (*b as f64 * g) as f32;
            }
            self.gain = g1;
        }
    }
}

/// Look-ahead window of the crest limiter, s.
pub const LIMIT_LOOKAHEAD: f64 = 0.005;
/// Release time constant of the crest limiter, s.
pub const LIMIT_RELEASE: f64 = 0.080;

/// What `crest_limit` did.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LimitStats {
    /// Smallest gain applied (1.0 when nothing was limited).
    pub min_gain: f64,
    /// Samples with a gain below 1.
    pub reduced: usize,
}

/// Offline stereo-linked look-ahead limiter: no output sample exceeds
/// `ceiling` in magnitude. When no input sample exceeds it, the signal is
/// left untouched, bit for bit, and `None` is returned.
///
/// With W = `LIMIT_LOOKAHEAD` in samples: the required gain is
/// r[n] = min(1, ceiling / max(|L[n]|, |R[n]|)); m[n] is the minimum of r
/// over [n, n + W] (a monotonic deque, O(n)); the release
/// s[n] = min(m[n], s[n-1] + (1 - s[n-1]) k) with k = 1 - exp(-1 / (tau fs)),
/// tau = `LIMIT_RELEASE`, s[-1] = 1, lets the gain recover toward 1 but never
/// above m; the attack smoothing g[n] is the mean of s over [n - W, n]
/// (indices before 0 count as 1). Every s[j] in that window is at most r[n],
/// so g[n] <= r[n]. Both channels are multiplied by g. Gain math is `f64`;
/// one `f64` buffer of the signal length is allocated. Sequential, so the
/// result does not depend on the thread count. Lengths may differ; the
/// shorter bounds the linked part and the rest is left unchanged.
pub fn crest_limit(l: &mut [f32], r: &mut [f32], ceiling: f64, fs: f64) -> Option<LimitStats> {
    let n = l.len().min(r.len());
    let peak = |l: &[f32], r: &[f32], i: usize| (l[i] as f64).abs().max((r[i] as f64).abs());
    if !(0..n).any(|i| peak(l, r, i) > ceiling) {
        return None;
    }
    let w = ((LIMIT_LOOKAHEAD * fs).round() as usize).max(1);
    let k = 1.0 - (-1.0 / (LIMIT_RELEASE * fs)).exp();
    let req = |l: &[f32], r: &[f32], i: usize| {
        let p = peak(l, r, i);
        if p > ceiling {
            ceiling / p
        } else {
            1.0
        }
    };
    // The deque holds (index, r) with rising r; its front is the window min.
    let mut s = vec![1.0f64; n];
    let mut dq: std::collections::VecDeque<(usize, f64)> =
        std::collections::VecDeque::with_capacity(w + 2);
    let mut next = 0usize;
    let mut prev = 1.0f64;
    for (i, si) in s.iter_mut().enumerate() {
        while next < n && next <= i + w {
            let v = req(l, r, next);
            while dq.back().is_some_and(|&(_, b)| b >= v) {
                dq.pop_back();
            }
            dq.push_back((next, v));
            next += 1;
        }
        while dq.front().is_some_and(|&(j, _)| j < i) {
            dq.pop_front();
        }
        let m = dq.front().map_or(1.0, |&(_, v)| v);
        prev = m.min(prev + (1.0 - prev) * k);
        *si = prev;
    }
    // Attack smoothing: running mean over the w + 1 samples ending at i. The
    // sum is rebuilt every RESUM samples so rounding cannot drift.
    const RESUM: usize = 4096;
    let len = (w + 1) as f64;
    let at = |s: &[f64], j: isize| if j < 0 { 1.0 } else { s[j as usize] };
    let (mut sum, mut min_gain, mut reduced) = (0.0f64, 1.0f64, 0usize);
    for i in 0..n {
        if i % RESUM == 0 {
            sum = (0..=w as isize).map(|d| at(&s, i as isize - d)).sum();
        } else {
            sum += s[i] - at(&s, i as isize - w as isize - 1);
        }
        // g <= r[i] holds exactly; the clamp removes rounding of the sum.
        let g = (sum / len).min(1.0).min(req(l, r, i));
        l[i] = (l[i] as f64 * g) as f32;
        r[i] = (r[i] as f64 * g) as f32;
        if g < 1.0 {
            reduced += 1;
            min_gain = min_gain.min(g);
        }
    }
    Some(LimitStats { min_gain, reduced })
}

#[cfg(test)]
mod limit_tests {
    use super::*;

    const FS: f64 = 44100.0;

    fn noise(n: usize, amp: f32, seed: u32) -> Vec<f32> {
        let mut x = seed;
        (0..n)
            .map(|_| {
                x = x.wrapping_mul(1664525).wrapping_add(1013904223);
                ((x >> 8) as f32 / (1u32 << 24) as f32 * 2.0 - 1.0) * amp
            })
            .collect()
    }

    #[test]
    fn no_output_exceeds_the_ceiling() {
        let mut l = noise(50_000, 0.3, 1);
        let mut r = noise(50_000, 0.3, 2);
        for i in [0usize, 100, 101, 9_000, 30_000, 30_001, 49_998] {
            l[i] = 2.5;
            r[i + 1] = -4.0;
        }
        l[49_999] = 3.0;
        let c = 0.5;
        let st = crest_limit(&mut l, &mut r, c, FS).expect("limited");
        assert!(st.reduced > 0 && st.min_gain < 0.2);
        let worst = l
            .iter()
            .chain(r.iter())
            .fold(0.0f32, |a, &v| a.max(v.abs()));
        assert!(worst as f64 <= c * (1.0 + 1e-6), "peak {worst}");
    }

    #[test]
    fn signal_under_ceiling_is_unchanged() {
        let mut l = noise(20_000, 0.4, 3);
        let mut r = noise(20_000, 0.4, 4);
        let (l0, r0) = (l.clone(), r.clone());
        assert_eq!(crest_limit(&mut l, &mut r, 0.5, FS), None);
        assert!(l.iter().zip(&l0).all(|(a, b)| a.to_bits() == b.to_bits()));
        assert!(r.iter().zip(&r0).all(|(a, b)| a.to_bits() == b.to_bits()));
    }

    #[test]
    fn gain_recovers_after_an_isolated_spike() {
        let n = 44_100;
        let mut l = vec![0.5f32; n];
        let mut r = vec![0.5f32; n];
        l[1000] = 2.0;
        crest_limit(&mut l, &mut r, 1.0, FS).expect("limited");
        assert!(r[1000] < 0.3, "spike not reduced");
        // 0.5 s is 6.25 time constants: within 0.5% of unity.
        let i = 1000 + 22_050;
        assert!(r[i] > 0.5 * 0.995, "gain {}", r[i] / 0.5);
        assert!(r[n - 1] > 0.5 * 0.999);
    }

    #[test]
    fn constant_over_ceiling_settles_to_the_ceiling() {
        let mut l = vec![3.0f32; 10_000];
        let mut r = vec![-1.0f32; 10_000];
        crest_limit(&mut l, &mut r, 1.0, FS).expect("limited");
        for &v in &l[1000..] {
            assert!((v as f64 - 1.0).abs() < 1e-6, "{v}");
        }
        assert!(l.iter().all(|&v| v <= 1.0 + 1e-6));
    }
}
