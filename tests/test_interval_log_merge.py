# -*- coding: utf-8 -*-
"""间隔调整日志：整段加快过程合并成一条，不再每档都刷。

用户反馈「运行日志弹重复的消息」：从 24s 一路回到 8s 要连着回 4 档，
以前每档都打一条 INFO（「运行平稳，轮询间隔 Xs → Ys」），翻译后文案还
都是「检查频率自动调整了」，日志页看着就是同一条消息连着出现好几次。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

PASS = FAIL = 0


def ck(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


class FakeLog:
    """收集日志。⚠️ 记录用的列表不能叫 info/warning/debug ——
       那会把同名方法盖掉，_log.info(msg) 就变成调用 list 报错。"""

    def __init__(self):
        self._rec = {"info": [], "warning": [], "debug": []}

    def _add(self, k, msg, a):
        self._rec[k].append(msg % a if a else msg)

    def info(self, msg, *a):
        self._add("info", msg, a)

    def warning(self, msg, *a):
        self._add("warning", msg, a)

    def debug(self, msg, *a):
        self._add("debug", msg, a)

    @property
    def infos(self):
        return self._rec["info"]

    @property
    def warnings(self):
        return self._rec["warning"]

    @property
    def debugs(self):
        return self._rec["debug"]

    def clear(self):
        for v in self._rec.values():
            v.clear()


class FakeBili:
    def __init__(self, hits=0, since=1e9):
        self.risk_hits = hits
        self._since = since

    def since_risk(self):
        return self._since


def make_bot(interval=24, base=8, mn=8, mx=120, hits=0, since=1e9):
    import bot as B

    cls = B.NotifyBot
    self = cls.__new__(cls)          # 不跑 __init__（要连 QQ）
    # ⚠️ _adapt_interval 里用的是**模块级** _log（不是 self._log），
    #    所以这里必须换掉 bot._log 才收得到，挂到 self 上没用。
    B._log = LOG
    self.bili = FakeBili(hits, since)
    self.interval = interval
    self.base_interval = base
    self.min_interval = mn
    self.max_interval = mx
    self._risk_seen = 0
    self._calm_rounds = 0
    self._iv_from = None
    self._iv_last_log = 0.0
    return self


LOG = FakeLog()


def speed_up(bot, rounds=12):
    """连续跑若干轮「平安」，让间隔从当前值一路回到最快档。"""
    for _ in range(rounds):
        bot._adapt_interval()


def main():
    # ---- 1. 逐步加快：中间档不刷 INFO ----
    LOG.clear()
    # ⚠️ since 必须落在 60~300 之间才会走「逐步加快」那条路；
    #    默认的 1e9 会直接命中「>5 分钟没风控 → 一步回到最快档」，
    #    中间档根本不经过，下面「中间档进了 debug」必然不成立。
    b = make_bot(interval=24, since=100)
    # 每 3 轮才回一档（-20%）：24→19→15→12→9→8 要 15 轮才真正落到最快档，
    # 给 16 轮，保证最后一步会调 _iv_settled 打出那条汇总 INFO。
    speed_up(b, rounds=16)
    ck("逐步加快只打一条 INFO", len(LOG.infos) == 1)
    ck("中间档进了 debug", len(LOG.debugs) >= 2)
    ck("不再出现旧文案「运行平稳」",
       all("运行平稳" not in m for m in LOG.infos))
    if LOG.infos:
        msg = LOG.infos[0]
        ck("汇总带起点 24s", "24s" in msg)
        ck("汇总带终点 8s", "8s" in msg)
    else:
        ck("汇总带起点 24s", False)
        ck("汇总带终点 8s", False)

    # ---- 2. 撞风控：WARNING 只一条（60 秒冷却） ----
    LOG.clear()
    b2 = make_bot(interval=8, hits=1)
    b2._adapt_interval()
    ck("撞风控打 WARNING", len(LOG.warnings) == 1)
    old_iv = b2.interval
    ck("撞风控后退避", old_iv > 8)
    # 紧接着再撞一次：冷却期内只进 debug
    b2.bili.risk_hits = 2
    b2._adapt_interval()
    ck("冷却期内不再刷 WARNING", len(LOG.warnings) == 1)
    ck("冷却期内进了 debug", len(LOG.debugs) >= 1)

    # ---- 3. 冷却解除后能再打 ----
    b2._iv_last_log = time.time() - 120
    b2.bili.risk_hits = 3
    b2._adapt_interval()
    ck("冷却过后恢复 WARNING", len(LOG.warnings) == 2)

    # ---- 4. 已在最快档：不打日志 ----
    LOG.clear()
    b3 = make_bot(interval=8)
    speed_up(b3, rounds=6)
    ck("已在最快档不打日志", len(LOG.infos) == 0)

    # ---- 5. 状态字段存在，且撞风控会清掉起点 ----
    b4 = make_bot(interval=8, hits=1)
    b4._iv_from = 24
    b4._adapt_interval()
    ck("撞风控后清掉加快链起点", b4._iv_from is None)

    # ---- 6. 源码里保留合并逻辑 ----
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "bot.py"), encoding="utf-8").read()
    ck("有 _iv_settled 汇总方法", "def _iv_settled" in src)
    ck("中间档走 debug", "检查频率加快中" in src)
    ck("__init__ 初始化 _iv_from", "self._iv_from = None" in src)
    ck("__init__ 初始化 _iv_last_log", "self._iv_last_log = 0.0" in src)

    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
