"""合集「小节」：没分节就别显示，分了节要按小节内的时间线挑最新。

用户反馈（v1.56）：
    ① 合集只有一个"正片"时，消息里还写着「小节：正片」—— 那等于没分节，
       属于噪音，不该显示。
    ② 分了小节的合集，UP 主往往**往某个小节里加视频**，整个列表的顺序
       会按小节重排，"最新那条"不再落在扁平数组的头或尾，分页也很可能
       翻不到它 —— 只看扁平数组必然错位。所以要按小节逐个比 pubdate。

关键实现：
    get_season_sections()  一次详情请求拿到整个合集的小节结构
    section_map()          → {bvid: 小节名}；**只有一个小节时返回空**
    newest_in_sections()   按小节比 pubdate，挑时间线最新的那条
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from bilibili import BiliClient, SeasonInfo, SeasonVideo  # noqa: E402

NOW = 1_760_000_000
DAY = 86400

results = []


def ck(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name + (
        f"   {extra}" if (extra and not cond) else ""))


def _ep(bvid, pub, title=None, pic=None):
    return {"bvid": bvid, "title": title or bvid,
            "pic": pic or f"pic_{bvid}", "pubdate": pub}


def _sec(title, eps):
    return {"title": title, "episodes": list(eps)}


# ---------------------------------------------------------------- ① 没分节
print("\n[1] 只有一个小节 = 没分节，不显示")


class OneSection(BiliClient):
    """合集只有一个"正片"（B站默认给的那个）。"""

    UGC = {"sections": [_sec("正片", [_ep("BV1", NOW - 3 * DAY),
                                     _ep("BV2", NOW - DAY)])]}

    async def _fetch_ugc_season(self, bvid):
        return self.UGC


c = OneSection("")
secs = asyncio.run(c.get_season_sections("BV2", 1001))
ck("取到小节结构", len(secs) == 1 and secs[0]["title"] == "正片")
ck("只有一个小节时 section_map 为空（不显示小节）",
   c.section_map(secs) == {})
ck("get_video_section_title 也返回空（不写「小节：正片」）",
   asyncio.run(c.get_video_section_title("BV2", 1001)) == "")


# ------------------------------------------------------- ② 分了节要显示
print("\n[2] 真的分了小节时才显示")


class TwoSections(OneSection):
    """正片 + 花絮两节；最新那条在**花絮**里（第二节的末尾）。"""

    UGC = {"sections": [
        _sec("正片", [_ep("BV1", NOW - 10 * DAY), _ep("BV2", NOW - 5 * DAY)]),
        _sec("花絮", [_ep("BV3", NOW - DAY), _ep("BV4", NOW)]),
    ]}


c2 = TwoSections("")
s2 = asyncio.run(c2.get_season_sections("BV1", 1001))
m2 = c2.section_map(s2)
ck("两节都取到", len(s2) == 2)
ck("分了节就有 bvid→小节名", m2.get("BV4") == "花絮" and m2.get("BV1") == "正片",
   str(m2))
ck("小节名按视频对得上", asyncio.run(
    c2.get_video_section_title("BV4", 1001)) == "花絮")


# --------------------------------------------- ③ 按小节比时间线挑最新
print("\n[3] 按小节比时间线（扁平数组会错位的那种）")

ep, st = c2.newest_in_sections(s2)
ck("挑到的是时间线最新那条", ep and ep["bvid"] == "BV4", str(ep and ep["bvid"]))
ck("同时给出它属于哪个小节", st == "花絮", str(st))
ck("挑出来的 pubdate 确实是最大的",
   ep and ep["pubdate"] == NOW, str(ep and ep["pubdate"]))

# 顺序错位的具体场景：扁平数组里"最新"排在中间/靠前都行，
# 但小节扫描必须无视这个顺序
ugs_mixed = {"sections": [
    _sec("正片", [_ep("BV9", NOW)]),                 # 最新在这，但只有一条
    _sec("花絮", [_ep("BV8", NOW - 100 * DAY)]),
]}
c3 = OneSection("")
c3.UGC = ugs_mixed
ep3, st3 = c3.newest_in_sections(asyncio.run(c3.get_season_sections("BV9", 1)))
ck("最新落在第一节也能挑出来", ep3 and ep3["bvid"] == "BV9")

# 全体都没有 pubdate 时：不能拿"第一个"冒充最新
ugs_nopub = {"sections": [_sec("正片", [{"bvid": "BVa", "title": "a"}]),
                          _sec("花絮", [{"bvid": "BVb", "title": "b"}])]}
c4 = OneSection("")
c4.UGC = ugs_nopub
s4 = asyncio.run(c4.get_season_sections("BVa", 1))
ck("没有 pubdate 时归一化成 0（不报错）",
   all(e["pubdate"] == 0 for s in s4 for e in s["episodes"]))
ep4, _ = c4.newest_in_sections(s4)
ck("全 0 时挑第一个但不谎称更新", ep4 and ep4["pubdate"] == 0)


# --------------------------------------- ④ pubdate 藏在 arc 里也能拿到
print("\n[4] 字段位置不固定（episode.arc）也能解析")


class ArcStyle(OneSection):
    UGC = {"sections": [
        {"title": "正片", "episodes": [
            {"bvid": "BVx", "arc": {"title": "X", "pic": "px",
                                    "pubdate": NOW - 9 * DAY}}]},
        {"title": "花絮", "episodes": [
            {"bvid": "BVy", "arc": {"title": "Y", "pic": "py",
                                    "ctime": NOW}}]},
    ]}


c5 = ArcStyle("")
s5 = asyncio.run(c5.get_season_sections("BVx", 1))
ep5, st5 = c5.newest_in_sections(s5)
ck("arc.pubdate 能读到", any(e["pubdate"] == NOW - 9 * DAY
                             for e in s5[0]["episodes"]))
ck("arc.ctime 也能当发布时间", ep5 and ep5["pubdate"] == NOW, str(ep5))
ck("arc 里的标题/封面也带出来了",
   ep5 and ep5["title"] == "Y" and ep5["pic"] == "py", str(ep5))


# ------------------------------- ⑤ newest_season_video：小节比列表更新
print("\n[5] 推送用的 newest_season_video 走小节比对")


class FlatOld(BiliClient):
    """扁平列表只给了旧视频（分页没翻到 / 顺序错位），
    小节结构里才有真正最新的那条。"""

    async def get_season_archives(self, uid, season_id, max_pages=2):
        return SeasonInfo(
            season_id=season_id, uid=uid, title="星瞳のVLOG", total=60,
            videos=[SeasonVideo(bvid="BV_old", title="老视频",
                                pic="p_old", pubdate=NOW - 30 * DAY)])

    async def _fetch_ugc_season(self, bvid):
        return {"sections": [
            _sec("正片", [_ep("BV_old", NOW - 30 * DAY)]),
            _sec("旅行", [_ep("BV_new", NOW, "狂炫云南菌子宴", "p_new")]),
        ]}


got = asyncio.run(FlatOld("").newest_season_video(1, 1001))
ck("小节里更新的那条被挑中", got and got["push"].bvid == "BV_new",
   str(got and got["push"].bvid))
ck("标题/封面用的是小节里那条",
   got and got["push"].title == "狂炫云南菌子宴"
   and got["push"].pic == "p_new")
ck("同时带出小节名", got and got["push"].section == "旅行",
   str(got and got["push"].section))


# ------------------------------- ⑥ 单节时不为小节名发请求、也不显示
class OneOnly(BiliClient):
    async def get_season_archives(self, uid, season_id, max_pages=2):
        return SeasonInfo(season_id=season_id, uid=uid, title="某合集",
                          total=2, videos=[
                              SeasonVideo(bvid="BV1", title="t1",
                                          pic="p1", pubdate=NOW - DAY),
                              SeasonVideo(bvid="BV2", title="t2",
                                          pic="p2", pubdate=NOW)])

    async def _fetch_ugc_season(self, bvid):
        return {"sections": [_sec("正片", [_ep("BV1", NOW - DAY),
                                           _ep("BV2", NOW)])]}


g1 = asyncio.run(OneOnly("").newest_season_video(1, 1002))
ck("只有一个小节：不显示小节名", g1 and g1["push"].section == "",
   str(g1 and g1["push"].section))
ck("只有一个小节：仍挑最新那条", g1 and g1["push"].bvid == "BV2")


# --------------------------------------- ⑦ 小节结构取不到时不能拖垮检测
class Broken(BiliClient):
    async def get_season_archives(self, uid, season_id, max_pages=2):
        return SeasonInfo(season_id=season_id, uid=uid, title="某合集",
                          total=1, videos=[SeasonVideo(
                              bvid="BV1", title="t1", pic="p1",
                              pubdate=NOW)])

    async def _fetch_ugc_season(self, bvid):
        raise RuntimeError("详情接口挂了")


g2 = asyncio.run(Broken("").newest_season_video(1, 1003))
ck("小节接口挂了也不抛异常", g2 and g2["push"].bvid == "BV1")
ck("小节接口挂了就当没分节", g2 and g2["push"].section == "")


# --------------------------------------- ⑧ 合集没有 ugc_season（普通视频）
class NoUgc(BiliClient):
    async def get_season_archives(self, uid, season_id, max_pages=2):
        return SeasonInfo(season_id=season_id, uid=uid, title="某合集",
                          total=1, videos=[SeasonVideo(
                              bvid="BV1", title="t1", pic="p1",
                              pubdate=NOW)])

    async def _fetch_ugc_season(self, bvid):
        return {}


g3 = asyncio.run(NoUgc("").newest_season_video(1, 1004))
ck("没有 ugc_season 时正常返回（不崩）", g3 and g3["push"].bvid == "BV1")


# --------------------------------------- ⑨ 小节里多出来的视频要并进列表
print("\n[6] bot 侧：小节里多出来的视频要并进检测")

import bot  # noqa: E402


class FakeStore:
    def get_up(self, uid):
        return {"uname": "星瞳"}


class FakeBili2:
    async def get_season_sections(self, bvid, season_id=0):
        return [_sec("正片", [_ep("BV1", NOW - DAY)]),
                _sec("花絮", [_ep("BV2", NOW, "新加的花絮", "p2")])]

    def section_map(self, sections):
        real = [s for s in (sections or []) if s.get("episodes")]
        if len(real) <= 1:
            return {}
        return {e["bvid"]: (s.get("title") or "")
                for s in real for e in (s.get("episodes") or [])
                if e.get("bvid")}


class FakeBot(bot.NotifyBot):
    def __init__(self):
        self.cfg = {}
        self.bili = FakeBili2()
        self.store = FakeStore()
        self._silent = True


b = FakeBot()
info = SeasonInfo(season_id=1, uid=1, title="某合集", total=30,
                  videos=[SeasonVideo(bvid="BV1", title="t1", pic="p1",
                                      pubdate=NOW - DAY)])
asyncio.run(b._season_apply_sections(info, 1))
ck("小节里多出来的视频被并进列表",
   sorted(v.bvid for v in info.videos) == ["BV1", "BV2"],
   str([v.bvid for v in info.videos]))
ck("并进来的视频带 pubdate（能参与新旧比对）",
   any(v.bvid == "BV2" and v.pubdate == NOW for v in info.videos))
ck("sec_map 挂好了（推送时不用再逐条查）",
   getattr(info, "sec_map", None) == {"BV1": "正片", "BV2": "花絮"},
   str(getattr(info, "sec_map", None)))

# 单节 → 什么都不动
class FakeBili3(FakeBili2):
    async def get_season_sections(self, bvid, season_id=0):
        return [_sec("正片", [_ep("BV1", NOW - DAY)])]


b.bili = FakeBili3()
info2 = SeasonInfo(season_id=1, uid=1, title="某合集", total=2,
                   videos=[SeasonVideo(bvid="BV1", title="t1", pic="p1",
                                       pubdate=NOW - DAY)])
asyncio.run(b._season_apply_sections(info2, 1))
ck("只有一个小节：不并、也不显示",
   len(info2.videos) == 1 and getattr(info2, "sec_map", None) == {})

# 小节接口挂了 → 退回老行为，不能把检测搞挂
class FakeBili4(FakeBili2):
    async def get_season_sections(self, bvid, season_id=0):
        raise RuntimeError("挂了")


b.bili = FakeBili4()
info3 = SeasonInfo(season_id=1, uid=1, title="某合集", total=2,
                   videos=[SeasonVideo(bvid="BV1", title="t1", pic="p1",
                                       pubdate=NOW - DAY)])
try:
    asyncio.run(b._season_apply_sections(info3, 1))
    ck("小节接口挂了也不拖垮检测", True)
except Exception as e:
    ck("小节接口挂了也不拖垮检测", False, repr(e))
ck("挂了就当没分节", getattr(info3, "sec_map", None) == {})


# --------------------------------------- ⑩ 自检第七项也要显示小节名
print("\n[7] 自检「合集检测」那一行的文案")

import webui  # noqa: E402


class DiagBili(BiliClient):
    async def get_up_seasons(self, uid, page_size=20):
        return [{"id": 2001, "title": "整活日记", "total": 2,
                 "mtime": NOW, "type": "season"}]

    async def get_season_archives(self, uid, season_id, max_pages=2):
        return SeasonInfo(season_id=season_id, uid=uid, title="整活日记",
                          total=2, videos=[SeasonVideo(
                              bvid="BV1", title="第1期", pic="p1",
                              pubdate=NOW - DAY)])

    async def _fetch_ugc_season(self, bvid):
        return {"sections": [
            _sec("正片", [_ep("BV1", NOW - DAY)]),
            _sec("花絮", [_ep("BV2", NOW, "幕后花絮", "p2")]),
        ]}

    async def close(self):
        pass


d1 = asyncio.run(webui._probe_season_diag(DiagBili(""), 1))
ck("分了小节时，自检结果里写出小节名", "花絮" in d1, d1)
ck("自检结果里仍有合集名", "整活日记" in d1, d1)


class DiagOne(DiagBili):
    async def _fetch_ugc_season(self, bvid):
        return {"sections": [_sec("正片", [_ep("BV1", NOW - DAY)])]}


d2 = asyncio.run(webui._probe_season_diag(DiagOne(""), 1))
ck("只有一个小节时，自检结果里不写小节", "小节" not in d2, d2)


bad = [n for n, ok in results if not ok]
print(f"\n小节处理：{len(results) - len(bad)}/{len(results)} 通过")
if bad:
    print("失败项：")
    for n in bad:
        print("  - " + n)
    sys.exit(1)
