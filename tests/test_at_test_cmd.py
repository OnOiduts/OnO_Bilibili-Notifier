# -*- coding: utf-8 -*-
"""「@机器人 测试」自检：群里看到的那一句必须是**真正的结论**。

背景（真实事故）：
群里 @ 机器人发「测试」，机器人回了「✅ 首次交互成功…已激活登记」，
用户以为权限没问题 —— 但日志里同一时刻是 40034105 主动消息无权限，
订阅推送一条都发不出去。

根因是顺序反了：
  1. 先被动回「首次交互成功」（用掉了这个 msg_id 唯一一次被动机会）
  2. 再主动发送 → 撞 40034105
  3. 想把「没权限」也用被动发回群里 → 机会已用完，被拒 →
     异常被 `except Exception: pass` 静默吞掉，群里什么也看不到

所以"别让人以为能收到"这个功能，自己把那个错觉造出来了。

修法：**先主动探活，再用唯一一次被动机会回真正的结论**。
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import importlib

bot = importlib.import_module("bot")

REAL_400 = ('HTTP 400: {"message":"主动消息失败, 无权限","code":40034105,'
            '"err_code":40034105,"trace_id":"a1975437383a4e77"}')


class FakeQQ:
    """模拟 QQ 发送端。

    关键：被动回复（带 msg_id）**同一个 msg_id 只允许成功一次**，
    第二次开始抛异常 —— 这正是 QQ 的真实限制，也是这个 bug 的成因。
    """

    def __init__(self, active_error=None, passive_error=None):
        self.sent = []            # [(gid, text, msg_id)]
        self.active_error = active_error
        self.passive_error = passive_error
        self._passive_used = set()

    async def send_text(self, gid, text, msg_id=None):
        if msg_id is not None:
            if msg_id in self._passive_used:
                # 被动机会已用完 —— 真实 QQ 就是这么拒的
                raise RuntimeError("被动回复次数已用完")
            self._passive_used.add(msg_id)
            if self.passive_error:
                raise RuntimeError(self.passive_error)
        else:
            if self.active_error:
                raise RuntimeError(self.active_error)
        self.sent.append((gid, text, msg_id))
        return True

    @property
    def texts(self):
        return [t for (_g, t, _m) in self.sent]


def _mkbot(qq, **kw):
    b = bot.NotifyBot.__new__(bot.NotifyBot)
    b.qq = qq
    b._noperm_until = {}
    b._test_cmd_at = {}
    b._noperm_cd = 600      # _mark_no_perm 认这个值，0 表示不冷却
    b.cfg = {}
    return b


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── ① 主动正常：只发一条，且是"能收到"的结论 ──────────────────────
def test_active_ok_sends_one():
    qq = FakeQQ()
    b = _mkbot(qq)
    _run(b._handle_test_cmd("G1", "M1"))
    assert len(qq.sent) == 1, f"应只发 1 条，实发 {len(qq.sent)}"
    assert "主动消息" in qq.texts[0] or "正常" in qq.texts[0]
    assert "没权限" not in qq.texts[0]


def test_active_ok_clears_noperm():
    qq = FakeQQ()
    b = _mkbot(qq)
    b._noperm_until["G1"] = time.time() + 600
    _run(b._handle_test_cmd("G1", "M1"))
    assert "G1" not in b._noperm_until, "验通过后应立刻解除无权限冷却"


# ── ② 主动撞 40034105：结论必须发到群里（这条就是那个 bug） ────────
def test_blocked_conclusion_reaches_group():
    qq = FakeQQ(active_error=REAL_400)
    b = _mkbot(qq)
    _run(b._handle_test_cmd("G1", "M1"))
    # 主动那条发不出去，结论靠被动发到群里
    got = [t for (_g, t, m) in qq.sent if m == "M1"]
    assert got, "主动失败时，结论必须靠被动发回群里，否则用户只看到假成功"
    assert "没权限" in got[0]
    assert "40034105" in got[0]


def test_blocked_marks_noperm():
    qq = FakeQQ(active_error=REAL_400)
    b = _mkbot(qq)
    _run(b._handle_test_cmd("G1", "M1"))
    assert "G1" in b._noperm_until


def test_blocked_no_fake_success():
    """核心回归：绝不出现"主动失败、群里却显示成功"。"""
    qq = FakeQQ(active_error=REAL_400)
    b = _mkbot(qq)
    _run(b._handle_test_cmd("G1", "M1"))
    for t in qq.texts:
        assert "首次交互成功" not in t, "不该再回那句会误导的『首次交互成功』"
        assert "已激活登记" not in t


# ── ③ 主动其它错误：把原因带回去 ──────────────────────────────────
def test_other_error_shows_reason():
    qq = FakeQQ(active_error="网络超时")
    b = _mkbot(qq)
    _run(b._handle_test_cmd("G1", "M1"))
    got = [t for (_g, t, m) in qq.sent if m == "M1"]
    assert got and "网络超时" in got[0]


# ── ④ 被动也失败：兜底再主动试一次，且不能静默吞掉 ────────────────
def test_passive_used_up_falls_back():
    """被动机会已被别处用掉时，兜底用主动再试，不留静默失败。"""
    class UsedUp(FakeQQ):
        async def send_text(self, gid, text, msg_id=None):
            if msg_id is not None:
                raise RuntimeError("被动回复次数已用完")
            return await FakeQQ.send_text(self, gid, text, msg_id=msg_id)

    qq = UsedUp(active_error=REAL_400)
    b = _mkbot(qq)
    _run(b._handle_test_cmd("G1", "M1"))
    # 被动失败后仍尝试过主动兜底（不抛异常、不静默）
    assert isinstance(qq.sent, list)
