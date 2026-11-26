"""Harmonic peak prominence over the spectral trend, singers against the engine.

usage: harmonic_prominence.py ref VOCADITO_DIR
       harmonic_prominence.py eng NAME DIR...
Same inputs and frames as vowel_open_h12.py. Per frame, the harmonic levels up to
1.5 kHz are fitted with a line against log2 frequency. The report gives, per f0 band
(100-150, 150-200, 200-260 Hz; median and quartiles): the peak's excess over that line,
the gap between the two highest harmonics, the trend slope in dB per octave, and H1
below the strongest harmonic. Reference data: vocadito, Zenodo 5578807, CC BY 4.0.
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
            f0=best[1]; K=int(1500/f0)
            p=20*np.log10(np.array([X[int(np.floor(k*f0*0.97/df)):int(np.ceil(k*f0*1.03/df))+1].max() for k in range(1,K+1)])+1e-12)
            # regression line through harmonic levels vs log-frequency = source+radiation trend; peak excess over it
            lf=np.log2(np.arange(1,K+1)); A=np.polyfit(lf,p,1); res=p-np.polyval(A,lf)
            s2=np.sort(p)[::-1]
            out.append((f0,res.max(),s2[0]-s2[1],A[0],np.argmax(p)+1,p[0]-p.max()))
    return np.array(out)
def rep(name,F):
    print("==",name)
    for lo,hi in ((100,150),(150,200),(200,260)):
        m=(F[:,0]>=lo)&(F[:,0]<hi)
        if m.sum()<50: continue
        q=lambda c: "%5.1f [%5.1f %5.1f]"%tuple(np.percentile(F[m,c],[50,25,75]))
        print(f"  f0 {lo}-{hi} n={m.sum():5d}  peak excess over trend {q(1)}  top-2nd {q(2)}  slope dB/oct (0-1.5k) {q(3)}  H1 below strongest {q(5)}")
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
