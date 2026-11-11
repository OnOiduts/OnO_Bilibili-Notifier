# -*- coding: utf-8 -*-
"""反向验证：把「@全体 合成一条」的每处改动逐条退回旧写法，测试必须变红。

用法：PYTHONPATH=/data/workspace/_pydeps python3 tests/_rv_at_all_merge.py
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
TESTS = os.path.join(ROOT, "tests")
ENV = dict(os.environ)
ENV["PYTHONPATH"] = "/data/workspace/_pydeps"

GROUPS = [
    ("A 去掉合成分支（标签不再进卡片）", [
        ("bot.py",
         "md_body = (atall.AT_ALL_TAG + \"\\n\" + md_g_no_img\n"
         "                               if merge else md_g_no_img)",
         "md_body = md_g_no_img  # reverted"),
    ]),
    ("B 去掉被拒后去掉标签重发", [
        ("bot.py",
         "await self.qq.send_markdown(\n"
         "                                    gid, md_g_no_img, keyboard=keyboard)",
         "pass  # reverted: no fallback markdown"),
    ]),
    ("C 去掉回退双消息模式", [
        ("bot.py", "if want_at and not merge:", "if False:  # reverted"),
    ]),
    ("D 合成成功却不占配额", [
        ("bot.py",
         "                            self._atall_used(gid)\n"
         "                            _log.info(",
         "                            pass  # reverted\n"
         "                            _log.info("),
    ]),
    ("E 后端不收 at_all_merge", [
        ("webui.py",
         'cfg["at_all_merge"] = bool(d.get("at_all_merge"))',
         "pass  # reverted"),
    ]),
    ("F 后端 state 不回显 at_all_merge", [
        ("webui.py",
         '"at_all_merge": bool(cfg.get("at_all_merge", True)),',
         '"at_all_merge_off": True,  # reverted'),
    ]),
    ("G 面板测试推送退回两条", [
        ("webui.py",
         'merge_at = bool(at_all and cfg.get("at_all_merge", True))',
         "merge_at = False  # reverted"),
    ]),
    ("H 前端不提交 at_all_merge", [
        ("static/app.js",
         "at_all_merge: !$('cAtAllMerge') || $('cAtAllMerge').checked,",
         "at_all_merge_dummy: true,  // reverted"),
    ]),
    ("I 面板删掉合并开关", [
        ("templates/index.html",
         'id="cAtAllMerge" checked',
         'id="cAtAllMergeX" checked'),
    ]),
    ("J at_all_merge 不登记热更新", [
        ("bot.py",
         '"at_all_on_live", "at_all_merge",',
         '"at_all_on_live",'),
    ]),
]


def patch(base, edits):
    for fn, old, new in edits:
        p = os.path.join(base, fn)
        s = open(p, encoding="utf-8").read()
        if s.count(old) != 1:
            print(f"  [WARN] anchor count={s.count(old)} in {fn}: {old[:60]!r}")
            return False
        open(p, "w", encoding="utf-8").write(s.replace(old, new, 1))
    return True


def run_tests(src, full=False):
    """baseline 跑三个；每组反向验证只跑主测试即可（每组必须让它变红）。"""
    env = dict(ENV)
    env["BILI_TRUE_SRC"] = src
    files = (("test_at_all_merge.py", "test_at_all_live.py",
              "test_at_all_testpush.py") if full else ("test_at_all_merge.py",))
    out = []
    for t in files:
        r = subprocess.run([sys.executable, os.path.join(TESTS, t)],
                           env=env, capture_output=True, text=True,
                           timeout=180)
        out.append(r.stdout + r.stderr)
    return "\n".join(out)


def main():
    print("baseline (unmodified copy):")
    base = tempfile.mkdtemp(prefix="rv_merge_base_")
    dst = os.path.join(base, "src")
    shutil.copytree(SRC, dst)
    o = run_tests(dst, full=True)
    print("  ", [l for l in o.splitlines() if l.strip().endswith("/28")
                 or l.strip().endswith("/26") or l.strip().endswith("/25")])
    if "[FAIL]" in o:
        print("  [ERROR] baseline itself is red")
        print(o[-3000:])
        shutil.rmtree(base, ignore_errors=True)
        return 1
    shutil.rmtree(base, ignore_errors=True)

    bad = 0
    for name, edits in GROUPS:
        tmp = tempfile.mkdtemp(prefix="rv_merge_")
        dst = os.path.join(tmp, "src")
        shutil.copytree(SRC, dst)
        ok = patch(dst, edits)
        if not ok:
            print(f"  [SKIP] {name}  (anchor broken)")
            bad += 1
            shutil.rmtree(tmp, ignore_errors=True)
            continue
        out = run_tests(dst)
        red = "[FAIL]" in out
        print(("  [OK]   " if red else "  [MISS] ") + name
              + (f"  ({out.count('[FAIL]')} 项红)" if red else "  仍然全绿"))
        if not red:
            bad += 1
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"{len(GROUPS) - bad}/{len(GROUPS)} 组反向验证咬住")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
