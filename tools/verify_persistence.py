#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""装完新版必跑：确认数据真的存得住。

为什么要有这个脚本：
    v1.92.0 及更早的版本，落盘那一步"只有调用、没有实现"，
    订阅只活在进程内存里 —— 关掉程序就没了，看起来像"被误删"。
    从 v1.93.0 起才真正写盘。这个脚本就是用来当面验证：
    现在手上这一份，到底存不存得住。

用法（在项目根目录）：
    python tools/verify_persistence.py

只读检查真实数据目录；写入类测试全在临时目录里做，不碰你的数据。
"""
import os
import sys
import json
import shutil
import tempfile

def _find_src():
    """脚本放在哪都能跑：找含 paths.py 的那个目录。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, "src"),
             os.path.join(here, "..", "src"),
             os.path.join(here, "..", "..", "src")]
    # 再退一步：从当前工作目录往下找两层
    cwd = os.path.abspath(os.getcwd())
    for base in (cwd, os.path.dirname(cwd)):
        for sub in ("src", "."):
            cands.append(os.path.join(base, sub))
    for c in cands:
        c = os.path.abspath(c)
        if os.path.isfile(os.path.join(c, "paths.py")):
            return c
    return ""


_src = _find_src()
if not _src:
    print("找不到 src/ 目录（里面要有 paths.py）。")
    print("请把这个脚本放到程序根目录，然后从那里运行：")
    print("    python 数据落盘自检.py")
    sys.exit(2)
sys.path.insert(0, _src)

import paths  # noqa: E402
import db  # noqa: E402

OK, BAD, WARN = "[通过]", "[失败]", "[注意]"


def line(tag, text):
    print("  %s %s" % (tag, text))


def head(t):
    print()
    print("=" * 58)
    print("  " + t)
    print("=" * 58)


def count_subs(state):
    """通用统计：不写死表名，认带 gid 的订阅类表。"""
    n_sub = n_grp = 0
    for name, rows in (state or {}).items():
        if not isinstance(rows, list) or not rows:
            continue
        if not isinstance(rows[0], dict):
            continue
        cols = set(rows[0].keys())
        if name.lower() in ("groups", "group"):
            n_grp = len(rows)
        elif "gid" in cols and ("uid" in cols or "season_id" in cols):
            # 去重：一个群订同一个 UP 的多种动态会占多行
            keys = set()
            for r in rows:
                keys.add((r.get("gid"), r.get("uid"), r.get("season_id")))
            n_sub = len(keys)
    return n_sub, n_grp


def main():
    fails = []

    # ---------------------------------------------------------------
    head("1. 数据目录在哪（最关键一项）")
    home = os.path.abspath(paths.data_home())
    prog = os.path.abspath(paths.app_dir())
    print("      数据目录：%s" % home)
    print("      程序目录：%s" % prog)

    # 核心：数据目录不能在程序目录里面，
    # 否则"删文件夹换新版"就会把数据一起删掉。
    try:
        inside = os.path.commonpath([home, prog]) == prog
    except ValueError:
        inside = False  # 不同盘符，必然在外面

    if inside:
        line(BAD, "数据目录还在程序目录里面 —— 删文件夹会连数据一起删")
        fails.append("数据目录位置")
    else:
        line(OK, "数据目录在程序目录之外，删文件夹删不到它")

    # ---------------------------------------------------------------
    head("2. 真实数据目录里有什么")
    if not os.path.isdir(home):
        line(WARN, "数据目录还不存在（首次启动会自动建）")
    else:
        for name in ("config.yaml", "state.json", ".machine_key"):
            p = os.path.join(home, name)
            if os.path.exists(p):
                line(OK, "%-14s %d 字节" % (name, os.path.getsize(p)))
            else:
                line(WARN, "%-14s 还没建（首次启动 / 保存凭据时会建）" % name)

    st = os.path.join(home, "state.json")
    if os.path.exists(st):
        try:
            with open(st, "r", encoding="utf-8") as f:
                s = json.load(f)
            n_sub, n_grp = count_subs(s)
            line(OK, "订阅 %d 条 / 群 %d 个" % (n_sub, n_grp))
            if not n_sub:
                line(WARN, "订阅是空的 —— 按你手上的情况重新添加吧")
        except Exception as e:
            line(BAD, "state.json 读不出来：%s" % e)
            fails.append("数据文件损坏")

    # ---------------------------------------------------------------
    head("3. 落盘往返（v1.92.0 就栽在这一项）")
    tmp = tempfile.mkdtemp(prefix="bili_verify_")
    try:
        os.environ["BILI_NOTIFY_HOME"] = tmp
        s1 = db.Store()
        s1.ensure_group("__verify_test__", name="自检临时")
        s1.save()
        del s1  # 毁掉对象 = 模拟"关掉程序"

        s2 = db.Store()  # 重新打开 = "再启动一次"
        got = s2.get_group("__verify_test__")
        del s2

        if got and got.get("name") == "自检临时":
            line(OK, "写入 → 关掉 → 重开，数据还在（真落盘了）")
        else:
            line(BAD, "重开之后读不回来 —— 和 v1.92.0 是同一个毛病")
            fails.append("落盘往返")

        jf = os.path.join(tmp, "state.json")
        if os.path.exists(jf) and os.path.getsize(jf) > 0:
            line(OK, "磁盘上确有落盘文件 state.json（%d 字节）"
                  % os.path.getsize(jf))
        else:
            line(BAD, "磁盘上没有落盘文件")
            fails.append("落盘文件")
    finally:
        os.environ.pop("BILI_NOTIFY_HOME", None)
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------------------
    head("4. 凭据加密")
    try:
        import secretbox
        stt = secretbox.status()
        if stt.get("has_key"):
            line(OK, ".machine_key 在（密文凭据能解开）")
        else:
            line(WARN, ".machine_key 还没生成（保存凭据时会自动建）")
        broken = stt.get("broken") or stt.get("broken_fields") or []
        if broken:
            line(BAD, "这些字段存的是密文但解不开：%s —— 需要重填" % broken)
            fails.append("凭据解不开")
        else:
            line(OK, "已存的密文字段都能正常解开")
    except Exception as e:
        line(WARN, "凭据状态读不了：%s" % e)

    # ---------------------------------------------------------------
    head("5. 备份")
    bdir = os.path.join(home, "backups")
    if os.path.isdir(bdir):
        n = len([f for f in os.listdir(bdir)
                 if os.path.isfile(os.path.join(bdir, f))])
        line(OK, "备份目录已有 %d 份（最多留 10 份，每天自动一份）" % n)
    else:
        line(WARN, "还没备份过 —— 加完订阅后去面板点一次「备份」")

    # ---------------------------------------------------------------
    print()
    print("=" * 58)
    if fails:
        print("  还有问题：%s" % "、".join(fails))
        print("  先别关程序，按上面提示处理完再说。")
    else:
        print("  全部通过 —— 加完订阅可以放心关程序，数据丢不了。")
        print("  建议：加完订阅去面板「设置 → 数据与备份」点一次备份。")
    print("=" * 58)
    print()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
