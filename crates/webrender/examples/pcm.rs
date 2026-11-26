//! Native reference for the wasm build: `pcm SONG.json SEED OUT.f32` writes
//! interleaved little-endian f32 stereo. Run with RAYON_NUM_THREADS=1.
use std::io::Write;

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let json = std::fs::read_to_string(&a[1]).expect("read song");
    let seed: u64 = a[2].parse().expect("seed");
    let t = std::time::Instant::now();
    let r = webrender::render_pcm(&json, seed, None, &engine::NoProgress).expect("render");
    eprintln!(
        "native render {:.2} s, {} samples",
        t.elapsed().as_secs_f64(),
        r.l.len()
    );
    let mut f = std::io::BufWriter::new(std::fs::File::create(&a[3]).expect("create"));
    for (l, r) in r.l.iter().zip(&r.r) {
        f.write_all(&l.to_le_bytes()).unwrap();
        f.write_all(&r.to_le_bytes()).unwrap();
    }
}
