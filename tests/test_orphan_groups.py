# -*- coding: utf-8 -*-
"""锁定「订阅在跑、面板却显示『还没有登记任何群』」的修复（v1.98.1）。

症状（用户实况）：
    日志：启动同步完成：6 位 UP 主的当前状态已记录为基线
         启动同步：3 个合集的当前内容已记为基线
    面板：「订阅」页显示「还没有登记任何群」

    机器人在按订阅检测，面板却一个群都看不到 —— 看不见、改不了、删不掉。

根因：
  groups 表是面板「群与订阅」的**唯一来源**（webui → groups_with_meta）。
  而 subs / seasons 里只要有 gid，机器人就照常推送，跟 groups 表有没有
  这一行毫无关系。

  旧库来源复杂（早期版本落盘不全、手工改过、从回收站捞回），
  groups 表缺行是常态。于是出现「幽灵订阅」：
      subs   里有 GHOST1 的 18 条订阅   → 机器人照推
      groups 表里没有 GHOST1            → groups_with_meta() 返回 []
                                        → 面板「还没有登记任何群」

  更隐蔽的一点：_load_cache() 会用 setdefault 把 subs 里的 gid 补进
  **内存缓存**，所以 all_groups() 看着是有群的，只有查表的
  groups_with_meta() 是空的 —— 两边不一致，排查时极易被误导。

修法：repair_orphan_groups() 把「订阅里有、groups 表里没有」的 gid 补进
  groups 表（只补 gid + 空名，已有群名一律不动）。两处调用：
    · import_legacy_file() 末尾 —— 合并旧库后立刻补
    · start.py 每次启动自检 —— 导入过的旧库打了标记不会再导，
      漏这一步就永远补不回来
"""
import os
import shutil
import sqlite3
import sys
import tempfile

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
    d = tempfile.mkdtemp(prefix="og-%s-" % tag)
    os.environ["BILI_NOTIFY_HOME"] = d
    return d


def _mk_legacy(path, groups, ups=6, subs_per_up=3, seasons=1):
    """造一份旧版 data.db。groups 传空列表即复现「幽灵群」。"""
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript(db.SCHEMA)
    db._ensure_columns(c)
    for gid, name in groups:
        c.execute("INSERT INTO groups(gid,name,created_at,updated_at)"
                  " VALUES(?,?,?,?)", (gid, name, 1.0, 1.0))
    for uid in range(1, ups + 1):
        c.execute("INSERT INTO ups(uid,uname) VALUES(?,?)", (uid, "UP%d" % uid))
    for uid in range(1, ups + 1):
        for kind in ("dynamic", "video", "live")[:subs_per_up]:
            c.execute('INSERT INTO subs(gid,uid,kind,"on",updated_at)'
                      ' VALUES(?,?,?,?,?)', ("G1", uid, kind, 1, 1.0))
    for i in range(seasons):
        c.execute('INSERT INTO seasons(gid,uid,season_id,title,"on",known,'
                  'created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                  ("G1", 3, 3175959 + i, "合集%d" % i, 1, "[]", 1, 1))
    c.commit()
    c.close()
    return path


print("\n== 1. 幽灵群：groups 表空，但订阅在 ==")
_home("ghost")
_legacy = _mk_legacy(os.path.join(tempfile.mkdtemp(prefix="og-legacy-"),
                                  "data.db"), groups=[])
st = db.Store(db.DATA_FILE_DEFAULT)
ck(st.groups_with_meta() == [], "复现：导入前面板确实一个群都没有")

res = st.import_legacy_file(_legacy)
ck(res.get("ok") is True, "旧库导入成功")
ck((res.get("summary") or {}).get("subs", 0) > 0, "订阅确实进了库（机器人在推）")
ck((res.get("report") or {}).get("groups_recovered", 0) == 1,
   "报告里记了补回 1 个群（groups_recovered）")
gm = st.groups_with_meta()
ck(len(gm) == 1 and gm[0]["gid"] == "G1", "面板看得到这个群了（不是空列表）")
ck(gm[0]["sub_count"] == 6, "群里的订阅数正确（6 位 UP 主）")

print("\n== 2. 只有合集、没有 UP 订阅时也要能看到群 ==")
_home("seasononly")
_legacy2 = _mk_legacy(os.path.join(tempfile.mkdtemp(prefix="og-legacy2-"),
                                   "data.db"), groups=[], subs_per_up=0, seasons=3)
st2 = db.Store(db.DATA_FILE_DEFAULT)
st2.import_legacy_file(_legacy2)
ck(len(st2.groups_with_meta()) == 1, "只订合集的群也能补回来")

print("\n== 3. 启动自检：不导入也能补（导入过的库打了标记不会再导）==")
_home("selftest")
st3 = db.Store(db.DATA_FILE_DEFAULT)
conn = st3._conn()
for uid in range(1, 7):
    conn.execute('INSERT INTO subs(gid,uid,kind,"on",updated_at)'
                 ' VALUES(?,?,?,?,?)', ("OLDG", uid, "dynamic", 1, 1.0))
conn.commit()
st3._persist()
st3._load_cache()
ck(st3.groups_with_meta() == [], "自检前：面板看不到（幽灵群）")
n = st3.repair_orphan_groups()
ck(n == 1, "自检补回 1 个群")
ck(len(st3.groups_with_meta()) == 1, "自检后：面板看得到了")

print("\n== 4. 幂等：补完再补必须是 0 ==")
ck(st3.repair_orphan_groups() == 0, "重复调用返回 0（不会每启一次刷一条日志）")

print("\n== 5. 完整库不受影响，群名不动 ==")
_home("intact")
st4 = db.Store(db.DATA_FILE_DEFAULT)
conn = st4._conn()
conn.execute("INSERT INTO groups(gid,name,created_at,updated_at)"
             " VALUES(?,?,?,?)", ("G1", "我的群", 1.0, 1.0))
conn.execute('INSERT INTO subs(gid,uid,kind,"on",updated_at)'
             ' VALUES(?,?,?,?,?)', ("G1", 1, "dynamic", 1, 1.0))
conn.commit()
st4._persist()
st4._load_cache()
before = st4.groups_with_meta()
ck(st4.repair_orphan_groups() == 0, "groups 表完整时一个都不补")
ck(st4.groups_with_meta() == before, "群名等既有信息一个字都没改")
ck(before[0]["name"] == "我的群", "已登记的群名保持原样")

print("\n== 6. 文案表里的 gid 也算（不只 subs / seasons）==")
_home("tpl")
st5 = db.Store(db.DATA_FILE_DEFAULT)
conn = st5._conn()
conn.execute("INSERT INTO group_templates(gid,kind,content,updated_at)"
             " VALUES(?,?,?,?)", ("TPLG", "dynamic", "自定义文案", 1))
conn.commit()
st5._persist()
st5._load_cache()
ck(db._orphan_gids(st5._conn()) == ["TPLG"], "group_templates 里的 gid 也能收进来")
ck(st5.repair_orphan_groups() == 1, "文案带来的群也补了")

print("\n== 7. 反向验证：废掉修复，症状必须回来 ==")
_home("reverse")
_orig = db._orphan_gids
db._orphan_gids = lambda conn: []          # 冒充没修的旧版
try:
    st6 = db.Store(db.DATA_FILE_DEFAULT)
    st6.import_legacy_file(_legacy)
    bad = st6.groups_with_meta()
finally:
    db._orphan_gids = _orig
ck(bad == [], "废掉修复后：面板又变成『还没有登记任何群』（断言咬得住）")

print("\n== 8. 前端：无名群要有名字兜底 ==")
_appjs = os.path.join(os.path.dirname(_HERE), "src", "static", "app.js")
_txt = ""
if os.path.isfile(_appjs):
    with open(_appjs, "r", encoding="utf-8") as f:
        _txt = f.read()
ck("未命名群" in _txt, "补回来的群没名字时，前端显示『未命名群 xxxxxxxx』而不是空白")

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAILED:
    print("FAILED:")
    for n in FAILED:
        print("  -", n)
    sys.exit(1)
