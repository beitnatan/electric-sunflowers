"""Vibrato of sung notes: rate, extent and wobble, engine renders against vocadito.

usage: vibrato_engine.py ref VOCADITO_DIR
       vibrato_engine.py eng NAME DIR...
eng reads DIR/lead.wav and DIR/notes.json (t0, t1, midi) of each render. ref reads the
male vocadito tracks through their note annotations (Annotations/Notes/*_notesA1.csv).
Per note of 1.2 s or more, the middle 25-90% is tracked in 2048-sample frames (512 hop)
within 200 cents in 5-cent steps, and a linear trend is removed. The report gives the
vibrato rate, its extent in cents, and the non-vibrato spread below and above 2 Hz.
Reference data: vocadito, Zenodo 5578807, CC BY 4.0.
"""
import numpy as np, sys, json, csv, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wavread import rd
M=2048; HOP=512; MINF=70
def notes_stats(x,sr,notes):
    w=np.hanning(M); ff=np.fft.rfftfreq(M,1/sr); df=ff[1]; out=[]
    for (t0,f0n,d) in notes:
        if d<1.2 or f0n<=0: continue
        a=int((t0+0.25*d)*sr); e=int((t0+0.9*d)*sr); tr=[]
        for s in range(a,e-M,HOP):
            X=np.abs(np.fft.rfft(x[s:s+M]*w))
            if X.max()<1e-5: continue
            best=None
            for c in range(-200,201,5):
                f=f0n*2**(c/1200); idx=np.round(np.arange(1,9)*f/df).astype(int)
                sc=np.log(X[idx]+1e-9).sum()
                if best is None or sc>best[0]: best=(sc,c)
            tr.append(best[1])
        if len(tr)<MINF: continue
        c=np.array(tr,float); n=len(c); t=np.arange(n)
        c=c-np.polyval(np.polyfit(t,c,1),t)
        Z=np.abs(np.fft.rfft(c*np.hanning(n),2048))/(n/4); fz=np.fft.rfftfreq(2048,HOP/sr)
        m=(fz>=3.5)&(fz<=8.5); i=np.argmax(Z*m)
        # remove the vibrato band (+-1.5 Hz around the peak) and measure the rest
        C=np.fft.rfft(c); fc=np.fft.rfftfreq(n,HOP/sr); C2=C.copy(); C2[(fc>fz[i]-1.5)&(fc<fz[i]+1.5)]=0
        r=np.fft.irfft(C2,n)
        # slow part (<2 Hz) and fast part (>2 Hz outside vibrato)
        Cs=C2.copy(); Cs[fc>=2]=0; slow=np.fft.irfft(Cs,n)
        out.append((fz[i],Z[i],r.std(),slow.std(),(r-slow).std(),n*HOP/sr))
    return out
def rep(name,A):
    A=np.array(A); q=lambda c:"%.2f [%.2f %.2f]"%tuple(np.percentile(A[:,c],[50,25,75]))
    print(f"== {name}: {len(A)} notes (analysed span median {np.median(A[:,5]):.2f} s)")
    print("  vibrato rate Hz",q(0),"| extent cents",q(1)); print("  non-vibrato sd cents: all",q(2),"| slow <2 Hz",q(3),"| fast",q(4))
if sys.argv[1]=="ref":
    d=sys.argv[2]; A=[]
    for m in csv.DictReader(open(d+"/vocadito_metadata.csv")):
        if float(m["average_pitch"])>55: continue
        i=m["track_id"]; x,sr=rd(f"{d}/Audio/vocadito_{i}.wav")
        A+=notes_stats(x,sr,[tuple(map(float,l.split(","))) for l in open(f"{d}/Annotations/Notes/vocadito_{i}_notesA1.csv") if l.strip()])
    rep("vocadito male",A)
else:
    A=[]
    for d in sys.argv[3:]:
        x,sr=rd(d+"/lead.wav"); n=json.load(open(d+"/notes.json"))
        A+=notes_stats(x,sr,[(v["t0"],440*2**((v["midi"]-69)/12),v["t1"]-v["t0"]) for v in n])
    rep(sys.argv[2],A)
