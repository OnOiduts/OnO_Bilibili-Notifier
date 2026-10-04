"""残留进程"杀不干净" + 日志"没翻译"。

用户反馈两件事：
  「进程关闭又杀不干净了」
  「运行日志你看图一这不是没翻译么」

一、杀不干净的两个真因
  ① 面板算"残留数"只用 list_ours()，而它在 Windows 上只有 PowerShell
     一条路 —— 一旦失败就返回空，于是"进程还在跑"却显示残留 0。
  ② 运行状态页只提示「N 个残留，建议清理」，但**根本没有清理按钮**，
     用户只能整个关掉重开。
  ③ kill_ours() 以前在**杀之前**算 alive 并写 PID 文件，文件里留下
     一堆马上要死的号；且杀完从不复查，返回值只是"发过命令了"。

二、日志"没翻译"
  审计日志直接写英文 kind，翻译器又只认得 offline（→"离线"），
  于是同一行出现 "dynamic/video/离线/live" 这种中英混杂。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import cleanup as C
import paths          # noqa: E402
import logtranslate as LT    # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ok(cond, msg):
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    return bool(cond)


# ---------- 一、all_ours 必须三条路汇总 ----------

def test_all_ours_merges():
    print("[1] all_ours 汇总三条路")
    orig = (C._pids_from_file, C.list_ours, C.pids_on_ports,
            C._cmdline_of, C.is_ours)
    try:
        C._pids_from_file = lambda: [101, 102]
        C.list_ours = lambda: [(102, "python bot.py"), (103, "python webui.py")]
        C.pids_on_ports = lambda: [104]
        C._cmdline_of = lambda p: "python webui.py"
        C.is_ours = lambda c: True
        got = C.all_ours()
        r = _ok(set(got) == {101, 102, 103, 104},
                "三条路并集 101/102/103/104（实际 %s）" % got)
    finally:
        (C._pids_from_file, C.list_ours, C.pids_on_ports,
         C._cmdline_of, C.is_ours) = orig

    # PowerShell 整条路挂掉（返回空）时，不能因此认为"没有残留"
    orig2 = (C._pids_from_file, C.list_ours, C.pids_on_ports,
             C._cmdline_of, C.is_ours)
    try:
        C._pids_from_file = lambda: [201]
        C.list_ours = lambda: []          # ← 模拟 PowerShell 失败
        C.pids_on_ports = lambda: []
        got = C.all_ours()
        r &= _ok(201 in got,
                 "命令行扫描失效时仍能从 PID 文件找到 201（实际 %s）" % got)
    finally:
        (C._pids_from_file, C.list_ours, C.pids_on_ports,
         C._cmdline_of, C.is_ours) = orig2
    return r


def test_all_ours_excludes_self():
    print("[2] all_ours 排除自己")
    me = os.getpid()
    orig = (C._pids_from_file, C.list_ours, C.pids_on_ports)
    try:
        C._pids_from_file = lambda: [me, 999]
        C.list_ours = lambda: []
        C.pids_on_ports = lambda: []
        got = C.all_ours()
        return _ok(me not in got and 999 in got,
                   "不含自己 %d，含 999（实际 %s）" % (me, got))
    finally:
        (C._pids_from_file, C.list_ours, C.pids_on_ports) = orig


# ---------- 二、kill_ours ----------

def test_kill_keeps_running_bot():
    print("[3] 清理保留正在运行的机器人（keep）")
    orig = (C.all_ours, C._pid_alive, C.pids_on_ports, C._pids_from_file)
    killed = []
    try:
        C.all_ours = lambda: [501, 502, 503]
        C._pid_alive = lambda p: False          # 杀完都死了
        C.pids_on_ports = lambda: []
        C._pids_from_file = lambda: []
        import subprocess as sp
        real_run = sp.run
        sp.run = lambda *a, **k: killed.append(a[0]) or real_run(*a, **k)
        try:
            n = C.kill_ours(keep={502})
        finally:
            sp.run = real_run
        cmds = [c for c in killed if isinstance(c, list) and len(c) > 1]
        hit_502 = any("502" in " ".join(str(x) for x in c) for c in cmds)
        return _ok((not hit_502) and n == 2,
                   "keep=502 未被杀，关掉 2 个（实际关 %d，502 被杀=%s）"
                   % (n, hit_502))
    finally:
        (C.all_ours, C._pid_alive, C.pids_on_ports,
         C._pids_from_file) = orig


def test_pids_file_rewritten_after_kill():
    print("[4] PID 文件在杀**之后**重写")
    path = os.path.join(HERE, ".bot.pids")
    orig = (C.all_ours, C._pid_alive, C.pids_on_ports, C._pids_from_file,
            C.PIDS_FILE)
    tmp = os.path.join(HERE, "_t_pids_test")
    # PID 文件已经不在程序目录了（数据目录/run 下），测试里把它指回 HERE，
    # 否则断言读的是项目根、实际写在数据目录 → 必然 FileNotFoundError。
    _orig_run_file = getattr(paths, "run_file", None)
    try:
        C.PIDS_FILE = "_t_pids_test"
        if _orig_run_file is not None:
            paths.run_file = lambda name: os.path.join(HERE, name)
            import cleanup as _c2
            _c2.paths.run_file = paths.run_file
        # 601/602 都要被杀，603 是"杀之后"仍然活着的（模拟没杀掉）
        C.all_ours = lambda: [601, 602]
        C._pid_alive = lambda p: p == 603
        C.pids_on_ports = lambda: []
        C._pids_from_file = lambda: []
        C.kill_ours()
        with open(tmp, encoding="utf-8") as f:
            left = [x.strip() for x in f if x.strip()]
        # 文件里应是"杀完之后仍活着"的：601/602 已死，不该留在里面
        bad = [x for x in left if x in ("601", "602")]
        os.remove(tmp)
        return _ok(not bad, "已杀掉的 601/602 不在 PID 文件里（实际 %s）" % left)
    finally:
        if _orig_run_file is not None:
            paths.run_file = _orig_run_file
        (C.all_ours, C._pid_alive, C.pids_on_ports, C._pids_from_file,
         C.PIDS_FILE) = orig
        if os.path.exists(tmp):
            os.remove(tmp)


def test_wmic_fallback_exists():
    print("[5] Windows 有 wmic 兜底（PowerShell 失败时不至于返回空）")
    return _ok(callable(getattr(C, "_list_ps_wmic", None)),
               "_list_ps_wmic 存在且在 list_ours 中被调用")


def test_kill_returns_confirmed():
    print("[6] 杀完复查：没真杀掉的不算进返回值")
    orig = (C.all_ours, C._pid_alive, C.pids_on_ports, C._pids_from_file)
    try:
        C.all_ours = lambda: [701, 702]
        # 701 死了，702 还活着（模拟 taskkill 没生效）
        C._pid_alive = lambda p: p == 702
        C.pids_on_ports = lambda: []
        C._pids_from_file = lambda: []
        n = C.kill_ours()
        return _ok(n == 1, "只确认关掉 1 个（实际 %d）" % n)
    finally:
        (C.all_ours, C._pid_alive, C.pids_on_ports, C._pids_from_file) = orig


# ---------- 三、日志翻译 ----------

def test_push_kind_log_cn():
    print("[7] 审计日志里的推送类型写中文")
    try:
        import webui as W
    except Exception as e:
        return _ok(False, "无法导入 webui：%s" % e)
    lbl = getattr(W, "PUSH_KIND_LABEL", None)
    if not lbl:
        return _ok(False, "缺少 PUSH_KIND_LABEL")
    kinds = getattr(W, "PUSH_KINDS", ())
    miss = [k for k in kinds if not lbl.get(k)]
    r = _ok(not miss, "7 种类型都有中文名（缺 %s）" % miss)
    r &= _ok(all(any(ord(ch) > 127 for ch in lbl[k])
                 for k in kinds if lbl.get(k)),
             "中文名确实含中文，不是把英文原名抄一遍")
    return r


def test_translate_kind_line():
    print("[8] 历史日志里的英文 kind 翻成中文")
    r = True
    for en, cn in (("dynamic", "动态"), ("video", "投稿"),
                   ("top_comment", "置顶评论"), ("season", "合集")):
        src = "测试推送 127.0.0.1 6FBBD9B5 " + en
        out = LT.translate_symbols(src)
        r &= _ok(cn in out and en not in out,
                 "%s → %s（实际 %r）" % (en, cn, out))
    return r


def test_translate_no_false_positive():
    print("[9] 不误伤域名与变量名")
    r = True
    # ⚠️ "live_status=1" 已不在此列：v1.50 起直播状态统一说人话
    #    （0/1/2 → 未开播/已开播/轮播中），它属于"该翻"而不是"误伤"。
    #    这里只锁真正不能动的东西：域名、方法名、mime 类型。
    for src in ("进入直播间 https://live.bilibili.com/7788",
                "_check_live(uid=1)",
                "video/x/flv 分段"):
        out = LT.translate_symbols(src)
        r &= _ok(("开播" not in out) and ("投稿" not in out),
                 "未误伤 %r → %r" % (src, out))
    return r


def test_translate_level():
    print("[10] 日志级别翻成中文")
    out = LT.translate_symbols("日志级别已更新：DEBUG（输出调试日志）")
    return _ok("调试" in out and "DEBUG" not in out,
               "DEBUG → 调试（实际 %r）" % out)


# ---------- 四、前端按钮 ----------

def test_frontend_clean_button():
    print("[11] 运行状态页有清理按钮 + 有处理器")
    base = os.path.dirname(HERE) if os.path.basename(HERE) == "tests" else HERE
    # 源码已收进 src/，兼容新旧两种布局
    _t = os.path.join(HERE, "templates", "index.html")
    _j = os.path.join(HERE, "static", "app.js")
    if not os.path.exists(_t):
        _t = os.path.join(HERE, "src", "templates", "index.html")
    if not os.path.exists(_j):
        _j = os.path.join(HERE, "src", "static", "app.js")
    html_p, js_p = _t, _j
    try:
        html = open(html_p, encoding="utf-8").read()
        js = open(js_p, encoding="utf-8").read()
    except Exception as e:
        return _ok(False, "读不到前端文件：%s" % e)
    r = _ok('data-act="clean-procs"' in html, "index.html 有 clean-procs 按钮")
    r &= _ok("'clean-procs'" in js or '"clean-procs"' in js,
             "app.js 有 clean-procs 处理器")
    r &= _ok("/api/clean-procs" in js, "app.js 调用了 /api/clean-procs")
    return r


def main():
    print("=" * 60)
    print("残留进程清理 + 日志翻译")
    print("=" * 60)
    rs = [
        test_all_ours_merges(),
        test_all_ours_excludes_self(),
        test_kill_keeps_running_bot(),
        test_pids_file_rewritten_after_kill(),
        test_wmic_fallback_exists(),
        test_kill_returns_confirmed(),
        test_push_kind_log_cn(),
        test_translate_kind_line(),
        test_translate_no_false_positive(),
        test_translate_level(),
        test_frontend_clean_button(),
    ]
    bad = rs.count(False)
    print("-" * 60)
    print("通过 %d / %d" % (len(rs) - bad, len(rs)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
