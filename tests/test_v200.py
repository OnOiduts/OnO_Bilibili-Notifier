# -*- coding: utf-8 -*-
"""锁定 v2.0.0 的三件事：

  1) **界面音效**：给动效配声，但必须"别太吵太刺耳"。
     吵不吵不能靠感觉写，得有硬指标 —— 这里把「只用柔和波形」
     「频率压在 330~990Hz」「每个音都有淡入淡出（否则是爆音）」
     「峰值增益 ≤ 0.2」全部断言住，并用假的 AudioContext 真跑一遍。

  2) **更新日志内容**：每条多一句摘要（不展开就知道改了什么），
     日志里的 `代码` 渲染成小代码块。

  3) **前端消息排版**：提示条不再是不换行的胶囊长条（长句会顶出屏幕外），
     成功/出错加图标，结果类提示统一带 ok 样式。

⚠️ 音效那部分在 tests/_sfx_test.js 里用 node 真跑，
   这里负责把它拉起来；跑不了（没 node）就跳过而不是假装通过。
"""
import io
import os
import re as _re
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


JS = read(os.path.join(_SRC, "static", "app.js"))
HTML = read(os.path.join(_SRC, "templates", "index.html"))
SN = read(os.path.join(_SRC, "static", "sound.js"))
VER = read(os.path.join(_SRC, "VERSION")).strip()
CL = read(os.path.join(_ROOT, "docs", "CHANGELOG.md"))

print("== v%s 界面音效 / 更新日志 / 提示排版 ==" % VER)

# ---------- 版本号 ----------
ok("VERSION 是合法语义化版本", _re.match(r"^\d+\.\d+\.\d+$", VER) is not None, VER)
ok("CHANGELOG 首条就是当前版本", CL.startswith("## v" + VER), CL[:40])
m = re.search(r"^## v2\.0\.0（(\d{4}-\d{2}-\d{2}[^）]*)）", CL, re.M)
ok("2.0.0 条目写了日期（历史条目都带，新条目不能漏）", bool(m),
   (m.group(1) if m else "没找到"))

# ---------- 音效：文件与接入 ----------
ok("sound.js 存在", bool(SN))
ok("index.html 引入了 sound.js", "/static/sound.js" in HTML)
ok("sound.js 在 app.js 之前引入（app.js 一上来就可能用）",
   HTML.find("/static/sound.js") < HTML.find("/static/app.js"))
ok("app.js 有统一的 sfx() 入口", "function sfx(" in JS)
ok("sfx 找不到 Sound 时不报错（不能因为音效挂掉界面）",
   "if (window.Sound" in JS and "catch" in JS)

# ---------- 音效：挂在各处动效上 ----------
ok("提示条配声（成功/出错）", re.search(r"sfx\(kind === 'err'", JS) is not None)
ok("折叠卡片配声", "sfx(d.open ? 'unfold' : 'fold')" in JS)
ok("开关拨动配声", re.search(r"sfx\('toggle'\)", JS) is not None)
ok("更新日志展开配声",
   re.search(r"sfx\(open \? 'unfold' : 'fold'\)", JS) is not None)
ok("主按钮配声", re.search(r"sfx\('click'\)", JS) is not None)
ok("音效档位可选（有 sound-change 动作）", "'sound-change'" in JS)
ok("设置页有音效下拉", 'id="cSound"' in HTML)
ok("下拉有关闭档位（嫌吵能关）",
   re.search(r'<option value="off">\s*关闭', HTML) is not None)

# ---------- 音效：不吵的关键约定 ----------
ok("页面打开时恢复折叠状态不发声（否则一开页哗啦响一片）",
   "FOLD_SFX_READY" in JS and "setTimeout(() => { FOLD_SFX_READY = true; }, 0)" in JS)
ok("音效档位存本地、不混进订阅数据", "soundLevel" in JS and "localStorage" in SN)

# ---------- 更新日志 ----------
import version  # noqa: E402

ents = version.changelog()
ok("更新日志能解析出条目", len(ents) > 0, len(ents))
ok("最新一条是当前版本", ents and ents[0]["version"] == VER,
   ents[0]["version"] if ents else "空")
ok("条目带一句话摘要", bool(ents and ents[0].get("summary")))
ok("前端渲染摘要（cl-sum）", 'class="cl-sum"' in JS)
ok("日志里的 `代码` 渲染成 <code>",
   re.search(r"\.replace\(/`\(\[\^`\]\+\)`/g", JS) is not None)
ok("先转义再替换 code（顺序反了 <code> 会被转义掉）",
   JS.find("esc(s).replace(/`") > 0)

# ---------- 提示排版 ----------
# ⚠️ 下面这条断言，注释里的 "white-space:nowrap" 会被当成真的样式
#    —— 那段注释正是在说明"以前是 nowrap"，结果把断言自己带红了。
#    所以先剥掉 CSS 注释再看。
_toast_blk = HTML.split("#toast {")[1].split("}")[0] if "#toast {" in HTML else ""
_toast_nc = re.sub(r"/\*.*?\*/", "", _toast_blk, flags=re.S)
ok("提示条不再 nowrap（长句会顶出屏幕外）",
   bool(_toast_nc) and "white-space:nowrap" not in _toast_nc, _toast_nc[:80])
ok("提示条允许换行（且确实写在 toast 块里）",
   "white-space:pre-wrap" in _toast_nc)
ok("提示条限宽", "max-width:min(560px" in HTML)
ok("成功提示带 ✅ 图标", "kind === 'ok' ? '✅ '" in JS)
ok("出错提示带 ⚠️ 图标", "kind === 'err' ? '⚠️ '" in JS)
ok("结果类提示统一带 ok 样式（保存/添加/删除/检测完成）",
   JS.count("'已删除', 'ok'") == 1 and JS.count("'设置已保存', 'ok'") == 1)
ok("「没拿到群 ID」已改成说法更清楚的文案",
   "没拿到群 ID" not in JS and "没认出是哪个群" in JS)

# ---------- 音效真跑一遍（node） ----------
node_js = os.path.join(_HERE, "_sfx_test.js")
if not os.path.exists(node_js):
    ok("音效实跑脚本存在", False, node_js)
else:
    try:
        r = subprocess.run(["node", node_js], capture_output=True,
                           text=True, timeout=60)
        out = (r.stdout or "") + (r.stderr or "")
        ok("音效实跑：无失败项", "[FAIL]" not in out,
           [ln for ln in out.splitlines() if "FAIL" in ln])
        ok("音效实跑：退出码 0", r.returncode == 0, r.returncode)
        tiny = re.search(r"小计：(\d+) 通过 / (\d+) 失败", out)
        ok("音效实跑有断言条数", bool(tiny) and int(tiny.group(1)) >= 10,
           tiny.group(0) if tiny else "没解析到")
    except FileNotFoundError:
        print("  [跳过] 环境没有 node，音效实跑未执行（不是代码问题）")
    except subprocess.TimeoutExpired:
        ok("音效实跑未超时", False, "timeout")

print()
print("小计：" + (str(len(FAILS)) + " 项失败" if FAILS else "全部通过"))
sys.exit(1 if FAILS else 0)
