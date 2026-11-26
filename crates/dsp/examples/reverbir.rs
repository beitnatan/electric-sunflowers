//! Impulse responses of the mix reverb, for `tools/reverb_check.py`.
//!
//! `reverbir [--seed N] [--wet W] OUT_PREFIX` writes 4 s of stereo output as
//! 32-bit float WAV: `OUT_PREFIX-both.wav` (unit impulse on both send
//! channels) and `OUT_PREFIX-left.wav` (left only). The reverb is built as the
//! mix builds it: seed 1234, the mix T60s.

use dsp::reverb::{Fdn16 as Fdn, T60_DC, T60_NYQ};
use sfcore::SR_F;
use std::io::Write;

const SECONDS: usize = 4;

fn ir(seed: u64, wet: f64, left: bool, right: bool) -> [Vec<f32>; 2] {
    let n = (SR_F as usize) * SECONDS;
    let mut fdn = Fdn::new(SR_F, seed, T60_DC, T60_NYQ);
    let mut sl = vec![0.0f32; n];
    let mut sr = vec![0.0f32; n];
    if left {
        sl[0] = 1.0;
    }
    if right {
        sr[0] = 1.0;
    }
    let mut ol = vec![0.0f32; n];
    let mut or = vec![0.0f32; n];
    for s in (0..n).step_by(1024) {
        let e = (s + 1024).min(n);
        fdn.process_block([&sl[s..e], &sr[s..e]], [&mut ol[s..e], &mut or[s..e]], wet);
    }
    [ol, or]
}

fn write_wav(path: &str, ch: &[Vec<f32>; 2]) -> std::io::Result<()> {
    let n = ch[0].len();
    let data = (n * 2 * 4) as u32;
    let mut f = std::io::BufWriter::new(std::fs::File::create(path)?);
    f.write_all(b"RIFF")?;
    f.write_all(&(36 + data).to_le_bytes())?;
    f.write_all(b"WAVEfmt ")?;
    f.write_all(&16u32.to_le_bytes())?;
    f.write_all(&3u16.to_le_bytes())?; // IEEE float
    f.write_all(&2u16.to_le_bytes())?;
    f.write_all(&(SR_F as u32).to_le_bytes())?;
    f.write_all(&((SR_F as u32) * 8).to_le_bytes())?;
    f.write_all(&8u16.to_le_bytes())?;
    f.write_all(&32u16.to_le_bytes())?;
    f.write_all(b"data")?;
    f.write_all(&data.to_le_bytes())?;
    for i in 0..n {
        f.write_all(&ch[0][i].to_le_bytes())?;
        f.write_all(&ch[1][i].to_le_bytes())?;
    }
    f.flush()
}

fn main() -> std::io::Result<()> {
    let (mut seed, mut wet, mut prefix) = (1234u64, 1.0f64, None);
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--seed" => seed = it.next().and_then(|v| v.parse().ok()).unwrap_or(seed),
            "--wet" => wet = it.next().and_then(|v| v.parse().ok()).unwrap_or(wet),
            _ => prefix = Some(a),
        }
    }
    let Some(prefix) = prefix else {
        eprintln!("usage: reverbir [--seed N] [--wet W] OUT_PREFIX");
        std::process::exit(2);
    };
    write_wav(&format!("{prefix}-both.wav"), &ir(seed, wet, true, true))?;
    write_wav(&format!("{prefix}-left.wav"), &ir(seed, wet, true, false))?;
    Ok(())
}
