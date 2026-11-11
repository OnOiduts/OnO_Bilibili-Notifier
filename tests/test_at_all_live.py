# -*- coding: utf-8 -*-
"""开播提醒 @全体成员的真值测试。

不验「源码里有没有这段字」，而是构造真实 NotifyBot + 假 QQClient，
跑完 _push 后直接查**真正发出去的消息体**。
本文件锁的是**回退模式**（at_all_merge=False）：@ 单独一条纯文本；
默认的「合成一条」模式见 test_at_all_merge.py。
  · 回退模式开播卡片配一条独立的 @ 纯文本
  · 每群每天 1 次的配额是不是真的落盘、重启后还在、跨天会重置
  · 平台拒收时是不是真的去掉标签重发了一次（开播提醒不能丢）
"""
import asyncio
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(os.path.dirname(HERE), "src")
BASE = os.environ.get("BILI_TRUE_SRC") or REAL
STUBS = os.path.join(HERE, "_stubs")
for p in (STUBS, REAL, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, BASE)

# media 只在 _push 里局部 import；用桩替掉，避免测试依赖图片链
sys.modules.setdefault("media", types.SimpleNamespace(
    send_image=None, remember_recent=lambda *a, **k: None))

import botpy  # noqa: E402
import bot as botmod  # noqa: E402
from bot import NotifyBot  # noqa: E402
from bilibili import LiveInfo  # noqa: E402
import atall  # noqa: E402

OK = []
UID, GID = 320070259, "G_ATALL_1"


def ck(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    OK.append(bool(cond))
    return bool(cond)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeQQ:
    """记录真正发出去的消息体；可模拟平台拒收带 @全体 的那一条。"""

    def __init__(self, reject_tag=False):
        self.sent = []
        self.reject_tag = reject_tag

    async def send_markdown(self, gid, md, keyboard=None,
                            msg_id=None, event_id=None):
        if self.reject_tag and atall.AT_ALL_TAG in md:
            raise RuntimeError("HTTP 400: 机器人非群管理员或今日额度已用")
        self.sent.append(("markdown", gid, md))
        return {}

    async def send_text(self, gid, text, msg_id=None, event_id=None):
        if self.reject_tag and atall.AT_ALL_TAG in text:
            raise RuntimeError("HTTP 400: 机器人非群管理员或今日额度已用")
        self.sent.append(("text", gid, text))
        return {}

    def bodies(self, kind=None, gid=None):
        return [b for k, g, b in self.sent
                if (kind is None or k == kind) and (gid is None or g == gid)]


def make_bot(tmp, *, style="markdown", on=True, qq=None, quota=None, now=None,
             merge=False):
    fd, state = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    cfg = {"data_file": state, "message_style": style,
           "at_all_on_live": on, "send_image": False,
           "image_send_mode": "off",
           # 本文件锁的是「单独一条纯文本」回退模式（用户截图里已真机
           # 验证生效）；默认的合成模式见 test_at_all_merge.py。
           "at_all_merge": merge}
    bot = NotifyBot(cfg, intents=botpy.Intents(public_messages=True))
    bot.cfg = dict(cfg)
    bot._silent = False
    bot.send_image = False
    bot.qq = qq or FakeQQ()
    bot._tpls_for = lambda gid, uid=None, season_key=None: {}
    bot._remember_pushed = lambda *a, **k: None
    bot._noperm_active = lambda gid: False
    bot._mark_no_perm = lambda gid: None
    bot._note_send_error = lambda gid, e: False
    bot._atall_q = atall.AtAllQuota(
        path=quota or os.path.join(tmp, "quota.json"), now=now)
    bot.store.set_up_info(UID, "测试UP", 1, "", 0)
    bot.store.set_sub(GID, UID, "live", True)
    return bot, state


def live():
    return LiveInfo(room_id=12345, live_status=1, title="测试开播")


def push(bot, kind="live"):
    run(bot._push(kind, UID, "测试UP", live()))


def main():
    tmp = tempfile.mkdtemp(prefix="atall_")
    NOW = [1000000.0]
    try:
        # ① 官方标签常量必须是对的（旧标签 @everyone 群聊根本不支持）
        ck("官方标签常量是 <qqbot-at-everyone />",
           atall.AT_ALL_TAG == "<qqbot-at-everyone />")

        # ② 回退模式：@全体 走**单独一条纯文本**（标签落在 content 字段），
        #    markdown 卡片本身不带标签。这是需要时能一键退回的已验证路径。
        qq = FakeQQ()
        bot, st = make_bot(tmp, qq=qq, quota=os.path.join(tmp, "q1.json"),
                           now=lambda: NOW[0])
        push(bot)
        md = qq.bodies("markdown")
        tx = qq.bodies("text")
        ck("@全体 单独一条纯文本且标签在最前面",
           len(tx) == 1 and tx[0].startswith(atall.AT_ALL_TAG + "\n"))
        ck("markdown 卡片不带标签", bool(md) and atall.AT_ALL_TAG not in md[0])
        ck("@ 文本先发、卡片后发",
           bool(qq.sent) and qq.sent[0][0] == "text"
           and qq.sent[-1][0] == "markdown")
        os.remove(st)

        # ③ 下播 / 别的类型一律不 @
        qq2 = FakeQQ()
        bot2, st2 = make_bot(tmp, qq=qq2, quota=os.path.join(tmp, "q2.json"),
                             now=lambda: NOW[0])
        push(bot2, "offline")
        ck("下播提醒不发 @ 文本", not qq2.bodies("text"))
        ck("下播提醒卡片照常发", bool(qq2.bodies("markdown")))
        os.remove(st2)

        # ④ 每群每天 1 次：第二次开播不再带标签
        qq3 = FakeQQ()
        q3 = os.path.join(tmp, "q3.json")
        bot3, st3 = make_bot(tmp, qq=qq3, quota=q3, now=lambda: NOW[0])
        push(bot3)
        push(bot3)
        b3 = qq3.bodies("markdown")
        t3 = qq3.bodies("text")
        ck("同一群当天第二次开播不再 @",
           len(b3) == 2 and len(t3) == 1
           and atall.AT_ALL_TAG in t3[0]
           and all(atall.AT_ALL_TAG not in x for x in b3))
        # 配额真的落盘，且换一个实例（等价于重启）仍然生效
        fresh = atall.AtAllQuota(path=q3, now=lambda: NOW[0])
        ck("配额落盘且新建实例仍生效", fresh.available(GID) is False)
        os.remove(st3)

        # ⑤ 跨自然日后重新可 @
        NOW[0] += 90000.0
        qq4 = FakeQQ()
        bot4, st4 = make_bot(tmp, qq=qq4, quota=q3, now=lambda: NOW[0])
        push(bot4)
        t4 = qq4.bodies("text")
        ck("跨自然日后重新可 @", bool(t4) and atall.AT_ALL_TAG in t4[0])
        os.remove(st4)

        # ⑥ 开关关掉
        qq5 = FakeQQ()
        bot5, st5 = make_bot(tmp, on=False, qq=qq5,
                             quota=os.path.join(tmp, "q5.json"),
                             now=lambda: NOW[0])
        push(bot5)
        ck("开关关闭时不发 @ 文本", not qq5.bodies("text"))
        ck("开关关闭时卡片照常发", bool(qq5.bodies("markdown")))
        os.remove(st5)

        # ⑦ 平台拒收 @ 文本：卡片照常发出，开播提醒本身不丢
        qq6 = FakeQQ(reject_tag=True)
        q6 = os.path.join(tmp, "q6.json")
        bot6, st6 = make_bot(tmp, qq=qq6, quota=q6, now=lambda: NOW[0])
        push(bot6)
        b6 = qq6.bodies("markdown")
        ck("@ 被平台拒绝时卡片照常发出（不丢开播提醒）",
           len(b6) == 1 and atall.AT_ALL_TAG not in b6[0])
        ck("@ 被拒后不再重发带标签文本", not qq6.bodies("text"))
        data6 = atall.AtAllQuota(path=q6, now=lambda: NOW[0])._load()
        ck("失败不消耗配额且进入冷却",
           GID not in (data6.get("used") or {})
           and GID in (data6.get("failed") or {})
           and atall.AtAllQuota(path=q6, now=lambda: NOW[0]).available(GID) is False)
        os.remove(st6)

        # ⑧ 纯文本样式（没申请 Markdown 权限）同样带标签
        qq7 = FakeQQ()
        bot7, st7 = make_bot(tmp, style="text", qq=qq7,
                             quota=os.path.join(tmp, "q7.json"),
                             now=lambda: NOW[0])
        push(bot7)
        t7 = qq7.bodies("text")
        ck("纯文本样式下 content 带 @全体",
           bool(t7) and t7[0].startswith(atall.AT_ALL_TAG + "\n"))
        os.remove(st7)

        # ⑨ 纯文本样式被拒也能降级重发
        qq8 = FakeQQ(reject_tag=True)
        bot8, st8 = make_bot(tmp, style="text", qq=qq8,
                             quota=os.path.join(tmp, "q8.json"),
                             now=lambda: NOW[0])
        push(bot8)
        t8 = qq8.bodies("text")
        ck("纯文本样式失败也会降级重发",
           len(t8) == 1 and atall.AT_ALL_TAG not in t8[0])
        os.remove(st8)

        # ⑩ 配置链路的结构检查（面板能改、后端收、state 回、开关登记）
        def src(name):
            # 读 BASE 而非写死真源：反向验证把副本改坏时必须能照出来
            with open(os.path.join(BASE, name), encoding="utf-8") as f:
                return f.read()

        w = src("webui.py")
        ck("后端白名单收 at_all_on_live",
           'cfg["at_all_on_live"] = bool(d.get("at_all_on_live"))' in w
           and '"at_all_on_live": bool(cfg.get("at_all_on_live", True))' in w)
        h = src("templates/index.html")
        a = src("static/app.js")
        ck("面板开关与保存链路齐全",
           'id="cAtAllLive"' in h
           and "at_all_on_live: !$('cAtAllLive')" in a
           and "'cAtAllLive'" in a)
        bsrc = src("bot.py")
        ck("bot 用官方群聊标签而非旧 @everyone",
           "atall.AT_ALL_TAG" in bsrc and "@everyone" not in bsrc)
        ck("bot 的 @全体 只限开播提醒",
           'if kind != "live":' in bsrc and "_atall_want" in bsrc)
        ck("bot 同时保留合成模式与回退双消息路径",
           "at_all_merge" in bsrc
           and "md_body" in bsrc
           and "if want_at and not merge:" in bsrc
           and "await self.qq.send_text(gid, at_text)" in bsrc)
        ck("面板新增「@全体 合并进卡片」开关且默认开",
           'id="cAtAllMerge" checked' in h
           and "at_all_merge: !$('cAtAllMerge')" in a
           and "'cAtAllMerge'" in a)
        ck("未 @ 的原因可判定（explain + 按天去重日志）",
           "def explain(" in src("atall.py")
           and "_atall_note_skip" in bsrc)
        ck("纯文本路径带标签失败时无条件降级重发",
           "if at_all:" in bsrc
           and "if at_all and not is_no_perm_error(e):" not in bsrc)
        ck("面板 @全体 说明写明官方每日 1 次限制",
           'id="cAtAllLive"' in h and "每天只能 @ 1 次" in h)
        n = src("notify.py")
        ck("过时结论已从源码注释里清掉",
           "群聊不支持 —— 本项目的" not in n
           and "2026-10-26" in n)
    finally:
        try:
            for f in os.listdir(tmp):
                os.remove(os.path.join(tmp, f))
            os.rmdir(tmp)
        except OSError:
            pass

    bad = OK.count(False)
    print()
    print(f"{len(OK) - bad}/{len(OK)}")
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
