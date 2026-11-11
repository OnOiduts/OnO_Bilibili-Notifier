# -*- coding: utf-8 -*-
"""退避之后不许立刻冲回最快档（8s ↔ 16s 自激振荡）。

真实现象（控制台.txt）：

    [WARNING] B 站接口超时/繁忙，轮询间隔 8s → 16s
    [INFO]    风控风险已解除，检查频率已回到最快：16s → 8s（最快档）
    [WARNING] B 站接口超时/繁忙，轮询间隔 8s → 16s
    [INFO]    风控风险已解除，检查频率已回到最快：16s → 8s（最快档）

两条各带 60s 日志冷却，看起来"紧挨着"，实际相隔一个完整周期：
一回最快就撞、一撞就退避、平安 300s 又回最快 —— 永远停在最快档附近抖。

根因：撞风控退避时记的是"这次撞了"，却没记"**是在哪一档撞的**"。
于是"平安 5 分钟"这个条件一到，就无条件一步跳回 min_interval，
而 min_interval 恰恰是刚刚证明撑不住的那一档。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.abspath("/data/workspace/_stub_botpy"))

from bot import NotifyBot as Bot  # noqa: E402

PASS = FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f"  {extra}" if extra else ""))


class FakeBili:
    """虚拟时钟：撞一次风控就把 since 归零，之后按轮次累加。"""

    def __init__(self):
        self._hits = 0
        self._since = 1e9
        self._reason = "code=-504"
        self._auth = False

    @property
    def risk_hits(self):
        return self._hits

    def since_risk(self):
        return self._since

    def risk_reason(self):
        return self._reason

    def auth_bad(self):
        return self._auth

    def auth_reason(self):
        return ""

    def trip(self):
        """这一轮撞了风控（超时/404/被拦）。"""
        self._hits += 1
        self._since = 0.0

    def tick(self, dt):
        self._since += dt


def new_bot(bili, interval=8, min_interval=8, base=12, maxi=300):
    b = Bot.__new__(Bot)
    b.bili = bili
    b.interval = interval
    b.min_interval = min_interval
    b.base_interval = base
    b.max_interval = maxi
    b._risk_seen = 0
    b._calm_rounds = 0
    b._iv_last_log = 0.0
    b._iv_from = None
    return b


def run(b, rounds, trip_every=0):
    """跑若干轮；trip_every>0 表示每 N 轮撞一次。返回 interval 轨迹。"""
    trace = []
    for i in range(rounds):
        b.bili.tick(b.interval)
        if trip_every and (i % trip_every == 0):
            b.bili.trip()
        Bot._adapt_interval(b)
        trace.append(b.interval)
    return trace


def main():
    # ---- 1. 复现：退避 → 平安 300s → 一步跳回最快（旧行为） ----
    fb = FakeBili()
    b = new_bot(fb, interval=8)
    fb.trip()                      # 在最快档撞了一次
    Bot._adapt_interval(b)
    ck("撞风控后退避到 16s", b.interval == 16, f"实际 {b.interval}")

    # 平安足够久
    for _ in range(40):
        fb.tick(10)
    Bot._adapt_interval(b)
    after_calm = b.interval

    # ---- 2. 修复后：不该回到"刚证明撑不住"的 8s ----
    ck("平安 300s 后不再冲回 8s", after_calm > 8,
       f"实际回到 {after_calm}s（8s 刚撞过，冲回去就是振荡源）")

    # ---- 3. 完整轨迹里不应出现 8 → 16 → 8 的来回 ----
    fb2 = FakeBili()
    b2 = new_bot(fb2, interval=8)
    tr = []
    for i in range(60):
        fb2.tick(b2.interval)
        # 只在 ≤8s 时才撞：模拟"最快档撑不住"
        if b2.interval <= 8:
            fb2.trip()
        Bot._adapt_interval(b2)
        tr.append(b2.interval)

    # 只要"从退避档降回最快档"出现一次，下一轮必然再撞 —— 那就是回摆
    swing = 0
    for i in range(1, len(tr)):
        if tr[i - 1] > 8 and tr[i] == 8:
            swing += 1
    ck("不再从退避档降回最快档", swing == 0,
       f"降回 8s {swing} 次，轨迹: {tr[:24]}")

    # ---- 4. 真的长期平安时，仍要能回到最快档（不能永久锁死） ----
    fb3 = FakeBili()
    b3 = new_bot(fb3, interval=8)
    fb3.trip()
    Bot._adapt_interval(b3)            # → 16
    ck("退避后是 16s", b3.interval == 16, f"实际 {b3.interval}")
    for _ in range(200):
        fb3.tick(10)                   # 累计 2000s 平安
        Bot._adapt_interval(b3)
    ck("长期平安后能回到最快档 8s", b3.interval == 8, f"实际 {b3.interval}s")

    # ---- 5. 回到最快后如果又撞，必须重新抬起地板 ----
    fb4 = FakeBili()
    b4 = new_bot(fb4, interval=8)
    fb4.trip()
    Bot._adapt_interval(b4)            # 8 → 16, floor=16
    for _ in range(200):
        fb4.tick(10)
        Bot._adapt_interval(b4)        # 逐步回到 8
    ck("先回到最快档", b4.interval == 8, f"实际 {b4.interval}")
    fb4.trip()
    Bot._adapt_interval(b4)
    ck("再撞一次又退避到 16s", b4.interval == 16, f"实际 {b4.interval}")
    for _ in range(40):
        fb4.tick(10)
        Bot._adapt_interval(b4)
    ck("再次退避后仍不冲回 8s", b4.interval > 8, f"实际 {b4.interval}")

    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
