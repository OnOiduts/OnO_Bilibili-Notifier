# -*- coding: utf-8 -*-
"""锁定「开播动态」的识别、展示与去重（v1.90.0）。

症状（用户原话）：
    还有像这种动态就是直播中的直播推送：
      📝 新动态
      【动态订阅】"波萝Buono"新动态更新了
      > 动态内容
      > [伊万勋章]

为什么成了这样：
  新版开播动态**不给** live_rcmd 卡片，直播间地址藏在 opus 的
  jump_url 里 —— 和当初投稿动态把 BV 号藏进 jump_url 是同一个套路。
  房间号抠不出来，于是：
    · major_type 退化成 normal → 卡片渲染成「📝 新动态」，
      正文只有一个表情勋章，谁也看不出这是在播；
    · 房间号去重失效 → 「开播提醒 + 开播动态」连着推两条。

修法：从各种跳转链接里抠直播间号（opus / live_rcmd / 正文），
抠到号又没认出更具体的主体类型就判成开播动态；渲染走直播样式，
去重按房间号（长号/短号两种写法都认）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, "/data/workspace/_stub2")

import bilibili as B  # noqa: E402
import bot  # noqa: E402
import notify  # noqa: E402

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


def _item(major, desc="", pub=1700000000):
    return {
        "id_str": "1242997444260659222",
        "basic": {"comment_type": 17, "comment_id_str": "1", "rid_str": "1"},
        "modules": {
            "module_author": {"pub_ts": pub},
            "module_dynamic": {"desc": {"text": desc}, "major": major},
        },
    }


# ---------------------------------------------------------------- 识别
def test_room_from_jump_url():
    """新版开播动态：直播间号藏在 opus 的 jump_url 里。"""
    print("[1] 房间号能从跳转链接里抠出来")
    item = _item({"type": "opus", "opus": {
        "jump_url": "https://live.bilibili.com/1234567?broadcast_type=0",
        "summary": {"text": "", "rich_text_nodes": [
            {"type": 1, "text": "", "emoji": {"text": "伊万勋章"}}]}}})
    d = B._parse_polymer(item)
    ck(d.ref_room_id == 1234567, "opus.jump_url → 抠出直播间号 1234567")
    ck(d.major_type == "live", "判成开播动态（不是普通图文）")


def test_room_variants():
    """各种链接写法都要认得。"""
    print("[2] 链接写法：h5 / blanc / 正文里贴的")
    for url, want in (
        ("https://live.bilibili.com/h5/888", 888),
        ("https://live.bilibili.com/blanc/777", 777),
        ("http://live.bilibili.com/6666?x=1", 6666),
    ):
        ck(B._room_from_src(url) == want, f"认得 {url}")
    ck(B._room_from_src("https://www.bilibili.com/video/BV1xx411c7mD") == 0,
       "视频链接不会误判成直播间")


def test_live_rcmd_card_still_works():
    """老版开播卡片（live_rcmd）不受影响。"""
    print("[3] 老版 live_rcmd 卡片照旧")
    item = _item({"type": "live_rcmd", "live_rcmd": {
        "content": {"live_play_info": {"room_id": 222}}}})
    d = B._parse_polymer(item)
    ck(d.major_type == "live", "卡片型 → live")
    ck(d.ref_room_id == 222, "房间号来自卡片")


def test_video_dyn_not_live():
    """投稿动态不能被误判成开播动态。"""
    print("[4] 投稿动态不误判")
    item = _item({"type": "opus", "opus": {
        "jump_url": "https://www.bilibili.com/video/BV1GJ411x7h7",
        "summary": {"text": "新视频"}}})
    d = B._parse_polymer(item)
    ck(d.major_type != "live", "投稿动态不是 live")
    ck(d.ref_bvid == "BV1GJ411x7h7", "BV 号照旧抠得到")
    ck(d.ref_room_id == 0, "没有直播间号")


def test_plain_dyn_untouched():
    """普通图文动态（没链接）保持 normal。"""
    print("[5] 普通动态保持原样")
    item = _item({"type": "opus", "opus": {"summary": {"text": "今天天气不错"}}})
    d = B._parse_polymer(item)
    ck(d.major_type == "normal", "普通图文 → normal")
    ck(d.ref_room_id == 0, "没有直播间号")


# ---------------------------------------------------------------- 展示
def _dyn(room=0, mtype="normal", title="", text=""):
    d = B.DynamicInfo(dyn_id="1", major_type=mtype, ref_room_id=room,
                      title=title, text=text)
    return d


def test_render_live_dyn():
    """开播动态要显示成直播，不能再是「📝 新动态」。"""
    print("[6] 开播动态按直播样式渲染")
    md, btns = notify.render_dynamic(_dyn(room=1234567, mtype="live",
                                          text="[伊万勋章]"), "波萝Buono", {})
    ck("🔴 直播中" in md, "标题是「🔴 直播中」")
    ck("📝 新动态" not in md, "不再显示「📝 新动态」")
    ck("开播中" in md, "文案说开播")
    ck(any("live.bilibili.com/1234567" in b[1] for b in btns),
       "带直达直播间的按钮")


def test_render_normal_dyn_untouched():
    """普通动态 / 置顶动态的展示不受影响。"""
    print("[7] 普通动态与置顶动态照旧")
    md, _ = notify.render_dynamic(_dyn(text="今天天气不错"), "A", {})
    ck("📝 新动态" in md, "普通动态仍是「📝 新动态」")
    d = _dyn(text="x")
    d.pinned = True
    md2, _ = notify.render_dynamic(d, "A", {})
    ck("📌 置顶动态" in md2, "置顶动态仍是「📌 置顶动态」")


def test_tpl_keys_registered():
    """新增文案必须登记进变量表与分组（面板靠它们生成）。"""
    print("[8] 文案登记齐全")
    for k in ("dynamic_live", "dynamic_live_no_title"):
        ck(k in notify.DEFAULT_TEMPLATES, f"默认文案有 {k}")
        ck(k in notify.TPL_VARS, f"变量表有 {k}")
    keys = [k for _, _, ks in notify.TPL_GROUPS for k in ks]
    ck("dynamic_live" in keys and "dynamic_live_no_title" in keys,
       "分组里有开播动态文案")
    ck(notify.HEAD_TITLE.get("dynamic_live") == "🔴 直播中",
       "卡片标题登记为「🔴 直播中」")


# ---------------------------------------------------------------- 去重
class FakeLive:
    def __init__(self, room, short=0, pub=0):
        self.room_id = room
        self.short_room_id = short
        self.live_time = pub or int(time.time())
        self.pubdate = 0


def _bot(cfg=None):
    b = bot.NotifyBot.__new__(bot.NotifyBot)
    b.cfg = cfg if cfg is not None else {}
    b._recent_push = {}
    b._pushed_content = {}
    return b


def test_dyn_silenced_by_room():
    """开播提醒先推 → 开播动态静默（房间号对得上）。"""
    print("[9] 同一件事只提醒一次（按房间号）")
    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "live", FakeLive(1234567))
    ck(b._should_silence_dynamic(1, _dyn(room=1234567, mtype="live")),
       "开播动态指向同一直播间 → 静默")


def test_dyn_silenced_by_short_room():
    """长号/短号两种写法都得对上。"""
    print("[10] 长号与短号")
    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "live", FakeLive(1234567, short=777))
    ck(b._should_silence_dynamic(1, _dyn(room=777, mtype="live")),
       "动态里给的是短号 → 也认得")


def test_not_mis_silenced():
    """不该静默的绝不能吞。"""
    print("[11] 反例")
    # 从没推开播提醒
    ck(not _bot()._should_silence_dynamic(1, _dyn(room=1, mtype="live")),
       "没推开播 → 不静默")
    # 另一个直播间
    b = _bot({"dedupe_window": 900})
    b._remember_pushed(1, "live", FakeLive(1234567))
    ck(not b._should_silence_dynamic(1, _dyn(room=999, mtype="live")),
       "别的直播间 → 照推")
    # 普通图文动态
    ck(not b._should_silence_dynamic(1, _dyn(mtype="normal")),
       "普通动态 → 照推")
    # 直播预约（UP 主动发的预告）不静默
    ck(not b._should_silence_dynamic(1, _dyn(room=1234567, mtype="reserve")),
       "直播预约 → 照推")


def main():
    test_room_from_jump_url()
    test_room_variants()
    test_live_rcmd_card_still_works()
    test_video_dyn_not_live()
    test_plain_dyn_untouched()
    test_render_live_dyn()
    test_render_normal_dyn_untouched()
    test_tpl_keys_registered()
    test_dyn_silenced_by_room()
    test_dyn_silenced_by_short_room()
    test_not_mis_silenced()
    print(f"\n通过 {PASS} / 失败 {FAIL}")
    if FAILED:
        print("失败项：")
        for n in FAILED:
            print("  -", n)
        sys.exit(1)


if __name__ == "__main__":
    main()
