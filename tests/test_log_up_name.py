# -*- coding: utf-8 -*-
"""日志里提到某个 UP 主时要用**名字**，不能只给一串 UID。

用户反馈的现象（v2.3.5）：

    【信息】【UID 320070259】基线对齐暂缓（live）：该 UP 主没有直播间；
    600s 后再试（不影响正常检测，首次检测到时会自动静默对齐）

看日志的人还得自己拿 320070259 这串数字去搜是谁 —— 而名字本来就存在
本地（订阅时存下来的 uname），别处日志也都是打名字的：

    [星瞳_Official] 群 6FBBD9B5… 已推送【置顶评论】

修复：新增 NotifyBot._up_tag(uid)，有名字返回名字，没有才退回 UID；
`_init_baseline` 里的 10 处日志全改用它。

⚠️ 只从本地缓存取，**不为此发请求** —— 为了日志好看再打一次接口纯属
白发（还会加重风控）。订阅后确实可能还没拉到名字，那时退回 UID。
"""
import asyncio
import os
import sys
import tempfile

BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_stubs"))
sys.path.insert(0, BASE)

import botpy  # noqa: E402
import bot as botmod  # noqa: E402
from bot import NotifyBot  # noqa: E402

OK = []


def ck(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    OK.append(bool(cond))
    return bool(cond)


def _src():
    with open(os.path.join(BASE, "bot.py"), encoding="utf-8") as f:
        return f.read()


def _strip_comments(src):
    """去掉整行注释 —— 否则说明文字里含同样的字样，退化后照样为绿。"""
    return "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))


def _body(fn=None):
    """取 _init_baseline 的函数体（剥注释后）。"""
    src = _strip_comments(_src())
    i = src.find("    async def _init_baseline(")
    assert i > 0, "找不到 _init_baseline"
    j = src.find("    # ============ 轮询 ============", i)
    return src[i:j] if j > 0 else src[i:]


# ──────────────────────────────────────────────────────────────
# 假外部依赖
# ──────────────────────────────────────────────────────────────

class Rec:
    def __init__(self):
        self.msgs = []

    def _add(self, m):
        self.msgs.append(str(m))

    info = _add
    warning = _add
    error = _add

    def debug(self, *a, **k):
        pass

    def critical(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


class FakeQQ:
    def __init__(self):
        self.sent = []

    async def send_text(self, gid, content, **kw):
        self.sent.append((gid, content))

    async def send_markdown(self, gid, md, keyboard=None, **kw):
        self.sent.append((gid, md))

    async def close(self):
        pass


class FakeBili:
    """⚠️ get_up 会计数 —— 用来证明"没有为了取名字白发请求"。"""

    def __init__(self):
        self.n = {"up": 0}

    async def get_up(self, uid):
        self.n["up"] += 1
        from bilibili import Up
        return Up(uid=uid, uname="x", room_id=0)

    async def get_live(self, room_id):
        from bilibili import LiveInfo
        return LiveInfo(room_id=room_id, live_status=0)

    async def get_latest_video(self, uid):
        return None

    async def get_latest_dynamic(self, uid):
        return None

    async def get_room_id_by_mid(self, uid):
        return (0, 0)

    async def get_pinned_dynamic(self, uid):
        return None

    async def get_pinned_comment(self, dyn_id, uid=0, rid="", **kw):
        return None

    async def close(self):
        pass


def make_bot():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path},
                    intents=botpy.Intents(public_messages=True))
    bot.qq = FakeQQ()
    bot.bili = FakeBili()
    bot._silent = False
    rec = Rec()
    botmod._log = rec
    return bot, rec, path


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


UID = 320070259


# ──────────────────────────────────────────────────────────────
# 1. 源码层面
# ──────────────────────────────────────────────────────────────

def test_source_uses_tag():
    body = _body()
    ck("_init_baseline 里不再硬打 [UID {uid}]", "[UID {uid}]" not in body)
    ck("_init_baseline 里改用 [{up_tag}]", "[{up_tag}]" in body)
    ck("会用 _up_tag 取名", "self._up_tag(uid)" in body)
    src = _strip_comments(_src())
    ck("存在 _up_tag 方法", "def _up_tag(self, uid: int)" in src)


def test_up_tag_uses_only_store():
    """取名字不能发请求 —— 为了日志好看打接口纯属白发。"""
    src = _src()
    i = src.find("    def _up_tag(self, uid: int)")
    assert i > 0
    j = src.find("    async def _init_baseline(", i)
    fn = src[i:j]
    ck("_up_tag 里没有 await（不发请求）", "await" not in fn)
    ck("_up_tag 不碰 bili 客户端", "self.bili" not in fn)
    ck("_up_tag 从 store 取名字", 'get_up(uid).get("uname")' in fn)


# ──────────────────────────────────────────────────────────────
# 2. 端到端
# ──────────────────────────────────────────────────────────────

def t_log_shows_name():
    """有名字时，日志打名字，且不该出现那串 UID。"""
    bot, rec, path = make_bot()
    gid = "G1"
    bot.store.set_up_info(UID, "波萝Buono", 0, "", 0)
    bot.store.set_sub(gid, UID, "video", True)
    run(bot._init_baseline(UID))
    msgs = [m for m in rec.msgs if "暂缓" in m or "已静默对齐" in m]
    ck("打出了日志", bool(msgs))
    ck("日志里是名字而不是 UID",
       bool(msgs) and "[波萝Buono]" in msgs[0])
    ck("日志里不再出现那串 UID",
       bool(msgs) and str(UID) not in msgs[0])


def t_log_falls_back_to_uid():
    """还没拉到名字时退回 UID —— 至少还能照着查。"""
    bot, rec, path = make_bot()
    gid = "G1"
    bot.store.set_up_info(UID, "", 0, "", 0)   # 名字是空的
    bot.store.set_sub(gid, UID, "video", True)
    run(bot._init_baseline(UID))
    msgs = [m for m in rec.msgs if "暂缓" in m]
    ck("无名字时会打日志", bool(msgs))
    ck("无名字时退回 UID（还能查）",
       bool(msgs) and f"UID {UID}" in msgs[0])


def t_no_extra_request_for_name():
    """跑完一轮，一个取名字的请求都没发。"""
    bot, rec, path = make_bot()
    gid = "G1"
    bot.store.set_up_info(UID, "波萝Buono", 0, "", 0)
    bot.store.set_sub(gid, UID, "video", True)
    run(bot._init_baseline(UID))
    ck("没有为了取名字打接口", bot.bili.n["up"] == 0)


def t_up_tag_direct():
    """直接验证 _up_tag 的两种返回值。"""
    bot, rec, path = make_bot()
    bot.store.set_up_info(UID, "伊万酱哒油", 0, "", 0)
    ck("有名字 → 返回名字", bot._up_tag(UID) == "伊万酱哒油")
    other = 999
    ck("没记录 → 退回 UID", bot._up_tag(other) == f"UID {other}")


def t_does_not_affect_push_log():
    """别处的日志本来就是名字，改完仍不受影响（回归）。"""
    bot, rec, path = make_bot()
    gid = "G1"
    bot.store.set_up_info(UID, "波萝Buono", 0, "", 0)
    bot.store.set_sub(gid, UID, "video", True)
    async def _gv(uid):
        return _mk_video()

    bot.bili.get_latest_video = _gv
    bot._silent = False
    run(bot._check_video(UID, "波萝Buono"))
    pushed = [m for m in rec.msgs if "已推送" in m]
    ck("推送日志仍用名字", (not pushed) or all("[波萝Buono]" in m for m in pushed))


def _mk_video(bvid="BV1", pubdate=1000):
    from bilibili import VideoInfo
    return VideoInfo(bvid=bvid, title="t", pubdate=pubdate, pic="", desc="")


if __name__ == "__main__":
    test_source_uses_tag()
    test_up_tag_uses_only_store()
    t_log_shows_name()
    t_log_falls_back_to_uid()
    t_no_extra_request_for_name()
    t_up_tag_direct()
    t_does_not_affect_push_log()
    print(f"\n{sum(OK)}/{len(OK)}")
    sys.exit(0 if all(OK) else 1)
