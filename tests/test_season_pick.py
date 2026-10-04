"""挑「最近更新」的合集：不能拿 recent / mtime 直接当答案。

用户实测反馈（星瞳，UID 401315430）：
    应该挑中【合集·星瞳のVLOG】的《狂炫云南菌子宴》，
    实际挑中的是【合集·整活日记】的《辞职！今天把鹅厂炒了！》
    —— 合集内那条视频确实是它自己里头最新的，但**合集挑错了**。

元凶是 _pick_newest_season 里的一条捷径：
    best > 0 且只有一个候选达到 best → 直接返回它，不再探测。
而 recent_archives / meta.mtime 本身**不等于**"最近一次往里加视频"
（老合集改个名字 mtime 就很新；recent 里混进一条旧视频也会偏），
于是这条捷径稳定地把答案定在错误的合集上，永远不会被纠正。

现在：时间戳只用来排**探测顺序**，候选多于一个就一定真去探测，
用"该合集里真实最新视频的 pubdate"决胜负。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from bilibili import BiliClient, SeasonInfo, SeasonVideo  # noqa: E402

UID = 401315430
DAY = 86400
NOW = 1_760_000_000          # 一个固定的"现在"，方便构造 pubdate

results = []


def ck(name, cond):
    results.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name)


class FakeClient:
    """只实现 _pick_newest_season 会用到的 get_season_archives。"""

    def __init__(self, mapping, fail=()):
        self.mapping = mapping          # sid -> SeasonInfo
        self.fail = set(int(x) for x in fail)
        self.calls = []                 # 记录探测顺序，用于断言排序

    async def get_season_archives(self, uid, season_id, max_pages=2):
        self.calls.append(int(season_id))
        sid = int(season_id)
        if sid in self.fail:
            raise Exception("模拟取不到这个合集")
        return self.mapping.get(sid)


def _season(sid, name, pubdates, total=None):
    """造一个合集，pubdates 是里面视频的发布时间列表。"""
    vids = [SeasonVideo(bvid=f"BV{i}{sid}", title=f"{name}-{i}",
                        pubdate=int(p))
            for i, p in enumerate(pubdates)]
    return SeasonInfo(season_id=int(sid), uid=UID, title=name,
                      total=total or len(vids), videos=vids)


def _pick(client, pick, probes=5):
    """用未绑定方式调用：方法体只用到 self.get_season_archives。"""
    return asyncio.get_event_loop().run_until_complete(
        BiliClient._pick_newest_season(client, UID, pick, probes=probes))


print("=== ① recent 骗人时，必须靠真实探测纠正（星瞳那个例子）===")

# 整活日记：recent 很新（最近往里加过东西），但里面最新的视频其实很旧
# VLOG：recent 为 0（接口没给），但里面最新的视频是三天前的
pick1 = [
    {"id": 111, "title": "整活日记", "recent": NOW - DAY,
     "mtime": NOW - DAY, "total": 40, "type": "season"},
    {"id": 222, "title": "星瞳のVLOG", "recent": 0,
     "mtime": 0, "total": 12, "type": "season"},
]
c1 = FakeClient({
    111: _season(111, "整活日记", [NOW - 400 * DAY]),
    222: _season(222, "星瞳のVLOG", [NOW - 3 * DAY]),
})
sid1, info1 = _pick(c1, pick1)
ck("挑中的是真实最新那个（VLOG 222）", sid1 == 222)
ck("顺带把已取到的合集带回来（省一次重复请求）",
   info1 is not None and info1.season_id == 222)
ck("两个候选都被探测过（不再偷懒直接返回）",
   set(c1.calls) == {111, 222})
ck("先探测时间戳高的那个（排序仍用于省请求）",
   c1.calls and c1.calls[0] == 111)

print("=== ② 时间戳全为 0：按接口原始顺序逐个探测 ===")

pick2 = [
    {"id": 1, "title": "老合集A", "recent": 0, "mtime": 0, "total": 99,
     "type": "season"},
    {"id": 2, "title": "老合集B", "recent": 0, "mtime": 0, "total": 5,
     "type": "season"},
    {"id": 3, "title": "真最新", "recent": 0, "mtime": 0, "total": 2,
     "type": "season"},
]
c2 = FakeClient({
    1: _season(1, "老合集A", [NOW - 500 * DAY]),
    2: _season(2, "老合集B", [NOW - 300 * DAY]),
    3: _season(3, "真最新", [NOW - DAY]),
})
sid2, _ = _pick(c2, pick2)
ck("挑中真最新（3 号）", sid2 == 3)
ck("不再按 total 排（99 个视频的老合集没被选中）", sid2 != 1)
ck("三个都探测了", set(c2.calls) == {1, 2, 3})

print("=== ③ 只有一个合集：直接返回，不发多余请求 ===")

c3 = FakeClient({7: _season(7, "唯一", [NOW - DAY])})
sid3, _ = _pick(c3, [{"id": 7, "title": "唯一", "recent": 0, "mtime": 0,
                      "total": 1, "type": "season"}])
ck("返回这个合集", sid3 == 7)
ck("没发探测请求（本来就知道答案）", not c3.calls)

print("=== ④ 探测全部失败：回退排序第一个，不能返回 0 ===")

c4 = FakeClient({}, fail=(11, 22))
sid4, info4 = _pick(c4, [
    {"id": 11, "title": "取不到1", "recent": NOW, "mtime": NOW, "total": 3,
     "type": "season"},
    {"id": 22, "title": "取不到2", "recent": 0, "mtime": 0, "total": 3,
     "type": "season"},
])
ck("回退到排序第一个（11）", sid4 == 11)
ck("回退时不带缓存（没真取到）", info4 is None)

print("=== ⑤ 部分失败：跳过失败的，在成功的里挑最新 ===")

c5 = FakeClient({
    33: _season(33, "成功但旧", [NOW - 200 * DAY]),
    55: _season(55, "成功且新", [NOW - DAY]),
}, fail=(44,))
sid5, _ = _pick(c5, [
    {"id": 33, "title": "成功但旧", "recent": 0, "mtime": 0, "total": 3,
     "type": "season"},
    {"id": 44, "title": "取不到", "recent": 0, "mtime": 0, "total": 3,
     "type": "season"},
    {"id": 55, "title": "成功且新", "recent": 0, "mtime": 0, "total": 3,
     "type": "season"},
])
ck("跳过失败的，挑中 55", sid5 == 55)
ck("失败的也被试过（不是提前放弃）", 44 in c5.calls)

print("=== ⑥ 早停：候选时间戳都不如已探测结果时不再发请求 ===")

c6 = FakeClient({
    101: _season(101, "A", [NOW - DAY]),
    102: _season(102, "B", [NOW - 2 * DAY]),
    103: _season(103, "C", [NOW - 3 * DAY]),
})
sid6, _ = _pick(c6, [
    {"id": 101, "title": "A", "recent": NOW - DAY, "mtime": 0, "total": 3,
     "type": "season"},
    {"id": 102, "title": "B", "recent": NOW - 2 * DAY, "mtime": 0, "total": 3,
     "type": "season"},
    {"id": 103, "title": "C", "recent": NOW - 3 * DAY, "mtime": 0, "total": 3,
     "type": "season"},
])
ck("挑中 A", sid6 == 101)
ck("早停生效（后面的没再请求）", c6.calls == [101])

print("=== ⑦ 视频列表(series) 也能正常参与挑选 ===")

c7 = FakeClient({9: _season(9, "视频列表", [NOW - DAY])})
sid7, _ = _pick(c7, [{"id": 9, "title": "视频列表", "recent": 0, "mtime": 0,
                      "total": 3, "type": "series"}])
ck("series 也能被挑中", sid7 == 9)

bad = [n for n, ok in results if not ok]
print()
print(f"通过 {len(results) - len(bad)}/{len(results)}")
if bad:
    print("失败：")
    for n in bad:
        print("  - " + n)
    sys.exit(1)
