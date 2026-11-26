"""Which word-initial consonants a recogniser keeps. usage: consonant_survival.py WER.txt...

WER.txt is the --show output of tools/wer_song.py. For each reference word that
starts with a consonant, counts it kept when the aligned hypothesis word starts
with the same letters. Letters stand in for phonemes: a rough, relative measure.
"""
import sys,re,collections,jiwer
def lines(p):
    ref=hyp=None; out=[]
    for l in open(p):
        if l.startswith("  ") and not l.startswith("    "): ref=l.strip()
        elif l.strip().startswith("voice |"): out.append((ref,l.split("|",1)[1].strip()))
    return out
CL={'p':'stop','b':'stop','t':'stop','d':'stop','k':'stop','g':'stop','c':'stop','q':'stop','m':'nasal','n':'nasal','s':'fric','z':'fric','f':'fric','v':'fric','h':'h','l':'liquid','r':'liquid','w':'glide','y':'glide','j':'affric'}
def onset(w):
    if w.startswith(('th','sh','ch')): return w[:2]
    return w[0]
for p in sys.argv[1:]:
    keep=collections.Counter(); tot=collections.Counter(); sub=collections.Counter()
    for ref,hyp in lines(p):
        r=jiwer.process_words(ref.lower(),hyp.lower())
        R=r.references[0]; H=r.hypotheses[0]
        for a in r.alignments[0]:
            for i in range(a.ref_end_idx-a.ref_start_idx):
                w=R[a.ref_start_idx+i]; o=onset(w)
                if o[0] in 'aeiou': continue
                cl=CL.get(o[0],'other') if len(o)==1 else {'th':'fric','sh':'fric','ch':'affric'}[o]
                tot[cl]+=1
                if a.type=='equal': keep[cl]+=1
                elif a.type=='substitute':
                    h=H[a.hyp_start_idx+i] if a.hyp_start_idx+i<a.hyp_end_idx else ''
                    if h and onset(h)==o: keep[cl]+=1
                    elif h: sub[(o,onset(h))]+=1
    print("==",p.split('/')[-1])
    for cl in sorted(tot): print(f"  {cl:7s} word-initial consonant kept {keep[cl]:3d}/{tot[cl]:3d} = {keep[cl]/tot[cl]:.2f}")
    print("  commonest substitutions (ref onset -> heard onset):",sub.most_common(14))
