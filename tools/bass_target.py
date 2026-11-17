#!/usr/bin/env python3
"""Target statistics for the string bass, and comparison with the engine.
numpy only.

Reference: University of Iowa Musical Instrument Samples, double bass,
pizzicato (free for any use), mono 44.1 kHz WAV, several notes per file.
Two statistics:

 spectrum  Notes are found by the energy rule: 5 ms frames of mean square,
           smoothed over 4 frames; a note starts where the energy crosses 2%
           of the file's maximum and ends where it falls below 0.1% of the
           maximum. Each note's first 0.5 s, from 200 samples before the
           onset, is normalised to unit RMS, Hann windowed and transformed;
           the power spectra are averaged with equal weight per note (mf and
           ff pooled). Bands: 1/3-octave centres 40 Hz .. 6.3 kHz, each the
           power in [c 2^(-1/6), c 2^(1/6)), in dB relative to the power in
           40..400 Hz.
 envelope  For every note: time from onset to the energy peak, and to 10 and
           20 dB below the peak (5 ms frames, energy). Medians and quartiles
           over the mf notes.

usage:
  bass_target.py ref DIR --json OUT.json   (DIR holds Bass.pizz.*.wav)
  bass_target.py notes DIR                 (isolated engine notes: envelope)
  bass_target.py eng BASS_WAV... --target JSON [--lo HZ --hi HZ]
        long-term spectrum of engine bass stems (frames of 8192, Hann, hop
        4096, frames above 5% of the loudest, mean power over the files, same
        bands and reference) and E - T per band; RMS over --lo..--hi
        (default 200..3150 Hz)
"""
import glob, json, os, struct, sys
import numpy as np

C = np.array([40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000,
              1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300], dtype=float)
NORM = (40.0, 400.0)
SOURCE = "University of Iowa Musical Instrument Samples, double bass pizzicato mf and ff; statistics only"


def rd(p):
    b = open(p, "rb").read()
    i = 12
    fmt = None
    while i < len(b) - 8:
        cid = b[i:i + 4]
        sz = struct.unpack_from("<I", b, i + 4)[0]
        if cid == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", b, i + 8)
        if cid == b"data":
            tag, nch, sr, _, _, bits = fmt
            if tag == 3:
                x = np.frombuffer(b, dtype="<f4", count=sz // 4, offset=i + 8).astype(float)
            elif bits == 16:
                x = np.frombuffer(b, dtype="<i2", count=sz // 2, offset=i + 8) / 32768.
            elif bits == 24:
                a = np.frombuffer(b, dtype=np.uint8, count=sz // 3 * 3, offset=i + 8)
                a = a.reshape(-1, 3).astype(np.int32)
                x = ((a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)) ^ 0x800000) - 0x800000
                x = x / 8388608.
            else:
                raise SystemExit("fmt %s" % (fmt,))
            return x.reshape(-1, nch).mean(1), sr
        i += 8 + sz + (sz & 1)
    raise SystemExit("no data chunk in " + p)


def bands(P, f):
    out = np.array([10 * np.log10(P[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-30)
                    for c in C])
    return out - 10 * np.log10(P[(f >= NORM[0]) & (f < NORM[1])].sum())


def find_notes(x, sr):
    """List of (onset sample, peak s, t-10 dB s, t-20 dB s) per note."""
    h = int(0.005 * sr)
    e = (x[:len(x) // h * h].reshape(-1, h) ** 2).mean(1)
    e = np.convolve(e, np.ones(4) / 4, "same")
    th = 0.02 * e.max()
    out = []
    i = 0
    while i < len(e):
        if e[i] > th and (i == 0 or e[i - 1] <= th):
            j = i
            while j < len(e) and e[j] > th * 0.05:
                j += 1
            seg = e[i:j]
            k = int(np.argmax(seg))
            r = seg[k:] / seg[k]
            b10 = np.where(r < 0.1)[0]
            b20 = np.where(r < 0.01)[0]
            if len(b20) and len(seg) > 40:
                out.append((i * h, k * 0.005, b10[0] * 0.005, b20[0] * 0.005))
            i = j
        i += 1
    return out


def note_spectrum(x, sr, on):
    a = max(on - 200, 0)
    seg = x[a:a + int(0.5 * sr)]
    seg = seg / np.sqrt((seg ** 2).mean())
    P = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    return P, np.fft.rfftfreq(len(seg), 1 / sr)


def cmd_ref(d, out):
    S, E = [], []
    for p in sorted(glob.glob(os.path.join(d, "*.wav"))):
        x, sr = rd(p)
        mf = ".mf." in os.path.basename(p)
        for on, pk, t10, t20 in find_notes(x, sr):
            P, f = note_spectrum(x, sr, on)
            S.append(P)
            if mf:
                E.append((pk, t10, t20))
    n = min(len(s) for s in S)
    P = np.mean([s[:n] for s in S], 0)
    f = np.fft.rfftfreq(2 * (n - 1), 1 / sr)
    spec = bands(P, f)
    E = np.array(E)
    env = {"n": len(E),
           "peak_s": [float(np.percentile(E[:, 0], q)) for q in (25, 50, 75)],
           "t10_s": [float(np.percentile(E[:, 1], q)) for q in (25, 50, 75)],
           "t20_s": [float(np.percentile(E[:, 2], q)) for q in (25, 50, 75)]}
    json.dump({"c": C.tolist(), "spec": spec.tolist(), "norm": list(NORM), "n_spec": len(S),
               "env": env, "source": SOURCE}, open(out, "w"))
    print("notes for spectrum %d (mf and ff); envelope notes (mf) %d" % (len(S), len(E)))
    for k in ("peak_s", "t10_s", "t20_s"):
        print("%-7s median %.3f  quartiles %.3f %.3f" % (k, env[k][1], env[k][0], env[k][2]))


def cmd_notes(d):
    R = []
    for p in sorted(glob.glob(os.path.join(d, "*.wav"))):
        x, sr = rd(p)
        n = find_notes(x, sr)
        if not n:
            print("%s: no note found" % os.path.basename(p))
            continue
        _, pk, t10, t20 = n[0]
        R.append((pk, t10, t20))
        print("%-24s peak %.3f s  -10 dB %.3f s  -20 dB %.3f s" % (os.path.basename(p), pk, t10, t20))
    R = np.array(R)
    print("median: peak %.3f s  -10 dB %.3f s  -20 dB %.3f s" % tuple(np.median(R, 0)))


def eng_ltas(x, N=8192):
    fr = np.lib.stride_tricks.sliding_window_view(x, N)[::N // 2]
    e = (fr ** 2).mean(1)
    fr = fr[e > 0.05 * e.max()]
    return (np.abs(np.fft.rfft(fr * np.hanning(N), axis=1)) ** 2).mean(0)


def cmd_eng(files, tj, lo, hi):
    t = json.load(open(tj))
    Ps = []
    for p in files:
        x, sr = rd(p)
        Ps.append(eng_ltas(x))
    E = bands(np.mean(Ps, 0), np.fft.rfftfreq(8192, 1 / sr))
    r = E - np.array(t["spec"])
    for c, v in zip(C, r):
        print("%7.0f  %+6.1f" % (c, v))
    m = (C >= lo) & (C <= hi)
    print("RMS of E - T over %.0f..%.0f Hz: %.2f dB" % (lo, hi, np.sqrt((r[m] ** 2).mean())))


def main(a):
    if len(a) < 2:
        raise SystemExit(__doc__)
    if a[0] == "ref" and "--json" in a:
        cmd_ref(a[1], a[a.index("--json") + 1])
    elif a[0] == "notes":
        cmd_notes(a[1])
    elif a[0] == "eng" and "--target" in a:
        k = a.index("--target")
        lo, hi = 200.0, 3150.0
        rest = a[1:k] + a[k + 2:]
        if "--lo" in rest:
            i = rest.index("--lo")
            lo = float(rest[i + 1])
            del rest[i:i + 2]
        if "--hi" in rest:
            i = rest.index("--hi")
            hi = float(rest[i + 1])
            del rest[i:i + 2]
        cmd_eng(rest, a[k + 1], lo, hi)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
