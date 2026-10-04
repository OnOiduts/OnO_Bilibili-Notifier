# -*- coding: utf-8 -*-
"""启动变慢的三处根因 + 对应修复。

背景：用户反馈「现在启动的越来越慢」。排查出三个真因，而且其中两个
天生就是"越用越慢"的（跟订阅数、日志体积正相关）：

  1. start.py 把 cleanup.kill_ours() 调了 **3 次**（两块复制粘贴的重复
     代码 + _kill_leftovers）。Windows 上每次都要 PowerShell/wmic 扫全机
     python 进程 + netstat 查端口，单次好几秒。
  2. cleanup 里对每个 PID **单独起一次 tasklist** 子进程，.bot.pids 攒多少
     个就起多少次；kill_ours 内部还要跑两遍 all_ours()。
  3. 启动同步 **完全串行**：每个 UP 依次跑 直播/投稿/动态，跑完睡 0.4s。
     5 UP + 3 合集 = 18 次串行请求，B站 一风控每次等满 10s 超时 = 三分钟。
     订阅越多越慢。

本文件逐条钉住这三点，防止改回去。
"""
import asyncio
import os
import re
import sys
import time

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
# ⚠️ 不把源码根目录加进 sys.path，`import cleanup` 会直接失败：
#    pytest 是插 rootdir，直接 `python3 tests/xxx.py` 时插的是 tests/ 目录。
sys.path.insert(0, BASE)


def _src(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()


# ──────────────────────────────────────────────────────────────
# 1. start.py 只清理一次
# ──────────────────────────────────────────────────────────────

def _strip_comments(src):
    """去掉整行注释后再做文本断言。

    ⚠️ 不剥的话，注释里提一句"以前调了三次 kill_ours"也会被算成一次
       调用 —— 断言就永远对不上号（这就是第一版踩的坑）。
    """
    return "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))


def test_start_cleans_up_only_once():
    """start.py 里 kill_ours 只能有一处调用。

    以前是三处（两块复制粘贴 + _kill_leftovers），Windows 上光"清自己"
    就要半分钟，而且这部分耗时跟订阅多少、日志多大都无关，纯浪费。
    """
    src = _strip_comments(_src("start.py"))
    hits = re.findall(r"kill_ours\s*\(", src)
    assert len(hits) == 1, (
        f"start.py 里 kill_ours 应只调用 1 次，实际 {len(hits)} 次")


def test_start_still_registers_pid():
    """清理合并成一次后，register_pid 不能跟着被删掉 —— 否则
    .bot.pids 里没有自己，下次清理就找不到本进程。"""
    src = _src("start.py")
    assert "register_pid" in src


def test_no_duplicated_cleanup_block():
    """不留两份一模一样的清理块（当初就是复制粘贴留下的）。"""
    src = _src("start.py")
    pat = r"import cleanup\s*\n\s*n = cleanup\.kill_ours\(\)"
    assert len(re.findall(pat, src)) <= 1


# ──────────────────────────────────────────────────────────────
# 2. cleanup 不再对每个 PID 起一次子进程
# ──────────────────────────────────────────────────────────────

def test_alive_set_exists_and_is_batch():
    """_alive_set 必须存在，且 Windows 分支只用一次子进程取回全部 PID。"""
    import cleanup
    assert hasattr(cleanup, "_alive_set")
    src = _src("cleanup.py")
    fn = src.split("def _alive_set", 1)[1].split("\ndef ", 1)[0]
    # 批量：一条 tasklist 拿全部，而不是 /FI "PID eq X" 逐个问
    assert "tasklist" in fn
    assert '"/FO", "CSV", "/NH"' in fn
    assert 'PID eq' not in fn, "批量函数里不该再出现逐个查询的 /FI PID eq"


def test_pids_from_file_uses_batch():
    """_pids_from_file 必须走批量判定，而不是逐个 _pid_alive。"""
    src = _src("cleanup.py")
    fn = src.split("def _pids_from_file", 1)[1].split("\ndef ", 1)[0]
    assert "_alive_set" in fn, "_pids_from_file 应改用批量判定"
    assert "_pid_alive(" not in fn


def test_alive_set_returns_only_live_pids():
    """_alive_set 只返回活着的进程号（本机 PID 必活，不存在的必死）。"""
    import cleanup
    me = os.getpid()
    dead = 999997
    got = cleanup._alive_set([me, dead])
    assert me in got
    assert dead not in got


def test_alive_set_handles_empty():
    import cleanup
    assert cleanup._alive_set([]) == set()
    assert cleanup._alive_set(None) == set()


def test_kill_ours_scans_only_once():
    """kill_ours 内部只能跑一次 all_ours()。

    以前开头一次、结尾复查一次；再叠加 start.py 调三次 → 六趟全机扫描。
    """
    import cleanup
    calls = {"n": 0}
    orig = cleanup.all_ours

    def counting():
        calls["n"] += 1
        return orig()

    cleanup.all_ours = counting
    try:
        cleanup.kill_ours()
    finally:
        cleanup.all_ours = orig
    assert calls["n"] == 1, (
        f"kill_ours 内 all_ours 应只跑 1 次，实际 {calls['n']} 次")


def test_kill_ours_still_returns_zero_when_clean():
    """没有残留时应返回 0，且不抛异常（启动脚本靠它判断能不能起）。"""
    import cleanup
    assert cleanup.kill_ours() == 0


# ──────────────────────────────────────────────────────────────
# 3. bot.log 瘦身 + 只读尾部
# ──────────────────────────────────────────────────────────────

def test_trim_log_file_keeps_tail():
    """超过阈值只留最后 N 行，且必须是最后那些（不能留开头）。"""
    sys.path.insert(0, BASE)
    import importlib
    _stub_botpy()
    bot = importlib.import_module("bot")

    import tempfile
    fd, path = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    try:
        lines = [f"line-{i}\n" for i in range(500)]
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        dropped = bot._trim_log_file(path, max_bytes=10, keep=100)
        assert dropped == 400
        with open(path, encoding="utf-8") as f:
            left = f.read().splitlines()
        assert left == [f"line-{i}" for i in range(400, 500)]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_trim_log_file_noop_when_small():
    """没超阈值就一个字都别动。"""
    import importlib
    _stub_botpy()
    bot = importlib.import_module("bot")

    import tempfile
    fd, path = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("a\n")
        assert bot._trim_log_file(path, max_bytes=1 << 20, keep=10) == 0
        with open(path, encoding="utf-8") as f:
            assert f.read() == "a\n"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_tail_lines_matches_full_read():
    """尾部读取的结果必须和全量读取的末尾一致 —— 不能为了快读错内容。"""
    sys.path.insert(0, BASE)
    import importlib
    webui = importlib.import_module("webui")

    import tempfile
    fd, path = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            for i in range(5000):
                f.write(f"2026-09-28 10:00:00 [INFO] 第 {i} 条日志\n")
        want = 200
        with open(path, encoding="utf-8") as f:
            full = [l.rstrip("\n") for l in f.readlines()]
        tail = webui._tail_lines(path, want)
        assert tail == full[-want:]
        assert webui._count_lines_fast(path) == 5000
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_tail_lines_handles_empty_and_missing():
    sys.path.insert(0, BASE)
    import importlib
    webui = importlib.import_module("webui")
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    try:
        assert webui._tail_lines(path, 10) == []
        assert webui._count_lines_fast(path) == 0
    finally:
        os.remove(path)
    assert webui._tail_lines(os.path.join(BASE, "__no_such__.log"), 10) == []


def test_api_log_no_longer_readlines_whole_file():
    """/api/log 里不能再出现把整个文件读进内存的 readlines()。"""
    src = _src("webui.py")
    # ⚠️ 必须匹配完整的 "def api_log():" —— 写成 "def api_log" 会先撞上
    #    api_login()（前缀相同），切出来的是别的函数，断言自然全不对。
    fn = _strip_comments(
        src.split("\ndef api_log():", 1)[1].split("\n@app.route", 1)[0])
    assert "readlines()" not in fn, (
        "api_log 仍然全量 readlines()，日志一大面板就会卡")
    assert "_tail_lines" in fn, "api_log 应改用尾部读取"
    assert "_count_lines_fast" in fn, "总条数应改用字节扫描，别再全量构造列表"


# ──────────────────────────────────────────────────────────────
# 4. 启动同步：并发 + 预算
# ──────────────────────────────────────────────────────────────

class _FakeStore:
    def __init__(self, uids):
        self._uids = uids
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1

    def all_ups(self):
        return list(self._uids)

    def get_up(self, uid):
        return {"uname": f"UP{uid}", "room_id": 0}

    def enabled_kinds_of_up(self, uid):
        return ["live", "video", "dynamic"]

    def all_season_keys(self):
        return []


def _make_bot(uids, delay, cfg=None):
    sys.path.insert(0, BASE)
    import importlib
    _stub_botpy()
    bot = importlib.import_module("bot")

    b = bot.NotifyBot.__new__(bot.NotifyBot)      # 绕开 __init__（要连 QQ）
    b.cfg = cfg or {}
    b.store = _FakeStore(uids)
    b._silent = True

    async def _fix():
        return 0
    b._fix_missing_rooms = _fix

    async def _seasons(deadline=None):
        return 0
    b._startup_sync_seasons = _seasons

    async def _slow(uid, name):
        await asyncio.sleep(delay)
    b._check_live = _slow
    b._check_video = _slow
    b._check_dynamic = _slow
    return b


def test_startup_sync_runs_concurrently():
    """并发后总耗时应明显低于串行。

    3 个 UP、每类 1.0s：串行要 9s，并发 3 只要 ~3s。
    """
    b = _make_bot([1, 2, 3], delay=1.0,
                  cfg={"startup_sync_concurrency": 3,
                       "startup_sync_budget": 60})
    t = time.time()
    done = asyncio.run(b._do_startup_sync())
    cost = time.time() - t
    assert done == 3, f"应完成 3 个 UP，实际 {done}"
    assert cost < 6.0, f"并发后仍耗时 {cost:.1f}s（串行约 9s），并发没生效"


def test_startup_sync_respects_budget():
    """超预算就停，剩下的留给轮询 —— 不能把启动无限拖下去。"""
    b = _make_bot([1, 2, 3], delay=2.0,
                  cfg={"startup_sync_concurrency": 1,
                       "startup_sync_budget": 5})
    t = time.time()
    done = asyncio.run(b._do_startup_sync())
    cost = time.time() - t
    assert done < 3, f"预算耗尽时不应全部完成，实际完成 {done}"
    # 关键：不能无限拖。3 个 UP 串行要 18s，必须在 9s 内收住。
    assert cost < 9.0, f"启动同步耗时 {cost:.1f}s，预算没起作用"


def test_startup_sync_budget_floor():
    """预算有下限（5s），写 0 也不会变成"一启动就全跳过"。"""
    b = _make_bot([1], delay=0.05,
                  cfg={"startup_sync_concurrency": 1,
                       "startup_sync_budget": 0})
    done = asyncio.run(b._do_startup_sync())
    assert done == 1


def test_season_sync_accepts_deadline():
    """_startup_sync_seasons 必须接受 deadline，否则超预算时也收不住。"""
    src = _src("bot.py")
    fn = src.split("async def _startup_sync_seasons", 1)[1].split("\n    async def ", 1)[0]
    assert "deadline" in fn


_BOTPY_STUBBED = False


def _stub_botpy():
    """botpy 装不上（PyPI 无此发行版），用桩顶上，只补 bot.py 用到的部分。"""
    global _BOTPY_STUBBED
    if _BOTPY_STUBBED:
        return
    if "botpy" in sys.modules:
        _BOTPY_STUBBED = True
        return
    import types
    m = types.ModuleType("botpy")

    class Client:
        def __init__(self, *a, **k):
            pass

    class Intents:
        def __init__(self, *a, **k):
            pass

    m.Client = Client
    m.Intents = Intents
    lg = types.ModuleType("botpy.logging")

    class _Log:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

        def critical(self, *a, **k):
            pass

        def exception(self, *a, **k):
            pass

    lg.get_logger = lambda *_a, **_k: _Log()
    m.logging = lg
    sys.modules["botpy"] = m
    sys.modules["botpy.logging"] = lg
    _BOTPY_STUBBED = True


if __name__ == "__main__":
    bad = 0
    for k in sorted(list(globals())):
        if not k.startswith("test_"):
            continue
        try:
            globals()[k]()
            print(f"  OK   {k}")
        except AssertionError as e:
            bad += 1
            print(f"  FAIL {k}: {e}")
        except Exception as e:
            bad += 1
            print(f"  ERR  {k}: {type(e).__name__}: {e}")
    print(f"\n通过 {len([k for k in globals() if k.startswith('test_')]) - bad}/{len([k for k in globals() if k.startswith('test_')])}")
    sys.exit(1 if bad else 0)
