# -*- coding: utf-8 -*-
"""新订阅的静默对齐不能每轮刷屏，也不能每轮白发请求。

用户反馈的现象（v2.3.4）：
    日志里同一行反复出现，几秒一次、从不停止：

        [UID 320070259] 新订阅/新增类型：已静默对齐 1 个群（不推送历史内容）

根因：`_init_baseline` 只要"打算对齐"就打这行 INFO —— 但有几类内容
压根取不到（没直播间 / 还没投过稿 / 置顶动态下没有本人评论），这时候
一个基线都没写进去。于是下一轮 `_missing_baselines` 照样判定缺失，
又打一行。

危害两条：
  ① 日志被同一句话刷满，真正的推送记录反而看不见
  ② 每轮白发一遍请求（7 个 UP × 每 8 秒一轮）—— 这些请求全是白跑的，
     反过来加重 B 站风控，是日志里满屏「接口超时/繁忙」的成因之一

修复：只有**真写进基线**了才算对齐成功、才打日志；取不到时记进暂缓表，
冷却期内不再重复请求也不再刷日志。

安全边界（本文件专门钉住）：暂缓**不能**造成补推历史 —— 检测本身有
"首次静默对齐"兜底，基线缺失时只建立基线、不推送。
"""
import asyncio
import os
import re
import sys
import tempfile
import time

BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_stubs"))
sys.path.insert(0, BASE)

import botpy  # noqa: E402
import bot as botmod  # noqa: E402
from bot import NotifyBot  # noqa: E402

OK = []


def ck(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    OK.append(bool(cond))
    return bool(cond)


def _src():
    with open(os.path.join(BASE, "bot.py"), encoding="utf-8") as f:
        return f.read()


def _strip_comments(src):
    """去掉整行注释 —— 否则修复处写的说明里含同样的字样，退化后照样为绿。"""
    return "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))


# ──────────────────────────────────────────────────────────────
# 假外部依赖
# ──────────────────────────────────────────────────────────────

class Rec:
    """替掉 bot._log，把 INFO 收集起来数条数。"""

    def __init__(self):
        self.msgs = []       # 当前轮（_loops 每轮清空）
        self.all_msgs = []   # 全程累计（用来数"某条日志一共出现几次"）

    def info(self, m, *a, **k):
        self.msgs.append(str(m))
        self.all_msgs.append(str(m))

    def warning(self, m, *a, **k):
        self.msgs.append(str(m))
        self.all_msgs.append(str(m))

    def debug(self, *a, **k):
        pass

    def error(self, m, *a, **k):
        self.msgs.append("ERR:" + str(m))

    def critical(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


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
    def __init__(self, dyn_id, rid="", pubdate=0,
                 comment_id_str="", comment_type=0):
        self.dyn_id = dyn_id
        self.rid = rid
        self.pubdate = pubdate
        self.comment_id_str = comment_id_str
        self.comment_type = comment_type


class FakeBili:
    """默认什么都取不到 —— 正是触发刷屏的那种 UP 主。"""

    def __init__(self, pin=None, cmt=None, video=None, dyn=None):
        self.pin = pin          # TopDyn or None
        self.cmt = cmt          # dict or None
        self.video = video      # VideoInfo or None
        self.dyn = dyn          # 有 dyn_id/pubdate 的对象 or None
        self.n = {"dyn": 0, "cmt": 0, "video": 0, "live": 0, "room": 0}

    async def get_up(self, uid):
        from bilibili import Up
        return Up(uid=uid, uname=f"UP{uid}", room_id=0)

    async def get_live(self, room_id):
        self.n["live"] += 1
        from bilibili import LiveInfo
        return LiveInfo(room_id=room_id, live_status=0)

    async def get_latest_video(self, uid):
        self.n["video"] += 1
        return self.video

    async def get_latest_dynamic(self, uid):
        return self.dyn

    async def get_room_id_by_mid(self, uid):
        self.n["room"] += 1
        return (0, 0)

    async def get_pinned_dynamic(self, uid):
        self.n["dyn"] += 1
        return self.pin

    async def get_pinned_comment(self, dyn_id, uid=0, rid="", **kw):
        self.n["cmt"] += 1
        return self.cmt

    async def close(self):
        pass


def make_bot(**kw):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path},
                    intents=botpy.Intents(public_messages=True))
    bot.qq = FakeQQ()
    bot.bili = FakeBili(**kw)
    bot._silent = False
    rec = Rec()
    botmod._log = rec
    return bot, rec, path


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _mk_video(bvid="BV1", pubdate=1000):
    from bilibili import VideoInfo
    return VideoInfo(bvid=bvid, title="t", pubdate=pubdate, pic="", desc="")


def _mk_dyn(dyn_id="D1", pubdate=1000):
    return TopDyn(dyn_id, pubdate=pubdate)


def _loops(bot, rec, uid, rounds=4, only_missing=True):
    """跑 N 轮对齐，返回 ('已静默对齐'的条数, 总请求数)。

    ⚠️ 轮询里的真实调用是 only_missing=True（只补缺失的）。
    写成 False 就变成"强制刷新"，每轮都会重新请求 —— 测不出刷屏。
    """
    hit = 0
    total = 0
    for _ in range(rounds):
        rec.msgs = []
        run(bot._init_baseline(uid, only_missing=only_missing))
        hit += len([m for m in rec.msgs if "已静默对齐" in m])
    total = sum(bot.bili.n.values())
    return hit, total


# ──────────────────────────────────────────────────────────────
# 1. 源码层面
# ──────────────────────────────────────────────────────────────

def test_source_has_pending():
    src = _strip_comments(_src())
    i = src.find("async def _init_baseline(")
    assert i > 0, "找不到 _init_baseline"
    # ⚠️ 不能用固定字符窗口：函数体一变长，末尾的暂缓写入就被切在窗外，
    #    断言会假红（本文件第一版就是这么踩的）。按下一个同级方法切。
    j = src.find("\n    async def ", i + 10)
    if j < 0:
        j = src.find("\n    def ", i + 10)
    body = src[i:j if j > 0 else len(src)]

    ck("有暂缓表（_bl_pending）", "_bl_pending" in body)
    ck("有暂缓时长常量（BL_PENDING_TTL）", "BL_PENDING_TTL" in body)
    ck("冷却中的项会被过滤掉", "alive" in body and "if not alive" in body)
    ck("只按真写入的群报数（done）",
       "if done:" in body and "len({g for g, _ in done})" in body)
    ck("取不到时会记进暂缓表", "pend[(uid, bk)] = (now + ttl, why)" in body)
    ck("没直播间会被识别为对齐不上", "该 UP 主没有直播间" in body)
    ck("无投稿会被识别为对齐不上", "该 UP 主还没有投稿" in body)
    ck("无置顶动态会被识别为对齐不上", "该 UP 主没有置顶动态" in body)


# ──────────────────────────────────────────────────────────────
# 2. 端到端：取不到时不刷屏、不白发请求
# ──────────────────────────────────────────────────────────────

def _case(name, kinds, **kw):
    bot, rec, path = make_bot(**kw)
    uid, gid = 320070259, "G1"
    bot.store.set_up_info(uid, "波萝Buono", 0, "", 0)
    for k in kinds:
        bot.store.set_sub(gid, uid, k, True)
    hit, total = _loops(bot, rec, uid)
    return bot, rec, uid, gid, hit, total


def t_normal_still_aligns_once():
    """正常情况不受影响：对齐一次就成功，只打一行。"""
    bot, rec, uid, gid, hit, total = _case(
        "正常", ["top_comment"],
        pin=_mk_dyn(), cmt={"id": "C1", "content": "x", "mid": "320070259"})
    ck("正常：4 轮里只打 1 次「已静默对齐」", hit == 1)
    ck("正常：请求没有重复发（2 次以内）", total <= 2)
    ck("正常：基线确实写进去了",
       bool(bot.store.baseline_of(gid, uid, "dyn_top_cmt")))


def t_no_comment_no_spam():
    """没有置顶评论：不刷屏，且只请求一次（以前每轮 2 次）。"""
    bot, rec, uid, gid, hit, total = _case(
        "无评论", ["top_comment"], pin=_mk_dyn(), cmt=None)
    ck("无置顶评论：4 轮里没有「已静默对齐」的假日志", hit == 0)
    # 一轮的正常开销是 2 次（取置顶动态 + 取它的评论）；以前是 4 轮 × 2 = 8 次
    ck("无置顶评论：4 轮只发了 2 次请求（不是 8 次）", total == 2)
    ck("无置顶评论：暂缓原因写清楚了",
       any("置顶评论" in m and "暂缓" in m for m in rec.all_msgs))


def t_no_pinned_dyn_no_spam():
    """没有置顶动态：只请求一次。"""
    bot, rec, uid, gid, hit, total = _case(
        "无置顶动态", ["top_comment"], pin=None)
    ck("无置顶动态：没有假日志", hit == 0)
    ck("无置顶动态：4 轮只发了 1 次请求", total == 1)


def t_no_video_no_spam():
    """没投过稿：只请求一次。"""
    bot, rec, uid, gid, hit, total = _case("无投稿", ["video"], video=None)
    ck("无投稿：没有假日志", hit == 0)
    ck("无投稿：4 轮只发了 1 次请求", total == 1)


def t_no_room_no_request():
    """没直播间：只反查一次房间号，不再每轮白发请求。

    ⚠️ 旧断言是「一个请求都不发」。后来加了房间号反查
       （直播间短号在未开播状态下也取得到，见 _resolve_room_id），
       正确行为变成：4 轮只发 1 次反查，且绝不调 get_live。
    """
    bot, rec, uid, gid, hit, total = _case("无直播间", ["live"])
    ck("无直播间：没有假日志", hit == 0)
    ck("无直播间：4 轮只反查 1 次房间号", total == 1)
    ck("无直播间：没有白发 get_live", bot.bili.n["live"] == 0)


# ──────────────────────────────────────────────────────────────
# 3. 暂缓必须能恢复，且不能造成补推
# ──────────────────────────────────────────────────────────────

def t_cooldown_expires_then_retry():
    """冷却到期后要能重新尝试 —— UP 主后来真发了内容才不会永远漏。"""
    bot, rec, uid, gid, hit, total = _case(
        "冷却", ["video"], video=None)
    first = total
    ck("冷却：第一次只发 1 次请求", first == 1)
    # 手动把冷却到期时间拨到过去
    for k in list(bot._bl_pending):
        bot._bl_pending[k] = (time.time() - 1, "测试")
    bot.bili.n["video"] = 0
    rec.msgs = []
    run(bot._init_baseline(uid))
    ck("冷却：到期后会重新尝试", bot.bili.n["video"] == 1)
    # 这回给一条真实的投稿
    bot.bili.video = _mk_video()
    for k in list(bot._bl_pending):
        bot._bl_pending[k] = (time.time() - 1, "测试")
    bot.bili.n["video"] = 0
    rec.msgs = []
    run(bot._init_baseline(uid))
    ck("冷却：UP 主真有投稿后能正常建立基线",
       bool(bot.store.baseline_of(gid, uid, "video")))
    ck("冷却：建立成功时会正常打一行",
       any("已静默对齐" in m for m in rec.msgs))


def t_force_refresh_bypasses_cooldown():
    """开机宽限轮的强制刷新不受冷却影响。"""
    bot, rec, uid, gid, hit, total = _case(
        "强制", ["video"], video=None)
    ck("强制：普通调用被冷却挡住", total == 1)
    bot.bili.n["video"] = 0
    rec.msgs = []
    run(bot._init_baseline(uid, only_missing=False))
    ck("强制：only_missing=False 会忽略冷却",
       bot.bili.n["video"] == 1)


def t_check_still_builds_baseline_without_push():
    """暂缓期间检测仍会静默建基线，且**不推送** —— 不会补推历史。"""
    bot, rec, uid, gid = None, None, 320070259, "G1"
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path},
                    intents=botpy.Intents(public_messages=True))
    bot.qq = FakeQQ()
    bot.bili = FakeBili()
    bot._silent = False
    rec = Rec()
    botmod._log = rec
    bot.store.set_up_info(uid, "波萝Buono", 0, "", 0)
    bot.store.set_sub(gid, uid, "video", True)
    # 先让它进入暂缓
    run(bot._init_baseline(uid))
    ck("暂缓：基线此时仍是空的",
       not bot.store.baseline_of(gid, uid, "video"))
    # 冷却期间检测照常跑（UP 主有了第一条投稿）
    bot.bili.video = _mk_video(bvid="BV_NEW", pubdate=2000)
    bot._silent = False
    run(bot._check_video(uid, "波萝Buono"))
    ck("暂缓期间检测会自己建基线",
       bool(bot.store.baseline_of(gid, uid, "video")))
    ck("暂缓期间首次检测不推送（不会补推历史）",
       len(bot.qq.sent) == 0)


def t_pending_reason_readable():
    """暂缓日志要能看懂为什么，而不是一句"已对齐"。"""
    bot, rec, uid, gid, hit, total = _case(
        "文案", ["video"], video=None)
    msgs = [m for m in rec.all_msgs if "暂缓" in m]
    ck("暂缓日志只打一次（不是每轮一条）", len(msgs) == 1)
    ck("暂缓日志说明了原因和重试时间",
       bool(msgs) and "还没有投稿" in msgs[0] and "s 后再试" in msgs[0])
    ck("暂缓日志说明不影响正常检测",
       bool(msgs) and "不影响正常检测" in msgs[0])


if __name__ == "__main__":
    test_source_has_pending()
    t_normal_still_aligns_once()
    t_no_comment_no_spam()
    t_no_pinned_dyn_no_spam()
    t_no_video_no_spam()
    t_no_room_no_request()
    t_cooldown_expires_then_retry()
    t_force_refresh_bypasses_cooldown()
    t_check_still_builds_baseline_without_push()
    t_pending_reason_readable()
    print(f"\n{sum(OK)}/{len(OK)}")
    sys.exit(0 if all(OK) else 1)
