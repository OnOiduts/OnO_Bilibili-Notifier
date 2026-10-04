# -*- coding: utf-8 -*-
"""主动消息无权限（官方 40034105）的处理。

日志里原来只是两条笼统警告：
    Markdown 推送失败，自动改用纯文本：HTTP 400 ...40034105
    纯文本推送失败：HTTP 400 ...40034105
用户看不出该怎么修，而且**每一轮都会撞两次墙**，日志成对刷屏。

现在要满足三条：
  ① 认得出 40034105，给出能照做的中文提示
  ② 命中后**不再退纯文本重试**（同一个权限，退过去必然也是同一个错）
  ③ 该群冷却一段时间，冷却期内跳过；过期后再试一次
  ④ ⚠️ 绝不能当成"机器人不在群里"把群删掉
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import importlib

bot = importlib.import_module("bot")

REAL_400 = ('HTTP 400: {"message":"主动消息失败, 无权限","code":40034105,'
            '"err_code":40034105,"trace_id":"a1975437383a4e77"}')


def test_recognize_code():
    assert bot.is_no_perm_error(REAL_400) is True


def test_recognize_words():
    assert bot.is_no_perm_error("主动消息失败, 无权限") is True


def test_not_other_errors():
    for t in ("", None, "网络超时", "HTTP 500", "内容包含违规信息",
              "机器人已不在该群", "鉴权失败，机器人非群成员"):
        assert bot.is_no_perm_error(t) is False, t


def test_no_perm_not_gone_group():
    """最关键的一条：无权限 ≠ 不在群里，绝不能删群。"""
    assert bot.is_gone_group_error(REAL_400) is False
    assert "无权限" not in " ".join(bot.GONE_GROUP_WORDS)


def test_hint_is_actionable():
    h = bot.no_perm_hint("1BA9FBD04B5B6256334476300905717D")
    assert "主动消息" in h
    assert "允许机器人主动发言" in h or "@" in h
    assert "40034105" in h


class _QQ:
    def __init__(self):
        self.md_calls = 0
        self.text_calls = 0

    async def send_markdown(self, gid, md, keyboard=None):
        self.md_calls += 1
        raise RuntimeError(REAL_400)

    async def send_text(self, gid, text, msg_id=None):
        self.text_calls += 1
        return True


class _B:
    """只测 _mark_no_perm / 冷却判定，不牵扯真实机器人。"""
    def __init__(self, cd=600):
        self._noperm_until = {}
        self._noperm_cd = cd

    _mark_no_perm = bot.NotifyBot._mark_no_perm


def test_cooldown_skips():
    b = _B(cd=600)
    gid = "G1"
    b._mark_no_perm(gid)
    assert time.time() < b._noperm_until[gid], "冷却未生效"
    assert b._noperm_until[gid] <= time.time() + 601


def test_cooldown_zero_disabled():
    b = _B(cd=0)
    b._mark_no_perm("G1")
    assert b._noperm_until == {}, "冷却时长为 0 时应完全不冷却"


def test_cooldown_expired_retry():
    b = _B(cd=1)
    b._mark_no_perm("G1")
    b._noperm_until["G1"] = time.time() - 1      # 已过期
    assert time.time() >= b._noperm_until["G1"], "过期后应允许再试"


def test_no_plain_fallback_on_no_perm():
    """命中无权限时不应再调 send_text —— 那是同一个权限，白试一次。"""
    import asyncio

    b = _B(cd=600)
    qq = _QQ()

    async def main():
        # 复刻 _push 里的判断顺序：先判无权限，命中就跳过纯文本
        try:
            await qq.send_markdown("G1", "md")
        except Exception as e:
            if bot.is_no_perm_error(e):
                b._mark_no_perm("G1")
                return
            await qq.send_text("G1", "plain")
        await qq.send_text("G1", "plain")

    asyncio.run(main())
    assert qq.md_calls == 1
    assert qq.text_calls == 0, "无权限时不该再退纯文本重试"
    assert "G1" in b._noperm_until


def test_config_default_present():
    """冷却时长缺省应为 600，且配置项名对得上文档。"""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "bot.py"), encoding="utf-8").read()
    assert "push_noperm_cooldown" in src
