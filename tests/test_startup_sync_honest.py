# -*- coding: utf-8 -*-
"""启动同步的汇总行不能撒谎（v2.3.4）。

用户反馈的现象：
    日志里 cookie 明明已经失效（code=-101），启动同步却照样宣布

        启动同步完成：9 位 UP 主的当前状态已记录为基线（静默，不推送）

    —— 一个基线都没写进去。用户看到"已记录为基线"就以为静默真的生效了，
    于是不会去重新登录，问题一直挂着。说谎比不说更坑。

根因两条，本文件分别钉住：
    ① _sync_one 里检测失败会被 except 吞掉，之后照样 return 1 → done 虚高
    ② on_ready 的汇总行无条件打印"已记录为基线"，不看登录态

修复：
    ① _sync_one 开头检查 bili.auth_bad()，登录态不被认可时直接 return 0
    ② 汇总行按登录态分两种说法：失效时明确写"未能记录"+ 该怎么办
"""
import asyncio
import os
import sys
import tempfile

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
    """剥掉整行注释 —— 修复处写的说明里含同样字样，不剥会假通过。"""
    return "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))


# ──────────────────────────────────────────────────────────────
# 假依赖：登录态失效，所有接口都失败
# ──────────────────────────────────────────────────────────────

class FakeBiliDead:
    """B站 不认登录态：每个接口都抛 -101。"""

    def __init__(self, bad=True):
        self._bad = bad

    def auth_bad(self):
        return self._bad

    def auth_reason(self):
        return "查询失败（code=-101）" if self._bad else ""

    async def get_up(self, uid):
        from bilibili import Up
        return Up(uid=uid, uname=f"UP{uid}", room_id=0)

    async def get_live(self, room_id):
        from bilibili import BiliError
        raise BiliError("查询失败（code=-101）")

    async def get_latest_video(self, uid):
        from bilibili import BiliError
        raise BiliError("查询失败（code=-101）")

    async def get_latest_dynamic(self, uid):
        from bilibili import BiliError
        raise BiliError("查询失败（code=-101）")

    async def get_pinned_dynamic(self, uid):
        from bilibili import BiliError
        raise BiliError("查询失败（code=-101）")

    async def get_room_id_by_mid(self, uid):
        return (0, 0)

    async def close(self):
        pass


class FakeQQ:
    def __init__(self):
        self.sent = []

    async def send_text(self, gid, c, **kw):
        self.sent.append((gid, c))

    async def send_markdown(self, gid, md, keyboard=None, **kw):
        self.sent.append((gid, md))

    async def close(self):
        pass


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def make_bot(bad=True):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path},
                    intents=botpy.Intents(public_messages=True))
    bot.qq = FakeQQ()
    bot.bili = FakeBiliDead(bad=bad)
    bot._silent = False
    bot._startup_synced = set()
    return bot, path


def _add_sub(bot, uid, gid, kinds):
    bot.store.set_up_info(uid, f"UP{uid}", 0, "", 0)
    for k in kinds:
        bot.store.set_sub(gid, uid, k, True)


# ──────────────────────────────────────────────────────────────
# 1. 源码层面
# ──────────────────────────────────────────────────────────────

def test_source_layer():
    src = _strip_comments(_src())

    # ① _sync_one 里要有登录态短路
    i = src.find("async def _sync_one(")
    ck("找得到 _sync_one", i > 0)
    body = src[i:i + 2000] if i > 0 else ""
    ck("_sync_one 会检查 auth_bad", "auth_bad()" in body)

    # ② 汇总行要分情况
    j = src.find("启动同步完成")
    ck("汇总行仍在（正常路径保留）", j > 0)
    seg = src[max(0, j - 900):j + 900]
    ck("失效时改说「未能记录」", "未能" in seg)
    ck("按登录态分支（_boot_bili_state）", "_boot_bili_state" in seg)
    ck("会提示重新登录", "重新登录" in seg)
    ck("on_ready 会记录启动时的登录态", "_boot_bili_state" in src)


# ──────────────────────────────────────────────────────────────
# 2. 端到端：登录态失效时，同步不能算成功
# ──────────────────────────────────────────────────────────────

def test_dead_auth_sync_returns_zero():
    bot, path = make_bot(bad=True)
    uid, gid = 320070259, "G1"
    _add_sub(bot, uid, gid, ["live", "video", "dynamic"])
    try:
        n = run(bot._startup_sync())
        ck("登录态失效：启动同步不再虚报成功数（0）", n == 0)
        ck("登录态失效：没有被记进 _startup_synced",
           uid not in (getattr(bot, "_startup_synced", set()) or set()))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_healthy_auth_still_works():
    """登录态正常时不受影响 —— 不能为了修撒谎把正常路径也改坏。"""
    bot, path = make_bot(bad=False)
    uid, gid = 320070259, "G1"
    _add_sub(bot, uid, gid, ["live", "video", "dynamic"])
    try:
        # auth_bad=False，接口仍会抛错（这个假 B站 就是全失败的），
        # 这里只验证**短路没被误触发**：返回值不该因为短路逻辑而崩掉
        try:
            n = run(bot._startup_sync())
            ok = isinstance(n, int)
        except Exception:
            ok = False
        ck("登录态正常：短路不误触发（不抛异常）", ok)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def main():
    test_source_layer()
    test_dead_auth_sync_returns_zero()
    test_healthy_auth_still_works()
    bad = OK.count(False)
    print()
    print(f"{len(OK) - bad}/{len(OK)}")
    if bad:
        for i, v in enumerate(OK):
            if not v:
                print(f"  [FAIL] #{i}")
        sys.exit(1)


if __name__ == "__main__":
    main()
