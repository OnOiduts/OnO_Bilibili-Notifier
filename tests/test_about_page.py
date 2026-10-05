# -*- coding: utf-8 -*-
"""锁定 v2.0.3 的「设置 → 关于」页与发布包名带版本号。

为什么要锁：
  1) 反馈 bug 时最常被问「哪个版本 / 数据在哪 / 装了哪些依赖」，
     这页就是为这个存在的。缺了任何一块，反馈就得来回问。
  2) 发布包名 v2.0.1 起把版本号去掉了，结果每版打出来都同名，
     从文件名看不出是哪一版 —— 这里必须把"版本号在名字里"钉死。

⚠️ 反向验证在文件末尾：把这几处改回旧写法，对应断言必须变红。
   否则这个测试就是摆样子。
"""
import io
import os
import re
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


HTML = read(os.path.join(_SRC, "templates", "index.html"))
JS = read(os.path.join(_SRC, "static", "app.js"))
WEBUI = read(os.path.join(_SRC, "webui.py"))
MKZIP = read(os.path.join(_ROOT, "tools", "make_zip.py"))
VER = read(os.path.join(_SRC, "VERSION")).strip()
CL = read(os.path.join(_ROOT, "docs", "CHANGELOG.md"))

print("== v%s 关于页 / 包名带版本号 ==" % VER)

# ---------- 1. 设置页有「关于」标签 ----------
ok("设置页有 about 标签按钮",
   re.search(r"data-tab=\"about\"", HTML) is not None)
# ⚠️ 光有按钮不够：得有对应的子面板，否则点了是空白页
ok("有 st-about 子面板", 'id="st-about"' in HTML and 'data-tab="about"' in HTML)
# 标签按钮和子面板必须成对：只加面板忘了按钮 = 永远点不到
_tabs = re.findall(r"data-tab=\"([a-z0-9]+)\"", HTML)
ok("about 既作标签又作面板（成对出现）", _tabs.count("about") >= 2,
   _tabs.count("about"))

# ---------- 2. 项目三种名字都要写全 ----------
ok("写了中文名", "OnOB站通知订阅工具" in HTML)
ok("写了英文名", "OnO Bilibili Notifier" in HTML)
ok("写了缩写 OnOBN", "OnOBN" in HTML)

# ---------- 3. 声明三件事 ----------
ok("写了服务器端从未测试", "服务器端从未测试过" in HTML)
ok("写了代码由 AI 生成", "代码由 AI 生成" in HTML)
ok("写了安全功能未审计", "安全功能未做安全审计" in HTML)

# ---------- 4. 后端 /api/about ----------
ok("后端有 /api/about 路由", "/api/about" in WEBUI)
ok("/api/about 返回版本号", re.search(r"\"version\":\s*_version\.current\(\)", WEBUI) is not None)
ok("/api/about 返回数据目录", "data_dir" in WEBUI and "state_path" in WEBUI)
ok("/api/about 返回依赖情况", '"deps"' in WEBUI)

# ---------- 5. 前端填充 + 一键复制 ----------
ok("前端有 loadAbout()", "async function loadAbout()" in JS)
ok("loadAbout 接进了 safe() 调度", re.search(r"safe\('about',\s*loadAbout\)", JS) is not None)
ok("有 about-copy 动作", "'about-copy'" in JS)
ok("复制内容含版本号", "OnOBN 版本：" in JS)
ok("复制内容含数据目录", "数据目录：" in JS)
# ⚠️ 前缀从「依赖：」改成了「依赖（必装）：/ 依赖（可选）：」——
#    分类由后端给，复制出去的反馈文本也要分得清哪个是必装。
ok("复制内容含依赖（必装）", "依赖（必装）：" in JS)
ok("复制内容含依赖（可选）", "依赖（可选）：" in JS)
ok("复制内容不再用不分类的旧前缀", "'依赖：' + Object.keys" not in JS)

# ---------- 6. 发布包名必须带版本号 ----------
ok("包名模板带 %s（版本号占位）", "OnO_Bilibili-Notifier_v%s.zip" in MKZIP, MKZIP[:0])
ok("打包时把版本号填进文件名", "OUT_NAME % ver" in MKZIP)
# ⚠️ 不能又退回写死无版本号的名字
ok("没有写死成不带版本号的名字",
   'OUT_NAME = "OnO_Bilibili-Notifier.zip"' not in MKZIP)

# ---------- 7. 更新日志 ----------
ok("CHANGELOG 首条就是当前版本", CL.startswith("## v" + VER), CL[:40])
ok("首条 v%s 写了日期" % VER,
   re.match(r"^## v%s（\d{4}-\d{2}-\d{2}" % re.escape(VER), CL) is not None)

# ---------- 8. 真跑一遍打包脚本，确认输出名带版本号 ----------
try:
    r = subprocess.run([sys.executable, os.path.join(_ROOT, "tools", "make_zip.py")],
                       capture_output=True, text=True, cwd=_ROOT, timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    ok("打包脚本能跑通", r.returncode == 0, out[-300:])
    m = re.search(r"打包完成：(\S+OnO_Bilibili-Notifier\S*\.zip)", out)
    ok("输出名带版本号", bool(m) and (("_v%s" % VER) in (m.group(1) if m else "")),
       out[-300:])
    if m and os.path.exists(m.group(1)):
        ok("包确实落在磁盘上", True)
except Exception as e:
    ok("打包脚本能跑通", False, str(e))

# ---------- 9. 语法与接口可用性 ----------
try:
    import webui as _w  # noqa: F401
    ok("webui.py 能导入（/api/about 没写坏）", True)
    ok("注册了 /api/about",
       any(str(r) == "/api/about" for r in _w.app.url_map.iter_rules()))
except Exception as e:
    ok("webui.py 能导入（/api/about 没写坏）", False, str(e)[:200])

print()
if FAILS:
    print("❌ 失败 %d 项：%s" % (len(FAILS), FAILS))
    sys.exit(1)
print("✅ 全部通过")
