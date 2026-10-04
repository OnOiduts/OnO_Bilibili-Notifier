# -*- coding: utf-8 -*-
"""锁定「动态先被检测到 → 视频随后又推一遍」的修复（反向去重）。

症状（v1.61.0 之前）：
  更新视频时群里连着来两条 —— 一条「新动态」，一条「新视频投稿」，
  内容是同一个视频。

为什么旧的去重没拦住：
  投稿/开播时 B 站会自动发一条动态，指向同一个视频/直播间。旧逻辑只有
  **一个方向**：视频先推 → 记下 BV → 动态来时查得见 → 静默动态。
  但动态接口通常比投稿接口快（视频要等索引），实际常常是**动态先到**。
  动态推送时 `_remember_pushed(uid, "dynamic", d)` 走的是空分支
  （dynamic 既不是 live 也不是 video/season，val 为空直接 return），
  **什么都没记** —— 等视频随后被发现，没有任何记录可查，于是又推一遍。

正确做法：按**内容**建一份索引。不管由哪一类提醒先推出去，记的都是
那个视频 / 那个直播间，后到的那一类查得见就不再推。
谁先发现就推谁，另一方向静默。
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


class FakeStore:
    """只提供 targets()，用来区分「该群订了动态没有」。"""

    def __init__(self, mapping):
        self._m = mapping

    def targets(self, uid, kind):
        return list(self._m.get((uid, kind), []))


def _bot(cfg=None, mapping=None):
    b = bot.NotifyBot.__new__(bot.NotifyBot)
    b.cfg = cfg if cfg is not None else {}
    b._recent_push = {}
    b._pushed_content = {}
    b.store = FakeStore(mapping or {})
    return b


def test_dynamic_marks_content():
    """动态推送时必须记下它指向的视频 / 直播间（旧写法这里直接 return）。"""
    print("[1] 动态推送要记下指向的内容")
    now = int(time.time())

    b = _bot()
    b._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid="BV1xx"))
    ck(b._content_pushed_by(1, "video", "BV1xx", ("dynamic",)),
       "投稿动态 → 记下了 BV 号")

    b2 = _bot()
    b2._remember_pushed(1, "dynamic", FakeDyn(pub=now, room=7788))
    ck(b2._content_pushed_by(1, "room", "7788", ("dynamic",)),
       "开播动态 → 记下了房间号")

    b3 = _bot()
    b3._remember_pushed(1, "dynamic", FakeDyn(pub=now))
    ck(not b3._content_pushed_by(1, "video", "BV1xx", ("dynamic",)),
       "没指向任何内容 → 不记（不能瞎静默）")


def test_by_kind_isolation():
    """按"谁推的"区分：视频自己推过不算动态推过。"""
    print("[2] 记录要能区分是谁推的")
    now = int(time.time())

    b = _bot()
    b._remember_pushed(1, "video", FakeVideo("BV1xx", now))
    ck(b._content_pushed_by(1, "video", "BV1xx", ("video", "season")),
       "视频推过 → by=video")
    ck(not b._content_pushed_by(1, "video", "BV1xx", ("dynamic",)),
       "视频推过 → 不算 dynamic 推过")

    # 别的 BV 互不影响
    ck(not b._content_pushed_by(1, "video", "BV1zz", ("video",)),
       "别的 BV → 不命中")


def test_video_silenced_after_dynamic():
    """核心回归：动态先推了，视频随后到 → 视频静默。"""
    print("[3] 动态先到 → 视频静默（本次修的就是这条）")
    now = int(time.time())

    mapping = {(1, "dynamic"): ["GA", "GB"], (1, "video"): ["GA", "GB"]}
    b = _bot({"dedupe_window": 900}, mapping)
    b._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid="BV1xx"))

    ck(b._gid_also_gets_dynamic(1, "GA"), "GA 订了动态")
    ck(b._content_pushed_by(1, "video", "BV1xx", ("dynamic",)),
       "该视频已被动态推过 → 视频应静默")

    # 只订投稿的群：收不到那条动态，不能一并静默
    b2 = _bot({"dedupe_window": 900},
              {(1, "dynamic"): ["GA"], (1, "video"): ["GA", "GB"]})
    b2._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid="BV1xx"))
    ck(not b2._gid_also_gets_dynamic(1, "GB"),
       "GB 只订了投稿 → 不静默（否则它什么都收不到）")


def test_live_silenced_after_dynamic():
    """开播动态先推了 → 开播提醒静默。"""
    print("[4] 开播动态先到 → 开播提醒静默")
    now = int(time.time())

    b = _bot({"dedupe_window": 900},
             {(1, "dynamic"): ["GA"], (1, "live"): ["GA"]})
    b._remember_pushed(1, "dynamic", FakeDyn(pub=now, room=7788))
    ck(b._content_pushed_by(1, "room", "7788", ("dynamic",)),
       "该房间已被开播动态推过 → 开播提醒应静默")


def test_not_mis_silenced():
    """反例：不能把该推的也吞掉。"""
    print("[5] 反例：不该静默的别静默")
    now = int(time.time())

    # 从没推过动态
    b = _bot({"dedupe_window": 900}, {(1, "dynamic"): ["GA"]})
    ck(not b._content_pushed_by(1, "video", "BV1xx", ("dynamic",)),
       "没推过 → 不静默")

    # 别的 UP
    b2 = _bot({"dedupe_window": 900})
    b2._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid="BV1xx"))
    ck(not b2._content_pushed_by(2, "video", "BV1xx", ("dynamic",)),
       "别的 UP → 不静默")

    # 超出时间窗
    b3 = _bot({"dedupe_window": 900})
    b3._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid="BV1xx"))
    b3._pushed_content[1][("video", "BV1xx")]["ts"] = float(now - 5000)
    ck(not b3._content_pushed_by(1, "video", "BV1xx", ("dynamic",)),
       "超出时间窗 → 不静默")


def test_old_direction_intact():
    """老方向不能回归：视频先推 → 动态仍要静默。"""
    print("[6] 老方向（视频先推 → 静默动态）仍然有效")
    now = int(time.time())

    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "video", FakeVideo("BV1xx", now - 180))
    ck(b._should_silence_dynamic(1, FakeDyn(pub=now - 180, bvid="BV1xx",
                                           mtype="video")),
       "视频先推，投稿动态 → 静默")

    b2 = _bot({"dedupe_window": 900})
    b2._remember_pushed(1, "live", FakeLive(7788, now - 180))
    ck(b2._should_silence_dynamic(1, FakeDyn(pub=now - 180, room=7788)),
       "开播先推，开播动态 → 静默")


def test_room_key_not_empty():
    """房间号 0 不能当成有效内容记进去。"""
    print("[7] 无效内容号不入库")
    b = _bot()
    b._remember_pushed(1, "live", FakeLive(0, 0))
    ck(not b._pushed_content.get(1), "房间号 0 → 不记")

    b2 = _bot()
    b2._remember_pushed(1, "video", FakeVideo("", 0))
    ck(not b2._pushed_content.get(1), "空 BV → 不记")


if __name__ == "__main__":
    for fn in (test_dynamic_marks_content,
               test_by_kind_isolation,
               test_video_silenced_after_dynamic,
               test_live_silenced_after_dynamic,
               test_not_mis_silenced,
               test_old_direction_intact,
               test_room_key_not_empty):
        fn()
    print("")
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    if FAILED:
        for n in FAILED:
            print("  FAILED:", n)
        sys.exit(1)
