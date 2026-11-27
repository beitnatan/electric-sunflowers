#!/usr/bin/env python3
"""Harmonic envelope of sung vowels: real singers against the engine voice.

  voice_target.py ref VOCADITO_DIR [--json OUT] [--min-pitch M] [--max-pitch M]
                  [--open-k K] [--open-f0 HZ]
  voice_target.py eng DIR... [--json OUT] [--target JSON]
                  [--open-k K] [--open-f0 HZ]

ref  measures the tracks of vocadito whose average pitch (MIDI) lies in
     [--min-pitch, --max-pitch] on their annotated notes (notesA1). Default:
     average pitch <= 55 (male range); with --min-pitch only, no upper limit
     (female range: --min-pitch 58 --open-k 2 --open-f0 450). The open-vowel
     rule (below) is stored in the target as open_k and open_f0, and so is
     the consonant prominence (cons_prom).
eng  measures engine stems directories (each holds lead.wav and notes.json,
     written by `engine --example stems`) and pools their frames; with
     --target prints E - T per band, J (RMS over 630 Hz .. 8 kHz) and the
     largest |E - T|.

Rows, in 1/3-octave bands:
  vowel H   harmonic power, dB re the harmonic power in 315-1000 Hz.
  whole     long-term spectrum of the whole stem (frames above 5% of the
            peak energy), dB re the sum over 315-1000 Hz, pooled by mean
            of the per-stem spectra.
  peak-flr  harmonic peak minus the median of the inter-harmonic bins, dB.
H1-H2 is the level of the first minus the second harmonic per frame.
open H1-H2 is its median over open-vowel frames: the strongest of the first
8 harmonics is harmonic K or higher (--open-k, default 3) and f0 <= F (--open-f0,
default 200 Hz). `eng --target` takes both from the target file. The pooled median is
confounded by the singers' f0 and vowel mix (the real tracks sing at a median
of 144 Hz, the engine lead at 192 Hz); this one compares like with like. The per-bin medians of the same frames
(open_h12_bins, f0 250-300, 300-350, 350-400 Hz) are compared bin by bin by
`eng --target` when the target holds them (the female target does). For
information, per f0 bin (100-150, 150-200 Hz): "slope", the regression slope
of harmonic level against log2 k below 1.5 kHz (dB/oct), and "H1 below
strongest", harmonic 1 minus the strongest harmonic below 1.5 kHz (dB).
Consonant prominence: the mean over the 5, 6.3 and 8 kHz bands of "whole"
minus "vowel H" (dB). Only sustained notes (0.5 s and longer, middle 60%) are
used. Needs numpy.
"""
import csv
import json
import struct
import sys

import numpy as np

C3 = np.array([200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000,
               2500, 3150, 4000, 5000, 6300, 8000, 10000.])
FIT = (C3 >= 630) & (C3 <= 8000)
M = 2048
HOP = 512
SOURCE = ("vocadito (Bittner et al., ISMIR 2021 late-breaking), Zenodo 5578807, "
          "CC BY 4.0; male-range tracks (average pitch <= MIDI 55); "
          "statistics only")
SOURCE_MIN = ("vocadito (Bittner et al., ISMIR 2021 late-breaking), Zenodo 5578807, "
              "CC BY 4.0; tracks with average pitch >= MIDI %g; "
              "statistics only")
PROM = (C3 == 5000) | (C3 == 6300) | (C3 == 8000)
OPEN_BINS = ((250, 300), (300, 350), (350, 400))  # f0 bins for open H1-H2
MIN_BIN_N = 50


def read_wav(path):
    """Mono float samples and the rate of a PCM 16/24 or float32 WAV."""
    b = open(path, "rb").read()
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
                x = np.frombuffer(b, dtype="<i2", count=sz // 2, offset=i + 8) / 32768.0
            elif bits == 24:
                a = np.frombuffer(b, dtype=np.uint8, count=sz // 3 * 3,
                                  offset=i + 8).reshape(-1, 3).astype(np.int32)
                x = ((a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)) ^ 0x800000) - 0x800000
                x = x / 8388608.0
            else:
                raise SystemExit("unsupported WAV format %s" % (fmt,))
            return x.reshape(-1, nch).mean(1), sr
        i += 8 + sz + (sz & 1)
    raise SystemExit("no data chunk in " + path)


def band(c):
    return c / 2 ** (1 / 6), c * 2 ** (1 / 6)


def analyse(x, sr, notes, min_dur=0.5):
    """notes: (t0, f0 Hz, duration). Per-stem sums and per-frame samples.
    Notes shorter than min_dur seconds are skipped."""
    w = np.hanning(M)
    ff = np.fft.rfftfreq(M, 1 / sr)
    df = ff[1]
    hb = np.zeros(len(C3))
    pv = [[] for _ in C3]
    h12 = []
    fr = []
    nfr = 0
    for (t0, f0n, d) in notes:
        if d < min_dur or f0n <= 0:
            continue
        a = int((t0 + 0.2 * d) * sr)
        e = int((t0 + 0.8 * d) * sr)
        for s in range(a, e - M, HOP):
            X = np.abs(np.fft.rfft(x[s:s + M] * w))
            if X.max() < 1e-5:
                continue
            best = None
            for c in range(-200, 201, 10):
                f = f0n * 2 ** (c / 1200)
                idx = np.round(np.arange(1, 9) * f / df).astype(int)
                sc = np.log(X[idx] + 1e-9).sum()
                if best is None or sc > best[0]:
                    best = (sc, f)
            f0 = best[1]
            K = int(min(11000, sr / 2 - 200) / f0)
            pk = np.zeros(K + 1)
            fl = np.zeros(K + 1)
            for k in range(1, K + 1):
                lo = int(np.floor(k * f0 * 0.97 / df))
                hi = int(np.ceil(k * f0 * 1.03 / df)) + 1
                pk[k] = X[lo:hi].max()
                lo = int((k + 0.3) * f0 / df)
                hi = int((k + 0.7) * f0 / df) + 1
                fl[k] = np.median(X[lo:hi])
            h12.append(20 * np.log10(pk[1] / pk[2]))
            kst = int(np.argmax(pk[1:9])) + 1
            kk = max(int(1500 / f0), 2)
            p = 20 * np.log10(pk[1:kk + 1] + 1e-12)
            slope = np.polyfit(np.log2(np.arange(1, kk + 1)), p, 1)[0]
            fr.append((f0, kst, h12[-1], slope, p[0] - p.max()))
            ks = np.arange(1, K + 1) * f0
            for j, c in enumerate(C3):
                lo, hi = band(c)
                m = (ks >= lo) & (ks < hi)
                if m.any():
                    hb[j] += (pk[1:][m] ** 2).sum()
                    pv[j].append(20 * np.log10(np.mean(pk[1:][m]) / np.mean(fl[1:][m])))
            nfr += 1
    return dict(hb=hb, nfr=nfr, h12=h12, pv=pv, fr=fr)


def whole(x, sr):
    N = 4096
    w = np.hanning(N)
    fr = np.lib.stride_tricks.sliding_window_view(x, N)[::N // 2]
    e = (fr ** 2).mean(1)
    fr = fr[e > 0.05 * e.max()]
    P = (np.abs(np.fft.rfft(fr * w, axis=1)) ** 2).mean(0)
    f = np.fft.rfftfreq(N, 1 / sr)
    return np.array([P[(f >= band(c)[0]) & (f < band(c)[1])].sum() for c in C3])


def pool(rs, ws, open_k=3, open_f0=200.0):
    """Rows over the pooled analyses `rs` and whole-stem spectra `ws`."""
    hb = sum(r["hb"] for r in rs)
    ref = hb[(C3 >= 315) & (C3 <= 1000)].sum()
    mid = (C3 >= 315) & (C3 <= 1000)
    wh = sum(w / w[mid].sum() for w in ws) / len(ws)
    h12 = np.concatenate([np.asarray(r["h12"], float) for r in rs])
    pk = []
    for j in range(len(C3)):
        v = np.concatenate([np.asarray(r["pv"][j], float) for r in rs])
        pk.append(float(np.median(v)) if len(v) else float("nan"))
    q1, med, q3 = np.percentile(h12, [25, 50, 75])
    fr = np.array([f for r in rs for f in r["fr"]], float).reshape(-1, 5)
    om = (fr[:, 1] >= open_k) & (fr[:, 0] <= open_f0)
    if om.any():
        oq = np.percentile(fr[om, 2], [25, 50, 75])
        open_h12 = {"q1": float(oq[0]), "median": float(oq[1]), "q3": float(oq[2]), "n": int(om.sum())}
    else:
        open_h12 = {"q1": float("nan"), "median": float("nan"), "q3": float("nan"), "n": 0}
    open_bins = {}
    for lo, hi in OPEN_BINS:
        m = om & (fr[:, 0] >= lo) & (fr[:, 0] < hi)
        open_bins["%d-%d" % (lo, hi)] = {
            "n": int(m.sum()),
            "median": float(np.median(fr[m, 2])) if m.any() else float("nan")}
    bins = {}
    for lo, hi in ((100, 150), (150, 200)):
        m = (fr[:, 0] >= lo) & (fr[:, 0] < hi)
        if m.sum():
            bins["%d-%d" % (lo, hi)] = {"n": int(m.sum()),
                                        "slope": float(np.median(fr[m, 3])),
                                        "h1_below_strongest": float(np.median(fr[m, 4]))}
    vh = 10 * np.log10(hb / ref + 1e-30)
    wl = 10 * np.log10(wh + 1e-30)
    return {
        "bands": [float(c) for c in C3],
        "vowel_h": vh.tolist(),
        "whole": wl.tolist(),
        "cons_prom": float(np.mean((wl - vh)[PROM])),
        "open_k": int(open_k),
        "open_f0": float(open_f0),
        "peak_flr": pk,
        "h1h2": {"q1": float(q1), "median": float(med), "q3": float(q3)},
        "open_h12": open_h12,
        "open_h12_bins": open_bins,
        "f0_bins": bins,
        "frames": int(sum(r["nfr"] for r in rs)),
    }


def show(name, r):
    print("==", name, "frames", r["frames"])
    print("band     " + " ".join("%5d" % c for c in r["bands"]))
    print("vowel H  " + " ".join("%5.0f" % v for v in r["vowel_h"]))
    print("whole    " + " ".join("%5.0f" % v for v in r["whole"]))
    print("peak-flr " + " ".join("%5.0f" % v for v in r["peak_flr"]))
    h = r["h1h2"]
    print("H1-H2 dB %.2f [%.2f %.2f]" % (h["median"], h["q1"], h["q3"]))
    o = r["open_h12"]
    print("open H1-H2 dB %.2f [%.2f %.2f] n=%d (open-k %d, open-f0 %g Hz)" % (
        o["median"], o["q1"], o["q3"], o["n"], r["open_k"], r["open_f0"]))
    print("consonant prominence (mean of whole - vowel H at 5, 6.3, 8 kHz) %.2f dB" % r["cons_prom"])
    for k, v in r["f0_bins"].items():
        print("f0 %s Hz n=%d  slope %.1f dB/oct  H1 below strongest %.1f dB" % (k, v["n"], v["slope"], v["h1_below_strongest"]))


def measure_ref(d, min_pitch=None, max_pitch=55.0, open_k=3, open_f0=200.0):
    meta = list(csv.DictReader(open(d + "/vocadito_metadata.csv")))
    rs, ws = [], []
    for m in meta:
        ap = float(m["average_pitch"])
        if ap > max_pitch or (min_pitch is not None and ap < min_pitch):
            continue
        i = m["track_id"]
        x, sr = read_wav("%s/Audio/vocadito_%s.wav" % (d, i))
        path = "%s/Annotations/Notes/vocadito_%s_notesA1.csv" % (d, i)
        notes = [tuple(map(float, l.split(","))) for l in open(path) if l.strip()]
        rs.append(analyse(x, sr, notes))
        ws.append(whole(x, sr))
    r = pool(rs, ws, open_k, open_f0)
    r["n_tracks"] = len(rs)
    return r


def measure_eng(dirs, open_k=3, open_f0=200.0):
    rs, ws = [], []
    for d in dirs:
        x, sr = read_wav(d + "/lead.wav")
        n = json.load(open(d + "/notes.json"))
        notes = [(v["t0"], 440 * 2 ** ((v["midi"] - 69) / 12), v["t1"] - v["t0"]) for v in n]
        rs.append(analyse(x, sr, notes))
        ws.append(whole(x, sr))
    return pool(rs, ws, open_k, open_f0)


def compare(e, t):
    """E - T over the fit bands (630 Hz .. 8 kHz), J and the largest |E - T|."""
    diff = np.array(e["vowel_h"]) - np.array(t["vowel_h"])
    d = diff[FIT]
    print("band     " + " ".join("%5d" % c for c in C3[FIT]))
    print("E - T    " + " ".join("%5.1f" % v for v in d))
    j = float(np.sqrt(np.mean(d ** 2)))
    mx = float(np.max(np.abs(d)))
    print("J %.2f dB   max |E - T| %.2f dB" % (j, mx))
    j1 = float(np.sqrt(np.mean(diff[(C3 >= 630) & (C3 <= 3150)] ** 2)))
    print("J1 (630 Hz .. 3.15 kHz) %.2f dB" % j1)
    if "open_h12" in t and not np.isnan(t["open_h12"]["median"]):
        print("open H1-H2 E - T %.2f dB" % (e["open_h12"]["median"] - t["open_h12"]["median"]))
    eb, tb = e.get("open_h12_bins"), t.get("open_h12_bins")
    if eb and tb:
        parts, vals = [], []
        for k in tb:
            d = eb[k]["median"] - tb[k]["median"]
            parts.append("%s Hz %+.2f (n %d / %d)" % (k, d, eb[k]["n"], tb[k]["n"]))
            if eb[k]["n"] >= MIN_BIN_N and tb[k]["n"] >= MIN_BIN_N:
                vals.append(d)
        print("open H1-H2 per bin E - T: " + "; ".join(parts))
        if vals:
            print("open H1-H2 per bin mean E - T %.2f dB (%d bins with n >= %d on both sides)"
                  % (float(np.mean(vals)), len(vals), MIN_BIN_N))
    if "cons_prom" in t:
        print("consonant prominence E %.2f  T %.2f dB" % (e["cons_prom"], t["cons_prom"]))
    return j, mx


def main(argv):
    if len(argv) < 3 or argv[1] not in ("ref", "eng"):
        raise SystemExit(__doc__)
    out = target = None
    min_pitch, max_pitch = None, None
    open_k, open_f0 = None, None
    pos = []
    a = argv[2:]
    i = 0
    while i < len(a):
        if a[i] == "--json":
            out = a[i + 1]
            i += 2
        elif a[i] == "--target":
            target = a[i + 1]
            i += 2
        elif a[i] == "--min-pitch":
            min_pitch = float(a[i + 1])
            i += 2
        elif a[i] == "--max-pitch":
            max_pitch = float(a[i + 1])
            i += 2
        elif a[i] == "--open-k":
            open_k = int(a[i + 1])
            i += 2
        elif a[i] == "--open-f0":
            open_f0 = float(a[i + 1])
            i += 2
        else:
            pos.append(a[i])
            i += 1
    if argv[1] == "ref":
        if max_pitch is None:
            max_pitch = 55.0 if min_pitch is None else float("inf")
        r = measure_ref(pos[0], min_pitch, max_pitch, open_k or 3,
                        200.0 if open_f0 is None else open_f0)
        r["T"] = r["vowel_h"]
        r["source"] = SOURCE if min_pitch is None else SOURCE_MIN % min_pitch
        show("vocadito %s, %d tracks" % ("male-range" if min_pitch is None else
                                         "average pitch >= %g" % min_pitch,
                                         r["n_tracks"]), r)
    else:
        t = json.load(open(target)) if target else {}
        ok = open_k if open_k is not None else t.get("open_k", 3)
        of = open_f0 if open_f0 is not None else t.get("open_f0", 200.0)
        r = measure_eng(pos, ok, of)
        show("engine " + " + ".join(pos), r)
        if target:
            r["j"], r["max_abs"] = compare(r, t)
    if out:
        json.dump(r, open(out, "w"), indent=1)
        open(out, "a").write("\n")


if __name__ == "__main__":
    main(sys.argv)
