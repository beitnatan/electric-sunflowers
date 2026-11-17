#!/usr/bin/env python3
"""Vowel time per sung note, from a stems render (notes.json, plan.json).

Usage: python3 -I tools/artic_check.py DIR...

DIR holds the output of `examples/stems`: notes.json (lead notes, t0 and t1)
and plan.json (planned consonant segments, [kind, t0, t1]). The interval of
note i runs from its t0 to min(next t0, its t1 + 0.05 s). Vowel time is the
interval less the total overlap of plan segments with it (the vowel with its
transitions). Per directory, one row for all notes and one for the notes
whose interval is under 0.32 s: median, 25th and 10th percentile of vowel
time in ms, the share of notes under 80 ms, and the vowel's share of the
interval (median, 10th percentile). Also the count of plan segments shorter
than 15 ms, all and other than bursts (a stop burst is 12 ms). Standard
library only.
"""

import json
import os
import sys

TAIL = 0.05
FAST = 0.32
SHORT = 0.080
TINY = 0.015


def pct(xs, q):
    """Percentile q (0-100) of xs by linear interpolation."""
    s = sorted(xs)
    if not s:
        return float("nan")
    x = (len(s) - 1) * q / 100.0
    i = int(x)
    j = min(i + 1, len(s) - 1)
    return s[i] + (s[j] - s[i]) * (x - i)


def measure(d):
    with open(os.path.join(d, "notes.json")) as f:
        notes = json.load(f)
    with open(os.path.join(d, "plan.json")) as f:
        plan = json.load(f)
    notes = sorted(notes, key=lambda n: n["t0"])
    segs = sorted((s[1], s[2]) for s in plan)
    tiny_nb = sum(1 for s in plan if s[0] != "burst" and s[2] - s[1] < TINY)
    rows = []
    for i, n in enumerate(notes):
        a = n["t0"]
        b = n["t1"] + TAIL
        if i + 1 < len(notes):
            b = min(b, notes[i + 1]["t0"])
        if b <= a:
            continue
        cons = 0.0
        for s0, s1 in segs:
            if s0 >= b:
                break
            cons += max(0.0, min(b, s1) - max(a, s0))
        v = max(0.0, (b - a) - cons)
        rows.append((b - a, v))
    tiny = sum(1 for s0, s1 in segs if s1 - s0 < TINY)
    return rows, tiny, tiny_nb


def line(label, rows):
    if not rows:
        return "%-26s no notes" % label
    v = [r[1] for r in rows]
    share = [r[1] / r[0] for r in rows]
    under = sum(1 for x in v if x < SHORT) / len(v)
    return "%-26s n %4d  med %4.0f  p25 %4.0f  p10 %4.0f ms  <80ms %3.0f%%  share med %.2f p10 %.2f" % (
        label,
        len(v),
        1e3 * pct(v, 50),
        1e3 * pct(v, 25),
        1e3 * pct(v, 10),
        100 * under,
        pct(share, 50),
        pct(share, 10),
    )


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    for d in argv:
        rows, tiny, tiny_nb = measure(d)
        name = os.path.basename(os.path.normpath(d))
        print(line(name + " all", rows))
        print(line(name + " IOI<0.32s", [r for r in rows if r[0] < FAST]))
        print("%-26s segments under 15 ms: %d (not bursts: %d)" % (name, tiny, tiny_nb))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
