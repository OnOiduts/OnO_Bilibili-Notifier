# -*- coding: utf-8 -*-
"""@全体 合成进开播卡片的真值测试。

不验「源码里有没有这段字」，而是构造真实 NotifyBot + 假 QQClient，
跑完 _push 后直接查**真正发出去的消息体**：
  · 默认只发一条 markdown，且 <qqbot-at-everyone /> 在 markdown.content 最前面
  · 卡片正文没被标签吃掉
  · 每群每天 1 次真的落盘、重启仍在、跨天重置
  · 带标签那条被平台拒收时，去掉标签重发**同一张**卡片（不丢提醒）
  · 关掉「合并进卡片」能回到已真机验证生效的「单独一条纯文本」模式
  · 测试推送 /api/test 也合成一条（见 test_at_all_testpush.py）
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

sys.modules.setdefault("media", types.SimpleNamespace(
    send_image=None, remember_recent=lambda *a, **k: None))

import botpy  # noqa: E402
from bot import NotifyBot  # noqa: E402
from bilibili import LiveInfo  # noqa: E402
import atall  # noqa: E402

OK = []
UID, GID = 320070259, "G_MERGE_1"


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


def make_bot(tmp, *, style="markdown", on=True, merge=None, qq=None,
             quota=None, now=None):
    fd, state = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    cfg = {"data_file": state, "message_style": style,
           "at_all_on_live": on, "send_image": False,
           "image_send_mode": "off"}
    # merge=None 表示「用户没设过」→ 走默认值，专门验证默认合成
    if merge is not None:
        cfg["at_all_merge"] = merge
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


def src(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()


def main():
    tmp = tempfile.mkdtemp(prefix="atall_merge_")
    NOW = [1000000.0]
    try:
        # ① 没设过 at_all_merge → 默认合成一条
        qq = FakeQQ()
        bot, st = make_bot(tmp, qq=qq, quota=os.path.join(tmp, "q1.json"),
                           now=lambda: NOW[0])
        push(bot)
        md = qq.bodies("markdown")
        ck("默认只发一条消息（合成，不是两条）", len(qq.sent) == 1)
        ck("这条是 markdown 卡片", len(md) == 1 and not qq.bodies("text"))
        ck("标签在 markdown.content 最前面",
           bool(md) and md[0].startswith(atall.AT_ALL_TAG + "\n"))
        ck("卡片正文没被标签吃掉",
           bool(md) and len(md[0]) > len(atall.AT_ALL_TAG) + 20
           and "测试UP" in md[0])
        os.remove(st)

        # ② 每群每天 1 次：第二次开播不再带标签，配额真的落盘
        qq3 = FakeQQ()
        q3 = os.path.join(tmp, "q3.json")
        bot3, st3 = make_bot(tmp, qq=qq3, quota=q3, now=lambda: NOW[0])
        push(bot3)
        push(bot3)
        b3 = qq3.bodies("markdown")
        ck("当天第二次开播仍只发一条且不带标签",
           len(qq3.sent) == 2 and len(b3) == 2
           and atall.AT_ALL_TAG in b3[0] and atall.AT_ALL_TAG not in b3[1])
        ck("合成成功才占额度且落盘（新建实例仍生效）",
           atall.AtAllQuota(path=q3, now=lambda: NOW[0]).available(GID) is False)
        os.remove(st3)

        # ③ 跨自然日后重新可 @
        NOW[0] += 90000.0
        qq4 = FakeQQ()
        bot4, st4 = make_bot(tmp, qq=qq4, quota=q3, now=lambda: NOW[0])
        push(bot4)
        b4 = qq4.bodies("markdown")
        ck("跨自然日后重新可 @", len(b4) == 1 and atall.AT_ALL_TAG in b4[0])
        os.remove(st4)

        # ④ 回退模式：关掉开关 → 单独一条纯文本 + 卡片（真机已验证的路径）
        qq5 = FakeQQ()
        bot5, st5 = make_bot(tmp, merge=False, qq=qq5,
                             quota=os.path.join(tmp, "q5.json"),
                             now=lambda: NOW[0])
        push(bot5)
        t5, b5 = qq5.bodies("text"), qq5.bodies("markdown")
        ck("回退模式发一条 @ 纯文本 + 一条卡片",
           len(t5) == 1 and len(b5) == 1)
        ck("回退模式 @ 文本标签在最前面",
           t5[0].startswith(atall.AT_ALL_TAG + "\n"))
        ck("回退模式卡片不带标签", atall.AT_ALL_TAG not in b5[0])
        ck("回退模式 @ 先发、卡片后发",
           qq5.sent[0][0] == "text" and qq5.sent[-1][0] == "markdown")
        os.remove(st5)

        # ⑤ 带标签那条被平台拒收 → 去掉标签重发同一张卡片
        qq6 = FakeQQ(reject_tag=True)
        q6 = os.path.join(tmp, "q6.json")
        bot6, st6 = make_bot(tmp, qq=qq6, quota=q6, now=lambda: NOW[0])
        push(bot6)
        b6 = qq6.bodies("markdown")
        ck("被拒后重发一张不带标签的卡片（提醒不丢）",
           len(b6) == 1 and atall.AT_ALL_TAG not in b6[0]
           and "测试UP" in b6[0])
        ck("被拒后不再发 @ 纯文本", not qq6.bodies("text"))
        data6 = atall.AtAllQuota(path=q6, now=lambda: NOW[0])._load()
        ck("被拒不消耗配额且进入冷却",
           GID not in (data6.get("used") or {})
           and GID in (data6.get("failed") or {}))
        os.remove(st6)

        # ⑥ 总开关关掉 / 非开播类型 / 纯文本样式
        qq7 = FakeQQ()
        bot7, st7 = make_bot(tmp, on=False, qq=qq7,
                             quota=os.path.join(tmp, "q7.json"),
                             now=lambda: NOW[0])
        push(bot7)
        ck("总开关关掉时只发一张不带标签的卡片",
           len(qq7.sent) == 1 and atall.AT_ALL_TAG not in qq7.bodies("markdown")[0])
        os.remove(st7)

        qq8 = FakeQQ()
        bot8, st8 = make_bot(tmp, qq=qq8, quota=os.path.join(tmp, "q8.json"),
                             now=lambda: NOW[0])
        push(bot8, "offline")
        ck("下播提醒不 @",
           bool(qq8.bodies("markdown"))
           and atall.AT_ALL_TAG not in qq8.bodies("markdown")[0])
        os.remove(st8)

        qq9 = FakeQQ()
        bot9, st9 = make_bot(tmp, style="text", qq=qq9,
                             quota=os.path.join(tmp, "q9.json"),
                             now=lambda: NOW[0])
        push(bot9)
        t9 = qq9.bodies("text")
        ck("纯文本样式本来就是一条（content 带标签）",
           len(qq9.sent) == 1 and bool(t9)
           and t9[0].startswith(atall.AT_ALL_TAG + "\n"))
        os.remove(st9)

        # ⑦ 配置链路：面板能改、后端收、state 回、热更新登记
        b = src("bot.py")
        w = src("webui.py")
        a = src("static/app.js")
        h = src("templates/index.html")
        ck("bot 读取 at_all_merge 且默认开",
           'self.cfg.get("at_all_merge", True)' in b)
        ck("bot 合成模式把标签拼进 markdown.content",
           "md_body = (atall.AT_ALL_TAG" in b
           and "send_markdown(gid, md_body" in b)
        ck("bot 保留被拒后去掉标签重发",
           "gid, md_g_no_img, keyboard=keyboard" in b
           and "已去掉 @全体" in b)
        ck("bot 保留回退的双消息路径",
           "if want_at and not merge:" in b
           and "await self.qq.send_text(gid, at_text)" in b)
        ck("at_all_merge 登记进热更新白名单",
           '"at_all_on_live", "at_all_merge"' in b)
        ck("后端 state 回显 at_all_merge",
           '"at_all_merge": bool(cfg.get("at_all_merge", True))' in w)
        ck("后端保存白名单收 at_all_merge",
           'cfg["at_all_merge"] = bool(d.get("at_all_merge"))' in w)
        ck("测试推送也合成一条",
           'merge_at = bool(at_all and cfg.get("at_all_merge", True))' in w
           and "md_body = (atall.AT_ALL_TAG" in w)
        ck("面板开关存在且默认开",
           'id="cAtAllMerge" checked' in h)
        ck("前端填充 / 提交 / 登记 / 即时保存齐全",
           "c.at_all_merge !== false" in a
           and "at_all_merge: !$('cAtAllMerge')" in a
           and "'cAtAllMerge'" in a
           and "'cAtAllMerge'," in a)
        ck("qqapi 不改写标签（原样透传）",
           "本方法只负责原样透传" in src("qqapi.py"))
    finally:
        for f in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, f))
            except OSError:
                pass
        try:
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
