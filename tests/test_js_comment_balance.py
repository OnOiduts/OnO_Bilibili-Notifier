# -*- coding: utf-8 -*-
"""锁住「块注释没闭合，把下面的函数整段吞进注释」这件事（v2.0.4）。

实况（用户截图）：
    点「🔄 刷新」→ 「refresh-state」出错了：renderHero is not defined

根因：
    src/static/app.js 里给 loadLegacyInfo 写说明时只写了 `/*`、没写收尾，
    于是从那一行起一直到**下一个** `*/`（几十行之后）全被当成注释：

        735  /* 旧版 data.db —— …新版落盘换成了 JSON，
        736  async function loadLegacyInfo() {   ← 进了注释，不是代码
        771  function renderHero() {             ← 同上

    函数声明被注释掉之后不会报语法错误（node --check 照样通过），
    只有跑到 loadState() 里调用 renderHero() 的那一刻才炸：
        ReferenceError: renderHero is not defined
    而且是**点刷新才炸**——首屏那次 loadState 之前别的报错把它盖住了，
    所以特别难联想。

    更阴的一点：被吞掉的是"声明"，不是"调用"——所以 grep renderHero
    能看到两处（一处调用、一处"声明"），肉眼完全看不出问题，
    必须真正解析一遍才知道那处声明压根不在语法树里。

防法（这个测试）：
    ① 逐字符扫一遍 JS（跳过字符串 / 模板串 / 正则 / 行注释），
       发现 `/*` 找不到收尾 → 直接失败
    ② 已经闭合的块注释里若含 `function xxx(` 这种整行 → 判定"吞了代码"
    ③ 拿 node 真正解析一遍（有 node 就跑，没有就跳过），
       确认渲染首屏要用的那几个函数确实在语法树里

    ⚠️ 第 ③ 条是关键：光看文本永远看不出来，必须解析。
"""
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
_STATIC = os.path.join(_SRC, "static")
_HTML = os.path.join(_SRC, "templates", "index.html")

PASS = 0
FAIL = 0
FAILED = []


def ck(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [ok] " + name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  [FAIL] " + name)


# ---------------------------------------------------------------- 扫描器
def _scan(src):
    """产出 (kind, start, end, text)；kind: block / line / block_open。

    会跳过单双引号字符串、模板串（含 ${} 嵌套）和正则字面量，
    免得字符串里的 `/*` 或 `"` 把状态带偏。
    """
    out = []
    i, n = 0, len(src)
    prev_sig = ""          # 上一个非空白字符，用来判断 / 是除号还是正则
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(("line", i, j, src[i:j]))
            i = j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            if j < 0:
                out.append(("block_open", i, n, src[i:]))
                i = n
                continue
            out.append(("block", i, j + 2, src[i:j + 2]))
            i = j + 2
            continue
        if c in "\"'":
            q = c
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == q:
                    i += 1
                    break
                if src[i] == "\n":
                    break
                i += 1
            continue
        if c == "`":
            i += 1
            depth = 0
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "`":
                    i += 1
                    break
                if src.startswith("${", i):
                    depth += 1
                    i += 2
                    continue
                if src[i] == "}" and depth:
                    depth -= 1
                i += 1
            continue
        if c == "/" and prev_sig in "([{,=:;!&|?+-*%~^<>":
            i += 1
            in_cls = False
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "[":
                    in_cls = True
                elif src[i] == "]":
                    in_cls = False
                elif src[i] == "/" and not in_cls:
                    i += 1
                    break
                elif src[i] == "\n":
                    break
                i += 1
            prev_sig = "/"
            continue
        if not c.isspace():
            prev_sig = c
        i += 1
    return out


_CODE_LINE = re.compile(r"^\s*(?:async\s+)?function\s+\w+\s*\(", re.M)


def _problems(name, src):
    bad = []
    for kind, s, e, txt in _scan(src):
        ls = src.count("\n", 0, s) + 1
        if kind == "block_open":
            bad.append("%s 第 %d 行的块注释没闭合，会把后面的代码整段吞掉"
                       % (name, ls))
        elif kind == "block":
            body = txt[2:-2]
            if _CODE_LINE.search(body):
                le = src.count("\n", 0, e) + 1
                bad.append("%s 第 %d-%d 行的块注释里含函数定义，疑似吞了代码"
                           % (name, ls, le))
    return bad


# ---------------------------------------------------------------- ① 文本扫描
print("== 1. 静态资源：块注释必须成对、不许含整段函数 ==")
_targets = []
for _f in sorted(os.listdir(_STATIC)):
    if _f.endswith(".js"):
        _targets.append(os.path.join(_STATIC, _f))
ck(bool(_targets), "找到静态 JS 文件（%d 个）" % len(_targets))

_bad = []
for _p in _targets:
    with open(_p, "r", encoding="utf-8") as _fh:
        _bad += _problems(os.path.basename(_p), _fh.read())
ck(not _bad, "静态 JS 没有未闭合 / 吞代码的块注释")
for _b in _bad:
    print("       ! " + _b)

print("\n== 2. index.html 内联脚本同样要干净 ==")
_html = ""
if os.path.isfile(_HTML):
    with open(_HTML, "r", encoding="utf-8") as _fh:
        _html = _fh.read()
_inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                     _html, re.S)
ck(bool(_inline), "index.html 里找到内联脚本（%d 段）" % len(_inline))
_bad2 = []
for _i, _code in enumerate(_inline):
    _bad2 += _problems("index.html 内联#%d" % (_i + 1), _code)
ck(not _bad2, "内联脚本没有未闭合 / 吞代码的块注释")
for _b in _bad2:
    print("       ! " + _b)

# ---------------------------------------------------------------- ② 真解析
print("\n== 3. 真解析一遍：首屏要用的函数必须在语法树里 ==")
_node = None
for _cand in ("node", "nodejs"):
    try:
        subprocess.run([_cand, "--version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        _node = _cand
        break
    except Exception:
        continue

_WANT = ["renderHero", "loadState", "renderStats", "renderGuide",
         "renderGroups", "loadLegacyInfo", "fmtUptime"]

if not _node:
    print("  [跳过] 环境里没有 node，跳过 AST 解析检查")
else:
    _js = r"""
const fs=require('fs'),vm=require('vm');
const file=process.argv[2];   // argv[1] 是探针自己
const code=fs.readFileSync(file,'utf8');
// vm 里跑一遍：函数声明会被提升，能在全局拿到的才算"真的是代码"
const el=new Proxy({},{get:(t,p)=>{
  if(p==='style')return {};
  if(p==='classList')return {add(){},remove(){},toggle(){},contains(){return false}};
  if(p==='dataset')return {};
  if(p==='children')return [];
  if(p==='textContent'||p==='innerHTML'||p==='value')return '';
  return ()=>{};
},set:()=>true});
const document={getElementById:()=>el,querySelector:()=>el,querySelectorAll:()=>[],
  createElement:()=>el,addEventListener:()=>{},body:el,documentElement:el,
  execCommand:()=>{}};
const ctx={console,document,
  window:{addEventListener:()=>{},matchMedia:()=>({matches:false,addEventListener(){}})},
  navigator:{clipboard:null,userAgent:'node'},
  localStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},
  fetch:()=>Promise.resolve({json:()=>({}),ok:true}),
  setTimeout,clearTimeout,setInterval,clearInterval,
  location:{href:'',protocol:'http:'},alert:()=>{},confirm:()=>true,
  requestAnimationFrame:(f)=>f&&f(),Date,JSON,Math,
  encodeURIComponent,decodeURIComponent,Intl};
ctx.window.document=document;ctx.window.localStorage=ctx.localStorage;
ctx.window.navigator=ctx.navigator;ctx.globalThis=ctx;
const sb=vm.createContext(ctx);
let loadErr='';
try{ vm.runInContext(code,sb,{filename:file}); }catch(e){ loadErr=e.message+' | '+(e.stack||'').split('\n').slice(0,3).join(' >> '); }
const want=process.argv.slice(3);
const got={};
want.forEach(n=>{ try{ got[n]=vm.runInContext('typeof '+n,sb); }catch(e){ got[n]='ERR'; } });
console.log(JSON.stringify({loadErr:loadErr,got:got}));
"""
    _tmp = os.path.join(_HERE, "_js_scope_probe.js")
    with open(_tmp, "w", encoding="utf-8") as _fh:
        _fh.write(_js)
    try:
        _appjs = os.path.join(_STATIC, "app.js")
        _r = subprocess.run([_node, _tmp, _appjs] + _WANT,
                            capture_output=True, text=True, timeout=120)
        import json as _json
        _data = _json.loads(_r.stdout.strip().splitlines()[-1])
        print("       （加载期报错：%s）" % (_data["loadErr"] or "无"))
        for _n in _WANT:
            ck(_data["got"].get(_n) == "function",
               "app.js 里 %s 是真函数（实测 typeof = %s）"
               % (_n, _data["got"].get(_n)))
    finally:
        if os.path.isfile(_tmp):
            os.remove(_tmp)

# ---------------------------------------------------------------- ③ 反向验证
print("\n== 4. 反向验证：把注释收尾去掉，检查必须能抓到 ==")
_appjs = os.path.join(_STATIC, "app.js")
with open(_appjs, "r", encoding="utf-8") as _fh:
    _orig_src = _fh.read()
_lines = _orig_src.split("\n")
_start = None
for _i, _l in enumerate(_lines):
    if "旧版 data.db" in _l and _l.strip().startswith("/*"):
        _start = _i
        break
ck(_start is not None, "定位到 loadLegacyInfo 上方的那条块注释")
if _start is not None:
    _end = None
    for _j in range(_start + 1, _start + 12):
        if _lines[_j].rstrip().endswith("*/"):
            _end = _j
            break
    ck(_end is not None, "那条注释确实有收尾（现状是好的）")
    if _end is not None:
        _broken = _lines[:]
        _broken[_end] = _broken[_end].rstrip()[:-2]
        _bad3 = _problems("broken", "\n".join(_broken))
        ck(bool(_bad3), "去掉收尾后，检查立刻报错（断言咬得住）")
        if _bad3:
            print("       ! " + _bad3[0])

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAILED:
    print("FAILED:")
    for _n in FAILED:
        print("  -", _n)
    sys.exit(1)
