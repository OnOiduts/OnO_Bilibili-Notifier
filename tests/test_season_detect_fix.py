# -*- coding: utf-8 -*-
"""合集检测的三个真问题（回归测试）

1. **批量新增被永久漏推**
   以前把整批 fresh 一次写进基线，再只推前 limit 条。
   超出 limit 的那几条稀里糊涂进了基线，下一轮不再算新 ——
   UP 主一次往合集里加 5 个视频、limit=3，那 2 个**永远推不出来**。

2. **B 站合集列表索引延迟 → 判成旧内容静默**
   视频发布/加入合集后，列表接口往往要过几分钟才看得到。
   那段时间我们照常写基线（updated_at 一直往前走），等它出现时
   pubdate 已经早于 last_seen。容差只有 300 秒的话整条被静默，
   表现就是"合集更新了但永远不推送"。

3. **投稿先推、合集后到 → 同一条视频推两次**
   投稿接口比合集列表快，常常先以「新投稿」推掉，过几分钟才出现在
   合集里，那时基线还没有它，于是又来一条「合集更新」。
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.environ.setdefault("BOT_CONFIG",
                      os.path.join(sys.path[0], "config.yaml"))

import bot as B  # noqa: E402
from bilibili import SeasonInfo, SeasonVideo  # noqa: E402
from db import Store  # noqa: E402

OK = FAIL = 0
NOW = int(time.time())
UID = 259149243
SID = 1001


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


class FakeBili:
    async def get_video_section_title(self, bvid, season_id=0):
        return ""


class FakeBot:
    """只凑齐 _season_sync_group 用得到的那几个属性。"""
    _should_silence_season = None
    _pushed_recently = None

    def __init__(self, store, cfg=None):
        self.store = store
        self.cfg = cfg or {"season_push_limit": 3}
        self._silent = False
        self.bili = FakeBili()
        self.pushed = []
        self._recent_push = {}

    async def _push_season(self, gid, uid, season_id, name, push):
        self.pushed.append(push.bvid)


FakeBot._should_silence_season = B.NotifyBot._should_silence_season
FakeBot._pushed_recently = B.NotifyBot._pushed_recently


def mk_info(pairs):
    return SeasonInfo(
        season_id=SID, uid=UID, title="录播合集", cover="",
        total=len(pairs),
        videos=[SeasonVideo(bvid=b, aid=i, title=b, pic="", pubdate=p)
                for i, (b, p) in enumerate(pairs)],
    )


def run(bot, info, gid="GA"):
    asyncio.get_event_loop().run_until_complete(
        B.NotifyBot._season_sync_group(bot, gid, UID, SID, "测试UP", info))


def rewind(st, ts, gid="GA"):
    c = st._conn()
    c.execute("UPDATE seasons SET updated_at=? WHERE gid=?", (ts, gid))
    c.commit()


def fresh_store():
    tmp = tempfile.mkdtemp()
    st = Store(os.path.join(tmp, "t.db"))
    st.ensure_group("GA", "群A")
    return st


# ────────────────────────────────────────────────────────────
def t_batch_not_lost():
    """1) 一次加 5 个、limit=3：不能永久漏推那 2 个。"""
    print("=== 1) 批量新增不永久漏推 ===")
    st = fresh_store()
    st.add_season("GA", UID, SID, "录播合集")
    st.set_season_known("GA", UID, SID, ["BV0"])
    rewind(st, NOW - 120)
    fresh = [(f"BVN{i}", NOW - 60 + i) for i in range(5)]
    allv = [("BV0", NOW - 99999)] + fresh

    b1 = FakeBot(st)
    run(b1, mk_info(allv))
    chk("第1轮推 3 条（不刷屏）", len(b1.pushed) == 3, b1.pushed)
    chk("推的是**最新**的 3 条", b1.pushed == ["BVN4", "BVN3", "BVN2"],
        b1.pushed)

    rewind(st, NOW - 120)
    b2 = FakeBot(st)
    run(b2, mk_info(allv))
    chk("第2轮把剩下的 2 条推出来", b2.pushed == ["BVN1", "BVN0"], b2.pushed)

    rewind(st, NOW - 120)
    b3 = FakeBot(st)
    run(b3, mk_info(allv))
    chk("第3轮一条都不推（不重复）", b3.pushed == [], b3.pushed)


def t_index_delay():
    """2) 合集列表索引延迟：发布时间早于上次检测也该推。"""
    print("=== 2) 索引延迟不误杀 ===")
    st = fresh_store()
    st.add_season("GA", UID, SID, "录播合集")
    st.set_season_known("GA", UID, SID, ["BV0"])
    # 上次检测是"刚刚"，而视频 10 分钟前就发布了
    # （列表接口延迟，我们这轮才第一次看到它）
    rewind(st, NOW)
    info = mk_info([("BV0", NOW - 99999), ("BVNEW", NOW - 600)])
    b = FakeBot(st)
    run(b, info)
    chk("延迟 10 分钟出现的视频照样推", b.pushed == ["BVNEW"], b.pushed)
    chk("容差常量已放宽到 3600", B.SEASON_FRESH_GRACE >= 3600,
        B.SEASON_FRESH_GRACE)


def t_upgrade_guard_still_works():
    """升级保护不能被放宽的容差带崩。"""
    print("=== 3) 升级保护仍然成立 ===")
    st = fresh_store()
    st.add_season("GA", UID, SID, "录播合集")
    st.set_season_known("GA", UID, SID, ["BV0"])
    rewind(st, NOW - 120)
    old = NOW - 86400 * 30          # 一个月前
    info = mk_info([("BV0", old), ("BVOLD1", old), ("BVNEW", NOW - 30)])
    b = FakeBot(st)
    run(b, info)
    chk("一个月前的老视频不推", "BVOLD1" not in b.pushed, b.pushed)
    chk("真新视频照推", "BVNEW" in b.pushed, b.pushed)


def t_first_subscribe_silent():
    """首次订阅仍然静默对齐，不能把历史视频分批推出来。"""
    print("=== 4) 首次订阅静默对齐 ===")
    st = fresh_store()
    st.add_season("GA", UID, SID, "录播合集")
    info = mk_info([(f"BVH{i}", NOW - 86400 * (30 - i)) for i in range(10)])
    b = FakeBot(st)
    run(b, info)
    chk("首次订阅一条都不推", b.pushed == [], b.pushed)
    known = st.get_season_known("GA", UID, SID)
    chk("10 个历史视频全进基线", len(known) == 10, known)


def t_dup_with_video_push():
    """5) 投稿先推过 → 合集别再推一遍。"""
    print("=== 5) 投稿先推 → 合集静默 ===")
    st = fresh_store()
    st.ensure_group("GB", "群B")
    st.add_season("GA", UID, SID, "录播合集")
    st.add_season("GB", UID, SID, "录播合集")
    st.set_season_known("GA", UID, SID, ["BV0"])
    st.set_season_known("GB", UID, SID, ["BV0"])
    # 群A 既订投稿也订合集；群B 只订合集
    st.set_sub("GA", UID, "video", True, "测试UP")
    rewind(st, NOW - 120)

    info = mk_info([("BV0", NOW - 99999), ("BVNEW", NOW - 30)])
    b = FakeBot(st)
    b._recent_push = {UID: {"video": {"val": "BVNEW", "ts": time.time(),
                                      "pub": NOW - 30}}}
    run(b, info, gid="GA")
    chk("群A（也订投稿）不重复推合集", b.pushed == [], b.pushed)

    rewind(st, NOW - 120)
    b2 = FakeBot(st)
    b2._recent_push = {UID: {"video": {"val": "BVNEW", "ts": time.time(),
                                       "pub": NOW - 30}}}
    run(b2, info, gid="GB")
    chk("群B（只订合集）照常推", b2.pushed == ["BVNEW"], b2.pushed)


def t_other_group_season_no_silence():
    """别的群推过合集，不该让本群也安静。"""
    print("=== 6) 别的群的合集推送不误伤本群 ===")
    st = fresh_store()
    st.ensure_group("GB", "群B")
    st.add_season("GB", UID, SID, "录播合集")
    st.set_sub("GB", UID, "video", True, "测试UP")
    st.set_season_known("GB", UID, SID, ["BV0"])
    rewind(st, NOW - 120, gid="GB")
    info = mk_info([("BV0", NOW - 99999), ("BVNEW", NOW - 30)])
    b = FakeBot(st)
    # 群A 推过合集（记成 season），群B 不该被它静默
    b._recent_push = {UID: {"season": {"val": "BVNEW", "ts": time.time(),
                                       "pub": NOW - 30}}}
    run(b, info, gid="GB")
    chk("只认投稿记录，不认别的群的合集记录",
        b.pushed == ["BVNEW"], b.pushed)


def main():
    t_batch_not_lost()
    t_index_delay()
    t_upgrade_guard_still_works()
    t_first_subscribe_silent()
    t_dup_with_video_push()
    t_other_group_season_no_silence()
    print(f"\n合计 {OK + FAIL} 项，失败 {FAIL} 项")
    print("全部通过 ✅" if not FAIL else "有失败 ❌")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
