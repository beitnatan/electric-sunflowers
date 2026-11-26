"""WAV reading and long-term average spectrum (LTAS) for the singing tools.

rd(path) returns (mono float samples, sample rate) for 16-bit, 24-bit and float
WAV files; other formats stop the run. ltas(x, sr) gives 1/3-octave levels on a
1/12-octave grid from 80 Hz, relative to 100-400 Hz, over the frames within 13 dB
of the loudest one. As a script it writes the median and quartiles of many files:
  wavread.py OUT.json WAV_GLOB...
No input directories are needed; the other tools import rd from this file.
"""
import numpy as np, sys, struct, glob, os, json
def rd(p):
    b=open(p,"rb").read()
    i=12; fmt=None
    while i<len(b)-8:
        cid=b[i:i+4]; sz=struct.unpack_from("<I",b,i+4)[0]
        if cid==b"fmt ": fmt=struct.unpack_from("<HHIIHH",b,i+8)
        if cid==b"data":
            tag,nch,sr,_,_,bits=fmt
            if tag==3: x=np.frombuffer(b,dtype="<f4",count=sz//4,offset=i+8).astype(float)
            elif bits==16: x=np.frombuffer(b,dtype="<i2",count=sz//2,offset=i+8)/32768.
            elif bits==24:
                a=np.frombuffer(b,dtype=np.uint8,count=sz//3*3,offset=i+8).reshape(-1,3).astype(np.int32)
                x=((a[:,0]|(a[:,1]<<8)|(a[:,2]<<16))^0x800000)-0x800000; x=x/8388608.
            else: raise SystemExit("fmt %s"%(fmt,))
            return x.reshape(-1,nch).mean(1),sr
        i+=8+sz+(sz&1)
PER=12; LO=80.; NB=86
C=LO*2**(np.arange(NB)/PER)
def ltas(x,sr,N=8192):
    w=np.hanning(N); fr=np.lib.stride_tricks.sliding_window_view(x,N)[::N//2]
    e=(fr**2).mean(1); fr=fr[e>0.05*e.max()]
    P=(np.abs(np.fft.rfft(fr*w,axis=1))**2).mean(0); f=np.fft.rfftfreq(N,1/sr)
    # 1/3-octave-wide window centred on each 1/12-octave point (smoothed)
    out=np.array([10*np.log10(P[(f>=c/2**(1/6))&(f<c*2**(1/6))].sum()+1e-30) for c in C])
    ref=10*np.log10(P[(f>=100)&(f<400)].sum())
    return out-ref
if __name__=="__main__":
    rows=[]; names=[]
    for p in sorted(sum([glob.glob(a) for a in sys.argv[2:]],[])):
        x,sr=rd(p); rows.append(ltas(x,sr)); names.append(os.path.basename(p))
    R=np.array(rows); med=np.median(R,0); q1,q3=np.percentile(R,[25,75],0)
    json.dump({"c":C.tolist(),"med":med.tolist(),"q1":q1.tolist(),"q3":q3.tolist(),"n":len(rows)},open(sys.argv[1],"w"))
    print(len(rows),"files")
    for i in range(0,NB,4): print(f"{C[i]:7.0f} {med[i]:6.1f}  [{q1[i]:6.1f} {q3[i]:6.1f}]")
