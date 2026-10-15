"""最小 TC32 反組譯器，編碼表取自 trust1995 Ghidra_TELink_TC32 的 Telink_TC32.slaspec。
只為讀懂 OTA / flash 相關程式，涵蓋常見指令，少數罕見指令印成 .word。"""
import sys, struct

def f(w, a, b):  # op_a_b = bits[a..a+b-1]
    return (w >> a) & ((1 << b) - 1)

R = lambda n: ("sp" if n==13 else "lr" if n==14 else "pc" if n==15 else f"r{n}")

# 指令表：每筆 (助記, [(a,b,val)...約束], 格式函式)
def hi70(w): return (f(w,7,1)<<3)|f(w,0,3)
def hi63(w): return (f(w,6,1)<<3)|f(w,3,3)

TBL = []
def I(mnem, cons, fmt): TBL.append((mnem, cons, fmt))

I("tadds",  [(9,7,0x76)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}, #{f(w,6,3)}")
I("tadds",  [(9,7,0x74)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}, r{f(w,6,3)}")
I("tadds",  [(11,5,0x16)], lambda w:f"r{f(w,8,3)}, #{f(w,0,8)}")
I("tadd",   [(11,5,0x0e)], lambda w:f"r{f(w,8,3)}, pc, #{f(w,0,8)*4}")
I("tadd",   [(11,5,0x0f)], lambda w:f"r{f(w,8,3)}, sp, #{f(w,0,8)*4}")
I("tadd",   [(8,8,0x04)],  lambda w:f"{R(hi70(w))}, {R(hi63(w))}")
I("tadd",   [(7,9,0xc0)],  lambda w:f"sp, #{f(w,0,7)*4}")
I("taddcs", [(6,10,0x05)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tands",  [(6,10,0x00)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tasrs",  [(6,10,0x04)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tasrs",  [(11,5,0x1c)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}, #{f(w,6,5)}")
I("tbclrs", [(6,10,0x0e)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tcmp",   [(6,10,0x0a)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tcmp",   [(11,5,0x15)], lambda w:f"r{f(w,8,3)}, #{f(w,0,8)}")
I("tcmp",   [(8,8,0x05)],  lambda w:f"{R(hi70(w))}, {R(hi63(w))}")
I("tcmpn",  [(6,10,0x0b)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tjex",   [(7,9,0x0e)],  lambda w:f"{R(hi63(w))}")
I("tloadm", [(11,5,0x1b)], lambda w:f"r{f(w,8,3)}!, {{{reglist(f(w,0,8))}}}")
I("tloadr", [(9,1,0),(11,5,0x03),(10,1,0)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, r{f(w,6,3)}]")
I("tloadrb",[(9,1,0),(11,5,0x03),(10,1,1)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, r{f(w,6,3)}]")
I("tloadr", [(11,5,0x0b)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, #{f(w,6,5)*4}]")
I("tloadr", [(11,5,0x01)], lambda w:f"r{f(w,8,3)}, [pc, #{f(w,0,8)*4}]")
I("tloadr", [(11,5,0x07)], lambda w:f"r{f(w,8,3)}, [sp, #{f(w,0,8)*4}]")
I("tloadrb",[(11,5,0x09)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, #{f(w,6,5)}]")
I("tloadrh",[(11,5,0x05)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, #{f(w,6,5)*2}]")
I("tloadrh",[(9,7,0x0d)],  lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, r{f(w,6,3)}]")
I("tloadrsb",[(9,2,0x03),(12,4,0x01),(11,1,0)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, r{f(w,6,3)}]")
I("tloadrsh",[(9,2,0x03),(12,4,0x01),(11,1,1)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, r{f(w,6,3)}]")
I("tmcsr",  [(3,13,0x0d78)], lambda w:f"r{f(w,0,3)}")
I("tmovs",  [(11,5,0x14)], lambda w:f"r{f(w,8,3)}, #{f(w,0,8)}")
I("tmov",   [(8,8,0x06)],  lambda w:f"{R(hi70(w))}, {R(hi63(w))}")
I("tmovns", [(6,10,0x0f)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tmrcs",  [(3,13,0x0d79)], lambda w:f"r{f(w,0,3)}")
I("tmrss",  [(3,13,0x0d7b)], lambda w:f"r{f(w,0,3)}")
I("tmssr",  [(3,13,0x0d7a)], lambda w:f"r{f(w,0,3)}")
I("tmuls",  [(6,10,0x0d)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tnand",  [(6,10,0x08)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tnegs",  [(6,10,0x09)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tnop",   [(0,16,0x46c0)], lambda w:"")
I("tors",   [(6,10,0x0c)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tpop",   [(9,7,0x36)],  lambda w:f"{{{reglist(f(w,0,8))}{', pc' if f(w,8,1) else ''}}}")
I("tpush",  [(9,7,0x32)],  lambda w:f"{{{reglist(f(w,0,8))}{', lr' if f(w,8,1) else ''}}}")
I("treti",  [(9,7,0x34)],  lambda w:f"{{{reglist(f(w,0,8))}, pc}}")
I("trotrs", [(6,10,0x07)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tserv",  [(8,8,0xcf)],  lambda w:f"#{f(w,0,8)}")
I("tshftls",[(6,10,0x02)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tshftls",[(11,5,0x1e)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}, #{f(w,6,5)}")
I("tshftrs",[(6,10,0x03)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("tshftrs",[(11,5,0x1f)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}, #{f(w,6,5)}")
I("tstorem",[(11,5,0x1a)], lambda w:f"r{f(w,8,3)}!, {{{reglist(f(w,0,8))}}}")
I("tstorerb",[(9,1,0),(11,5,0x02),(10,1,1)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, r{f(w,6,3)}]")
I("tstorer", [(9,1,0),(11,5,0x02),(10,1,0)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, r{f(w,6,3)}]")
I("tstorer", [(11,5,0x0a)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, #{f(w,6,5)*4}]")
I("tstorer", [(11,5,0x06)], lambda w:f"r{f(w,8,3)}, [sp, #{f(w,0,8)*4}]")
I("tstorerb",[(11,5,0x08)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, #{f(w,6,5)}]")
I("tstorerh",[(11,5,0x04)], lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, #{f(w,6,5)*2}]")
I("tstorerh",[(9,7,0x09)],  lambda w:f"r{f(w,0,3)}, [r{f(w,3,3)}, r{f(w,6,3)}]")
I("tsubs",  [(9,7,0x77)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}, #{f(w,6,3)}")
I("tsubs",  [(9,7,0x75)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}, r{f(w,6,3)}")
I("tsubs",  [(11,5,0x17)], lambda w:f"r{f(w,8,3)}, #{f(w,0,8)}")
I("tsub",   [(7,9,0xc1)], lambda w:f"sp, #{f(w,0,7)*4}")
I("tsubcs", [(6,10,0x06)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")
I("txors",  [(6,10,0x01)], lambda w:f"r{f(w,0,3)}, r{f(w,3,3)}")

def reglist(bits):
    return ", ".join(f"r{i}" for i in range(8) if bits&(1<<i))

# 預先算每筆的 mask/match 與特定度
PREP=[]
for mnem,cons,fmt in TBL:
    mask=match=0
    for a,b,v in cons:
        m=((1<<b)-1)<<a; mask|=m; match|=(v<<a)&m
    PREP.append((bin(mask).count("1"),mask,match,mnem,fmt))
PREP.sort(key=lambda x:-x[0])

CC=["eq","ne","cs","cc","mi","pl","vs","vc","hi","ls","ge","lt","gt","le","","nv"]

def dis_one(w, addr, w2=None):
    # tj 條件短跳
    if f(w,12,4)==0x0c and f(w,8,4)!=0x0f:
        off=f(w,0,8); off-=256 if off&0x80 else 0
        return f"tj{CC[f(w,8,4)]}", f"{addr+4+off*2:#x}", 2
    if f(w,11,5)==0x10:  # tj 無條件
        off=f(w,0,11); off-=0x800 if off&0x400 else 0
        return "tj", f"{addr+4+off*2:#x}", 2
    if f(w,11,5)==0x12 and w2 is not None:  # tjl 長跳(2 halfword)
        hi=f(w,0,11); hi-=0x800 if hi&0x400 else 0
        tgt=(addr+4)+(hi<<12)+(f(w2,0,11)<<1)
        return "tjl", f"{tgt:#x}", 4
    for _,mask,match,mnem,fmt in PREP:
        if (w&mask)==match:
            return mnem, fmt(w), 2
    return ".hword", f"{w:#06x}", 2

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    d=open(sys.argv[1],"rb").read()
    start=int(sys.argv[2],16); end=int(sys.argv[3],16)
    a=start
    while a<end:
        w=struct.unpack_from("<H",d,a)[0]
        w2=struct.unpack_from("<H",d,a+2)[0] if a+3<len(d) else None
        mn,ops,sz=dis_one(w,a,w2)
        raw=d[a:a+sz].hex()
        print(f"{a:#07x}: {raw:<9} {mn:9} {ops}")
        a+=sz

if __name__=="__main__":
    main()
