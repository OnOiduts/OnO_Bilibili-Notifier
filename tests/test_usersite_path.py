# -*- coding: utf-8 -*-
"""Store Python: pip --user packages are invisible to every new process.

Symptom (real log): pip prints "Successfully installed ...", then start.py
prints "装完仍缺依赖" and the NEXT launch downloads all 25 packages again.
That proves it is not "the current process started before the dir existed":
a fresh interpreter cannot import them either. The Store build leaves the
user site off sys.path (disabled or AppX-redirected %APPDATA%), while pip
still writes to %APPDATA%\\Python\\PythonXY\\site-packages.

Fix: the launcher exports PYTHONPATH explicitly (inherited by all children),
and start.py mirrors those candidate paths into sys.path before missing_deps.
"""
import ast
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = os.path.join(ROOT, "src", "start.py")
BAT = os.path.join(ROOT, "启动机器人.bat")


def _start():
    return open(START, "r", encoding="utf-8").read()


def _bat_lines():
    return [l.strip() for l in
            open(BAT, "rb").read().decode("ascii").splitlines()]


def test_bat_exports_pythonpath_for_usersite():
    lines = _bat_lines()
    assert any("ONOBN_USERSITE" in l and "Python311" in l for l in lines), \
        "fixed %APPDATA% user site must be set"
    assert any('set "PYTHONPATH=%ONOBN_USERSITE%;' in l for l in lines), \
        "user site must be prepended to PYTHONPATH"
    assert any("site.getusersitepackages()" in l for l in lines), \
        "dynamic user site lookup is the fallback for other machines"
    # must happen BEFORE start.py is spawned
    i_site = next(i for i, l in enumerate(lines) if "ONOBN_USERSITE" in l)
    i_start = next(i for i, l in enumerate(lines)
                   if '"%~dp0src\\start.py"' in l)
    assert i_site < i_start


def test_bat_pythonpath_uses_real_interpreter():
    lines = _bat_lines()
    i = next(i for i, l in enumerate(lines)
             if "site.getusersitepackages()" in l)
    assert '"%PYCMD%" %PYARGS% -c' in lines[i], \
        "probe must use the resolved interpreter, not `python`"


def test_start_refreshes_before_missing_deps():
    s = _start()
    head = s[:s.index("def missing_deps():")]
    assert re.search(r"^_refresh_user_site\(\)\s*$", head, re.M), \
        "must fix sys.path BEFORE the first dependency check"
    assert s.count("def _refresh_user_site()") == 1, \
        "duplicate definition would silently override the fix"


def test_refresh_covers_all_three_candidates():
    s = _start()
    fn = s[s.index("def _refresh_user_site():"):s.index("def _deps_ok_in_fresh_process():")]
    assert 'os.environ.get("APPDATA"' in fn
    assert 'os.environ.get("LOCALAPPDATA"' in fn
    assert "site.getusersitepackages()" in fn
    assert '"_vendor"' in fn
    assert "invalidate_caches()" in fn


def _load_refresh():
    tree = ast.parse(_start())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_refresh_user_site")
    mod = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {"os": os, "sys": sys}
    exec(compile(mod, "start.py", "exec"), ns)
    return ns["_refresh_user_site"]


def test_refresh_actually_makes_a_dep_importable():
    with tempfile.TemporaryDirectory() as td:
        tag = "Python%d%d" % sys.version_info[:2]
        site_dir = os.path.join(td, "Python", tag, "site-packages")
        os.makedirs(site_dir)
        with open(os.path.join(site_dir, "onobn_fake_dep.py"), "w",
                  encoding="utf-8") as f:
            f.write("VALUE = 42\n")
        old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = td
        saved_path = list(sys.path)
        try:
            added = _load_refresh()()
            norm = [os.path.normpath(p) for p in sys.path]
            assert os.path.normpath(site_dir) in norm, \
                "pip --user dir must land on sys.path"
            sys.modules.pop("onobn_fake_dep", None)
            import onobn_fake_dep
            assert onobn_fake_dep.VALUE == 42
            sys.modules.pop("onobn_fake_dep", None)
        finally:
            sys.path[:] = saved_path
            if old is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old


def test_failure_hint_no_longer_says_reinstall():
    s = _start()
    assert "装完仍缺依赖，请手动安装后重试。" not in s
    assert 'set "PYTHONPATH=' in s
    assert "不要反复重装" in s
