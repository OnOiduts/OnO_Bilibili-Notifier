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
    """压缩包名是 OnO_Bilibili-Notifier[v2.3.4].zip 这种方括号写法。"""
    name = V.PACK_NAME_FMT % ("2.3.4", "")
    assert name == "OnO_Bilibili-Notifier[v2.3.4].zip", name
    gh = V.PACK_NAME_FMT % ("2.3.4", "_github")
    assert "[" in gh and "]" in gh, "版本号得用方括号包住：%s" % gh


def test_readme_rules_severe_fix():
    """README 版本号规则表：严重 bug 修复 = +0.1.0 且带 fix 后缀。"""
    s = _read("README.md")
    m = re.search(r"\| 严重 bug 修复 \|(.+?)\|", s)
    assert m, "README 里找不到「严重 bug 修复」那一行"
    row = m.group(1)
    assert "+0.1.0" in row, "严重修复应为 +0.1.0，现在是：%s" % row.strip()
    assert "fix" in row, "严重修复版本号要带 fix 后缀，现在是：%s" % row.strip()


def test_docs_readme_in_sync():
    """docs/README.md 与根 README 的规则保持一致。"""
    s = _read("docs/README.md")
    m = re.search(r"\| 严重 bug 修复 \|(.+?)\|", s)
    assert m, "docs/README.md 里找不到「严重 bug 修复」那一行"
    row = m.group(1)
    assert "+0.1.0" in row and "fix" in row, row.strip()


def test_version_docstring_rules():
    """version.py 顶部的规则注释也要跟上（改表格容易漏掉注释）。"""
    s = _read("src/version.py")
    assert "+0.1.0" in s and "fix" in s, \
        "version.py 规则注释里没有「+0.1.0 + fix」的严重修复说明"


def test_severe_fix_example_present():
    """规则要给出举例，否则「+0.1.0 再加 fix」容易被理解成别的算法。"""
    s = _read("README.md")
    assert re.search(r"v2\.3\.4.*v2\.4\.0fix", s, re.S), \
        "README 缺举例（v2.3.4 → v2.4.0fix）"


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
