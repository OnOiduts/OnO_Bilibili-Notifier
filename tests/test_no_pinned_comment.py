# -*- coding: utf-8 -*-
"""「没有置顶评论」不能被当成接口异常 / UP 主不存在（v2.3.5 真值测试）。

用户日志里的真实现象：

    [星瞳_Official] 置顶评论获取失败：code=-404 msg=啥都木有   ← 每轮一条
    启动宽限（第 10/1 轮）：8 位 UP 主的基线已强制对齐
    启动宽限已达上限（5 轮），仍有 1 位 UP 主基线未对齐 …       ← 每轮一条

根因：`code=-404 / 啥都木有` 是评论接口对「这条动态没有可取的置顶评论」
的正常回答，却被逐层当成真错误：
    ① 抛 BiliError → 基线对齐记「接口异常」→ 宽限永远差这 1 位
    ② 检测每轮打 WARNING → 刷屏 + 每轮十几个无用请求（反过来加重风控）
    ③ 面板翻译器把 -404 一律翻成「UP 主不存在或已注销 → 核对 UID」
    ④ 宽限放行后 else 分支每轮重复打同一条上限告警
    ⑤ 登录态恢复后宽限轮数没归零 → 「第 10/1 轮」

本文件全部查**真值**：假客户端的调用次数、store 里的基线、日志对象
实际收到的 WARNING 条数、翻译器返回的 level/文案。不验「源码里有没有字」。
"""
import asyncio
import os
import re
import sys
import tempfile
from types import SimpleNamespace

BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
_STUBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_stubs")
for _p in (_STUBS, BASE):
    if os.path.isdir(_p):
        sys.path.insert(0, _p)

import botpy  # noqa: E402
import bilibili as bl  # noqa: E402
import bot as botmod  # noqa: E402
from bot import NotifyBot  # noqa: E402

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


def _src(fn="bot.py"):
    with open(os.path.join(BASE, fn), encoding="utf-8") as f:
        return f.read()


class FakeLog:
    def __init__(self):
        self.warnings = []
        self.debugs = []

    def warning(self, *a, **kw):
        self.warnings.append(" ".join(str(x) for x in a))

    def debug(self, *a, **kw):
        self.debugs.append(" ".join(str(x) for x in a))

    def info(self, *a, **kw):
        pass

    def error(self, *a, **kw):
        pass


class FakeBili:
    """置顶动态存在，但评论区所有候选都回 -404。"""

    def __init__(self, comment_mode="404"):
        self.comment_mode = comment_mode
        self.calls = {"pinned": 0, "comment": 0}

    def auth_bad(self):
        return False

    def auth_reason(self):
        return ""

    def _is_no_comment(self, e):
        s = str(e or "")
        return "code=-404" in s or "啥都木有" in s

    async def get_up(self, uid):
        return bl.Up(uid=uid, uname=f"UP{uid}", room_id=0)

    async def get_pinned_dynamic(self, uid):
        self.calls["pinned"] += 1
        return SimpleNamespace(dyn_id="D1", rid="R1",
                               comment_id_str="", comment_type=0)

    async def get_pinned_comment(self, *a, **kw):
        self.calls["comment"] += 1
        if self.comment_mode == "404":
            raise bl.BiliError("code=-404 msg=啥都木有")
        if self.comment_mode == "none":
            return None
        return {"id": "C1", "mid": "0", "content": "hello"}

    async def get_room_id_by_mid(self, uid):
        return (0, 0)

    async def close(self):
        pass


class FakeQQ:
    async def send_text(self, *a, **kw):
        pass

    async def send_markdown(self, *a, **kw):
        pass

    async def close(self):
        pass


def make_bot(comment_mode="404"):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    b = NotifyBot({"data_file": path},
                  intents=botpy.Intents(public_messages=True))
    b.qq = FakeQQ()
    b.bili = FakeBili(comment_mode)
    b._silent = False
    b._startup_synced = set()
    return b, path


def add_sub(b, uid=320070259, gid="G1"):
    b.store.set_up_info(uid, f"UP{uid}", 0, "", 0)
    b.store.set_sub(gid, uid, "top_comment", True)


# ──────────────────────────────────────────────────────────────
# 1. 底层：全候选 -404 是常态，混合错误仍要抛
# ──────────────────────────────────────────────────────────────

def _client_with(handler):
    cli = bl.BiliClient()
    cli._get = handler
    return cli


def test_bili_all_404_returns_none():
    calls = []

    async def h(url, params, referer=None, **kw):
        calls.append(params)
        raise bl.BiliError("code=-404 msg=啥都木有")

    cli = _client_with(h)
    try:
        got = run(cli.get_pinned_comment("D1", 123, "R1"))
        raised = False
    except bl.BiliError:
        got, raised = None, True
    ck("全候选 -404：返回 None（不再抛 BiliError）",
       (not raised) and got is None)
    ck("全候选 -404：确实遍历了多个评论区候选", len(calls) > 1)


def test_bili_mixed_errors_still_raise():
    n = [0]

    async def h(url, params, referer=None, **kw):
        n[0] += 1
        if n[0] >= 3:
            raise bl.BiliError("code=-352 风控拦截")
        raise bl.BiliError("code=-404 msg=啥都木有")

    cli = _client_with(h)
    try:
        run(cli.get_pinned_comment("D1", 123, "R1"))
        raised = False
        msg = ""
    except bl.BiliError as e:
        raised, msg = True, str(e)
    ck("混入 -352 时仍按真异常抛出", raised and "-352" in msg)


def test_bili_success_still_returns_dict():
    async def h(url, params, referer=None, **kw):
        return {"data": {"top_replies": [
            {"rpid_str": "C9", "member": {"uname": "u", "mid": 123},
             "content": {"message": "hi"}}]}}

    got = run(_client_with(h).get_pinned_comment("D1", 123, "R1"))
    ck("真有置顶评论时仍正常返回 dict", isinstance(got, dict) and got.get("id") == "C9")


# ──────────────────────────────────────────────────────────────
# 2. 基线 / 宽限：常态不该拖住静默
# ──────────────────────────────────────────────────────────────

def test_baseline_no_comment_is_not_an_error():
    b, path = make_bot("404")
    log = FakeLog()
    old = botmod._log
    botmod._log = log
    try:
        uid, gid = 320070259, "G1"
        add_sub(b, uid, gid)
        receipt = run(b._init_baseline(uid, only_missing=False))
        stalled = receipt.get("stalled") or {}
        why = str(stalled.get("dyn_top_cmt", ""))
        ck("基线暂缓原因不再写成「接口异常」", "接口异常" not in why)
        ck("基线暂缓原因明确是「没有可取的置顶评论」",
           "没有可取的置顶评论" in why)
        ck("-404 不再打「初始化置顶评论基线失败」WARNING",
           not any("初始化置顶评论基线失败" in w for w in log.warnings))

        pending = b._grace_pending_map()
        pending.add(uid)
        done = run(b._grace_align())
        ck("宽限轮：这位 UP 被判为已对齐（不再永远差 1 位）",
           uid not in pending and done >= 1)
    finally:
        botmod._log = old
        try:
            os.remove(path)
        except OSError:
            pass


def test_top_cmt_empty_cooldown():
    b, path = make_bot("404")
    log = FakeLog()
    old = botmod._log
    botmod._log = log
    try:
        run(b._check_top_comment(320070259, "UP"))
        until = getattr(b, "_top_cmt_empty_until", {}).get(320070259, 0)
        ck("无置顶评论：记下常态冷却", until > 0)
        ck("无置顶评论：不再打 WARNING 刷屏", len(log.warnings) == 0)
        calls_before = b.bili.calls["comment"]
        run(b._check_top_comment(320070259, "UP"))
        ck("冷却期内不再向 B 站发评论请求",
           b.bili.calls["comment"] == calls_before)
    finally:
        botmod._log = old
        try:
            os.remove(path)
        except OSError:
            pass


def test_log_fail_throttle():
    b, path = make_bot("404")
    log = FakeLog()
    old = botmod._log
    botmod._log = log
    try:
        err = bl.BiliError("HTTP 502")
        b._log_check_fail("UP", "动态", err)
        b._log_check_fail("UP", "动态", err)
        b._log_check_fail("UP", "动态", err)
        ck("同一失败 3 次只 WARNING 一次", len(log.warnings) == 1)
        ck("重复的那几条降为 debug", len(log.debugs) >= 2)
    finally:
        botmod._log = old
        try:
            os.remove(path)
        except OSError:
            pass


def test_grace_release_warns_once():
    b, path = make_bot("404")
    log = FakeLog()
    old = botmod._log
    botmod._log = log
    try:
        first = b._grace_release({320070259}, 5)
        second = b._grace_release({320070259}, 5)
        third = b._grace_release({320070259}, 5)
        ck("首次放行打一次上限告警", first is True)
        ck("后续轮次不再重复打", second is False and third is False)
        ck("告警实际只写出 1 条", len(log.warnings) == 1)
        ck("放行后 _grace_finished 置位", b._grace_finished is True)
    finally:
        botmod._log = old
        try:
            os.remove(path)
        except OSError:
            pass


def test_auth_recovered_resets_grace():
    b, path = make_bot("404")
    log = FakeLog()
    old = botmod._log
    botmod._log = log
    try:
        add_sub(b)
        b._grace_round_no = 10
        b._grace_warned = True
        b._grace_finished = True
        b._grace_extra_rounds = 5
        b._on_bili_auth_recovered()
        ck("恢复后宽限相对轮数归零（不再「第 10/1 轮」）",
           b._grace_round_no == 0)
        ck("恢复后上限告警重新有资格打一次", b._grace_warned is False)
        ck("恢复后可以重新进入静默", b._grace_finished is False
           and b._grace_extra_rounds == 0 and b._silent is True)
    finally:
        botmod._log = old
        try:
            os.remove(path)
        except OSError:
            pass


def test_grace_uses_relative_round():
    src = _src()
    ck("_grace_keep_silent 接收相对轮数 _grace_round_no",
       re.search(r"_grace_keep_silent\(\s*self\._grace_round_no", src) is not None)
    ck("不再把开机累计的 _round 当宽限轮数",
       re.search(r"_grace_keep_silent\(\s*self\._round", src) is None)


# ──────────────────────────────────────────────────────────────
# 3. 面板翻译：不再把 -404 一律说成「UP 主不存在」
# ──────────────────────────────────────────────────────────────

def test_logtranslate_no_false_alarm():
    import logtranslate
    d = logtranslate.explain(
        "[WARNING] (bot.py:2241)_log_check_fail    "
        "[星瞳_Official] 置顶评论获取失败：code=-404 msg=啥都木有")
    ck("置顶评论 -404 归为 info（不是告警卡片）", d["level"] == "info")
    ck("不再说「UP 主不存在或已注销」", "UP 主不存在" not in d["meaning"])
    ck("不再叫用户去核对 UID", "核对 UID" not in d["howto"])
    d2 = logtranslate.explain(
        "[INFO] [星瞳_Official] 该置顶动态没有可取的置顶评论（常态）")
    ck("新常态日志有白话说明", d2["known"] and "正常状态" in d2["meaning"])
    d3 = logtranslate.explain("[WARN] [X] 动态获取失败：code=-404 msg=啥都木有")
    ck("通用 -404 文案不再武断下结论",
       "UP 主不存在" not in d3["meaning"])


def main():
    test_bili_all_404_returns_none()
    test_bili_mixed_errors_still_raise()
    test_bili_success_still_returns_dict()
    test_baseline_no_comment_is_not_an_error()
    test_top_cmt_empty_cooldown()
    test_log_fail_throttle()
    test_grace_release_warns_once()
    test_auth_recovered_resets_grace()
    test_grace_uses_relative_round()
    test_logtranslate_no_false_alarm()
    passed = sum(1 for x in OK if x)
    print(f"\n{passed}/{len(OK)} passed")
    if passed != len(OK):
        sys.exit(1)


if __name__ == "__main__":
    main()
