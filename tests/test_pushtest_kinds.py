# -*- coding: utf-8 -*-
"""推送测试「全部跑一遍」的类型修正 + 修过的逻辑延伸到其它入口（v1.35.0）。

要锁的坑：
 1. 置顶动态测试不能渲染成普通「新动态」
    （否则 7 条里会出现两张一样的动态卡片）
 2. 置顶动态**正式推送**同样要走 📌 分支（同一个标记，不能只修测试）
 3. 合集真实数据必须给出 SeasonPush，不是 SeasonInfo（字段对不上）
 4. 「挑合集 + 按发布时间挑最新」是共用方法，自检和推送测试都走它
 5. 置顶评论的 rid / comment_type / comment_id 三件套，
    自检那条路也要带上（不能只有推送测试有）
"""
import asyncio
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

BILI = os.path.join(ROOT, "bilibili.py")
WEBUI = os.path.join(ROOT, "webui.py")

FAILS = []


def ck(name, cond, extra=""):
    if cond:
        print("  PASS  " + name)
    else:
        print("  FAIL  " + name + ("  " + extra if extra else ""))
        FAILS.append(name)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


bili = read(BILI)
web = read(WEBUI)

print("\n[1] 置顶动态：get_pinned_dynamic 必须带回 pinned=True")
ck("命中置顶标记时显式置 True", "hit.pinned = True" in bili)
ck("标记没认出来（退回第一条）也置 True",
   bili.count("d.pinned = True") >= 2,
   "只置了一处，退回路径仍会丢标记")

# 真跑一遍：造一份**没有任何置顶标记**的返回，走 fallback 路径
import bilibili as B  # noqa: E402

PAYLOAD = {"code": 0, "data": {"items": [
    {"id_str": "111", "modules": {"module_author": {"pub_ts": 1700000000},
                                  "module_dynamic": {"major": None}}},
]}}


class FakeTop(B.BiliClient):
    async def _get(self, url, params=None, referer=None, **kw):
        return PAYLOAD

    async def ensure_buvid3(self):
        return None

    async def _throttle(self, key=""):
        return None


d = asyncio.run(FakeTop("").get_pinned_dynamic(1))
ck("fallback 路径也返回了动态", bool(d and d.dyn_id), repr(d))
ck("fallback 路径 pinned=True", bool(d and getattr(d, "pinned", False)),
   "仍为 False → 会渲染成普通新动态")


print("\n[2] render_dynamic 靠 pinned 区分两张卡片")
from notify import render  # noqa: E402
from bilibili import DynamicInfo  # noqa: E402

base = dict(dyn_id="999", title="标题", text="正文", images=[])
md_pin, _ = render("dynamic", DynamicInfo(**base, pinned=True), "UP", {})
md_new, _ = render("dynamic", DynamicInfo(**base, pinned=False), "UP", {})
ck("置顶卡片是 📌 置顶动态", "置顶动态" in md_pin, md_pin[:60])
ck("普通卡片是 📝 新动态", "置顶动态" not in md_new, md_new[:60])


print("\n[3] 推送测试：真实数据按 kind 取，不按 render_kind")
seg = web[web.index("def _real_demo("):web.index("def _real_demo(") + 4000]
ck("置顶动态走 get_pinned_dynamic",
   'kind == "dynamic_pinned"' in seg and "get_pinned_dynamic" in seg)
ck("分支用的是 kind 而不是 render_kind",
   'if render_kind == "dynamic_pinned"' not in web,
   "仍按 render_kind 分支 → 会取到最新动态")
ck("普通动态仍走 get_latest_dynamic", 'if kind == "dynamic"' in seg)
ck("取完再补一次 pinned（双保险）",
   'if kind == "dynamic_pinned"' in web
   and web.count('setattr(info, "pinned", True)') >= 1)


print("\n[4] 合集：共用方法 newest_season_video")
ck("BiliClient 有 newest_season_video",
   "async def newest_season_video" in bili)
ck("推送测试的真实数据走共用方法", 'newest_season_video(' in web)
ck("自检第七项也走共用方法（不各写一份）",
   web.count('newest_season_video(') >= 2,
   "只有一处调用了")


class FakeSeason(B.BiliClient):
    ORDER = "new_first"

    async def get_up_seasons(self, uid, page_size=20):
        return [{"id": 777, "title": "测试合集", "total": 3,
                 "mtime": 1700000000, "type": "season"}]

    async def get_season_archives(self, uid, season_id, max_pages=2):
        vids = [B.SeasonVideo(bvid="BV_old", aid=1, title="旧视频",
                              pic="p1", pubdate=1600000000),
                B.SeasonVideo(bvid="BV_new", aid=2, title="新视频",
                              pic="p2", pubdate=1800000000),
                B.SeasonVideo(bvid="BV_mid", aid=3, title="中间视频",
                              pic="p3", pubdate=1700000000)]
        if self.ORDER == "old_first":
            vids = list(reversed(vids))
        return B.SeasonInfo(season_id=season_id, uid=uid, title="测试合集",
                            cover="c", total=3, videos=vids)

    async def get_season_sections(self, bvid, season_id=0):
        # ⚠️ 真的分了小节（两节）→ 小节名才显示；只有一个"正片"不显示。
        #    而且最新那条 BV_new 在**第二节**里 —— 正是"按小节比时间线"
        #    才挑得出来的情况（只看扁平数组会错位）。
        return [{"title": "正片", "episodes": [
                    {"bvid": "BV_old", "title": "旧视频", "pic": "p1",
                     "pubdate": 1600000000},
                    {"bvid": "BV_mid", "title": "中间视频", "pic": "p3",
                     "pubdate": 1700000000}]},
                {"title": "花絮", "episodes": [
                    {"bvid": "BV_new", "title": "新视频", "pic": "p2",
                     "pubdate": 1800000000}]}]

    def section_map(self, sections):
        real = [s for s in (sections or []) if s.get("episodes")]
        if len(real) <= 1:
            return {}
        return {e["bvid"]: (s.get("title") or "")
                for s in real
                for e in (s.get("episodes") or [])
                if e.get("bvid")}


for order in ("new_first", "old_first"):
    c = FakeSeason("")
    c.ORDER = order
    got = asyncio.run(c.newest_season_video(1, 777))
    ck(f"{order}：挑到的是最新那条", got and got["push"].bvid == "BV_new",
       str(got and got["push"].bvid))
    ck(f"{order}：返回的是 SeasonPush（有 bvid/pic/season_title/section）",
       got and got["push"].pic == "p2"
       and got["push"].season_title == "测试合集"
       and got["push"].section == "花絮")

c = FakeSeason("")
c.ORDER = "new_first"
got2 = asyncio.run(c.newest_season_video(1, 0))
ck("没给 season_id 时自动挑最近更新的合集",
   got2 and got2["season_id"] == 777, str(got2 and got2.get("season_id")))
ck("自动挑时标了来源", got2 and "自动挑" in (got2.get("src") or ""),
   str(got2 and got2.get("src")))


class NoSeason(B.BiliClient):
    async def get_up_seasons(self, uid, page_size=20):
        return []


ck("没有合集时返回 None（不抛异常）",
   asyncio.run(NoSeason("").newest_season_video(1, 0)) is None)


print("\n[5] SeasonInfo 交给 render 是坏的（旧写法），SeasonPush 才是好的")
bad_md, bad_btn = render("season", B.SeasonInfo(season_id=1, uid=1,
                                                title="T", videos=[]), "UP", {})
good_md, good_btn = render("season", B.SeasonPush(
    bvid="BV_new", title="新视频", pic="p2",
    season_title="测试合集", section="正片"), "UP", {})
# button() 返回 (标题, url, 回执)
bad_url = (bad_btn[0][1] if bad_btn else "")
good_url = (good_btn[0][1] if good_btn else "")
ck("旧写法（SeasonInfo）按钮链到合集页而不是视频",
   "BV_new" not in bad_url, bad_url)
ck("新写法（SeasonPush）按钮链到具体视频",
   "BV_new" in good_url, good_url)
ck("新写法卡片里有合集名和小节", "测试合集" in good_md and "正片" in good_md)


print("\n[6] 置顶评论三件套：自检那条路也要带")
calls = []
_pos = 0
while True:
    _i = web.find("cm = await c.get_pinned_comment(", _pos)
    if _i < 0:
        break
    calls.append(web[_i:_i + 400])
    _pos = _i + 1
full = [c for c in calls if "rid=" in c and "comment_type=" in c
        and "comment_id=" in c]
ck("调用点至少两处（推送测试 + 自检）", len(calls) >= 2, str(len(calls)))
ck("每一处都带 rid/comment_type/comment_id 三件套",
   len(full) == len(calls), "%d/%d" % (len(full), len(calls)))


print("\n[7] 全量跑一遍的顺序覆盖 7 种类型")
import json  # noqa: E402
appjs = read(os.path.join(ROOT, "static", "app.js"))
ks = re.findall(r"\{ key: '([a-z_]+)', nm:", appjs)
for want in ("live", "offline", "video", "dynamic", "dynamic_pinned",
             "top_comment", "season"):
    ck("前端 PUSH_KINDS 含 " + want, want in ks, str(ks))

print("\n" + "=" * 60)
if FAILS:
    print("FAILED: %d" % len(FAILS))
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("ALL PASSED")
