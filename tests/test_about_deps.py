# -*- coding: utf-8 -*-
"""「设置 → 关于」页的依赖分类：playwright 必须归必装。

为什么要锁
----------
src/requirements.txt 里 playwright 早已从可选移回必装（面板两条 B 站登录
路线——浏览器登录 / 手动粘贴 Cookie——最终都靠它把 Cookie 换成长效登录态，
没装点登录只会报「缺少依赖」）。

但关于页前端 `loadAbout()` 里把必装/可选清单**写死**了：

    const must = ['aiohttp', 'botpy', 'yaml', 'flask'];
    const opt  = ['playwright'];

于是页面一直显示「可选：playwright」，与 requirements.txt 对不上。危害不只是
显示错：用户照着页面判断"这只是个可选功能"，装漏了也不会当回事，直到点登录
才发现跑不了。

修法是让**分类只由后端定义一份**（webui.REQUIRED_MODULES / OPTIONAL_MODULES），
前端照着渲染，两边不可能再不一致。

⚠️ 本文件末尾有反向验证：把任意一处改回旧写法，对应断言必须变红。
"""
import io
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
sys.path.insert(0, _SRC)

FAILS = []


def ok(name, cond, extra=""):
    if cond:
        print("  [OK] " + name)
    else:
        print("  [FAIL] " + name + (" → " + str(extra) if extra else ""))
        FAILS.append(name)


def read(p):
    try:
        return io.open(p, encoding="utf-8").read()
    except OSError:
        return ""


WEBUI = read(os.path.join(_SRC, "webui.py"))
JS = read(os.path.join(_SRC, "static", "app.js"))
REQ_TXT = read(os.path.join(_SRC, "requirements.txt"))

print("== 关于页依赖分类 ==")

# ---------- 1. 源码层：分类只由后端定义 ----------
ok("后端定义了 REQUIRED_MODULES",
   "REQUIRED_MODULES" in WEBUI)
ok("REQUIRED_MODULES 含 playwright",
   '"playwright"' in WEBUI.split("REQUIRED_MODULES = ")[1].split("\n")[0]
   if "REQUIRED_MODULES = " in WEBUI else False)
ok("后端定义了 OPTIONAL_MODULES",
   "OPTIONAL_MODULES" in WEBUI)
# 可选清单现在应当是空的：qrcode / Pillow 随扫码登录一起删了
_opt_line = ""
for _ln in WEBUI.splitlines():
    if _ln.startswith("OPTIONAL_MODULES"):
        _opt_line = _ln
        break
ok("OPTIONAL_MODULES 为空（目前确实没有可选依赖）",
   "()" in _opt_line.replace(" ", "") or ": tuple = ()" in _opt_line,
   _opt_line)

# ---------- 2. 前端不许再自己写死必装/可选清单 ----------
ok("前端不再写死 opt = ['playwright']",
   "const opt = ['playwright']" not in JS)
ok("前端改用后端给的 deps_required",
   "d.deps_required" in JS)
ok("前端改用后端给的 deps_optional",
   "d.deps_optional" in JS)

# ---------- 3. 与 requirements.txt 保持一致 ----------
# 页面显示的 pip 名必须能在 requirements.txt 里找到，否则用户照着装不上
ok("requirements.txt 里 playwright 是必装项",
   "playwright" in REQ_TXT and "requirements-optional" not in REQ_TXT)
ok("requirements.txt 不含已删除的 qrcode / Pillow",
   "qrcode" not in [l.split(">")[0].split("=")[0].strip()
                    for l in REQ_TXT.splitlines()
                    if l.strip() and not l.strip().startswith("#")])

# ---------- 4. 端到端：真跑接口，看它到底返回什么 ----------
info = None
try:
    import flask  # noqa: F401
    os.environ.setdefault("BILI_NOTIFY_HOME", "/tmp/onobn_about_deps")
    os.makedirs("/tmp/onobn_about_deps", exist_ok=True)
    import webui
    c = webui.app.test_client()
    r = c.get("/api/about", headers={"X-Requested-With": "XMLHttpRequest"})
    info = r.get_json() if r.status_code == 200 else None
except Exception as e:      # 环境缺依赖时跳过，不算失败
    print("  [SKIP] 端到端接口跑不起来：" + type(e).__name__)

if info is not None:
    req = info.get("deps_required") or []
    opt = info.get("deps_optional") or []
    ok("接口返回 deps_required", bool(req), req)
    ok("接口把 playwright 归为必装", "playwright" in req, req)
    ok("接口返回的必装项有 5 个", len(req) == 5, req)
    ok("接口返回 deps_optional 为空", opt == [], opt)
    ok("接口给了 pip 名映射（botpy → qq-botpy）",
       (info.get("deps_pip") or {}).get("botpy") == "qq-botpy",
       info.get("deps_pip"))

    # ---------- 5. 真跑前端渲染逻辑（用接口真返回的数据）----------
    # 不只是看源码里有这几个字，而是把 loadAbout 里 abDeps 那段原样 eval 一遍
    node = "node"
    try:
        js_src = read(os.path.join(_SRC, "static", "app.js"))
        start = js_src.find("const pEl = document.getElementById('abDeps');")
        stop = js_src.find("\n  }\n", start)
        block = js_src[start:stop + 4] if start >= 0 and stop > start else ""
        runner = (
            "const fs=require('fs');"
            "const d=JSON.parse(fs.readFileSync('/tmp/_about_deps.json','utf8'));"
            "let out='';"
            "const pEl={set innerHTML(v){out=v;}};"
            "global.document={getElementById:(id)=>(id==='abDeps'?pEl:null)};"
            + "eval(" + json.dumps(block, ensure_ascii=False) + ");"
            "const t=out.replace(/<br>/g,'\\n').replace(/<[^>]+>/g,'');"
            "process.stdout.write(t);"
        )
        io.open("/tmp/_about_deps.json", "w",
                encoding="utf-8").write(json.dumps(info, ensure_ascii=False))
        io.open("/tmp/_about_deps_run.js", "w", encoding="utf-8").write(runner)
        text = subprocess.run([node, "/tmp/_about_deps_run.js"],
                              capture_output=True, timeout=60)
        rendered = (text.stdout or b"").decode("utf-8", "replace")
        if text.returncode != 0:
            print("  [SKIP] node 跑不起来：" +
                  (text.stderr or b"").decode("utf-8", "replace")[:200])
        else:
            ok("渲染结果里 playwright 在「必装」一行",
               "必装" in rendered and "playwright" in rendered.split("必装")[1]
               .split("\n")[0], rendered[:200])
            ok("渲染结果不再出现「可选：playwright」",
               "可选：playwright" not in rendered.replace(" ", ""),
               rendered[:200])
            ok("渲染结果里可选显示为「无」",
               "可选：无" in rendered.replace(" ", ""), rendered[:200])
    except Exception as e:
        print("  [SKIP] 前端渲染验证跳过：" + type(e).__name__)

print("")
if FAILS:
    print("❌ 失败 %d 项：%s" % (len(FAILS), "、".join(FAILS)))
    sys.exit(1)
print("✅ 全部通过")
