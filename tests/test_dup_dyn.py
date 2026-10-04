# -*- coding: utf-8 -*-
"""锁定两个修复，防止改回去：

1. 自检「合集检测」取不到合集 —— seasons_series_list（/lists 页面用的
   就是这个）返回的是 data.items_lists.seasons_list / .series_list，
   不是 data.items。只认 data.items 会取到空列表，于是永远显示
   "该 UP 主没有公开合集"。
2. 开播后 B 站自动发的动态没被静默 —— 它不一定带 live_rcmd 卡片
   （有的版本 major 里只有 opus），ref_room_id 取不到 0，按内容比对
   判不出来，于是「开播提醒 + 开播动态」连着推两条。
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
# botpy 在公共源里没有，用桩顶上（只含 botpy，不碰 aiohttp ——
# 假 aiohttp 会把行为测歪）
sys.path.insert(0, "/data/workspace/_stub2")

from bilibili import BiliClient  # noqa: E402

PASS = FAIL = 0


def ck(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


# ── 1. get_up_seasons 解析 ──


class FakeBili(BiliClient):
    """绕过 __init__（它要配置/凭据），只测解析。"""

    def __init__(self, data):
        self._data = data

    async def ensure_buvid3(self):
        return None

    async def _wbi_sign(self, base):
        return dict(base)

    async def _throttle(self, key):
        return None

    async def _get(self, url, params=None, referer=None):
        return self._data


NESTED = {
    "code": 0,
    "data": {
        "items_lists": {
            "seasons_list": [
                {"meta": {"season_id": 2446, "name": "合集A",
                          "total": 30, "mtime": 1712345678}},
            ],
            "series_list": [
                {"meta": {"series_id": 370368, "name": "列表B",
                          "total": 20, "mtime": 1712345000}},
            ],
        }
    },
}

FLAT = {
    "code": 0,
    "data": {"items": [
        {"season_id": 999, "name": "平铺合集", "total": 5, "mtime": 1700000000},
    ]},
}


def test_seasons_parse():
    print("[1] 合集清单解析（/lists 页面结构）")
    out = asyncio.run(FakeBili(NESTED).get_up_seasons(15262818))
    ck(len(out) == 2, f"能取到合集和列表两个（实际 {len(out)}）")
    ck(bool(out) and out[0]["type"] == "season", "第一个是合集(season)")
    ck(bool(out) and out[0]["id"] == 2446, "合集 id=2446")
    ck(bool(out) and out[1]["type"] == "series", "第二个是视频列表(series)")
    ck(bool(out) and out[1]["id"] == 370368, "列表 id=370368")
    ck(any(s["type"] == "season" for s in out), "存在可订阅的合集")

    # 旧结构（平铺 data.items）也要认
    out2 = asyncio.run(FakeBili(FLAT).get_up_seasons(1))
    ck(len(out2) == 1 and out2[0]["id"] == 999, "兼容平铺 data.items")

    # 空数据不能炸
    out3 = asyncio.run(FakeBili({"code": 0, "data": {}}).get_up_seasons(1))
    ck(out3 == [], "空返回 → 空列表，不抛异常")

    # ⚠️ 关键回归：错误写法（只读 data.items）在嵌套结构下必然取空
    nested_items = ((NESTED.get("data") or {}).get("items")) or []
    ck(nested_items == [], "（对照）旧写法在嵌套结构下确实取不到东西")


# ── 2. 开播后重复动态 ──


class FakeDyn:
    def __init__(self, pub=0, room=0, bvid="", mtype="normal"):
        self.pubdate = pub
        self.ref_room_id = room
        self.ref_bvid = bvid
        self.major_type = mtype


def _bot(cfg=None, rec=None):
    import bot
    b = bot.NotifyBot.__new__(bot.NotifyBot)
    b.cfg = cfg if cfg is not None else {}
    b._recent_push = rec if rec is not None else {}
    return b


def test_silence():
    print("[2] 开播/投稿后的重复动态")
    now = int(time.time())

    # ① 刚推过开播提醒，动态是这之后发的 → 静默
    b = _bot({"dedupe_window": 900},
             {1: {"live": {"val": "7788", "ts": float(now - 10)}}})
    ck(b._silence_after_push(1, FakeDyn(pub=now)), "刚开播 → 紧跟着的动态静默")

    # ② 动态发布时间明显早于开播 → 是旧动态，不能静默
    ck(not b._silence_after_push(1, FakeDyn(pub=now - 3600)),
       "发布时间早于开播 → 不静默")

    # ③ 超出时间窗 → 不静默（不能一直吞动态）
    b2 = _bot({"dedupe_window": 900},
              {1: {"live": {"val": "7788", "ts": float(now - 5000)}}})
    ck(not b2._silence_after_push(1, FakeDyn(pub=now)), "超出窗口 → 不静默")

    # ④ 开关关掉 → 不静默（用户可退回只按内容精确去重）
    b3 = _bot({"dedupe_window": 900, "silence_dyn_after_push": False},
              {1: {"live": {"val": "7788", "ts": float(now - 10)}}})
    ck(not b3._silence_after_push(1, FakeDyn(pub=now)), "开关关 → 不静默")

    # ⑤ 从没推送过 → 不静默
    ck(not _bot({}, {})._silence_after_push(1, FakeDyn(pub=now)),
       "没推送记录 → 不静默")

    # ⑥ 投稿同理
    b4 = _bot({"dedupe_window": 900},
              {1: {"video": {"val": "BV1xx", "ts": float(now - 5)}}})
    ck(b4._silence_after_push(1, FakeDyn(pub=now)), "刚投过稿 → 动态静默")

    # ⑦ 集成：ref_room_id 取不到（0）时，靠时间窗兜住
    ck(b._should_silence_dynamic(1, FakeDyn(pub=now, room=0, mtype="normal")),
       "集成：取不到直播间号也能静默")

    # ⑧ 直播预约（reserve）始终不静默 —— 它通知的是"未来要播"
    ck(not b._should_silence_dynamic(
        1, FakeDyn(pub=now, room=7788, mtype="reserve")), "直播预约不静默")

    # ⑨ 精确去重仍然有效（房间号对得上）
    b5 = _bot({"dedupe_window": 900},
              {2: {"live": {"val": "7788", "ts": float(now)}}})
    ck(b5._should_silence_dynamic(2, FakeDyn(pub=now, room=7788,
                                             mtype="live")),
       "精确去重：房间号对得上 → 静默")

    # ⑩ 别的 UP 不受影响
    ck(not b._should_silence_dynamic(3, FakeDyn(pub=now)), "别的 UP 不受影响")

    # ⑪ 配置项有默认值（不写也按 true 处理）
    ck(_bot({}, {1: {"live": {"val": "1", "ts": float(now)}}})
       ._silence_after_push(1, FakeDyn(pub=now)) is True, "默认开启")

    # ⑫ ⚠️ 取不到发布时间 → 不静默。宁可重复推一条，也不能吞掉真动态
    #    （test_notify 的「动态只推给开了动态的群」就卡在这：测试里
    #     的动态没设 pubdate，被兜底静默掉了）
    ck(not b._silence_after_push(1, FakeDyn(pub=0)),
       "取不到发布时间 → 不静默（宁可多推也别漏）")
    ck(not b._should_silence_dynamic(1, FakeDyn(pub=0, room=0)),
       "集成：无时间无指向 → 不静默")


if __name__ == "__main__":
    test_seasons_parse()
    test_silence()
    print(f"\n通过 {PASS} / 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)
