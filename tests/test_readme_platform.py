#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""README 必须写清项目定位：它是给「QQ 开放平台官方机器人」用的程序。

为什么单独立一个测试：本项目放 GitHub 是给外人看的，而"往 QQ 群推消息"
这件事在开源世界里有两个截然不同的实现路线 ——
官方机器人 API（botpy）和第三方协议客户端（go-cqhttp / Mirai 等）。
两者在合规风险、部署方式、能力边界上完全不同。
如果 README 只写"推送到 QQ 群"而不点明走的是官方接口，
读者很容易误以为是后者，进而误判风险与用法。

所以这里锁死：README 必须同时写清「是什么」和「不是什么」。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")
DOCS_README = os.path.join(ROOT, "docs", "README.md")


def _read(p):
    assert os.path.isfile(p), "缺少文件：%s" % p
    return open(p, encoding="utf-8").read()


def test_readme_exists():
    _read(README)


def test_readme_states_official_platform():
    s = _read(README)
    assert "QQ 开放平台" in s, "README 未说明本项目基于 QQ 开放平台"
    assert "q.qq.com" in s, "README 未给出 QQ 开放平台地址"


def test_readme_states_official_sdk():
    s = _read(README)
    assert "botpy" in s, "README 未说明使用官方 SDK qq-botpy"
    assert "官方" in s, "README 未点明走的是官方接口"


def test_readme_denies_third_party_protocol():
    """必须写清「不是什么」——否则读者会按第三方协议的印象去理解它。

    ⚠️ 这里特意要求「点名第三方方案」和「否定说明」出现在**同一行**。
    早先写成全文各查一次（`go-cqhttp in s` 且 `不是 in s`），
    做反向验证时发现：把 go-cqhttp 换成别的词，另一处（文档别处的"不是"）
    照样把断言顶住，测试全绿 —— 等于没守住。
    """
    lines = _read(README).splitlines()
    hit = [ln for ln in lines
           if ("go-cqhttp" in ln or "Mirai" in ln)
           and ("不是" in ln or "非官方" in ln or "不涉及" in ln)]
    assert hit, (
        "README 未在同一处同时点名第三方方案（go-cqhttp / Mirai）并写明「不是」；"
        "读者会按第三方协议的印象去理解本项目"
    )


def test_readme_has_platform_section():
    """章节标题必须是真的标题行（`## ` 开头），不能只是正文里提了一嘴。"""
    s = _read(README)
    titles = [ln for ln in s.splitlines()
              if ln.startswith("#") and "它是基于什么运行的" in ln]
    assert titles, "README 缺少「它是基于什么运行的」章节标题（正文里提到不算）"


def test_readme_has_credential_steps():
    """拿 AppID / AppSecret 的步骤要写清，尤其是「把机器人拉进群」这一步。"""
    s = _read(README)
    assert "AppID" in s and "AppSecret" in s, "README 未说明需要 AppID / AppSecret"
    assert "拉进" in s or "加进群" in s, "README 未说明机器人需要被拉进群"


def test_readme_dependency_table_mentions_botpy():
    s = _read(README)
    assert "qq-botpy" in s, "第三方依赖表里缺少 qq-botpy"


def test_docs_readme_states_platform():
    s = _read(DOCS_README)
    assert "QQ 开放平台" in s, "docs/README.md 未说明基于 QQ 开放平台"
    assert "q.qq.com" in s, "docs/README.md 未给出开放平台地址"


if __name__ == "__main__":
    failed = 0
    for name in sorted(dir()):
        if not name.startswith("test_"):
            continue
        fn = globals()[name]
        try:
            fn()
            print("[PASS] %s" % name)
        except AssertionError as e:
            failed += 1
            print("[FAIL] %s: %s" % (name, e))
    total = len([n for n in dir() if n.startswith("test_")])
    print("-" * 60)
    print("通过 %d / 失败 %d" % (total - failed, failed))
    sys.exit(1 if failed else 0)
