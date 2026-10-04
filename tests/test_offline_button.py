"""下播卡片：不给任何按钮，改为显示本场直播时长。

用户反馈：「都下播了怎么还要有进入直播间（/进入空间）的按钮，
这不符合逻辑啊」。

沿革（别改回去）：
  · v1.31 之前：一律给「进入直播间」+ live.bilibili.com/{房间号}
    —— 下播后直播间关了，点进去是黑屏/轮播，纯属摆设。
  · v1.35.1：改成「进入个人空间」+ space.bilibili.com/{uid}
    —— 还是个按钮，但和"下播"这件事没关系，用户仍觉得多余。
  · v1.38.0：**彻底不给按钮**，改为显示本场直播时长（这才是下播时
    真正想知道的信息）。

时长由 bot 在检测到下播时用"开播时刻"算好塞进 LiveInfo.duration，
渲染层只负责显示；算不出来（重启前就在播且接口没给 live_time）就是 0，
此时不显示这一行，绝不瞎编。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import notify as N          # noqa: E402
from bilibili import LiveInfo  # noqa: E402
from qqapi import build_keyboard  # noqa: E402

TPL = {}
ROOM = 214
UID = 401315430

results = []


def ck(name, cond):
    results.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name)


def _offline(uid=0, duration=0):
    info = LiveInfo(room_id=ROOM, live_status=0, title="下播标题",
                    duration=duration)
    return N.render("offline", info, "小狐狸", TPL, uid=uid)


print("=== 下播卡片：不再给按钮 ===")

md, btns = _offline()
ck("没有 uid 时不给按钮", not btns)
ck("没有 uid 时卡片正文照常渲染", "已下播" in md)
ck("没有 uid 时 build_keyboard 返回 None", build_keyboard(btns) is None)

md, btns = _offline(UID)
ck("有 uid 时也**不给**按钮（不再跳个人空间）", not btns)
ck("有 uid 时 build_keyboard 返回 None", build_keyboard(btns) is None)
ck("卡片里不再出现直播间链接", "live.bilibili.com" not in md)

md, btns = _offline(UID)
ck("卡片里也不再出现空间主页链接", "space.bilibili.com" not in md)

print("=== 本场直播时长 ===")

md, _ = _offline(UID, duration=3725)
ck("时长 3725 秒 → 「1 小时 2 分」", "1 小时 2 分" in md)
md, _ = _offline(UID, duration=7200)
ck("整 2 小时 → 「2 小时 0 分」", "2 小时 0 分" in md)
md, _ = _offline(UID, duration=65)
ck("65 秒 → 「1 分」（不再带秒）", "1 分" in md and "5 秒" not in md)
md, _ = _offline(UID, duration=45)
ck("45 秒 → 「45 秒」", "45 秒" in md)
md, _ = _offline(UID, duration=0)
ck("时长为 0 时不显示这一行", "直播时长" not in md)
md, _ = _offline(UID, duration=-5)
ck("负数时长当无效（不显示）", "直播时长" not in md)
md, _ = _offline(UID, duration="abc")
ck("非数字时长不抛异常（不显示）", "直播时长" not in md)
md, _ = _offline(UID, duration=3725)
ck("时长行带「直播时长」标签", "直播时长" in md)

print("=== 格式函数边界 ===")
ck("0 → 空", N._fmt_duration(0) == "")
ck("59 → 「59 秒」", N._fmt_duration(59) == "59 秒")
ck("60 → 「1 分」", N._fmt_duration(60) == "1 分")
ck("3600 → 「1 小时 0 分」", N._fmt_duration(3600) == "1 小时 0 分")
ck("None → 空", N._fmt_duration(None) == "")

print("=== 开播卡片不受影响 ===")

live_info = LiveInfo(room_id=ROOM, live_status=1, title="标题",
                     area="虚拟主播", online=999)
md_live, btns_live = N.render("live", live_info, "小狐狸", TPL)
ck("开播仍给「进入直播间」", btns_live and btns_live[0][0] == "进入直播间")
ck("开播按钮指向直播间",
   btns_live and btns_live[0][1] == f"https://live.bilibili.com/{ROOM}")

bad = [n for n, ok in results if not ok]
print()
print(f"通过 {len(results) - len(bad)}/{len(results)}")
if bad:
    print("失败：")
    for n in bad:
        print("  - " + n)
    sys.exit(1)
