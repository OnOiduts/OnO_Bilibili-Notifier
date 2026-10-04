"""推送测试四类内容的图片内嵌 + 合集前后比对。

用户反馈：
1. 勾了「用真实数据」仍然没图 —— 视频要有封面、动态/置顶动态有图要插、
   合集用最新视频封面、评论有图也要插。
2. 合集里视频顺序可能是混合的（早期正序、后来倒序），
   只看第一页判断方向会错过最新那条，必须前后比对。
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from bilibili import (SeasonVideo, _comment_images, _pick_top_reply)  # noqa: E402
from notify import render  # noqa: E402

PIC = "https://i0.hdslb.com/bfs/archive/abc.jpg"


def _has_img(md):
    """卡片里有内嵌图（![alt](url)，alt 不能为空）。"""
    for line in str(md or "").splitlines():
        m = re.match(r"!\[([^\]]+)\]\((https://[^)\s]+)\)$", line.strip())
        if m:
            return True
    return False


# ────────────────── 1. 视频封面 ──────────────────
def test_video_cover_inline():
    from bilibili import VideoInfo
    v = VideoInfo(bvid="BV1", title="标题", pic=PIC, desc="", pubdate=0)
    md, _ = render("video", v, "UP", {})
    assert _has_img(md), "视频卡片必须内嵌封面"
    # QQ markdown 图片必须带尺寸提示，否则手机端不渲染。
    assert "![#" in md and "px #" in md and "px](https://" in md, \
        "封面必须带 QQ 尺寸提示（![#宽px #高px](url)）"


# ────────────────── 2. 动态 / 置顶动态配图 ──────────────────
def _dyn(pinned=False, images=None):
    from bilibili import DynamicInfo
    return DynamicInfo(dyn_id="1", title="", text="正文",
                       images=images or [], pinned=pinned, pubdate=0)


def test_dynamic_image_inline():
    md, _ = render("dynamic", _dyn(images=[PIC]), "UP", {})
    assert _has_img(md), "动态卡片必须内嵌配图"


def test_dynamic_pinned_image_inline():
    md, _ = render("dynamic", _dyn(pinned=True, images=[PIC]), "UP", {})
    assert _has_img(md), "置顶动态同样要内嵌配图"
    assert "置顶" in md


def test_dynamic_no_image_ok():
    md, _ = render("dynamic", _dyn(images=[]), "UP", {})
    assert not _has_img(md), "没图就不该凭空插图"


# ────────────────── 3. 合集用最新视频的封面 ──────────────────
def test_season_cover_inline():
    from bilibili import SeasonPush
    p = SeasonPush(bvid="BV2", title="合集视频",
                   pic=PIC, season_title="某合集", section="正片")
    md, _ = render("season", p, "UP", {})
    assert _has_img(md), "合集卡片要用最新视频的封面"


# ────────────────── 4. 评论图片 ──────────────────
def test_comment_images_extracted():
    data = {"data": {"top_replies": [{
        "rpid_str": "9",
        "member": {"uname": "UP", "mid": 1},
        "content": {"message": "看图",
                    "pictures": [{"img_src": PIC}]},
    }]}}
    got = _pick_top_reply(data)
    assert got and got.get("images") == [PIC], "评论图片必须被提取出来"


def test_comment_images_dict_and_str():
    # content 有时是 dict，有时是 JSON 字符串，都不能崩
    assert _comment_images({"pictures": [{"img_src": PIC}]}) == [PIC]
    import json
    assert _comment_images(json.dumps(
        {"pictures": [{"img_src": PIC}]})) == [PIC]
    assert _comment_images("not a dict") == []
    assert _comment_images(None) == []


def test_comment_image_inline():
    md, _ = render("top_comment", {"content": "看图", "images": [PIC]},
                   "UP", {})
    assert _has_img(md), "评论卡片必须内嵌图片"


def test_comment_no_image_ok():
    md, _ = render("top_comment", {"content": "纯文字", "images": []},
                   "UP", {})
    assert not _has_img(md)


# ────────────────── 5. 合集前后比对 ──────────────────
class _FakeClient:
    """模拟 seasons_archives_list：可指定"每页的内容"和 total。"""

    def __init__(self, pages, total):
        self.pages = pages      # {pn: [SeasonVideo]}
        self.total = total
        self.asked = []

    async def ensure_buvid3(self):
        return None

    async def _wbi_sign(self, base):
        return base

    async def _throttle(self, k):
        return None

    async def _get(self, url, params, referer=None):
        pn = int(params.get("page_num") or 1)
        self.asked.append(pn)
        arr = self.pages.get(pn, [])
        return {"data": {"archives": [
            {"bvid": v.bvid, "aid": 1, "title": v.title,
             "pic": v.pic, "pubdate": v.pubdate} for v in arr],
            "meta": {"name": "c"}, "page": {"total": self.total}}}


def _mk(n, base_ts, reverse, prefix="BV"):
    """造 n 条视频，reverse=True 表示新的在前。

    ⚠️ 不同页必须用不同 prefix：跨页去重是按 bvid 做的，
    三页都用 BV0..BV29 的话尾页会被当成重复全部丢掉，
    那测出来的"没取到尾页"是测试数据的问题，不是代码的问题。
    """
    out = []
    for i in range(n):
        ts = base_ts + (n - i if reverse else i)
        out.append(SeasonVideo(bvid=f"{prefix}{i}", aid=i, title=f"t{i}",
                               pic="", pubdate=ts))
    return out


def _run(coro):
    # ⚠️ 不能用 asyncio.get_event_loop()：Python 3.10+ 在没有当前事件循环的
    #    线程里会直接抛 RuntimeError（3.12 起更是直接移除），于是这几个用例
    #    在别的机器上**莫名其妙全红**，看着像合集分页回归了，其实只是
    #    测试辅助函数太老。asyncio.run 每次新建一个干净的 loop，行为一致。
    return asyncio.run(coro)


def _mkclient(fake):
    """造一个只打桩网络/签名的 BiliClient（都是真 async，不嵌套事件循环）。"""
    import bilibili

    class _C(bilibili.BiliClient):
        async def ensure_buvid3(self):
            return None

        async def _wbi_sign(self, base):
            return base

        async def _throttle(self, key):
            return None

        async def _get(self, url, params, referer=None):
            return await fake._get(url, params, referer=referer)

    return _C("")


def _patched(fake):
    import bilibili
    async def _fetch(self, pn):
        from bilibili import SeasonVideo
        data = await fake._get("", {"page_num": pn}, referer=None)
        d = data["data"]
        arr = [SeasonVideo(bvid=a["bvid"], aid=a["aid"], title=a["title"],
                           pic=a["pic"], pubdate=a["pubdate"])
               for a in d["archives"]]
        return arr, d["meta"], int(d["page"]["total"])
    return _fetch


def test_season_mixed_order_head_and_tail():
    """混合顺序：第 1 页是最老的(正序)、最后一页是最新的(倒序)。

    只看第 1 页首尾会判断成"旧的在前/正序"，于是往第 2 页翻，
    完美错过最后一页的最新那条。前后比对才拿得到。
    """
    import bilibili
    oldest = _mk(30, 1000, reverse=False, prefix="A")   # 第 1 页：正序(老→新)
    mid = _mk(30, 1030, reverse=False, prefix="B")
    newest = _mk(30, 1060, reverse=True, prefix="C")    # 第 3 页(尾页)：最新
    fake = _FakeClient({1: oldest, 2: mid, 3: newest}, total=90)

    c = _mkclient(fake)
    info = _run(c.get_season_archives(1, 1, max_pages=2))
    assert info, "必须取到合集"
    got = max(v.pubdate for v in info.videos)
    best = max(v.pubdate for v in oldest + mid + newest)
    assert got == best, f"前后比对应拿到最新那条：{got} != {best}"
    assert 3 in fake.asked, f"尾页必须被取到，实际取了 {fake.asked}"


def test_season_newest_first_still_works():
    """新的在第 1 页时，仍然能拿到最新。"""
    import bilibili
    p1 = _mk(30, 1060, reverse=True, prefix="A")   # 最新在第 1 页
    p3 = _mk(30, 1000, reverse=False, prefix="C")
    fake = _FakeClient({1: p1, 3: p3}, total=60)
    c = _mkclient(fake)
    info = _run(c.get_season_archives(1, 1, max_pages=2))
    got = max(v.pubdate for v in info.videos)
    assert got == max(v.pubdate for v in p1 + p3)


def test_season_single_page_no_extra_fetch():
    """总共就一页时，不该再去翻尾页。"""
    import bilibili
    one = _mk(5, 1000, reverse=False)
    fake = _FakeClient({1: one}, total=5)
    c = _mkclient(fake)
    info = _run(c.get_season_archives(1, 1, max_pages=2))
    assert len(info.videos) == 5
    assert fake.asked == [1], f"只有一页不该多翻，实际 {fake.asked}"


def test_season_dedup():
    """首页和尾页有重叠时，去重后不重复计数。"""
    import bilibili
    same = _mk(30, 1000, reverse=False)
    fake = _FakeClient({1: same, 2: same}, total=30)
    c = _mkclient(fake)
    info = _run(c.get_season_archives(1, 1, max_pages=2))
    bvids = [v.bvid for v in info.videos]
    assert len(bvids) == len(set(bvids)), "跨页重复必须去重"


def ck(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    return bool(cond)


def main():
    ok = True
    for fn in (test_video_cover_inline, test_dynamic_image_inline,
               test_dynamic_pinned_image_inline, test_dynamic_no_image_ok,
               test_season_cover_inline, test_comment_images_extracted,
               test_comment_images_dict_and_str, test_comment_image_inline,
               test_comment_no_image_ok, test_season_mixed_order_head_and_tail,
               test_season_newest_first_still_works,
               test_season_single_page_no_extra_fetch, test_season_dedup):
        try:
            fn()          # 用 assert 断言，没抛异常即通过
            ok &= ck(fn.__name__, True)
        except AssertionError as e:
            ok = ck(fn.__name__ + f" {e}", False)
        except Exception as e:
            import traceback
            traceback.print_exc()
            ok = ck(fn.__name__ + f" (异常 {e})", False)
    print("-" * 60)
    print("ALL PASS" if ok else "HAS FAILURE")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
