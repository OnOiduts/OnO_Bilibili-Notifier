# -*- coding: utf-8 -*-
"""锁定「数据落盘失败：[WinError 5] 拒绝访问」的修复（v2.2.4）。

症状（用户实况）：
    [ERROR] (db.py:858)_persist  数据落盘失败（...\\数据备份\\state.json）：
            [WinError 5] 拒绝访问。: '...\\.tmp-y8c9gj6x.json'
            -> '...\\数据备份\\state.json'
    连续三条，临时文件名各不相同 —— 每次都是新建、失败、清理，
    说明不是"卡住"，而是**每一次替换都被系统拒绝**。

后果：这是唯一的持久化出口。写不进去 = 面板上所有订阅改动重启即丢，
    属于维护政策里"严重 bug"那一档，必须修。

根因（Windows 上 os.replace 抛 WinError 5 的常见三种）：
    1. 目标文件带只读属性 —— Windows 上替换只读目标会被直接拒绝
    2. 目标被别的进程独占打开 —— 另一实例 / 编辑器 / 云同步客户端
    3. 杀软或"受控文件夹访问"正在扫描拦截 —— 桌面、文档这类受保护
       目录尤其常见；用户的数据目录正是在桌面下，命中概率最高

    1 能绕（清只读位），2 和 3 只能等它松开。所以修法是逐级降级：
        退避重试 os.replace（清只读 + 等）
        → shutil.move（内部换策略：rename → 复制后删源）
        → 直接写目标文件（放弃原子性，但保住数据）
    另外启动自检清理上次失败留下的 .tmp-*.json。

已排除的自食其疑点：写完后会调 restrict_perms()，若它在 Windows 上设了
    只读位，下一次 replace 必然失败 —— 查过实现，非 POSIX 直接 return，不是它。
"""
import builtins
import os
import shutil
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

import db  # noqa: E402

PASS = FAIL = 0
FAILED = []


def ck(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  ", name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  FAIL", name)


def _home(tag):
    d = tempfile.mkdtemp(prefix="we5-%s-" % tag)
    os.environ["BILI_NOTIFY_HOME"] = d
    return d


# ────────────────────────── 1. 正常路径仍是原子的 ──────────────────────────

def test_normal_write_still_atomic():
    _home("normal")
    d = tempfile.mkdtemp(prefix="we5-norm-")
    p = os.path.join(d, "state.json")
    db._atomic_write_json(p, {"hello": "world"})
    with open(p, encoding="utf-8") as f:
        ck(f.read().strip().startswith("{"), "正常写入仍成功")
    left = [n for n in os.listdir(d) if n.startswith(".tmp-")]
    ck(not left, "正常写入后不留临时文件")


# ────────────────────────── 2. 只读目标会被清掉 ──────────────────────────

def test_clear_readonly_runs_on_nt():
    d = tempfile.mkdtemp(prefix="we5-ro-")
    p = os.path.join(d, "state.json")
    with open(p, "w") as f:
        f.write("{}")
    os.chmod(p, 0o444)                      # 只读
    orig_name = db.os.name
    calls = {"n": 0}
    real_chmod = os.chmod

    def fake_chmod(path, mode):
        calls["n"] += 1
        real_chmod(path, mode)

    db.os.name = "nt"
    db.os.chmod = fake_chmod
    try:
        db._clear_readonly(p)
    finally:
        db.os.name = orig_name
        db.os.chmod = real_chmod
    ck(calls["n"] == 1, "只读目标在 Windows 下会被清掉只读位")

    # POSIX 下不动 —— chmod 语义不同，靠目录权限管控
    calls["n"] = 0
    db.os.chmod = fake_chmod
    try:
        db._clear_readonly(p)
    finally:
        db.os.chmod = real_chmod
    ck(calls["n"] == 0, "非 Windows 下不做 chmod")


# ────────────────────────── 3. 降级：replace 失败 → move 兜住 ──────────────────────────

def _patch_replace_fail():
    """让 os.replace 一直抛 WinError 5，模拟被系统拒绝。"""
    real = os.replace

    def fake(src, dst):
        raise OSError(5, "拒绝访问", dst)

    db.os.replace = fake
    return real


def test_falls_back_to_move():
    _home("move")
    d = tempfile.mkdtemp(prefix="we5-mv-")
    p = os.path.join(d, "state.json")
    real_sleep = db.time.sleep
    db.time.sleep = lambda s: None          # 跳过退避等待
    real_replace = _patch_replace_fail()
    try:
        db._atomic_write_json(p, {"via": "move"})
    finally:
        db.os.replace = real_replace
        db.time.sleep = real_sleep
    ck(os.path.exists(p), "os.replace 全程失败时，shutil.move 兜住了")
    with open(p, encoding="utf-8") as f:
        ck('"via": "move"' in f.read(), "  → 内容正确写入")


# ────────────────────────── 4. 再降级：move 也失败 → 直接写 ──────────────────────────

def test_falls_back_to_direct_write():
    _home("direct")
    d = tempfile.mkdtemp(prefix="we5-dw-")
    p = os.path.join(d, "state.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"old": 1}')
    real_sleep = db.time.sleep
    real_replace = _patch_replace_fail()
    real_move = db.shutil.move

    def fake_move(src, dst):
        raise OSError(5, "拒绝访问", dst)

    db.time.sleep = lambda s: None
    db.shutil.move = fake_move
    try:
        db._atomic_write_json(p, {"via": "direct"})
    finally:
        db.os.replace = real_replace
        db.shutil.move = real_move
        db.time.sleep = real_sleep
    with open(p, encoding="utf-8") as f:
        body = f.read()
    ck('"via": "direct"' in body, "move 也失败时，直接写目标兜住了数据")
    left = [n for n in os.listdir(d) if n.startswith(".tmp-")]
    ck(not left, "  → 临时文件已清理")
    db.os.replace = real_replace


# ────────────────────────── 5. 全失败：抛错 + 不残留 + 旧数据不动 ──────────────────────────

def test_all_fail_raises_and_cleans():
    _home("allfail")
    d = tempfile.mkdtemp(prefix="we5-af-")
    p = os.path.join(d, "state.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"old": 1}')

    real_sleep = db.time.sleep
    real_replace = _patch_replace_fail()
    real_move = db.shutil.move
    real_fsync = db.os.fsync
    counter = {"n": 0}

    def fake_move(src, dst):
        raise OSError(5, "拒绝访问", dst)

    def fake_fsync(fd):
        # 第一次是写临时文件（放行），之后的调用代表"直接写"也失败
        counter["n"] += 1
        if counter["n"] > 1:
            raise OSError(5, "拒绝访问")

    db.time.sleep = lambda s: None
    db.shutil.move = fake_move
    db.os.fsync = fake_fsync
    raised = False
    try:
        db._atomic_write_json(p, {"via": "none"})
    except OSError:
        raised = True
    finally:
        db.os.replace = real_replace
        db.shutil.move = real_move
        db.os.fsync = real_fsync
        db.time.sleep = real_sleep
    ck(raised, "所有方案都失败时如实抛错（不静默吞掉）")
    left = [n for n in os.listdir(d) if n.startswith(".tmp-")]
    ck(not left, "  → 失败也清掉临时文件，不堆积")
    with open(p, encoding="utf-8") as f:
        ck(f.read() == '{"old": 1}', "  → 旧数据没被破坏")


# ────────────────────────── 6. 残留临时文件清理 ──────────────────────────

def test_cleanup_stale_tmp():
    _home("stale")
    d = tempfile.mkdtemp(prefix="we5-st-")
    p = os.path.join(d, "state.json")

    old = os.path.join(d, ".tmp-old.json")
    with open(old, "w") as f:
        f.write("{}")
    os.utime(old, (time.time() - 7200, time.time() - 7200))   # 2 小时前

    fresh = os.path.join(d, ".tmp-fresh.json")
    with open(fresh, "w") as f:
        f.write("{}")                                          # 刚刚

    keep = os.path.join(d, "state.json.bak")
    with open(keep, "w") as f:
        f.write("{}")

    n = db.cleanup_stale_tmp(p, max_age=3600)
    ck(n == 1, f"只清掉过期的临时文件（实际 {n}）")
    ck(not os.path.exists(old), "  → 过期的已删")
    ck(os.path.exists(fresh), "  → 正在写的不误删（另一实例可能在用）")
    ck(os.path.exists(keep), "  → 非临时文件不动")


# ────────────────────────── 7. 启动自检接线 ──────────────────────────

def test_startup_wires_cleanup():
    src = os.path.join(os.path.dirname(_HERE), "src", "start.py")
    with open(src, encoding="utf-8") as f:
        body = f.read()
    ck("db.cleanup_stale_tmp(" in body, "start.py 启动自检会清残留临时文件")


def main():
    tests = [
        test_normal_write_still_atomic,
        test_clear_readonly_runs_on_nt,
        test_falls_back_to_move,
        test_falls_back_to_direct_write,
        test_all_fail_raises_and_cleans,
        test_cleanup_stale_tmp,
        test_startup_wires_cleanup,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:
            ck(False, f"{t.__name__} 抛异常：{type(e).__name__}: {e}")
    print(f"\n通过 {PASS} / 失败 {FAIL}")
    if FAILED:
        print("失败项：")
        for n in FAILED:
            print("  -", n)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
