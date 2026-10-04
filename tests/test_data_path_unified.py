#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据文件路径必须只有一个答案（v1.98.3）。

背景：面板(webui)、机器人(bot)、安全自检(security)、启动自检(start)
四处各自算路径，算出了**两个不同的文件**：

    面板 ：security.resolve_in_base()  → 相对路径拼到**源码目录**
    机器人：db.resolve_data_path()     → 相对路径拼到**数据目录**

于是机器人照着数据目录那份在推（订阅、合集都在），面板读源码目录那份
（空的）显示"还没有登记任何群" —— 订阅看不见、改不了、删不掉。

修法：四处统一走 db.data_path_from_cfg()。
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
for _p in (_SRC, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db          # noqa: E402
import security    # noqa: E402

PASS = []
FAIL = []


def ck(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(("  [OK] " if cond else "  [FAIL] ") + msg)


def _cfg(data_file):
    return {"data_file": data_file} if data_file else {}


def main():
    print("== 数据文件路径统一 ==")
    tmp = tempfile.mkdtemp(prefix="dp-")
    os.environ["BILI_NOTIFY_HOME"] = tmp
    import importlib
    importlib.reload(db)
    home = os.path.abspath(db.data_home())

    # 1) 三种 config 取值，都必须落在数据目录
    for v in ("data.db", "state.json", "", None):
        p = os.path.abspath(db.data_path_from_cfg(_cfg(v)))
        ck(p.startswith(home + os.sep),
           "data_file=%r → 落在数据目录（%s）" % (v, os.path.basename(p)))
        ck(p.endswith("state.json"),
           "data_file=%r → 一律归到 state.json" % (v,))

    # 2) 复现旧的分裂：旧写法把相对路径拼到源码目录
    old_panel = os.path.abspath(
        security.resolve_in_base(_cfg("data.db").get("data_file")))
    new_both = os.path.abspath(db.data_path_from_cfg(_cfg("data.db")))
    ck(not old_panel.startswith(home + os.sep),
       "复现：旧写法算出来的路径不在数据目录（%s）" % old_panel)
    ck(old_panel != new_both,
       "复现：旧写法与统一入口不是同一个文件 —— 这正是面板空白的病根")

    # 3) 旧 config 里存了源码目录的绝对路径 → 拉回数据目录
    bad = os.path.join(os.path.abspath(db.paths.src_dir()), "state.json")
    p = os.path.abspath(db.data_path_from_cfg({"data_file": bad}))
    ck(p.startswith(home + os.sep),
       "绝对路径指向源码目录时拉回数据目录（不再产生第二份）")

    # 4) 端到端：机器人写入 → 面板读得到
    st = db.Store(db.data_path_from_cfg({}))
    st.set_group_name("G1", "测试群")
    st.set_sub("G1", 12345, "dyn", True, "某UP")
    ck(any(g["gid"] == "G1" for g in st.groups_with_meta()),
       "机器人那份：能读到群")

    panel = db.Store(db.data_path_from_cfg({}))     # 面板现在的取法
    ck(any(g["gid"] == "G1" for g in panel.groups_with_meta()),
       "面板用统一入口：同一个群读得到")
    ck(panel.path == st.path, "面板与机器人是同一个文件")

    # 旧取法：读的是另一个文件，什么都看不到
    old = db.Store(os.path.abspath(
        security.resolve_in_base(_cfg("").get("data_file") or "state.json")))
    ck(old.path != st.path, "旧取法确实是另一个文件")
    ck(old.groups_with_meta() == [],
       "复现：旧取法下面板一个群都看不到（空白）")

    # 5) 源码级：四处都改成同一个入口
    import io
    srcs = {}
    for name in ("webui.py", "bot.py", "start.py", "security.py"):
        srcs[name] = io.open(os.path.join(_SRC, name), encoding="utf-8").read()
    for name, need in (("webui.py", "db.data_path_from_cfg"),
                       ("bot.py", "db.data_path_from_cfg"),
                       ("start.py", "db.data_path_from_cfg"),
                       ("security.py", "data_path_from_cfg")):
        ck(need in srcs[name], "%s 走统一入口" % name)
    ck("resolve_in_base(load_cfg()" not in srcs["webui.py"],
       "webui 不再用 resolve_in_base 拼数据路径")
    ck("db.Store(db.DATA_FILE_DEFAULT)" not in srcs["start.py"],
       "start 不再写死 DATA_FILE_DEFAULT")

    # 6) 反向验证：把统一入口废掉，分裂立刻回来
    _orig = db.data_path_from_cfg
    db.data_path_from_cfg = lambda cfg=None: os.path.abspath(
        security.resolve_in_base((cfg or {}).get("data_file") or "state.json"))
    try:
        bad2 = db.data_path_from_cfg({})
        ck(not bad2.startswith(home + os.sep),
           "反向：废掉统一入口后路径又跑到源码目录（断言咬得住）")
    finally:
        db.data_path_from_cfg = _orig

    print("\n通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
