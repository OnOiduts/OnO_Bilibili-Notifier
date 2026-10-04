# -*- coding: utf-8 -*-
"""日志页「连着出现的同一句」合并成一条。

用户反馈：一次突发里每一步各打一条日志（撞风控放慢 → 解除回最快 →
平稳加快），翻译后说法相同或都在讲"检查频率"，日志页看着就是同一条
消息被重复推送了好几次。collapse_log_items() 负责把这类连发并成一条。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from webui import collapse_log_items  # noqa: E402


def _it(meaning, time="05:01:27", date="2026-09-27", cn="", raw=""):
    return {"meaning": meaning, "time": time, "date": date,
            "cn": cn or meaning, "raw": raw or meaning}


def test_same_meaning_same_second_collapsed():
    """说法相同 + 同一秒 → 并成一条，次数 +1。"""
    out = collapse_log_items([
        _it("撞到 B 站限流，检查频率已临时放慢。", cn="A"),
        _it("撞到 B 站限流，检查频率已临时放慢。", cn="B"),
    ])
    assert len(out) == 1, out
    assert out[0]["repeat"] == 2
    # 具体数值不能丢：两条原文都要在
    assert "A" in out[0]["cn"] and "B" in out[0]["cn"]


def test_same_meaning_different_second_kept():
    """说法相同但不在同一秒 → 那是两次真实变化，各自保留。"""
    out = collapse_log_items([
        _it("运行平稳，检查频率自动加快了一档。", time="05:01:27"),
        _it("运行平稳，检查频率自动加快了一档。", time="05:02:11"),
    ])
    assert len(out) == 2, out


def test_no_meaning_never_collapsed():
    """没有白话解释的条目不参与合并（没法判断是不是同一件事）。"""
    out = collapse_log_items([
        {"meaning": "", "time": "05:01:27", "date": "2026-09-27", "cn": "x"},
        {"meaning": "", "time": "05:01:27", "date": "2026-09-27", "cn": "x"},
    ])
    assert len(out) == 2, out


def test_burst_steps_collapsed_to_final_state():
    """一次突发里的不同步骤（放慢 → 解除）并为一条，说法取最终状态。"""
    out = collapse_log_items([
        _it("撞到 B 站限流，检查频率已临时放慢。", cn="8s → 24s"),
        _it("B 站限流已过去，检查频率已恢复到最快。", cn="24s → 8s"),
    ])
    assert len(out) == 1, out
    assert out[0]["repeat"] == 2
    assert "恢复到最快" in out[0]["meaning"]
    # 每一步的具体数值都留着
    assert "8s → 24s" in out[0]["cn"] and "24s → 8s" in out[0]["cn"]


def test_unrelated_items_kept():
    """不是讲检查频率的，同秒也不合并。"""
    out = collapse_log_items([
        _it("已推送【直播】甲 → 群 A"),
        _it("已推送【投稿】乙 → 群 B"),
    ])
    assert len(out) == 2, out


def test_freq_and_normal_mixed():
    """频率调整后面跟一条普通日志：只合前面的，普通那条照常显示。"""
    out = collapse_log_items([
        _it("撞到 B 站限流，检查频率已临时放慢。"),
        _it("B 站限流已过去，检查频率已恢复到最快。"),
        _it("已推送【直播】甲 → 群 A"),
    ])
    assert len(out) == 2, out
    assert out[0]["repeat"] == 2
    assert out[1]["meaning"].startswith("已推送")


def test_empty():
    assert collapse_log_items([]) == []
    assert collapse_log_items(None) == []


def test_more_flag_set_for_expand():
    """合并过的要标 more，前端据此允许展开看原文。"""
    out = collapse_log_items([
        _it("撞到 B 站限流，检查频率已临时放慢。", cn="A"),
        _it("撞到 B 站限流，检查频率已临时放慢。", cn="B"),
    ])
    assert out[0].get("more") is True


def test_real_burst_of_four():
    """真实场景：同一秒 4 条间隔日志 → 1 条。"""
    out = collapse_log_items([
        _it("撞到 B 站限流，检查频率已临时放慢。", cn="12s → 24s"),
        _it("撞到 B 站限流，检查频率已临时放慢。", cn="24s → 48s"),
        _it("B 站限流已过去，检查频率已恢复到最快。", cn="48s → 8s"),
        _it("运行平稳，检查频率自动加快了一档。", cn="20s → 16s"),
    ])
    assert len(out) == 1, out
    assert out[0]["repeat"] == 4
    for frag in ("12s → 24s", "24s → 48s", "48s → 8s", "20s → 16s"):
        assert frag in out[0]["cn"], frag
