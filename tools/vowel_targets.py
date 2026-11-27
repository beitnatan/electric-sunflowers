#!/usr/bin/env python3
"""Measured F1-F3 of sung male vowels: VocalSet long tones (a e i o u).

  vowel_targets.py VOCALSET_MALE_DIR [--json OUT] [--max-f0 300]

DIR holds files m<N>_long_{straight,forte}_<v>.wav (male singers 1-11 of
VocalSet, Zenodo 1193957, CC BY 4.0; statistics only). Per file: resample to
10 kHz, track f0 by autocorrelation, keep 40 ms frames (hop 10 ms) in the
steady middle 60% whose f0 is within 3% of the file median and at most
--max-f0 Hz (LPC is unreliable above), LPC order 12 on the pre-emphasised
frame, roots with bandwidth under 400 Hz, F1 > 200 Hz. The first three
formants are taken when the root count and spacing make sense (F1 < 1000,
F2 > F1 + 250, F3 > F2 + 300). File value: median over frames. Vowel value:
median over files (singer-balanced: one value per file), with sd over files
and counts, overall and per f0 bin. Needs numpy, scipy.
"""
import glob
import json
import os
import re
import sys

import numpy as np
from scipy.linalg import solve_toeplitz
from scipy.signal import resample_poly

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_target as vt  # noqa: E402

SR = 10000
M = 400
HOP = 100
ORDER = 12
ENV = False  # True: LPC on the harmonic-peak envelope (cross-check)
BINS = ((0, 150), (150, 200), (200, 300))
# VocalSet sings Italian vowels; ARPAbet closest.
ARPA = {"a": "aa", "e": "eh", "i": "iy", "o": "ow", "u": "uw"}


def f0_of(fr, lo=70, hi=400):
    fr = fr - fr.mean()
    ac = np.correlate(fr, fr, "full")[len(fr) - 1:]
    a, b = int(SR / hi), int(SR / lo)
    if ac[0] <= 0:
        return None
    k = a + int(np.argmax(ac[a:b]))
    return SR / k if ac[k] / ac[0] > 0.6 else None


def env_autocorr(x, f0):
    """Autocorrelation of the spectral envelope drawn through the harmonic
    peaks (linear in dB between harmonics): LPC on this envelope is not
    pulled to single harmonics, which matters when f0 is near F1."""
    N = 4096
    X = np.abs(np.fft.rfft(x * np.hanning(len(x)), N))
    fr = np.arange(len(X)) * SR / N
    ks = np.arange(1, int(4800 / f0) + 1)
    pk, lv = [], []
    for k in ks:
        m = (fr > (k - 0.3) * f0) & (fr < (k + 0.3) * f0)
        pk.append(k * f0)
        lv.append(np.log(X[m].max() + 1e-9))
    env = np.interp(fr, [0] + pk + [SR / 2], [lv[0]] + lv + [lv[-1]])
    P = np.exp(2 * env)
    return np.fft.irfft(P)[:ORDER + 1]


def formants(fr, f0=None):
    x = np.append(fr[0], fr[1:] - 0.97 * fr[:-1])
    if ENV and f0:
        r = env_autocorr(x, f0)
    else:
        x = x * np.hamming(len(x))
        r = np.correlate(x, x, "full")[len(x) - 1:len(x) + ORDER]
    if r[0] <= 0:
        return None
    try:
        a = solve_toeplitz(r[:ORDER], r[1:ORDER + 1])
    except Exception:
        return None
    rts = np.roots(np.concatenate([[1.0], -a]))
    rts = rts[np.imag(rts) > 0.01]
    f = np.angle(rts) * SR / (2 * np.pi)
    bw = -np.log(np.abs(rts)) * SR / np.pi
    ok = (f > 200) & (f < 4000) & (bw < 400)
    f = np.sort(f[ok])
    if len(f) < 3 or f[0] > 1000:
        return None
    # keep the first three that are spaced like formants
    out = [f[0]]
    for v in f[1:]:
        if len(out) == 1 and v > out[0] + 250:
            out.append(v)
        elif len(out) == 2 and v > out[1] + 300:
            out.append(v)
            break
    return out if len(out) == 3 else None


def one(path, max_f0):
    x, sr = vt.read_wav(path)
    g = np.gcd(SR, int(sr))
    x = resample_poly(x, SR // g, int(sr) // g)
    n = len(x)
    seg = x[int(0.2 * n):int(0.8 * n)]
    fr, f0s = [], []
    for s in range(0, len(seg) - M, HOP):
        f = seg[s:s + M]
        if np.abs(f).max() < 0.02:
            continue
        p = f0_of(f)
        if p:
            fr.append(f)
            f0s.append(p)
    if len(fr) < 10:
        return None
    med = np.median(f0s)
    if med > max_f0:
        return None
    F = []
    for f, p in zip(fr, f0s):
        if abs(p / med - 1) < 0.03:
            r = formants(f, p)
            if r:
                F.append(r)
    if len(F) < 10:
        return None
    return float(med), np.median(np.array(F), 0), len(F)


def main(argv):
    out, max_f0, d = None, 300.0, None
    i = 1
    while i < len(argv):
        if argv[i] == "--json":
            out = argv[i + 1]; i += 2
        elif argv[i] == "--max-f0":
            max_f0 = float(argv[i + 1]); i += 2
        else:
            d = argv[i]; i += 1
    if not d:
        raise SystemExit(__doc__)
    global ENV
    env_med = {}
    ENV = True
    for p in sorted(glob.glob(os.path.join(d, "m*_long_*_?.wav"))):
        m = re.match(r"(m\d+)_long_(\w+)_(\w)\.wav", os.path.basename(p))
        r = one(p, max_f0)
        if r:
            env_med.setdefault(ARPA[m.group(3)], []).append(r[1])
    ENV = False
    rows = []
    for p in sorted(glob.glob(os.path.join(d, "m*_long_*_?.wav"))):
        m = re.match(r"(m\d+)_long_(\w+)_(\w)\.wav", os.path.basename(p))
        r = one(p, max_f0)
        if r:
            rows.append((m.group(1), m.group(3), r[0], r[1], r[2]))
    res = {"source": "VocalSet (Cooper/Wilkins, Zenodo 1193957, CC BY 4.0); male singers 1-11, "
                     "long tones straight+forte; LPC order 12, 40 ms frames, f0 <= %g Hz; "
                     "statistics only" % max_f0, "vowels": {}}
    for v, arpa in ARPA.items():
        vr = [r for r in rows if r[1] == v]
        if not vr:
            continue
        F = np.array([r[3] for r in vr])
        e = {"files": len(vr), "singers": len({r[0] for r in vr}),
             "frames": int(sum(r[4] for r in vr)),
             "median": [round(float(x), 0) for x in np.median(F, 0)],
             "sd": [round(float(x), 0) for x in F.std(0)],
             "median_env_lpc": [round(float(x), 0) for x in np.median(env_med[arpa], 0)],
             "median_f0": round(float(np.median([r[2] for r in vr])), 0), "bins": {}}
        for lo, hi in BINS:
            b = [r for r in vr if lo <= r[2] < hi]
            if b:
                Fb = np.array([r[3] for r in b])
                e["bins"]["%d-%d" % (lo, hi)] = {"files": len(b),
                    "median": [round(float(x), 0) for x in np.median(Fb, 0)]}
        res["vowels"][arpa] = e
        print("%-3s n=%2d sing=%2d f0=%3.0f  F1 %4.0f (sd %3.0f)  F2 %4.0f (sd %3.0f)  F3 %4.0f (sd %3.0f)" % (
            arpa, e["files"], e["singers"], e["median_f0"], *[x for k in range(3) for x in (e["median"][k], e["sd"][k])]))
        for k, b in e["bins"].items():
            print("      f0 %-8s n=%2d  %s" % (k, b["files"], b["median"]))
    if out:
        json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv)
