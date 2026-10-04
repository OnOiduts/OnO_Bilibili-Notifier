#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LICENSE 必须存在、必须署名「波萝Buono」，且必须被两个打包脚本收进包里。

为什么单独立一个测试：v2.0.6 之前 LICENSE 一直在源码目录里躺着，
但两个打包脚本的 KEEP_FILES 都没列它 —— 结果用户拿到的压缩包里
根本没有许可文件，放 GitHub 会显示「No license」。
署名义务又是本协议的核心条款，漏了等于协议白写。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTHOR = "波萝Buono"
GH = "XxBoLuoxX"


def test_license_exists():
    p = os.path.join(ROOT, "LICENSE")
    assert os.path.isfile(p), "项目根目录缺少 LICENSE 文件"
    assert os.path.getsize(p) > 1000, "LICENSE 内容过短，像是写残了"


def test_license_has_author():
    """版权人必须写在 LICENSE 的版权行里 —— 这是 MIT 唯一要求的署名位置。

    v2.1.3 起 LICENSE 回归正统 MIT，GitHub 链接挪去 README，
    所以这里只查版权行，不再要求 LICENSE 里出现仓库地址。
    """
    s = open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read()
    assert AUTHOR in s, "LICENSE 未署名作者「%s」" % AUTHOR
    assert re.search(r"Copyright \(c\)[^\n]*%s" % AUTHOR, s), \
        "LICENSE 的版权行（Copyright (c) ...）里没有作者名"


def test_license_no_supplement():
    """v2.1.3 起：LICENSE 里只有 MIT 本身，不许再挂「补充说明」。

    署名、AI 生成、测试范围这些属于背景信息，全部搬到 README。
    夹在许可证文件里不但不属于许可条款，还会干扰 GitHub 的自动识别。
    """
    s = open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read()
    for kw in ("关于本项目", "补充说明", "腾讯元宝", "Hy4 preview",
               "服务器端从未测试", "安全审计", "波萝Buono（GitHub"):
        assert kw not in s, "LICENSE 里仍挂着补充内容（%s），应放进 README" % kw


def test_license_bilingual():
    """LICENSE 是中英双语：英文原文在前，中文译文在后，并声明以英文为准。"""
    s = open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read()
    assert "MIT License" in s and "MIT 许可证" in s, "LICENSE 不是中英双语"
    assert "特此免费授予" in s, "LICENSE 缺少中文译文"
    assert "以英文原文为准" in s, "LICENSE 未声明中英不一致时以英文为准"


def test_readme_has_ai_note():
    """AI 生成来源从 LICENSE 搬到了 README。"""
    s = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert "腾讯元宝" in s and "Hy4 preview" in s, "README 未注明 AI 生成来源"


def test_readme_has_github():
    """GitHub 来源地址从 LICENSE 搬到了 README。"""
    s = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert GH in s, "README 未给出 GitHub 来源 %s" % GH


def test_license_is_mit():
    """v2.1.1 起许可证由自定义协议改为标准 MIT。

    MIT 的关键特征：允许「无限制地处理本软件」，唯一条件是保留版权与许可声明。
    这两句话是 GitHub 自动识别 MIT 的依据，缺了就会显示成 Unknown。
    """
    s = open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read()
    assert "MIT License" in s, "LICENSE 标题不是 MIT License"
    assert "Permission is hereby granted, free of charge" in s, \
        "LICENSE 缺少 MIT 的授权条款原文"
    assert "WITHOUT WARRANTY OF ANY KIND" in s, "LICENSE 缺少 MIT 的免责条款原文"


def test_license_no_extra_restriction():
    """MIT 不应夹带额外限制（例如禁止出售）——那会让 GitHub 识别成 Unknown，
    且会让「MIT」这个说法名不副实。署名与来源说明只能以非约束性补充的形式出现。"""
    s = open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read()
    # v2.1.3 起 LICENSE 是纯 MIT（英文正文 + 中文译文），全文都不该有限制性措辞
    for word in ("禁止", "不得", "未经许可"):
        assert word not in s, "MIT 里夹带了额外限制条款（%s）" % word


def test_license_no_fake_url():
    """不能留下 space.bilibili.com/xxx 这类占位假链接。"""
    s = open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read()
    assert "space.bilibili.com/xxx" not in s, "LICENSE 里有占位假链接"


def test_pack_scripts_include_license():
    """打包脚本必须把 LICENSE 列进 KEEP_FILES（v2.0.6 之前漏了，包里根本没有）。

    注：v2.1.0 起不再出混淆版，make_zip_obf.py 已删除，这里只查 make_zip.py。
    """
    p = os.path.join(ROOT, "tools", "make_zip.py")
    s = open(p, encoding="utf-8").read()
    assert '"LICENSE"' in s, "make_zip.py 的 KEEP_FILES 没包含 LICENSE"


def test_no_obf_pack_script():
    """v2.1.0 起只出源码 GitHub 版 + 成品版，不再出混淆版。"""
    assert not os.path.exists(os.path.join(ROOT, "tools", "make_zip_obf.py")), \
        "make_zip_obf.py 应该已经删除（不再出混淆版）"


def test_readme_has_author():
    s = open(os.path.join(ROOT, "docs", "README.md"), encoding="utf-8").read()
    assert AUTHOR in s, "README 未署名作者 %s" % AUTHOR
    assert GH in s, "README 未给出 GitHub 来源"


def test_about_api_has_author():
    s = open(os.path.join(ROOT, "src", "webui.py"), encoding="utf-8").read()
    assert '"author": "%s"' % AUTHOR in s, "关于页接口未返回作者"
    assert GH in s, "关于页接口未返回 GitHub 地址"


def test_about_page_shows_author():
    p = os.path.join(ROOT, "src", "templates", "index.html")
    s = open(p, encoding="utf-8").read()
    assert "作者 / 来源：<b>%s</b>" % AUTHOR in s, "关于页未显示作者"


def test_about_copy_includes_author():
    s = open(os.path.join(ROOT, "src", "static", "app.js"), encoding="utf-8").read()
    assert "'作者：'" in s, "「复制版本信息」未带上作者署名"


if __name__ == "__main__":
    fails = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_") and callable(v):
            try:
                v()
                print("  PASS %s" % k)
            except AssertionError as e:
                fails += 1
                print("  FAIL %s: %s" % (k, e))
    print("LICENSE 与署名：%d 项，失败 %d" % (
        len([k for k in globals() if k.startswith("test_")]), fails))
    sys.exit(1 if fails else 0)
