"""点「UP 主 / 合集」二选一就能订阅 —— 查询结果要顺带把合集带回来。

用户反馈：
    合集订阅为什么不能单独选，应该搞个和群与订阅一样的筛选，
    点击 up 主就给 up 主，点击合集就给合集。

以前订阅合集只能手动粘 space.bilibili.com/UID/lists/ID?type=season，
UID 和合集 ID 都得自己翻；而 UP 主订阅在同一张卡片里一个按钮就完事
—— 两套东西两种做法，很容易加错。

现在 /api/up/search 除了 UP 主，还返回：
    seasons   可订阅的合集（按"最近一次加视频"倒序）
    series_n  「视频列表」的个数（那是另一个东西，ID 不通用，只统计不给订）
    season_err 合集没取到时的原因（不能连 UP 主一起查不出来）
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import webui  # noqa: E402

results = []


def ck(name, cond):
    results.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name)


NOW = 1_784_000_000


def _run(seasons, sea_err=None):
    """把 _fetch_up / _fetch_up_seasons 换成同步桩，跑一次接口。"""
    async def _up(uid):
        return {"uid": int(uid), "uname": "甲", "face": "https://i0.hdslb.com/a.jpg",
                "room_id": 2263, "short_room_id": 0}

    async def _seasons(uid):
        if sea_err:
            raise RuntimeError(sea_err)
        return seasons

    old_up, old_sea = webui._fetch_up, webui._fetch_up_seasons
    webui._fetch_up, webui._fetch_up_seasons = _up, _seasons
    try:
        app = webui.app.test_client()
        return app.get("/api/up/search?uid=111").get_json()
    finally:
        webui._fetch_up, webui._fetch_up_seasons = old_up, old_sea


print("=== ① 合集按「最近一次加视频」倒序 ===")

d = _run([
    {"id": 901, "title": "老合集", "total": 12, "recent": NOW - 900 * 86400,
     "mtime": 0, "type": "season"},
    {"id": 902, "title": "新合集", "total": 3, "recent": NOW - 3600,
     "mtime": 0, "type": "season"},
])
ids = [s["id"] for s in d.get("seasons") or []]
ck("返回两个合集", len(ids) == 2)
ck("最新更新的排第一", ids[0] == 902)
ck("标题/数量都在", d["seasons"][0]["title"] == "新合集"
   and d["seasons"][0]["total"] == 3)

print("=== ② 视频列表(series) 只统计，不给订阅 ===")

d = _run([
    {"id": 901, "title": "真合集", "total": 5, "recent": NOW, "mtime": 0,
     "type": "season"},
    {"id": 77, "title": "视频列表", "total": 9, "recent": NOW, "mtime": 0,
     "type": "series"},
])
ids = [s["id"] for s in d.get("seasons") or []]
ck("series 不进 seasons", ids == [901])
ck("series_n 统计到了", d.get("series_n") == 1)

print("=== ③ 合集取不到时，UP 主照样查得出来 ===")

d = _run([], sea_err="模拟接口 412")
ck("up 还在", (d.get("up") or {}).get("uname") == "甲")
ck("ok 仍为真", d.get("ok") is True)
ck("seasons 是空列表", d.get("seasons") == [])
ck("season_err 带回来了", "412" in str(d.get("season_err") or ""))

print("=== ④ 没 recent 时退回 mtime，不会排成 0 ===")

d = _run([
    {"id": 1, "title": "A", "total": 1, "recent": 0, "mtime": NOW - 100,
     "type": "season"},
    {"id": 2, "title": "B", "total": 1, "recent": 0, "mtime": NOW,
     "type": "season"},
])
ck("mtime 兜底生效", [s["id"] for s in d["seasons"]] == [2, 1])

print("=== ⑤ 没有合集时不报错 ===")

d = _run([])
ck("seasons 空", d.get("seasons") == [])
ck("series_n 为 0", d.get("series_n") == 0)
ck("up 正常", (d.get("up") or {}).get("uid") == 111)

bad = [n for n, ok in results if not ok]
print()
print(f"通过 {len(results) - len(bad)}/{len(results)}")
if bad:
    print("失败：")
    for n in bad:
        print("  - " + n)
    sys.exit(1)
