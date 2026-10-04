# -*- coding: utf-8 -*-
"""「沙箱环境」开关关不掉 —— 回归测试。

真实事故：面板上关掉「沙箱环境」，开关动了一下（外观变关），
下一秒又弹回"开启"。根因两条，缺一不可：

  1. saveCfg 用 `cSandbox.checked || cSandbox2.checked` 合成布尔值
     —— 只要还有一个没同步、结果恒为 true，提交的永远是"开"；
  2. 一对开关的同步监听在 fillCfg 里注册，比 initFormDirty 的
     保存监听晚（DOMContentLoaded 里先 init、后 loadState），
     于是保存发生时另一个开关还是旧值。

所以必须同时满足：① 保存前先同步 ② 布尔值不用 || 合成。
"""
import io, os, re, sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
SRC = os.path.join(ROOT, "static", "app.js")

FAIL = []


def ck(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAIL.append(name)


def main():
    src = io.open(SRC, encoding="utf-8").read()

    print("[1] 布尔值不再用 || 合成")
    ck("saveCfg 用 sandboxOn()", "is_sandbox: sandboxOn()," in src)
    ck("不再出现 is_sandbox: !!(", not re.search(r"is_sandbox: !!", src))

    print("[2] 同步 / 读取 helper")
    ck("有 syncSandboxPair()", "function syncSandboxPair(" in src)
    ck("有 sandboxOn()", "function sandboxOn(" in src)
    m = re.search(r"function sandboxOn\(\) \{(.*?)\n\}", src, re.S)
    ck("sandboxOn 内部不含 ||", bool(m) and "||" not in m.group(1))
    m2 = re.search(r"function syncSandboxPair\(srcId\) \{(.*?)\n\}", src, re.S)
    ck("syncSandboxPair 会把两个开关写成同一个值",
       bool(m2) and "a.checked = b.checked" in m2.group(1))

    print("[3] 保存发生在同步之后")
    m = re.search(r"function initFormDirty\(\) \{(.*?)\nfunction ", src, re.S)
    body = m.group(1) if m else ""
    i_sync = body.find("syncSandboxPair(id)")
    i_save = body.find("saveCfg(true)")
    ck("initFormDirty 里先同步再保存",
       i_sync >= 0 and i_save >= 0 and i_sync < i_save)
    m = re.search(r"function initSwitches\(\) \{(.*?)\n\}", src, re.S)
    body = m.group(1) if m else ""
    m3 = re.search(r"el\.addEventListener\('change', function \(\) \{(.*?)\n    \}\);",
                   body, re.S)
    cb = m3.group(1) if m3 else ""
    ck("initSwitches 的 change 回调里先同步再画",
       cb.find("syncSandboxPair") >= 0
       and cb.find("syncSandboxPair") < cb.find("paintSwitches"))

    print("[4] fillCfg 的同步监听走同一条路")
    m = re.search(r"\['cSandbox', 'cSandbox2'\]\.forEach\(id => \{(.*?)\n  \}\);",
                  src, re.S)
    b = m.group(1) if m else ""
    ck("改调 syncSandboxPair(id)", "syncSandboxPair(id);" in b)
    ck("不再手动写 other.checked", "other.checked = el.checked" not in src)

    print("")
    if FAIL:
        print("FAILED: %d" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
