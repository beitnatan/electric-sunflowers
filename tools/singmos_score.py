"""SingMOS-Pro singing naturalness MOS (1..5) per 10 s window.

usage: singmos_score.py OUT.json WAV...

Weights: github.com/South-Twilight/SingMOS release ckpt_v3 (the release URL works
from a cloud session; the repository pages do not). Source: raw files of tag v1.1.2
(hubconf.py, singmos/ssl_mos/{__init__,singmos_pro,utils}.py) in SINGMOS_DIR/src,
the .pth in SINGMOS_DIR. Needs torch, torchaudio, s3prl, librosa, soundfile. The
stubs below cover torchaudio calls that s3prl still makes and torchaudio 2.9+ removed.
Calibrated 26 November 2026: vocadito singers 3.2-4.7; the engine's lead 2.5-2.9.
"""
import os
import sys, json, numpy as np, soundfile as sf, torch, librosa, torchaudio
if not hasattr(torchaudio, "set_audio_backend"): torchaudio.set_audio_backend = lambda *a, **k: None
import types
for _m in ("sox_effects", "backend"):
    if not hasattr(torchaudio, _m):
        _x = types.ModuleType("torchaudio." + _m); _x.apply_effects_tensor = _x.apply_effects_file = (lambda *a, **k: None); sys.modules["torchaudio." + _m] = _x; setattr(torchaudio, _m, _x)
SINGMOS_DIR = os.environ.get('SINGMOS_DIR', 'singmos')
sys.path.insert(0, SINGMOS_DIR + '/src')
import hubconf
torch.set_num_threads(4)
m = hubconf.singmos_pro(pretrained=False, model_path=SINGMOS_DIR + '/singmos_pro.pth')
out = {}
for p in sys.argv[2:]:
    x, sr = sf.read(p, dtype="float32", always_2d=True); x = x.mean(1)
    if sr != 16000: x = librosa.resample(x, orig_sr=sr, target_sr=16000)
    W = 160000; res = []
    for s in range(0, len(x) - W + 1, W):
        seg = x[s:s+W]
        if np.sqrt((seg**2).mean()) < 1e-3: res.append(None); continue
        with torch.no_grad():
            res.append(float(m(torch.from_numpy(seg.copy())[None, :], torch.tensor([W])).item()))
    out[p] = res; v = [r for r in res if r is not None]
    print(f"{p.split('/')[-1]:38s} windows {len(v):3d}  MOS {np.mean(v):.2f}  [{np.min(v):.2f} {np.max(v):.2f}]", flush=True)
json.dump(out, open(sys.argv[1], "w"))
