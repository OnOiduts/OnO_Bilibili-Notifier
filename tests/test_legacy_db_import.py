# -*- coding: utf-8 -*-
"""旧版 data.db 兼容：能不能把 v1.92 的 SQLite 订阅读回来。

⚠️ 背景：v1.93 把落盘从 SQLite 文件库（程序目录里的 data.db）换成了
   JSON（state.json）。老用户手里那份 data.db 是唯一的订阅记录，
   读不回来就得把群、UP 主、合集重新加一遍。

⚠️ 最关键的一条：导入**不能带推送基线**。旧库那份进度是几个月前的，
   盖到新库上，落在这之后的所有历史内容都会被判成"新内容"再推一遍。
   不导入反而安全 —— 缺基线的群在下一轮检测前会自动静默对齐
   （bot._missing_baselines → _init_baseline），只记进度、不推送。
"""
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

# v1.92 的真实表结构（从 v1.92.0 的 db.py 里原样取出）。
# 用它造库和现行结构对比，才能真的验证"旧库缺列"这条路径。
V92_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS groups (
    gid        TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    created_at REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS ups (
    uid         INTEGER PRIMARY KEY,
    uname       TEXT NOT NULL DEFAULT '',
    face        TEXT NOT NULL DEFAULT '',
    room_id     INTEGER NOT NULL DEFAULT 0,
    short_room_id INTEGER NOT NULL DEFAULT 0,   -- 直播间短号，没有则为 0
    live_status INTEGER,
    live_title  TEXT NOT NULL DEFAULT '',
    video_bvid  TEXT NOT NULL DEFAULT '',
    dyn_id      TEXT NOT NULL DEFAULT '',
    dyn_top_id  TEXT NOT NULL DEFAULT '',      -- 置顶动态 ID（独立基线）
    dyn_top_cmt TEXT NOT NULL DEFAULT '',      -- 置顶动态下的置顶评论 ID
    updated_at  REAL
);
CREATE TABLE IF NOT EXISTS group_templates (
  gid TEXT NOT NULL,
  kind TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  updated_at INTEGER,
  PRIMARY KEY (gid, kind)
);
CREATE TABLE IF NOT EXISTS seasons (
  gid TEXT NOT NULL,
  uid INTEGER NOT NULL,
  season_id INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  "on" INTEGER NOT NULL DEFAULT 1,
  known TEXT NOT NULL DEFAULT '[]',
  created_at INTEGER,
  updated_at INTEGER,
  PRIMARY KEY (gid, uid, season_id)
);
CREATE TABLE IF NOT EXISTS up_templates (
  gid TEXT NOT NULL,
  uid INTEGER NOT NULL,
  kind TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  updated_at INTEGER,
  PRIMARY KEY (gid, uid, kind)
);
CREATE TABLE IF NOT EXISTS season_templates (
  gid TEXT NOT NULL,
  uid INTEGER NOT NULL,
  season_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  updated_at INTEGER,
  PRIMARY KEY (gid, uid, season_id, kind)
);
CREATE TABLE IF NOT EXISTS subs (
    gid        TEXT NOT NULL,
    uid        INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    "on"       INTEGER NOT NULL DEFAULT 0,      -- on 是保留字，必须加引号
    updated_at REAL,
    PRIMARY KEY (gid, uid, kind)
);
CREATE INDEX IF NOT EXISTS idx_subs_uid_kind ON subs(uid, kind);
CREATE INDEX IF NOT EXISTS idx_subs_kind ON subs(kind);
CREATE INDEX IF NOT EXISTS idx_subs_gid ON subs(gid);

-- 按「群 × UP主 × 类型」隔离的基线。
-- ⚠️ 不能只按 (uid, kind) 存：A 群早就订阅、B 群刚订阅，两者进度不同。
--    共用一份基线会导致 B 群新订阅时，把 A 群留下的过期进度当成"旧内容"
--    判定成新内容，于是 B 群一订阅就收到它订阅之前就存在的投稿/动态。
-- v1 = 主值（bvid / dyn_id / live_status / 评论 id）
-- v2 = 副值（直播标题等）
CREATE TABLE IF NOT EXISTS sub_baseline (
    gid TEXT NOT NULL,
    uid INTEGER NOT NULL,
    kind TEXT NOT NULL,
    v1 TEXT,
    v2 TEXT,
    updated_at REAL,
    PRIMARY KEY (gid, uid, kind)
);
CREATE INDEX IF NOT EXISTS idx_sbl_uid_kind ON sub_baseline(uid, kind);
"""

FAILS = []


def ck(cond, name):
    if cond:
        print("  [ok] " + name)
    else:
        print("  [失败] " + name)
        FAILS.append(name)
    return bool(cond)


def make_old_db(path):
    """按 v1.92 的表结构造一份旧库，内容尽量贴近真实使用。"""
    conn = sqlite3.connect(path)
    conn.executescript(V92_SCHEMA)
    conn.execute("INSERT INTO meta(key,value) VALUES('schema_version','1')")
    conn.execute("INSERT INTO groups(gid,name,created_at) VALUES('G1','老群A',1)")
    conn.execute("INSERT INTO groups(gid,name,created_at) VALUES('G2','老群B',2)")
    conn.execute(
        "INSERT INTO ups(uid,uname,face,room_id,short_room_id,live_status,"
        "live_title,video_bvid,dyn_id,updated_at) "
        "VALUES(280242571,'星瞳_Official','https://f/1.jpg',12345,678,1,"
        "'直播中','BV_OLD_0001','90001',1)")
    conn.execute(
        "INSERT INTO ups(uid,uname,face,room_id,short_room_id,live_status,"
        "live_title,video_bvid,dyn_id,updated_at) "
        "VALUES(111,'伊万酱哒油','',0,0,0,'','BV_OLD_0002','90002',1)")
    # 订阅：G1 订四个类型，G2 只订动态
    for k in ("live", "video", "dynamic", "top_comment"):
        conn.execute(
            'INSERT INTO subs(gid,uid,kind,"on",updated_at) '
            "VALUES('G1',280242571,?,1,1)", (k,))
    conn.execute(
        'INSERT INTO subs(gid,uid,kind,"on",updated_at) '
        "VALUES('G2',280242571,'dynamic',1,1)")
    conn.execute(
        'INSERT INTO subs(gid,uid,kind,"on",updated_at) '
        "VALUES('G1',111,'video',1,1)")
    # 合集（含用户日志里那个 3175959）
    conn.execute(
        "INSERT INTO seasons(gid,uid,season_id,title,\"on\",known,"
        "created_at,updated_at) VALUES('G1',280242571,3175959,'星瞳合集',"
        "1,'[]',1,1)")
    conn.execute(
        "INSERT INTO seasons(gid,uid,season_id,title,\"on\",known,"
        "created_at,updated_at) VALUES('G2',111,40002,'伊万合集',1,'[]',1,1)")
    conn.execute(
        "INSERT INTO up_templates(gid,uid,kind,content,updated_at) "
        "VALUES('G1',280242571,'video','旧文案：{uname}投稿了',1)")
    # 旧的推送进度 —— 导入时**绝不能**带过来
    conn.execute(
        "INSERT INTO sub_baseline(gid,uid,kind,v1,v2,updated_at) "
        "VALUES('G1',280242571,'video','BV_OLD_0001','1600000000',1)")
    conn.execute(
        "INSERT INTO sub_baseline(gid,uid,kind,v1,v2,updated_at) "
        "VALUES('G1',280242571,'dyn','90001','1600000000',1)")
    conn.commit()
    conn.close()
    return path


def main():
    tmp = tempfile.mkdtemp(prefix="legacydb-")
    os.environ["BILI_NOTIFY_HOME"] = os.path.join(tmp, "home")
    for m in list(sys.modules):
        if m in ("db", "paths"):
            del sys.modules[m]
    import db

    old = make_old_db(os.path.join(tmp, "data.db"))

    print("—— 读旧库 ——")
    st, sm = db.read_legacy_db(old)
    ck(bool(st), "旧库能读出来")
    ck(sm.get("groups") == 2, "读出 2 个群")
    ck(sm.get("seasons") == 2, "读出 2 个合集")
    ck(sm.get("ups") == 2, "读出 2 位 UP 主")
    ck(sm.get("subs") == 6, "读出 6 条开启的订阅")
    ck(sm.get("templates") == 1, "读出 1 条文案")
    # 旧库没有 short_room_id 之外的很多新列，补齐后导出的行必须带全
    ck("dyn_top_id" in (st.get("ups") or [{}])[0],
       "旧库缺的列已补齐（dyn_top_id）")

    print("—— 合并导入 ——")
    store = db.Store("state.json")
    res = store.import_legacy_file(old)
    ck(res.get("ok") is True, "导入成功")
    s2 = db.Store("state.json")
    groups = s2._cache["groups"]
    ck(len(groups) == 2, "导入后 2 个群")
    ck((groups.get("G1") or {}).get("name") == "老群A", "群名对得上")
    ck(len((groups.get("G1") or {}).get("subs") or {}) == 2,
       "G1 订阅了 2 位 UP 主")
    ck(len((groups.get("G2") or {}).get("subs") or {}) == 1,
       "G2 订阅了 1 位 UP 主")
    ck(len(s2.seasons_of_group("G1")) == 1, "G1 的合集进来了")
    ck((s2.all_season_keys() or []) and (280242571, 3175959) in
       [tuple(x[1:]) for x in s2.all_season_keys()],
       "合集 3175959 进来了")
    ck(s2.get_up_templates("G1", 280242571).get("video") ==
       "旧文案：{uname}投稿了", "文案进来了")
    ck(s2.get_up(280242571).get("uname") == "星瞳_Official", "UP 名字进来了")
    ck(s2.get_up(280242571).get("face") == "https://f/1.jpg", "头像进来了")

    print("—— 不带旧基线（最关键的一条）——")
    up = s2.get_up(280242571)
    ck(not (up.get("video") or {}).get("bvid"), "UP 级的旧投稿基线没带过来")
    ck(not (up.get("dyn") or {}).get("id"), "UP 级的旧动态基线没带过来")
    ck(not (s2.baseline_of("G1", 280242571, "video") or {}),
       "按群的旧投稿基线没带过来")
    ck(not (s2.baseline_of("G1", 280242571, "dyn") or {}),
       "按群的旧动态基线没带过来")

    print("—— 已有数据不动 ——")
    s2.set_group_name("G1", "我改过的名字")
    s2.set_up_templates("G1", 280242571, {"video": "我自己写的文案"})
    s2.set_baseline_of("G1", 280242571, "video", bvid="BV_NEW", pubdate=999)
    r2 = s2.import_legacy_file(old)
    s3 = db.Store("state.json")
    ck((s3._cache["groups"].get("G1") or {}).get("name") == "我改过的名字",
       "群名没被旧的盖掉")
    ck(s3.get_up_templates("G1", 280242571).get("video") == "我自己写的文案",
       "文案没被旧的盖掉")
    ck((s3.baseline_of("G1", 280242571, "video") or {}).get("bvid") ==
       "BV_NEW", "基线没被旧的盖掉")
    ck(not (r2.get("report") or {}).get("groups", {}).get("added"),
       "二次导入没有新增群（幂等）")
    ck(not (r2.get("report") or {}).get("up_templates"),
       "二次导入没有动文案")

    print("—— 空字段才补 ——")
    # 新库里这个 UP 的头像为空 → 用旧库的补上
    s3.set_up_info(111, "伊万酱哒油", 0, "", 0)
    conn = db.Store("state.json")
    conn._conn().execute(
        "UPDATE ups SET face='https://f/2.jpg' WHERE uid=280242571")
    conn._conn().commit()
    conn._persist()
    s4 = db.Store("state.json")
    s4._conn().execute("UPDATE ups SET face='' WHERE uid=111")
    s4._conn().commit()
    s4.import_legacy_file(old)
    s5 = db.Store("state.json")
    ck(s5.get_up(280242571).get("face") == "https://f/2.jpg",
       "已有头像不被旧的改掉")

    print("—— 自动导入只做一次 ——")
    tmp2 = os.path.join(tmp, "home2")
    os.makedirs(tmp2, exist_ok=True)
    os.environ["BILI_NOTIFY_HOME"] = tmp2
    for m in ("db", "paths"):
        sys.modules.pop(m, None)
    import db as db2
    import shutil
    shutil.copy2(old, os.path.join(tmp2, "data.db"))
    cands = db2.scan_legacy_files()
    ck(len(cands) == 1, "扫描到 1 个旧数据文件")
    ck(cands[0].get("imported") is False, "首次扫描：未导入")
    done = db2.auto_import_legacy()
    ck(len(done) == 1, "自动导入了 1 份")
    st_after = db2.Store("state.json")
    ck(len(st_after._cache["groups"]) == 2, "自动导入后群进来了")
    ck(all(c.get("imported") for c in db2.scan_legacy_files()),
       "再扫描：已标记为导入过")
    ck(not db2.auto_import_legacy(), "再启动：不再重复导入")

    print("—— 旧格式 data.json ——")
    tmp3 = os.path.join(tmp, "home3")
    os.makedirs(tmp3, exist_ok=True)
    os.environ["BILI_NOTIFY_HOME"] = tmp3
    for m in ("db", "paths"):
        sys.modules.pop(m, None)
    import db as db3
    old_json = os.path.join(tmp3, "data.json")
    with open(old_json, "w", encoding="utf-8") as f:
        json.dump({"groups": {"G9": {"name": "JSON老群", "subs": {
            "555": {"uname": "老UP", "video": {"on": True}}}}},
            "ups": {"555": {"uname": "老UP", "face": "", "room_id": 0,
                            "video": {"bvid": "BV_json_old"}}}},
            f, ensure_ascii=False)
    s6 = db3.Store("state.json")
    r6 = s6.import_legacy_file(old_json)
    ck(r6.get("ok") is True, "旧格式 JSON 能导入")
    s7 = db3.Store("state.json")
    ck((s7._cache["groups"].get("G9") or {}).get("name") == "JSON老群",
       "JSON 老群的订阅进来了")
    ck(not (s7.get_up(555).get("video") or {}).get("bvid"),
       "旧 JSON 里的投稿基线没带过来")

    print("—— 现行数据文件不被当成旧库 ——")
    ck(not [c for c in db3.scan_legacy_files()
            if c["name"] == "state.json"], "state.json 不在候选里")

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
