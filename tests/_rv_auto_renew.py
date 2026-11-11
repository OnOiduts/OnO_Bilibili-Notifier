# -*- coding: utf-8 -*-
"""反向验证：把「更稳口径 + 提醒自动续期」逐条退回旧写法，测试必须变红。

⚠️ 为什么单独成脚本：test_auto_renew.py 的注释里写过「全部做过反向验证」，
   但这个脚本一开始并不存在 —— 等于凭空声明。补上，且每组都要求：
     1) 替换锚点必须真的命中（没命中直接判失败，不许假红）
     2) 退回后测试确实 FAIL
     3) 真源副本原样跑必须 PASS

做法：把 src + tests 完整复制到临时目录，只在副本上改，真源一行不动。
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
TESTS = os.path.join(ROOT, "tests")
DEPS = "/data/workspace/.deps"

FAILS = []


def chk(name, cond, extra=""):
    if cond:
        print(f"  [ok] {name}")
    else:
        print(f"  [FAIL] {name} {extra}")
        FAILS.append(name)


def copy_repo(td):
    dst_src = os.path.join(td, "src")
    dst_tests = os.path.join(td, "tests")
    shutil.copytree(SRC, dst_src, ignore=shutil.ignore_patterns(
        "__pycache__", "backups", "*.pyc"))
    os.makedirs(dst_tests, exist_ok=True)
    for f in ("test_auto_renew.py", "test_auto_renew_ui.py"):
        shutil.copy2(os.path.join(TESTS, f), os.path.join(dst_tests, f))
    return dst_src, dst_tests


def run_suite(td):
    """返回 (是否全过, 合并输出)。两组测试都要过才算过。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = DEPS + os.pathsep + env.get("PYTHONPATH", "")
    out = []
    ok = True
    for f in ("test_auto_renew.py", "test_auto_renew_ui.py"):
        r = subprocess.run([sys.executable, os.path.join(td, "tests", f)],
                           cwd=td, env=env, capture_output=True,
                           text=True, timeout=180)
        out.append((r.stdout or "") + (r.stderr or ""))
        if r.returncode != 0:
            ok = False
    return ok, "\n".join(out)


def patch(path, old, new):
    s = open(path, encoding="utf-8").read()
    if old not in s:
        return False
    open(path, "w", encoding="utf-8").write(s.replace(old, new, 1))
    return True


OLD_TS_BLOCK = '''        est = False
        if changed:
            # 只有真的换到新 SESSDATA，才从这一刻重新起算 7 天。
            cfg["bili_cookie_ts"] = time.time()
            _exp, est = bili_login.effective_expires(
                new_cs, float(res.get("expires") or 0), time.time())
            cfg["bili_cookie_expires"] = _exp
        else:
            # ⚠️ 没换到时**保留原登录时刻**，宁可早提醒。
            #    原来这里无条件写 now()，等于把本地提醒计时偷偷往后推 7 天：
            #    B 站明天就把旧会话踢了，面板还显示「还剩 7 天」，
            #    第 5/6 天那两条提醒反而来晚了。
            if old_exp > 0:
                cfg["bili_cookie_expires"] = bili_login.apply_cap(old_exp, old_ts)
'''
NEW_TS_BLOCK = '''        est = False
        cfg["bili_cookie_ts"] = time.time()
        _exp, est = bili_login.effective_expires(
            new_cs, float(res.get("expires") or 0), time.time())
        cfg["bili_cookie_expires"] = _exp
'''


def case_a(td):
    """没换到也重算 ts（旧写法）→ 「ts 保持原时刻/档位不后移」必须红。"""
    return patch(os.path.join(td, "src", "webui.py"),
                 OLD_TS_BLOCK, NEW_TS_BLOCK)


def case_b(td):
    """gate 从不触发（旧写法）→ gate/tick 必须红。"""
    return patch(os.path.join(td, "src", "bili_expiry_alert.py"),
                 "        if level == LEVEL_OK:\n"
                 "            self.reset()          # 回到健康区间 → 下个周期重新有资格\n"
                 "            return False\n",
                 "        if level == LEVEL_OK:\n"
                 "            self.reset()          # 回到健康区间 → 下个周期重新有资格\n"
                 "            return False\n"
                 "        return False  # 旧写法：从不自动触发\n")


def case_c(td):
    """main() 忘了拉线程 → 挂载断言必须红。"""
    return patch(os.path.join(td, "src", "webui.py"),
                 "    _start_auto_renew_thread()\n    app.run(",
                 "    app.run(")


def case_d(td):
    """状态接口不带 auto 字段 → 回显断言必须红。"""
    return patch(os.path.join(td, "src", "webui.py"),
                 '        "auto": _auto_renew.get("last", ""),\n', "")


def case_e(td):
    """删掉前端 checkAutoRenew → 前端测试必须红。"""
    p = os.path.join(td, "src", "static", "app.js")
    s = open(p, encoding="utf-8").read()
    a = s.find("async function checkAutoRenew")
    b = s.find("\nasync function pollBrowser", a)
    if a < 0 or b < 0:
        return False
    open(p, "w", encoding="utf-8").write(s[:a] + s[b + 1:])
    return True


def case_f(td):
    """删掉 DOM 元素 → 结构检查必须红。"""
    return patch(os.path.join(td, "src", "templates", "index.html"),
                 '          <div id="autoRenewNote" class="msg"></div>\n', "")


def case_g(td):
    """去掉「只在文案变化时更新」的去重 → 重复写断言必须红。"""
    return patch(os.path.join(td, "src", "static", "app.js"),
                 "  if (auto !== AUTO_RENEW_LAST) {",
                 "  if (true) {")


CASES = [
    ("A 没换到也重算 ts（旧口径）", case_a),
    ("B gate 从不触发", case_b),
    ("C main 忘了拉线程", case_c),
    ("D status 不回显 auto", case_d),
    ("E 删掉 checkAutoRenew", case_e),
    ("F 删掉 autoRenewNote 元素", case_f),
    ("G 去掉文案去重", case_g),
]


def main():
    print("== 基线：真源副本原样跑 ==")
    with tempfile.TemporaryDirectory(prefix="rv_base_") as td:
        copy_repo(td)
        ok, out = run_suite(td)
        chk("未改动的副本全过", ok, out[-700:])
        if not ok:
            print(out[-1500:])

    print("\n== 逐组退回旧写法，期望全部变红 ==")
    for name, fn in CASES:
        with tempfile.TemporaryDirectory(prefix="rv_case_") as td:
            copy_repo(td)
            hit = fn(td)
            chk(name + "：锚点命中", hit)
            if not hit:
                continue
            ok, out = run_suite(td)
            reds = [l.strip() for l in out.splitlines() if "[FAIL]" in l]
            chk(name + "：测试确实变红", not ok, "输出尾部：" + out[-300:])
            if not ok:
                print("        " + (reds[0] if reds else "(无 FAIL 明细)")[:160])

    print()
    if FAILS:
        print("[FAIL] %d 项：%s" % (len(FAILS), "; ".join(FAILS)))
        return 1
    print("[PASS] 基线全绿，%d 组反向验证全部咬住" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
