# -*- coding: utf-8 -*-
"""锁定「面板凭空多出『未命名群 GA』」的修复（v1.98.4）。

症状（用户实况）：
    面板「订阅」页出现一个「未命名群 GA」，下面是 1 位 UP 主、0 个合集。
    这个群用户从没登记过 —— 是打包时混进 src/state.json 的**测试残留**。

根因（我自己埋的）：
  v1.98.3 为了让"面板上手动登记过的群"在统一路径后不丢，
  加了「扫描程序目录 / 源码目录里遗留的 state.json 并合并」。
  而 v1.98.1 / v1.98.2 的包里恰好带着 src/state.json（内含测试假群 GA）。
  于是：我自己的测试残留 → 被当成用户的旧订阅 → 合进真数据。

修法（两道）：
  1. 合并时挡住：_merge_state 跳过 gid 是测试残留的行（is_test_gid）
  2. 已经混进去的当面清掉：Store.purge_test_groups()，start.py 每次启动跑，
     且必须在 repair_orphan_groups() **之前**跑（顺序反了会把假群又补回来）

判据：真实群 openid 是 32 位左右的长串，短到 8 字符以内的一律不是真群。
"""
import os
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
    d = tempfile.mkdtemp(prefix="tgp-%s-" % tag)
    os.environ["BILI_NOTIFY_HOME"] = d
    return d


REAL_GID = "A1B2C3D4E5F60718293A4B5C6D7E8F90"   # 32 位，像真 openid


def _state_with(groups):
    """造一份"旧 state.json"：给定 gid 列表，带群、订阅、合集。"""
    return {
        "groups": [{"gid": g, "name": "", "created_at": 0, "updated_at": 0}
                   for g in groups],
        "subs": [{"gid": g, "uid": 259149243, "kind": "dyn", "on": 1,
                  "uname": "测试UP"} for g in groups],
        "seasons": [{"gid": g, "uid": 280242571, "sid": 3175959,
                     "title": "录播合集", "on": 1} for g in groups],
    }


def main():
    print("=" * 60)
    print("测试残留假群（GA）不得混进用户数据")
    print("=" * 60)

    # ---------- 1. 判据 ----------
    print("\n[1] is_test_gid 判据")
    ck(db.is_test_gid("GA") is True, "GA 判为测试残留")
    ck(db.is_test_gid("GB") is True, "GB 判为测试残留")
    ck(db.is_test_gid("G1") is False, "G1 不误判（测试常用名，不能一刀切拦）")
    ck(db.is_test_gid("") is True, "空 gid 判为测试残留")
    ck(db.is_test_gid(REAL_GID) is False, "32 位真 openid 不误判")
    ck(db.is_test_gid("A1B2C3D4") is False, "8 位长的不误判")
    ck(db.is_test_gid("GHOST1") is False, "GHOST1 不误判")

    # ---------- 2. 合并时挡住 ----------
    print("\n[2] 合并旧 state.json 时不把 GA 带进来")
    _home("merge")
    st = db.Store(db.data_path_from_cfg())
    st.ensure_group(REAL_GID, "真群")
    st.set_sub(REAL_GID, 259149243, "dyn", True, "测试UP")
    conn = st._conn()
    db._merge_state(conn, _state_with(["GA", REAL_GID]))
    conn.commit()
    st._load_cache()
    names = [g.get("gid") for g in st.groups_with_meta()]
    ck("GA" not in names, "合并后面板看不到 GA")
    ck(REAL_GID in names, "真群不受影响")
    ck(bool(st.sub_of(REAL_GID, 259149243).get("dyn")),
       "真群已有订阅不被旧库覆盖")
    ck(not bool(st.sub_of("GA", 259149243).get("dyn")),
       "GA 的订阅也没进来")

    # ---------- 3. 已经混进去的能清掉 ----------
    print("\n[3] purge_test_groups 清掉已混进去的假群")
    _home("purge")
    st2 = db.Store(db.data_path_from_cfg())
    # 手工把 GA 塞进去，模拟"已经被合进真数据"的现状
    conn2 = st2._conn()
    db._merge_state(conn2, _state_with(["GA"]))
    conn2.execute("INSERT OR IGNORE INTO groups"
                  "(gid,name,created_at,updated_at) VALUES(?,?,?,?)",
                  ("GA", "", 0, 0))
    conn2.commit()
    st2._load_cache()
    st2.ensure_group(REAL_GID, "真群")
    ck(any(g.get("gid") == "GA" for g in st2.groups_with_meta()),
       "前置：GA 确实在面板里（模拟现状）")
    n = st2.purge_test_groups()
    ck(n >= 1, f"清掉了假群（返回 {n}）")
    names2 = [g.get("gid") for g in st2.groups_with_meta()]
    ck("GA" not in names2, "清理后面板不再有 GA")
    ck(REAL_GID in names2, "真群一个都没动")
    ck(st2.purge_test_groups() == 0, "再清一次返回 0（不会每启刷日志）")

    # ---------- 4. 不能补回来 ----------
    print("\n[4] 补幽灵群时不得把 GA 又补回来")
    _home("orphan")
    st3 = db.Store(db.data_path_from_cfg())
    conn3 = st3._conn()
    conn3.execute('INSERT OR IGNORE INTO subs'
                  '("gid","uid","kind","on") VALUES(?,?,?,?)',
                  ("GA", 259149243, "dyn", 1))
    conn3.commit()
    st3._load_cache()
    ck(st3.repair_orphan_groups() == 0, "GA 不会被当成幽灵群补回")

    # ---------- 5. 反向验证 ----------
    print("\n[5] 反向验证（证明断言咬得住）")
    _real = db.is_test_gid
    try:
        db.is_test_gid = lambda g: False        # 假装"什么都是真群"
        _home("rev")
        st4 = db.Store(db.data_path_from_cfg())
        c4 = st4._conn()
        db._merge_state(c4, _state_with(["GA"]))
        c4.commit()
        st4._load_cache()
        got = any(g.get("gid") == "GA" for g in st4.groups_with_meta())
        ck(got is True, "关掉过滤后 GA 又冒出来（断言确实咬得住）")
    finally:
        db.is_test_gid = _real

    # ---------- 6. 源码目录不得带残留 ----------
    print("\n[6] 源码目录不得带数据残留")
    src = os.path.join(os.path.dirname(_HERE), "src")
    ck(not os.path.exists(os.path.join(src, "state.json")),
       "src/ 下没有 state.json")
    ck(not os.path.isdir(os.path.join(src, "backups")),
       "src/ 下没有 backups/")

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILED:
        for f in FAILED:
            print("   FAIL:", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
