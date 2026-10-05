# -*- coding: utf-8 -*-
"""动态推送文案瘦身（v2.2.5）：去掉「动态内容」标签行、不显示粉丝勋章。

用户原话：
    动态推送的消息里可以把"动态内容"这句话删掉，
    还有粉丝勋章例如 [伊万勋章]这种也可以不显示了

两条都是"推送里多出来、看了没用"的东西：
  · 「动态内容」只是个标签，正文自己就说明了一切；
  · 粉丝勋章是 B 站装扮体系里的东西，在 QQ 里只是一串方括号名字，
    尤其开播动态常常**正文只有勋章** —— 删掉它，那条动态就干净了。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, "/data/workspace/_stub2")

import bilibili as B  # noqa: E402
import notify  # noqa: E402

PASS = FAIL = 0
FAILED = []


def ck(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] " + name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("[FAIL] " + name)


class Dyn:
    def __init__(self, **kw):
        self.title = kw.get("title", "")
        self.text = kw.get("text", "")
        self.images = kw.get("images", [])
        self.url = kw.get("url", "https://www.bilibili.com/opus/1")
        self.pinned = kw.get("pinned", False)
        self.major_type = kw.get("major_type", "")
        self.ref_room_id = kw.get("ref_room_id", 0)


# ── 1. 「动态内容」标签行不再出现 ───────────────────────────────
md, _ = notify.render_dynamic(Dyn(title="", text="今天也要开开心心"), "伊万酱", {})
ck("> **动态内容**" not in md, "普通动态：不再有「动态内容」标签行")
ck("今天也要开开心心" in md, "普通动态：正文仍然在")

md2, _ = notify.render_dynamic(Dyn(title="新周边", text="内容文本"), "伊万酱", {})
ck("> **动态内容**" not in md2, "带标题动态：不再有「动态内容」标签行")
ck("内容文本" in md2, "带标题动态：正文仍然在")

md3, _ = notify.render_dynamic(Dyn(title="", text="第一行\n第二行"), "伊万酱", {})
ck("> **动态内容**" not in md3, "多行正文：不再有「动态内容」标签行")
ck("第一行" in md3 and "第二行" in md3, "多行正文：两行都保留")

# 置顶动态走的是另一套模板，同样不该有标签行
md4, _ = notify.render_dynamic(Dyn(title="", text="置顶正文", pinned=True), "伊万酱", {})
ck("> **动态内容**" not in md4, "置顶动态：不再有「动态内容」标签行")

# 只去掉标签行，不能顺手把块引用整体弄没
ck(any(l.startswith("> 　") for l in md.splitlines()),
   "正文仍然缩进在引用块里（不是直接裸排）")


# ── 2. 粉丝勋章不显示 ───────────────────────────────────────────
ck(B._is_dress_emote("[伊万勋章]"), "粉丝勋章「[伊万勋章]」被判为装扮类")
ck(B._is_dress_emote("[星瞳勋章]"), "粉丝勋章「[星瞳勋章]」被判为装扮类")
ck(B._is_dress_emote("[粉丝勋章_小星星]"), "带前缀的粉丝勋章也被判为装扮类")

# 不能误伤：普通表情、UP 主充电大表情都要留着
ck(not B._is_dress_emote("[doge]"), "普通表情 [doge] 保留")
ck(not B._is_dress_emote("[OnO_气死我了]"), "UP 主充电表情 [OnO_气死我了] 保留")
ck(not B._is_dress_emote("[热词系列_干杯]"), "热词表情保留")

# 节点层面：装扮类会被整块剔掉
nodes = [
    {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "开播啦"},
    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
     "text": "伊万勋章", "emoji": {"text": "伊万勋章"}},
]
txt = B._nodes_text(nodes)
ck(txt is not None and "勋章" not in txt, "富文本节点里的粉丝勋章被剔除")
ck(txt is not None and "开播啦" in txt, "其余正文不受影响")

# 剔除后正文为空 → 那条动态就不该再挂引用块
if txt is not None and txt.strip() == "开播啦":
    pass
md5, _ = notify.render_dynamic(Dyn(title="", text=""), "伊万酱", {})
ck("> " not in md5, "正文为空时不多余挂引用块")


# ── 3. 开播动态：勋章剔掉后只剩直播文案 ─────────────────────────
md6, _ = notify.render_dynamic(
    Dyn(title="", text="", major_type="live", ref_room_id=55), "伊万酱", {})
ck("直播中" in md6 or "开播" in md6, "开播动态：走直播文案")
ck("勋章" not in md6, "开播动态：不含勋章字样")


# ── 4. 反向验证的钩子：确认判据真的来自源码里的关键词表 ─────────
ck("勋章" in B._DRESS_RE.pattern, "「勋章」真的写进了装扮关键词表")

print("\n%d 通过 / %d 失败" % (PASS, FAIL))
if FAILED:
    print("失败项：" + "、".join(FAILED))
    sys.exit(1)
