"""验证：本地没存直播间号时，基线对齐要先反查一次再下结论。

直播间号（长号 / 短号）是 UP 主**开通过直播就一直存在**的，跟当前播没播
无关 —— 未开播时同样反查得到。以前 `_init_baseline` 只看本地缓存的
room_id，是 0 就直接判定「该 UP 主没有直播间」并暂缓 10 分钟，于是有
直播间的 UP 主被误晾着，日志里还写着一句错的结论。

（`_check_live` 里早就有"本地没有就反查"的兜底，基线对齐这条路径漏了，
两边行为不一致 —— 检测能查到、对齐却说没有。）
"""
import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
_STUBS = os.path.join(HERE, "_stubs")
if os.path.isdir(_STUBS):
    sys.path.insert(0, _STUBS)

import botpy  # noqa: E402
from bilibili import BiliError, LiveInfo  # noqa: E402
from bot import (  # noqa: E402
    BL_PENDING_TTL, BL_PENDING_TTL_ERR, NotifyBot,
)

OK = []


def ck(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    OK.append(bool(cond))
    return bool(cond)


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
    """room_map: uid -> (长号, 短号)；probe_raise: 反查时抛异常。"""

    def __init__(self, room_map=None, probe_raise=False):
        self.room_map = room_map or {}
        self.probe_raise = probe_raise
        self.probes = 0
        self.live = {}

    async def get_room_id_by_mid(self, uid):
        self.probes += 1
        if self.probe_raise:
            raise BiliError("HTTP 404（对面繁忙）")
        return self.room_map.get(uid, (0, 0))

    async def get_live(self, room_id):
        return self.live.get(room_id, LiveInfo(room_id=room_id, live_status=0))

    async def get_up(self, uid):
        from bilibili import Up
        r, s = self.room_map.get(uid, (0, 0))
        return Up(uid=uid, uname="UP%s" % uid, room_id=r, short_room_id=s)

    async def get_latest_video(self, uid):
        return None

    async def get_latest_dynamic(self, uid):
        return None

    async def get_pinned_dynamic(self, uid):
        return None

    async def get_pinned_comment(self, dyn_id, uid=0, rid="", **kw):
        return None

    async def close(self):
        pass


def make_bot(bili=None):
    fd, path = __import__("tempfile").mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path},
                    intents=botpy.Intents(public_messages=True))
    bot.qq = FakeQQ()
    bot.bili = bili or FakeBili()
    bot._silent = False
    return bot, path


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _pend(bot, uid):
    """取 live 的暂缓记录 (到期时间, 原因)。"""
    return getattr(bot, "_bl_pending", {}).get((uid, "live"))


# ---------------------------------------------------------------- 1
def t_local_room_no_probe():
    """本地已经存了直播间号 → 直接用，不再白发一次反查请求。"""
    bot, path = make_bot()
    uid, gid = 2001, "G1"
    st = bot.store
    st.set_up_info(uid, "波萝Buono", 12345, "", 678)
    st.set_sub(gid, uid, "live", True)
    run(bot._init_baseline(uid))
    ck("本地有房间号：不再反查", bot.bili.probes == 0)
    ck("本地有房间号：直播基线已建立", bool(st.baseline_of(gid, uid, "live")))
    os.unlink(path)


# ---------------------------------------------------------------- 2
def t_probe_then_baseline():
    """本地是 0 但反查得到 → 正常建基线，并把房间号补写回库。"""
    bot, path = make_bot(FakeBili(room_map={2002: (12345, 678)}))
    uid, gid = 2002, "G2"
    st = bot.store
    st.set_up_info(uid, "波萝Buono", 0, "", 0)
    st.set_sub(gid, uid, "live", True)
    run(bot._init_baseline(uid))
    ck("本地没房间号时反查了一次", bot.bili.probes == 1)
    ck("反查到后直播基线已建立", bool(st.baseline_of(gid, uid, "live")))
    up = st.get_up(uid)
    ck("房间号已补写回库（长号）", int(up.get("room_id") or 0) == 12345)
    ck("房间号已补写回库（短号）", int(up.get("short_room_id") or 0) == 678)
    ck("建成功就不再暂缓", _pend(bot, uid) is None)
    os.unlink(path)


# ---------------------------------------------------------------- 3
def t_baseline_when_offline():
    """没在播（live_status=0）也算对齐成功 —— 直播间号跟播没播无关。"""
    bot, path = make_bot(FakeBili(room_map={2003: (999, 0)}))
    uid, gid = 2003, "G3"
    st = bot.store
    st.set_up_info(uid, "星瞳", 0, "", 0)
    st.set_sub(gid, uid, "live", True)
    bot.bili.live[999] = LiveInfo(room_id=999, live_status=0, title="")
    run(bot._init_baseline(uid))
    base = st.baseline_of(gid, uid, "live") or {}
    ck("未开播也建立了基线（不是判成没有直播间）", bool(base))
    ck("基线记的是未开播状态", int(base.get("status", -1)) == 0)
    ck("未开播不进暂缓表", _pend(bot, uid) is None)
    os.unlink(path)


# ---------------------------------------------------------------- 4
def t_no_room_stall():
    """反查确实返回 0（真没开通过直播）→ 才判定没有直播间，用长冷却。"""
    bot, path = make_bot(FakeBili(room_map={}))
    uid, gid = 2004, "G4"
    st = bot.store
    st.set_up_info(uid, "没直播的UP", 0, "", 0)
    st.set_sub(gid, uid, "live", True)
    run(bot._init_baseline(uid))
    pend = _pend(bot, uid)
    ck("反查不到才记暂缓", pend is not None)
    if pend:
        ck("暂缓原因是「没有直播间」", "没有直播间" in str(pend[1]))
        left = float(pend[0]) - time.time()
        ck("确实是没直播间 → 用长冷却（约 %ds）" % BL_PENDING_TTL,
           abs(left - BL_PENDING_TTL) < 5)
    os.unlink(path)


# ---------------------------------------------------------------- 5
def t_probe_error_short_ttl():
    """反查接口异常 ≠ 没有直播间：用短冷却，2 分钟后就该重试。"""
    bot, path = make_bot(FakeBili(probe_raise=True))
    uid, gid = 2005, "G5"
    st = bot.store
    st.set_up_info(uid, "被风控的UP", 0, "", 0)
    st.set_sub(gid, uid, "live", True)
    run(bot._init_baseline(uid))
    pend = _pend(bot, uid)
    ck("反查异常也记暂缓", pend is not None)
    if pend:
        ck("暂缓原因归到接口异常", str(pend[1]).startswith("接口异常"))
        ck("接口异常不谎报「没有直播间」", "没有直播间" not in str(pend[1]))
        left = float(pend[0]) - time.time()
        ck("接口异常 → 用短冷却（约 %ds）" % BL_PENDING_TTL_ERR,
           abs(left - BL_PENDING_TTL_ERR) < 5)
    os.unlink(path)


# ---------------------------------------------------------------- 6
def t_source_uses_resolve():
    """源码扫描：_init_baseline 必须走反查，不能退回"缓存是 0 就认定没有"。"""
    src = open(os.path.join(os.path.dirname(HERE), "src", "bot.py"),
               encoding="utf-8").read()
    lines = src.splitlines()
    try:
        start = next(i for i, l in enumerate(lines)
                     if "async def _init_baseline" in l)
    except StopIteration:
        ck("找得到 _init_baseline", False)
        return
    end = start + 1
    while end < len(lines):
        s = lines[end]
        if s.startswith("    def ") or s.startswith("    async def "):
            break
        end += 1
    body = "\n".join(lines[start:end])
    ck("_init_baseline 里会反查直播间号", "_resolve_room_id" in body)
    ck("有 _resolve_room_id 这个方法", "async def _resolve_room_id" in src)


def main():
    t_local_room_no_probe()
    t_probe_then_baseline()
    t_baseline_when_offline()
    t_no_room_stall()
    t_probe_error_short_ttl()
    t_source_uses_resolve()
    bad = OK.count(False)
    print("\n" + ("ALL PASS" if not bad else "%d FAILED" % bad)
          + "（%d 项）" % len(OK))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
