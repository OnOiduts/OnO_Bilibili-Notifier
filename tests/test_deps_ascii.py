# -*- coding: utf-8 -*-
"""pip -r files must be ASCII-only, and manual install hints must not use -r.

Root cause: on zh-CN Windows, pip decodes -r files with the system
locale (GBK). A UTF-8 requirements.txt with Chinese comments dies in
pip/_internal/utils/encoding.py -> auto_decode:
  UnicodeDecodeError: 'gbk' codec can't decode byte 0xa7
The automatic install, the launcher hint and 安装依赖.bat all used -r,
so every path failed at once.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _raw(rel):
    with open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _text(rel, enc="utf-8"):
    return _raw(rel).decode(enc, errors="replace")


REQ_FILES = (
    "src/requirements.txt",
    "src/requirements-core.txt",
    "src/requirements-optional.txt",
)


def test_all_requirement_files_are_ascii_and_gbk_safe():
    for rel in REQ_FILES:
        raw = _raw(rel)
        assert all(b < 128 for b in raw), (
            "%s contains non-ASCII bytes; pip -r will fail under GBK" % rel
        )
        raw.decode("gbk")


def test_requirements_txt_keeps_full_list():
    s = _text("src/requirements.txt")
    for pkg in ("qq-botpy", "aiohttp", "PyYAML", "Flask", "playwright"):
        assert re.search(r"^%s" % pkg, s, re.M), "missing %s" % pkg
    assert "==" not in s, "pinned versions conflict with other packages"


def test_start_py_manual_hint_does_not_use_r():
    s = _text("src/start.py")
    body = s[s.index("def ensure_deps():"):s.index("def ensure_config():")]
    assert "仍然失败" in body, "manual hint not found"
    tail = " ".join(body[body.index("仍然失败"):].split())
    assert "-r requirements.txt" not in tail, "manual hint still uses -r"
    assert "pip install --user" in tail
    assert "qq-botpy aiohttp PyYAML Flask playwright" in tail, (
        "manual hint must list all packages explicitly"
    )


def test_start_py_has_user_fallback():
    s = _text("src/start.py")
    body = s[s.index("def ensure_deps():"):s.index("def ensure_config():")]
    assert '"--user"' in body, "no --user fallback for read-only site-packages"
    assert "attempts" in body, "install attempts not chained"


def test_launcher_hint_does_not_use_r():
    s = _text("启动机器人.bat", "ascii")
    i = s.rindex(":NEED_DEPS")
    j = s.rindex(":NEED_CRED")
    seg = s[i:j]
    assert "-r " not in seg, "launcher hint still uses -r"
    assert "pip install --user" in seg


def test_install_bat_resolves_real_python():
    s = _text("安装依赖.bat", "ascii")
    assert "Get-AppxPackage" in s, "install bat still trusts where python"
    assert ":TRY_PY" in s and "--version" in s
    assert '"%PYCMD%" %PYARGS% -m pip install' in s, (
        "pip calls must quote the interpreter path (Store path has spaces)"
    )


def test_launcher_and_install_bat_are_ascii_crlf():
    for rel in ("启动机器人.bat", "安装依赖.bat"):
        raw = _raw(rel)
        assert b"\r\n" in raw, "%s lost CRLF" % rel
        raw.decode("ascii")
