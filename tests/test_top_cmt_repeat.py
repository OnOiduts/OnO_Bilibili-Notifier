# -*- coding: utf-8 -*-
"""置顶评论不能连着重复推（用户日志：同一位 UP 主连推 4 次）。

现象（v2.3.4，日志里满屏 404 / 超时）：
    _push 已推送【置顶评论】星瞳_Official → 群 6FBBD9B5…
    _push 已推送【置顶评论】星瞳_Official → 群 1BA9FBD0…
    _push 已推送【置顶评论】星瞳_Official → 群 6FBBD9B5…
    _push 已推送【置顶评论】星瞳_Official → 群 1BA9FBD0…

根因：一条置顶动态可能同时存在多个能取到评论的评论区组合 ——
带图动态既有 (11, 相簿id) 也有 (17, 动态id)，两者返回的置顶评论
**不是同一条**。get_pinned_comment 原本每轮从候选表头开始挨个试，
接口一抖（404 / 超时）排在前面的就取不到、落到后面那个，于是
A → B → A 来回漂。每漂一次都被判成"新评论"再推一遍。

两道防线：
  1. bilibili.get_pinned_comment 记住上次成功命中的 (type, oid) 并
     优先复用 —— 从源头消除漂移
  2. bot._check_top_comment 记住最近推过的几条评论 id —— 万一还是漂了
     （比如 UP 主换了置顶动态、评论区整体失效），也不重复推

本文件两组反向验证（退化后必须变红），不是只看代码。
"""
import asyncio
import os
import sys

BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE)

import botpy  # noqa: E402
from bilibili import BiliClient, BiliError  # noqa: E402
from bot import NotifyBot  # noqa: E402

OK = []


def ck(name, cond, extra=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + (
        f"  ← {extra}" if (extra and not cond) else ""))
    OK.append(bool(cond))
    return bool(cond)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ──────────────────────────────────────────────────────────────
# 1. bilibili 层：评论区 (type, oid) 必须被锁定
# ──────────────────────────────────────────────────────────────

def _reply(rpid, msg, mid=1):
    return {"rpid_str": rpid,
            "member": {"uname": "u", "mid": mid},
            "content": {"message": msg}}


def _fake_get(registry, fail=()):
    """registry: (type, oid) -> 返回的评论数据；fail: 这些组合抛错。"""
    calls = []

    async def _get(url, params, referer=""):
        key = (str(params.get("type")), str(params.get("oid")))
        calls.append(key)
        if key in fail:
            raise BiliError("boom 404")
        data = registry.get(key)
        if not data:
            return {"data": {}}
        return {"data": {"top_replies": [data]}}
    return _get, calls


def test_oid_locked_after_first_hit():
    """第一次命中之后，后续必须复用同一个 (type, oid)。"""
    reg = {("17", "DYN1"): _reply("R_A", "评论A"),
           ("11", "RID1"): _reply("R_B", "评论B")}
    get, calls = _fake_get(reg)
    cli = BiliClient(cookie="x")
    cli._get = get

    g1 = run(cli.get_pinned_comment("DYN1", uid=1, rid="RID1"))
    ck("第一次取到评论", bool(g1 and g1.get("id")), repr(g1))
    ck("第一次拿到的是 A（候选表里 dyn_id 在前）",
       (g1 or {}).get("id") == "R_A", repr(g1))

    # 第二轮：把第一个候选做成失败，逼它漂到第二个
    get2, calls2 = _fake_get(reg, fail={("17", "DYN1")})
    cli._get = get2
    g2 = run(cli.get_pinned_comment("DYN1", uid=1, rid="RID1"))
    ck("第一轮失败会退到另一个评论区（仍能取到）",
       bool(g2 and g2.get("id") == "R_B"), repr(g2))
    ck("退到第二个之后就锁住它", cli._cmt_oid_hint.get("DYN1") == ("11", "RID1"),
       repr(cli._cmt_oid_hint))

    # 第三轮：两个都能通，但锁定优先 → 必须还是第二个
    get3, calls3 = _fake_get(reg)
    cli._get = get3
    g3 = run(cli.get_pinned_comment("DYN1", uid=1, rid="RID1"))
    ck("锁定生效后不再漂回第一个（id 稳定）",
       (g3 or {}).get("id") == "R_B", repr(g3))
    ck("第一个请求就用锁定的组合", calls3 and calls3[0] == ("11", "RID1"),
       repr(calls3[:2]))


def test_oid_lock_is_per_dynamic():
    """换了置顶动态（dyn_id 变了）要重新探测，不能沿用旧的。"""
    reg = {("17", "DYN1"): _reply("R_A", "评论A"),
           ("17", "DYN2"): _reply("R_C", "评论C")}
    get, _ = _fake_get(reg)
    cli = BiliClient(cookie="x")
    cli._get = get

    run(cli.get_pinned_comment("DYN1", uid=1, rid=""))
    ck("DYN1 记下了自己的组合", cli._cmt_oid_hint.get("DYN1") == ("17", "DYN1"),
       repr(cli._cmt_oid_hint))
    g2 = run(cli.get_pinned_comment("DYN2", uid=1, rid=""))
    ck("换动态后重新探测，取到新评论",
       (g2 or {}).get("id") == "R_C", repr(g2))


# ──────────────────────────────────────────────────────────────
# 2. bot 层：漂回来的那条不再重复推
# ──────────────────────────────────────────────────────────────

class TopDyn:
    def __init__(self, dyn_id, rid="", pubdate=0,
                 comment_id_str="", comment_type=0):
        self.dyn_id = dyn_id
        self.rid = rid
        self.pubdate = pubdate
        self.comment_id_str = comment_id_str
        self.comment_type = comment_type


class FakeQQ:
    def __init__(self):
        self.sent = []

    async def send_text(self, gid, content, **kw):
        self.sent.append((gid, content))

    async def send_markdown(self, gid, md, keyboard=None, **kw):
        self.sent.append((gid, md))

    async def close(self):
        pass


class FakeBili:
    def __init__(self):
        self.pinned = {}
        self.pinned_cmt = {}

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
        return (0, 0)

    async def get_pinned_dynamic(self, uid):
        return self.pinned.get(uid)

    async def get_pinned_comment(self, dyn_id, uid=0, rid="", **kw):
        return self.pinned_cmt.get(uid)

    async def close(self):
        pass


def make_bot():
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path},
                    intents=botpy.Intents(public_messages=True))
    bot.qq = FakeQQ()
    bot.bili = FakeBili()
    bot._silent = False
    return bot, path


def _cmt(cid, mid):
    return {"id": cid, "content": f"内容{cid}", "mid": str(mid)}


def test_drift_back_not_repushed():
    """A → B → A 漂移：A 第二次出现不该再推。"""
    bot, path = make_bot()
    uid, gid = 4001, "GROUP_DRIFT"
    st = bot.store
    st.set_up_info(uid, "UP4001", 0, "", 0)
    st.set_sub(gid, uid, "top_comment", True)
    st.set_baseline_of(gid, uid, "dyn_top_cmt", id="CMT_OLD")
    bot.bili.pinned[uid] = TopDyn(dyn_id="D1", rid="R1", pubdate=1)

    try:
        seq = ["CMT_A", "CMT_B", "CMT_A", "CMT_B", "CMT_A"]
        pushes = []
        for cid in seq:
            bot.bili.pinned_cmt[uid] = _cmt(cid, uid)
            bot.qq.sent.clear()
            run(bot._check_top_comment(uid, "UP4001"))
            if bot.qq.sent:
                pushes.append(cid)

        ck("漂回来的那条不再重复推", pushes.count("CMT_A") == 1,
           f"实际推了 {pushes}")
        ck("每条评论最多推一次", len(pushes) == len(set(pushes)),
           f"实际推了 {pushes}")
        ck("至少推过一次（没有把正常推送也挡掉）", bool(pushes),
           f"实际推了 {pushes}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_new_comment_still_pushes():
    """真的换了新评论时必须照常推（去重不能误伤）。"""
    bot, path = make_bot()
    uid, gid = 4002, "GROUP_NEW"
    st = bot.store
    st.set_up_info(uid, "UP4002", 0, "", 0)
    st.set_sub(gid, uid, "top_comment", True)
    st.set_baseline_of(gid, uid, "dyn_top_cmt", id="CMT_OLD")
    bot.bili.pinned[uid] = TopDyn(dyn_id="D2", rid="R2", pubdate=1)

    try:
        bot.bili.pinned_cmt[uid] = _cmt("CMT_1", uid)
        run(bot._check_top_comment(uid, "UP4002"))
        ck("第一条新评论会推", len(bot.qq.sent) == 1, str(len(bot.qq.sent)))

        bot.qq.sent.clear()
        bot.bili.pinned_cmt[uid] = _cmt("CMT_2", uid)
        run(bot._check_top_comment(uid, "UP4002"))
        ck("换了新评论照样推", len(bot.qq.sent) == 1, str(len(bot.qq.sent)))

        bot.qq.sent.clear()
        run(bot._check_top_comment(uid, "UP4002"))
        ck("同一条不重复推", not bot.qq.sent, str(len(bot.qq.sent)))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_seen_list_bounded():
    """「最近推过」列表要有上限，不能无限增长。"""
    from bot import TOP_CMT_SEEN_KEEP
    bot, path = make_bot()
    uid, gid = 4003, "GROUP_KEEP"
    st = bot.store
    st.set_up_info(uid, "UP4003", 0, "", 0)
    st.set_sub(gid, uid, "top_comment", True)
    st.set_baseline_of(gid, uid, "dyn_top_cmt", id="CMT_OLD")
    bot.bili.pinned[uid] = TopDyn(dyn_id="D3", rid="R3", pubdate=1)
    try:
        for i in range(TOP_CMT_SEEN_KEEP + 6):
            bot.bili.pinned_cmt[uid] = _cmt(f"CMT_{i}", uid)
            run(bot._check_top_comment(uid, "UP4003"))
        lst = (getattr(bot, "_top_cmt_seen", {}) or {}).get(uid) or []
        ck("列表长度不超上限", len(lst) <= TOP_CMT_SEEN_KEEP,
           f"{len(lst)} > {TOP_CMT_SEEN_KEEP}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def main():
    test_oid_locked_after_first_hit()
    test_oid_lock_is_per_dynamic()
    test_drift_back_not_repushed()
    test_new_comment_still_pushes()
    test_seen_list_bounded()
    bad = OK.count(False)
    print(f"\n合计 {len(OK)} 项，失败 {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
