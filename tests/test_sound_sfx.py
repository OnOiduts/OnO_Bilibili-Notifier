# -*- coding: utf-8 -*-
"""界面音效丰富度、一次性动画可重播、关于页重写与使用声明标红。

三件事都是「界面手感」类，为什么值得写测试：
  1. 音效种类会越加越少——有人嫌某个音吵就顺手删掉，删了也不报错；
  2. 「动画只播一次」是 CSS 的经典陷阱，改回 classList.add 一样通过评审，
     但用户看到的就是"第一次有效果，之后没了"；
  3. 使用声明是**必须被看见**的信息，被人改成普通灰色提示就白写了。
"""
import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

SRC = os.path.join(_ROOT, "src")
SOUND = os.path.join(SRC, "static", "sound.js")
APP = os.path.join(SRC, "static", "app.js")
STYLE = os.path.join(SRC, "static", "style.css")
HTML = os.path.join(SRC, "templates", "index.html")
WEBUI = os.path.join(SRC, "webui.py")

n = 0
bad = 0


def ck(name, cond, extra=""):
    global n, bad
    n += 1
    if cond:
        print("  ok   %s" % name)
    else:
        bad += 1
        print("  FAIL %s %s" % (name, extra))


def _read(p):
    with io.open(p, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


print("[音效丰富度]")

snd = _read(SOUND)
app = _read(APP)

# 音效表里实际有多少种：按 "名字: [{" 计数
names = re.findall(r"^\s{4}(\w+):\s*\[\{", snd, re.M)
ck("音效种类 >= 24 种", len(names) >= 24, "实际 %d 种：%s" % (len(names), names))
# 关键场景必须各有其声，不能全靠一个 ok 打天下
for must in ("ok", "err", "warn", "save", "del", "add", "remove", "clear",
             "copy", "fold", "unfold", "panel", "panelClose", "page", "tab",
             "refresh", "connect", "connected", "disconnect", "login",
             "import", "export", "download", "notify", "bell", "click",
             "tap", "toggle", "done"):
    ck("有 %s 音效" % must, must in names, "缺 %s" % must)

# 不吵不刺耳的硬指标（新增音效时最容易破的几条）
ck("只用柔和波形（无 square/sawtooth）",
   "square" not in snd and "sawtooth" not in snd)
freqs = [int(x) for x in re.findall(r"f[01]:\s*(\d+)", snd)]
ck("频率压在 330~900Hz", freqs and min(freqs) >= 330 and max(freqs) <= 900,
   "实际 %d~%d" % (min(freqs), max(freqs)) if freqs else "没读到频率")
ck("有淡入（防爆音）", "exponentialRampToValueAtTime(peak, now + 0.01)" in snd)
_g = re.search(r"var GAIN = \{([^}]*)\}", snd)
# ⚠️ 只取冒号**右边**的值：左边那些 '1' '2' '3' 是档位编号，
#    一并抓进来会把 3 当成增益，于是"峰值 <= 0.2"永远量不到真正的峰值。
_peak = max([float(x) for x in re.findall(r":\s*([\d.]+)", _g.group(1))]) if _g else 0
ck("峰值增益 <= 0.2", _peak <= 0.2, "实际 %s" % _peak)
ck("有节流（连点不成串）", "_throttle" in snd)
_th = re.search(r"_throttle\s*=\s*(\d+)", snd)
ck("节流窗口 >= 140ms", _th and int(_th.group(1)) >= 140,
   _th.group(1) if _th else "无")

# 每种音效都要在 MASTER_SCALE 里登记：漏了会按满音量响，那正是"吵"的来源
_ms = re.search(r"var MASTER_SCALE = \{(.*?)\n  \};", snd, re.S)
scaled = set(re.findall(r"(\w+):\s*[\d.]+", _ms.group(1))) if _ms else set()
missing = [x for x in names if x not in scaled]
ck("每种音效都配了音量系数", not missing, "漏配：%s" % missing)

print("\n[音效调用点]")

# 动作 → 音效映射表
ck("有 ACT_SFX 映射表", "const ACT_SFX = {" in app)
_act = re.search(r"const ACT_SFX = \{(.*?)\n\};", app, re.S)
_pairs = re.findall(r"'([^']+)':\s*'([^']+)'", _act.group(1)) if _act else []
ck("映射条目 >= 30 条", len(_pairs) >= 30, "实际 %d 条" % len(_pairs))
ck("映射的音效名都真实存在",
   all(v in names for _, v in _pairs),
   "无效：%s" % [v for _, v in _pairs if v not in names])
ck("分发处调了 actSfx", "const _s = actSfx(act);" in app and "sfx(_s)" in app)
ck("有前缀回退规则（新动作自动有声）",
   "/^refresh-/.test(act)" in app and "/^(save|set)-/.test(act)" in app)
# 侧栏不走 data-act 委托，必须单独配音效
ck("侧栏切板块也有声", "sfx('page'); openSec(sec);" in app)
# 弹层打开
ck("弹层打开有 panel 音", "sfx('panel');" in app)
# 全局计数：直接调用点（排除定义行 function sfx( 与注释里那一次）
calls = len([m for m in re.findall(r"sfx\(", app)])
_direct = app.count("sfx(") - app.count("function sfx(")
ck("sfx 直接调用点 >= 10 处", _direct >= 10, "实际 %d 处" % _direct)
ck("音效覆盖面：映射 30+ 条且分发处动态调用",
   len(_pairs) >= 30 and "sfx(_s)" in app)

print("\n[一次性动画可重播]")

ck("有 restartFx 工具函数", "function restartFx(" in app)
ck("restartFx 有强制重排（关键一步）", "void el.offsetWidth" in app)
_rf = re.search(r"function restartFx\(.*?\n\}", app, re.S)
ck("restartFx 会清掉上一次的定时器",
   _rf and "clearTimeout" in _rf.group(0),
   "否则连点时旧定时器会提前摘掉新动画")

# 三处一次性动画必须走 restartFx，不能回退成直接 add
for cls in ("spin-once", "fx-pulse", "push-ok"):
    ck("%s 走 restartFx" % cls,
       re.search(r"restartFx\([^)]*'%s'" % cls, app) is not None,
       "仍然在直接 classList.add('%s')" % cls)
    ck("%s 不再直接 add" % cls,
       re.search(r"classList\.add\('%s'\)" % cls, app) is None,
       "还有直接 add 的写法，会导致第二次不播")

print("\n[关于页与使用声明]")

html = _read(HTML)
ck("关于页有「这是什么」", "ℹ️ 这是什么" in html)
ck("关于页说明机器人是官方接口",
   "QQ 开放平台" in html and "不是" in html)
ck("关于页有 B 站真实主页", "space.bilibili.com/259149243" in html)
ck("关于页有维护状态", "维护状态" in html)
ck("关于页有反馈指南", "📮 反馈" in html)

# 使用声明标红
ck("使用声明标题标红", '<h3 class="danger">' in html)
ck("使用声明整块标红（note danger）", html.count('class="note danger"') >= 3,
   "实际 %d 块" % html.count('class="note danger"'))
ck("声明里写了服务器端从未测试", "服务器端从未测试过" in html)
ck("声明里写了代码由 AI 生成", "代码由 AI 生成" in html)
ck("声明里写了安全未审计", "安全功能未做安全审计" in html)
ck("反馈里的打码提示也标红", "截图前请先打码" in html)
# 声明块必须在关于页的 sub-panel 里
_seg = html[html.index('id="st-about"'):html.index("</main>")]
ck("声明块确实在关于页内", 'class="note danger"' in _seg)

# 标红样式要真的存在
css = _read(STYLE)
ck("有 .note.danger 样式", ".note.danger" in css)
ck("标红用 danger 变量而非写死颜色",
   "var(--danger" in css.split(".note.danger")[1][:300])
ck("h3.danger 标题也标红", "h3.danger" in css)

print("\n[后端声明文案]")

wb = _read(WEBUI)
ck("后端声明提到服务器端未实测", "服务器端从未测试过" in wb)
ck("后端声明提到代码由 AI 生成", "代码由 AI 生成" in wb)
ck("后端声明提到安全未审计", "安全功能未做安全审计" in wb)
ck("后端声明提到维护状态", "维护状态" in wb)
ck("维护状态那条提到功能更新", "功能更新" in wb)
ck("无「措辞措辞」类笔误", "措辞措辞" not in html and "措辞措辞" not in wb)

print("\n通过 %d / 失败 %d" % (n - bad, bad))
sys.exit(1 if bad else 0)
