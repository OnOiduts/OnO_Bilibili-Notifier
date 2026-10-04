# -*- coding: utf-8 -*-
"""合集「升级保护」测试：分页修好后不能把老视频当新视频狂推。

背景：分页 bug 修好之前，合集基线可能只记了**最老**那几十个视频。
修好后会突然扫出一大堆"没见过但其实早就存在"的视频 ——
不挡一下，升级后会一口气推十几轮（每轮 3 条）。

判据：发布时间早于「上次写基线」→ 说明上次看的时候它就在了，
只是以前翻不到，不算新。pubdate 取不到时不拦（宁可多推也别漏）。
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
LAST_SEEN = NOW - 120          # 上次写基线的时刻
OLD = NOW - 86400 * 30         # 一个月前：早就存在的老视频
NEW = NOW - 30                 # 刚发的


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
    def __init__(self, store):
        self.store = store
        self.cfg = {"season_push_limit": 3}
        self._silent = False
        self.bili = FakeBili()
        self.pushed = []

    async def _push_season(self, gid, uid, season_id, name, push):
        self.pushed.append(push.bvid)


def mk_info(pairs):
    """pairs: [(bvid, pubdate)]"""
    return SeasonInfo(
        season_id=1001, uid=259149243, title="录播合集", cover="",
        total=len(pairs),
        videos=[SeasonVideo(bvid=b, aid=i, title=b, pic="", pubdate=p)
                for i, (b, p) in enumerate(pairs)],
    )


def rewind(st, gid, ts):
    """把某行的 updated_at 拨回指定时刻（模拟"上次检测是那时候"）。"""
    conn = st._conn()
    conn.execute("UPDATE seasons SET updated_at=? WHERE gid=?", (ts, gid))
    conn.commit()


def run(bot, info, gid="GA", sid=1001):
    asyncio.get_event_loop().run_until_complete(
        B.NotifyBot._season_sync_group(bot, gid, 259149243, sid,
                                       "测试UP", info))


FakeBot._should_silence_season = B.NotifyBot._should_silence_season
FakeBot._pushed_recently = B.NotifyBot._pushed_recently

def main():
    tmp = tempfile.mkdtemp()
    st = Store(os.path.join(tmp, "t.db"))
    st.ensure_group("GA", "群A")
    st.add_season("GA", 259149243, 1001, "录播合集")
    # 老版本留下的基线：只有最老的 3 个
    st.set_season_known("GA", 259149243, 1001,
                        ["BV0000", "BV0001", "BV0002"])
    rewind(st, "GA", LAST_SEEN)

    # 修好后一次扫回：老的 3 个（已在基线）+ 一堆以前翻不到的老视频
    # + 2 个真正刚发的
    info = mk_info([
        ("BV0000", OLD), ("BV0001", OLD + 1), ("BV0002", OLD + 2),
        ("BV0003", OLD + 3), ("BV0004", OLD + 4), ("BV0010", OLD + 10),
        ("BV0020", OLD + 20),
        ("BV0050", NEW), ("BV0051", NEW + 1),
    ])

    print("=== 1) 升级后不会把「以前翻不到的老视频」当新的推 ===")
    bot = FakeBot(st)
    run(bot, info)
    chk("只推真新视频", sorted(bot.pushed) == ["BV0050", "BV0051"],
        bot.pushed)
    chk("没有推 BV0003 这类老视频", "BV0003" not in bot.pushed)

    print("=== 2) 老视频仍然进了基线（下次不会再冒出来） ===")
    known = st.get_season_known("GA", 259149243, 1001)
    for b in ["BV0003", "BV0010", "BV0020"]:
        chk(f"{b} 已补进基线", b in known)

    print("=== 3) 再跑一次：不重复推 ===")
    bot2 = FakeBot(st)
    run(bot2, info)
    chk("第二次一条都不推", bot2.pushed == [], bot2.pushed)

    print("=== 4) 时间推进后又出现新视频 → 正常推 ===")
    rewind(st, "GA", LAST_SEEN)          # 模拟"又过了一轮"
    info2 = mk_info([(b, p) for b, p in
                     [("BV0000", OLD), ("BV0003", OLD + 3),
                      ("BV0050", NEW), ("BV0051", NEW + 1),
                      ("BV0099", NOW - 10)]])
    bot3 = FakeBot(st)
    run(bot3, info2)
    chk("新出现的 BV0099 被推送", "BV0099" in bot3.pushed, bot3.pushed)
    chk("老的 BV0050 不再重复推", "BV0050" not in bot3.pushed)

    print("=== 5) pubdate 取不到时不拦（宁可多推也别漏） ===")
    st2 = Store(os.path.join(tmp, "t2.db"))
    st2.ensure_group("GB", "群B")
    st2.add_season("GB", 259149243, 2002, "无时间合集")
    st2.set_season_known("GB", 259149243, 2002, ["BV0000"])
    rewind(st2, "GB", LAST_SEEN)
    info3 = mk_info([("BV0000", OLD), ("BV0007", 0)])
    info3.videos[1].bvid = "BV0007"
    bot4 = FakeBot(st2)
    run(bot4, info3, gid="GB", sid=2002)
    chk("pubdate=0 的视频照样推", bot4.pushed == ["BV0007"], bot4.pushed)

    print("=== 6) 容差：发布时间略早于上次检测也算新（不能漏推）===")
    # 刚发布的视频：我们"写基线"的时刻可能比它发布晚几秒到几十秒，
    # 没有容差就会被判成旧内容 → 真·新视频反而推不出去。
    st3 = Store(os.path.join(tmp, "t3.db"))
    st3.ensure_group("GC", "群C")
    st3.add_season("GC", 259149243, 3003, "容差合集")
    st3.set_season_known("GC", 259149243, 3003, ["BV0000"])
    rewind(st3, "GC", LAST_SEEN)
    # 发布时间比"上次检测"早 10 秒 —— 在容差内，必须推
    edge = LAST_SEEN - 10
    info4 = mk_info([("BV0000", OLD), ("BV0008", edge)])
    info4.videos[1].bvid = "BV0008"
    bot5 = FakeBot(st3)
    run(bot5, info4, gid="GC", sid=3003)
    chk("容差内（早 10 秒）照样推", bot5.pushed == ["BV0008"], bot5.pushed)
    chk("容差常量已定义",
        getattr(B, "SEASON_FRESH_GRACE", 0) > 0,
        str(getattr(B, "SEASON_FRESH_GRACE", None)))

    print()
    print(f"合计 {OK + FAIL} 项，失败 {FAIL} 项")
    if FAIL:
        sys.exit(1)
    print("全部通过 ✅")


main()
