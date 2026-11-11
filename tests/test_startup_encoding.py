"""启动链：编码容错 + 依赖自检可达性 + 启动脚本分诊。"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "src"))
BAT = os.path.normpath(os.path.join(HERE, "..", "启动机器人.bat"))


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def test_cleanup_no_text_true():
    t = _read(os.path.join(SRC, "cleanup.py"))
    assert "text=True" not in t, "subprocess text=True decodes with GBK locale"
    assert "_decode_out" in t
    assert t.count("_decode_out(r.stdout)") >= 8


def test_decode_out_survives_utf8_continuation_byte():
    sys.path.insert(0, SRC)
    import cleanup
    buf = b'"python.exe","1234","Console"\xe4\xb8\xad\x80'
    s = cleanup._decode_out(buf)
    assert isinstance(s, str)
    assert "1234" in s
    assert cleanup._decode_out(None) == ""
    assert cleanup._decode_out("already str") == "already str"


def test_cfgutil_importable_without_yaml():
    """顶层 import yaml 会让 start.py 在 ensure_deps() 之前就崩。"""
    code = (
        "import sys; sys.modules['yaml'] = None; "
        "sys.path.insert(0, %r); import cfgutil; print('OK')" % SRC
    )
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
    assert "ModuleNotFoundError" not in r.stderr


def test_start_required_covers_yaml():
    t = _read(os.path.join(SRC, "start.py"))
    assert '("yaml", "PyYAML")' in t
    assert "def ensure_deps" in t


def test_bat_is_ascii_and_triage_exit_1():
    raw = open(BAT, "rb").read()
    assert all(b < 128 for b in raw), "bat must be pure ASCII (GBK console)"
    t = raw.decode("ascii")
    assert 'if "%RC%"=="1" goto :NEED_DEPS' in t
    assert ":NEED_DEPS" in t
    assert "-m pip install --user qq-botpy" in t, "NEED_DEPS hint lost"
    assert "v2.3.5" in t
    assert "v2.3.6" not in t
    assert 'if "%RC%"=="2" goto :NEED_CRED' in t
    assert 'if "%RC%"=="9009" goto :NO_PY_RC' in t
    assert "chcp 65001" in t


def test_bat_no_chinese_echo():
    t = open(BAT, "rb").read().decode("ascii")
    for ln in t.splitlines():
        s = ln.strip()
        if s.lower().startswith("echo"):
            assert all(ord(c) < 128 for c in s)


def test_cleanup_real_call_with_utf8_bytes():
    """真跑一次 _pid_alive：子进程输出带 UTF-8 续字节时不能抛异常。"""
    sys.path.insert(0, SRC)
    import cleanup

    class FakeResult:
        stdout = b'"python.exe","1234","Console","1","12,345 K"\xe4\xb8\xad\x80'
        stderr = b""

    orig = cleanup.subprocess.run
    orig_name = cleanup.os.name
    cleanup.subprocess.run = lambda *a, **kw: FakeResult()
    cleanup.os.name = "nt"          # 走 tasklist 分支，与用户机器一致
    try:
        assert cleanup._pid_alive(1234) is True
    finally:
        cleanup.subprocess.run = orig
        cleanup.os.name = orig_name


def test_start_importable_without_yaml():
    """整条 start.py 导入链在缺 PyYAML 时必须能走完，ensure_deps 才可达。"""
    code = (
        "import sys; sys.modules['yaml'] = None; "
        "sys.path.insert(0, %r); import start; print('OK')" % SRC
    )
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
    assert "ModuleNotFoundError" not in r.stderr
