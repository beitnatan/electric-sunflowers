//! Writes isolated bass notes as mono 32-bit float WAV files for
//! measurement (tools/bass_target.py `notes`): MIDI 34, 38, 41, 43, 46 at
//! velocity 0.8, each held 2.4 s, in a 3 s file, rendered by the bass's own
//! code path (`render_bass`: pluck, sine sub layer and thump; no channel
//! strip).
//!
//! Usage: `bassnotes DIR [--seed N] [--k K]`. `--k` overrides the envelope
//! scale `guitar::BASS_ENV_K` (for the fit). Files are `bass_<midi>.wav`.

use instruments::guitar::{render_bass_k, BASS_ENV_K};
use sfcore::SR;
use song::events::PluckNote;
use std::io::Write;

const MIDI: [u8; 5] = [34, 38, 41, 43, 46];
const T0: f64 = 0.1;
const HOLD_S: f64 = 2.4;
const FILE_S: f64 = 3.0;

fn wav(path: &std::path::Path, x: &[f32]) -> std::io::Result<()> {
    let mut f = std::io::BufWriter::new(std::fs::File::create(path)?);
    let bytes = (x.len() * 4) as u32;
    f.write_all(b"RIFF")?;
    f.write_all(&(36 + bytes).to_le_bytes())?;
    f.write_all(b"WAVEfmt ")?;
    f.write_all(&16u32.to_le_bytes())?;
    f.write_all(&3u16.to_le_bytes())?; // IEEE float
    f.write_all(&1u16.to_le_bytes())?;
    f.write_all(&(SR as u32).to_le_bytes())?;
    f.write_all(&((SR * 4) as u32).to_le_bytes())?;
    f.write_all(&4u16.to_le_bytes())?;
    f.write_all(&32u16.to_le_bytes())?;
    f.write_all(b"data")?;
    f.write_all(&bytes.to_le_bytes())?;
    for v in x {
        f.write_all(&v.to_le_bytes())?;
    }
    Ok(())
}

fn main() {
    let mut a = std::env::args().skip(1);
    let Some(dir) = a.next() else {
        eprintln!("usage: bassnotes DIR [--seed N] [--k K]");
        std::process::exit(2);
    };
    let (mut seed, mut k) = (1234u64, BASS_ENV_K);
    while let Some(o) = a.next() {
        match o.as_str() {
            "--seed" => seed = a.next().and_then(|v| v.parse().ok()).unwrap_or(seed),
            "--k" => k = a.next().and_then(|v| v.parse().ok()).unwrap_or(k),
            _ => {}
        }
    }
    std::fs::create_dir_all(&dir).expect("create dir");
    let len = (FILE_S * SR as f64) as usize;
    for m in MIDI {
        let note = PluckNote {
            t0: T0,
            t1: T0 + HOLD_S,
            midi: m as f32,
            vel: 0.8,
        };
        let x = render_bass_k(&[note], seed, len, k);
        wav(
            &std::path::Path::new(&dir).join(format!("bass_{m}.wav")),
            &x,
        )
        .expect("write");
    }
}
