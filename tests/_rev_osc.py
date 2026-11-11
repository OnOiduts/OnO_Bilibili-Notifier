# -*- coding: utf-8 -*-
"""反向验证：把振荡修复退回旧写法，测试必须变红。

不改代码就"通过"的测试等于没测 —— 每一处修复都要能被证伪。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "bot.py")
BAK = "/tmp/_bot_osc_bak.py"
TEST = os.path.join(ROOT, "tests", "test_interval_oscillate.py")

shutil.copy(SRC, BAK)

CASES = [
    ("退避时不记地板（_slow_floor）",
     "                self._slow_floor = self.interval\n",
     "                pass\n"),
    ("去掉地板拦截（恢复一步跳回最快）",
     "        if floor:\n",
     "        if False:\n"),
    ("去掉长期平安后遗忘地板的机制",
     "        if floor and since > CALM_FORGET_AFTER:\n",
     "        if False:\n"),
    ("撞过之后仍一步冲回最快（不看 hits）",
     "        if hits == 0 and since > 300 and self.interval > self.min_interval:\n",
     "        if since > 300 and self.interval > self.min_interval:\n"),
]


def run():
    r = subprocess.run([sys.executable, TEST], capture_output=True, text=True,
                       cwd=ROOT)
    out = r.stdout
    fail = [l for l in out.splitlines() if l.startswith("[FAIL]")]
    return len(fail), fail


def main():
    n0, _ = run()
    print(f"修复后基线: {n0} 项失败（应为 0）\n")

    bad = 0
    for name, old, new in CASES:
        txt = open(BAK, encoding="utf-8").read()
        if old not in txt:
            print(f"[SKIP] {name}：找不到目标片段")
            bad += 1
            continue
        open(SRC, "w", encoding="utf-8").write(txt.replace(old, new, 1))
        n, fails = run()
        ok = n > 0
        print(f"[{'OK' if ok else 'BAD'}] {name} → {n} 项失败")
        for f in fails:
            print(f"         {f}")
        if not ok:
            bad += 1
        shutil.copy(BAK, SRC)

    n1, _ = run()
    print(f"\n还原后: {n1} 项失败（应为 0）")
    return 1 if (bad or n0 or n1) else 0


if __name__ == "__main__":
    sys.exit(main())
