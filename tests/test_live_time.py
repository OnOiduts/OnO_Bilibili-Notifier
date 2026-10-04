# -*- coding: utf-8 -*-
"""房间接口 live_time 是"日期字符串"，不能拿 int() 硬转。

真实事故：get_live 里写的是 `int(d.get("live_time") or 0)`，而官方
没在播时给的是 "0000-00-00 00:00:00" —— 它是**非空字符串**，`or 0`
兜不住，直接 ValueError: invalid literal for int() with base 10:
'0000-00-00 00:00:00'。

这个异常不是 BiliError，bot._check_live 又只 catch BiliError，于是
每轮直播检测都失败、开播下播提醒一条都不推，而且日志里看不出所以然。
面板上则表现为「直播状态 → 异常」。
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE)
os.environ["BOT_CONFIG"] = os.path.join(BASE, "config.yaml")

import bilibili as BL  # noqa: E402

ok = []


def ck(name, cond):
    ok.append(bool(cond))
    print(("  [PASS] " if cond else "  [FAIL] ") + name)
    return bool(cond)


_CST = timezone(timedelta(hours=8))


def t_to_epoch():
    f = BL._to_epoch
    ck("0000-00-00 00:00:00 → 0（不是报错）", f("0000-00-00 00:00:00") == 0)
    ck("空字符串 → 0", f("") == 0)
    ck("None → 0", f(None) == 0)
    ck("0 → 0", f(0) == 0)
    ck("负数 → 0", f(-1) == 0)
    ck("整数秒原样返回", f(1700000000) == 1700000000)
    ck("纯数字字符串", f("1700000000") == 1700000000)
    ck("毫秒(13位)收敛到秒", f(1700000000000) == 1700000000)
    ck("布尔不当时间", f(True) == 0 and f(False) == 0)
    ck("乱码 → 0", f("not a time") == 0)
    ck("列表 → 0", f(["x"]) == 0)

    # 北京时间字符串：必须按 UTC+8 解析，不能当成 UTC，否则差 8 小时
    want = int(datetime(2026, 9, 22, 0, 13, 26, tzinfo=_CST).timestamp())
    ck("日期字符串按北京时间解析", f("2026-09-22 00:13:26") == want)
    ck("日期字符串(到分)也能解析",
       f("2026-09-22 00:13") == int(
           datetime(2026, 9, 22, 0, 13, tzinfo=_CST).timestamp()))
    ck("1970 之前的年份当没有", f("1970-01-01 08:00:00") == 0)


def t_get_live():
    """构造官方真实返回，确认 get_live 不再抛异常。"""

    class _Stub:
        def __init__(self, data):
            self._data = data

        async def _throttle(self, *a, **k):
            return None

        async def _get(self, url, params=None, referer=None, **k):
            return {"code": 0, "data": self._data}

    async def run():
        base = {"room_id": 12345, "short_id": 0, "live_status": 1,
                "title": "测试", "online": 10}
        got = {}

        async def one(live_time, label):
            c = BL.BiliClient.__new__(BL.BiliClient)
            d = dict(base)
            d["live_time"] = live_time
            c._throttle = _Stub(d)._throttle
            c._get = _Stub(d)._get
            try:
                got[label] = await c.get_live(12345)
            except Exception as e:
                got[label] = e
        await one("0000-00-00 00:00:00", "offline")
        await one("2026-09-22 00:13:26", "live")
        await one(0, "zero")
        await one("1700000000", "numeric")
        return got

    got = asyncio.run(run())
    ck("没在播(0000-00-00)不抛异常",
       not isinstance(got["offline"], Exception))
    ck("没在播时 live_time 归一为 0",
       not isinstance(got["offline"], Exception)
       and got["offline"].live_time == 0)
    ck("在播时解析出开播时刻",
       not isinstance(got["live"], Exception)
       and got["live"].live_time == int(
           datetime(2026, 9, 22, 0, 13, 26, tzinfo=_CST).timestamp()))
    ck("数字字符串也能解析",
       not isinstance(got["numeric"], Exception)
       and got["numeric"].live_time == 1700000000)
    ck("0 不抛异常",
       not isinstance(got["zero"], Exception)
       and got["zero"].live_time == 0)
    # 开播时刻要早于"现在"，否则算出来的时长会是负数/荒谬值
    if not isinstance(got["live"], Exception):
        import time
        ck("解析出的开播时刻是个合理值",
           0 < got["live"].live_time <= int(time.time()) + 86400)


def t_bot_catch():
    """_check_live 必须连非 BiliError 一起接住，否则整条直播链静默死掉。"""
    src = open(os.path.join(BASE, "bot.py"), encoding="utf-8").read()
    i = src.find("async def _check_live")
    seg = src[i:i + 3000]
    ck("接住 BiliError", "except BiliError" in seg)
    ck("也接住其它异常（解析类不再静默致命）",
       "except Exception" in seg)
    ck("异常写进面板可见的最近检测结果", "检测异常" in seg)


if __name__ == "__main__":
    print("[live_time 解析]")
    t_to_epoch()
    t_get_live()
    t_bot_catch()
    print(f"  ---- {sum(ok)}/{len(ok)} 通过")
    sys.exit(0 if all(ok) else 1)
