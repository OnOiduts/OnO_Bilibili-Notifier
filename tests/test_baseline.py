"""验证：基线按群隔离 + 删稿/删动态不重推旧内容。

两个都是"为什么新订阅的群收到了几个月前的旧投稿"这类问题的根因：

1. 基线按群隔离：A 群早就对齐、B 群刚订阅，B 不该继承 A 的进度差异，
   更不该把订阅之前就存在的内容当成新内容推一遍。
2. 发布时间比对：UP 主删掉最新一条后，接口返回的"最新"会退回更早的一条。
   只比 ID 就会判定成"有变化"，把旧内容再推一遍。
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import botpy  # noqa: E402
from bilibili import DynamicInfo, LiveInfo, VideoInfo  # noqa: E402
from bot import NotifyBot  # noqa: E402

OK = []


def ck(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    OK.append(bool(cond))
    return bool(cond)


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
    def __init__(self):
        self.video = {}
        self.dyn = {}
        self.live = {}

    async def get_up(self, uid):
        from bilibili import Up
        return Up(uid=uid, uname=f"UP{uid}", room_id=uid * 10)

    async def get_live(self, room_id):
        return self.live.get(room_id, LiveInfo(room_id=room_id, live_status=0))

    async def get_latest_video(self, uid):
        return self.video.get(uid)

    async def get_pinned_dynamic(self, uid):
        return None

    async def get_pinned_comment(self, dyn_id, uid=0, rid=""):
        return None

    async def get_latest_dynamic(self, uid):
        return self.dyn.get(uid)

    async def close(self):
        pass


def make_bot():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path}, intents=botpy.Intents(public_messages=True))
    bot.qq = FakeQQ()
    bot.bili = FakeBili()
    bot._silent = False
    return bot, path


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------- 1
def t_new_group_no_backfill():
    """新订阅的群不能收到它订阅之前就存在的投稿/动态。

    复现用户场景：UP 在别的群只开了置顶动态，在另一个群新开视频+动态，
    结果立刻收到几小时前发布的旧视频。
    """
    bot, path = make_bot()
    uid, gid_a, gid_b = 1001, "GROUP_A", "GROUP_B"
    st = bot.store

    # A 群先订阅并对齐
    st.set_sub(gid_a, uid, "video", True)
    bot.bili.video[uid] = VideoInfo(bvid="BV_OLD", title="旧投稿", pubdate=1000)
    run(bot._check_video(uid, "UP1001"))
    base_a = st.baseline_of(gid_a, uid, "video")
    if not ck("A 群首次检测只对齐不推送", not bot.qq.sent
              and base_a.get("bvid") == "BV_OLD"):
        return

    # B 群后来才订阅同一个 UP 的视频
    st.set_sub(gid_b, uid, "video", True)
    bot.qq.sent.clear()
    run(bot._check_video(uid, "UP1001"))
    hit_b = [g for g, _ in bot.qq.sent if g == gid_b]
    ck("B 群新订阅：静默对齐，不补推旧投稿", not hit_b)
    ck("B 群基线已建立",
       (st.baseline_of(gid_b, uid, "video") or {}).get("bvid") == "BV_OLD")

    # 真发了新的：两个群都要收到
    bot.bili.video[uid] = VideoInfo(bvid="BV_NEW", title="新投稿", pubdate=2000)
    bot.qq.sent.clear()
    run(bot._check_video(uid, "UP1001"))
    got = {g for g, _ in bot.qq.sent}
    ck("真有新投稿时两个群都收到", got == {gid_a, gid_b})
    os.unlink(path)


# ---------------------------------------------------------------- 2
def t_deleted_video_not_repushed():
    """删掉最新投稿后，退回来的更早投稿不能被当成新投稿重推。"""
    bot, path = make_bot()
    uid, gid = 1002, "GROUP_C"
    st = bot.store
    st.set_sub(gid, uid, "video", True)

    bot.bili.video[uid] = VideoInfo(bvid="BV1", title="第一篇", pubdate=1000)
    run(bot._check_video(uid, "UP1002"))       # 对齐

    bot.bili.video[uid] = VideoInfo(bvid="BV2", title="第二篇", pubdate=2000)
    run(bot._check_video(uid, "UP1002"))       # 真新 → 推
    if not ck("真的新投稿会推送",
              any("第二篇" in c for _, c in bot.qq.sent)):
        print("  实际推送内容：", bot.qq.sent)
        os.unlink(path)
        return

    # UP 把 BV2 删了 → 最新退回 BV1（更早）
    bot.qq.sent.clear()
    bot.bili.video[uid] = VideoInfo(bvid="BV1", title="第一篇", pubdate=1000)
    run(bot._check_video(uid, "UP1002"))
    ck("删稿后回退到更早的投稿：不重推", not bot.qq.sent)
    ck("回退后基线已更新为 BV1",
       (st.baseline_of(gid, uid, "video") or {}).get("bvid") == "BV1")
    os.unlink(path)


# ---------------------------------------------------------------- 3
def t_deleted_dynamic_not_repushed():
    """删掉最新动态后，退回来的更早动态不能被当成新动态重推。"""
    bot, path = make_bot()
    uid, gid = 1003, "GROUP_D"
    st = bot.store
    st.set_sub(gid, uid, "dynamic", True)

    bot.bili.dyn[uid] = DynamicInfo(dyn_id="D1", text="第一条", pubdate=1000)
    run(bot._check_dynamic(uid, "UP1003"))     # 对齐

    bot.bili.dyn[uid] = DynamicInfo(dyn_id="D2", text="第二条", pubdate=2000)
    run(bot._check_dynamic(uid, "UP1003"))     # 真新 → 推
    if not ck("真的新动态会推送", any("第二条" in c for _, c in bot.qq.sent)):
        os.unlink(path)
        return

    # 删掉 D2 → 最新退回 D1
    bot.qq.sent.clear()
    bot.bili.dyn[uid] = DynamicInfo(dyn_id="D1", text="第一条", pubdate=1000)
    run(bot._check_dynamic(uid, "UP1003"))
    ck("删动态后回退到更早的：不重推", not bot.qq.sent)
    os.unlink(path)


# ---------------------------------------------------------------- 4
def t_missing_pubdate_falls_back():
    """取不到发布时间时保持原行为（按 ID 判），不能因为缺字段就彻底不提醒。"""
    bot, path = make_bot()
    uid, gid = 1004, "GROUP_E"
    st = bot.store
    st.set_sub(gid, uid, "video", True)

    bot.bili.video[uid] = VideoInfo(bvid="BV_A", title="A", pubdate=0)
    run(bot._check_video(uid, "UP1004"))       # 对齐（无时间）
    bot.qq.sent.clear()
    bot.bili.video[uid] = VideoInfo(bvid="BV_B", title="B", pubdate=0)
    run(bot._check_video(uid, "UP1004"))
    ck("双方都取不到时间时仍按 ID 判（不静默丢提醒）", bool(bot.qq.sent))
    os.unlink(path)


# ---------------------------------------------------------------- 5
def t_off_on_realigns():
    """订阅项关掉再打开：期间的旧内容不补推，只静默重新对齐。"""
    bot, path = make_bot()
    uid, gid = 1005, "GROUP_F"
    st = bot.store
    st.set_sub(gid, uid, "video", True)

    bot.bili.video[uid] = VideoInfo(bvid="BV_1", title="1", pubdate=1000)
    run(bot._check_video(uid, "UP1005"))       # 对齐

    # 关掉视频订阅，期间 UP 发了一篇
    st.set_sub(gid, uid, "video", False)
    bot.bili.video[uid] = VideoInfo(bvid="BV_2", title="2", pubdate=2000)
    bot.qq.sent.clear()
    run(bot._check_video(uid, "UP1005"))
    ck("关掉期间不推送", not bot.qq.sent)

    # 重新打开
    st.set_sub(gid, uid, "video", True)
    bot.qq.sent.clear()
    run(bot._check_video(uid, "UP1005"))
    ck("重新开启：只静默对齐，不补推期间的新投稿", not bot.qq.sent)
    ck("重新开启后基线已更新",
       (st.baseline_of(gid, uid, "video") or {}).get("bvid") == "BV_2")

    # 之后再发新的才推
    bot.bili.video[uid] = VideoInfo(bvid="BV_3", title="3", pubdate=3000)
    bot.qq.sent.clear()
    run(bot._check_video(uid, "UP1005"))
    ck("重新对齐后新投稿正常推送", bool(bot.qq.sent))
    os.unlink(path)


# ---------------------------------------------------------------- 6
def t_pubdate_persisted():
    """基线要真的把发布时间存下来，否则时间比对形同虚设。"""
    bot, path = make_bot()
    uid, gid = 1006, "GROUP_G"
    st = bot.store
    st.set_sub(gid, uid, "video", True)
    bot.bili.video[uid] = VideoInfo(bvid="BV_X", title="X", pubdate=1700000000)
    run(bot._check_video(uid, "UP1006"))
    base = st.baseline_of(gid, uid, "video")
    ck("投稿基线保存了发布时间", (base or {}).get("pubdate") == 1700000000)

    st.set_sub(gid, uid, "dynamic", True)
    bot.bili.dyn[uid] = DynamicInfo(dyn_id="DYN_X", text="x", pubdate=1700000100)
    run(bot._check_dynamic(uid, "UP1006"))
    ck("动态基线保存了发布时间",
       (st.baseline_of(gid, uid, "dyn") or {}).get("pubdate") == 1700000100)
    os.unlink(path)


# ---------------------------------------------------------------- 7
def t_migration_runs():
    """老库升级：迁移函数不能因未定义常量直接崩掉。"""
    bot, path = make_bot()
    uid, gid = 1007, "GROUP_H"
    st = bot.store
    st.set_sub(gid, uid, "video", True)
    # 手工造一份"老版本 UP 主级基线"
    st.set_baseline(uid, "video", bvid="BV_LEGACY")
    try:
        n = st.migrate_baseline_to_per_group()
    except Exception as e:
        ck("迁移函数可正常执行", False)
        print("  异常：", e)
        os.unlink(path)
        return
    ck("迁移函数可正常执行", True)
    ck("老基线已复制成按群基线",
       (st.baseline_of(gid, uid, "video") or {}).get("bvid") == "BV_LEGACY")
    os.unlink(path)


# ---------------------------------------------------------------- 8
def t_align_does_not_clobber_other_group():
    """给 B 群做静默对齐时，不能顺手把 A 群待推的那条吞掉。

    _init_baseline 以前是"给所有订阅了该类型的群重写基线"，
    A 群正等着推的新内容会被一起改成"已对齐"，结果永远推不出去。
    """
    bot, path = make_bot()
    uid, gid_a, gid_b = 1008, "GROUP_I", "GROUP_J"
    st = bot.store
    st.set_sub(gid_a, uid, "video", True)

    bot.bili.video[uid] = VideoInfo(bvid="BV_1", title="1", pubdate=1000)
    run(bot._check_video(uid, "UP1008"))       # A 对齐

    # UP 发了新的，A 群还没来得及检测（投稿是慢速轮次）
    bot.bili.video[uid] = VideoInfo(bvid="BV_2", title="2", pubdate=2000)

    # 这时 B 群新订阅同一个 UP
    st.set_sub(gid_b, uid, "video", True)
    run(bot._init_baseline(uid))

    # A 群的基线必须还是旧的，否则 BV_2 就推不出去了
    ck("B 群对齐不影响 A 群进度",
       (st.baseline_of(gid_a, uid, "video") or {}).get("bvid") == "BV_1")
    ck("B 群已静默对齐到当前最新",
       (st.baseline_of(gid_b, uid, "video") or {}).get("bvid") == "BV_2")

    bot.qq.sent.clear()
    run(bot._check_video(uid, "UP1008"))
    got = {g for g, _ in bot.qq.sent}
    ck("A 群照常收到 BV_2", got == {gid_a})
    os.unlink(path)


def main():
    print("=== 按群基线 / 发布时间比对 ===")
    t_new_group_no_backfill()
    t_deleted_video_not_repushed()
    t_deleted_dynamic_not_repushed()
    t_missing_pubdate_falls_back()
    t_off_on_realigns()
    t_pubdate_persisted()
    t_migration_runs()
    t_align_does_not_clobber_other_group()
    bad = OK.count(False)
    print(f"\n合计 {len(OK)} 项，失败 {bad} 项")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
