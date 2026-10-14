# -*- coding: utf-8 -*-
"""新视频发布时，「动态」优先于「投稿」。

UP 主发新视频时 B 站先发一条**带视频卡的动态**（动态接口几乎实时），
个人主页的投稿列表要等索引才更新。用户看到的优先级就是：
「📝 新动态」为主（带 UP 主自己写的配文），「📺 新视频投稿」为备。

所以同一轮里投稿和动态都是新的时，必须推动态那条 —— 内容更全，
也符合 B 站的实际顺序；投稿随后由 `_should_silence_video` 静默。

⚠️ 以前 `_poll_once` 里 video 排在 dynamic 前面：同一轮两者都新时
投稿先推，配文就没了，动态反而被当成重复动态静默掉。
"""
import asyncio
import os
import sys

BASE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE_DIR)
os.environ["BOT_CONFIG"] = os.path.join(BASE_DIR, "config.yaml")

from bilibili import DynamicInfo, VideoInfo  # noqa: E402
from db import Store  # noqa: E402
import bot as B  # noqa: E402

DB = os.path.join(BASE_DIR, "_prio.db")

RESULTS = []


def ck(name, cond):
    ok = bool(cond)
    RESULTS.append((name, ok))
    print(("  [PASS] " if ok else "  [FAIL] ") + name)
    return ok


def _wipe(p):
    """Store 会把 .db 换成 .json 落盘，只删 .db 会留下上一轮的数据。"""
    for base in (p, os.path.splitext(p)[0] + ".json"):
        for suf in ("", "-wal", "-shm", ".lock"):
            try:
                os.remove(base + suf)
            except OSError:
                pass


class FakeQQ:
    def __init__(self):
        self.texts = []
        self.markdowns = []

    async def send_text(self, gid, text, **kw):
        self.texts.append((gid, text))

    async def send_markdown(self, gid, md, **kw):
        self.markdowns.append((gid, md))

    async def close(self):
        pass


class FakeBili:
    def __init__(self):
        self.bvid = "BV1aa"
        self.dyn_id = "100"
        # 发布时间：投稿与「投稿动态」几乎同一时刻发生，去重要靠它兜底
        self.pub = 0
        self.dyn_top_id = ""
        self.top_cmt_id = ""
        self.room_by_mid = 0
        self.fail = set()

    risk_hits = 0

    async def get_latest_video(self, uid):
        return VideoInfo(bvid=self.bvid, title="视频标题", pic="",
                         desc="简介", pubdate=self.pub)

    async def get_latest_dynamic(self, uid):
        d = DynamicInfo(dyn_id=self.dyn_id, title="动态标题",
                        text="UP主自己写的配文", images=[])
        d.pubdate = self.pub
        return d

    async def get_pinned_dynamic(self, uid):
        if not self.dyn_top_id:
            return None
        d = DynamicInfo(dyn_id=self.dyn_top_id, title="", text="", images=[])
        d.pinned = True
        return d

    async def get_pinned_comment(self, dyn_id, uid=0, rid="",
                                 comment_id="", comment_type=0):
        return None

    async def close(self):
        pass


def _bot(only_video=False):
    _wipe(DB)
    st = Store(DB)
    st.set_up(111, uname="测试UP", room_id=7788, face="")
    st.set_sub("GROUP_A", 111, "video", True)
    if not only_video:
        st.set_sub("GROUP_A", 111, "dynamic", True)
    st.refresh()
    bili = FakeBili()
    b = B.NotifyBot.__new__(B.NotifyBot)
    b.cfg = {"send_image": False, "image_send_mode": "off",
             "dedupe_window": 900}
    b.send_image = False
    b.store = st
    b.bili = bili
    b.qq = FakeQQ()
    b.tpls = {}
    b.notify_offline = False
    b.interval = 60
    b.slow_every = 1      # 每轮都查，便于测试
    b._round = 0
    return b, st, bili


def _sent(b):
    return [m for _, m in b.qq.markdowns] + [t for _, t in b.qq.texts]


async def t_dyn_wins_when_both_new():
    """同一轮里投稿和动态都是新的 → 只推动态那条。"""
    b, st, bili = _bot()
    await b._poll_once()
    ck("首次静默建基线", not _sent(b))

    # B 站发新视频的实际行为：动态先出（带视频卡），投稿随后进索引
    import time
    bili.pub = int(time.time())
    bili.bvid = "BV_NEW"
    bili.dyn_id = "D_NEW"
    b.qq.markdowns.clear()
    b.qq.texts.clear()
    await b._poll_once()

    got = _sent(b)
    ck("只推一条（不重复刷屏）", len(got) == 1)
    joined = "\n".join(got)
    ck("推的是「新动态」", "新动态" in joined)
    ck("不是「新视频投稿」", "新视频投稿" not in joined and "视频投稿" not in joined)
    ck("配文在正文里", "UP主自己写的配文" in joined)

    # 投稿基线也要跟上，否则下一轮会再补一条「新视频投稿」
    base_v = st.baseline_of("GROUP_A", 111, "video") or {}
    ck("投稿基线已对齐（不会补推）", base_v.get("bvid") == "BV_NEW")


async def t_video_only_still_pushes():
    """只订投稿、不订动态的群，投稿照样要推 —— 顺序调整不能把它弄丢。"""
    b, st, bili = _bot(only_video=True)
    await b._poll_once()
    ck("首次静默", not _sent(b))

    import time
    bili.pub = int(time.time())
    bili.bvid = "BV_NEW"
    b.qq.markdowns.clear()
    b.qq.texts.clear()
    await b._poll_once()

    got = _sent(b)
    ck("只订投稿时投稿照推", len(got) == 1)
    joined = "\n".join(got)
    ck("推的是「新视频投稿」", "视频投稿" in joined)


def main():
    print("== 新视频发布：动态优先于投稿 ==")
    asyncio.run(t_dyn_wins_when_both_new())
    asyncio.run(t_video_only_still_pushes())
    bad = [n for n, ok in RESULTS if not ok]
    print(f"\n合计 {len(RESULTS)} 项，失败 {len(bad)}")
    for n in bad:
        print("  FAIL: " + n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
