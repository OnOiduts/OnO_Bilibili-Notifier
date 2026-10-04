# -*- coding: utf-8 -*-
"""锁定「投稿提醒之后又来一条投稿动态」的修复。

症状：更新视频时，视频提醒和动态提醒**同时**都推了。
用户要的是：优先保留视频推送，动态不推送。

根因（v1.51.0 之前的写法）：
  投稿那一刻 B 站就自动发了动态，而我们**下一轮轮询**才发现投稿，
  中间隔着整个轮询间隔。去重时拿"我们推送视频的时刻 ts"去和
  "动态发布时间 pub" 比，要求 pub >= ts - 60 —— 动态永远显得更"早"，
  于是被判成旧动态放行，结果连着推两条。

正确做法：比**两件事各自的发生时间**（投稿 pubdate vs 动态 pubdate），
它们几乎是同一秒发生的；不是拿"我们发现它的时刻"去比。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, "/data/workspace/_stub2")

import bot  # noqa: E402

PASS = FAIL = 0
FAILED = []


def ck(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  ", name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  FAIL", name)


class FakeDyn:
    def __init__(self, pub=0, room=0, bvid="", mtype="normal"):
        self.pubdate = pub
        self.ref_room_id = room
        self.ref_bvid = bvid
        self.major_type = mtype


class FakeVideo:
    def __init__(self, bvid, pubdate):
        self.bvid = bvid
        self.pubdate = pubdate


class FakeLive:
    def __init__(self, room_id, live_time):
        self.room_id = room_id
        self.live_time = live_time


def _bot(cfg=None):
    b = bot.NotifyBot.__new__(bot.NotifyBot)
    b.cfg = cfg if cfg is not None else {}
    b._recent_push = {}
    return b


def test_remember_pub():
    """_remember_pushed 必须记下内容自身的发布时间。"""
    print("[1] 记住的是内容发布时间，不是推送时刻")
    now = int(time.time())
    T = now - 180

    b = _bot()
    b._remember_pushed(1, "video", FakeVideo("BV1xx", T))
    hit = b._recent_push[1]["video"]
    ck(hit.get("val") == "BV1xx", "BV 号记下来了")
    ck(int(hit.get("pub", 0)) == T, "投稿的 pubdate 记下来了")
    ck(hit.get("ts", 0) > now - 5, "推送时刻也记着（用于时间窗）")

    b2 = _bot()
    b2._remember_pushed(1, "live", FakeLive(7788, T))
    ck(int(b2._recent_push[1]["live"].get("pub", 0)) == T,
       "开播时刻记下来了（live_time）")

    # 内容时间取不到 → 退回推送时刻兜底，不因缺字段就放弃去重
    b3 = _bot()
    b3._remember_pushed(1, "video", FakeVideo("BV1yy", 0))
    ck(int(b3._recent_push[1]["video"].get("pub", 0)) > now - 5,
       "pubdate 缺失时退回推送时刻")


def test_video_dyn_silenced():
    """核心回归：动态发布时间早于我们发现投稿的时刻，也必须静默。"""
    print("[2] 投稿动态（时间早于推送时刻）必须静默")
    now = int(time.time())
    T = now - 180          # 3 分钟前投稿，B站同时发了动态

    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "video", FakeVideo("BV1xx", T))
    d = FakeDyn(pub=T, mtype="normal")
    ck(b._silence_after_push(1, d), "早于推送时刻 3 分钟 → 仍要静默")
    ck(b._should_silence_dynamic(1, d), "集成：_should_silence_dynamic 静默")

    # 轮询更慢的场景：10 分钟才发现投稿
    b2 = _bot({"dedupe_window": 900})
    T2 = now - 600
    b2._remember_pushed(1, "video", FakeVideo("BV1xx", T2))
    ck(b2._should_silence_dynamic(1, FakeDyn(pub=T2, mtype="normal")),
       "轮询慢（10 分钟）也一样静默")


def test_live_dyn_silenced():
    """开播同理：开播动态的时间早于我们发现开播的时刻。"""
    print("[3] 开播动态（时间早于推送时刻）必须静默")
    now = int(time.time())
    T = now - 240

    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "live", FakeLive(7788, T))
    ck(b._should_silence_dynamic(1, FakeDyn(pub=T, mtype="normal")),
       "开播动态早于开播提醒 4 分钟 → 静默")


def test_not_same_event():
    """反例：时间对不上就不是同一件事，不能吞掉真动态。"""
    print("[4] 时间对不上 → 不静默")
    now = int(time.time())
    T = now - 180

    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "video", FakeVideo("BV1xx", T))

    # UP 两小时后手发一条普通动态
    ck(not b._should_silence_dynamic(1, FakeDyn(pub=T + 7200, mtype="normal")),
       "两小时后手发的动态 → 不静默")
    # 投稿之前的旧动态
    ck(not b._should_silence_dynamic(1, FakeDyn(pub=T - 7200, mtype="normal")),
       "投稿之前的旧动态 → 不静默")
    # 从没推过
    ck(not _bot({"dedupe_window": 900})._should_silence_dynamic(
        1, FakeDyn(pub=T, mtype="normal")), "没推过 → 不静默")
    # 别的 UP 不受影响
    ck(not b._should_silence_dynamic(2, FakeDyn(pub=T, mtype="normal")),
       "别的 UP → 不静默")


def test_exact_bvid_still_works():
    """精确比对（BV 号对得上）不受本次改动影响。"""
    print("[5] 精确去重仍然有效")
    now = int(time.time())
    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "video", FakeVideo("BV1xx", now - 180))
    ck(b._should_silence_dynamic(1, FakeDyn(pub=now - 180, bvid="BV1xx",
                                           mtype="video")),
       "BV 号对得上 → 静默")
    # ⚠️ BV 对不上但时间挨在一起 → 时间兜底仍会静默。
    #    这是 silence_dyn_after_push 的已知副作用（UP 同期手发的动态
    #    会被一并吞掉），不是 bug —— 想退回"只按内容精确去重"就把
    #    silence_dyn_after_push 设成 false。所以这里要隔开时间再测。
    ck(not b._should_silence_dynamic(1, FakeDyn(pub=now - 7200, bvid="BV1zz",
                                               mtype="video")),
       "BV 对不上且时间隔开 → 不静默")


def test_window_and_switch():
    """时间窗与开关仍然生效。"""
    print("[6] 时间窗 / 开关")
    now = int(time.time())

    # 超出 dedupe_window（不再吞动态）
    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "video", FakeVideo("BV1xx", now - 5000))
    b._recent_push[1]["video"]["ts"] = float(now - 5000)
    ck(not b._should_silence_dynamic(1, FakeDyn(pub=now - 5000,
                                               mtype="normal")),
       "超出时间窗 → 不静默")

    # 开关关掉 → 不静默（退回只按内容精确去重）
    b2 = _bot({"dedupe_window": 900, "silence_dyn_after_push": False})
    b2._remember_pushed(1, "video", FakeVideo("BV1xx", now - 180))
    ck(not b2._should_silence_dynamic(1, FakeDyn(pub=now - 180,
                                                mtype="normal")),
       "silence_dyn_after_push=false → 不静默")

    # 取不到动态发布时间 → 不静默（宁可重复推，也不吞真动态）
    b3 = _bot({"dedupe_window": 900})
    b3._remember_pushed(1, "video", FakeVideo("BV1xx", now - 180))
    ck(not b3._should_silence_dynamic(1, FakeDyn(pub=0, mtype="normal")),
       "动态没给发布时间 → 不静默")


def test_reserve_never_silenced():
    """直播预约始终不静默 —— 它通知的是"未来要播"。"""
    print("[7] 直播预约不静默")
    now = int(time.time())
    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "live", FakeLive(7788, now - 180))
    ck(not b._should_silence_dynamic(1, FakeDyn(pub=now - 180, room=7788,
                                               mtype="reserve")),
       "预约动态 → 不静默")


def test_bv_extraction():
    """新版 opus 动态不带 archive 卡片，也要能从 jump_url / 正文抠出 BV。"""
    print("[8] 从 opus 动态里抠出 BV 号")
    try:
        from bilibili import _BV_RE
    except ImportError:
        ck(False, "_BV_RE 未导出")
        return
    ck(bool(_BV_RE.search("https://www.bilibili.com/video/BV1xx411c7mD")),
       "能从跳转链接里抠出 BV")
    ck(bool(_BV_RE.search("新视频来啦 BV1Gt4y1X7ab 速看")),
       "能从正文里抠出 BV")
    ck(not _BV_RE.search("今天天气不错，没有链接"),
       "没有 BV 时不误报")


if __name__ == "__main__":
    for fn in (test_remember_pub, test_video_dyn_silenced,
               test_live_dyn_silenced, test_not_same_event,
               test_exact_bvid_still_works, test_window_and_switch,
               test_reserve_never_silenced, test_bv_extraction):
        fn()
    print(f"--- {PASS}/{PASS + FAIL} passed")
    if FAILED:
        print("FAILED:", FAILED)
        sys.exit(1)
