"""启动前弹 "tasklist.exe - Application Error (0xc0000142)" 的回归。

现象：本机 WMI 的 framedyn.dll 损坏后，tasklist / taskkill / wmic
启动即 STATUS_DLL_INIT_FAILED，Windows 弹模态框挡在启动流程前面；
即便点掉，stdout 也是空的 → 残留检查误判「没有残留进程」。

修法：Windows 上的进程存活判定、终止、进程树枚举全部改为
kernel32 API（OpenProcess / GetExitCodeProcess / TerminateProcess /
CreateToolhelp32Snapshot），零外部进程。PowerShell 走 COM 路径，
本机实测正常，保留；netstat/ps/ss 与 framedyn 无关，保留。
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "src"))

import cleanup as C  # noqa: E402

SRC = io.open(os.path.join(HERE, "src", "cleanup.py"), encoding="utf-8").read()


def _ok(cond, msg):
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    return bool(cond)


def _subprocess_targets():
    tree = ast.parse(SRC)
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "run"):
            continue
        if not (n.args and isinstance(n.args[0], ast.List) and n.args[0].elts):
            continue
        first = n.args[0].elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.append(first.value.lower())
    return out


# ---------- 一、源码层：三个 WMI 工具一个都不许再被调用 ----------

def test_no_wmi_tools_invoked():
    print("[1] subprocess.run 不再调用 tasklist / taskkill / wmic")
    ext = _subprocess_targets()
    bad = [x for x in ext if x in ("tasklist", "taskkill", "wmic")]
    return _ok(not bad, "WMI 工具残留 %s（现存外部命令：%s）" % (bad, sorted(set(ext))))


def test_win_helpers_exist():
    print("[2] kernel32 辅助函数齐备")
    need = ("_win_pid_alive", "_win_terminate", "_win_enum_procs",
            "_win_terminate_tree", "_reg_argv")
    miss = [n for n in need if not callable(getattr(C, n, None))]
    return _ok(not miss, "缺少 %s" % miss)


def test_decode_still_used():
    print("[3] 剩余外部命令仍走统一解码（不回退 text=True）")
    return _ok("text=True" not in SRC and "_decode_out" in SRC,
               "text=True 未回潮、_decode_out 仍在")


# ---------- 二、Windows 分支不再起子进程 ----------

def test_alive_set_windows_uses_api():
    print("[4] Windows 上 _alive_set 走 OpenProcess，不碰 tasklist")
    orig_name, orig_alive = C.os.name, C._win_pid_alive
    calls = []
    try:
        C.os.name = "nt"
        C._win_pid_alive = lambda p: calls.append(p) or (p in (11, 33))
        got = C._alive_set([11, 22, 33, 44])
        r = _ok(got == {11, 33}, "只留 11/33（实际 %s）" % sorted(got))
        r &= _ok(sorted(calls) == [11, 22, 33, 44],
                 "逐个 API 判定，无子进程（调用 %s）" % calls)
        return r
    finally:
        C.os.name, C._win_pid_alive = orig_name, orig_alive


def test_pid_alive_windows_uses_api():
    print("[5] Windows 上 _pid_alive 走 OpenProcess")
    orig_name, orig_alive = C.os.name, C._win_pid_alive
    try:
        C.os.name = "nt"
        C._win_pid_alive = lambda p: p == 77
        r = _ok(C._pid_alive(77) is True, "77 判活")
        r &= _ok(C._pid_alive(78) is False, "78 判死")
        return r
    finally:
        C.os.name, C._win_pid_alive = orig_name, orig_alive


def test_kill_windows_uses_api():
    print("[6] Windows 上 kill_ours 走 TerminateProcess，不碰 taskkill")
    orig = (C.os.name, C.all_ours, C._win_pid_alive,
            C._win_terminate_tree, C._win_terminate,
            C.pids_on_ports, C._pids_from_file)
    trees, singles, spawned = [], [], []
    try:
        C.os.name = "nt"
        C.all_ours = lambda: [501, 502]
        # 501 没杀掉（仍活着），502 杀掉了
        C._win_pid_alive = lambda p: p == 501
        C._win_terminate_tree = lambda p: trees.append(p)
        C._win_terminate = lambda p: singles.append(p)
        C.pids_on_ports = lambda: []
        C._pids_from_file = lambda: []
        import subprocess as sp
        real_run = sp.run
        sp.run = lambda *a, **k: spawned.append(a[0]) or real_run(*a, **k)
        try:
            n = C.kill_ours()
        finally:
            sp.run = real_run
        r = _ok(sorted(trees) == [501, 502], "进程树终止 501/502（实际 %s）" % trees)
        r &= _ok(singles == [501], "补刀只针对仍活着的 501（实际 %s）" % singles)
        r &= _ok(not spawned, "一次外部命令都没起（实际 %s）" % spawned)
        r &= _ok(n == 1, "只确认关掉 1 个（实际 %d）" % n)
        return r
    finally:
        (C.os.name, C.all_ours, C._win_pid_alive,
         C._win_terminate_tree, C._win_terminate,
         C.pids_on_ports, C._pids_from_file) = orig


# ---------- 三、进程树与纯 Python 兜底 ----------

def test_terminate_tree_child_first():
    print("[7] 进程树先子后父（替代 taskkill /T）")
    orig = (C._win_enum_procs, C._win_terminate)
    order = []
    try:
        C._win_enum_procs = lambda: {
            100: (1, "python.exe"),
            101: (100, "python.exe"),
            102: (101, "python.exe"),
            999: (1, "other.exe"),
        }
        C._win_terminate = lambda p: order.append(p)
        C._win_terminate_tree(100)
        return _ok(order == [102, 101, 100],
                   "顺序 102→101→100 且不碰 999（实际 %s）" % order)
    finally:
        C._win_enum_procs, C._win_terminate = orig


def test_wmic_fallback_is_pure_python():
    print("[8] PowerShell 失败后的兜底只认「解释器名对 + 已登记」")
    orig = (C._NT, C._win_enum_procs, C.registry_pids, C._pids_from_file,
            C.pids_on_ports, C._reg_argv, C.is_ours, C._proc_names)
    try:
        C._NT = True
        C._win_enum_procs = lambda: {
            201: (1, "python.exe"),      # 已登记、名字对 → 认
            202: (1, "python.exe"),      # 没登记 → 不认
            203: (1, "other.exe"),       # 名字不对 → 不认
        }
        C.registry_pids = lambda: [201]
        C._pids_from_file = lambda: []
        C.pids_on_ports = lambda: []
        C._reg_argv = lambda p: "start.py"
        C.is_ours = lambda c: "start.py" in c
        C._proc_names = lambda: ["python.exe", "pythonw.exe"]
        got = C._list_ps_wmic()
        return _ok([p for p, _ in got] == [201],
                   "只返回 201（实际 %s）" % got)
    finally:
        (C._NT, C._win_enum_procs, C.registry_pids, C._pids_from_file,
         C.pids_on_ports, C._reg_argv, C.is_ours, C._proc_names) = orig


def test_fallback_still_called_by_list_ours():
    print("[9] list_ours 仍接得上这条兜底")
    return _ok("out = _list_ps_wmic()" in SRC,
               "list_ours 里仍调用 _list_ps_wmic")


def main():
    print("=" * 60)
    print("0xc0000142 弹窗：WMI 外部命令清零")
    print("=" * 60)
    rs = [
        test_no_wmi_tools_invoked(),
        test_win_helpers_exist(),
        test_decode_still_used(),
        test_alive_set_windows_uses_api(),
        test_pid_alive_windows_uses_api(),
        test_kill_windows_uses_api(),
        test_terminate_tree_child_first(),
        test_wmic_fallback_is_pure_python(),
        test_fallback_still_called_by_list_ours(),
    ]
    bad = rs.count(False)
    print("-" * 60)
    print("通过 %d / %d" % (len(rs) - bad, len(rs)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
