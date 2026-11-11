# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""守住两条用户定的规矩：解压后目录名固定、压缩包名带方括号版本号。

为什么单独测：
    这两条都是「改一次就悄悄退化」的类型——顶层目录名跟着源码目录走是
    最自然的写法（os.path.relpath 的默认值），一不留神就回到 bili-notify；
    版本号规则写在 README 里，改表格时也很容易漏掉严重修复那一行。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import version as V  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TOPDIR = "OnO_Bilibili-Notifier"


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_topdir_is_fixed():
    """解压后的顶层目录固定叫 OnO_Bilibili-Notifier，不跟源码目录名走。"""
    assert V.PACK_TOPDIR == TOPDIR, (
        "顶层目录成了 %r，应为 %r（固定名才能整目录覆盖解压）" % (V.PACK_TOPDIR, TOPDIR))


def test_name_fmt_has_bracket_version():
    """压缩包名是 OnO_Bilibili-Notifier[v2.3.5].zip 这种方括号写法。"""
    v = _read("src/VERSION").strip()
    name = V.PACK_NAME_FMT % (v, "")
    assert name == "OnO_Bilibili-Notifier[v%s].zip" % v, name
    gh = V.PACK_NAME_FMT % (v, "_github")
    assert "[" in gh and "]" in gh, "版本号得用方括号包住：%s" % gh


def _cur_version():
    """当前版本号（README 里的严重修复举例要跟它一致）。"""
    return _read("src/VERSION").strip()


def _severe_row(fname):
    s = _read(fname)
    m = re.search(r"\| 严重 bug 修复 \|(.+?)\|", s)
    assert m, "%s 里找不到「严重 bug 修复」那一行" % fname
    return m.group(1)


def test_readme_rules_severe_fix():
    """README 版本号规则表：严重 bug 修复 = 版本号不涨，只加 fix 后缀。

    ⚠️ 这条规则改过一次：最初是「+0.1.0 且带 fix」（v2.3.4 → v2.4.0fix），
    后来改成「三位数字不动，只加 fix」（v2.3.5 → v2.3.5fix）—— 修 bug
    不该让人以为出了新功能。断言随之改成守「不涨」而不是守「+0.1.0」。
    """
    row = _severe_row("README.md")
    assert "fix" in row, "严重修复版本号要带 fix 后缀，现在是：%s" % row.strip()
    assert "+0.1.0" not in row, \
        "严重修复不该再涨次版本号（已改成不涨号），现在是：%s" % row.strip()
    assert "不涨" in row or "不动" in row, \
        "严重修复要写明「版本号不涨」，现在是：%s" % row.strip()


def test_docs_readme_in_sync():
    """docs/README.md 与根 README 的规则保持一致。"""
    row = _severe_row("docs/README.md")
    assert "fix" in row, row.strip()
    assert "+0.1.0" not in row, \
        "docs/README 的严重修复不该再涨次版本号，现在是：%s" % row.strip()
    assert "不涨" in row or "不动" in row, row.strip()


def test_version_docstring_rules():
    """version.py 顶部的规则注释也要跟上（改表格容易漏掉注释）。"""
    s = _read("src/version.py")
    assert "严重修复" in s and "fix" in s, \
        "version.py 规则注释里没有严重修复的说明"
    assert "不涨" in s, \
        "version.py 规则注释没写明严重修复「版本号不涨」"


def test_severe_fix_example_present():
    """规则要给出举例，否则「不涨号只加 fix」容易被理解成别的算法。

    ⚠️ 举例必须跟当前版本一致：版本一涨，文档里的旧举例就过期了
    （test_release_rule_dedup 也在盯着这一点）。
    """
    v = _cur_version()
    s = _read("README.md")
    assert re.search(re.escape("v%s" % v) + r".*v" + re.escape(v) + r"fix", s, re.S), \
        "README 缺与当前版本一致的举例（v%s → v%sfix）" % (v, v)


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("  \u2713 %s" % name)
        except AssertionError as e:
            bad += 1
            print("  \u2717 %s: %s" % (name, e))
    print("%d passed, %d failed" % (len(fns) - bad, bad))
    sys.exit(1 if bad else 0)
