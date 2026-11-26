#!/usr/bin/env python3
"""Measure a reverb impulse response (numpy only).

usage: python3 -I tools/reverb_check.py IR.wav

IR.wav is stereo (or mono) WAV, 16/24/32-bit PCM or 32-bit float, as written
by `cargo run --release -p dsp --example reverbir -- PREFIX`. Prints:

 a. T60 per octave band, 125 Hz to 8 kHz: Schroeder backward integral of the
    band energy (both channels), line fit from -5 to -25 dB, times 3.
 b. Normalised echo density (Abel and Huang 2006) of the left channel: in a
    sliding 20 ms Hann-weighted window, the fraction of samples whose
    magnitude exceeds the window's standard deviation, over
    erfc(1/sqrt 2) = 0.3173. First time it reaches 0.9, and values at
    30, 60, 100 and 200 ms (window centre).
 c. Modal resolution: left channel, 0.3 s to 2.3 s, Hann window, FFT. Over
    200 Hz to 2 kHz: standard deviation of the magnitude in dB about its
    1/3-octave-smoothed trend (a diffuse field gives about 5.6 dB) and the
    mean number of spectral peaks per 100 Hz more than 10 dB above the trend.
 d. Left/right correlation of the tail from 0.1 s on.
 e. Total energy of the impulse response (dB, both channels, sum of squares).
"""
import math
import struct
import sys

import numpy as np

SQ = math.erfc(1.0 / math.sqrt(2.0))  # 0.3173


def read_wav(path):
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"RIFF" or d[8:12] != b"WAVE":
        raise SystemExit("not a RIFF/WAVE file")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(d):
        cid = d[pos:pos + 4]
        size = struct.unpack("<I", d[pos + 4:pos + 8])[0]
        body = d[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise SystemExit("missing fmt or data chunk")
    tag, ch, fs, _, _, bits = fmt
    if tag == 0xFFFE:
        tag = 3 if bits == 32 else 1  # extensible: assume float at 32 bits
    if tag == 3 and bits == 32:
        x = np.frombuffer(data, dtype="<f4").astype(np.float64)
    elif tag == 1 and bits == 16:
        x = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
    elif tag == 1 and bits == 32:
        x = np.frombuffer(data, dtype="<i4").astype(np.float64) / 2147483648.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float64) / 8388608.0
    else:
        raise SystemExit("unsupported WAV format %r" % (fmt,))
    n = len(x) // ch
    x = x[: n * ch].reshape(n, ch)
    return fs, x


def octave_band(x, fs, fc):
    """Zero-phase band-pass: 4th-order Butterworth magnitude, one octave wide."""
    n = len(x)
    nf = 1 << (n - 1).bit_length()
    f = np.fft.rfftfreq(nf, 1.0 / fs)
    lo, hi = fc / math.sqrt(2.0), fc * math.sqrt(2.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        hp = 1.0 / np.sqrt(1.0 + (lo / np.maximum(f, 1e-9)) ** 8)
    lp = 1.0 / np.sqrt(1.0 + (f / hi) ** 8)
    h = hp * lp
    h[0] = 0.0
    return np.fft.irfft(np.fft.rfft(x, nf) * h, nf)[:n]


def t60_schroeder(e, fs):
    edc = np.cumsum(e[::-1])[::-1]
    db = 10.0 * np.log10(np.maximum(edc / edc[0], 1e-30))
    m = (db <= -5.0) & (db >= -25.0)
    if m.sum() < 10:
        return float("nan")
    t = np.nonzero(m)[0] / fs
    slope = np.polyfit(t, db[m], 1)[0]
    return -60.0 / slope


def echo_density(x, fs, win_s=0.020, hop=4):
    w = np.hanning(int(round(win_s * fs)))
    w = w / w.sum()
    n = len(w)
    pad = np.concatenate([np.zeros(n // 2), x, np.zeros(n - n // 2)])
    idx = np.arange(0, len(x), hop)
    ed = np.empty(len(idx))
    for c in range(0, len(idx), 2048):
        ii = idx[c:c + 2048]
        seg = pad[ii[:, None] + np.arange(n)[None, :]]
        sd = np.sqrt((seg * seg) @ w)
        ed[c:c + len(ii)] = ((np.abs(seg) > sd[:, None]) @ w) / SQ
    return idx / fs, ed


def smooth_third_octave(p):
    """Mean of power `p` (per rfft bin) over a 1/3-octave window at each bin."""
    cs = np.concatenate([[0.0], np.cumsum(p)])
    k = np.arange(len(p))
    r = 2.0 ** (1.0 / 6.0)
    lo = np.maximum((k / r).astype(int), 0)
    hi = np.minimum(np.ceil(k * r).astype(int) + 1, len(p))
    hi = np.maximum(hi, lo + 1)
    return (cs[hi] - cs[lo]) / (hi - lo)


def modal(x, fs, t0=0.3, t1=2.3, f0=200.0, f1=2000.0):
    seg = x[int(t0 * fs):int(t1 * fs)]
    seg = seg * np.hanning(len(seg))
    sp = np.fft.rfft(seg)
    p = np.abs(sp) ** 2
    trend = smooth_third_octave(p)
    f = np.fft.rfftfreq(len(seg), 1.0 / fs)
    m = (f >= f0) & (f <= f1)
    d = 10.0 * np.log10(np.maximum(p, 1e-60) / np.maximum(trend, 1e-60))
    sd = d[m].std()
    dm = d.copy()
    pk = (dm[1:-1] > dm[:-2]) & (dm[1:-1] >= dm[2:]) & (dm[1:-1] > 10.0)
    pk = np.concatenate([[False], pk, [False]]) & m
    return sd, pk.sum() / ((f1 - f0) / 100.0)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    fs, x = read_wav(sys.argv[1])
    l = x[:, 0]
    r = x[:, 1] if x.shape[1] > 1 else x[:, 0]
    print("file %s  fs %d  %.2f s  %d ch" % (sys.argv[1], fs, len(l) / fs, x.shape[1]))

    print("a. T60 per octave band (s):")
    e_all = np.stack([l, r])
    row = []
    for fc in (125, 250, 500, 1000, 2000, 4000, 8000):
        bl, br = octave_band(l, fs, fc), octave_band(r, fs, fc)
        row.append("%d Hz %.2f" % (fc, t60_schroeder(bl * bl + br * br, fs)))
    print("   " + "   ".join(row))

    t, ed = echo_density(l, fs)
    above = np.nonzero(ed >= 0.9)[0]
    # first time after the direct impulse window (t > 10 ms) that it reaches 0.9
    above = above[t[above] > 0.010] if len(above) else above
    t09 = t[above[0]] * 1000.0 if len(above) else float("nan")
    vals = ["%d ms %.3f" % (ms, np.interp(ms / 1000.0, t, ed)) for ms in (30, 60, 100, 200)]
    print("b. echo density (left): reaches 0.9 at %.1f ms;  %s" % (t09, "  ".join(vals)))

    sd, pk = modal(l, fs)
    print("c. modal (left, 0.3-2.3 s, 200-2000 Hz): sd about 1/3-octave trend %.2f dB;"
          " peaks >10 dB per 100 Hz %.2f" % (sd, pk))

    s = int(0.1 * fs)
    a, b = l[s:], r[s:]
    rho = float((a * b).sum() / math.sqrt((a * a).sum() * (b * b).sum() + 1e-300))
    print("d. L/R tail correlation (from 0.1 s): %.3f" % rho)

    en = float((l * l).sum() + (r * r).sum())
    print("e. total energy (both channels): %.2f dB" % (10.0 * math.log10(en + 1e-300)))


if __name__ == "__main__":
    main()
