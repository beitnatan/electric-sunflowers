"""Vibrato of vocadito male tracks from their F0 annotations: the target for the engine.

usage: vibrato_vocadito.py VOCADITO_DIR
VOCADITO_DIR holds vocadito_metadata.csv and Annotations/F0/vocadito_N_f0.csv (time in
s, Hz, 0 when unvoiced). Male tracks only (average pitch <= 55 MIDI). A stable segment
is a voiced run of at least 1 s whose pitch stays within 120 cents of its running
median; its first 0.15 s are dropped. Each segment gives the vibrato rate, extent, and
slow and fast wobble (components above 12 Hz are ignored). The report gives pooled
quartiles and per-singer medians. Reference data: vocadito, Zenodo 5578807, CC BY 4.0.
"""
import numpy as np, sys, csv
def seg_stats(c,dt):
    n=len(c); t=np.arange(n); c=c-np.polyval(np.polyfit(t,c,1),t)
    Z=np.abs(np.fft.rfft(c*np.hanning(n),8192))/(n/4); fz=np.fft.rfftfreq(8192,dt)
    m=(fz>=3.5)&(fz<=8.5); i=np.argmax(Z*m)
    C=np.fft.rfft(c); fc=np.fft.rfftfreq(n,dt); C2=C.copy(); C2[(fc>fz[i]-1.5)&(fc<fz[i]+1.5)]=0
    C2[fc>12]=0   # ignore tracker noise above 12 Hz
    r=np.fft.irfft(C2,n); Cs=C2.copy(); Cs[fc>=2]=0; slow=np.fft.irfft(Cs,n)
    return (fz[i],Z[i],r.std(),slow.std(),(r-slow).std(),n*dt)
d=sys.argv[1]; A=[]; per={}
for m in csv.DictReader(open(d+"/vocadito_metadata.csv")):
    if float(m["average_pitch"])>55: continue
    a=np.loadtxt(f"{d}/Annotations/F0/vocadito_{m['track_id']}_f0.csv",delimiter=","); dt=a[1,0]-a[0,0]
    f=a[:,1]; v=f>0; c=np.where(v,1200*np.log2(np.maximum(f,1)/100),np.nan)
    i=0; N=len(c)
    while i<N:
        if not v[i]: i+=1; continue
        j=i; 
        while j<N and v[j] and abs(c[j]-np.median(c[i:j+1]))<120: j+=1
        if (j-i)*dt>=1.0:
            k=int(0.15/dt); s=seg_stats(c[i+k:j-k//2],dt); A.append(s); per.setdefault(m['singer_id'],[]).append(s)
        i=max(j,i+1)
A=np.array(A); q=lambda c:"%.2f [%.2f %.2f]"%tuple(np.percentile(A[:,c],[50,25,75]))
print(f"== vocadito male, stable voiced segments >= 1 s: {len(A)} segments, {len(per)} singers (span median {np.median(A[:,5]):.2f} s)")
print("  vibrato rate Hz",q(0),"| extent cents",q(1)); print("  non-vibrato sd cents: all",q(2),"| slow <2 Hz",q(3),"| fast 2-12 Hz",q(4))
print("  share of segments with extent < 10 cents (straight tone): %.2f"%(A[:,1]<10).mean())
for s,v in per.items():
    v=np.array(v); print(f"   {s}: n={len(v)} rate {np.median(v[:,0]):.1f} extent {np.median(v[:,1]):.0f} slow {np.median(v[:,3]):.1f} fast {np.median(v[:,4]):.1f}")
