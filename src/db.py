# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""SQLite 数据库存储：群列表、订阅关系、UP 主状态。

为什么从 JSON 换成 SQLite：
    1. JSON 是整个文件读、整个文件写，两个进程（机器人 / 面板）同时用容易互相覆盖
    2. 之前的 ensure_group 只在「传了群名」时才落盘，导致 @机器人 登记的群
       只存在于内存，面板永远看不到 —— 这类"改了但不存"的问题在 JSON 全量
       覆盖写模型下很容易再犯，SQLite 每次操作独立提交就没这个坑
    3. 有了表结构，群列表 / 订阅可以单独查询、排序、统计

表结构：
    groups(gid, name, created_at, updated_at)
    ups(uid, uname, face, room_id, live_status, live_title,
        video_bvid, dyn_id, updated_at)
    subs(gid, uid, kind, on, updated_at)   -- 主键 (gid, uid, kind)
    meta(key, value)                                -- 表结构与数据版本

自动刷新：
    面板改了数据后，机器人不需要重启。每次读操作前检查 meta.revision，
    变了就重新载入内存缓存（见 Store.refresh()）。
"""
import json
import os
import sqlite3
import threading
import time

from security import restrict_perms
import paths

# 说明：旧的 at_all（@全体）列已在 v1.18.0 移除。
# 群聊没有官方 @全体能力（内嵌 @everyone 仅文字子频道可用，
# 且机器人无法被设为管理员），功能整个移除，数据列一并清掉。
#
# top_comment = 置顶动态下的置顶评论，独立开关，
# 不想被置顶评论打扰的群可以只开 dynamic 不开这个。
KINDS = ("live", "video", "dynamic", "top_comment")
KIND_LABEL = {"live": "直播", "video": "视频", "dynamic": "动态",
              "top_comment": "置顶评论"}

# 「不填类型 = 全开」时只开这三类。
# 置顶评论要显式勾选 —— 它噪音相对大，默认塞给所有人会引起反感。
DEFAULT_KINDS = ("live", "video", "dynamic")

# ⚠️ 两套"类型名"不是一回事，混用会静默取到空值：
#   subs.kind        = 订阅开关用的名字（live / video / dynamic / top_comment）
#   sub_baseline.kind = 按群基线用的名字（live / video / dyn / dyn_top /
#                       dyn_top_cmt / dyn_seen）
#                       dyn_seen = 该群**真的推送过**的动态 ID，
#                       用来拦"新动态被置顶后又推一遍"。
# 动态尤其容易踩：订阅里叫 dynamic，基线里叫 dyn。
_BL_KIND_OF_SUB = {
    "live": "live",
    "video": "video",
    "dynamic": "dyn",
    "top_comment": "dyn_top_cmt",
}

# 关掉某个订阅再打开时，要一并作废的**基线名**（可能不止一条）：
#   dynamic → 普通动态 dyn / 置顶动态 dyn_top / 已提醒记录 dyn_seen
# ⚠️ 置顶动态没有独立开关，跟着 dynamic 一起作废，否则重新订阅后
#    置顶基线还是关之前那条，会把历史置顶当新内容再推一遍。
_BASELINE_KINDS_OF_SUB = {
    "live": ("live",),
    "video": ("video",),
    "dynamic": ("dyn", "dyn_top", "dyn_seen"),
    "top_comment": ("dyn_top_cmt",),
}

# 「这个群提醒过哪些动态」最多留几条。
# 留一串是为了拦"推过 A、又推过 D、UP 回头把 A 置顶"这种隔一轮的重复；
# 留太多没意义 —— UP 翻出几个月前的动态置顶本来就该重新提醒一次。
DYN_SEEN_KEEP = 20

SCHEMA_VERSION = "1"

SCHEMA = """
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


# ==================== 落盘层：JSON ====================
# ⚠️ 为什么落盘从 SQLite 换成 JSON：
#   1. data.db 是二进制，误删之后既看不了、改不了，也没法从文本里捞回来
#   2. SQLite 在部分文件系统上（网络盘 / 容器 / U 盘 / 某些虚拟磁盘）
#      执行 journal 相关操作会直接 disk I/O error —— 结果是**整个程序
#      起不来**，而不是"并发差一点"
#   3. 数据量其实很小（几千条订阅关系），JSON 明文完全够用：
#      可以直接打开看、手改，也能从备份里挑一份回来
#
# ⚠️ SQLite 并没有被扔掉，而是退到**内存里当工作引擎**：
#    现有所有 SQL 一行都不用改，只是落盘格式换了。
#      - 启动：JSON → 灌进内存 SQLite
#      - 每次写：内存 SQLite → 导出 JSON → 原子落盘
import glob
import logging as _logging
import shutil
import tempfile

_log = _logging.getLogger("bili-notify")

# 默认数据文件名
DATA_FILE_DEFAULT = "state.json"

# 备份保留份数
BACKUP_KEEP = 10
# 两次备份之间的最小间隔（秒）。写操作很频繁，不能每写一次就留一份。
BACKUP_MIN_INTERVAL = 300.0

_backup_last = {}


def data_home() -> str:
    """数据目录。统一由 paths.py 定义（默认在程序目录之外）。"""
    return paths.data_home()


def resolve_data_path(path: str = "") -> str:
    """把调用方给的路径落到 JSON 文件上。

    - 绝对路径 → 原地换后缀（测试传的临时目录照旧能用）
    - 只给文件名 → 放到数据目录（不再落在程序目录里）
    """
    p = str(path or DATA_FILE_DEFAULT)
    # 老布局留下的 data.db / data.json 一律归到同一个文件，
    # 否则面板和机器人各写各的，看着像"数据丢了"。
    if not os.path.dirname(p) and os.path.basename(p) in ("data.db", "data.json"):
        p = DATA_FILE_DEFAULT
    # 只给了文件名（没有任何目录分隔符）→ 放数据目录
    if not os.path.dirname(p):
        p = os.path.join(data_home(), os.path.basename(p))
    p = os.path.abspath(os.path.expanduser(p))
    root, ext = os.path.splitext(p)
    if ext.lower() in (".db", ".sqlite", ".sqlite3"):
        p = root + ".json"
    elif ext.lower() != ".json":
        p = p + ".json"
    return p


def data_path_from_cfg(cfg=None) -> str:
    """全局**唯一**的数据文件路径入口。面板 / 机器人 / 安全自检 / 启动自检
    都必须走这里。

    ⚠️ 为什么必须统一（v1.98.3 修的就是这个）：
    以前面板走 security.resolve_in_base()（相对路径拼到**源码目录**），
    机器人走 resolve_data_path()（相对路径拼到**数据目录**），两边算出
    的是两个不同的文件。实测三种 config 取值全都分裂：

        cfg data_file    面板实际读的              机器人实际读的
        "data.db"        <程序目录>/src/data.json   <数据目录>/state.json
        "state.json"     <程序目录>/src/state.json  <数据目录>/state.json
        （不填）         <程序目录>/src/state.json  <数据目录>/state.json

    后果：机器人照着数据目录那份在推（订阅、合集都在，日志里 6 位 UP、
    3 个合集静默对齐），面板读的是源码目录那份（空的），显示"还没有登记
    任何群"——订阅看不见、改不了、删不掉。而且启动自检补的群补在数据目录
    那份上，面板永远看不到，看着像"修了个寂寞"。
    """
    if cfg is None:
        try:
            from cfgutil import load_cfg
            cfg = load_cfg()
        except Exception:
            cfg = {}
    try:
        v = str((cfg or {}).get("data_file") or "").strip()
    except Exception:
        v = ""
    p = resolve_data_path(v or DATA_FILE_DEFAULT)
    # 兜底：万一解析结果落在源码目录 / 程序目录里（旧 config 里存了那个
    # 年代的绝对路径），一律拉回数据目录 —— 那正是"第二份数据文件"的源头。
    try:
        ap = os.path.abspath(p)
        for bad in (paths.src_dir(), paths.app_dir()):
            bd = os.path.abspath(bad)
            if ap == bd or ap.startswith(bd + os.sep):
                return resolve_data_path(DATA_FILE_DEFAULT)
    except Exception:
        pass
    return p


def _atomic_write_json(path: str, obj) -> None:
    """原子写：先写临时文件，再 os.replace 整体替换。

    ⚠️ 不能直接 open(path,'w')：写到一半进程被杀 / 断电，文件就成半截，
       JSON 解析失败 = 数据整个读不出来。os.replace 保证读到的一定是
       完整的新文件或完整旧文件。
    """
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise
    try:
        restrict_perms(path)
    except Exception:
        pass


def backup_dir(path: str) -> str:
    return os.path.join(os.path.dirname(path) or ".", "backups")


def _prune_backups(d: str, keep: int = BACKUP_KEEP) -> None:
    try:
        fs = sorted(glob.glob(os.path.join(d, "state-*.json")),
                    key=os.path.getmtime, reverse=True)
        for f in fs[keep:]:
            try:
                os.unlink(f)
            except Exception:
                pass
    except Exception:
        pass


def maybe_backup(path: str, min_interval: float = BACKUP_MIN_INTERVAL):
    """写之前留一份备份。返回备份路径，没留则为 None。"""
    if not os.path.exists(path):
        return None
    now = time.time()
    if now - _backup_last.get(path, 0) < min_interval:
        return None
    try:
        d = backup_dir(path)
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(
            d, "state-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
        if os.path.exists(dst):
            dst = os.path.join(d, "state-%s-%03d.json" % (
                time.strftime("%Y%m%d-%H%M%S"), int(now * 1000) % 1000))
        shutil.copy2(path, dst)
        _backup_last[path] = now
        _prune_backups(d)
        return dst
    except Exception:
        return None


def list_backups(path: str) -> list:
    """按时间倒序列出可用备份（最新的在前）。"""
    try:
        fs = sorted(glob.glob(os.path.join(backup_dir(path), "state-*.json")),
                    key=os.path.getmtime, reverse=True)
        return [{"path": f, "name": os.path.basename(f),
                 "mtime": os.path.getmtime(f),
                 "size": os.path.getsize(f)} for f in fs]
    except Exception:
        return []


_AUTO_RECOVERED = set()


def auto_recover(path: str = "") -> str:
    """启动时的一次性兜底：主数据文件没了 / 坏了，就从滚动备份回来一份。

    ⚠️ 为什么只在这里做、而且只做一次：
    「误删了数据文件」需要兜底，但「用户主动删掉想重来」不该被悄悄塞回来。
    放在启动流程里做一次，两个场景就能区分：删文件后重启 → 恢复一份；
    程序跑着的时候删 → 不会被硬塞回来。
    """
    p = resolve_data_path(path or DATA_FILE_DEFAULT)
    if p in _AUTO_RECOVERED:
        return ""
    _AUTO_RECOVERED.add(p)
    if os.path.exists(p) and _read_state(p) is not None:
        return ""
    src = restore_backup(p)
    if src:
        _log.warning("数据文件不见了，已自动从备份恢复：%s", src)
    return src


def restore_backup(path: str, backup_path: str = ""):
    """主文件丢了就从这个回来。返回恢复来源，没有可用备份则为 None。"""
    bks = list_backups(path)
    if not bks:
        return None
    src = backup_path or bks[0]["path"]
    if not os.path.exists(src):
        return None
    try:
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        shutil.copy2(src, path)
        return src
    except Exception:
        return None


def _read_state(path: str):
    """读 JSON 数据文件。不存在 / 损坏都返回 None（不抛）。"""
    try:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return None
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception as e:
        _log.warning("数据文件读不出来（%s）：%s", path, e)
        return None


def _file_sig(path: str):
    """文件指纹。变了就说明被别的进程改过，需要重新载入。"""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except Exception:
        return None


def _table_names(conn) -> list:
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()
        return [r["name"] if isinstance(r, sqlite3.Row) else r[0]
                for r in rows]
    except Exception:
        return []


def _export_state(conn) -> dict:
    """整库导出成可 JSON 化的 dict。

    通用做法：直接读 sqlite_master 拿所有表，不写死表名 ——
    以后新增表不用改这里。
    """
    out = {}
    for t in _table_names(conn):
        try:
            cur = conn.execute('SELECT * FROM "%s"' % t)
            cols = [d[0] for d in cur.description]
            out[t] = [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            continue
    return out


def _clear_tables(conn) -> None:
    for t in _table_names(conn):
        try:
            conn.execute('DELETE FROM "%s"' % t)
        except Exception:
            continue


def _import_state(conn, state) -> None:
    """把 dict 灌回内存库。"""
    if not isinstance(state, dict) or not state:
        return
    for t, rows in state.items():
        if not isinstance(rows, list) or not rows:
            continue
        t = str(t)
        try:
            cols = [c["name"] for c in
                    conn.execute('PRAGMA table_info("%s")' % t)]
        except Exception:
            continue
        if not cols:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            # 只灌表里真实存在的列：旧备份可能带着已被删掉的字段
            use = [c for c in cols if c in row]
            if not use:
                continue
            try:
                conn.execute(
                    'INSERT OR REPLACE INTO "%s" (%s) VALUES (%s)' % (
                        t, ",".join('"%s"' % c for c in use),
                        ",".join("?" * len(use))),
                    [row[c] for c in use])
            except Exception:
                continue


def _sqlite_to_json(src: str, dst: str) -> int:
    """把旧版 data.db 导出成新的 JSON 数据文件。"""
    conn = sqlite3.connect(src)
    conn.row_factory = sqlite3.Row
    try:
        st = _export_state(conn)
    finally:
        conn.close()
    _atomic_write_json(dst, st)
    return len(st)


def migrate_legacy(prog_dir: str, path: str = DATA_FILE_DEFAULT) -> str:
    """把程序目录里的旧数据搬到新的数据目录（只搬一次）。

    搬完旧文件**保留不动** —— 万一新位置出问题，还能从旧的找回来。
    """
    home_path = resolve_data_path(path)
    try:
        if os.path.exists(home_path) and os.path.getsize(home_path) > 0:
            return home_path
    except Exception:
        pass
    for old in ("data.db", "data.json"):
        src = os.path.join(prog_dir, old)
        try:
            if not os.path.exists(src) or os.path.getsize(src) == 0:
                continue
        except Exception:
            continue
        try:
            if old.endswith(".db"):
                _sqlite_to_json(src, home_path)
            else:
                os.makedirs(os.path.dirname(home_path) or ".",
                            exist_ok=True)
                shutil.copy2(src, home_path)
            _log.info("旧数据已搬到新位置：%s → %s", src, home_path)
            return home_path
        except Exception as e:
            _log.warning("旧数据 %s 搬运失败：%s", src, e)
    return home_path


_UPS_EXTRA_COLS = {"dyn_top_id": "TEXT NOT NULL DEFAULT ''",
                   "dyn_top_cmt": "TEXT NOT NULL DEFAULT ''",
                   "short_room_id": "INTEGER NOT NULL DEFAULT 0"}

# 合集表补一列「UP 主名字」。
# ⚠️ 以前合集显示的是 uid：名字只从 ups 缓存里取，而 ups 缓存
#    只给**订阅过 UP 主**的人填。只订了合集、没订 UP 主的，
#    缓存里根本没有这一条 → 界面上就显示成数字 uid。
_SEASONS_EXTRA_COLS = {"uname": "TEXT NOT NULL DEFAULT ''",
                       "cover": "TEXT NOT NULL DEFAULT ''"}


def _ensure_columns(conn):
    """旧库缺列时自动补上（升级不丢数据）。"""
    try:
        have = {r["name"] for r in conn.execute("PRAGMA table_info(ups)")}
        for col, ddl in _UPS_EXTRA_COLS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE ups ADD COLUMN {col} {ddl}")
    except Exception:
        pass
    try:
        have = {r["name"] for r in conn.execute("PRAGMA table_info(seasons)")}
        for col, ddl in _SEASONS_EXTRA_COLS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE seasons ADD COLUMN {col} {ddl}")
    except Exception:
        pass


def _drop_at_all_column(conn):
    """移除 subs 表的 at_all 列（v1.18.0）。

    SQLite 老版本不支持 DROP COLUMN，且这里要保住订阅关系，
    所以用「建新表 → 复制 → 删旧表 → 改名」的方式重建。
    """
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(subs)")}
    except Exception:
        return
    if "at_all" not in cols:
        return
    try:
        conn.executescript(
            "CREATE TABLE subs_new ("
            "gid TEXT NOT NULL, uid INTEGER NOT NULL, kind TEXT NOT NULL, "
            '"on" INTEGER NOT NULL DEFAULT 0, updated_at REAL, '
            "PRIMARY KEY (gid, uid, kind));")
        conn.execute(
            'INSERT INTO subs_new(gid, uid, kind, "on", updated_at) '
            'SELECT gid, uid, kind, "on", updated_at FROM subs')
        conn.execute("DROP TABLE subs")
        conn.execute("ALTER TABLE subs_new RENAME TO subs")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_subs_uid_kind "
                     "ON subs(uid, kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_subs_kind ON subs(kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_subs_gid ON subs(gid)")
    except Exception:
        pass


def _json_list(raw) -> list:
    """把库里的 JSON 字符串还原成列表；坏数据返回空列表，不抛异常。"""
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _now():
    return time.time()


def _as_int(v) -> int:
    """把任意值安全地转成非负整数，失败返回 0。

    基线的发布时间是从接口里取的，取不到就是 0，
    绝不能因为一个脏值让整条检测抛异常。
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _as_dict(v) -> dict:
    """把任意值安全转成 dict，非 dict 一律返回 {}。

    ⚠️ 为什么需要这个：历史数据里混进过字符串（比如某个订阅项或
    UP 主缓存被写成了字符串而不是字典）。凡是 `x.get(...)` 的地方，
    x 一旦是 str 就会抛
        'str' object has no attribute 'get'
    而这个异常发生在轮询主循环里，会让**整轮检测全部停摆**——
    所有 UP 主的直播/投稿/动态都不再检测，且日志只有一句话，
    看不出是哪一位 UP 主、哪一步出的问题。

    所以在这里统一兜底：读出来的东西不是 dict 就当空处理，
    宁可这一项检测不到，也不能让整条检测链挂掉。
    """
    return v if isinstance(v, dict) else {}


def _as_conf(v) -> dict:
    """订阅项里某个提醒类型的配置（{"on": True/False}）。

    和 _as_dict 同理，但语义更明确：拿到非 dict 就当作"未开启"。
    """
    return v if isinstance(v, dict) else {}


class Store:
    """存储：落盘用 JSON，SQLite 退到内存里当工作引擎。

    对外接口与旧版完全一致，调用方无需改动。
    """

    def __init__(self, path: str = DATA_FILE_DEFAULT):
        # 兼容：调用方可能还传 .db，自动换成 .json
        self.path = resolve_data_path(path)
        self._lock = threading.RLock()
        self._mem = None
        self._revision = -1
        self._file_sig = None
        self._loaded = False
        self._cache = {"groups": {}, "ups": {}}
        self._init_db()
        moved = self._migrate_group_tpl_to_up()
        if moved:
            self._persist()
        self.refresh()

    def _migrate_group_tpl_to_up(self) -> int:
        """把「群专属文案」摊到这个群订阅的每个 UP 主上，然后废弃那一层。

        ⚠️ 为什么要迁移（而不是直接删掉）：
        v1.30.0 把文案入口挪到了每个订阅项后面，群级那一层**没有编辑入口**
        了 —— 但老用户库里还存着改过的群级文案，直接删等于把人家写好的
        东西无声丢掉。所以升级时摊下去：这个群订阅的每个 UP 主都拿到一份。

        ⚠️ 已经自己改过文案的 UP 主**不动**（尊重更细的那一层）。
        """
        try:
            conn = self._conn()
            # 只在老表里真有数据时才跑
            rows = conn.execute("SELECT gid, kind, content FROM "
                                "group_templates").fetchall()
            if not rows:
                return 0
            by_gid = {}
            for r in rows:
                by_gid.setdefault(r["gid"], {})[r["kind"]] = r["content"]
            now = _now()
            moved = 0
            for gid, mapping in by_gid.items():
                # 这个群订阅了哪些 UP 主
                subs = conn.execute(
                    "SELECT DISTINCT uid FROM subs WHERE gid=?",
                    (gid,)).fetchall()
                for row in subs:
                    uid = int(row["uid"])
                    for kind, content in mapping.items():
                        if not content:
                            continue
                        # 已有自己的文案 → 不动
                        exist = conn.execute(
                            "SELECT content FROM up_templates WHERE gid=? "
                            "AND uid=? AND kind=?", (gid, uid, kind)).fetchone()
                        if exist is not None:
                            continue
                        conn.execute(
                            "INSERT INTO up_templates(gid, uid, kind, content, "
                            "updated_at) VALUES(?,?,?,?,?) "
                            "ON CONFLICT(gid,uid,kind) DO UPDATE SET "
                            "content=excluded.content, "
                            "updated_at=excluded.updated_at",
                            (gid, uid, kind, content, now))
                        moved += 1
            # 摊完就清掉旧表，避免留下没有入口的孤儿数据
            conn.execute("DELETE FROM group_templates")
            conn.commit()
            return moved
        except Exception:
            return 0

    # ---------- 连接 ----------
    def _conn(self):
        """内存工作库（全局共用一个）。

        ⚠️ 不能像文件版那样用 threading.local 给每线程开一个连接：
        内存库建出来是空的，每线程一个 = 每线程一份互不相干的数据，
        彼此的修改谁也看不见。所以全局共用一个，靠 _lock 保护。
        """
        if self._mem is None:
            with self._lock:
                if self._mem is None:
                    self._mem = sqlite3.connect(":memory:",
                                                check_same_thread=False)
                    self._mem.row_factory = sqlite3.Row
        return self._mem

    def _init_db(self):
        with self._lock:
            conn = self._conn()
            conn.executescript(SCHEMA)
            _ensure_columns(conn)
            _drop_at_all_column(conn)
            conn.execute("INSERT OR IGNORE INTO meta(key, value) "
                         "VALUES('schema_version', ?)", (SCHEMA_VERSION,))
            conn.execute("INSERT OR IGNORE INTO meta(key, value) "
                         "VALUES('revision', '0')")
            # ⚠️ 这里**不做**自动恢复。曾经放在这儿，结果：
            #   1) 每次 new Store 都会触发一遍，测试里删文件想重置也重置不掉；
            #   2) 更糟的是"数据文件被误删"和"用户主动清空"无法区分，
            #      想彻底重来的人删了文件又被悄悄塞回来。
            #   自动恢复改由启动流程调一次 auto_recover()（见下）。
            st = _read_state(self.path)
            if st:
                _import_state(conn, st)
            conn.commit()
            self._file_sig = _file_sig(self.path)

    # ---------- 版本与缓存 ----------
    def _bump(self, conn=None):
        """数据变更时递增 revision，让其它进程感知到。"""
        c = conn or self._conn()
        c.execute("UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 "
                  "AS TEXT) WHERE key='revision'")
        r = c.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
        if r:
            self._revision = int(r[0])

    def _current_revision(self) -> int:
        try:
            r = self._conn().execute(
                "SELECT value FROM meta WHERE key='revision'").fetchone()
            return int(r[0]) if r else 0
        except Exception:
            return 0

    def refresh(self):
        """读操作前调用：如果别的进程改了数据，重新载入内存缓存。

        这就是「会自己刷新」——面板里新增/修改订阅后，机器人下一轮轮询
        就能感知到，不需要重启机器人。

        ⚠️ 判定依据是**数据文件的指纹**（mtime+大小）而不是 meta.revision：
        两个进程各有一份内存库，revision 只在自己库里递增，对方看不见；
        真正能被两边同时观察到的只有文件本身。
        """
        with self._lock:
            sig = _file_sig(self.path)
            if self._loaded and sig is not None and sig == self._file_sig:
                return
            if sig is not None:
                # 文件被别的进程改过 → 整库重建再载入
                st = _read_state(self.path)
                if st:
                    conn = self._conn()
                    _clear_tables(conn)
                    _import_state(conn, st)
                    conn.commit()
            self._file_sig = sig
            self._load_cache()
            self._revision = self._current_revision()
            self._loaded = True

    def _load_cache(self):
        conn = self._conn()
        groups = {}
        for row in conn.execute(
                "SELECT gid, name FROM groups ORDER BY created_at"):
            groups[row["gid"]] = {"name": row["name"] or "", "subs": {}}
        for row in conn.execute(
                'SELECT gid, uid, kind, "on" FROM subs '
                'WHERE "on"=1 ORDER BY uid'):
            g = groups.setdefault(row["gid"], {"name": "", "subs": {}})
            item = g["subs"].setdefault(str(row["uid"]), {"uname": ""})
            item[row["kind"]] = {"on": bool(row["on"])}
        # 补齐未开启的类型键，避免调用方 KeyError
        for g in groups.values():
            for item in g["subs"].values():
                for k in KINDS:
                    item.setdefault(k, {"on": False})

        ups = {}
        for row in conn.execute("SELECT * FROM ups"):
            ups[str(row["uid"])] = {
                "uname": row["uname"] or "",
                "face": row["face"] or "",
                "room_id": row["room_id"] or 0,
                "short_room_id": row["short_room_id"] or 0,
                "live": {"status": row["live_status"],
                         "title": row["live_title"] or ""},
                "video": {"bvid": row["video_bvid"] or ""},
                "dyn": {"id": row["dyn_id"] or ""},
                "dyn_top": {"id": row["dyn_top_id"] or ""},
                "dyn_top_cmt": {"id": row["dyn_top_cmt"] or ""},
            }
        # 把 UP 主昵称回填到订阅项里。
        # 之前这里缺一步：建订阅项时 uname 写死成 ""，
        # 而昵称在后面才从 ups 表读出来（且存进另一个 dict），
        # 结果「列表」指令和面板永远显示 UID 而不是名字。
        for g in groups.values():
            for uid_str, item in g["subs"].items():
                if not item.get("uname"):
                    item["uname"] = (ups.get(uid_str) or {}).get("uname", "")

        self._cache = {"groups": groups, "ups": ups}

    def _persist(self) -> bool:
        """内存库 → JSON 落盘（唯一的持久化出口）。

        ⚠️ 为什么必须有这一步：SQLite 现在只是**内存里的工作引擎**，
           它本身不落任何盘。以前这个方法只有调用、没有实现，
           于是所有订阅/文案/基线只活在进程内存里 —— 重启即空，
           表现就是"数据莫名其妙没了 / 好像被误删了"。

        落盘用原子写（临时文件 + os.replace），中途被杀也不会留下半截文件。
        """
        with self._lock:
            try:
                # 写新文件**之前**先留一份旧数据，写坏了还能回滚
                maybe_backup(self.path)
                st = _export_state(self._conn())
                _atomic_write_json(self.path, st)
                self._file_sig = _file_sig(self.path)
                # 第一次写数据时文件还不存在，"写前留旧"会跳过，
                # 于是第一份数据没有任何备份可回滚 —— 写完后立刻补一份。
                if not _backup_last.get(self.path):
                    maybe_backup(self.path, 0)
                return True
            except Exception as e:
                _log.error("数据落盘失败（%s）：%s", self.path, e)
                return False

    def _write(self, fn):
        """统一的写入包装：吸收外部改动 → 执行 → 递增 → 提交 → 落盘。"""
        with self._lock:
            # 先吸收别的进程写进文件的内容。
            # ⚠️ 落盘是整库导出，不先合并就会把对方的改动整个盖掉。
            #    refresh 内部仅在文件指纹变化时才真正重载，开销可忽略。
            self.refresh()
            conn = self._conn()
            try:
                fn(conn)
                self._bump(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            self._load_cache()
            self._revision = self._current_revision()
            self._persist()

    @property
    def data(self) -> dict:
        """兼容旧接口：以字典形式暴露当前快照。

        每次访问都先 refresh，保证拿到的是最新数据。
        """
        self.refresh()
        return self._cache

    # ---------- 兼容旧 API ----------
    def load(self):
        self.refresh()

    def save(self):
        """兼容旧调用（旧代码会在修改后调 save）。

        SQLite 版每次操作都已提交，这里只需同步一次缓存。
        """
        self.refresh()

    # ---------- 群 ----------
    def ensure_group(self, gid: str, name: str = ""):
        """登记群。**无论有没有传名字都会落盘**。

        旧实现把 save() 写在 `if name:` 里，@机器人 不带名字登记时
        只改内存不落盘，面板永远看不到 —— 这是之前 @不登记 的根因。
        """
        if not gid:
            return {"name": "", "subs": {}}
        now = _now()

        def _fn(conn):
            conn.execute(
                "INSERT INTO groups(gid, name, created_at, updated_at) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(gid) DO UPDATE SET "
                "  name = CASE WHEN groups.name='' AND excluded.name<>'' "
                "              THEN excluded.name ELSE groups.name END,"
                "  updated_at = excluded.updated_at",
                (gid, name or "", now, now))
        self._write(_fn)
        return self.get_group(gid)

    def remove_group(self, gid: str) -> dict:
        """删除一个群，以及**这个群独有的一切数据**。

        机器人被移出群聊时调用。留在库里只会变成垃圾：面板里显示一个
        永远收不到消息的群，订阅照旧往那推然后全部失败。

        ⚠️ 只删跟群绑定的东西：
             groups / subs / seasons / group_templates / season_templates
        ⚠️ **不删 ups 表** —— UP 主资料是全局的，别的群可能还订阅着。

        返回清掉了多少，给日志用。
        """
        if not gid:
            return {"subs": 0, "seasons": 0, "tpl": 0, "stpl": 0,
                    "up_tpl": 0}
        counts = {}

        def _fn(conn):
            counts["subs"] = conn.execute(
                "DELETE FROM subs WHERE gid=?", (gid,)).rowcount
            counts["seasons"] = conn.execute(
                "DELETE FROM seasons WHERE gid=?", (gid,)).rowcount
            counts["stpl"] = conn.execute(
                "DELETE FROM season_templates WHERE gid=?", (gid,)).rowcount
            counts["tpl"] = conn.execute(
                "DELETE FROM group_templates WHERE gid=?", (gid,)).rowcount
            counts["up_tpl"] = conn.execute(
                "DELETE FROM up_templates WHERE gid=?", (gid,)).rowcount
            conn.execute("DELETE FROM groups WHERE gid=?", (gid,))
        self._write(_fn)
        self.refresh()
        return counts

    def get_group(self, gid: str) -> dict:
        self.refresh()
        return _as_dict(self._cache["groups"].get(gid)) or {
            "name": "", "subs": {}}

    def set_group_name(self, gid: str, name: str):
        now = _now()

        def _fn(conn):
            conn.execute(
                "INSERT INTO groups(gid, name, created_at, updated_at) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(gid) DO UPDATE SET name=excluded.name, "
                "updated_at=excluded.updated_at",
                (gid, name or "", now, now))
        self._write(_fn)

    def all_groups(self) -> list:
        self.refresh()
        return list(self._cache["groups"].keys())

    def groups_with_meta(self) -> list:
        """带登记时间的群列表，面板展示用。"""
        self.refresh()
        rows = self._conn().execute(
            "SELECT gid, name, created_at, updated_at FROM groups "
            "ORDER BY created_at").fetchall()
        out = []
        for r in rows:
            subs = self._cache["groups"].get(r["gid"], {}).get("subs", {})
            out.append({
                "gid": r["gid"],
                "name": r["name"] or "",
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "sub_count": len(subs),
            })
        return out

    # ---------- 订阅 ----------
    def set_sub(self, gid: str, uid: int, kind: str, on: bool,
                uname: str = ""):
        """设置某群对某 UP 主某类型的订阅开关。

        ⚠️ **关→开**时会清掉这个群这个类型的基线。
        重新开启等于"重新订阅"：期间 UP 主发过什么，对这个群来说都算
        订阅之前的事，不该补推。留着旧进度就会把旧内容当成新内容推一遍。
        开→开（重复保存）不动基线，否则每次保存设置都会重新对齐。
        """
        if kind not in KINDS:
            return
        now = _now()
        uid = int(uid)
        on = bool(on)

        def _fn(conn):
            conn.execute(
                "INSERT INTO groups(gid, name, created_at, updated_at) "
                "VALUES(?,'',?,?) ON CONFLICT(gid) DO NOTHING",
                (gid, now, now))
            prev = conn.execute(
                "SELECT \"on\" FROM subs WHERE gid=? AND uid=? AND kind=?",
                (gid, uid, kind)).fetchone()
            was_on = bool(prev and int(prev["on"] or 0))
            conn.execute(
                "INSERT INTO subs(gid, uid, kind, \"on\", updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(gid, uid, kind) DO UPDATE SET "
                "\"on\"=excluded.\"on\", updated_at=excluded.updated_at",
                (gid, uid, kind, int(on), now))
            # 关→开：旧进度作废，让它下次静默重新对齐
            if on and not was_on:
                # ⚠️ 必须按**基线名**删，不能用订阅名：
                # 订阅里叫 dynamic，基线里叫 dyn，照订阅名直接删等于
                # 一行都删不掉 —— "关掉再打开"本该重新对齐，却继承了
                # 关之前的进度。dyn_seen（已提醒记录）也要一并清，
                # 否则重新订阅后那条动态会被当成"提醒过"而永远不推。
                for bk in _BASELINE_KINDS_OF_SUB.get(kind, ()):
                    conn.execute(
                        "DELETE FROM sub_baseline WHERE gid=? AND uid=? "
                        "AND kind=?", (gid, uid, bk))
        self._write(_fn)

    def set_all_kinds(self, gid: str, uid: int, on: bool, uname: str = ""):
        now = _now()
        uid = int(uid)
        kinds = list(DEFAULT_KINDS)

        def _fn(conn):
            conn.execute(
                "INSERT INTO groups(gid, name, created_at, updated_at) "
                "VALUES(?,'',?,?) ON CONFLICT(gid) DO NOTHING",
                (gid, now, now))
            for k in kinds:
                conn.execute(
                    "INSERT INTO subs(gid, uid, kind, \"on\", updated_at) "
                    "VALUES(?,?,?,?,?) "
                    'ON CONFLICT(gid,uid,kind) DO UPDATE SET "on"=excluded."on", '
                    "updated_at=excluded.updated_at",
                    (gid, uid, k, int(bool(on)), now))
        self._write(_fn)

    # ---------- 视频合集订阅 ----------
    def add_season(self, gid: str, uid: int, season_id: int,
                   title: str = "", uname: str = "", cover: str = "") -> bool:
        """订阅一个合集。

        同一个 UP 主可以订阅**多个**合集（录播一个、切片一个），
        主键是 (群, UP主, 合集)，互不冲突，可以无限加。

        uname：订阅时就记下 UP 主名字。只订合集、没订 UP 主时
        ups 缓存里没有这一条，界面上就会显示成 uid。
        """
        now = _now()
        uid, season_id = int(uid), int(season_id)
        uname = str(uname or "")[:64]
        cover = str(cover or "").strip()[:512]

        def _fn(conn):
            conn.execute(
                "INSERT INTO groups(gid, name, created_at, updated_at) "
                "VALUES(?,'',?,?) ON CONFLICT(gid) DO NOTHING",
                (gid, now, now))
            conn.execute(
                'INSERT INTO seasons(gid, uid, season_id, title, "on", '
                "known, uname, cover, created_at, updated_at) "
                "VALUES(?,?,?,?,1,'[]',?,?,?,?) "
                "ON CONFLICT(gid,uid,season_id) DO UPDATE SET "
                "title=CASE WHEN excluded.title<>'' THEN excluded.title "
                "ELSE seasons.title END, "
                "uname=CASE WHEN excluded.uname<>'' THEN excluded.uname "
                "ELSE seasons.uname END, "
                "cover=CASE WHEN excluded.cover<>'' AND excluded.cover"
                "<>COALESCE(seasons.cover,'') THEN excluded.cover "
                "ELSE seasons.cover END, updated_at=excluded.updated_at",
                (gid, uid, season_id, str(title or ""), uname, cover,
                 now, now))
        self._write(_fn)
        return True

    def remove_season(self, gid: str, uid: int, season_id: int) -> bool:
        def _fn(conn):
            conn.execute(
                "DELETE FROM seasons WHERE gid=? AND uid=? AND season_id=?",
                (gid, int(uid), int(season_id)))
            conn.execute(
                "DELETE FROM season_templates WHERE gid=? AND uid=? "
                "AND season_id=?", (gid, int(uid), int(season_id)))
        self._write(_fn)
        return True

    def set_season_on(self, gid: str, uid: int, season_id: int, on: bool):
        now = _now()

        def _fn(conn):
            conn.execute(
                'UPDATE seasons SET "on"=?, updated_at=? '
                "WHERE gid=? AND uid=? AND season_id=?",
                (int(bool(on)), now, gid, int(uid), int(season_id)))
        self._write(_fn)

    def seasons_of_group(self, gid: str) -> list:
        """某群订阅的所有合集。

        uname 是订阅时记下的 UP 主名字 —— 只订合集没订 UP 主时，
        ups 缓存里没有这一条，只有它能填上名字。
        """
        self.refresh()
        rows = self._conn().execute(
            'SELECT uid, season_id, title, "on", known, uname, cover '
            'FROM seasons '
            "WHERE gid=? ORDER BY uid, season_id", (gid,)).fetchall()
        return [{"uid": r["uid"], "season_id": r["season_id"],
                 "title": r["title"], "on": bool(r["on"]),
                 "uname": r["uname"] if "uname" in r.keys() else "",
                 "cover": r["cover"] if "cover" in r.keys() else "",
                 "known_count": len(_json_list(r["known"]))} for r in rows]

    def set_season_uname(self, uid: int, uname: str) -> int:
        """给某个 UP 主的所有合集补上名字（旧数据没存时的补救）。"""
        def _fn(conn):
            cur = conn.execute(
                "UPDATE seasons SET uname=? WHERE uid=? AND uname=''",
                (str(uname or "")[:64], int(uid)))
            return cur.rowcount or 0
        try:
            return int(self._write(_fn) or 0)
        except Exception:
            return 0

    def all_season_keys(self) -> list:
        """所有**开着**的合集订阅，去重后 [(gid, uid, season_id)]。

        去重很关键：多个群订阅同一个合集时只查一次接口，
        再逐个群判断要不要推 —— 否则每多一个群就多一倍请求。
        """
        self.refresh()
        rows = self._conn().execute(
            'SELECT DISTINCT gid, uid, season_id FROM seasons WHERE "on"=1'
        ).fetchall()
        return [(r["gid"], int(r["uid"]), int(r["season_id"])) for r in rows]

    def sub_stats(self) -> dict:
        """去重后的订阅统计 —— 面板「订阅」卡片用这个数，不自己数。

        ⚠️ 为什么必须有这个方法（以前是前端按 groups 现算的，两处口径不一）：

          1) 合集以前按 "uid:season_id" 拼字符串去重。同一个合集只要在
             某个群里没存 uid（uid=0，旧数据常见），就变成 ':3175959' 和
             '123:3175959' 两个键 —— **一个合集被数成两个**，卡片数字虚高，
             「按合集筛群」的下拉里同一个合集也列两遍。
             B 站 season_id 是全局唯一的，直接按它去重最准。

          2) 前端 uid 一律按字符串收，非数字的坏值也算一个 UP 主；
             机器人侧 all_ups() 只认能 int() 的 —— 于是面板显示 7 位、
             日志说 6 位，两边对不上。这里跟 all_ups() 走同一套口径。

        ⚠️ 只数**开着**的订阅：seasons 带 "on" 条件；subs 的缓存本来就
           只装 "on"=1 的行。
        """
        self.refresh()
        ups = set()
        try:
            for u in self.all_ups():
                u = int(u)
                if u > 0:                      # 0 是"没存 uid"的坏值，不算
                    ups.add(u)
        except Exception:
            ups = set()
        seas = {}
        try:
            rows = self._conn().execute(
                'SELECT uid, season_id FROM seasons WHERE "on"=1').fetchall()
        except Exception:
            rows = []
        for r in rows:
            try:
                sid = int(r["season_id"])
            except (TypeError, ValueError):
                continue
            if sid <= 0:
                continue
            try:
                uid = int(r["uid"] or 0)
            except (TypeError, ValueError):
                uid = 0
            prev = seas.get(sid)
            # 同一个合集被多个群订阅：优先保留带 uid 的那条，
            # 缺 uid 的记录不覆盖有 uid 的（uid 只用来回显 UP 主名）
            if prev is None or (prev[0] == 0 and uid > 0):
                seas[sid] = (uid, sid)
        return {"ups": len(ups), "seasons": len(seas),
                "total": len(ups) + len(seas)}

    def get_season_known(self, gid: str, uid: int, season_id: int) -> list:
        self.refresh()
        r = self._conn().execute(
            "SELECT known FROM seasons WHERE gid=? AND uid=? AND season_id=?",
            (gid, int(uid), int(season_id))).fetchone()
        return _json_list(r["known"]) if r else []

    def get_season_updated_at(self, gid: str, uid: int, season_id: int) -> int:
        """这个合集**上次写基线**的时间（秒）。

        用它判断"这次扫到的视频到底是不是刚发的"：
        分页修好之前，合集基线可能只记了最老那几十个视频，
        修好后会突然多出一大堆"没见过但其实很早就有"的视频。
        不加这道判断的话，升级后会一口气把它们全当新视频推一遍。
        """
        self.refresh()
        r = self._conn().execute(
            "SELECT updated_at FROM seasons WHERE gid=? AND uid=? "
            "AND season_id=?", (gid, int(uid), int(season_id))).fetchone()
        try:
            return int(r["updated_at"] or 0) if r else 0
        except Exception:
            return 0

    def set_season_known(self, gid: str, uid: int, season_id: int,
                         bvids: list):
        """写回基线：这个合集已经见过哪些视频。

        ⚠️ 只保留最近 300 个 —— 合集可能有几百个视频，
        全存没必要，够判断"哪些是新的"就行。
        ⚠️ 上限必须**明显大于**一次能取到的视频数（season_max_pages × 30）：
        以前是 100，而分页修好后一次可能带回 90 条，
        截断后早先见过的会被挤出去，下次又被当成"新视频"重复推。
        """
        now = _now()
        # dict.fromkeys 去重且保序，再取末尾
        keep = list(dict.fromkeys(
            str(b) for b in (bvids or []) if b))[-300:]
        data = json.dumps(keep, ensure_ascii=False)

        def _fn(conn):
            conn.execute(
                "UPDATE seasons SET known=?, updated_at=? "
                "WHERE gid=? AND uid=? AND season_id=?",
                (data, now, gid, int(uid), int(season_id)))
        self._write(_fn)

    def set_season_title(self, gid: str, uid: int, season_id: int, title: str):
        """只在合集名还空的时候写入（不覆盖已有名字）。"""
        now = _now()

        def _fn(conn):
            conn.execute(
                "UPDATE seasons SET title=?, updated_at=? "
                "WHERE gid=? AND uid=? AND season_id=? AND title=''",
                (str(title or ""), now, gid, int(uid), int(season_id)))
        self._write(_fn)

    def set_season_cover(self, gid: str, uid: int, season_id: int,
                         cover: str) -> None:
        """记下合集封面（面板里当合集头像用，裁成 1:1 显示）。

        ⚠️ 只在**还没记过**时写入：UP 主换封面是常事，每轮检测都覆盖
        会白写；但第一次拿到必须落盘，否则老订阅永远没有封面。
        """
        cover = str(cover or "").strip()[:512]
        if not cover:
            return
        now = _now()

        def _fn(conn):
            conn.execute(
                "UPDATE seasons SET cover=?, updated_at=? "
                "WHERE gid=? AND uid=? AND season_id=? "
                "AND (cover IS NULL OR cover='')",
                (cover, now, gid, int(uid), int(season_id)))
        try:
            self._write(_fn)
        except Exception:
            pass

    def season_cover(self, uid: int, season_id: int) -> str:
        """取某个合集的封面（任一订阅它的群记下过就行）。"""
        self.refresh()
        try:
            row = self._conn().execute(
                "SELECT cover FROM seasons WHERE uid=? AND season_id=? "
                "AND cover<>'' ORDER BY updated_at DESC LIMIT 1",
                (int(uid), int(season_id))).fetchone()
        except Exception:
            return ""
        return str(row["cover"] or "") if row else ""

    def copy_seasons(self, src_gid: str, dst_gid: str, move: bool = False,
                     uids=None, season_ids=None) -> int:
        """把一个群的合集订阅复制/迁移到另一个群（含各自专属文案）。

        uids：只搬这几个 UP 主的合集（None = 全部）。
        season_ids：只搬这几个合集（None = 不过滤；空集合 = 明确一个都不搬）。
        ⚠️ 传了 season_ids 就**不再按 UP 过滤** —— 合集可能属于没勾的 UP，
           两个条件叠加会把该搬的筛掉。
        ⚠️「选择迁移」时源群只删搬走的合集，没勾选的 UP 不受影响。
        """
        self.refresh()
        want = None
        if uids is not None:
            want = set()
            for u in uids:
                try:
                    want.add(int(u))
                except (TypeError, ValueError):
                    continue
            if not want:
                return 0
        # ⚠️ uname / cover 也要一起选中：以前只选了前五列，
        #    搬过去后新群里的合集**丢掉了 UP 主名字**（界面又变成一串
        #    数字 uid）和封面。
        rows = self._conn().execute(
            'SELECT uid, season_id, title, "on", known, uname, cover '
            'FROM seasons '
            "WHERE gid=?", (src_gid,)).fetchall()
        def _ok_uid(r):
            if want is None:
                return True
            try:
                return int(r["uid"]) in want
            except (TypeError, ValueError):
                return False
        rows = [r for r in rows if _ok_uid(r)]
        if season_ids is not None:
            want_ids = set()
            for sid in season_ids:
                try:
                    want_ids.add(int(sid))
                except (TypeError, ValueError):
                    continue
            if not want_ids:
                return 0        # 一个合集都没勾 → 明确不搬
            def _ok_sid(r):
                try:
                    return int(r["season_id"]) in want_ids
                except (TypeError, ValueError):
                    return False
            rows = [r for r in rows if _ok_sid(r)]
        if not rows:
            return 0
        now = _now()

        def _fn(conn):
            conn.execute(
                "INSERT INTO groups(gid, name, created_at, updated_at) "
                "VALUES(?,'',?,?) ON CONFLICT(gid) DO NOTHING",
                (dst_gid, now, now))
            for r in rows:
                conn.execute(
                    'INSERT INTO seasons(gid, uid, season_id, title, "on", '
                    "known, uname, cover, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(gid,uid,season_id) DO UPDATE SET "
                    'title=excluded.title, "on"=excluded."on", '
                    "known=excluded.known, "
                    "uname=CASE WHEN excluded.uname<>'' THEN excluded.uname "
                    "ELSE seasons.uname END, "
                    "cover=CASE WHEN excluded.cover<>'' THEN excluded.cover "
                    "ELSE seasons.cover END, "
                    "updated_at=excluded.updated_at",
                    (dst_gid, r["uid"], r["season_id"], r["title"], r["on"],
                     r["known"],
                     r["uname"] if "uname" in r.keys() else "",
                     r["cover"] if "cover" in r.keys() else "",
                     now, now))
                for t in self._conn().execute(
                        "SELECT kind, content FROM season_templates "
                        "WHERE gid=? AND uid=? AND season_id=?",
                        (src_gid, r["uid"], r["season_id"])).fetchall():
                    conn.execute(
                        "INSERT INTO season_templates(gid, uid, season_id, "
                        "kind, content, updated_at) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(gid,uid,season_id,kind) DO UPDATE SET "
                        "content=excluded.content, "
                        "updated_at=excluded.updated_at",
                        (dst_gid, r["uid"], r["season_id"], t["kind"],
                         t["content"], now))
            if move:
                # ⚠️ 只有「既没按 UP 过滤、也没按合集过滤」才是真·整群搬走，
                #    这时才能把源群的合集整份清掉。
                #    一旦勾了具体合集（season_ids 不为 None），哪怕没勾任何 UP
                #    （want 仍是 None），也**只能删真正搬走的这几条** ——
                #    否则「只搬 1 个合集」会把源群所有合集全清空。
                if want is None and season_ids is None:
                    conn.execute("DELETE FROM seasons WHERE gid=?", (src_gid,))
                    conn.execute("DELETE FROM season_templates WHERE gid=?",
                                 (src_gid,))
                else:
                    for r in rows:
                        conn.execute(
                            "DELETE FROM seasons WHERE gid=? AND uid=? "
                            "AND season_id=?",
                            (src_gid, r["uid"], r["season_id"]))
                        conn.execute(
                            "DELETE FROM season_templates WHERE gid=? "
                            "AND uid=? AND season_id=?",
                            (src_gid, r["uid"], r["season_id"]))
        self._write(_fn)
        return len(rows)

    # ---------- 合集专属文案 ----------
    def get_season_templates(self, gid: str, uid: int, season_id: int) -> dict:
        """只返回这个合集**改过的**文案项（稀疏覆盖）。"""
        self.refresh()
        rows = self._conn().execute(
            "SELECT kind, content FROM season_templates "
            "WHERE gid=? AND uid=? AND season_id=?",
            (gid, int(uid), int(season_id))).fetchall()
        return {r["kind"]: r["content"] for r in rows}

    def set_season_templates(self, gid: str, uid: int, season_id: int,
                             mapping: dict) -> int:
        """批量设置。某项传空 = 取消覆盖、回落到上一层。返回写入条数。"""
        now = _now()
        written = 0
        for k, v in (mapping or {}).items():
            content = str(v or "")

            def _fn(conn, k=k, content=content):
                if content == "":
                    conn.execute(
                        "DELETE FROM season_templates WHERE gid=? AND uid=? "
                        "AND season_id=? AND kind=?",
                        (gid, int(uid), int(season_id), str(k)))
                else:
                    conn.execute(
                        "INSERT INTO season_templates(gid, uid, season_id, "
                        "kind, content, updated_at) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(gid,uid,season_id,kind) DO UPDATE SET "
                        "content=excluded.content, "
                        "updated_at=excluded.updated_at",
                        (gid, int(uid), int(season_id), str(k), content, now))
            self._write(_fn)
            if content:
                written += 1
        return written

    # ---------- 按群文案模板 ----------
    def up_kinds_in_group(self, gid: str, uid: int) -> set:
        """这个群对这个 UP 主**开了哪些类型**。

        用来决定"专属文案"编辑页里该显示哪些类目 ——
        只开了动态就不该出现"开播提醒"的输入框。
        """
        self.refresh()
        item = (self._cache["groups"].get(gid, {}).get("subs", {})
                or {}).get(str(uid)) or {}
        out = set()
        for k in KINDS:
            conf = item.get(k)
            if isinstance(conf, dict) and conf.get("on"):
                out.add(k)
        return out

    def get_up_templates(self, gid: str, uid: int) -> dict:
        """某个群对**某个 UP 主**的文案覆盖。

        比群级更细一层：A 群关注这个 UP 主要"沙雕风"文案，
        同一个群里另一个 UP 主要"正经风"，互不影响。
        """
        self.refresh()
        rows = self._conn().execute(
            "SELECT kind, content FROM up_templates WHERE gid=? AND uid=?",
            (gid, int(uid))).fetchall()
        return {r["kind"]: r["content"] for r in rows}

    def set_up_templates(self, gid: str, uid: int, mapping: dict) -> int:
        """批量设置某群对某 UP 主的文案。返回写进去的条数。"""
        written = 0
        now = _now()
        for k, v in (mapping or {}).items():
            content = str(v or "")
            if content == "":
                def _del(conn, k=k):
                    conn.execute(
                        "DELETE FROM up_templates WHERE gid=? AND uid=? "
                        "AND kind=?", (gid, int(uid), str(k)))
                self._write(_del)
                continue

            def _set(conn, k=k, content=content):
                conn.execute(
                    "INSERT INTO up_templates(gid, uid, kind, content, "
                    "updated_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(gid,uid,kind) DO UPDATE SET "
                    "content=excluded.content, updated_at=excluded.updated_at",
                    (gid, int(uid), str(k), content, now))
            self._write(_set)
            written += 1
        return written

    def up_tpl_counts(self, gid: str) -> dict:
        """这个群对每个 UP 主改了几条专属文案 → {uid: 条数}。"""
        self.refresh()
        rows = self._conn().execute(
            "SELECT uid, COUNT(*) AS n FROM up_templates WHERE gid=? "
            "GROUP BY uid", (gid,)).fetchall()
        return {str(r["uid"]): int(r["n"] or 0) for r in rows}

    def copy_up_templates(self, src_gid: str, dst_gid: str,
                          move: bool = False, uids=None) -> int:
        """把一个群的「UP 主专属文案」复制/迁移到另一个群。

        订阅迁移时一并带走 —— 只搬**改过的项**，
        没改过的项目标群本来就该用全局值，不该凭空多出一条。

        uids：只搬这几个 UP 主的文案（None = 全部）。
        ⚠️「选择迁移」只勾了一部分 UP 时，move 也只删搬走的那些，
        不能把源群整个清空 —— 没勾选的必须原样留着。

        ⚠️ v1.30.1 起文案层级里没有"群专属"这一层了（入口已挪到
        每个订阅项后面，群级没有编辑入口），所以这里是按 UP 主搬。
        """
        self.refresh()
        want = None
        if uids is not None:
            want = set()
            for u in uids:
                try:
                    want.add(int(u))
                except (TypeError, ValueError):
                    continue
            if not want:
                return 0
        conn = self._conn()
        rows = conn.execute(
            "SELECT uid, kind, content FROM up_templates WHERE gid=?",
            (src_gid,)).fetchall()
        if not rows:
            return 0
        now = _now()
        n = 0
        for r in rows:
            try:
                uid = int(r["uid"])
            except (TypeError, ValueError):
                continue
            if want is not None and uid not in want:
                continue
            conn.execute(
                "INSERT INTO up_templates(gid, uid, kind, content, updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(gid,uid,kind) DO UPDATE SET "
                "content=excluded.content, updated_at=excluded.updated_at",
                (dst_gid, uid, r["kind"], r["content"], now))
            n += 1
        conn.commit()
        if move:
            def _fn(conn):
                if want is None:
                    conn.execute("DELETE FROM up_templates WHERE gid=?",
                                 (src_gid,))
                else:
                    for u in want:
                        conn.execute(
                            "DELETE FROM up_templates WHERE gid=? AND uid=?",
                            (src_gid, u))
            self._write(_fn)
        self.refresh()
        return n

    def copy_subs(self, src_gid: str, dst_gid: str, move: bool = False,
                  picks=None, with_seasons: bool = True,
                  season_picks=None) -> dict:
        """把一个群的订阅复制（或迁移）到另一个群。

        move=False（复制）：源群保留，目标群得到一份相同的订阅
        move=True （迁移）：源群清空，订阅全部搬到目标群

        picks：{uid(int): set(kinds)} —— 只搬勾中的"UP 主 × 提醒类型"。
               None = 全体迁移（搬全部 UP 的全部类型）。
        with_seasons：合集订阅是否一并带走（按选中的 UP 过滤）。

        season_picks：只搬这几个合集 id（set）。
                      None = 按 UP 过滤的老行为；空集合 = 明确一个都不搬。
                      ⚠️ 有了它就可以"只搬合集、一个 UP 都不搬"。

        返回统计，方便面板告诉用户到底搬了多少。

        ⚠️ 目标群若已有同名 UP 主的订阅，按类型**覆盖**为目标应有的状态
        —— 这是"复制/迁移"的语义：结果应该和源群一致，
        而不是把两边的开关混在一起。
        """
        src = str(src_gid or "").strip()
        dst = str(dst_gid or "").strip()
        if not src or not dst or src == dst:
            return {"ok": False, "msg": "源群和目标群不能相同", "ups": 0,
                    "kinds": 0}

        self.refresh()
        subs = (self.get_group(src).get("subs") or {})
        # ⚠️ 允许"只搬合集"：源群一个 UP 都没订、但订了合集时，
        #    只要勾了具体合集，就不该被"没有任何订阅可搬"拦下。
        if not subs and season_picks is None:
            return {"ok": False, "msg": "源群没有任何订阅可搬", "ups": 0,
                    "kinds": 0}

        # 归一化 picks：key 统一成 int，kind 只认 KINDS 里的
        if picks is not None:
            norm = {}
            for u, ks in (picks or {}).items():
                try:
                    uid = int(u)
                except (TypeError, ValueError):
                    continue
                ks = set(str(x) for x in (ks or ()))
                ks = set(k for k in ks if k in KINDS)
                if ks:
                    norm[uid] = ks
            picks = norm

        now = _now()
        n_ups, n_kinds = 0, 0

        def _fn(conn):
            nonlocal n_ups, n_kinds
            # 目标群先存在（没有就建一个空壳）
            conn.execute(
                "INSERT INTO groups(gid, name, created_at, updated_at) "
                "VALUES(?,'',?,?) ON CONFLICT(gid) DO NOTHING",
                (dst, now, now))
            for uid_str, item in subs.items():
                try:
                    uid = int(uid_str)
                except (TypeError, ValueError):
                    continue
                want_kinds = None if picks is None else picks.get(uid)
                if picks is not None and not want_kinds:
                    continue
                hit = False
                for k in KINDS:
                    if want_kinds is not None and k not in want_kinds:
                        continue
                    conf = item.get(k)
                    if not isinstance(conf, dict):
                        continue
                    on = bool(conf.get("on"))
                    conn.execute(
                        "INSERT INTO subs(gid, uid, kind, \"on\", updated_at) "
                        "VALUES(?,?,?,?,?) "
                        'ON CONFLICT(gid,uid,kind) DO UPDATE SET '
                        '"on"=excluded."on", updated_at=excluded.updated_at',
                        (dst, uid, k, int(on), now))
                    if on:
                        n_kinds += 1
                    hit = True
                if hit:
                    n_ups += 1
            if move:
                if picks is None:
                    conn.execute("DELETE FROM subs WHERE gid=?", (src,))
                else:
                    # 只删真正搬走的那几个 (uid, kind)，没勾的原样留着
                    for uid, ks in picks.items():
                        for k in ks:
                            conn.execute(
                                "DELETE FROM subs WHERE gid=? AND uid=? "
                                "AND kind=?", (src, uid, k))

        self._write(_fn)
        # 选择迁移只搬勾中的 UP；全体迁移传 None
        moved_uids = None if picks is None else sorted(picks.keys())
        # 文案覆盖一并带走：按 UP 主搬（群级那一层已废弃）
        try:
            moved_tpl = self.copy_up_templates(src, dst, move=move,
                                               uids=moved_uids)
        except Exception:
            moved_tpl = 0
        msg = ("已迁移" if move else "已复制") + \
              f" {n_ups} 位 UP 主（{n_kinds} 个提醒开关）"
        try:
            if moved_tpl:
                msg += f"，另含 {moved_tpl} 条该群专属文案"
        except NameError:
            pass
        # 合集订阅一并带走（含每个合集自己的专属文案）
        moved_season = 0
        if with_seasons:
            try:
                # 勾了具体合集 → 不再按 UP 过滤（合集可能属于没勾的 UP）
                _uids = None if season_picks is not None else moved_uids
                moved_season = self.copy_seasons(src, dst, move=move,
                                                 uids=_uids,
                                                 season_ids=season_picks)
            except Exception:
                moved_season = 0
        if moved_season:
            msg += f"，{moved_season} 个视频合集"
        # 勾选了、但源群里根本没这些东西 —— 明确告诉用户，
        # 别回一句"已复制 0 位 UP 主"让人以为成功了。
        if picks is not None and n_ups == 0 and not moved_season:
            # 一个 UP 都没勾、也没勾合集 → "没勾选"；勾了但源群没有 → "找不到"
            _msg = ("还没勾选任何要搬的内容"
                    if (not picks and not season_picks)
                    else "勾选的内容在源群里没有，没有可搬的")
            return {"ok": False, "msg": _msg, "ups": 0, "kinds": 0,
                    "seasons": 0}
        return {"ok": True, "ups": n_ups, "kinds": n_kinds,
                "seasons": moved_season, "msg": msg}

    def remove_up(self, gid: str, uid: int) -> bool:
        uid = int(uid)

        def _fn(conn):
            conn.execute("DELETE FROM subs WHERE gid=? AND uid=?", (gid, uid))
        self._write(_fn)
        return True

    def sub_of(self, gid: str, uid: int) -> dict:
        self.refresh()
        return self.get_group(gid).get("subs", {}).get(str(uid), {})

    def ups_of_group(self, gid: str) -> dict:
        self.refresh()
        return _as_dict(self.get_group(gid).get("subs"))

    def all_ups(self) -> set:
        self.refresh()
        uids = set()
        for g in self._cache["groups"].values():
            for uid in _as_dict(_as_dict(g).get("subs")):
                try:
                    uids.add(int(uid))
                except (TypeError, ValueError):
                    continue
        return uids

    def enabled_kinds_of_up(self, uid: int) -> set:
        self.refresh()
        kinds = set()
        for g in self._cache["groups"].values():
            item = _as_dict(_as_dict(g).get("subs")).get(str(uid))
            item = _as_dict(item)
            for k in KINDS:
                # ⚠️ 订阅项里除了各类型配置，还混着 uname 这类字符串字段。
                # 直接 .get() 会抛 'str' object has no attribute 'get'。
                if _as_conf(item.get(k)).get("on"):
                    kinds.add(k)
        return kinds

    def subscribed_kinds(self, uid: int) -> set:
        """这个 UP 主被订阅了哪些类型（跨所有群合并）。

        用来在检测前判断"有没有人要看"，不必白跑一次接口。
        """
        self.refresh()
        kinds = set()
        for _gid, g in self._cache["groups"].items():
            item = _as_dict(_as_dict(_as_dict(g).get("subs")).get(str(uid)))
            # ⚠️ item 里除了各类型的配置，还混着 `uname` 这种字符串字段。
            # 不加类型判断直接 .get() 会抛 AttributeError
            # （'str' object has no attribute 'get'），
            # 结果整个检测链静默挂掉 —— 置顶评论就永远收不到。
            for k, conf in item.items():
                if k not in KINDS:
                    continue
                if _as_conf(conf).get("on"):
                    kinds.add(k)
        return kinds

    def targets(self, uid: int, kind: str) -> list:
        """哪些群订阅了这个 UP 主的这一类提醒 → [gid, ...]。

        ⚠️ 以前返回 [(gid, at_all), ...]。@全体 移除后不需要 at_all，
        直接返回 gid 列表。
        """
        self.refresh()
        out = []
        for gid, g in self._cache["groups"].items():
            item = _as_dict(_as_dict(_as_dict(g).get("subs")).get(str(uid)))
            # ⚠️ 与 enabled_kinds_of_up 同样的坑：item[kind] 可能是字符串
            conf = _as_conf(item.get(kind))
            if conf.get("on"):
                out.append(gid)
        return out

    # ---------- UP 主信息与基线 ----------
    def get_up(self, uid: int) -> dict:
        self.refresh()
        # ⚠️ 必须是 dict：调用方清一色写 up.get("uname") / up.get("room_id")，
        # 一旦缓存里存的是字符串就会抛 'str' object has no attribute 'get'，
        # 而这在轮询主循环里会让整轮检测停摆。
        return _as_dict(self._cache["ups"].get(str(uid)))

    def set_up(self, uid: int, **fields):
        self.set_up_info(uid,
                         fields.get("uname", ""),
                         fields.get("room_id", 0),
                         fields.get("face", ""))

    def set_up_info(self, uid: int, uname: str, room_id: int, face: str = "",
                    short_room_id: int = 0):
        uid = int(uid)
        now = _now()

        def _fn(conn):
            row = conn.execute(
                "SELECT uname, face, room_id, short_room_id FROM ups "
                "WHERE uid=?", (uid,)).fetchone()
            # face / uname / room_id 只在有值时覆盖，避免把已存的清空
            u = uname or (row["uname"] if row else "")
            f = face or (row["face"] if row else "")
            r = room_id or (row["room_id"] if row else 0)
            # 短号同理：很多主播没有短号（官方返回 0），
            # 不能因为一次没查到就把已有的短号抹掉
            sr = int(short_room_id or 0)
            if not sr:
                try:
                    sr = int(row["short_room_id"] or 0) if row else 0
                except (IndexError, KeyError, TypeError):
                    sr = 0
            conn.execute(
                "INSERT INTO ups(uid, uname, face, room_id, short_room_id, "
                "updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET "
                "uname=excluded.uname, face=excluded.face, "
                "room_id=excluded.room_id, "
                "short_room_id=excluded.short_room_id, "
                "updated_at=excluded.updated_at",
                (uid, u, f, r, sr, now))
        self._write(_fn)

    def baseline(self, uid: int, kind: str) -> dict:
        up = self.get_up(uid)
        return up.get(kind) or {}

    def set_baseline(self, uid: int, kind: str, **fields):
        uid = int(uid)
        now = _now()
        col = {"live": ("live_status", "live_title"),
               "video": ("video_bvid",),
               "dyn": ("dyn_id",)}.get(kind)
        if kind in ("dyn_top", "dyn_top_cmt"):
            col = (kind,)
        if not col:
            return

        def _fn(conn):
            conn.execute(
                "INSERT INTO ups(uid, updated_at) VALUES(?,?) "
                "ON CONFLICT(uid) DO NOTHING", (uid, now))
            if "status" in fields:
                conn.execute("UPDATE ups SET live_status=? WHERE uid=?",
                             (fields["status"], uid))
            if "title" in fields:
                conn.execute("UPDATE ups SET live_title=? WHERE uid=?",
                             (fields["title"], uid))
            if "bvid" in fields:
                conn.execute("UPDATE ups SET video_bvid=? WHERE uid=?",
                             (fields["bvid"], uid))
            if "id" in fields:
                if kind == "dyn_top":
                    conn.execute("UPDATE ups SET dyn_top_id=? WHERE uid=?",
                                 (fields["id"], uid))
                elif kind == "dyn_top_cmt":
                    conn.execute("UPDATE ups SET dyn_top_cmt=? WHERE uid=?",
                                 (fields["id"], uid))
                else:
                    conn.execute("UPDATE ups SET dyn_id=? WHERE uid=?",
                                 (fields["id"], uid))
            conn.execute("UPDATE ups SET updated_at=? WHERE uid=?", (now, uid))
        self._write(_fn)

    # ---------- 按群基线（新） ----------
    # 基线必须按「群 × UP主 × 类型」隔离，不能全站共用一份：
    # 否则新订阅的群会继承旧群留下的过期进度，把订阅之前就存在的内容
    # 当成新内容推一遍。
    # 保留 baseline()/set_baseline()（UP 主级）只为面板概览和旧数据兼容，
    # 检测流程一律走 baseline_of()/set_baseline_of()。

    @staticmethod
    def _bl_fields(kind: str, fields: dict) -> tuple:
        """把语义字段压成 (v1, v2)。"""
        if kind == "live":
            return (str(fields.get("status") if fields.get("status") is not
                        None else ""), str(fields.get("title") or ""))
        if kind == "video":
            # v2 存发布时间：删稿后"最新"会退回成更早的一条，
            # 只比 BV 号会把旧稿当成新稿再推一遍，必须比时间。
            return (str(fields.get("bvid") or ""),
                    str(_as_int(fields.get("pubdate"))))
        if kind == "dyn_seen":
            # 「这个群真的提醒过哪些动态」—— 必须记**一串**，不能只记一条。
            # 只记最后一条时：推 A → 推 D（A 被顶掉）→ UP 回头把 A 置顶，
            # 查不到 A 就又推一遍，群里看到同一条来两次（第二次还换了个
            # 「置顶」的帽子）。所以这里把最近若干条 ID 一起存进 v2。
            ids = fields.get("ids")
            if ids is None:
                ids = [str(fields.get("id") or "")] if fields.get("id") else []
            ids = [str(x) for x in ids if x][-DYN_SEEN_KEEP:]
            return (str(fields.get("id") or ""),
                    json.dumps({"p": int(_as_int(fields.get("pubdate"))),
                                "ids": ids}, ensure_ascii=False))
        return (str(fields.get("id") or ""),
                str(_as_int(fields.get("pubdate"))))

    @staticmethod
    def _bl_unpack(kind: str, v1, v2) -> dict:
        """把 (v1, v2) 还原成语义字段。"""
        v1 = v1 if v1 is not None else ""
        if kind == "live":
            out = {}
            if v1 != "":
                try:
                    out["status"] = int(v1)
                except (TypeError, ValueError):
                    out["status"] = None
            else:
                out["status"] = None
            out["title"] = v2 or ""
            return out
        if kind == "video":
            return {"bvid": v1 or "", "pubdate": _as_int(v2)}
        if kind == "dyn_seen":
            # 兼容旧数据：v2 以前是纯数字的 pubdate，那时等于只记了 v1 一条
            out = {"id": v1 or "", "pubdate": 0, "ids": []}
            raw = v2 if isinstance(v2, str) else ""
            obj = None
            if raw.startswith("{"):
                try:
                    obj = json.loads(raw)
                except (TypeError, ValueError):
                    obj = None
            if isinstance(obj, dict):
                out["pubdate"] = _as_int(obj.get("p"))
                out["ids"] = [str(x) for x in (obj.get("ids") or []) if x]
            else:
                out["pubdate"] = _as_int(v2)
                out["ids"] = [v1] if v1 else []
            return out
        return {"id": v1 or "", "pubdate": _as_int(v2)}

    def baseline_of(self, gid: str, uid: int, kind: str) -> dict:
        """某群对某 UP 主某类型的基线。没记过返回 {}。"""
        if not gid:
            return {}
        uid = int(uid)
        self.refresh()
        r = self._conn().execute(
            "SELECT v1, v2 FROM sub_baseline WHERE gid=? AND uid=? AND kind=?",
            (str(gid), uid, str(kind))).fetchone()
        if not r:
            return {}
        # 兜底：调用方清一色写 base.get("bvid") / base.get("status")，
        # 返回非 dict 会让整轮检测挂掉（'str' object has no attribute 'get'）
        return _as_dict(self._bl_unpack(kind, r["v1"], r["v2"]))

    def set_baseline_of(self, gid: str, uid: int, kind: str, **fields):
        """写入某群对某 UP 主某类型的基线。"""
        if not gid:
            return
        uid = int(uid)
        v1, v2 = self._bl_fields(kind, fields)
        now = _now()

        def _fn(conn):
            conn.execute(
                "INSERT INTO sub_baseline(gid, uid, kind, v1, v2, updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(gid,uid,kind) DO UPDATE SET "
                "v1=excluded.v1, v2=excluded.v2, updated_at=excluded.updated_at",
                (str(gid), uid, str(kind), v1, v2, now))
        self._write(_fn)

    def clear_baseline_of(self, gid: str, uid: int, kind: str = ""):
        """清除某群对某 UP 主的基线（下次检测会重新静默对齐）。

        用于「订阅项被关掉又打开 / 重新订阅」——旧进度已无意义，
        不应该拿它去判定新内容。
        """
        if not gid:
            return
        uid = int(uid)
        def _fn(conn):
            if kind:
                conn.execute("DELETE FROM sub_baseline WHERE gid=? AND uid=? "
                             "AND kind=?", (str(gid), uid, str(kind)))
            else:
                conn.execute("DELETE FROM sub_baseline WHERE gid=? AND uid=?",
                             (str(gid), uid))
        self._write(_fn)

    def migrate_baseline_to_per_group(self) -> int:
        """把旧的 UP 主级基线复制成按群基线（一次性迁移）。

        老用户升级后，各群继承当前进度，行为连续，不会因为升级而
        把所有群都当成"新订阅"重新对齐一遍。

        ⚠️ 订阅开关的 kind 和基线的 kind **不是同一套名字**：
        dynamic→dyn、top_comment→dyn_top_cmt。必须先映射再查，
        否则取到的是空值，迁移等于没做（而且不报错，很难发现）。
        """
        n = 0
        self.refresh()
        rows = self._conn().execute(
            "SELECT gid, uid, kind FROM subs WHERE \"on\"=1").fetchall()
        for r in rows:
            gid, uid, sub_kind = r[0], int(r[1]), r[2]
            bl_kind = _BL_KIND_OF_SUB.get(sub_kind)
            if not bl_kind:
                continue
            # 已有按群基线就跳过，不覆盖真实进度
            if self.baseline_of(gid, uid, bl_kind):
                continue
            old = self.baseline(uid, bl_kind)
            if not old:
                continue
            if bl_kind == "live":
                if old.get("status") is None:
                    continue
                self.set_baseline_of(gid, uid, bl_kind,
                                     status=old.get("status"),
                                     title=old.get("title") or "")
            elif bl_kind == "video":
                if not old.get("bvid"):
                    continue
                self.set_baseline_of(gid, uid, bl_kind, bvid=old["bvid"])
            else:
                if not old.get("id"):
                    continue
                self.set_baseline_of(gid, uid, bl_kind, id=old["id"])
            n += 1
        return n

    # ---------- 迁移 ----------
    def migrate_from_json(self, json_path: str,
                          with_baseline: bool = True) -> int:
        """把旧 data.json 的数据导进来。返回导入的群数量。"""
        if not os.path.exists(json_path):
            return 0
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            return 0
        if not isinstance(old, dict):
            return 0

        n = 0
        for gid, g in (old.get("groups") or {}).items():
            self.ensure_group(gid, g.get("name") or "")
            n += 1
            for uid, item in (g.get("subs") or {}).items():
                if item.get("uname"):
                    self.set_up_info(int(uid), item["uname"], 0)
                for k in KINDS:
                    conf = item.get(k) or {}
                    if conf.get("on"):
                        self.set_sub(gid, int(uid), k, bool(conf.get("on")),
                                     item.get("uname", ""))
        for uid, up in (old.get("ups") or {}).items():
            self.set_up_info(int(uid), up.get("uname", ""),
                             up.get("room_id", 0), up.get("face", ""),
                             up.get("short_room_id", 0))
            # ⚠️ 合并旧数据时**不要**带基线：旧库里的进度是几个月前的，
            #    盖到新库上会把那批历史内容当成"新投稿/新动态"再推一遍。
            if not with_baseline:
                continue
            for kind, keys in (("live", ("status", "title")),
                               ("video", ("bvid",)), ("dyn", ("id",))):
                b = up.get(kind) or {}
                if any(b.get(x) for x in keys):
                    self.set_baseline(int(uid), kind, **{
                        x: b.get(x) for x in keys if b.get(x) is not None})
        return n


    def purge_test_groups(self) -> int:
        """清掉混进来的测试假群（GA 之类），连带它的订阅和文案。

        ⚠️ 为什么需要：v1.98.3 之前打包把测试用的假群混进了 src/state.json，
           而 v1.98.3 起会"合并遗留 state.json" —— 假群被当成用户的旧
           订阅合进了真数据，面板上凭空多出「未命名群 GA」。只挡住以后
           不再合还不够，已经混进去的必须当面清掉。
        """
        with self._lock:
            self.refresh()
            conn = self._conn()
            bad = []
            for t in ("groups", "subs", "seasons",
                      "group_templates", "up_templates",
                      "season_templates"):
                try:
                    cols = _table_cols(conn, t)
                except Exception:
                    continue
                if "gid" not in cols:
                    continue
                try:
                    for r in conn.execute('SELECT DISTINCT gid FROM "%s"' % t):
                        if is_test_gid(r[0]):
                            bad.append(str(r[0]))
                except Exception:
                    continue
            bad = sorted(set(bad))
            if not bad:
                return 0
            for gid in bad:
                for t in ("groups", "subs", "seasons",
                          "group_templates", "up_templates",
                          "season_templates"):
                    try:
                        if "gid" in _table_cols(conn, t):
                            conn.execute(
                                'DELETE FROM "%s" WHERE gid=?' % t, (gid,))
                    except Exception:
                        continue
            self._bump(conn)
            conn.commit()
            self._persist()
            self._load_cache()
        _log.warning("清掉 %d 个测试残留假群：%s",
                     len(bad), "、".join(bad[:5]))
        return len(bad)

    def repair_orphan_groups(self) -> int:
        """把"订阅里有、groups 表里却没有"的 gid 补进 groups 表。

        ⚠️ 见 _orphan_gids 的说明：这类群机器人照推，但面板一个都不显示。
           补的只是 gid + 空名（前端显示「未命名群 xxxxxxxx」），
           已有的群名一律不动。
        """
        with self._lock:
            self.refresh()
            conn = self._conn()
            gids = _orphan_gids(conn)
            if not gids:
                return 0
            now = time.time()
            for gid in gids:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO groups"
                        "(gid, name, created_at, updated_at) "
                        "VALUES(?,?,?,?)", (gid, "", now, now))
                except Exception:
                    continue
            self._bump(conn)
            conn.commit()
            self._persist()
            self._load_cache()
        _log.warning(
            "补齐 %d 个没有群记录的订阅（此前面板完全看不到这些群）：%s",
            len(gids), "、".join(g[:12] + "…" for g in gids[:5]))
        return len(gids)

    def import_legacy_file(self, src: str) -> dict:
        """把旧版数据文件（data.db / 旧格式 data.json）**合并**进来。

        ⚠️ 是合并不是覆盖：已有的一律不动，只补缺失的。见 _merge_state。
        返回 {"ok": bool, "summary": {...}, "report": {...}}。
        """
        src = os.path.abspath(os.path.expanduser(str(src or "")))
        if not src or not os.path.isfile(src):
            return {"ok": False, "reason": "文件不存在"}
        ext = os.path.splitext(src)[1].lower()
        with self._lock:
            self.refresh()
            conn = self._conn()
            if ext in (".db", ".sqlite", ".sqlite3"):
                st, summary = read_legacy_db(src)
                if not st:
                    return {"ok": False, "reason": "读不出来或里面没有数据"}
                report = _merge_state(conn, st)
            else:
                try:
                    with open(src, "r", encoding="utf-8") as f:
                        obj = json.load(f)
                except Exception as e:
                    return {"ok": False, "reason": "读不出来：%s" % e}
                if not isinstance(obj, dict) or not obj:
                    return {"ok": False, "reason": "里面没有数据"}
                if _is_new_format(obj):
                    # 现行落盘格式（表名 → 行列表）
                    report = _merge_state(conn, obj)
                    summary = {
                        "groups": len(obj.get("groups") or []),
                        "subs": len([r for r in (obj.get("subs") or [])
                                     if r.get("on")]),
                        "seasons": len(obj.get("seasons") or []),
                        "ups": len(obj.get("ups") or []),
                        "templates": len(obj.get("up_templates") or []),
                    }
                else:
                    # 更老的 JSON（groups / ups 结构）。
                    # 不带基线 —— 旧进度盖回来会把历史内容再推一遍。
                    n = self.migrate_from_json(src, with_baseline=False)
                    report = {"groups": {"added": n}}
                    summary = {
                        "groups": len(obj.get("groups") or {}),
                        "subs": 0, "seasons": 0,
                        "ups": len(obj.get("ups") or {}),
                        "templates": 0,
                    }
            self._bump(conn)
            conn.commit()
            self._persist()
            self._load_cache()
        # 旧库常缺 groups 行（早期落盘不全 / 手工改过），订阅却带着 gid：
        # 不补的话面板永远显示"还没有登记任何群"，而机器人照推不误。
        fixed = self.repair_orphan_groups()
        if fixed:
            report["groups_recovered"] = fixed
        return {"ok": True, "summary": summary, "report": report}

def migrate_if_needed(db_path: str = "data.db",
                      json_path: str = "data.json") -> bool:
    """首次切换到数据库时，自动把旧的 data.json 导入。

    已存在数据文件且有内容 → 认为迁移过了，不重复导入。
    """
    # ⚠️ 必须按 Store 真正落盘的那个名字判断。
    # 落盘层会把 .db 归到 .json（resolve_data_path），这里若还拿 .db 去
    # exists()，永远查不到 → 每次启动都当成"还没迁移"重跑一遍，
    # 把老 data.json 里的推送基线反复盖回当前进度上。
    real = resolve_data_path(db_path)
    if os.path.exists(real) and os.path.getsize(real) > 0:
        return False
    if not os.path.exists(json_path):
        return False
    st = Store(db_path)
    n = st.migrate_from_json(json_path)
    return n > 0


# ==================== 旧版 data.db 兼容 ====================
# ⚠️ 背景：v1.93 之前数据是 SQLite 文件库（程序目录里的 data.db），
#    落盘层换成 JSON（state.json）之后，那个二进制文件就再也用不上了 ——
#    但老用户手里那份 data.db 是唯一一份订阅记录，必须能读回来。
#
# ⚠️ 为什么不能整库覆盖导入（INSERT OR REPLACE）：
#    旧库里的推送基线是几个月前的进度，一旦盖掉新库的进度，下一轮检测
#    就会把那批历史内容当成"新投稿/新动态"再推一遍。所以这里一律
#    **只补缺失、不动已有**。
import hashlib as _hashlib

# 旧数据文件可能的名字。data.db 是 v1.92 及更早的落盘文件，
# 其余几个是老版本改过名时留下的。
LEGACY_DB_NAMES = ("data.db", "data.json", "state.db",
                   "bili.db", "bili-notify.db", "notify.db")

# ups 表里「空了才用旧的补上」的字段（**不含**任何推送基线字段）。
_UPS_FILL_ONLY = ("uname", "face", "room_id", "short_room_id")

# ups 表里的基线字段。导入旧库时一律清零 —— 见 _strip_baseline 的说明。
_UPS_BASELINE_COLS = ("live_status", "live_title", "video_bvid", "dyn_id",
                      "dyn_top_id", "dyn_top_cmt")

# 整表不导入的表：sub_baseline 存的是"这个群推送到哪了"的进度。
# ⚠️ 旧库那份是几个月前的进度，导回来会让这期间的所有历史内容
#    都比它"新" → 全部当成新投稿/新动态再推一遍。
#    不导入反而安全：缺基线的群在下一轮检测前会自动静默对齐
#    （见 bot._missing_baselines），只记进度不推送。
_SKIP_TABLES = ("sub_baseline", "meta")


def _strip_baseline(row: dict) -> dict:
    """去掉旧行里的推送基线字段。

    ⚠️ 为什么连"更旧"的基线也不能留：基线记录的是"这个群已经推到哪了"，
    旧库那份越旧，落在这之后的真实历史内容就越容易被判成"新内容"。
    留空则由机器人下一轮静默对齐：只记当前进度，不推送。
    """
    out = dict(row or {})
    for c in _UPS_BASELINE_COLS:
        if c in out:
            out[c] = None if c == "live_status" else (
                0 if c.endswith("_id") and not c.startswith("dyn") else "")
    return out


def _table_cols(conn, t: str) -> list:
    try:
        rows = conn.execute('PRAGMA table_info("%s")' % t).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        out.append(r["name"] if isinstance(r, sqlite3.Row) else r[1])
    return out


def _pk_cols(conn, t: str) -> list:
    """表的主键列。PRAGMA 返回的 pk 是"第几主键列"，0 表示不是主键。"""
    try:
        rows = conn.execute('PRAGMA table_info("%s")' % t).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        name = r["name"] if isinstance(r, sqlite3.Row) else r[1]
        flag = r["pk"] if isinstance(r, sqlite3.Row) else r[5]
        if flag:
            out.append(name)
    return out


def _row_exists(conn, t: str, row: dict, pk: list) -> bool:
    try:
        where = " AND ".join('"%s"=?' % c for c in pk)
        r = conn.execute('SELECT 1 FROM "%s" WHERE %s LIMIT 1' % (t, where),
                         [row[c] for c in pk]).fetchone()
    except Exception:
        return False
    return r is not None


def _fill_ups(conn, row: dict) -> int:
    """ups 表已存在该 UP 主时，只把当前为空的资料字段补上。

    ⚠️ 基线字段（live_status / video_bvid / dyn_id / dyn_top_id…）一律不碰：
    旧库那份是几个月前的进度，填回去等于把历史内容再推一遍。
    """
    uid = row.get("uid")
    try:
        cur = conn.execute(
            'SELECT %s FROM ups WHERE uid=?' % ",".join(
                '"%s"' % c for c in _UPS_FILL_ONLY), (uid,)).fetchone()
    except Exception:
        return 0
    if not cur:
        return 0
    sets, vals = [], []
    for c in _UPS_FILL_ONLY:
        old = row.get(c)
        if not old:
            continue
        try:
            cur_v = cur[c]
        except (IndexError, KeyError):
            continue
        if cur_v in (None, "", 0):
            sets.append('"%s"=?' % c)
            vals.append(old)
    if not sets:
        return 0
    try:
        conn.execute('UPDATE ups SET %s WHERE uid=?' % ",".join(sets),
                     vals + [uid])
        return 1
    except Exception:
        return 0


def _merge_state(conn, state) -> dict:
    """把旧库导出的 dict **合并**进内存库：已有的一律不动，只补缺失。"""
    if not isinstance(state, dict) or not state:
        return {}
    have = set(_table_names(conn))
    report = {}
    for t in state.keys():
        rows = state.get(t)
        t = str(t)
        if not isinstance(rows, list) or not rows or t not in have:
            continue
        if t in _SKIP_TABLES:
            continue
        cols = _table_cols(conn, t)
        if not cols:
            continue
        pk = _pk_cols(conn, t) or []
        added = filled = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            # ⚠️ 测试残留（GA 之类假群）不合并进来，见 is_test_gid 的说明。
            if "gid" in cols and is_test_gid(row.get("gid")):
                continue
            if t == "ups":
                row = _strip_baseline(row)
            use = {k: v for k, v in row.items() if k in cols}
            if not use:
                continue
            if pk and all(c in use for c in pk):
                if _row_exists(conn, t, use, pk):
                    if t == "ups":
                        filled += _fill_ups(conn, use)
                    continue
            try:
                conn.execute(
                    'INSERT OR IGNORE INTO "%s" (%s) VALUES (%s)' % (
                        t, ",".join('"%s"' % c for c in use),
                        ",".join("?" * len(use))),
                    [use[c] for c in use])
                added += 1
            except Exception:
                continue
        if added or filled:
            report[t] = {"added": added, "filled": filled}
    return report


#: 真实群 openid（QQ 官方给的）都是 32 位左右的长串。
#: 短到 2 个字符、还全是纯大写字母的，只可能是测试里手写的假群名（GA / GB）。
#:
#: ⚠️ 为什么收紧到 2（原来是 4）：这是**破坏性**判据 —— purge_test_groups
#:    会把命中者连同它的订阅、文案一起删掉；repair_orphan_groups 也会拒绝
#:    补回。判据一宽，真实但名字偏短的群（OLDG / TPLG 这类 4 字符）就被
#:    当成残留：补不回面板、甚至被静默删掉订阅。宁可漏清一个假群（用户
#:    在面板能看见、可手动删），也不能误删真实数据。真实群 openid 是 32
#:    位长串，2 位纯大写字母不可能是真群。
TEST_GID_MAX_LEN = 2


def is_test_gid(gid) -> bool:
    """判断 gid 是不是测试残留（而不是真实群号）。

    ⚠️ 为什么需要：打包时偶尔会把测试用的假群（GA / GB …）混进
       src/state.json 一起发出去，而 v1.98.3 起会"合并遗留 state.json"——
       于是假群被当成用户的旧订阅合进真数据，面板上凭空多出
       「未命名群 GA」。真实群 openid 是长串，不会是纯大写短字母。
    """
    g = str(gid or "").strip()
    if not g:
        return True
    if len(g) > TEST_GID_MAX_LEN:
        return False
    return g.isalpha() and g.isupper()


def _orphan_gids(conn) -> list:
    """找出"订阅里有、但 groups 表里没有"的 gid。

    ⚠️ 为什么必须补：groups 表是面板「群与订阅」的唯一来源，groups 表空
       就显示"还没有登记任何群"——而 subs/seasons 里的订阅机器人照推不误。
       用户看到的是空白面板，实际订阅一直在跑（幽灵订阅）：看不见、改不了、
       删不掉。旧库来源复杂（早期版本落盘不全、手工改过、从回收站捞回），
       groups 表缺行是常态，不能指望它一定完整。

    ⚠️ is_test_gid 的判据必须窄（见 TEST_GID_MAX_LEN）：这里一旦把某个 gid
       误判成测试残留，它的订阅就永远补不回面板 —— 看不见、改不了、删不掉。
       真正的假群已由 purge_test_groups() 先行删掉（启动时先清后补）。
    """
    have = set()
    try:
        for r in conn.execute("SELECT gid FROM groups"):
            if r["gid"]:
                have.add(str(r["gid"]))
    except Exception:
        return []
    found = set()
    # subs / seasons 是主体；三张文案表也带 gid，一并收进来。
    for t, col in (("subs", "gid"), ("seasons", "gid"),
                   ("group_templates", "gid"),
                   ("up_templates", "gid"),
                   ("season_templates", "gid")):
        try:
            for r in conn.execute('SELECT DISTINCT "%s" FROM "%s"' % (col, t)):
                v = r[0]
                if v:
                    found.add(str(v))
        except Exception:
            continue
    return sorted(g for g in (found - have) if not is_test_gid(g))


def read_legacy_db(src: str) -> tuple:
    """读出旧版 data.db（SQLite 文件库）。返回 (数据 dict, 摘要 dict)。

    ⚠️ 原件一个字节都不动：先复制到临时文件，在副本上补表补列再导出。
       直接在原件上 executescript 会改写用户的旧库，万一后面还要从
       原件里捞东西就麻烦了。
    """
    src = os.path.abspath(os.path.expanduser(str(src or "")))
    if not src or not os.path.isfile(src):
        return {}, {}
    tmp = ""
    try:
        fd, tmp = tempfile.mkstemp(prefix=".legacy-", suffix=".db")
        os.close(fd)
        shutil.copy2(src, tmp)
        conn = sqlite3.connect(tmp, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            # 旧库缺新表/新列是常态（v1.92 的库没有 short_room_id、
            # dyn_top_id，seasons 没有 uname），按现行结构补齐之后
            # 导出来的行才不会缺字段。
            conn.executescript(SCHEMA)
            _ensure_columns(conn)
            conn.commit()
            st = _export_state(conn)
        finally:
            conn.close()
    except Exception as e:
        _log.warning("旧数据读不出来（%s）：%s", src, e)
        return {}, {}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if not st:
        return {}, {}
    summary = {
        "groups": len(st.get("groups") or []),
        "subs": len([r for r in (st.get("subs") or []) if r.get("on")]),
        "seasons": len(st.get("seasons") or []),
        "ups": len(st.get("ups") or []),
        "templates": len(st.get("up_templates") or []),
    }
    if not any(summary.values()):
        return {}, {}
    return st, summary


def _legacy_marker_path() -> str:
    """已导入记录。放在数据目录里，跟着数据走。"""
    try:
        return os.path.join(paths.run_dir(), "imported_legacy.json")
    except Exception:
        return ""


def _load_imported() -> dict:
    p = _legacy_marker_path()
    if not p or not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _save_imported(mark: dict) -> None:
    p = _legacy_marker_path()
    if not p:
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        _atomic_write_json(p, mark)
    except Exception:
        pass


def _fp_of(path: str) -> str:
    """文件指纹：大小 + 内容 sha1。改名/复制都能认出是同一份。"""
    try:
        h = _hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return "%d:%s" % (os.path.getsize(path), h.hexdigest())
    except Exception:
        return ""


def _is_new_format(obj: dict) -> bool:
    """区分两种 JSON：现行落盘（表名→行列表）与旧格式（groups/ups）。"""
    if not isinstance(obj, dict):
        return False
    return any(isinstance(v, list) for v in obj.values())


def scan_legacy_files() -> list:
    """扫描可能的旧数据文件（程序目录 / 源码目录 / 数据目录）。

    返回候选列表，每项带摘要和"是否已导入过"。
    """
    mark = _load_imported()
    dirs = []
    for d in (paths.app_dir(), paths.src_dir(), paths.data_home()):
        try:
            d = os.path.abspath(d)
        except Exception:
            continue
        if d and d not in dirs and os.path.isdir(d):
            dirs.append(d)
    out = []
    seen = set()
    cur = ""
    try:
        cur = os.path.abspath(resolve_data_path(DATA_FILE_DEFAULT))
    except Exception:
        pass
    _home = ""
    try:
        _home = os.path.abspath(paths.data_home())
    except Exception:
        pass
    for d in dirs:
        # ⚠️ 程序目录 / 源码目录里还要额外看一眼 state.json：
        # v1.98.3 之前面板把数据写在那儿（相对路径被拼到源码目录），
        # 那份里可能已经有用户手动登记过的群，统一路径后要捞回来，
        # 否则看着像"升级把订阅弄丢了"。数据目录里那份就是当前主文件，
        # 由下面的 ap == cur 跳过。
        _names = LEGACY_DB_NAMES
        if os.path.abspath(d) != _home:
            _names = tuple(_names) + ("state.json",)
        for name in _names:
            p = os.path.join(d, name)
            ap = os.path.abspath(p)
            if ap in seen or not os.path.isfile(ap):
                continue
            if ap == cur:
                continue
            seen.add(ap)
            try:
                size = os.path.getsize(ap)
            except OSError:
                continue
            if size == 0:
                continue
            fp = _fp_of(ap)
            if name.endswith(".db"):
                st, summary = read_legacy_db(ap)
                if not st:
                    continue
            else:
                try:
                    with open(ap, "r", encoding="utf-8") as f:
                        obj = json.load(f)
                except Exception:
                    continue
                if not isinstance(obj, dict) or not obj:
                    continue
                summary = {
                    "groups": len(obj.get("groups") or {}),
                    "subs": 0,
                    "seasons": 0,
                    "ups": len(obj.get("ups") or {}),
                    "templates": 0,
                }
                if not any(summary.values()):
                    continue
            out.append({
                "path": ap,
                "name": name,
                "size": size,
                "mtime": int(os.path.getmtime(ap)),
                "summary": summary,
                "imported": bool(fp and mark.get(ap) == fp),
            })
    return out


def auto_import_legacy(store=None, verbose=False) -> list:
    """启动时自动把没导入过的旧数据合并进来。

    ⚠️ 幂等：导入过的文件记进 imported_legacy.json（按大小+内容指纹），
       下次启动不再重复导入 —— 否则每启一次都合并一遍，日志全是噪音。
    """
    mark = _load_imported()
    done = []
    for item in scan_legacy_files():
        if item.get("imported"):
            continue
        p = item["path"]
        try:
            if store is None:
                store = Store(DATA_FILE_DEFAULT)
            res = store.import_legacy_file(p)
        except Exception as e:
            _log.warning("旧数据导入失败（%s）：%s", p, e)
            continue
        if not res.get("ok"):
            continue
        fp = _fp_of(p)
        mark[p] = fp
        _save_imported(mark)
        done.append({"path": p, "summary": res.get("summary") or {},
                     "report": res.get("report") or {}})
        n = (res.get("summary") or {}).get("groups", 0)
        _log.info("已合并旧数据 %s：%d 个群", os.path.basename(p), n)
        if verbose:
            print("  [*] 已合并旧数据 %s：%s" % (p, res.get("summary")))
    return done
