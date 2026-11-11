# -*- coding: utf-8 -*-
"""反向验证：把 @全体 的六处改动逐条退回旧写法，测试必须变红。

用法：python3 tests/_rv_at_all_delivery.py
只读真源、写临时副本，不改任何交付文件。
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
ENV = dict(os.environ)
ENV["PYTHONPATH"] = "/data/workspace/_pydeps"

GROUPS = [
    ("A 回退模式下不发单独 @ 文本", [
        ("bot.py",
         "await self.qq.send_text(gid, at_text)",
         "pass  # reverted: no standalone @ text"),
    ]),
    ("B 后端忽略 at_all", [
        ("webui.py",
         'at_all = (d.get("at_all") is True and kind == "live")',
         "at_all = False  # reverted"),
    ]),
    ("C 前端不提交 at_all", [
        ("static/app.js", "at_all: testOpt('atall', false)", "/* reverted */"),
    ]),
    ("D 面板开关默认开", [
        ("templates/index.html",
         'data-opt="atall"></button>',
         'data-opt="atall" class="sw-btn on"></button>'),
    ]),
    ("E 纯文本路径恢复有条件降级", [
        ("bot.py",
         "if at_all:\n                # 多半是机器人不是群管理员",
         "if at_all and not is_no_perm_error(e):\n                # 多半是机器人不是群管理员"),
    ]),
    ("F 去掉 explain（未 @ 原因不可判定）", [
        ("atall.py", "    def explain(self, gid: str) -> str:",
         "    def _explain_disabled(self, gid: str) -> str:"),
    ]),
]


def patch(base, edits):
    for fn, old, new in edits:
        p = os.path.join(base, fn)
        s = open(p, encoding="utf-8").read()
        if old not in s:
            print(f"  [WARN] anchor not found in {fn}: {old[:50]!r}")
            continue
        open(p, "w", encoding="utf-8").write(s.replace(old, new, 1))


def run_tests(src):
    env = dict(ENV)
    env["BILI_TRUE_SRC"] = src
    out = []
    for t in ("test_at_all_live.py", "test_at_all_testpush.py"):
        r = subprocess.run([sys.executable, os.path.join(HERE, t)],
                           env=env, capture_output=True, text=True,
                           timeout=180)
        out.append(r.stdout + r.stderr)
    return "\n".join(out)


def main():
    print("baseline (unmodified copy):")
    base = tempfile.mkdtemp(prefix="rv_atall_base_")
    shutil.copytree(SRC, os.path.join(base, "src"))
    o = run_tests(os.path.join(base, "src"))
    print("  ", [l for l in o.splitlines() if l.strip().endswith("/25")
                 or l.strip().endswith("/21")])
    if "[FAIL]" in o:
        print("  [ERROR] baseline itself is red")
        shutil.rmtree(base, ignore_errors=True)
        return 1
    shutil.rmtree(base, ignore_errors=True)

    bad = 0
    for name, edits in GROUPS:
        tmp = tempfile.mkdtemp(prefix="rv_atall_")
        dst = os.path.join(tmp, "src")
        shutil.copytree(SRC, dst)
        patch(dst, edits)
        o = run_tests(dst)
        red = "[FAIL]" in o
        print(("  [OK]   " if red else "  [MISS] ") + name
              + (f"  ({o.count('[FAIL]')} 项红)" if red else "  仍然全绿"))
        if not red:
            bad += 1
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"{len(GROUPS) - bad}/{len(GROUPS)} 组反向验证咬住")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
