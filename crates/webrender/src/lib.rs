//! Browser entry for the engine: song JSON in, stereo PCM out, in one
//! wasm module with a plain C interface (no wasm-bindgen, so the build
//! needs only rustc).
//!
//! Runs on one thread: GitHub Pages cannot send the COOP/COEP headers
//! that SharedArrayBuffer needs, so rayon falls back to the calling
//! thread. Call from a Web Worker; the host supplies `env.sf_progress`.
//!
//! Protocol: `sf_alloc` a buffer, copy the song JSON in, call `sf_probe`
//! (length and memory estimate) and then `sf_render`; read the channels
//! with `sf_channel_ptr`, the metadata JSON with `sf_meta_ptr`, and call
//! `sf_release`. After a trap, `sf_error_ptr` still holds the panic text.

use engine::{MixSettings, Progress, VoiceChoice};
use song::Voice;
use std::sync::Mutex;

/// A finished render.
pub struct Rendered {
    pub l: Vec<f32>,
    pub r: Vec<f32>,
    /// JSON: title, seconds, sample rate, sheet.
    pub meta: String,
}

/// Voice code from the host: 0 is the song's own voice.
pub fn voice_of(code: u32) -> Option<Voice> {
    match code {
        1 => Some(Voice::Bass),
        2 => Some(Voice::Baritone),
        3 => Some(Voice::Tenor),
        4 => Some(Voice::Alto),
        5 => Some(Voice::Soprano),
        _ => None,
    }
}

fn parse(json: &str) -> Result<song::Song, String> {
    let raw: serde_json::Value = serde_json::from_str(json).map_err(|e| format!("JSON: {e}"))?;
    song::normalize_value(&raw)
        .map(|(s, _repairs)| s)
        .map_err(|e| format!("song: {e}"))
}

/// Length of the song in seconds (arranges, does not play).
pub fn probe_seconds(json: &str, seed: u64, voice: Option<Voice>) -> Result<f64, String> {
    let song = parse(json)?;
    let (_, perf) = engine::arrange_song(&song, seed, VoiceChoice { a: voice, b: None });
    Ok(perf.end)
}

/// Renders and mixes `json`; the stems are dropped before the return, so
/// the peak is inside this call.
pub fn render_pcm(
    json: &str,
    seed: u64,
    voice: Option<Voice>,
    progress: &dyn Progress,
) -> Result<Rendered, String> {
    let song = parse(json)?;
    let (prepared, stems) = engine::render(&song, seed, voice, progress);
    let defaults = MixSettings::default_for(&stems);
    let m = engine::mix_with(&stems, &song.band, seed, &defaults);
    drop(stems);
    let sheet = engine::sheet_from(&song, seed, &prepared);
    let meta = serde_json::json!({
        "title": song.title,
        "seconds": m.l.len() as f64 / sfcore::SR_F,
        "rate": sfcore::SR,
        "sheet": sheet,
    })
    .to_string();
    Ok(Rendered {
        l: m.l,
        r: m.r,
        meta,
    })
}

// ---- C interface (the host is a JS worker) ----

static RESULT: Mutex<Option<Rendered>> = Mutex::new(None);
static TEXT: Mutex<String> = Mutex::new(String::new());
static PROBE: Mutex<f64> = Mutex::new(0.0);

fn set_text(s: String) {
    *TEXT.lock().unwrap_or_else(|e| e.into_inner()) = s;
}

#[cfg(target_arch = "wasm32")]
#[link(wasm_import_module = "env")]
extern "C" {
    fn sf_progress(done: u32, total: u32);
}

struct Host;
impl Progress for Host {
    fn advance(&self, _done: usize, _total: usize) {
        #[cfg(target_arch = "wasm32")]
        // SAFETY: the host function reads two integers.
        unsafe {
            sf_progress(_done as u32, _total as u32)
        }
    }
}

fn install_hook() {
    std::panic::set_hook(Box::new(|info| set_text(info.to_string())));
}

fn text<'a>(ptr: *const u8, len: usize) -> &'a str {
    // SAFETY: the host passes a buffer from `sf_alloc` that holds `len` bytes.
    let b = unsafe { std::slice::from_raw_parts(ptr, len) };
    std::str::from_utf8(b).unwrap_or("")
}

#[no_mangle]
pub extern "C" fn sf_alloc(len: usize) -> *mut u8 {
    let mut v = Vec::<u8>::with_capacity(len.max(1));
    let p = v.as_mut_ptr();
    std::mem::forget(v);
    p
}

/// # Safety
///
/// `ptr` and `len` must be a pointer from `sf_alloc(len)` that has not been
/// freed.
#[no_mangle]
pub unsafe extern "C" fn sf_dealloc(ptr: *mut u8, len: usize) {
    // SAFETY: the caller pairs this with `sf_alloc` (same capacity, no
    // initialised bytes).
    unsafe { drop(Vec::from_raw_parts(ptr, 0, len.max(1))) }
}

fn seed_of(lo: u32, hi: u32) -> u64 {
    (u64::from(hi) << 32) | u64::from(lo)
}

/// Song length in seconds, or a negative number (see `sf_error_ptr`).
#[no_mangle]
pub extern "C" fn sf_probe(ptr: *const u8, len: usize, lo: u32, hi: u32, voice: u32) -> f64 {
    install_hook();
    match probe_seconds(text(ptr, len), seed_of(lo, hi), voice_of(voice)) {
        Ok(s) => {
            *PROBE.lock().unwrap_or_else(|e| e.into_inner()) = s;
            s
        }
        Err(e) => {
            set_text(e);
            -1.0
        }
    }
}

/// 0 on success, 1 on a song error (`sf_error_ptr`). A panic traps.
#[no_mangle]
pub extern "C" fn sf_render(ptr: *const u8, len: usize, lo: u32, hi: u32, voice: u32) -> u32 {
    install_hook();
    match render_pcm(text(ptr, len), seed_of(lo, hi), voice_of(voice), &Host) {
        Ok(r) => {
            *RESULT.lock().unwrap_or_else(|e| e.into_inner()) = Some(r);
            0
        }
        Err(e) => {
            set_text(e);
            1
        }
    }
}

fn with_result<T>(f: impl FnOnce(&Rendered) -> T, none: T) -> T {
    match RESULT.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
        Some(r) => f(r),
        None => none,
    }
}

#[no_mangle]
pub extern "C" fn sf_samples() -> u32 {
    with_result(|r| r.l.len() as u32, 0)
}

#[no_mangle]
pub extern "C" fn sf_channel_ptr(ch: u32) -> *const f32 {
    with_result(
        |r| if ch == 0 { r.l.as_ptr() } else { r.r.as_ptr() },
        std::ptr::null(),
    )
}

#[no_mangle]
pub extern "C" fn sf_meta_ptr() -> *const u8 {
    with_result(|r| r.meta.as_ptr(), std::ptr::null())
}

#[no_mangle]
pub extern "C" fn sf_meta_len() -> u32 {
    with_result(|r| r.meta.len() as u32, 0)
}

#[no_mangle]
pub extern "C" fn sf_error_ptr() -> *const u8 {
    TEXT.lock().unwrap_or_else(|e| e.into_inner()).as_ptr()
}

#[no_mangle]
pub extern "C" fn sf_error_len() -> u32 {
    TEXT.lock().unwrap_or_else(|e| e.into_inner()).len() as u32
}

/// Linear memory now, in bytes. Wasm memory only grows, so after a render
/// this is the peak.
#[no_mangle]
pub extern "C" fn sf_memory_bytes() -> f64 {
    #[cfg(target_arch = "wasm32")]
    {
        (core::arch::wasm32::memory_size(0) as f64) * 65536.0
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        0.0
    }
}

#[no_mangle]
pub extern "C" fn sf_release() {
    *RESULT.lock().unwrap_or_else(|e| e.into_inner()) = None;
}
