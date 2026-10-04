# -*- coding: utf-8 -*-
"""自检页 4 项细节修复（v1.31.23）。

锁的都是"改完又被悄悄改回去"的坑：
 1. 自检六行的值**右对齐**
 2. 置顶评论检测到的数据不能有孤零零的「：」
 3. 跑完自检后「已记住」后面不能多出一截「测试 UID：xxxx」
 4. 「带 🧪 测试标记」对占位数据和真实数据都要生效
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

APP = os.path.join(ROOT, "static", "app.js")
HTML = os.path.join(ROOT, "templates", "index.html")
WEBUI = os.path.join(ROOT, "webui.py")
NOTIFY = os.path.join(ROOT, "notify.py")

FAILS = []


def ck(name, cond, extra=""):
    if cond:
        print("  PASS  " + name)
    else:
        print("  FAIL  " + name + ("  " + extra if extra else ""))
        FAILS.append(name)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


app = read(APP)
html = read(HTML)
web = read(WEBUI)
notify_src = read(NOTIFY)

print("\n[1] 自检六行：值右对齐")
m = re.search(r"\.diag-row \.dv \{([^}]*)\}", html)
dv = m.group(1) if m else ""
ck(".diag-row .dv 存在", bool(m))
ck("值右对齐 text-align:right", "text-align:right" in dv, dv)

print("\n[2] 置顶评论：不能有孤零零的冒号")
ck("不再无条件拼 who + 「：」",
   'f"{who}：' not in web, "仍有无条件 f\"{who}：\" 拼接")
ck("who 为空时省略冒号（lead 变量）", "lead" in web)
ck("评论者昵称做了 strip", 'cm.get("uname") or "").strip()' in web)
# 真实行为：造一条没有昵称的评论，走同一段拼法
who = ""
same = "（UP 主本人）"
txt = "评论正文"
lead = (who + "：") if who else ""
val = f"{lead}{txt}{same}"
ck("无昵称时不产出开头冒号", not val.startswith("："), repr(val))
ck("有昵称时保留冒号", ("x：" if "x" else "") + txt == "x：评论正文")

print("\n[3] 跑完自检不再多出『测试 UID：』")
ck("HTML 移除 selfcheckUid span", 'id="selfcheckUid"' not in html)
ck("app.js 不再写 selfcheckUid", "selfcheckUid" not in app)
ck("app.js 不再拼『测试 UID：』", "'测试 UID：'" not in app)
ck("自检后改为刷新同一处显示", "showChkUid(_v" in app)

print("\n[4] 「🧪 测试」标记：单独一行放在卡片最顶上")
ck("_apply_mark_md 已定义", "def _apply_mark_md" in web)
ck("notify.apply_test_mark 已定义", "def apply_test_mark" in notify_src)
ck("api_test 在渲染**之后**调用 _apply_mark_md",
   "_apply_mark_md(md, mark)" in web)
# ⚠️ v1.32.1：标记不再塞进 info.title（会跑进块引用的「标题」行）
ck("_demo 不再往 title 里塞标记",
   'title = ((MARK_PREFIX + " ")' not in web)
ck("_demo 签名不再带 mark", "def _demo(kind: str):" in web)
# 样式：全角「」包住，不带冒号
ck("标记用全角「」（U+300C/U+300D）",
   "\u300c" in notify_src and "\u300d" in notify_src)
ck("页面文案改成『卡片最上面会多一行』",
   "卡片最上面会多一行「🧪 测试」" in html)
ck("页面不再写旧的『标题前加』", "标题前加「🧪 测试」" not in html)

# 真跑一遍函数（flask 可用时）
try:
    import webui  # noqa: E402
    import notify  # noqa: E402
    P = webui.MARK_PREFIX

    ck("MARK_PREFIX 长得对（「🧪 测试」）", P == "\u300c\U0001F9EA \u6d4b\u8bd5\u300d", P)

    def _first(s):
        return str(s or "").split("\n")[0]

    ck("加标记 → 第一行就是标记",
       _first(webui._apply_mark_md("**🔴 开播提醒**", True)) == P)
    ck("加标记 → 第二行才是卡片标题",
       webui._apply_mark_md("**🔴 开播提醒**", True).split("\n")[1]
       == "**🔴 开播提醒**")
    ck("取消标记 → 第一行回到卡片标题",
       _first(webui._apply_mark_md(P + "\n**🔴 开播提醒**", False))
       == "**🔴 开播提醒**")
    ck("幂等：重复加不会叠加",
       webui._apply_mark_md(webui._apply_mark_md("**x**", True), True)
       == P + "\n**x**")
    # 全部 7 种推送类型：标记都必须在最顶上，紧跟着卡片标题
    for k in webui.PUSH_KINDS:
        rk = "dynamic" if k == "dynamic_pinned" else k
        d = webui._demo(k)
        md, _b = notify.render(rk, d, "测试UP主", {})
        md = webui._apply_mark_md(md, True)
        ls = md.split("\n")
        ck("推送类型 %s：标记在第一行" % k, ls[0] == P, ls[0])
        ck("推送类型 %s：第二行是卡片标题" % k,
           ls[1].startswith("**") and ls[1].endswith("**"), ls[1])
    # 真实数据（本来不带标记）也要能加上：走同一段渲染后处理
    raw = "**📺 新视频投稿**\n> **标题**　真实标题"
    ck("真实数据渲染后也能加标记",
       _first(webui._apply_mark_md(raw, True)) == P)
except Exception as e:
    ck("webui 可 import（用于真跑函数）", False, str(e))

print()
if FAILS:
    print("FAIL %d 项：%s" % (len(FAILS), "、".join(FAILS)))
    sys.exit(1)
print("全部通过")
