"""Consonant depth against the neighbouring vowel, from a stems render.

usage: consonant_depth.py STEMS_DIR   (lead.wav, plan.json, phones.json)

Stop closures, voiceless and voiced: the lowest 5 ms level in the closure against
the loudest 5 ms of the 60 ms before it, and the closure's 80-500 Hz and 0.5-5 kHz
band levels against the 60 ms that start 30 ms after it. Nasals: 80-500 Hz,
0.5-1.5 kHz and 1.5-5 kHz against the vowel after. Writes STEMS_DIR/segdepth.json.
"""
import json,sys,numpy as np,struct
def rd(p):
    b=open(p,'rb').read(); nch=struct.unpack_from('<H',b,22)[0]; return np.frombuffer(b,dtype='<f4',offset=44).reshape(-1,nch).mean(1).astype(float)
d=sys.argv[1]; sr=44100; h=int(0.005*sr)
x=rd(d+"/lead.wav"); plan=sorted(json.load(open(d+"/plan.json")),key=lambda s:s[1]); ph=json.load(open(d+"/phones.json"))
STOP={'p':0,'t':0,'k':0,'ch':0,'b':1,'d':1,'g':1,'jh':1}
stops=[p for n in ph for p in n['phones'] if p in STOP]; cl=[s for s in plan if s[0]=='closure']
e=20*np.log10(np.sqrt((x[:len(x)//h*h].reshape(-1,h)**2).mean(1))+1e-9)
def band(seg,lo,hi):
    X=np.abs(np.fft.rfft(seg*np.hanning(len(seg))))**2; f=np.fft.rfftfreq(len(seg),1/sr); return 10*np.log10(X[(f>=lo)&(f<hi)].sum()/len(seg)+1e-20)
out={}
if len(stops)==len(cl):
    for v,lab in ((0,"voiceless"),(1,"voiced")):
        A=[];B=[]
        for p,(k,a,b) in zip(stops,cl):
            if STOP[p]!=v or b-a<0.015: continue
            i0,i1=int(a*sr/h),max(int(b*sr/h),int(a*sr/h)+1); pre=e[max(i0-12,0):i0].max(); A.append(e[i0:i1].min()-pre)
            v0,v1=int((b+0.03)*sr),int((b+0.09)*sr); c=x[int(a*sr):int(b*sr)]; w=x[v0:v1]
            if len(c)>=256 and np.sqrt((w**2).mean())>1e-3: B.append((band(c,80,500)-band(w,80,500),band(c,500,5000)-band(w,500,5000)))
        A=np.array(A); B=np.array(B)
        out[lab]=dict(min_med=float(np.median(A)),share_above_m30=float((A>-30).mean()),lo=float(np.median(B[:,0])),hi=float(np.median(B[:,1])))
        print(f"  {lab:9s} closure n {len(A):3d}: min vs vowel before {np.median(A):6.1f} dB, above -30 dB {100*(A>-30).mean():3.0f}% | band vs next vowel: 80-500 {np.median(B[:,0]):+5.1f}, 0.5-5k {np.median(B[:,1]):+5.1f}")
else: print("  closures and stops differ:",len(cl),len(stops))
B=[]
for k,a,b in plan:
    if k!='nasal' or b-a<0.015: continue
    v0,v1=int((b+0.03)*sr),int((b+0.09)*sr); c=x[int(a*sr):int(b*sr)]; w=x[v0:v1]
    if v1<=len(x) and len(c)>=256 and np.sqrt((w**2).mean())>1e-3: B.append((band(c,80,500)-band(w,80,500),band(c,500,1500)-band(w,500,1500),band(c,1500,5000)-band(w,1500,5000)))
B=np.array(B); out['nasal']=dict(lo=float(np.median(B[:,0])),mid=float(np.median(B[:,1])),hi=float(np.median(B[:,2])))
print(f"  nasal     n {len(B):3d}: band vs next vowel: 80-500 {np.median(B[:,0]):+5.1f}, 0.5-1.5k {np.median(B[:,1]):+5.1f}, 1.5-5k {np.median(B[:,2]):+5.1f}")
json.dump(out,open(d+"/segdepth.json","w"))
