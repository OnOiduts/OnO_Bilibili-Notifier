# -*- coding: utf-8 -*-
"""逐条把 @全体 的修复退回旧写法，确认真值测试真的会红。

用法：python3 tests/_rv_at_all.py
所有数字由脚本实跑输出，不手写。
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL = os.path.join(ROOT, "src")
TEST = os.path.join(HERE, "test_at_all_live.py")
DEPS = "/data/workspace/_pydeps"


def replace(path, old, new, count=1):
    with open(path, encoding="utf-8") as f:
        s = f.read()
    if old not in s:
        raise SystemExit("锚点没找到：%s :: %r" % (os.path.basename(path), old[:60]))
    s = s.replace(old, new, count)
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)


CASES = [
    ("A 去掉 @全体 标签", "src/bot.py", [
        ('tagged_md = (atall.AT_ALL_TAG + "\\n" + md_g_no_img\n'
         '                                 if want_at else md_g_no_img)',
         'tagged_md = md_g_no_img'),
        ('        tagged = atall.AT_ALL_TAG + "\\n" + text if at_all else text',
         '        tagged = text'),
    ], "开播提醒带 @全体 标签"),

    ("B 去掉每群每天配额", "src/bot.py", [
        ("            return self._atall_quota().available(gid)",
         "            return True"),
    ], "同一群当天第二次开播不再 @"),

    ("C 去掉「只限开播」", "src/bot.py", [
        ('        if kind != "live":\n            return False\n', ''),
    ], "下播提醒不带 @全体"),

    ("D 去掉被拒后的降级重发", "src/bot.py", [
        ("                        if want_at and not is_no_perm_error(e_md):",
         "                        if False:"),
    ], "带标签发送失败会去掉标签重发"),

    ("E 去掉开关判定", "src/bot.py", [
        ('        if not bool(self.cfg.get("at_all_on_live", True)):\n'
         '            return False\n', ''),
    ], "开关关闭时不 @"),

    ("F 后端白名单不收该键", "src/webui.py", [
        ('        cfg["at_all_on_live"] = bool(d.get("at_all_on_live"))\n', ''),
    ], "后端白名单收 at_all_on_live"),

    ("G 前端不提交该键", "src/static/app.js", [
        ("    at_all_on_live: !$('cAtAllLive') || $('cAtAllLive').checked,\n", ''),
    ], "面板开关与保存链路齐全"),

    ("H 过时注释复活", "src/notify.py", [
        ("2026-10-26", "2020-01-01"),
    ], "过时结论已从源码注释里清掉"),

    ("I 旧标签 @everyone 复活", "src/bot.py", [
        ("# 「最近推过的置顶评论」保留几条。",
         "# 旧标签 @everyone 已废弃\n# 「最近推过的置顶评论」保留几条。"),
    ], "bot 用官方群聊标签而非旧 @everyone"),

    ("J @全体 溢出到别的类型", "src/bot.py", [
        ('        if kind != "live":\n            return False\n', ''),
    ], "bot 的 @全体 只限开播提醒"),

    ("K 面板不写每日 1 次限制", "src/templates/index.html", [
        ("每个群每天只能 @ 1 次", "每天可多次 @"),
    ], "面板 @全体 说明写明官方每日 1 次限制"),
]


def run_test(base):
    env = dict(os.environ)
    env["BILI_TRUE_SRC"] = base
    env["PYTHONPATH"] = DEPS
    p = subprocess.run([sys.executable, TEST], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, timeout=180)
    return p.stdout


def main():
    tmp = tempfile.mkdtemp(prefix="rv_atall_")
    try:
        # 基线：干净副本必须全绿
        clean = os.path.join(tmp, "clean")
        shutil.copytree(REAL, clean,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        out = run_test(clean)
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        print("[baseline] %s" % tail)
        if "18/18" not in out:
            print(out)
            raise SystemExit("干净副本没跑满 18/18，反向验证不成立")
        ok = 0
        for name, rel, edits, expect in CASES:
            case = os.path.join(tmp, "case_" + name[0])
            shutil.copytree(clean, case)
            # clean/ 就是 src 的内容本身（不再有一层 src/）
            target = os.path.join(case, rel.split("src/")[-1])
            for old, new in edits:
                replace(target, old, new)
            out = run_test(case)
            red = ("[FAIL] " + expect) in out
            print("[%s] %-24s 期望红项=%s" %
                  ("OK" if red else "NG", name, "红" if red else "仍绿"))
            if red:
                ok += 1
            else:
                print(out[-1200:])
        print()
        print("%d/%d 反向验证咬住" % (ok, len(CASES)))
        if ok != len(CASES):
            sys.exit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
