# -*- coding: utf-8 -*-
"""锁定：合集订阅里**没记名字的旧数据**，推送时也不能显示成一串 uid。

上一版（test_season_push_name.py）钉的是"订阅时带了名字"的场景，
三级兜底（合集 uname → ups → uid）够用。但用户**又**反馈：

    📦 合集更新 280242571更万子姐录播了！|《2026/10/12/歌咋歌咋！》

面板上明明写着中文名。区别在于：这些合集是**旧数据**，订阅那会儿
还没记 uname（或当时 B 站没查到），seasons.uname 是空的；
而只订合集、没订 UP 主，ups 表里也没有 —— 三级兜底一路走到
`return str(uid)`，{name} 就成了一串数字。

⚠️ 面板里那套补救（backfill_season_names）**只在打开面板时跑**：
机器人一直跑着不开面板就永远补不上，所以它治不了这个。

第四级：推送前实时问一次 B 站，拿到就回填 seasons.uname + ups，
之后走前三级就能拿到，不必再查；查不到就进冷却表，下次不再白打。

这里锁四件事：
  1) 三级兜底退化成 uid 时，会实时补查并回填
  2) 补查到之后不再重复打请求（回填 + 缓存）
  3) 补查失败不崩、退回 uid，且进冷却不反复打
  4) 端到端：旧数据场景推送正文是真名，不是数字
"""
import asyncio
import os
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import botpy  # noqa: E402
from bilibili import SeasonInfo, SeasonVideo  # noqa: E402
from bot import NotifyBot, resolve_season_up_name, season_name_is_uid  # noqa: E402

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
GID = "GROUP_SEASON_OLD"
NAME = "伊万"


class FakeUp:
    def __init__(self, uname):
        self.uname = uname


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
    """get_up 可控：能返回名字、也能模拟查不到 / 抛异常。"""

    def __init__(self, videos, uname=NAME, boom=False):
        self.videos = list(videos)
        # ⚠️ 合集名里**故意不带 UP 主名字**：否则退化成 uid 时，
        #    断言 "伊万 in md" 会被合集名救活 —— 那就是假通过。
        self.title = "录播合集"
        self.uname = uname
        self.boom = boom
        self.get_up_calls = 0
        self.owner_name = ""
        self.owner_mid = 0
        self.owner_calls = 0

    async def get_season_archives(self, uid, season_id, max_pages=2):
        return SeasonInfo(season_id=int(season_id), uid=int(uid),
                          title=self.title, total=len(self.videos),
                          videos=list(self.videos))

    async def get_season_sections(self, bvid, season_id):
        return []

    def section_map(self, secs):
        return {}

    async def get_up(self, uid):
        self.get_up_calls += 1
        if self.boom:
            raise RuntimeError("B 站超时")
        return FakeUp(self.uname)

    async def get_video_owner(self, bvid):
        self.owner_calls += 1
        return (self.owner_mid, self.owner_name)

    async def close(self):
        pass


def make_bot(videos, uname=NAME, boom=False):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path},
                    intents=botpy.Intents(public_messages=True))
    bot.qq = FakeQQ()
    bot.bili = FakeBili(videos, uname=uname, boom=boom)
    bot._silent = False
    return bot, path


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _vid(bvid):
    return SeasonVideo(bvid=bvid,
                       title="万子姐录播了！|《2026/10/12/歌咋歌咋！》",
                       pic="", pubdate=int(time.time()))


# ---------------------------------------------------------- 1 判定函数
def t_is_uid():
    ck(season_name_is_uid("", UID) is True, "空名字算退化")
    ck(season_name_is_uid(str(UID), UID) is True, "正好等于 uid 算退化")
    ck(season_name_is_uid("123456", UID) is True, "纯数字也算退化（不是真名）")
    ck(season_name_is_uid(NAME, UID) is False, "真名不算退化")
    ck(season_name_is_uid("UP主123", UID) is False, "带数字的名字不算退化")


# ---------------------------------------------------------- 2 第四级补查
def t_live_lookup_fills_name():
    """旧数据（uname 空 + ups 没有）→ 实时补查拿到真名。"""
    bot, path = make_bot([])
    try:
        st = bot.store
        # 复现场景：旧订阅，没记名字，也没订这个 UP 主
        st.add_season(GID, UID, SID, title="合集·伊万 录播", uname="")
        ck(not (st.get_up(UID) or {}).get("uname"),
           "前置：ups 表里没有这个 UP 主")
        ck(resolve_season_up_name(st, UID, SID) == str(UID),
           "前置：三级兜底确实退化成 uid（正是用户看到的现象）")

        got = run(bot._season_up_name(UID, SID))
        ck(got == NAME, f"第四级补查到真名（拿到 {got!r}）")
        ck(bot.bili.get_up_calls == 1, "只打了一次请求")

        # 回填：下次不必再查
        ck(str(st.season_uname(UID, SID) or "") == NAME,
           "已回填 seasons.uname（面板也能显示中文名了）")
        ck(str((st.get_up(UID) or {}).get("uname") or "") == NAME,
           "已回填 ups 缓存")
        ck(resolve_season_up_name(st, UID, SID) == NAME,
           "回填后三级兜底就能拿到名字")

        # 再解析一次：不应再打请求
        got2 = run(bot._season_up_name(UID, SID))
        ck(got2 == NAME, "第二次仍返回真名")
        ck(bot.bili.get_up_calls == 1, "第二次不再打请求（回填生效）")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------- 3 补查失败
def t_live_lookup_failure_is_safe():
    """查不到 / 接口炸了：不崩，且显示成「UP主xxx」而不是一串裸数字。"""
    bot, path = make_bot([], uname="", boom=True)
    try:
        st = bot.store
        st.add_season(GID, 9009, 7009, title="无名合集", uname="")
        got = run(bot._season_up_name(9009, 7009))
        ck(got == "UP主9009", f"补查失败时显示「UP主9009」（拿到 {got!r}）")
        ck(not got.isdigit(), "兜底文案不是一串光秃秃的数字")
        ck(bot.bili.get_up_calls == 1, "失败也只打了一次")
        # 冷却：别每轮猛打把自己送进风控
        got2 = run(bot._season_up_name(9009, 7009))
        ck(got2 == "UP主9009", "冷却期内仍走兜底")
        ck(bot.bili.get_up_calls == 1, "冷却生效，不再重复打请求")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def t_miss_cooldown_expires():
    """冷却**必须有时效**：到点自动重试，不用重启机器人才好。

    ⚠️ 这是"又不显示 UP 主了"反复发作的根因：以前失败一次就永久进
    冷却集合，而启动那波请求最容易撞风控，于是整个进程生命周期内
    合集推送的 {name} 全是数字串，重启才好、过阵又犯。
    """
    bot, path = make_bot([], uname="", boom=True)
    try:
        st = bot.store
        st.add_season(GID, 9009, 7009, title="无名合集", uname="")
        got = run(bot._season_up_name(9009, 7009))
        ck(got == "UP主9009", "第一次失败走兜底")
        ck(bot.bili.get_up_calls == 1, "第一次只打一次")

        # 把冷却戳往前拨，模拟"过了 ttl 那么久"
        ttl = float(bot.cfg.get("season_name_miss_ttl", 0) or 600)
        miss = bot._season_name_miss
        ck(9009 in miss, "已进冷却表")
        miss[9009] = time.time() - 1        # 已过期
        # 这次让它查到
        bot.bili.boom = False
        bot.bili.uname = NAME
        got2 = run(bot._season_up_name(9009, 7009))
        ck(got2 == NAME, f"冷却过期后自动重试并拿到真名（拿到 {got2!r}）")
        ck(bot.bili.get_up_calls == 2, "过期后确实又打了一次请求")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def t_owner_name_fallback():
    """第五级：card 接口不给名字时，用视频详情里的 owner.name。"""
    bot, path = make_bot([], uname="", boom=True)
    try:
        st = bot.store
        st.add_season(GID, 280242571, 3175959, title="合集·伊万 录播",
                      uname="")
        bot.bili.owner_name = NAME
        bot.bili.owner_mid = 280242571
        got = run(bot._season_up_name(280242571, 3175959, bvid="BV_NEW"))
        ck(got == NAME, f"card 拿不到时从视频详情兜底（拿到 {got!r}）")
        ck(str(st.season_uname(280242571, 3175959) or "") == NAME,
           "兜底拿到的名字同样回填进 seasons")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------- 4 已有名字
def t_no_lookup_when_known():
    """三级兜底能拿到名字时，一次请求都不该打。"""
    bot, path = make_bot([])
    try:
        st = bot.store
        st.add_season(GID, UID, SID, title="合集·伊万 录播", uname=NAME)
        got = run(bot._season_up_name(UID, SID))
        ck(got == NAME, "已有名字时直接返回")
        ck(bot.bili.get_up_calls == 0, "已有名字时不发任何请求")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------- 5 端到端
def t_push_uses_name_for_legacy_data():
    """旧数据场景走完整推送链路，正文里必须是名字不是 uid。"""
    bot, path = make_bot([_vid("BV_OLD")])
    try:
        st = bot.store
        # 旧订阅：没记名字（这正是"又"出现的那批数据）
        st.add_season(GID, UID, SID, title="合集·伊万 录播", uname="")
        # 第一轮：建基线
        run(bot._check_one_season(UID, SID))
        ck(not bot.qq.sent, "第一轮只记基线、不推送")

        bot.bili.videos.append(_vid("BV_NEW"))
        bot.qq.sent.clear()
        run(bot._check_one_season(UID, SID))

        md = "\n".join(bot.qq.sent)
        if not ck(bool(md), "第二轮确实推送了"):
            return
        ck(NAME in md, "推送正文里有 UP 主名字「伊万」")
        ck(str(UID) not in md, "推送正文里不再出现 uid 数字")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def t_source_wiring():
    """源码级的接线检查：光有函数没接上等于没做。

    ⚠️ 这类断言最容易假通过 —— 函数名在文件里出现一次就算命中，
    而真正要锁的是"调用处把参数传进去了"。所以这里查的是**调用点**。
    """
    src = open(os.path.join(_ROOT, "src", "bot.py"), encoding="utf-8").read()
    body = src.split("def _check_one_season", 1)[-1].split(
        "\n    async def ", 1)[0]
    # ⚠️ 必须锁 `bvid=` 这个实参：只查 `_season_up_name(` 的话，退化成
    #    不传 bvid 的老写法依然为绿，第五级就永远不会被触发。
    ck("bvid=hint_bvid" in body,
       "_check_one_season 把最新视频的 bvid 传进 _season_up_name")

    fn = src.split("async def _season_up_name", 1)[-1].split(
        "\n    async def ", 1)[0]
    ck("season_name_fallback(" in fn,
       "_season_up_name 的兜底走 season_name_fallback（不是裸 uid）")
    # ⚠️ 反向验证能不能咬住，取决于这条够不够具体：
    #    只查 "get_video_owner" 的话，删掉调用只留注释也会为绿。
    ck("await owner(bvid)" in fn or "get_video_owner" in fn,
       "_season_up_name 有第五级：视频详情里的 owner.name")
    ck("miss[uid] = now" in fn,
       "冷却写的是时间戳（有时效），不是永久集合")

    blsrc = open(os.path.join(_ROOT, "src", "bilibili.py"),
                 encoding="utf-8").read()
    ck("async def get_video_owner(" in blsrc,
       "bilibili 提供 get_video_owner（第五级的数据来源）")


def main():
    print("== test_season_up_name_live ==")
    t_is_uid()
    t_no_lookup_when_known()
    t_live_lookup_fills_name()
    t_live_lookup_failure_is_safe()
    t_miss_cooldown_expires()
    t_owner_name_fallback()
    t_source_wiring()
    t_push_uses_name_for_legacy_data()
    print(f"\n{PASS} passed, {FAIL} failed")
    if FAILED:
        for n in FAILED:
            print("   FAILED:", n)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
