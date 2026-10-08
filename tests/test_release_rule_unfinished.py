import io,os
BASE=os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '/data/workspace/bili-notify'
BASE='/data/workspace/bili-notify'
KEY="没做完的不算数"
def read(p): return io.open(os.path.join(BASE,p),encoding="utf-8").read()
def check():
    ok=True
    for p in ("README.md","docs/README.md"):
        s=read(p); 
        if KEY not in s: print("FAIL",p,"缺『"+KEY+"』"); ok=False
        if "不涨号" not in s: print("FAIL",p,"缺不涨号"); ok=False
    v=read("src/version.py")
    if "没做完 / 还在修的功能" not in v: print("FAIL version.py 缺规则"); ok=False
    if "已经验过的东西" not in v: print("FAIL version.py 缺说明"); ok=False
    # 举例必须与当前版本一致
    cur=read("src/VERSION").strip()
    if cur!="2.3.4": print("WARN 当前版本",cur)
    print("PASS" if ok else "FAIL")
check()
