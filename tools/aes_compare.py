#!/usr/bin/env python3
"""Meta Audiobox Aesthetics (facebook/audiobox-aesthetics, CC BY 4.0) as a listener.

  aes_compare.py score OUT.json WAV...      per 10 s window: PQ (production quality),
                                            PC (production complexity), CE (content
                                            enjoyment), CU (content usefulness), 1..10
  aes_compare.py pair BEFORE.wav AFTER.wav  scores both, then the paired difference per
                                            aligned window (same song and seed, so the
                                            windows hold the same music): mean, sd, t,
                                            and the share of windows that favour AFTER

Needs torch, torchaudio, audiobox_aesthetics, soundfile, requests (CPU is enough:
about 1 s per window on 4 cores). Calibrated on 26 November 2026 (docs/sound-plan.md):
it reproduces the owner's verdict on the modal brushes (t -4.6), sees nothing in the
full-kit change the owner preferred, and rates the synthetic voice no lower than real
amateur recordings: it judges production defects, not naturalness.
"""
import sys, json
import numpy as np
import soundfile as sf
import torch
from audiobox_aesthetics.infer import initialize_predictor

AXES = ("PQ", "PC", "CE", "CU")


def score(paths, pred):
    out = {}
    for p in paths:
        x, sr = sf.read(p, dtype="float32", always_2d=True)
        x = x.mean(1)
        w = 10 * sr
        items = []
        for s in range(0, len(x) - w + 1, w):
            seg = x[s:s + w]
            items.append(None if np.sqrt((seg ** 2).mean()) < 1e-3 else
                         {"path": torch.from_numpy(seg.copy())[None, :], "sample_rate": sr})
        live = [it for it in items if it is not None]
        rows = []
        for i in range(0, len(live), 8):
            rows += pred.forward(live[i:i + 8])
        k, res = 0, []
        for it in items:
            if it is None:
                res.append(None)
            else:
                res.append(rows[k])
                k += 1
        out[p] = res
        v = [r for r in res if r]
        print(f"{p.split('/')[-1]:40s} windows {len(v):3d}  " +
              "  ".join(f"{a} {np.mean([r[a] for r in v]):.2f}" for a in AXES), flush=True)
    return out


def main():
    torch.set_num_threads(4)
    pred = initialize_predictor()
    if sys.argv[1] == "score":
        json.dump(score(sys.argv[3:], pred), open(sys.argv[2], "w"))
    elif sys.argv[1] == "pair":
        d = score(sys.argv[2:4], pred)
        a, b = d[sys.argv[2]], d[sys.argv[3]]
        for ax in AXES:
            diff = np.array([y[ax] - x[ax] for x, y in zip(a, b) if x and y])
            t = diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)) + 1e-12)
            print(f"{ax}: mean {diff.mean():+.3f}  sd {diff.std(ddof=1):.3f}  n {len(diff)}  "
                  f"t {t:+.1f}  windows favouring AFTER {np.mean(diff > 0) * 100:.0f}%")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
