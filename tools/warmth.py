#!/usr/bin/env python3
"""Warmth and timbre of the engine voice on the TTS word list.

  warmth.py DIR [DIR...] [--words tools/ttswords.json] [--worst N] [--json OUT]

Each DIR is a `stems` render of out/wordlist/<voice>.json (lead.wav,
phones.json, plan.json); the directory's name is the voice (baritone, tenor,
alto, soprano). Prints one table row per voice and, on the last lines, the two
scores. Needs numpy, scipy, soundfile. Formulas: docs/voice-wordlist.md.

Vowel frames: for each sung note (one syllable), the part of the note that is
not inside a planned consonant segment (plan.json; 50 ms margin each side),
longest gap, middle 60% (voice_target.analyse). The harmonic bands, H1-H2 and
the open-vowel rule are voice_target's. The reference is the vocadito target
JSON (male for baritone and tenor, female for alto and soprano); its
statistics are enough, no audio.
"""
import json
import os
import sys

import numpy as np
import soundfile as sf
from scipy.linalg import solve_toeplitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_target as vt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
C3 = vt.C3
LOW = (C3 >= 200) & (C3 <= 800)
HIGH = (C3 >= 2000) & (C3 <= 5000)
TOP = (C3 >= 5000) & (C3 <= 8000)
ALLB = C3 <= 10000
FEMALE = ("alto", "soprano")
MARGIN = 0.05
MIN_VOWEL = 0.25  # s; shorter vowel gaps are skipped

# Scales of the score formula: a component scores 100 at zero distance and 0
# at its scale or more, linear between.
SCALES = {"tilt": 10.0, "centroid": 0.5, "h12": 8.0, "share": 10.0,
          "J": 10.0, "peak_flr": 12.0, "formant": 0.30}

# Hillenbrand et al. 1995, mean F1/F2 (Hz) at steady state; values as
# remembered, rounded.
HILL = {
    "m": {"iy": (342, 2322), "ih": (427, 2034), "eh": (580, 1799), "ae": (588, 1952),
          "aa": (768, 1333), "ao": (652, 997), "uh": (469, 1122), "uw": (378, 997),
          "ah": (623, 1200), "er": (474, 1379), "ey": (476, 2089), "ow": (497, 910)},
    "f": {"iy": (437, 2761), "ih": (483, 2365), "eh": (731, 2058), "ae": (669, 2349),
          "aa": (936, 1551), "ao": (781, 1136), "uh": (519, 1225), "uw": (459, 1105),
          "ah": (753, 1426), "er": (523, 1588), "ey": (536, 2530), "ow": (555, 1035)},
}
HVD = {"heed": "iy", "hid": "ih", "head": "eh", "had": "ae", "hod": "aa",
       "hawed": "ao", "hood": "uh", "who'd": "uw", "hud": "ah", "heard": "er",
       "hayed": "ey", "hoed": "ow"}
VOWELS = set(vt.__dict__.get("_x", ())) | {"iy", "ih", "eh", "ae", "aa", "ao", "oh", "uh", "uw",
                                           "ah", "ax", "er", "ey", "ow", "ay", "aw", "oy"}


def score(d, scale):
    return 100.0 * float(np.clip(1.0 - abs(d) / scale, 0.0, 1.0))


def vowel_gap(t0, t1, plan):
    """Longest part of [t0, t1] outside the planned consonant segments."""
    cuts = sorted((max(a - MARGIN, t0), min(b + MARGIN, t1))
                  for _, a, b in plan if b + MARGIN > t0 and a - MARGIN < t1)
    gaps, cur = [], t0
    for a, b in cuts:
        if a > cur:
            gaps.append((cur, a))
        cur = max(cur, b)
    if cur < t1:
        gaps.append((cur, t1))
    return max(gaps, key=lambda g: g[1] - g[0]) if gaps else None


def lpc_formants(x, sr, order=None):
    """F1, F2 (Hz) of one frame by autocorrelation LPC; None if not found."""
    order = order or int(2 + sr / 1000)
    x = np.append(x[0], x[1:] - 0.97 * x[:-1]) * np.hamming(len(x))
    r = np.correlate(x, x, "full")[len(x) - 1:len(x) + order]
    if r[0] <= 0:
        return None
    try:
        a = solve_toeplitz(r[:order], r[1:order + 1])
    except Exception:
        return None
    rts = np.roots(np.concatenate([[1.0], -a]))
    rts = rts[np.imag(rts) > 0.01]
    f = np.angle(rts) * sr / (2 * np.pi)
    bw = -np.log(np.abs(rts)) * sr / np.pi
    ok = (f > 150) & (f < 3800) & (bw < 500)
    f = np.sort(f[ok])
    return (f[0], f[1]) if len(f) >= 2 else None


def noise_stats(x, sr, f0n, t0, t1):
    """Per-frame HNR (dB, normalised autocorrelation peak) and the spectral
    flatness (dB) of the inter-harmonic bins below 8 kHz."""
    M = 2048
    hnr, flat = [], []
    w = np.hanning(M)
    lo = int(sr / (f0n * 2 ** (3 / 12)))
    hi = int(sr / (f0n * 2 ** (-3 / 12)))
    for s in range(int(t0 * sr), int(t1 * sr) - M, 512):
        fr = x[s:s + M]
        if np.abs(fr).max() < 1e-5:
            continue
        fr0 = fr - fr.mean()
        ac = np.correlate(fr0[:M // 2 + hi], fr0[:M // 2], "valid")
        e = (fr0[:M // 2] ** 2).sum()
        if e <= 0 or len(ac) <= hi:
            continue
        r = np.clip(ac[lo:hi + 1].max() / e, 1e-3, 0.999)
        hnr.append(10 * np.log10(r / (1 - r)))
        X = np.abs(np.fft.rfft(fr * w)) ** 2
        ff = np.fft.rfftfreq(M, 1 / sr)
        ph = ff / f0n
        m = (ff < 8000) & (ph > 1.5) & (np.abs(ph - np.round(ph)) > 0.3)
        if m.sum() > 20:
            p = X[m] + 1e-20
            flat.append(10 * np.log10(np.exp(np.mean(np.log(p))) / np.mean(p)))
    return hnr, flat


def row_from_hb(hb, open_med, peak_flr):
    """Warmth measures from harmonic band powers (linear)."""
    tilt = 10 * np.log10(hb[LOW].sum() / hb[HIGH].sum())
    cen = float((C3[ALLB] * hb[ALLB]).sum() / hb[ALLB].sum())
    share = float(hb[TOP].sum() / hb[ALLB].sum())
    return {"tilt": float(tilt), "centroid": cen, "share": share,
            "h12": float(open_med), "peak_flr": float(peak_flr)}


def ref_row(t):
    hb = 10 ** (np.array(t["vowel_h"]) / 10)
    pf = np.array(t["peak_flr"], float)
    pf = float(np.nanmean(pf[(C3 >= 200) & (C3 <= 5000)]))
    return row_from_hb(hb, t["open_h12"]["median"], pf)


def peak_flr_of(r):
    pf = np.array(r["peak_flr"], float)
    return float(np.nanmean(pf[(C3 >= 200) & (C3 <= 5000)]))


def j_of(vh, t):
    d = (np.array(vh) - np.array(t["vowel_h"]))[vt.FIT]
    return float(np.sqrt(np.mean(d ** 2)))


def measure(d, items, tgt, sex):
    voice = os.path.basename(os.path.normpath(d))
    x, sr = vt.read_wav(os.path.join(d, "lead.wav"))
    ph = json.load(open(os.path.join(d, "phones.json")))
    plan = json.load(open(os.path.join(d, "plan.json")))
    notes_midi = json.load(open(os.path.join(d, "notes.json")))
    if len(ph) != sum(i["syllables"] for i in items):
        raise SystemExit("%s: %d notes for %d syllables" % (d, len(ph), sum(i["syllables"] for i in items)))
    ok = tgt.get("open_k", 3), tgt.get("open_f0", 200.0)
    words, k = [], 0
    for it in items:
        ns = ph[k:k + it["syllables"]]
        ms = notes_midi[k:k + it["syllables"]]
        k += it["syllables"]
        pn, gaps = [], []
        for n, m in zip(ns, ms):
            g = vowel_gap(n["t0"], n["t1"], plan)
            if g and g[1] - g[0] >= MIN_VOWEL:
                f0 = 440 * 2 ** ((m["midi"] - 69) / 12)
                pn.append((g[0], f0, g[1] - g[0]))
                gaps.append((g, f0, n["phones"]))
        w = {"word": it["word"], "cat": it["cat"], "n": len(pn)}
        if pn:
            r = vt.analyse(x, sr, pn, min_dur=MIN_VOWEL)
            w["r"] = r
            if r["nfr"]:
                w["pool"] = vt.pool([r], [np.ones(len(C3))], *ok)
            hn, fl, forms = [], [], []
            for (g, f0, phs) in gaps:
                a, b = g[0] + 0.2 * (g[1] - g[0]), g[0] + 0.8 * (g[1] - g[0])
                h, f = noise_stats(x, sr, f0, a, b)
                hn += h
                fl += f
                if it["cat"] == "hvd":
                    for s in range(int(a * sr), int(b * sr) - 1323, 441):
                        ff = lpc_formants(x[s:s + 1323], sr)
                        if ff:
                            forms.append(ff)
            w["hnr"], w["flat"] = hn, fl
            if forms and it["word"] in HVD:
                F = np.median(np.array(forms), 0)
                T = HILL[sex][HVD[it["word"]]]
                w["formant"] = float(np.hypot((F[0] - T[0]) / T[0], (F[1] - T[1]) / T[1]))
                w["F"] = (float(F[0]), float(F[1]))
        words.append(w)
    return voice, words


def word_scores(w, t, tr):
    """(warmth, timbre) of one word against the reference row `tr`."""
    p = w.get("pool")
    if not p:
        return None
    hb = 10 ** (np.array(p["vowel_h"]) / 10)
    hb = np.where(np.isfinite(hb), hb, 1e-30)
    row = row_from_hb(hb, np.nan, peak_flr_of(p))
    wc = np.mean([score(row["tilt"] - tr["tilt"], SCALES["tilt"]),
                  score(np.log2(row["centroid"] / tr["centroid"]), SCALES["centroid"]),
                  score(10 * np.log10(max(row["share"], 1e-9) / tr["share"]), SCALES["share"])])
    parts = [score(j_of(p["vowel_h"], t), SCALES["J"]),
             score(row["peak_flr"] - tr["peak_flr"], SCALES["peak_flr"])]
    if "formant" in w:
        parts.append(score(w["formant"], SCALES["formant"]))
    return float(wc), float(np.mean(parts))


def report(d, items, worst):
    voice = os.path.basename(os.path.normpath(d))
    sex = "f" if voice in FEMALE else "m"
    tgt = json.load(open(os.path.join(HERE, "targets", "voice_vocadito_%s.json" % ("female" if sex == "f" else "male"))))
    tr = ref_row(tgt)
    voice, words = measure(d, items, tgt, sex)
    ok = tgt.get("open_k", 3), tgt.get("open_f0", 200.0)
    rs = [w["r"] for w in words if w.get("r") and w["r"]["nfr"]]
    pool = vt.pool(rs, [np.ones(len(C3))] * len(rs), *ok)
    hb = sum(r["hb"] for r in rs)
    row = row_from_hb(hb, pool["open_h12"]["median"], peak_flr_of(pool))
    hnr = np.concatenate([w["hnr"] for w in words if w.get("hnr")])
    flat = np.concatenate([w["flat"] for w in words if w.get("flat")])
    fm = [w["formant"] for w in words if "formant" in w]
    row["J"] = j_of(pool["vowel_h"], tgt)
    row["formant"] = float(np.mean(fm)) if fm else float("nan")
    row["hnr"] = float(np.median(hnr))
    row["flat"] = float(np.median(flat))
    comp = {
        "tilt": score(row["tilt"] - tr["tilt"], SCALES["tilt"]),
        "centroid": score(np.log2(row["centroid"] / tr["centroid"]), SCALES["centroid"]),
        "h12": score(row["h12"] - tr["h12"], SCALES["h12"]) if np.isfinite(row["h12"]) else 0.0,
        "share": score(10 * np.log10(max(row["share"], 1e-9) / tr["share"]), SCALES["share"]),
        "J": score(row["J"], SCALES["J"]),
        "peak_flr": score(row["peak_flr"] - tr["peak_flr"], SCALES["peak_flr"]),
        "formant": score(row["formant"], SCALES["formant"]) if fm else 0.0,
    }
    warm = float(np.mean([comp[k] for k in ("tilt", "centroid", "h12", "share")]))
    timb = float(np.mean([comp[k] for k in ("J", "peak_flr", "formant")]))
    ws = []
    for w in words:
        s = word_scores(w, tgt, tr)
        if s:
            ws.append((w["word"], w["cat"], s[0], s[1], (s[0] + s[1]) / 2, w.get("F")))
    return dict(voice=voice, row=row, ref=tr, comp=comp, warm=warm, timb=timb,
                words=sorted(ws, key=lambda v: v[4]), frames=pool["frames"],
                ref_j=0.0, open_n=pool["open_h12"]["n"])


HEAD = ("%-9s %6s %6s %6s %7s %6s %6s %6s %5s %6s %6s %6s"
        % ("voice", "tilt", "cent", "H1-H2", "5-8k%", "J", "pkflr", "HNR", "flat", "formD", "warm", "timbre"))


def fmt_row(name, r, warm="", timb=""):
    return ("%-9s %6.1f %6.0f %6.1f %7.2f %6s %6.1f %6s %5s %6s %6s %6s" % (
        name, r["tilt"], r["centroid"], r["h12"], 100 * r["share"],
        "%.1f" % r["J"] if "J" in r else "-", r["peak_flr"],
        "%.1f" % r["hnr"] if "hnr" in r else "-", "%.1f" % r["flat"] if "flat" in r else "-",
        "%.3f" % r["formant"] if "formant" in r and np.isfinite(r["formant"]) else "-",
        warm, timb))


def main(argv):
    worst, out, words = 10, None, os.path.join(HERE, "ttswords.json")
    dirs, i = [], 1
    while i < len(argv):
        if argv[i] == "--worst":
            worst = int(argv[i + 1]); i += 2
        elif argv[i] == "--json":
            out = argv[i + 1]; i += 2
        elif argv[i] == "--words":
            words = argv[i + 1]; i += 2
        else:
            dirs.append(argv[i]); i += 1
    if not dirs:
        raise SystemExit(__doc__)
    items = json.load(open(words))["items"]
    res = [report(d, items, worst) for d in dirs]
    print(HEAD)
    seen = set()
    for r in res:
        sex = "f" if r["voice"] in FEMALE else "m"
        if sex not in seen:
            seen.add(sex)
            print(fmt_row("ref-" + ("female" if sex == "f" else "male"), r["ref"]))
        print(fmt_row(r["voice"], r["row"], "%.0f" % r["warm"], "%.0f" % r["timb"]))
    print("tilt: dB, 200-800 Hz vs 2-5 kHz harmonic power; cent: Hz; H1-H2: open vowels, dB; "
          "5-8k%: share of harmonic power; J: dB; pkflr: dB; HNR, flat: dB (no reference); formD: relative F1/F2 distance")
    for r in res:
        print("-- %s: components " % r["voice"] + " ".join("%s %.0f" % kv for kv in r["comp"].items()))
        print("   worst words (warmth, timbre, mean): " + "; ".join(
            "%s %.0f/%.0f/%.0f" % (w[0], w[2], w[3], w[4]) for w in r["words"][:worst]))
    print("SCORES " + " ".join("%s warmth %.0f timbre %.0f |" % (r["voice"], r["warm"], r["timb"]) for r in res))
    if out:
        json.dump(res, open(out, "w"), indent=1, default=float)


if __name__ == "__main__":
    main(sys.argv)
