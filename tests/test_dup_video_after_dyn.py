# -*- coding: utf-8 -*-
"""锁定「动态先推 → 视频随后又推一遍」的**时间兜底**（v1.61.0）。

症状：
  更新视频时群里连着来两条 —— 一条「新动态」，一条「新视频投稿」，
  配图和标题是同一个：《会突然大笑》。

为什么 v1.51.0 的反向去重没拦住：
  它靠的是**动态里解析出的 BV 号**建索引：动态先推 → 记下 BV →
  视频随后到 → 查得见就静默。可新版投稿动态把视频地址**藏在跳转链接
  里**（major 只有 opus，没有 archive 卡片），BV 取不到时那份索引
  是**空的** —— 于是视频照样再推一遍。而另一方向的兜底
  （`_silence_after_push`，按发布时间比对）只管"视频先推、动态后到"，
  反过来完全没有兜底。

修法：动态推送时额外记一份「动态推过」的时间戳，
`_should_silence_video` 按**发布时间**兜底比对 ——
两边时间挨在一起就是同一件事。谁先被发现就保留谁：
动态接口通常更快，先到的多半是动态（保留动态）；
同一轮里视频先被检测到则保留视频（老行为不变）。
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
    """模拟动态：bvid 为空 = 新版投稿动态（地址藏在跳转链接里）。"""

    def __init__(self, pub=0, room=0, bvid="", mtype="normal", dyn_id="D1"):
        self.pubdate = pub
        self.ref_room_id = room
        self.ref_bvid = bvid
        self.major_type = mtype
        self.dyn_id = dyn_id


class FakeVideo:
    def __init__(self, bvid, pubdate):
        self.bvid = bvid
        self.pubdate = pubdate


class FakeStore:
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


def test_dyn_push_records_time():
    """动态推送必须留下"推过"的时间（旧写法这里直接 return，什么都不记）。"""
    print("[1] 动态推送要留下时间戳")
    now = int(time.time())

    b = _bot()
    b._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid=""))
    rec = (b._recent_push.get(1) or {}).get("dynamic")
    ck(isinstance(rec, dict), "动态推过 → 记下了 dynamic 条目")
    ck(int(rec.get("pub", 0) or 0) == now, "记的是动态自身的发布时间")
    ck(float(rec.get("ts", 0) or 0) > 0, "记下了推送时刻")

    # 取不到 BV 时那份内容索引确实是空的 —— 正是漏网的根源
    ck(not b._content_pushed_by(1, "video", "BV1xx", ("dynamic",)),
       "取不到 BV → 内容索引为空（所以才需要时间兜底）")


def test_video_silenced_by_time():
    """核心回归：动态先推、BV 取不到 → 视频按发布时间静默。"""
    print("[2] 动态先推（拿不到 BV）→ 视频按时间静默")
    now = int(time.time())

    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "dynamic", FakeDyn(pub=now - 60, bvid=""))
    ck(b._should_silence_video(1, FakeVideo("BV1xx", now - 60)),
       "投稿与动态同一时刻 → 静默（本次修的就是这条）")

    # 视频接口比动态慢 20 分钟：默认窗口 900s 已过，仍要静默
    b2 = _bot({"dedupe_window": 900})
    b2._remember_pushed(1, "dynamic", FakeDyn(pub=now - 1300, bvid=""))
    b2._recent_push[1]["dynamic"]["ts"] = float(now - 1200)
    ck(b2._should_silence_video(1, FakeVideo("BV1xx", now - 1300)),
       "视频接口慢 20 分钟 → 仍在回溯内，静默")


def test_not_mis_silenced():
    """反例：不该静默的绝不能吞。"""
    print("[3] 反例：不该静默的别静默")
    now = int(time.time())

    # 从没推过动态
    b = _bot()
    ck(not b._should_silence_video(1, FakeVideo("BV1xx", now)),
       "没推过动态 → 不静默")

    # 动态推过，但这条投稿是几天前的老稿
    b2 = _bot()
    b2._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid=""))
    ck(not b2._should_silence_video(1, FakeVideo("BV1old", now - 86400)),
       "发布时间差一天 → 不是同一件事，照推")

    # 别的 UP
    b3 = _bot()
    b3._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid=""))
    ck(not b3._should_silence_video(2, FakeVideo("BV1xx", now)),
       "别的 UP → 不静默")

    # 投稿时间取不到 → 判定不了，按"推"处理（绝不能吞真投稿）
    b4 = _bot()
    b4._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid=""))
    ck(not b4._should_silence_video(1, FakeVideo("BV1xx", 0)),
       "投稿时间取不到 → 不静默")

    # 用户手动关掉静默
    b5 = _bot({"silence_dyn_after_push": False})
    b5._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid=""))
    ck(not b5._should_silence_video(1, FakeVideo("BV1xx", now)),
       "silence_dyn_after_push=false → 不静默")

    # 回溯也过了（默认 1 小时）
    b6 = _bot({"dedupe_window": 900})
    b6._remember_pushed(1, "dynamic", FakeDyn(pub=now - 9000, bvid=""))
    b6._recent_push[1]["dynamic"]["ts"] = float(now - 9000)
    ck(not b6._should_silence_video(1, FakeVideo("BV1xx", now - 9000)),
       "超出回溯 → 不静默")


def test_bv_path_still_works():
    """动态里取得到 BV 时仍走内容索引，两条路互不干扰。"""
    print("[4] 取得到 BV 时老路子照旧")
    now = int(time.time())

    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid="BV1xx"))
    ck(b._content_pushed_by(1, "video", "BV1xx", ("dynamic",)),
       "内容索引命中")
    ck(b._should_silence_video(1, FakeVideo("BV1xx", now)),
       "时间兜底同样成立（双保险）")
    ck(not b._content_pushed_by(1, "video", "BV1zz", ("dynamic",)),
       "别的 BV → 不命中")


def test_dynamic_still_silenced_after_video():
    """老方向不能回归：视频先推 → 动态仍要静默。"""
    print("[5] 老方向（视频先推 → 静默动态）仍然有效")
    now = int(time.time())

    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "video", FakeVideo("BV1xx", now - 180))
    ck(b._should_silence_dynamic(1, FakeDyn(pub=now - 180, bvid="BV1xx",
                                           mtype="video")),
       "视频先推，投稿动态 → 静默")
    # 动态里取不到 BV 时靠 _silence_after_push 兜底
    ck(b._should_silence_dynamic(1, FakeDyn(pub=now - 180, bvid="")),
       "视频先推且动态取不到 BV → 按时间兜底静默")


def test_video_kind_entry_not_polluted():
    """dynamic 条目不能把 _silence_after_push 的 live/video 判断带偏。"""
    print("[6] dynamic 条目不干扰既有判断")
    now = int(time.time())

    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "dynamic", FakeDyn(pub=now, bvid=""))
    ck(not b._silence_after_push(1, FakeDyn(pub=now + 100)),
       "只推过动态 → 不会因为自己的记录去吞动态")


if __name__ == "__main__":
    for fn in (test_dyn_push_records_time,
               test_video_silenced_by_time,
               test_not_mis_silenced,
               test_bv_path_still_works,
               test_dynamic_still_silenced_after_video,
               test_video_kind_entry_not_polluted):
        fn()
    print("")
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    if FAILED:
        for n in FAILED:
            print("  FAILED:", n)
        sys.exit(1)
