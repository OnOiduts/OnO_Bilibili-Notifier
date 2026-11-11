# -*- coding: utf-8 -*-
"""pip --user first install: the current process cannot see the new site dir.

Symptom: pip prints "Successfully installed ..." and start.py immediately
prints "装完仍缺依赖，请手动安装后重试" and exits. Root cause: Python's
site.py adds the user site to sys.path at startup only, and only if the
directory exists at that moment. pip creates it later in the same run.
"""
import ast
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = os.path.join(ROOT, "src", "start.py")
BAT = os.path.join(ROOT, "启动机器人.bat")


def _start():
    return open(START, "r", encoding="utf-8").read()


def _ensure_body():
    s = _start()
    return s[s.index("def ensure_deps():"):s.index("def ensure_config():")]


def test_helpers_exist():
    s = _start()
    assert "def _refresh_user_site()" in s
    assert "def _deps_ok_in_fresh_process()" in s
    assert "site.getusersitepackages()" in s
    assert "importlib.invalidate_caches()" in s


def test_pip_success_refreshes_before_recheck():
    b = _ensure_body()
    assert "    _refresh_user_site()\n    if missing_deps():" in b, \
        "refresh must run immediately before the recheck"
    assert "安装完成（已在新进程验证）" in b
    assert "_deps_ok_in_fresh_process()" in b


def test_fresh_process_probe_is_real_subprocess():
    s = _start()
    fn = s[s.index("def _deps_ok_in_fresh_process():"):s.index("def ensure_deps():")]
    assert "subprocess.run" in fn
    assert "sys.executable" in fn
    assert "import " in fn


def test_dependency_failure_uses_exit_code_4():
    s = _start()
    assert "return 4" in _ensure_body()
    assert "dep_rc = ensure_deps()" in s
    assert "return int(dep_rc) if dep_rc else 4" in s
    # 2 is reserved for credentials; must never be reused for deps
    assert 'return 2                        # 桌面版据此判断失败' not in s


def test_bat_triages_exit_4_away_from_credentials():
    t = open(BAT, "rb").read().decode("ascii")
    lines = [l.strip() for l in t.splitlines()]
    assert 'if "%RC%"=="4" goto :DEPS_FAILED' in lines
    # Label must exist on its own line. A bare substring check once passed
    # while the label had been swallowed into "goto :DEPS_FAILED".
    assert lines.count(":DEPS_FAILED") == 1, "label missing or duplicated"
    assert "goto :OTHER_FAIL" in lines, "unclassified goto was destroyed"
    assert not any(l == "goto :DEPS_FAILED" for l in lines)
    i = lines.index(":DEPS_FAILED")
    j = lines.index(":OTHER_FAIL")
    seg = "\n".join(lines[i:j])
    assert "NOT an AppID / AppSecret problem" in seg
    assert "pip install --user qq-botpy" in seg
    assert "goto :NEED_CRED" not in seg
    assert "goto :KEEP_PANEL" in seg


def test_bat_keeps_existing_triage():
    t = open(BAT, "rb").read().decode("ascii")
    for line in ('if "%RC%"=="1" goto :NEED_DEPS',
                 'if "%RC%"=="2" goto :NEED_CRED',
                 'if "%RC%"=="9009" goto :NO_PY_RC'):
        assert line in t


def test_refresh_actually_makes_a_new_module_importable():
    """Real behaviour: extract the helper, point site at a fresh dir, import."""
    s = _start()
    tree = ast.parse(s)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_refresh_user_site")
    ns = {"os": os, "sys": sys}
    mod = ast.Module(body=[fn], type_ignores=[])
    exec(compile(mod, "<_refresh_user_site>", "exec"), ns)
    refresh = ns["_refresh_user_site"]

    tmp = tempfile.mkdtemp(prefix="onobn_usersite_")
    with open(os.path.join(tmp, "_onobn_probe_mod.py"), "w") as f:
        f.write("VALUE = 1\n")
    import site
    orig = site.getusersitepackages
    site.getusersitepackages = lambda: tmp
    sys.modules.pop("_onobn_probe_mod", None)
    try:
        try:
            import _onobn_probe_mod  # noqa
            raise AssertionError("module must NOT be importable before refresh")
        except ImportError:
            pass
        added = refresh()
        assert tmp in added, "user site was not added to sys.path"
        assert tmp in sys.path
        import _onobn_probe_mod  # noqa
        assert _onobn_probe_mod.VALUE == 1
    finally:
        site.getusersitepackages = orig
        if tmp in sys.path:
            sys.path.remove(tmp)
        sys.modules.pop("_onobn_probe_mod", None)
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_tmp_junk_in_src():
    junk = [f for f in os.listdir(os.path.join(ROOT, "src"))
            if f.startswith(".tmp-")]
    assert not junk, "stray temp files in package: %r" % junk
