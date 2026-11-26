"""H1-H2 and H1-H4 of sung vowels: how open the voice is, singers against the engine.

usage: vowel_open_h12.py ref VOCADITO_DIR
       vowel_open_h12.py eng NAME DIR...
ref reads the male vocadito tracks (average pitch <= 55 MIDI) through their notes
(Annotations/Notes/*_notesA1.csv) and vocadito_metadata.csv. eng reads DIR/lead.wav
and DIR/notes.json (t0, t1, midi) of each render. Frames are 2048-sample Hann windows
over the middle 60% of each note of 0.5 s or more; the f0 is refined within 200 cents
and k* is the strongest of harmonics 1 to 8. The last line summarises the open vowels
(k* >= 3, f0 <= 200 Hz). Reference data: vocadito, Zenodo 5578807, CC BY 4.0.
"""
import numpy as np, sys, json, csv, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wavread import rd
M=2048; HOP=512
def frames(x,sr,notes):
    w=np.hanning(M); ff=np.fft.rfftfreq(M,1/sr); df=ff[1]; out=[]
    for (t0,f0n,d) in notes:
        if d<0.5 or f0n<=0: continue
        a=int((t0+0.2*d)*sr); e=int((t0+0.8*d)*sr)
        for s in range(a,e-M,HOP):
            X=np.abs(np.fft.rfft(x[s:s+M]*w))
            if X.max()<1e-5: continue
            best=None
            for c in range(-200,201,10):
                f=f0n*2**(c/1200); idx=np.round(np.arange(1,9)*f/df).astype(int)
                sc=np.log(X[idx]+1e-9).sum()
                if best is None or sc>best[0]: best=(sc,f)
            f0=best[1]
            pk=[X[int(np.floor(k*f0*0.97/df)):int(np.ceil(k*f0*1.03/df))+1].max() for k in range(1,9)]
            out.append((f0,int(np.argmax(pk))+1,20*np.log10(pk[0]/pk[1]),20*np.log10(pk[0]/pk[3])))
    return np.array(out)
def rep(name,F):
    print("==",name,len(F),"frames; f0 median %.0f Hz"%np.median(F[:,0]))
    print("  strongest harmonic k*:  share   H1-H2 med   H1-H4 med   f0 med")
    for k in (1,2,3,4,5,6):
        m=F[:,1]==k
        if m.sum()>20: print(f"   k*={k}   {m.mean():5.2f}   {np.median(F[m,2]):6.1f}    {np.median(F[m,3]):6.1f}   {np.median(F[m,0]):5.0f}")
    m=(F[:,1]>=3)&(F[:,0]<=200)
    print("  open vowels (k*>=3, f0<=200): n=%d  H1-H2 %.1f [%.1f %.1f]   H1-H4 %.1f"%((m.sum(),)+tuple(np.percentile(F[m,2],[50,25,75]))+(np.median(F[m,3]),)))
if sys.argv[1]=="ref":
    d=sys.argv[2]; A=[]
    for m in csv.DictReader(open(d+"/vocadito_metadata.csv")):
        if float(m["average_pitch"])>55: continue
        i=m["track_id"]; x,sr=rd(f"{d}/Audio/vocadito_{i}.wav")
        notes=[tuple(map(float,l.split(","))) for l in open(f"{d}/Annotations/Notes/vocadito_{i}_notesA1.csv") if l.strip()]
        f=frames(x,sr,notes)
        if len(f): A.append(f)
    rep("vocadito male",np.vstack(A))
else:
    A=[]
    for d in sys.argv[3:]:
        x,sr=rd(d+"/lead.wav"); n=json.load(open(d+"/notes.json"))
        A.append(frames(x,sr,[(v["t0"],440*2**((v["midi"]-69)/12),v["t1"]-v["t0"]) for v in n]))
    rep(sys.argv[2],np.vstack(A))
