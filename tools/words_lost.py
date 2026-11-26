"""Words misheard or lost by the recogniser, per reference word.

usage: words_lost.py WER.txt...
Each WER.txt is the --show output of tools/wer_song.py for one render: reference lines
(two leading spaces) and hypothesis lines tagged voice or mix. For each tag the script
pools substitutions and deletions per reference word, insertions per reference word,
and the median line's share of substitutions and deletions. Needs jiwer.
"""
import sys,jiwer,numpy as np
def lines(p,tag):
    ref=None; out=[]
    for l in open(p):
        if l.startswith("  ") and not l.startswith("    "): ref=l.strip()
        elif l.strip().split(" ")[0]==tag: out.append((ref,l.split("|",1)[1].strip() if "|" in l else ""))
    return out
for p in sys.argv[1:]:
    for tag in ("voice","mix"):
        L=lines(p,tag); s=d=i=n=0; per=[]
        for r,h in L:
            o=jiwer.process_words(r.lower(),h.lower() if h else ""); s+=o.substitutions; d+=o.deletions; i+=o.insertions; n+=len(r.split()); per.append((o.substitutions+o.deletions)/len(r.split()))
        print(f"{p.split('/')[-2]+'/'+p.split('/')[-1]:22s} {tag:5s} sub+del per ref word {(s+d)/n:.3f}  insertions {i/n:.3f}  median line (sub+del) {np.median(per):.2f}  lines {len(L)}")
