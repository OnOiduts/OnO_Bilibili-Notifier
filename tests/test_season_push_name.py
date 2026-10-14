# -*- coding: utf-8 -*-
"""锁定：合集更新的推送正文里，UP 主必须显示名字，不能是一串 uid。

用户反馈：只订了合集、没订 UP 主时，推送出来是
    📦 合集更新 280242571更万子姐录播了！|《2026/10/12/歌咋歌咋！》
而面板上同一个合集明明写着「伊万」—— 同一个 UP 主两个地方两个名字。

根因：推送那一路只认 `store.get_up(uid).get("uname")`，而**只订合集**时
ups 表里根本没有这一条（订阅合集不会往 ups 里写），于是退回 `str(uid)`。
面板那一路早就做了三级兜底（合集上记的 uname → ups → uid），
只有推送没跟上 —— 所以面板显示中文、发出去的消息是数字。

这里锁两件事：
  1) 端到端：走完整的 _check_one_season → 推送，正文里必须是名字
  2) 三级兜底的每一级都要对（合集名 → ups 名 → 都没有才 uid）
"""
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import botpy  # noqa: E402
from bilibili import SeasonInfo, SeasonVideo  # noqa: E402
from bot import NotifyBot, resolve_season_up_name  # noqa: E402

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
    return bool(cond)


UID = 280242571
SID = 3175959
GID = "GROUP_SEASON"
NAME = "伊万"


class FakeQQ:
    def __init__(self):
        self.sent = []

    async def send_text(self, gid, content, **kw):
        self.sent.append(content)

    async def send_markdown(self, gid, md, keyboard=None, **kw):
        self.sent.append(md)

    async def close(self):
        pass


class FakeBili:
    """合集列表可控：videos 里放什么就返回什么。"""

    def __init__(self, videos, title="合集·伊万 录播"):
        self.videos = list(videos)
        self.title = title

    async def get_season_archives(self, uid, season_id, max_pages=2):
        return SeasonInfo(season_id=int(season_id), uid=int(uid),
                          title=self.title, total=len(self.videos),
                          videos=list(self.videos))

    async def get_season_sections(self, bvid, season_id):
        return []

    def section_map(self, secs):
        return {}

    async def close(self):
        pass


def make_bot(videos):
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path},
                    intents=botpy.Intents(public_messages=True))
    bot.qq = FakeQQ()
    bot.bili = FakeBili(videos)
    bot._silent = False
    return bot, path


def run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _vid(bvid, title="万子姐录播了！|《2026/10/12/歌咋歌咋！》"):
    return SeasonVideo(bvid=bvid, title=title, pic="", pubdate=int(time.time()))


# ------------------------------------------------------------ 1 端到端
def t_push_uses_name_not_uid():
    """只订合集（ups 里没有这个人）时，推送正文必须是名字。"""
    bot, path = make_bot([_vid("BV_OLD")])
    try:
        st = bot.store
        # 订阅合集：带名字，但**不**往 ups 里写（真实订阅就是这样）
        st.add_season(GID, UID, SID, title="合集·伊万 录播", uname=NAME)
        ck(not (st.get_up(UID) or {}).get("uname"),
           "前置：ups 表里确实没有这个 UP 主（复现场景）")

        # 第一轮：建立基线，不推送
        run(bot._check_one_season(UID, SID))
        ck(not bot.qq.sent, "第一轮只记基线、不推送")

        # 第二轮：合集里多了一个新视频
        bot.bili.videos.append(_vid("BV_NEW"))
        bot.qq.sent.clear()
        run(bot._check_one_season(UID, SID))

        md = "\n".join(bot.qq.sent)
        if not ck(bool(md), "第二轮确实推送了合集更新"):
            return
        ck(NAME in md, "推送正文里有 UP 主名字「伊万」")
        ck(str(UID) not in md, "推送正文里不再出现 uid 数字")
        ck("合集·伊万 录播" in md, "合集名照常显示")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ------------------------------------------------------------ 2 三级兜底
def t_fallback_chain():
    """③ 级顺序：合集上记的名字 → ups → uid。"""
    bot, path = make_bot([])
    try:
        st = bot.store

        # ① 合集上有名字
        st.add_season(GID, UID, SID, title="合集·伊万 录播", uname=NAME)
        ck(resolve_season_up_name(st, UID, SID) == NAME,
           "① 只订合集时，取合集上记的名字")

        # ② 合集上没名字（旧数据），ups 里有
        st.add_season(GID, 9002, 7002, title="旧合集", uname="")
        st.set_up_info(9002, "老UP", 0)
        ck(resolve_season_up_name(st, 9002, 7002) == "老UP",
           "② 合集上没名字时，退回 ups 缓存")

        # ③ 都没有 → uid（至少不是空的）
        st.add_season(GID, 9003, 7003, title="无名合集", uname="")
        ck(resolve_season_up_name(st, 9003, 7003) == "9003",
           "③ 都没有时才退回 uid")

        # 按 season_id 精确取：同一个 UP 主两个合集，名字各自独立
        st.add_season(GID, 9004, 8001, title="A", uname="甲")
        st.add_season(GID, 9004, 8002, title="B", uname="乙")
        ck(resolve_season_up_name(st, 9004, 8002) == "乙",
           "给了 season_id 就认这一个合集，不串台")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ------------------------------------------------------------ 3 源码锁
def t_source_uses_helper():
    """推送那一路必须走兜底函数，不能再写回一行 get_up。"""
    src = open(os.path.join(_ROOT, "src", "bot.py"), encoding="utf-8").read()
    body = src.split("def _check_one_season", 1)[-1].split(
        "\n    async def ", 1)[0]
    # ⚠️ 必须带 `self.` 前缀：`resolve_season_up_name(` 里本来就含
    #    `_season_up_name(` 这个子串，只查后者的话，退化回旧写法依然
    #    为绿 —— 反向验证就成了摆设（这个项目里踩过不止一次）。
    ck("self._season_up_name(" in body,
       "_check_one_season 走 _season_up_name（含第四级实时兜底）")
    ck("get_up(uid).get(\"uname\") or str(uid)" not in body,
       "_check_one_season 里没有退回 uid 的一行写法")

    # ⚠️ 第四级不是取代三级，而是在三级退化成 uid 时才补一步：
    #    _season_up_name 内部仍必须先走 resolve_season_up_name。
    fn = src.split("async def _season_up_name", 1)[-1].split(
        "\n    async def ", 1)[0]
    ck("resolve_season_up_name(" in fn,
       "_season_up_name 内部先走三级兜底")
    ck("get_up(uid)" in fn, "_season_up_name 有第四级：实时问 B 站")

    dbsrc = open(os.path.join(_ROOT, "src", "db.py"), encoding="utf-8").read()
    ck(re.search(r"def season_uname\(", dbsrc) is not None,
       "db 提供 season_uname（合集上记的名字）")


def main():
    print("== test_season_push_name ==")
    t_source_uses_helper()
    t_fallback_chain()
    t_push_uses_name_not_uid()
    print(f"\n{PASS} passed, {FAIL} failed")
    if FAILED:
        for n in FAILED:
            print("   FAILED:", n)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
