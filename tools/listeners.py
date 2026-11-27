#!/usr/bin/env python3
"""Phoneme listener: run a CTC phoneme recogniser over each syllable of a stems render.

usage: listeners.py STEMS_DIR [--pad 0.15] [--focus f,r,l]
STEMS_DIR holds lead.wav and phones.json (from the `stems` example).
Prints, per syllable, the expected ARPAbet and the recognised IPA, and a
per-phone hit rate for the focus phones. Model: facebook/wav2vec2-lv-60-espeak-cv-ft
(no language model, so no word prior; Whisper's prior hides phone errors).
"""
import sys, json, argparse
import numpy as np, soundfile as sf, torch, torchaudio
from transformers import Wav2Vec2Processor, Wav2Vec2ForCTC

IPA = {"f":["f"],"v":["v"],"r":["ɹ","r","ɻ"],"l":["l","ɫ"],"s":["s"],"sh":["ʃ"],
       "iy":["i"],"ih":["ɪ"],"ey":["e","eɪ"],"ay":["aɪ","a"],"m":["m"],"n":["n"],
       "p":["p"],"t":["t"],"k":["k"],"th":["θ"],"dh":["ð"],"z":["z"],"w":["w"],
       "hh":["h"],"d":["d"],"b":["b"],"g":["ɡ","g"]}

ap = argparse.ArgumentParser()
ap.add_argument("dir"); ap.add_argument("--pad", type=float, default=0.15)
ap.add_argument("--focus", default="f,r,l,iy,ey,ih")
a = ap.parse_args()
name = "facebook/wav2vec2-lv-60-espeak-cv-ft"
proc = Wav2Vec2Processor.from_pretrained(name); model = Wav2Vec2ForCTC.from_pretrained(name).eval()
x, sr = sf.read(f"{a.dir}/lead.wav", dtype="float32")
if x.ndim > 1: x = x.mean(1)
x = torchaudio.functional.resample(torch.from_numpy(x), sr, 16000).numpy()
phones = json.load(open(f"{a.dir}/phones.json"))
focus = a.focus.split(","); hit = {p: [0, 0] for p in focus}
for s in phones:
    t0, t1 = max(0, s["t0"] - a.pad), s["t1"] + a.pad
    seg = x[int(t0*16000):int(t1*16000)]
    if len(seg) < 1600: continue
    seg = (seg - seg.mean()) / (seg.std() + 1e-7)
    with torch.no_grad():
        lg = model(proc(seg, sampling_rate=16000, return_tensors="pt").input_values).logits
    out = proc.batch_decode(lg.argmax(-1))[0].replace(" ", "")
    ok = []
    for p in s["phones"]:
        if p in focus:
            h = any(c in out for c in IPA.get(p, [p]))
            hit[p][0] += h; hit[p][1] += 1; ok.append(p + ("+" if h else "-"))
    print(f"{s['t0']:8.2f} {' '.join(s['phones']):14s} {out:16s} {' '.join(ok)}")
print("hit rate:", {p: f"{h}/{n}" for p, (h, n) in hit.items() if n})
