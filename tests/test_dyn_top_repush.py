# -*- coding: utf-8 -*-
"""锁定「新动态被 UP 主置顶后又推一遍」的修复（v1.94.0）。

症状（用户原话）：
    新动态被置顶，检测到 id 一样应该不推送

为什么旧的去重没拦住：
  置顶动态和普通动态是**两条独立基线**（dyn_top / dyn），
  置顶那条常驻列表第一位，不会因为发新动态而移位，所以必须分开记。
  但"分开设基线"也带来一个洞 —— 置顶分支只跟**自己上一次的置顶 ID**
  比，从来不跟"这个群已经提醒过的普通动态"比。

  于是：
    第 N 轮   UP 发了 A（未置顶）→ 按「📝 新动态」推出去，dyn 基线 = A
    第 N+k 轮 UP 把 A 置顶      → 置顶分支看到 dyn_top 基线还是老的 B，
                                 A != B 且更新 → 又按「📌 置顶动态」推一遍

  群里看到的就是同一个内容来两次，第二次还换了个「置顶」的帽子。

修法：新增一条按群的「已提醒过的动态 ID」记录（dyn_seen）。
  置顶分支和普通分支在推之前都先查它 —— 这个群已经为这条 dyn_id
  提醒过（不管当时是置顶还是普通），就不再提醒。
  ⚠️ 必须是"真的推过"才记：首次静默对齐时基线也会写成 A，
     那时群其实什么都没收到，不能拿基线当"已提醒"用，
     否则订阅前的历史内容永远不会推。
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, "/data/workspace/_stub2")

import bot  # noqa: E402

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


class FakeDyn:
    def __init__(self, dyn_id, pubdate=0, pinned=False, mtype="normal"):
        self.dyn_id = dyn_id
        self.pubdate = pubdate
        self.pinned = pinned
        self.major_type = mtype
        self.ref_room_id = 0
        self.ref_bvid = ""
        self.short_room_id = 0


class FakeBili:
    """可编排的：这一轮置顶是什么、最新非置顶是什么。"""

    def __init__(self):
        self.top = None
        self.latest = None

    async def get_pinned_dynamic(self, uid):
        return self.top

    async def get_latest_dynamic(self, uid):
        return self.latest


class FakeStore:
    """按群基线，落在一个 dict 里。"""

    def __init__(self, mapping):
        self._m = mapping
        self.bl = {}

    def targets(self, uid, kind):
        return list(self._m.get((uid, kind), []))

    def baseline_of(self, gid, uid, kind):
        return dict(self.bl.get((gid, uid, kind)) or {})

    def set_baseline_of(self, gid, uid, kind, **fields):
        cur = dict(self.bl.get((gid, uid, kind)) or {})
        cur.update(fields)
        self.bl[(gid, uid, kind)] = cur


def _bot(mapping=None):
    b = bot.NotifyBot.__new__(bot.NotifyBot)
    b.cfg = {}
    b._recent_push = {}
    b._pushed_content = {}
    b.store = FakeStore(mapping or {})
    b.bili = FakeBili()
    b.pushed = []
    b.notes = []

    async def _push(kind, uid, name, info, exclude=None):
        b.pushed.append((kind, info, tuple(exclude or ())))

    b._push = _push
    b._note = lambda uid, kind, text: b.notes.append((uid, kind, text))
    b._log_check_fail = lambda *a, **k: None
    return b


def _round(b, top=None, latest=None):
    b.bili.top = top
    b.bili.latest = latest
    b.pushed = []
    asyncio.get_event_loop().run_until_complete(
        bot.NotifyBot._check_dynamic(b, 1, "UP"))
    return b.pushed


NOW = 1000000


def _warm(b, old="Z"):
    """第 0 轮：静默对齐（刚订阅时基线的建立方式）。"""
    return _round(b, FakeDyn("B", NOW - 9000, pinned=True),
                  FakeDyn(old, NOW - 5000))


# ---------------------------------------------------------------- 复现
def test_repro_pinned_repush():
    """置顶一条刚推过的动态 → 不该再推一遍（这是本次要修的症状）。"""
    print("[1] 新动态被置顶后不再重复推送")
    b = _bot({(1, "dynamic"): ["G1"]})

    _warm(b)                                    # 基线：置顶 B / 最新 Z
    p = _round(b, FakeDyn("B", NOW - 9000, pinned=True),
               FakeDyn("A", NOW))               # UP 发了 A
    ck(len(p) == 1 and p[0][1].dyn_id == "A",
       f"第1轮推了新动态 A（实际 {[x[1].dyn_id for x in p]}）")

    p = _round(b, FakeDyn("A", NOW, pinned=True),
               FakeDyn("Z", NOW - 5000))        # UP 把 A 置顶
    ck(len(p) == 0,
       f"第2轮 A 被置顶 → 不再推（实际 {[x[1].dyn_id for x in p]}）")


def test_reverse_pinned_first():
    """反过来：先被置顶推出去，之后 UP 取消置顶 → 普通分支也不再补推。"""
    print("[2] 置顶先推、取消置顶后普通分支不补推")
    b = _bot({(1, "dynamic"): ["G1"]})

    _warm(b)
    p = _round(b, FakeDyn("A", NOW, pinned=True),
               FakeDyn("Z", NOW - 5000))        # A 已置顶
    ck(len(p) == 1 and p[0][1].dyn_id == "A",
       f"第1轮按置顶推了 A（实际 {[x[1].dyn_id for x in p]}）")

    p = _round(b, FakeDyn("B", NOW - 9000, pinned=True),
               FakeDyn("A", NOW))               # 取消置顶，A 变最新
    ck(len(p) == 0,
       f"第2轮 A 已提醒过 → 不补推（实际 {[x[1].dyn_id for x in p]}）")


def test_first_align_still_pushes():
    """⚠️ 反向用例：首次静默对齐不能把真新动态吞掉。

    基线在"静默对齐"时也会写成 A，但那时群里什么都没收到。
    若拿基线当"已提醒"，订阅后就再也收不到任何动态了。
    """
    print("[3] 首次静默对齐不算「已提醒」")
    b = _bot({(1, "dynamic"): ["G1"]})

    p = _round(b, FakeDyn("B", NOW - 9000, pinned=True), FakeDyn("A", NOW))
    ck(len(p) == 0, "首次对齐：静默不推")
    ck(b.store.baseline_of("G1", 1, "dyn").get("id") == "A",
       "基线已写成 A（但没推过）")

    # ⚠️ 真置顶时「最新非置顶」要让位（latest 是更早的 Z），
    # 两个分支 id 不同才走置顶分支；写 latest=A 等于"置顶接口兜底
    # 返回了最新动态"，那是同一条，按新动态走，不再有置顶这一说。
    p = _round(b, FakeDyn("A", NOW, pinned=True), FakeDyn("Z", NOW - 5000))
    ck(len(p) == 1 and p[0][1].dyn_id == "A",
       f"置顶没提醒过的内容 → 照常推（实际 {[x[1].dyn_id for x in p]}）")


def test_new_content_still_pushes():
    """⚠️ 反向用例：真的是新动态照推，不能被去重误伤。"""
    print("[4] 真·新动态照常推送")
    b = _bot({(1, "dynamic"): ["G1"]})

    _warm(b)
    p = _round(b, FakeDyn("B", NOW - 9000, pinned=True), FakeDyn("A", NOW))
    ck(len(p) == 1, f"第1轮推了 A（实际 {[x[1].dyn_id for x in p]}）")

    p = _round(b, FakeDyn("B", NOW - 9000, pinned=True),
               FakeDyn("D", NOW + 5000))
    ck(len(p) == 1 and p[0][1].dyn_id == "D",
       f"新动态 D 照推（实际 {[x[1].dyn_id for x in p]}）")


def test_per_group_isolation():
    """⚠️ 按群隔离：A 群收过，B 群（新订阅）照样要收。"""
    print("[5] 按群隔离：一个群收过不影响另一个群")
    b = _bot({(1, "dynamic"): ["G1"]})

    _warm(b)                                    # 只有 G1 订阅
    p = _round(b, FakeDyn("B", NOW - 9000, pinned=True), FakeDyn("A", NOW))
    ck(len(p) == 1, f"G1 收到 A（实际 {len(p)}）")

    # G2 后订阅：首次对齐静默
    b.store._m = {(1, "dynamic"): ["G1", "G2"]}
    p = _round(b, FakeDyn("B", NOW - 9000, pinned=True), FakeDyn("A", NOW))
    ck(len(p) == 0, f"G2 首次对齐：静默（实际 {len(p)}）")

    # UP 把 A 置顶：G1 收过要跳过，G2 没收过要推
    p = _round(b, FakeDyn("A", NOW, pinned=True), FakeDyn("Z", NOW - 5000))
    ck(len(p) == 1, f"只推一次（实际 {len(p)}）")
    ck(p and "G1" in p[0][2],
       f"置顶推送把 G1 排除（它已作为新动态收过），排除项={p[0][2] if p else None}")


def test_pinned_change_still_pushes():
    """⚠️ 换了一条**没提醒过**的置顶 → 照推。"""
    print("[6] 换成没提醒过的新置顶照推")
    b = _bot({(1, "dynamic"): ["G1"]})

    _warm(b)
    p = _round(b, FakeDyn("B", NOW - 9000, pinned=True), FakeDyn("A", NOW))
    ck(len(p) == 1, f"第1轮推了 A（实际 {[x[1].dyn_id for x in p]}）")

    p = _round(b, FakeDyn("E", NOW - 100, pinned=True),
               FakeDyn("A", NOW))
    ck(len(p) == 1 and p[0][1].dyn_id == "E",
       f"没提醒过的置顶 E 照推（实际 {[x[1].dyn_id for x in p]}）")


def test_off_on_resets_seen():
    """关掉「动态」再打开 → 已提醒记录要一起作废。

    ⚠️ 旧代码这里删的是**订阅名**（dynamic），而 sub_baseline 里存的是
    基线名（dyn），一行都删不掉 —— 重新订阅后继承了关之前的进度。
    """
    print("[7] 关→开：dyn / dyn_top / dyn_seen 全部作废")
    import tempfile

    import db as DB
    d = tempfile.mkdtemp()
    st = DB.Store(os.path.join(d, "t.db"))
    st.ensure_group("G", "群")
    st.set_sub("G", 1, "dynamic", True, "UP")
    for bk in ("dyn", "dyn_top", "dyn_seen"):
        st.set_baseline_of("G", 1, bk, id="A", pubdate=NOW)
    ck(st.baseline_of("G", 1, "dyn_seen").get("id") == "A", "已提醒记录写入 A")

    st.set_sub("G", 1, "dynamic", False, "UP")     # 关
    st.set_sub("G", 1, "dynamic", True, "UP")      # 再开
    # ⚠️ 没记过时 baseline_of 返回 {}，.get("id") 是 None —— 不能拿它和
    #    "" 比，那样永远不相等，测试会假失败。
    ck(not st.baseline_of("G", 1, "dyn").get("id"), "dyn 基线已作废")
    ck(not st.baseline_of("G", 1, "dyn_top").get("id"), "dyn_top 基线已作废")
    ck(not st.baseline_of("G", 1, "dyn_seen").get("id"), "dyn_seen 已作废")


def test_seen_not_overwritten():
    """⚠️ 已提醒记录是**单槽**时的洞（本次要修的症状）。

    dyn_seen 原来只记"最后推过的那一条"。于是：
        推 A → dyn_seen = A
        推 D → dyn_seen = D（A 被顶掉了）
        UP 把 A 置顶 → 置顶分支查 dyn_seen 是 D，A 查不到 → 又推一遍 A

    群里看到的就是：同一个内容先来一次「📝 新动态」，
    隔一阵又来一次「📌 置顶动态」。
    """
    print("[8] 已提醒记录不能被后来的动态顶掉")
    b = _bot({(1, "dynamic"): ["G1"]})

    _warm(b)                                    # 基线：置顶 B / 最新 Z
    p = _round(b, FakeDyn("B", NOW - 9000, pinned=True),
               FakeDyn("A", NOW))
    ck(len(p) == 1 and p[0][1].dyn_id == "A", "第1轮推了 A")

    p = _round(b, FakeDyn("B", NOW - 9000, pinned=True),
               FakeDyn("D", NOW + 5000))
    ck(len(p) == 1 and p[0][1].dyn_id == "D", "第2轮推了 D")

    # UP 回头把 A 置顶：A 早就按「新动态」提醒过，不能再推
    p = _round(b, FakeDyn("A", NOW, pinned=True),
               FakeDyn("D", NOW + 5000))
    ck(len(p) == 0,
       f"第3轮 A 被置顶 → 不再推（实际 {[x[1].dyn_id for x in p]}）")

    # ⚠️ 反向用例：真的是新内容照推，不能被"记多条"误伤
    p = _round(b, FakeDyn("F", NOW + 9000, pinned=True),
               FakeDyn("D", NOW + 5000))
    ck(len(p) == 1 and p[0][1].dyn_id == "F",
       f"没提醒过的新置顶 F 照推（实际 {[x[1].dyn_id for x in p]}）")


def test_db_roundtrip_and_legacy():
    """真实存储下的往返 + 旧数据兼容。

    dyn_seen 的 v2 以前是纯数字 pubdate，现在改成存 {p, ids}。
    老用户升级上来时库里还是数字 —— 必须能读成"只记过一条"，
    不能解析失败变成空（那会把已提醒记录全丢，重复推送又回来了）。
    """
    print("[9] 真实存储往返 + 旧数据兼容")
    import tempfile

    import db as DB
    d = tempfile.mkdtemp()
    st = DB.Store(os.path.join(d, "t.db"))
    st.ensure_group("G", "群")
    st.set_sub("G", 1, "dynamic", True, "UP")

    st.set_baseline_of("G", 1, "dyn_seen", id="A", pubdate=NOW,
                       ids=["X", "A"])
    got = st.baseline_of("G", 1, "dyn_seen")
    ck(got.get("id") == "A", "最后一条仍是 A")
    ck(list(got.get("ids") or []) == ["X", "A"],
       f"整串往返不丢（实际 {got.get('ids')}）")
    ck(int(got.get("pubdate") or 0) == NOW, "发布时间没被挤掉")

    # 撞上限：只留最近的几条
    st.set_baseline_of("G", 1, "dyn_seen", id="Z", pubdate=NOW,
                       ids=[f"i{i}" for i in range(50)] + ["Z"])
    ids = list(st.baseline_of("G", 1, "dyn_seen").get("ids") or [])
    ck(len(ids) == DB.DYN_SEEN_KEEP and ids[-1] == "Z",
       f"超上限只留最近 {DB.DYN_SEEN_KEEP} 条（实际 {len(ids)}）")

    # 旧数据：v2 是纯数字
    conn = st._conn()
    conn.execute("INSERT INTO sub_baseline(gid,uid,kind,v1,v2,updated_at) "
                 "VALUES('G',1,'dyn_seen','A','12345',0) "
                 "ON CONFLICT(gid,uid,kind) DO UPDATE SET v1='A', v2='12345'")
    conn.commit()
    old = st.baseline_of("G", 1, "dyn_seen")
    ck(old.get("id") == "A" and list(old.get("ids") or []) == ["A"],
       f"旧数据兼容（实际 {old}）")
    ck(int(old.get("pubdate") or 0) == 12345, "旧数据的时间也能读出来")


def test_same_id_one_push_per_round():
    """同一轮里置顶与最新是同一条 → 只推一次，且按「新动态」推。

    真实成因：置顶接口在**认不出置顶标记**时会退而返回列表第一条
    （bilibili.get_pinned_dynamic 的兜底），也就是最新动态本身。
    于是同一条 id 同时落进两个分支 —— 一条「📌 置顶动态」加一条
    「📝 新动态」。用户看到的就是同一条内容来了两次。
    """
    print("[10] 置顶兜底返回最新动态：同一条只推一次")
    b = _bot({(1, "dynamic"): ["G1"]})

    _warm(b)
    # top 与 latest 是同一条 A（UP 其实没置顶，是兜底把它当置顶了）
    p = _round(b, FakeDyn("A", NOW, pinned=True), FakeDyn("A", NOW))
    ck(len(p) == 1 and p[0][1].dyn_id == "A",
       f"只推一次（实际 {[x[1].dyn_id for x in p]}）")
    ck(bool(p) and p[0][1].pinned is False,
       "按新动态推，不带「置顶」的帽子")

    # 之后 UP 发新的：照常推，不受上面这次影响
    p = _round(b, FakeDyn("D", NOW + 5000, pinned=True),
               FakeDyn("D", NOW + 5000))
    ck(len(p) == 1 and p[0][1].dyn_id == "D",
       f"新的那条照推（实际 {[x[1].dyn_id for x in p]}）")

    # 再回头把 A 置顶（此时 latest 让位给更早的）→ 已提醒过，不推
    p = _round(b, FakeDyn("A", NOW, pinned=True),
               FakeDyn("D", NOW + 5000))
    ck(len(p) == 0,
       f"A 已提醒过，置顶也不再推（实际 {[x[1].dyn_id for x in p]}）")


def test_seen_checked_before_pubdate():
    """「已提醒过」必须排在「时间是否更新」之前，否则日志会说错原因。

    真实成因：UP 把一条**早就提醒过**的旧动态置顶时，那条的时间比置顶
    基线里那条还旧，旧顺序会先命中「回退为更早的」分支 —— 结果虽然也是
    静默，但日志写成了"回退为更早的"，真实原因（早就提醒过）被盖掉，
    照着日志排查会被带偏。所以「已提醒过」必须先判。

    ⚠️ 这一条锁的是**日志准确性**，不是推送行为（两种顺序都不推）。
    """
    print("[11] 置顶一条提醒过的旧动态：日志说「已提醒过」而非「回退」")
    b = _bot({(1, "dynamic"): ["G1"]})

    _warm(b)                       # 置顶基线 B(NOW-9000) / dyn 基线 Z(NOW-5000)
    # 直接记一条"早先提醒过"的旧动态 X（时间比置顶基线那条还旧）
    b._mark_dyn_notified("G1", 1, "X", NOW - 50000)

    logs = []

    class H(logging.Handler):
        def emit(self, record):
            try:
                logs.append(record.getMessage())
            except Exception:
                pass

    h = H()
    _log = getattr(bot, "_log", None)
    ck(_log is not None, "能拿到模块 logger")
    if _log is not None:
        _log.addHandler(h)
    try:
        # UP 把 X 置顶（X 时间比基线更旧），latest 是真正的新动态 C
        p = _round(b, FakeDyn("X", NOW - 50000, pinned=True),
                   FakeDyn("C", NOW + 9000))
    finally:
        if _log is not None:
            _log.removeHandler(h)

    ck(not any(x[1].dyn_id == "X" for x in p),
       f"提醒过的 X 不再推（实际 {[x[1].dyn_id for x in p]}）")
    ck(any(x[1].dyn_id == "C" for x in p),
       f"真正的新动态 C 照常推（实际 {[x[1].dyn_id for x in p]}）")
    hit = [x for x in logs if "已作为新动态提醒过" in x]
    wrong = [x for x in logs if "回退为更早的" in x]
    ck(bool(hit), f"日志说的是「已提醒过」（实际 {logs[-2:]}）")
    ck(not wrong, f"不该写成「回退为更早的」（实际 {wrong}）")


def main():
    test_repro_pinned_repush()
    test_reverse_pinned_first()
    test_first_align_still_pushes()
    test_new_content_still_pushes()
    test_per_group_isolation()
    test_pinned_change_still_pushes()
    test_off_on_resets_seen()
    test_seen_not_overwritten()
    test_db_roundtrip_and_legacy()
    test_same_id_one_push_per_round()
    test_seen_checked_before_pubdate()
    print(f"\n通过 {PASS} / 失败 {FAIL}")
    if FAILED:
        for n in FAILED:
            print("  失败:", n)
        sys.exit(1)


if __name__ == "__main__":
    main()
