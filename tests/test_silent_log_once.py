# -*- coding: utf-8 -*-
"""锁定「静默不推送」的日志**每轮只说一次**（v2.3.4）。

症状：
  日志里连着二十行一模一样的话 ——

      [星瞳_Official] 群 6FBBD9B5… 新投稿 BV1R8p26mEuf 已由投稿动态
      提醒过，静默不重复推送

  看着像反向去重（动态先推 → 投稿别再推一遍）失灵了，其实去重一直在
  正常工作：那条「新视频投稿」一次都没发到群里。坏的是**话说了 20 遍**。

为什么会重复：
  静默分支只负责把群加进 skip，不记账。只要基线因为某种原因没推进
  （落盘被系统拒绝、接口偶发没返回发布时间……），下一轮就会再次判定成
  「新投稿 → 该静默」，于是同一句话再打一行。

修法：`_silent_note` 按 (群, UP 主, 类型, 内容 ID) 记一笔，打过就不再打。
这里**故意把基线做成写不进去**，模拟最坏情况 —— 哪怕基线永远推进不了，
日志也只出现一次。
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
sys.path.insert(0, "/data/workspace/_stub2")
sys.path.insert(0, "/data/workspace/_deps")

import bot  # noqa: E402

PASS = FAIL = 0
FAILED = []


def ck(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  ", name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  FAIL", name, extra)


class _LogSpy:
    """接住机器人打出来的日志 —— 直接顶掉 bot._log。

    ⚠️ 为什么不挂 logging.Handler：项目里有自己的日志配置，往 root 上挂
    handler 整条都接不到（试过，捕获为空）。顶掉模块级 `_log` 最稳，
    也顺带让这个测试不依赖日志级别怎么设。
    """

    def __init__(self):
        self.infos = []

    def info(self, msg, *a, **k):
        self.infos.append(str(msg))

    def debug(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


class _NoopStore:
    """基线**写不进去**的存储 —— 模拟落盘被拒的最坏情况。"""

    def __init__(self):
        self.writes = 0

    def targets(self, uid, kind):
        return ["G1"]

    def baseline_of(self, gid, uid, kind):
        return {"bvid": "BV_OLD", "pubdate": 1700000000}

    def set_baseline_of(self, gid, uid, kind, **f):
        self.writes += 1          # 收到了，但就是存不下


class _V:
    bvid = "BV1R8p26mEuf"
    title = "和朋友这样吃饭不烧心"
    pubdate = 1760000000


class _Bili:
    async def get_latest_video(self, uid):
        return _V()


def _mkbot(store):
    b = bot.NotifyBot.__new__(bot.NotifyBot)
    b.cfg = {"silence_dyn_after_push": True}
    b.store = store
    b.bili = _Bili()
    b._note = lambda *a, **k: None
    b._log_check_fail = lambda *a, **k: None
    b._wm_pub = bot.NotifyBot._wm_pub
    b._is_newer_content = bot.NotifyBot._is_newer_content
    b._gid_also_gets_dynamic = lambda uid, gid: True
    b._content_pushed_by = lambda uid, t, v, kinds: True
    b._should_silence_video = lambda uid, v: False
    b._video_covered_by_season = lambda *a: False
    b._silent_noted = None

    async def _noop(*a, **k):
        pass
    b._push = _noop
    return b


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


_saved_log = None


def _count(msgs, needle):
    return sum(1 for m in msgs if needle in m)


def main():
    global _saved_log
    _saved_log = bot._log
    cap = _LogSpy()
    bot._log = cap

    store = _NoopStore()
    b = _mkbot(store)

    async def go():
        for _ in range(20):
            await b._check_video(1, "星瞳_Official")

    old = asyncio.get_event_loop_policy().new_event_loop()
    asyncio.set_event_loop(old)
    old.run_until_complete(go())

    hit = _count(cap.infos, "已由投稿动态提醒过")
    print(f"  20 轮下来这句话出现了 {hit} 次")

    ck(hit == 1, "同一句话只出现一次（20 轮检测）", f"实际 {hit}")
    ck(store.writes >= 20, "基线仍在每轮正常尝试写入",
       f"writes={store.writes}")

    # 换一条投稿 → 应该能再打一次（不是永久闭嘴）
    _V.bvid = "BV_NEW_2"
    asyncio.set_event_loop(asyncio.new_event_loop())
    asyncio.get_event_loop().run_until_complete(b._check_video(1, "星瞳_Official"))
    ck(_count(cap.infos, "已由投稿动态提醒过") == 2,
       "换一条投稿会再说一次（不是永久闭嘴）")
    _V.bvid = "BV1R8p26mEuf"

    bot._log = _saved_log

    print(f"\n通过 {PASS} / 失败 {FAIL}")
    if FAILED:
        print("失败项：")
        for n in FAILED:
            print("   -", n)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
