# -*- coding: utf-8 -*-
"""锁定「删掉最新 → 回退 → 又推一遍旧动态」的修复（v1.95.0）。

症状（用户原话 + 日志）：
    刚刚又给我推了个旧的动态更新提醒

日志里同时出现了两行互相矛盾的输出：
    最新动态回退为更早的 XXX（疑似删动态），静默不推送
    已推送 … 的动态

为什么旧的"回退判定"没拦住：
  判定的那一半是对的 —— 最新一条被 UP 删掉后，接口返回的"最新"
  退回了更早的那条，时间更旧，所以静默不推。

  但**写基线那一半是错的**：判定之前就无条件执行
      set_baseline_of(..., id=当前ID, pubdate=当前发布时间)
  于是回退发生时，基线里的时间被改成了**更早**的那条 —— 水位线
  被往下拉了。等被删的那条恢复（或接口在两个 ID 之间来回跳），
  T_旧 > T_被拉低的水位线 → 又判成"新内容" → 把订阅前就存在的
  旧动态再推一遍。用户看到的两行矛盾日志就是这么来的。

修法：基线时间**只许涨不许跌**（_wm_pub）。
  判定出"不是新内容"时，ID 照常更新，但保留原来更高的那一档时间。
  已经见过的内容不会因为水位下降而重新变新。
  另外把回退的那条也记进 dyn_seen，按 ID 再兜一道。
"""
import asyncio
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
    def __init__(self, dyn_id, pubdate=0, pinned=False):
        self.dyn_id = dyn_id
        self.pubdate = pubdate
        self.pinned = pinned
        self.major_type = "normal"
        self.ref_room_id = 0
        self.ref_bvid = ""
        self.short_room_id = 0


class FakeVid:
    def __init__(self, bvid, pubdate=0):
        self.bvid = bvid
        self.pubdate = pubdate
        self.title = bvid


class FakeBili:
    def __init__(self):
        self.top = None
        self.latest = None
        self.video = None

    async def get_pinned_dynamic(self, uid):
        return self.top

    async def get_latest_dynamic(self, uid):
        return self.latest

    async def get_latest_video(self, uid):
        return self.video


class FakeStore:
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


def _rdyn(b, top=None, latest=None):
    b.bili.top = top
    b.bili.latest = latest
    b.pushed = []
    asyncio.get_event_loop().run_until_complete(
        bot.NotifyBot._check_dynamic(b, 1, "UP"))
    return b.pushed


def _rvid(b, video=None):
    b.bili.video = video
    b.pushed = []
    b._video_covered_by_season = lambda *a, **k: False
    b._gid_also_gets_dynamic = lambda *a, **k: False
    asyncio.get_event_loop().run_until_complete(
        bot.NotifyBot._check_video(b, 1, "UP"))
    return b.pushed


NOW = 1000000


# ------------------------------------------------------------ 纯函数
def test_wm_pub_unit():
    """水位线：只涨不跌。"""
    print("[1] _wm_pub 水位线")
    W = bot.NotifyBot._wm_pub
    ck(W(5000, 3000) == 5000, "时间更早 → 保持原水位（不回退）")
    ck(W(3000, 5000) == 5000, "时间更晚 → 涨到新水位")
    ck(W(5000, 5000) == 5000, "时间相同 → 不变")
    ck(W(0, 3000) == 3000, "原来取不到时间 → 用当前的")
    ck(W(5000, 0) == 5000, "当前取不到时间 → 保持原来的")
    ck(W(0, 0) == 0, "两边都取不到 → 0")


# ------------------------------------------------------------ 动态
def test_delete_then_restore_no_repush():
    """核心复现：删掉最新 → 回退 → 恢复 → 不能再推。

    ⚠️ 这里刻意用「订阅时就已存在、从未推送过」的 A：
    它不在 dyn_seen 里，只有水位线能拦住它。
    """
    print("[2] 删掉最新后恢复：不推旧动态")
    b = _bot({(1, "dynamic"): ["G1"]})

    # 第0轮 静默对齐：A 是订阅时就已经存在的动态（从未推过）
    p = _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("A", NOW))
    ck(len(p) == 0, "对齐轮静默")
    ck(b.store.baseline_of("G1", 1, "dyn").get("pubdate") == NOW,
       f"基线时间 = {NOW}（实际 "
       f"{b.store.baseline_of('G1', 1, 'dyn').get('pubdate')}）")

    # 第1轮 UP 删掉 A → 最新退回更早的 Z
    p = _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("Z", NOW - 5000))
    ck(len(p) == 0, "回退 → 静默不推")
    ck(b.store.baseline_of("G1", 1, "dyn").get("id") == "Z", "基线 ID 更新为 Z")
    ck(b.store.baseline_of("G1", 1, "dyn").get("pubdate") == NOW,
       f"⚠️ 水位线不许被拉低（实际 "
       f"{b.store.baseline_of('G1', 1, 'dyn').get('pubdate')}）")

    # 第2轮 A 恢复 → 不能再推
    p = _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("A", NOW))
    ck(len(p) == 0,
       f"A 恢复 → 不推旧动态（实际 {[x[1].dyn_id for x in p]}）")


def test_regress_marks_seen():
    """回退的那条要记进 dyn_seen，按 ID 再兜一道。"""
    print("[3] 回退的条目记入已提醒")
    b = _bot({(1, "dynamic"): ["G1"]})
    _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("A", NOW))
    _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("Z", NOW - 5000))
    ck(bot.NotifyBot._dyn_notified(b, "G1", 1, "Z") is True,
       "回退的 Z 已记为「已提醒」")


def test_new_after_regress_still_pushes():
    """⚠️ 反向用例：回退之后真的发了新动态，必须照推。"""
    print("[4] 回退之后的新内容照常推")
    b = _bot({(1, "dynamic"): ["G1"]})
    _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("A", NOW))          # 对齐
    _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("Z", NOW - 5000))   # 回退
    p = _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("D", NOW + 100))
    ck(len(p) == 1 and p[0][1].dyn_id == "D",
       f"新动态 D 照常推（实际 {[x[1].dyn_id for x in p]}）")


def test_pushed_then_delete_then_restore():
    """推过的那条被删又恢复：dyn_seen + 水位线双重拦住。"""
    print("[5] 推过 → 删除 → 恢复")
    b = _bot({(1, "dynamic"): ["G1"]})
    _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("Z", NOW - 5000))   # 对齐
    p = _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("A", NOW))      # 推 A
    ck(len(p) == 1 and p[0][1].dyn_id == "A", "第1轮推了 A")
    p = _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("Z", NOW - 5000))  # 删 A
    ck(len(p) == 0, "删掉 A → 回退静默")
    p = _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("A", NOW))      # 恢复
    ck(len(p) == 0, "A 恢复 → 不重复推")


# ------------------------------------------------------------ 置顶
def test_pinned_regress_no_repush():
    """置顶分支同样不许把水位线拉低。"""
    print("[6] 置顶取消/恢复不推旧动态")
    b = _bot({(1, "dynamic"): ["G1"]})

    # 对齐：置顶是 A（订阅时就置顶着，从未推过）
    p = _rdyn(b, FakeDyn("A", NOW), FakeDyn("Z", NOW - 5000))
    ck(len(p) == 0, "对齐轮静默")
    ck(b.store.baseline_of("G1", 1, "dyn_top").get("pubdate") == NOW,
       "置顶基线时间 = NOW")

    # 取消置顶 → 置顶接口兜底返回了更早的 B
    p = _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("Z", NOW - 5000))
    ck(len(p) == 0, "置顶回退 → 静默")
    ck(b.store.baseline_of("G1", 1, "dyn_top").get("pubdate") == NOW,
       f"⚠️ 置顶水位线不许被拉低（实际 "
       f"{b.store.baseline_of('G1', 1, 'dyn_top').get('pubdate')}）")

    # 又置顶回 A
    p = _rdyn(b, FakeDyn("A", NOW), FakeDyn("Z", NOW - 5000))
    ck(len(p) == 0, f"A 重新置顶 → 不推（实际 {[x[1].dyn_id for x in p]}）")


# ------------------------------------------------------------ 投稿
def test_video_regress_no_repush():
    """投稿：撤稿后"最新"退回更早的一条，恢复时不该再推。"""
    print("[7] 投稿撤稿/恢复不推旧稿")
    b = _bot({(1, "video"): ["G1"]})

    b.bili.video = FakeVid("BVold", NOW)
    b.pushed = []
    b._video_covered_by_season = lambda *a, **k: False
    b._gid_also_gets_dynamic = lambda *a, **k: False
    asyncio.get_event_loop().run_until_complete(
        bot.NotifyBot._check_video(b, 1, "UP"))
    ck(b.store.baseline_of("G1", 1, "video").get("pubdate") == NOW,
       "投稿基线时间 = NOW")

    p = _rvid(b, FakeVid("BVolder", NOW - 5000))     # 撤稿 → 回退
    ck(len(p) == 0, "撤稿回退 → 静默")
    ck(b.store.baseline_of("G1", 1, "video").get("pubdate") == NOW,
       f"⚠️ 投稿水位线不许被拉低（实际 "
       f"{b.store.baseline_of('G1', 1, 'video').get('pubdate')}）")

    p = _rvid(b, FakeVid("BVold", NOW))              # 恢复
    ck(len(p) == 0, f"旧稿恢复 → 不推（实际 {[x[1].bvid for x in p]}）")

    p = _rvid(b, FakeVid("BVnew", NOW + 100))        # 真新稿
    ck(len(p) == 1 and p[0][1].bvid == "BVnew", "真新稿照常推")


# ------------------------------------------------------------ 隔离
def test_group_isolation():
    """一个群回退，不能把另一个群的水位线也拉低。"""
    print("[8] 群与群互不影响")
    b = _bot({(1, "dynamic"): ["G1", "G2"]})
    _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("A", NOW))          # 对齐两个群
    # 只让 G1 经历回退：手动把 G2 摘掉
    b.store._m[(1, "dynamic")] = ["G1"]
    _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("Z", NOW - 5000))
    b.store._m[(1, "dynamic")] = ["G1", "G2"]
    ck(b.store.baseline_of("G1", 1, "dyn").get("pubdate") == NOW,
       "G1 经历回退，水位线仍为 NOW")
    ck(b.store.baseline_of("G2", 1, "dyn").get("pubdate") == NOW,
       "G2 未受影响，水位线仍为 NOW")
    p = _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("A", NOW))
    ck(len(p) == 0, "两个群都不推旧动态")


# ------------------------------------------------------------ 反向验证
def test_old_behavior_would_fail():
    """证明这条用例是真断言：把水位线手动拉低，旧行为就会误推。

    不做这一步，上面的「不推」有可能只是因为别的原因没推。
    """
    print("[9] 反向验证：水位线被拉低就会误推")
    b = _bot({(1, "dynamic"): ["G1"]})
    _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("A", NOW))          # 对齐
    # 手工复现旧行为：回退时把基线时间写成更早的
    b.store.set_baseline_of("G1", 1, "dyn", id="Z", pubdate=NOW - 5000)
    p = _rdyn(b, FakeDyn("B", NOW - 9000), FakeDyn("A", NOW))
    ck(len(p) == 1 and p[0][1].dyn_id == "A",
       f"水位线被拉低 → A 会被误推（证明上面的修复有效，实际 "
       f"{[x[1].dyn_id for x in p]}）")


def main():
    for fn in (test_wm_pub_unit,
               test_delete_then_restore_no_repush,
               test_regress_marks_seen,
               test_new_after_regress_still_pushes,
               test_pushed_then_delete_then_restore,
               test_pinned_regress_no_repush,
               test_video_regress_no_repush,
               test_group_isolation,
               test_old_behavior_would_fail):
        fn()
    print(f"\n通过 {PASS} / 失败 {FAIL}")
    if FAILED:
        for n in FAILED:
            print("  失败：", n)
        sys.exit(1)


if __name__ == "__main__":
    main()
