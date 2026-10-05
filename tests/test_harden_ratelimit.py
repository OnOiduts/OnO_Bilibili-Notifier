# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""限流：读请求不得消耗写额度。

症状：面板开着轮询一阵子之后，用户**第一次点按钮**就被拒，
提示「操作太快了，歇几秒再试」——明明才点了一下。

根因：读和写共用一个计数列表。读请求按读额度(300)放行，但同时被
算进了写额度(120)的计数；轮询把计数顶过 120 之后，所有写操作全被拒。

⚠️ 反向验证已做：把 allow() 改回共用单列表后，本文件第 1、3 条断言
   立刻失败（读到 200 次后第一次写被拒）。改回后必须全绿。
"""
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import harden  # noqa: E402


@pytest.fixture()
def guard():
    return harden.Guard()


def test_read_does_not_consume_write_budget(guard):
    """核心：读了 200 次之后，第一次写仍然放行。"""
    # ⚠️ 必须用**非本机** IP 来验：本机额度已放宽到 600，读 200 次也不会超，
    #    那样即使退化回"共用记账"这条断言也照样是绿的（第一版就漏了这个，
    #    反向验证时它没变红）。用对外 IP（写额度 120）才能真正卡住分账逻辑。
    for _ in range(200):
        assert guard.allow("198.51.100.7", False, path="/api/about") is True
    assert guard.allow("198.51.100.7", True, path="/api/lock") is True


def test_read_and_write_are_separate_buckets(guard):
    """读写的计数必须分开存，不能再是一个混在一起的列表。"""
    guard.allow("127.0.0.1", False, path="/api/about")
    guard.allow("127.0.0.1", True, path="/api/lock")
    b = guard._hits["127.0.0.1"]
    assert isinstance(b, dict), "计数必须按 r/w 分开存"
    assert len(b["r"]) == 1 and len(b["w"]) == 1


def test_poll_paths_are_free(guard):
    """轮询接口不计费，页面多开几个标签页也不能把用户自己挡在门外。"""
    assert "/api/runstate" in harden.POLL_PATHS, "/api/runstate 是轮询接口，必须白名单"
    for _ in range(500):
        assert guard.allow("127.0.0.1", False, path="/api/runstate") is True
    assert guard.allow("127.0.0.1", True, path="/api/lock") is True


def test_loopback_write_limit_is_generous(guard):
    """本机额度远高于对外额度：面板只监听 127.0.0.1，能访问的只有自己。"""
    assert harden.LOOPBACK_WRITE_MAX > harden.WRITE_MAX
    for _ in range(harden.WRITE_MAX + 5):
        assert guard.allow("127.0.0.1", True, path="/api/lock") is True


def test_remote_ip_still_limited(guard):
    """对外仍按原阈值限流，不能因为放宽本机就把防护拆了。"""
    ok = 0
    for _ in range(harden.WRITE_MAX + 10):
        if guard.allow("203.0.113.9", True, path="/api/lock"):
            ok += 1
    assert ok <= harden.WRITE_MAX


def test_remote_ip_gets_strikes(guard):
    """超限后要累计 strike，达到阈值才封——本机则永不封。"""
    for _ in range(harden.WRITE_MAX + 1):
        guard.allow("203.0.113.9", True, path="/api/lock")
    assert guard._strikes.get("203.0.113.9", 0) >= 1
    assert guard.is_loopback("127.0.0.1") is True
    assert guard.should_ban("127.0.0.1") is False
