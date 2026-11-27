#!/usr/bin/env python3
"""Harmonic profile of sung /iy/ (H1..H10 re the strongest, median) per f0 bin.

usage: iy_profile.py DIR [DIR...]    DIR: stems render (lead.wav, notes.json, phones.json)
Syllables with exactly one vowel, /iy/; frames of 40 ms every 50 ms from 80 ms after the
note start to 80 ms before its end; f0 refined within 200 cents of the note.
Reference (VocalSet female long tones, 9 singers; demo-diagnosis reportB):
  i 420-550: H6 -30 H7 -30 H8 -35;  i 550-750: H5 -32 H6 -37.
Prints the profile, and PASS when H6..H8 (420-550) and H5..H6 (550-750) are within 6 dB.
"""
import sys, json
import numpy as np
import soundfile as sf

REF = {(420, 550): {6: -30, 7: -30, 8: -35}, (550, 750): {5: -32, 6: -37}}
VOW = {"iy"}

def prof(d):
    x, sr = sf.read(f"{d}/lead.wav", dtype="float32", always_2d=True); x = x.mean(1)
    P = json.load(open(f"{d}/phones.json")); N = json.load(open(f"{d}/notes.json"))
    n = int(0.04 * sr); w = np.hanning(n); fr = np.fft.rfftfreq(8192, 1 / sr)
    res = {}
    for s in P:
        if "iy" not in s["phones"]: continue
        nt = [q for q in N if abs(q["t0"] - s["t0"]) < 0.01]
        if not nt: continue
        f0n = 440 * 2 ** ((nt[0]["midi"] - 69) / 12)
        t = s["t0"] + 0.08
        while t < s["t1"] - 0.08 - 0.04:
            i = int(t * sr); seg = x[i:i + n]; t += 0.05
            if len(seg) < n or np.sqrt(np.mean(seg ** 2)) < 1e-4: continue
            X = np.abs(np.fft.rfft(seg * w, 8192)) ** 2
            best = None
            for c in range(-200, 201, 10):
                f = f0n * 2 ** (c / 1200)
                sc = sum(np.log(X[(fr > k * f * .97) & (fr < k * f * 1.03)].max() + 1e-30) for k in range(1, 9))
                if best is None or sc > best[0]: best = (sc, f)
            f0 = best[1]
            H = np.array([10 * np.log10(X[(fr > k * f0 * .97) & (fr < k * f0 * 1.03)].max() + 1e-30) for k in range(1, 11)])
            for b in REF:
                if b[0] <= f0 < b[1]: res.setdefault(b, []).append(H - H.max())
    return res

for d in sys.argv[1:]:
    r = prof(d); ok = True; line = []
    for b in sorted(REF):
        if b not in r: line.append(f"{b}: none"); ok = False; continue
        m = np.median(np.array(r[b]), 0)
        line.append(f"i {b[0]}-{b[1]} n={len(r[b])}: " + " ".join(f"{v:4.0f}" for v in m))
        for k, v in REF[b].items():
            if abs(m[k - 1] - v) > 6: ok = False
    print(d.split("/")[-4] if d.count("/") > 3 else d, "|", " | ".join(line), "|", "PASS" if ok else "FAIL")
