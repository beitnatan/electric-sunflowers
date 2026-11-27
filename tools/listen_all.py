#!/usr/bin/env python3
"""All listeners on stems renders, one compact table.

usage: listen_all.py LABEL=STEMS_DIR [LABEL=STEMS_DIR ...] [--skip aes,mos,asr,ph]
STEMS_DIR holds lead.wav and phones.json (the `stems` example). Results cache in
STEMS_DIR/listen.json. Env: SINGMOS_DIR (weights), python with torch, whisper,
transformers, audiobox_aesthetics, s3prl.
Columns: ph = phoneme hits (f r l iy ey ih; wav2vec2 espeak, no word prior);
asr = Whisper small.en: share of the song's words kept (sub+del) and the target
words missed; PQ/CE = Audiobox; MOS = SingMOS-Pro.
"""
import sys, os, json, subprocess, re, difflib
import numpy as np

TARGET = "ferry river rail pale carry every harbor half four for".split()
args = [a for a in sys.argv[1:] if "=" in a and not a.startswith("--")]
skip = set((sys.argv[sys.argv.index("--skip") + 1] if "--skip" in sys.argv else "").split(","))
here = os.path.dirname(os.path.abspath(__file__))

def phoneme(d):
    out = subprocess.run([sys.executable, f"{here}/listeners.py", d], capture_output=True, text=True).stdout
    h = json.loads(out.strip().splitlines()[-1].split("hit rate:")[1].replace("'", '"'))
    return h

def words(s): return re.findall(r"[a-z']+", s.lower())

def asr(d):
    import whisper
    m = whisper.load_model("small.en")
    ph = json.load(open(f"{d}/phones.json"))
    # expected words come from the sidecar when present
    ref = None
    for f in (f"{d}/words.txt",):
        if os.path.exists(f): ref = words(open(f).read())
    r = m.transcribe(f"{d}/lead.wav", language="en", fp16=False, temperature=0)
    hyp = words(r["text"])
    if ref is None: return {"n": len(hyp), "hyp": " ".join(hyp)}
    sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    kept = sum(b.size for b in sm.get_matching_blocks())
    miss = [w for w in ref if w in TARGET]; got = [ref[i+k] for b in sm.get_matching_blocks() for k in range(b.size) for i in [b.a]]
    lost = sorted(set(w for w in miss if miss.count(w) > got.count(w)))
    return {"kept": kept / len(ref), "lost": lost}

def aes(d):
    out = subprocess.run([sys.executable, f"{here}/aes_compare.py", "score", f"{d}/aes.json", f"{d}/lead.wav"], capture_output=True, text=True).stdout
    m = dict(re.findall(r"(PQ|PC|CE|CU) ([\d.]+)", out)); return {k: float(v) for k, v in m.items()}

def mos(d):
    out = subprocess.run([sys.executable, f"{here}/singmos_score.py", f"{d}/mos.json", f"{d}/lead.wav"], capture_output=True, text=True).stdout
    m = re.search(r"MOS ([\d.]+)", out); return float(m.group(1)) if m else None

rows = {}
for a in args:
    lab, d = a.split("=", 1); cache = f"{d}/listen.json"
    c = json.load(open(cache)) if os.path.exists(cache) else {}
    for k, fn in (("ph", phoneme), ("asr", asr), ("aes", aes), ("mos", mos)):
        if k not in skip and k not in c: c[k] = fn(d); json.dump(c, open(cache, "w"))
    rows[lab] = c
print(f"{'label':10s} {'ph f r l iy ey ih':40s} asr kept/lost            PQ   CE   MOS")
for lab, c in rows.items():
    h = c.get("ph", {}); ph = " ".join(f"{k}:{v}" for k, v in h.items())
    a = c.get("asr", {}); at = f"{a.get('kept', 0):.2f} {','.join(a.get('lost', []))}" if "kept" in a else "-"
    q = c.get("aes", {})
    print(f"{lab:10s} {ph:40s} {at:24s} {q.get('PQ', 0):.2f} {q.get('CE', 0):.2f} {c.get('mos') or 0:.2f}")
