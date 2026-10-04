#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""维护政策必须写在文档里，且面板「关于」页也要能看到。

为什么单独立一个测试：v2.2.3 定下「此后只做功能更新和严重 bug 修复」
这条规矩，它的价值全在于**对外可见** —— 写在源码注释里没人看得到，
潜在的 Issue 提交者也不知道什么样的反馈才会被处理。
所以三处（根目录 README / docs/README / 使用说明）都必须有，
关于页的 notes 里也必须有一条，否则等于没立。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 政策里必须出现的核心措辞
KEY_HEAD = "维护政策"
KEY_FEATURE = "功能更新"
KEY_BUG = "严重 bug 修复"
KEY_WONTFIX = "wontfix"

ROOT_README = os.path.join(ROOT, "README.md")
DOCS_README = os.path.join(ROOT, "docs", "README.md")
USAGE = os.path.join(ROOT, "docs", "使用说明.md")


def _read(p):
    assert os.path.isfile(p), "缺少文件：%s" % p
    return open(p, encoding="utf-8").read()


def _has_section(s):
    return re.search(r"^##\s*%s\s*$" % KEY_HEAD, s, re.M) is not None


def test_root_readme_has_policy():
    """根目录 README 必须有「## 维护政策」章节 —— GitHub 主页渲染的正是这份。"""
    s = _read(ROOT_README)
    assert _has_section(s), "README.md 缺少「## 维护政策」章节"


def test_docs_readme_has_policy():
    """docs/README.md 也要有 —— 它是仓库文档区的入口。"""
    s = _read(DOCS_README)
    assert _has_section(s), "docs/README.md 缺少「## 维护政策」章节"


def test_usage_has_policy():
    """使用说明的「反馈 bug」章节要说明什么反馈才会被处理。"""
    s = _read(USAGE)
    assert KEY_HEAD in s or KEY_FEATURE in s, \
        "docs/使用说明.md 未提及维护政策（用户不知道什么反馈才会被处理）"


def test_policy_says_two_categories():
    """政策正文必须同时点明两类改动：功能更新、严重 bug 修复。"""
    s = _read(ROOT_README)
    assert KEY_FEATURE in s, "维护政策未写明「%s」这一类" % KEY_FEATURE
    assert KEY_BUG in s, "维护政策未写明「%s」这一类" % KEY_BUG


def test_policy_lists_wontfix():
    """必须列清楚哪些不再改 —— 只说「只改两类」不够，得点名 wontfix 范围。

    否则用户仍然会提「建议优化一下措辞」这类 Issue，然后被关闭时感到困惑。
    """
    s = _read(ROOT_README)
    for kw in ("措辞", "观感", "代码风格", "性能优化"):
        assert kw in s, "维护政策未点名不再处理「%s」类改动" % kw
    assert KEY_WONTFIX in s, \
        "维护政策未说明这类建议会被标记为 wontfix（用户会不知道为何被关闭）"


def test_about_page_mentions_policy():
    """面板「关于」页必须显示维护状态 —— 反馈时一键复制的版本信息里要有。

    这里不启 Flask，只做源码级检查：notes 列表里得有一条提到维护状态。
    """
    src = _read(os.path.join(ROOT, "src", "webui.py"))
    block = src
    assert "维护状态" in block, \
        "src/webui.py 的 /api/about notes 未提及维护状态"
    # 维护状态那条必须跟 README 口径一致：两条都要提到
    idx = block.index("维护状态")
    seg = block[idx:idx + 400]
    assert KEY_FEATURE in seg or "功能" in seg, \
        "关于页的维护状态条目未说明「功能更新」这一类"
