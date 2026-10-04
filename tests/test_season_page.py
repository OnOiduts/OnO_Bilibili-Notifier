# -*- coding: utf-8 -*-
"""合集分页取数测试：新的视频必须能翻到（不管接口按哪个方向排序）。

背景（真 bug）：
以前一律从第 1 页往后翻。接口有个 sort_reverse 参数，不同资料对
true/false 谁升序谁降序说法相反，「默认排序」还可能按"加入合集的顺序"排。
一旦拿到的是**旧视频在前**，第 1、2 页永远是合集中最老的那 60 个，
UP 主新传的视频排在最后一页，永远翻不到 —— 合集更新检测彻底失效。

现在先取第 1 页判断方向：新在前就往后翻，新在后就从最后一页往回翻。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.environ.setdefault("BOT_CONFIG",
                      os.path.join(sys.path[0], "config.yaml"))

from bilibili import BiliClient  # noqa: E402

PAGE_SIZE = 30
OK = 0
FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def mk_video(i):
    """第 i 个视频：pubdate 随 i 递增（i 越大越新）。"""
    return {"bvid": f"BV{i:04d}", "aid": 1000 + i, "title": f"t{i}",
            "pic": "", "pubdate": 1700000000 + i}


def mk_page(items, total):
    return {"code": 0, "data": {
        "meta": {"name": "测试合集", "cover": ""},
        "page": {"total": total, "page_size": PAGE_SIZE},
        "archives": items,
    }}


def mk_client(pages, order):
    """pages: 总共多少页；order: 'new_first' | 'old_first'
    返回值按 (路径, 参数) 给出该页内容。"""
    allv = [mk_video(i) for i in range(1, pages * PAGE_SIZE + 1)]
    total = len(allv)

    def page_items(pn):
        seg = allv[(pn - 1) * PAGE_SIZE: pn * PAGE_SIZE]
        # 顺序：new_first = 新的在前（整集倒序后分页）
        #       old_first = 旧的在前
        ordered = list(reversed(seg)) if order == "new_first" else seg
        # 注意：整集层面也要倒过来，否则"第1页"永远是同一批
        return ordered

    # 整集按 order 重排后再分页
    whole = list(reversed(allv)) if order == "new_first" else list(allv)
    table = {}
    for pn in range(1, pages + 1):
        table[pn] = whole[(pn - 1) * PAGE_SIZE: pn * PAGE_SIZE]

    calls = []

    async def _get(url, params, referer=None, **kw):
        pn = int(params.get("page_num") or 1)
        calls.append(pn)
        return mk_page(table.get(pn, []), total)

    c = object.__new__(BiliClient)
    c._get = _get
    c.calls = calls

    async def _noop(*a, **k):
        return None
    c.ensure_buvid3 = _noop
    c._throttle = _noop

    async def _sign(base):
        return dict(base)
    c._wbi_sign = _sign
    return c, allv


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


print("=== 1) 新的在前（倒序）：往后翻就能拿到最新 ===")
# 3 页共 90 个，新的在前 → 第 1 页就是最新的 30 个
c, allv = mk_client(3, "new_first")
info = run(c.get_season_archives(1, 1, max_pages=2))
bvs = [v.bvid for v in info.videos]
chk("第 1 页就是最新的（BV0090 在里面）", "BV0090" in bvs, bvs[:3])
chk("只翻 2 页 = 60 条", len(bvs) == 60, len(bvs))

print("=== 2) 旧的在前（升序）：必须跳到最后一页 ===")
c, allv = mk_client(3, "old_first")
info = run(c.get_season_archives(1, 1, max_pages=2))
bvs = [v.bvid for v in info.videos]
chk("拿到最新的 BV0090（以前永远拿不到）", "BV0090" in bvs, bvs[:3])
chk("拿到次新的 BV0089", "BV0089" in bvs)
chk("翻的页里包含最后一页 3", 3 in c.calls, c.calls)
chk("不会只停在第 1、2 页", sorted(set(c.calls)) != [1, 2], c.calls)

print("=== 3) 单页合集：不重复翻页 ===")
c, allv = mk_client(1, "old_first")
info = run(c.get_season_archives(1, 1, max_pages=3))
chk("只请求第 1 页", c.calls == [1], c.calls)
chk("拿到 30 条", len(info.videos) == 30, len(info.videos))

print("=== 4) 新的在前 + 总数已知：不多翻 ===")
c, allv = mk_client(2, "new_first")
info = run(c.get_season_archives(1, 1, max_pages=5))
chk("只有 2 页时不硬翻第 3 页", 3 not in c.calls, c.calls)

print("=== 5) 跨页不重复 ===")
c, allv = mk_client(4, "old_first")
info = run(c.get_season_archives(1, 1, max_pages=3))
bvs = [v.bvid for v in info.videos]
chk("bvid 无重复", len(bvs) == len(set(bvs)), len(bvs) - len(set(bvs)))

print("=== 6) 基类上限：known 截断不会挤掉已见的（db 层） ===")
from db import Store  # noqa: E402
import tempfile  # noqa: E402
import inspect  # noqa: E402
src = inspect.getsource(Store.set_season_known)
chk("基线裁剪上限 >= 300（不能小于一次取回的量）", "300" in src)
chk("基线写入先去重", "fromkeys" in src)

print()
print(f"合计 {OK + FAIL} 项，失败 {FAIL} 项")
if FAIL:
    sys.exit(1)
print("全部通过 ✅")
