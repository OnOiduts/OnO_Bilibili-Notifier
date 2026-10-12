# -*- coding: utf-8 -*-
"""开机静默必须**真的**静默：置顶评论也要在启动同步里对齐。

用户反馈的现象（v2.3.4）：
    开机后日志写着「启动同步完成：5 位 UP 主的当前状态已记录为基线
    （静默，不推送）」，紧接着第一轮轮询却推出两条：

        _push  已推送【置顶评论】星瞳_Official → 群 6FBBD9B5…
        _push  已推送【置顶评论】星瞳_Official → 群 1BA9FBD0…

根因：启动同步的 _sync_one 只调了 _check_live / _check_video /
_check_dynamic 三类，**漏了 _check_top_comment**。置顶评论在
_check_dynamic 里是被明确排除的（见那里 2882 行附近的注释"不在这里调"），
所以没有任何一条路径会在启动同步期间对齐它。

后果：停机期间 UP 主换了置顶评论，启动同步只把 live/video/dynamic 对齐了，
置顶评论基线还是停机前的旧 ID。第一轮轮询 prev(旧) != cur(新)
→ 判定成"新内容" → 补推一遍。这正是"静默没完全静默"的那一半。

本文件钉住两点：
  1. 源码里启动同步确实会调 _check_top_comment（且只在订阅了它时才调，
     不给没开这个开关的人白跑一次请求）
  2. 端到端：停机期间换过置顶评论 → 启动同步后基线已跟上最新，
     随后正常检测不再推送
"""
import asyncio
import os
import re
import sys
import tempfile

BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE)

import botpy  # noqa: E402
from bot import NotifyBot  # noqa: E402

OK = []


def ck(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    OK.append(bool(cond))
    return bool(cond)


def _src(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()


def _strip_comments(src):
    """去掉整行注释。

    ⚠️ 不剥的话，我在修复处写的那几行说明里就含 `_check_top_comment(`
       和 `if "top_comment" in kinds` 的字样 —— 退化成旧代码后断言照样
       为绿，反向验证就成了摆设。这个项目里已经踩过不止一次。
    """
    return "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))


# ──────────────────────────────────────────────────────────────
# 假外部依赖
# ──────────────────────────────────────────────────────────────

class FakeQQ:
    def __init__(self):
        self.sent = []

    async def send_text(self, gid, content, **kw):
        self.sent.append((gid, content))

    async def send_markdown(self, gid, md, keyboard=None, **kw):
        self.sent.append((gid, md))

    async def close(self):
        pass


class TopDyn:
    """置顶动态。字段跟 bilibili.DynamicInfo 对齐（都用 getattr 取，宽松）。"""

    def __init__(self, dyn_id, rid="", pubdate=0,
                 comment_id_str="", comment_type=0):
        self.dyn_id = dyn_id
        self.rid = rid
        self.pubdate = pubdate
        self.comment_id_str = comment_id_str
        self.comment_type = comment_type


class FakeBili:
    def __init__(self):
        self.pinned = {}        # uid -> TopDyn
        self.pinned_cmt = {}    # uid -> dict(id/content/mid)
        self.calls = []         # 记录调过什么，用来断言"没白跑"

    async def get_up(self, uid):
        from bilibili import Up
        return Up(uid=uid, uname=f"UP{uid}", room_id=0)

    async def get_live(self, room_id):
        from bilibili import LiveInfo
        return LiveInfo(room_id=room_id, live_status=0)

    async def get_latest_video(self, uid):
        return None

    async def get_latest_dynamic(self, uid):
        return None

    async def get_room_id_by_mid(self, uid):
        # _fix_missing_rooms / _check_live 都会调它。桩里返回空即可 ——
        # 本文件只关心置顶评论，room_id=0 时那两条路径会自己跳过。
        return (0, 0)

    async def get_pinned_dynamic(self, uid):
        self.calls.append("pinned_dyn")
        return self.pinned.get(uid)

    async def get_pinned_comment(self, dyn_id, uid=0, rid="", **kw):
        self.calls.append("pinned_cmt")
        return self.pinned_cmt.get(uid)

    async def close(self):
        pass


def make_bot():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path},
                    intents=botpy.Intents(public_messages=True))
    bot.qq = FakeQQ()
    bot.bili = FakeBili()
    bot._silent = False
    return bot, path


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ──────────────────────────────────────────────────────────────
# 1. 源码层面：启动同步会调 _check_top_comment
# ──────────────────────────────────────────────────────────────

def test_startup_sync_covers_top_comment():
    """启动同步必须覆盖置顶评论，且只给订阅了它的 UP 主调。"""
    src = _strip_comments(_src("bot.py"))

    # 定位 _do_startup_sync 里的 _sync_one（只截到它结束，不要一路读到
    # 后面的 _startup_sync_seasons，否则会串到别的函数上）
    i = src.find("async def _sync_one(uid)")
    assert i > 0, "找不到 _sync_one"
    body = src[i:i + 2200]
    j = body.find("return 1")
    ck("_sync_one 里识别出唯一出口", j > 0)
    body = body[:j]

    ck("启动同步会调 _check_top_comment",
       "_check_top_comment" in body)
    ck("只在订阅了置顶评论时才调（不给没开的白跑请求）",
       'if "top_comment" in kinds' in body)

    # 顺序：必须在置顶评论之前拿到 kinds
    k_kinds = body.find("enabled_kinds_of_up")
    k_top = body.find("_check_top_comment")
    ck("先取 kinds 再判断，顺序正确", 0 <= k_kinds < k_top)


def test_poll_once_also_independent():
    """轮询里置顶评论是独立调度的（不能挂回 _check_dynamic 里）。"""
    src = _strip_comments(_src("bot.py"))
    i = src.find("async def _poll_once(")
    assert i > 0, "找不到 _poll_once"
    body = src[i:i + 6000]
    j = body.find("async def _check_seasons(")
    body = body[:j] if j > 0 else body[:6000]

    ck("轮询里独立调度置顶评论",
       '_safe(self._check_top_comment(uid, name), "top_cmt")' in body)


# ──────────────────────────────────────────────────────────────
# 2. 端到端：停机期间换过置顶评论，开机不该补推
# ──────────────────────────────────────────────────────────────

def t_startup_does_not_backfill_top_comment():
    """复现用户场景：停机期间 UP 主换了置顶评论。

    启动同步后基线必须已经是新的；随后正常检测不能再推一次。
    """
    bot, path = make_bot()
    uid, gid = 3001, "GROUP_TC"
    st = bot.store
    st.set_up_info(uid, "UP3001", 0, "", 0)
    st.set_sub(gid, uid, "top_comment", True)
    # 停机前的旧基线
    st.set_baseline_of(gid, uid, "dyn_top_cmt", id="CMT_OLD")

    # 停机期间换成了新的置顶评论（评论者是 UP 主本人，才会被认）
    bot.bili.pinned[uid] = TopDyn(dyn_id="DYN1", rid="RID1", pubdate=1000)
    bot.bili.pinned_cmt[uid] = {
        "id": "CMT_NEW", "content": "停机期间换的新置顶评论",
        "mid": str(uid),
    }

    try:
        run(bot._startup_sync())

        ck("启动同步期间一条都没推", not bot.qq.sent)
        base = st.baseline_of(gid, uid, "dyn_top_cmt") or {}
        ck("启动同步已把置顶评论基线跟到最新",
           base.get("id") == "CMT_NEW")

        # 随后正常检测：基线已对齐，不该再推
        bot.qq.sent.clear()
        run(bot._check_top_comment(uid, "UP3001"))
        ck("对齐后正常检测不再补推", not bot.qq.sent)
    finally:
        os.unlink(path)


def t_no_sub_no_request():
    """没开置顶评论开关的 UP 主，启动同步不该为它发评论请求。"""
    bot, path = make_bot()
    uid, gid = 3002, "GROUP_NO_TC"
    st = bot.store
    st.set_up_info(uid, "UP3002", 0, "", 0)
    st.set_sub(gid, uid, "dynamic", True)      # 只开动态，不开置顶评论

    try:
        run(bot._startup_sync())
        ck("没开置顶评论时不请求评论接口",
           "pinned_cmt" not in bot.bili.calls)
        ck("没开置顶评论时一条都没推", not bot.qq.sent)
    finally:
        os.unlink(path)


def t_real_change_still_pushes():
    """回归护栏：修完不能把真·新置顶评论也吞掉。"""
    bot, path = make_bot()
    uid, gid = 3003, "GROUP_REAL"
    st = bot.store
    st.set_up_info(uid, "UP3003", 0, "", 0)
    st.set_sub(gid, uid, "top_comment", True)

    bot.bili.pinned[uid] = TopDyn(dyn_id="DYN1", rid="RID1", pubdate=1000)
    bot.bili.pinned_cmt[uid] = {
        "id": "CMT_1", "content": "第一条", "mid": str(uid)}

    try:
        run(bot._startup_sync())                # 对齐到 CMT_1
        ck("首次对齐不推送", not bot.qq.sent)

        # 真的换了置顶评论 → 必须推
        bot.bili.pinned_cmt[uid] = {
            "id": "CMT_2", "content": "第二条", "mid": str(uid)}
        bot.qq.sent.clear()
        run(bot._check_top_comment(uid, "UP3003"))
        ck("真的换了置顶评论仍然会推送", len(bot.qq.sent) > 0)
    finally:
        os.unlink(path)


def t_init_baseline_rejects_fan_comment():
    """_init_baseline 补基线时必须和检测用同一套「本人」判定。

    ⚠️ 这是补启动同步那一轮引出来的回归，实测踩到过：
       给 _init_baseline 补上置顶评论时只写了 `if c and c.get("id")`，
       没判评论者是谁。而 _check_top_comment 是**认人**的（粉丝刷上去的
       不算）。于是两边口径不一致 —— 粉丝评论被记成基线，占住了位置，
       之后 UP 主自己的评论反而成了"新内容"，而粉丝那条永远推不出去。
    """
    bot, path = make_bot()
    uid, gid = 3004, "GROUP_FAN"
    st = bot.store
    st.set_up_info(uid, "UP3004", 0, "", 0)
    st.set_sub(gid, uid, "top_comment", True)

    bot.bili.pinned[uid] = TopDyn(dyn_id="DYN1", rid="RID1", pubdate=1000)
    # 评论者是路人（mid=999），不是 UP 主本人
    bot.bili.pinned_cmt[uid] = {
        "id": "CMT_FAN", "content": "粉丝刷上去的", "mid": "999"}

    try:
        run(bot._init_baseline(uid))
        base = st.baseline_of(gid, uid, "dyn_top_cmt") or {}
        ck("粉丝的置顶评论不写基线", not base.get("id"))

        # 换成 UP 主本人的 → 必须正常建基线
        bot.bili.pinned_cmt[uid] = {
            "id": "CMT_SELF", "content": "UP 自己置顶的", "mid": str(uid)}
        run(bot._init_baseline(uid))
        base = st.baseline_of(gid, uid, "dyn_top_cmt") or {}
        ck("本人置顶评论正常建基线", base.get("id") == "CMT_SELF")
    finally:
        os.unlink(path)


def t_init_baseline_source_has_owner_check():
    """源码层面：_init_baseline 的置顶评论分支里必须有「本人」判定。

    ⚠️ 只截到该分支结束，别一路读到后面的 _check_top_comment /
    _check_live —— 那些函数本来就带 mid 校验，窗口开大了，
    把本分支的校验删掉**照样是绿的**（上一轮就是这样漏掉的）。
    """
    src = _strip_comments(_src("bot.py"))
    i = src.find("async def _init_baseline(")
    assert i > 0, "找不到 _init_baseline"
    body = src[i:i + 5000]
    j = body.find("if g_live or g_video or g_dyn or g_top_cmt:")
    ck("_init_baseline 里识别出收尾日志（分支截止点）", j > 0)
    body = body[:j]

    ck("置顶评论分支存在", "g_top_cmt" in body)
    ck("置顶评论分支里做了本人判定",
       "top_comment_only_up" in body and 'get("mid")' in body)


if __name__ == "__main__":
    test_startup_sync_covers_top_comment()
    test_poll_once_also_independent()
    t_startup_does_not_backfill_top_comment()
    t_no_sub_no_request()
    t_real_change_still_pushes()
    t_init_baseline_rejects_fan_comment()
    t_init_baseline_source_has_owner_check()
    print(f"\n{sum(OK)}/{len(OK)}")
    sys.exit(0 if all(OK) else 1)
