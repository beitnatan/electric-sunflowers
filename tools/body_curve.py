#!/usr/bin/env python3
"""Fit one row of BODY_CURVES (crates/instruments/src/body.rs) to a target
long-term spectrum. numpy only. Generalises tools/guitar_curve.py, which stays
as a thin wrapper (--body guitar --norm 100 400).

Measure: x = mean of the channels; frames of 8192 samples, Hann window, hop
4096; keep frames whose mean square exceeds 5% of the largest; P = mean power
spectrum of the kept frames. L_i = 10 log10 (power of P in
[c_i 2^(-1/6), c_i 2^(1/6))) minus 10 log10 (power in [LO, HI) Hz), with
c_i = 80 * 2^(i/12), i = 0..85. Target T = median of L over reference files;
engine E = mean of L over the engine's guitar stems (the gate seeds).

Defaults per body (--body):
  guitar: norm 100 400 Hz; first pass clamp(T - E, -6, +36) in all bands;
          accept when max |T - E| <= 3 dB for 100 Hz .. 8 kHz.
  violin: norm 300 3000 Hz; first pass clamp(T - E, -6, +20) for bands with
          c >= 250 Hz, 0 below (the reference holds only room noise there);
          accept when max |T - E| <= 3 dB for 300 Hz .. 10 kHz.
New row = old + d, peak moved to 0.0, rounded to 0.1 dB. Later passes
(--residual) add 0.7 * clamp(T - E, -12, 12) in the bands the first pass
touches.

usage:
  body_curve.py [--body B] [--norm LO HI] target OUT.json WAV...
  body_curve.py [--body B] [--norm LO HI] engine OUT.json STEM_WAV...
  body_curve.py [--body B] row TARGET.json ENGINE.json [--residual]
  body_curve.py [--body B] check TARGET.json ENGINE.json
  body_curve.py [--body B] cross TARGET.json STEM_WAV...   (RMS, worst band)
  body_curve.py [--body B] show   (print the row now in body.rs)
"""
import json, os, re, struct, sys
import numpy as np

PER = 12
LO = 80.0
NB = 86
C = LO * 2 ** (np.arange(NB) / PER)
BODY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "crates", "instruments", "src", "body.rs")


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


NORM = (100.0, 400.0)
BODY_NAME = "guitar"
PARAMS = {
    "guitar": dict(norm=(100.0, 400.0), clip=(-6.0, 36.0), fmin=0.0, acc=(100.0, 8000.0), idx=0),
    "violin": dict(norm=(300.0, 3000.0), clip=(-6.0, 20.0), fmin=250.0, acc=(300.0, 10000.0), idx=2),
}


def ltas(x, sr, N=8192):
    w = np.hanning(N)
    fr = np.lib.stride_tricks.sliding_window_view(x, N)[::N // 2]
    e = (fr ** 2).mean(1)
    fr = fr[e > 0.05 * e.max()]
    P = (np.abs(np.fft.rfft(fr * w, axis=1)) ** 2).mean(0)
    f = np.fft.rfftfreq(N, 1 / sr)
    out = np.array([10 * np.log10(P[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-30)
                    for c in C])
    ref = 10 * np.log10(P[(f >= NORM[0]) & (f < NORM[1])].sum())
    return out - ref


def measure(paths):
    return np.array([ltas(*rd(p)) for p in paths])


def read_row():
    s = open(BODY).read()
    m = re.search(r"// %s\s*\[(.*?)\]" % BODY_NAME.capitalize(), s, re.S)
    v = [float(t) for t in m.group(1).replace("\n", " ").split(",") if t.strip()]
    assert len(v) == NB
    return np.array(v)


def fmt_row(row):
    toks = ["%.1f," % v for v in row]
    lines, cur = [], "       "
    for t in toks:
        if len(cur) + 1 + len(t) > 100:
            lines.append(cur)
            cur = "       "
        cur += " " + t
    lines.append(cur)
    return "\n".join(["    [", *(l for l in lines), "    ],"])


def load(p):
    return json.load(open(p))


def residual(t, e):
    return np.array(t["med"]) - np.array(e["mean"])


def main(a):
    global NORM, BODY_NAME
    a = list(a)
    if "--body" in a:
        k = a.index("--body")
        BODY_NAME = a[k + 1]
        del a[k:k + 2]
    P = PARAMS[BODY_NAME]
    NORM = P["norm"]
    if "--norm" in a:
        k = a.index("--norm")
        NORM = (float(a[k + 1]), float(a[k + 2]))
        del a[k:k + 3]
    if len(a) < 2 and not (a and a[0] == "show"):
        raise SystemExit(__doc__)
    cmd = a[0]
    lo_clip, hi_clip = P["clip"]
    touch = C >= P["fmin"]
    if cmd == "target":
        R = measure(a[2:])
        json.dump({"c": C.tolist(), "med": np.median(R, 0).tolist(),
                   "q1": np.percentile(R, 25, 0).tolist(),
                   "q3": np.percentile(R, 75, 0).tolist(), "n": len(R),
                   "norm": list(NORM)}, open(a[1], "w"))
    elif cmd == "engine":
        R = measure(a[2:])
        json.dump({"c": C.tolist(), "mean": R.mean(0).tolist(), "n": len(R)}, open(a[1], "w"))
    elif cmd == "row":
        r = residual(load(a[1]), load(a[2]))
        old = read_row()
        if "--residual" in a:
            d = 0.7 * np.clip(r, -12, 12)
        else:
            d = np.clip(r, lo_clip, hi_clip)
        new = old + np.where(touch, d, 0.0)
        new = np.round(new - new.max(), 1)
        print(fmt_row(new))
    elif cmd == "show":
        print(fmt_row(read_row()))
    elif cmd == "cross":
        t = load(a[1])
        R = measure(a[2:]).mean(0) if len(a) > 2 else None
        r = np.array(t["med"]) - R
        lo, hi = P["acc"]
        band = (C >= lo) & (C <= hi)
        k = np.argmax(np.where(band, np.abs(r), -1))
        print("RMS %.2f dB over %.0f..%.0f Hz; worst %.0f Hz %+.1f dB"
              % (np.sqrt((r[band] ** 2).mean()), lo, hi, C[k], r[k]))
    elif cmd == "check":
        r = residual(load(a[1]), load(a[2]))
        lo, hi = P["acc"]
        band = (C >= lo) & (C <= hi)
        for i in range(0, NB, 3):
            print("%7.0f  %+6.1f" % (C[i], r[i]))
        k = np.argmax(np.where(band, np.abs(r), -1))
        print("worst %.0f Hz %+.1f dB" % (C[k], r[k]))
        ok = abs(r[k]) <= 3
        print("PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
