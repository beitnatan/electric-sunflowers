#!/usr/bin/env python3
"""Measure how well a rendered violin stem follows its written notes.

Usage: python3 -I tools/fiddle_check.py VIOLIN.wav NOTES.json [--max-dur 0.3]
                                        [--written] [--until SECONDS]

NOTES.json comes from `cargo run --release -p engine --example violin_notes --
--seed S --song FILE --out NOTES.json`: a list of {t0, t1, midi, vel, written}.
numpy only. The notes shorter than --max-dur (fast notes) and the longer ones
(slow notes) are reported separately. --written keeps only notes of a written
tune; --until keeps only notes that start before that time (and limits the
whole-file counts to it).

Pitch: 30 ms frames every 5 ms, normalised autocorrelation, lags for 180 Hz to
1600 Hz, the shortest local peak within 0.9 of the highest, parabolic
refinement. A frame is sounding when its RMS is within 40 dB of the loudest.

Per class:
  a. settled share: per note, the share of its frames (frame centre inside the
     note) whose pitch is within 30 cents of the note (median, quartiles).
  b. time to settle: from t0 until the pitch first stays within 30 cents for
     20 ms (4 frames); a note that never settles counts its whole duration.
  c. periodicity: the median autocorrelation peak per note, and the share of
     sounding frames under 0.7.
  d. note level: RMS in dB per note; standard deviation over notes; median
     difference between the first 20 ms and the whole note (attack dip or
     accent).
  e. written notes and pitch segments (runs of 25 ms or more on one rounded
     semitone, voiced frames only) whose centre is inside a note of the class.
"""
import json
import struct
import sys

import numpy as np

WIN = 0.030
HOP = 0.005
F_LO, F_HI = 180.0, 1600.0
CENTS = 30.0
SETTLE_FRAMES = 4  # 20 ms
SEG_MIN = 0.025
VOICED = 0.5
SOUND_DB = -40.0


def read_wav(path):
    """Mono float32 samples and the sample rate of a PCM or float WAV."""
    with open(path, "rb") as f:
        b = f.read()
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        raise SystemExit("not a RIFF/WAVE file: " + path)
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(b):
        cid = b[pos:pos + 4]
        size = struct.unpack("<I", b[pos + 4:pos + 8])[0]
        body = b[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise SystemExit("missing fmt or data chunk: " + path)
    tag, nch, sr, _, _, bits = fmt
    if tag == 3 and bits == 32:
        x = np.frombuffer(data, dtype="<f4").astype(np.float64)
    elif tag == 1 and bits == 16:
        x = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
    elif tag == 1 and bits == 24:
        raw = np.frombuffer(data[: len(data) // 3 * 3], dtype=np.uint8).reshape(-1, 3)
        v = raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8) | (raw[:, 2].astype(np.int32) << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float64) / (1 << 23)
    elif tag == 1 and bits == 32:
        x = np.frombuffer(data, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise SystemExit(f"unsupported WAV format tag {tag} bits {bits}")
    x = x[: len(x) // nch * nch].reshape(-1, nch).mean(axis=1)
    return x, sr


def track(x, sr):
    """Per frame: centre time, pitch in MIDI (nan if none), periodicity, rms."""
    n = int(round(WIN * sr))
    hop = int(round(HOP * sr))
    lo = int(np.floor(sr / F_HI))
    hi = int(np.ceil(sr / F_LO))
    nfr = max(0, (len(x) - n) // hop + 1)
    idx = np.arange(nfr)[:, None] * hop + np.arange(n)[None, :]
    fr = x[idx]
    nfft = 1 << int(np.ceil(np.log2(n + hi + 2)))
    spec = np.fft.rfft(fr, nfft, axis=1)
    acf = np.fft.irfft(spec * np.conj(spec), nfft, axis=1)[:, : hi + 2]
    c2 = np.concatenate([np.zeros((nfr, 1)), np.cumsum(fr * fr, axis=1)], axis=1)
    lags = np.arange(hi + 2)
    e1 = c2[:, n - lags]  # sum of x[0..n-l]^2
    e2 = c2[:, n][:, None] - c2[:, lags]  # sum of x[l..n]^2
    r = acf / np.sqrt(np.maximum(e1 * e2, 1e-30))
    rms = np.sqrt(c2[:, n] / n)
    midi = np.full(nfr, np.nan)
    per = np.zeros(nfr)
    for i in range(nfr):
        if rms[i] < 1e-9:
            continue
        seg = r[i, lo - 1:hi + 2]
        core = seg[1:-1]
        loc = np.where((core > seg[:-2]) & (core >= seg[2:]))[0]
        if len(loc) == 0:
            continue
        g = core[loc].max()
        k = loc[np.argmax(core[loc] >= 0.9 * g)]
        a, b, c = seg[k], seg[k + 1], seg[k + 2]
        den = a - 2 * b + c
        off = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
        lag = lo + k + off
        per[i] = b
        midi[i] = 69.0 + 12.0 * np.log2(sr / lag / 440.0)
    t = (np.arange(nfr) * hop + n / 2) / sr
    return t, midi, per, rms


def q3(v):
    if len(v) == 0:
        return "n/a"
    a = np.percentile(v, [25, 50, 75])
    return f"median {a[1]:.3f}  quartiles {a[0]:.3f} .. {a[2]:.3f}"


def segments(t, midi, per):
    """(centre time, duration) of runs on one rounded semitone, voiced frames."""
    out = []
    run = None  # [semitone, first index, last index]
    for i in range(len(t)):
        ok = per[i] >= VOICED and np.isfinite(midi[i])
        s = int(np.round(midi[i])) if ok else None
        if run is not None and s == run[0]:
            run[2] = i
            continue
        if run is not None:
            out.append(run)
        run = [s, i, i] if s is not None else None
    if run is not None:
        out.append(run)
    segs = []
    for s, a, b in out:
        dur = (b - a + 1) * HOP
        if dur >= SEG_MIN:
            segs.append(((t[a] + t[b]) / 2, dur))
    return segs


def report(name, notes, x, sr, t, midi, per, rms, segs, sound):
    print(f"--- {name}: {len(notes)} notes")
    if not notes:
        return
    settled, tsettle, perm, lev, att = [], [], [], [], []
    never = 0
    for n in notes:
        m = (t >= n["t0"]) & (t < n["t1"])
        if not m.any():
            continue
        dc = 100.0 * (midi[m] - n["midi"])
        ok = np.isfinite(dc) & (np.abs(dc) <= CENTS) & (per[m] >= VOICED)
        settled.append(ok.mean())
        perm.append(np.median(per[m]))
        run = 0
        found = None
        tm = t[m]
        for j, o in enumerate(ok):
            run = run + 1 if o else 0
            if run >= SETTLE_FRAMES:
                found = tm[j - SETTLE_FRAMES + 1] - n["t0"]
                break
        if found is None:
            never += 1
            found = n["t1"] - n["t0"]
        tsettle.append(max(found, 0.0))
        a, b = int(n["t0"] * sr), int(n["t1"] * sr)
        seg = x[a:b]
        if len(seg) < 8:
            continue
        r_all = np.sqrt(np.mean(seg ** 2))
        r_att = np.sqrt(np.mean(seg[: int(0.02 * sr)] ** 2))
        lev.append(20 * np.log10(max(r_all, 1e-9)))
        att.append(20 * np.log10(max(r_att, 1e-9)) - 20 * np.log10(max(r_all, 1e-9)))
    print(f"a. settled share (within {CENTS:.0f} cents): {q3(settled)}")
    ts = np.array(tsettle)
    print(f"b. time to settle: median {1000 * np.median(ts):.0f} ms, 90th percentile "
          f"{1000 * np.percentile(ts, 90):.0f} ms, never settled {never}/{len(ts)}")
    mask = np.zeros(len(t), dtype=bool)
    for n in notes:
        mask |= (t >= n["t0"]) & (t < n["t1"])
    s_in = mask & sound
    low = (per[s_in] < 0.7).mean() * 100 if s_in.any() else float("nan")
    print(f"c. periodicity: median of note medians {np.median(perm):.3f}; "
          f"sounding frames under 0.7: {low:.1f}% of {int(s_in.sum())}")
    print(f"d. level: std over notes {np.std(lev):.2f} dB (mean {np.mean(lev):.1f} dB); "
          f"median first-20-ms minus whole {np.median(att):+.2f} dB")
    nseg = sum(1 for c, _ in segs if any(n["t0"] <= c < n["t1"] for n in notes))
    nw = sum(1 for n in notes if n.get("written"))
    print(f"e. written notes {nw}, notes {len(notes)}, pitch segments inside them {nseg} "
          f"(ratio to notes {nseg / len(notes):.2f}"
          + (f", to written {nseg / nw:.2f}" if nw else "") + ")")


def main(argv):
    args = [a for a in argv[1:]]
    max_dur, until, only_w = 0.3, None, False
    pos = []
    i = 0
    while i < len(args):
        if args[i] == "--max-dur":
            max_dur = float(args[i + 1])
            i += 2
        elif args[i] == "--until":
            until = float(args[i + 1])
            i += 2
        elif args[i] == "--written":
            only_w = True
            i += 1
        else:
            pos.append(args[i])
            i += 1
    if len(pos) != 2:
        raise SystemExit(__doc__)
    x, sr = read_wav(pos[0])
    with open(pos[1]) as f:
        notes = json.load(f)
    if until is not None:
        notes = [n for n in notes if n["t0"] < until]
    if only_w:
        notes = [n for n in notes if n.get("written")]
    t, midi, per, rms = track(x, sr)
    top = rms.max() if len(rms) else 1.0
    sound = rms > top * 10 ** (SOUND_DB / 20)
    if until is not None:
        sound &= t < until
    segs = [s for s in segments(t, midi, per) if until is None or s[0] < until]
    fast = [n for n in notes if n["t1"] - n["t0"] < max_dur]
    slow = [n for n in notes if n["t1"] - n["t0"] >= max_dur]
    print(f"{pos[0]}: {len(x) / sr:.2f} s, {sr} Hz; notes {len(notes)} "
          f"(written {sum(1 for n in notes if n.get('written'))})")
    report(f"fast notes (< {max_dur} s)", fast, x, sr, t, midi, per, rms, segs, sound)
    report(f"slow notes (>= {max_dur} s)", slow, x, sr, t, midi, per, rms, segs, sound)
    allw = sum(1 for n in notes if n.get("written"))
    low = (per[sound] < 0.7).mean() * 100 if sound.any() else float("nan")
    print(f"--- whole file{'' if until is None else f' up to {until} s'}: written notes {allw}, "
          f"pitch segments {len(segs)}, sounding frames {int(sound.sum())}, "
          f"under 0.7: {low:.1f}%")


if __name__ == "__main__":
    main(sys.argv)
