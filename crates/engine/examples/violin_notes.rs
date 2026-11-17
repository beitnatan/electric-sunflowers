//! The violin's notes for a song, as the render plays them: writes JSON
//! `[{"t0", "t1", "midi", "vel", "written"}, ...]` sorted by onset, where
//! `written` marks a note of a written tune (schema 3). For
//! tools/fiddle_check.py.
//!
//! Usage: cargo run --release -p engine --example violin_notes -- --seed S --song demo|SONG.json --out FILE.json

use compose::prepare::VoiceChoice;
use engine::{arrange_song, demo_song};

fn main() {
    if let Err(e) = run() {
        eprintln!("violin_notes: {e}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let mut seed: u64 = 1234;
    let mut file: Option<String> = None;
    let mut out: Option<String> = None;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--seed" => {
                let v = args.next().ok_or("--seed needs a value")?;
                seed = v.parse().map_err(|_| format!("bad seed {v}"))?;
            }
            "--song" => {
                let v = args.next().ok_or("--song needs a value")?;
                if v != "demo" {
                    file = Some(v);
                }
            }
            "--out" => out = Some(args.next().ok_or("--out needs a value")?),
            _ => {
                return Err(format!(
                    "unknown argument {a}; usage: violin_notes --seed S --song demo|SONG.json --out FILE.json"
                ))
            }
        }
    }
    let out = out.ok_or("--out FILE is required")?;
    let song: &song::Song = match &file {
        None => demo_song(),
        Some(path) => {
            let json = std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
            let (s, _) = song::wire::normalize_str(&json).map_err(|e| e.to_string())?;
            Box::leak(Box::new(s))
        }
    };
    let (prepared, perf) = arrange_song(song, seed, VoiceChoice::default());
    let tl = &prepared.timeline;
    let written: Vec<f64> = prepared
        .comp
        .inst
        .iter()
        .filter(|n| n.written)
        .map(|n| tl.to_time(n.beat))
        .collect();
    let mut notes = perf.arrangement.violin.clone();
    notes.sort_by(|a, b| a.t0.total_cmp(&b.t0));
    let rows: Vec<serde_json::Value> = notes
        .iter()
        .map(|n| {
            let w = written.iter().any(|t| (t - n.t0).abs() < 1e-9);
            serde_json::json!({ "t0": n.t0, "t1": n.t1, "midi": n.midi, "vel": n.vel, "written": w })
        })
        .collect();
    let text = serde_json::to_string(&rows).map_err(|e| e.to_string())? + "\n";
    std::fs::write(&out, text).map_err(|e| format!("{out}: {e}"))
}
