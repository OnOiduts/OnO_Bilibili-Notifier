# -*- coding: utf-8 -*-
"""开机补推的真值测试：基线**真的写进 store** 才算对齐成功。

旧测试（test_startup_sync_honest.py）验的是「源码里有没有这段字」和
「函数有没有被调用」，所以全绿也能一直崩。本文件构造真实 NotifyBot +
假 B 站客户端，跑完对齐后直接查 store 里的基线水位。

复现的是用户这条真机路径：
    开机时 cookie 失效 → 接口全失败 → 宽限轮照样宣布「9 位已强制对齐」
    → 第 2 轮解除静默 → 扫码恢复 cookie → 拿停机前旧基线比对
    → 把重启期间的内容全补推一遍。
"""
import asyncio
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(os.path.dirname(HERE), "src")
# 反向验证时指向被改坏的 bot.py 副本；其余模块仍从真源补。
BASE = os.environ.get("BILI_TRUE_SRC") or REAL
STUBS = os.path.join(HERE, "_stubs")
for p in (STUBS, REAL, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)
# BASE 必须在最前
sys.path.insert(0, BASE)

import botpy  # noqa: E402
import bot as botmod  # noqa: E402
from bot import NotifyBot  # noqa: E402
from bilibili import BiliError  # noqa: E402

OK = []


def ck(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    OK.append(bool(cond))
    return bool(cond)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


DEAD_MSG = "查询失败（code=-101）"


class FakeBiliDead:
    """B 站不认登录态：每个接口都抛错。"""

    def __init__(self):
        self.cookie = "OLD"

    def auth_bad(self):
        return True

    def auth_reason(self):
        return DEAD_MSG

    async def get_live(self, room_id):
        raise BiliError(DEAD_MSG)

    async def get_latest_video(self, uid):
        raise BiliError(DEAD_MSG)

    async def get_latest_dynamic(self, uid):
        raise BiliError(DEAD_MSG)

    async def get_pinned_dynamic(self, uid):
        raise BiliError(DEAD_MSG)

    async def get_room_id_by_mid(self, uid):
        return (0, 0)

    async def close(self):
        pass


class FakeBiliGood:
    """登录态恢复后：接口返回当前最新内容。"""

    def __init__(self):
        self.cookie = "NEW"

    def auth_bad(self):
        return False

    def auth_reason(self):
        return ""

    async def get_live(self, room_id):
        return types.SimpleNamespace(live_status=0, title="新直播标题")

    async def get_latest_video(self, uid):
        return types.SimpleNamespace(bvid="BV_NEW", pubdate=2000)

    async def get_latest_dynamic(self, uid):
        return types.SimpleNamespace(dyn_id="D_NEW", pubdate=2000)

    async def get_pinned_dynamic(self, uid):
        return types.SimpleNamespace(dyn_id="", rid="")

    async def get_room_id_by_mid(self, uid):
        return (0, 0)

    async def close(self):
        pass


def make_bot():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path},
                    intents=botpy.Intents(public_messages=True))
    bot.bili = FakeBiliDead()
    bot._silent = False
    bot._startup_synced = set()
    bot._grace_pending = set()
    bot._grace_extra_rounds = 0
    return bot, path


def add_sub(bot, uid, gid, kinds):
    bot.store.set_up_info(uid, f"UP{uid}", 12345, "", 0)
    for k in kinds:
        bot.store.set_sub(gid, uid, k, True)


def bl(bot, gid, uid, kind):
    return bot.store.baseline_of(gid, uid, kind) or {}


def main():
    uid, gid = 320070259, "G1"
    kinds = ["live", "video", "dynamic"]
    bot, path = make_bot()
    try:
        add_sub(bot, uid, gid, kinds)
        # 停机前的旧基线（cookie 失效期间启动，这些值不会被刷新）
        bot.store.set_baseline_of(gid, uid, "video", bvid="BV_OLD", pubdate=1000)
        bot.store.set_baseline_of(gid, uid, "dyn", id="D_OLD", pubdate=1000)

        # ── ① 接口全失败：宽限轮不能算成功 ──
        n = run(bot._grace_align())
        ck("接口全失败：宽限对齐返回 0 位成功", n == 0)
        ck("接口全失败：视频基线仍是停机前旧值",
           bl(bot, gid, uid, "video").get("bvid") == "BV_OLD")
        ck("接口全失败：动态基线仍是停机前旧值",
           bl(bot, gid, uid, "dyn").get("id") == "D_OLD")
        pend = bot._grace_pending_map()
        ck("接口全失败：该 UP 主留在待对齐集合里（不会一轮放行）", uid in pend)
        ck("接口全失败：宽限判定继续保持静默",
           bot._grace_keep_silent(9, 1, pend, 0, 5) is True)

        # ── ② 登录态恢复：走真实 _reload_cookie ──
        async def fake_verify(_cookie=None):
            return {"ok": True, "state": "logged", "uname": "孜岚",
                    "mid": "1142118273"}

        bot._bili_verify = fake_verify
        botmod.load_config = lambda _p: {"bili_cookie": "NEW"}
        run(bot._reload_cookie())
        ck("cookie 恢复后重新进入静默", bot._silent is True)
        ck("cookie 恢复后该 UP 主被重新标成待对齐", uid in pend)
        ck("cookie 恢复后清空启动同步的假名单",
           uid not in (getattr(bot, "_startup_synced", set()) or set()))

        # ── ③ 换成正常接口：基线必须真落盘 ──
        bot.bili = FakeBiliGood()
        n2 = run(bot._grace_align())
        ck("接口恢复：宽限对齐返回 1 位真实成功", n2 == 1)
        ck("接口恢复：视频基线刷新成最新 BV_NEW",
           bl(bot, gid, uid, "video").get("bvid") == "BV_NEW")
        ck("接口恢复：动态基线刷新成最新 D_NEW",
           bl(bot, gid, uid, "dyn").get("id") == "D_NEW")
        ck("接口恢复：直播基线真的写入了",
           bl(bot, gid, uid, "live").get("title") == "新直播标题")
        ck("接口恢复：待对齐集合已清空", uid not in pend)
        ck("对齐完成后宽限判定允许解除静默",
           bot._grace_keep_silent(9, 1, pend, 0, 5) is False)

        # ── ④ 对齐函数本身要返回真实回执 ──
        receipt = run(bot._init_baseline(uid, only_missing=False))
        ck("_init_baseline 返回 dict 回执", isinstance(receipt, dict))
        ck("回执含真实写入项 (G1, video)",
           (gid, "video") in (receipt.get("written") or set()))
        ck("回执含真实写入项 (G1, dyn)",
           (gid, "dyn") in (receipt.get("written") or set()))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    bad = OK.count(False)
    print()
    print(f"{len(OK) - bad}/{len(OK)}")
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
