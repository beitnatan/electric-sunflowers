"""Vibrato of VocalSet long tones (straight, forte, pp): rate, extent and wobble.

usage: vibrato_vocalset.py VOCALSET_DIR
VOCALSET_DIR holds FULL/<singer>/long_tones/<style>/*_<vowel>.wav for male and female
singers. Pitch is tracked by autocorrelation (40 ms window, 10 ms hop, 70-700 Hz). The
longest voiced run of at least 2 s is analysed in 1.5 s windows, and a run that strays
more than 150 cents from its median is skipped. Per sex and style the report gives the
vibrato rate (3.5-8.5 Hz peak), its extent in cents, the slow (<2 Hz) and fast wobble,
and the per-singer ranges. Reference data: VocalSet, Zenodo 1193957, CC BY 4.0.
"""
import numpy as np, sys, glob, os, re, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wavread import rd
def f0track(x,sr,lo=70,hi=700):
    W=int(0.04*sr); H=int(0.01*sr); out=[]
    for s in range(0,len(x)-W,H):
        fr=x[s:s+W]-x[s:s+W].mean(); e=(fr**2).sum()
        if e<1e-4: out.append(0); continue
        n=1<<int(np.ceil(np.log2(2*W))); F=np.fft.rfft(fr,n); ac=np.fft.irfft(F*np.conj(F))[:W]
        a,b=int(sr/hi),int(sr/lo); k=a+np.argmax(ac[a:b])
        if ac[k]/ac[0]<0.5: out.append(0); continue
        # avoid octave-down: prefer shortest lag within 90% of max
        cand=np.where(ac[a:b]>0.9*ac[k])[0]; k=a+cand[0]
        while k+1<b and ac[k+1]>ac[k]: k+=1
        y0,y1,y2=ac[k-1],ac[k],ac[k+1]; d=0.5*(y0-y2)/(y0-2*y1+y2+1e-12); out.append(sr/(k+d))
    return np.array(out),0.01
def vib(c,dt):
    n=len(c); t=np.arange(n); c=c-np.polyval(np.polyfit(t,c,1),t)
    Z=np.abs(np.fft.rfft(c*np.hanning(n),4096))/(n/4); fz=np.fft.rfftfreq(4096,dt); m=(fz>=3.5)&(fz<=8.5); i=np.argmax(Z*m)
    C=np.fft.rfft(c); fc=np.fft.rfftfreq(n,dt); C2=C.copy(); C2[(fc>fz[i]-1.5)&(fc<fz[i]+1.5)]=0; C2[fc>12]=0; r=np.fft.irfft(C2,n)
    Cs=C2.copy(); Cs[fc>=2]=0; slow=np.fft.irfft(Cs,n)
    return fz[i],Z[i],slow.std(),(r-slow).std()
res={}
for p in sorted(glob.glob(sys.argv[1]+"/FULL/*/long_tones/*/*.wav")):
    m=re.search(r'/(male|female)(\d+)/long_tones/(\w+)/.*_([aeiou])\.wav$',p)
    if not m or m.group(3) not in ("straight","forte","pp"): continue
    x,sr=rd(p); f,dt=f0track(x,sr)
    v=f>0
    # longest voiced run
    best=(0,0); i=0
    while i<len(v):
        if v[i]:
            j=i
            while j<len(v) and v[j]: j+=1
            if j-i>best[1]-best[0]: best=(i,j)
            i=j
        i+=1
    a,b=best
    if (b-a)*dt<2.0: continue
    k=int(0.4/dt); c=1200*np.log2(f[a+k:b-k]/np.median(f[a+k:b-k]))
    if np.abs(c).max()>150: continue
    # analyse in 1.5 s windows to follow drift of the vibrato itself
    W=int(1.5/dt); rows=[vib(c[s:s+W],dt) for s in range(0,len(c)-W+1,W)]
    if not rows: continue
    R=np.array(rows)
    res.setdefault((m.group(1),m.group(3)),[]).append((m.group(1)+m.group(2),np.median(f[a:b]),np.median(R[:,0]),np.median(R[:,1]),R[:,0].std() if len(R)>1 else np.nan,np.median(R[:,2]),np.median(R[:,3]),(R[:,1].max()-R[:,1].min()) if len(R)>1 else np.nan))
for key in sorted(res):
    A=res[key]; M=np.array([r[1:] for r in A],float); q=lambda c:"%.2f [%.2f %.2f]"%tuple(np.nanpercentile(M[:,c],[50,25,75]))
    print(f"{key[0]:6s} {key[1]:8s} n={len(A):3d} singers={len(set(r[0] for r in A))} f0 {np.median(M[:,0]):4.0f} Hz | rate Hz {q(1)} | extent cents {q(2)} | rate sd within tone {q(3)} | extent range within tone {q(6)} | slow<2Hz sd {q(4)} | fast sd {q(5)} | straight-ish (<10c): {100*(M[:,2]<10).mean():.0f}%")
    # between-singer spread of rate and extent
    by={}
    for r in A: by.setdefault(r[0],[]).append(r)
    rs=[np.median([x[2] for x in v]) for v in by.values()]; es=[np.median([x[3] for x in v]) for v in by.values()]
    print(f"        per-singer medians: rate {np.min(rs):.1f}..{np.max(rs):.1f} Hz, extent {np.min(es):.0f}..{np.max(es):.0f} cents")
