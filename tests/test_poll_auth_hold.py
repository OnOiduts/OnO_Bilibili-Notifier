# -*- coding: utf-8 -*-
"""登录态失效期间，轮询不许加速。

用户日志里同时出现：
    [WARNING] 撞到 B 站风控，轮询间隔 8s → 24s
    ... 满屏 -352 / HTTP 412 ...
    [INFO]   风控风险已解除，检查频率已回到最快：24s → 8s（最快档）

「风控风险已解除」是假的 —— -352/-403/HTTP 412 走 _note_auth，
**不计入 risk_hits**，于是 since_risk 一直涨，下面那条
「超过 5 分钟没风控 → 直接回最快」就在这时误判，一路把间隔压到最快档。
结果是每 8 秒撞一次墙，B站 风控更重、更难恢复，日志还在谎报解除。
"""
import os
import sys

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
    """⚠️ 记录用的列表不能叫 info/warning/debug —— 会把同名方法盖掉。"""

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
    """⚠️ 故意不提供 auth_bad 之外的东西：_adapt_interval 里对 auth 的
       取值必须自己兜底，不能因为缺属性把整轮巡查打断。"""

    def __init__(self, hits=0, since=1e9, bad=False, reason=""):
        self.risk_hits = hits
        self._since = since
        self._bad = bad
        self._reason = reason

    def since_risk(self):
        return self._since

    def auth_bad(self):
        return self._bad

    def auth_reason(self):
        return self._reason


def make_bot(interval=24, base=8, mn=8, mx=120, hits=0, since=1e9,
             bad=False, reason=""):
    import bot as B

    cls = B.NotifyBot
    self = cls.__new__(cls)          # 不跑 __init__（要连 QQ）
    B._log = LOG
    self.bili = FakeBili(hits, since, bad, reason)
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


def main():
    # ---- 1. 核心：登录态失效时绝不回到最快档 ----
    LOG.clear()
    # since=1e9（"很久没风控"）在以前必然一步回到 8s
    b = make_bot(interval=24, base=8, mn=8, mx=120, since=1e9, bad=True,
                 reason="code=-352")
    for _ in range(30):
        b._adapt_interval()
    ck("登录态失效时不回最快档", b.interval != b.min_interval)
    ck("停在认证档(30s)", b.interval == 30)
    ck("没谎报「风控风险已解除」",
       all("风控风险已解除" not in m for m in LOG.infos))
    ck("没谎报「撞到 B 站风控」",
       all("撞到 B 站风控" not in m for m in LOG.warnings))

    # ---- 2. 说的是登录态，不是风控 ----
    ck("日志点明登录态", any("登录态" in m for m in LOG.warnings))
    ck("日志点明暂停加速", any("暂停" in m for m in LOG.warnings))
    ck("退避只报一次（冷却）", len(LOG.warnings) == 1)

    # ---- 3. 多轮保持不动，不加速 ----
    iv = b.interval
    for _ in range(20):
        b._adapt_interval()
    ck("连续多轮不加速", b.interval == iv)

    # ---- 4. 上限/下限都要尊重 ----
    b2 = make_bot(interval=8, base=8, mn=8, mx=120, since=1e9, bad=True)
    b2._adapt_interval()
    ck("不快于基线（base=8 时仍拉到 30）", b2.interval == 30)

    b3 = make_bot(interval=8, base=60, mn=8, mx=120, since=1e9, bad=True)
    b3._adapt_interval()
    ck("基线 60s 时不倒退到 30s", b3.interval == 60)

    b4 = make_bot(interval=8, base=8, mn=8, mx=20, since=1e9, bad=True)
    b4._adapt_interval()
    ck("不超过配置上限 20s", b4.interval == 20)

    # ---- 5. 登录恢复后立刻回到正常加速 ----
    LOG.clear()
    b5 = make_bot(interval=30, base=8, mn=8, mx=120, since=1e9, bad=True)
    b5._adapt_interval()
    ck("失效时冻结在 30s", b5.interval == 30)
    b5.bili._bad = False             # 扫码成功 → _clear_auth 解除
    b5._iv_last_log = 0.0
    b5._adapt_interval()
    ck("恢复后一步回到最快档", b5.interval == b5.min_interval)
    ck("恢复后会说「风控风险已解除」",
       any("风控风险已解除" in m for m in LOG.infos))

    # ---- 6. 面板能看出来是"暂停加速"而不是卡住 ----
    b6 = make_bot(interval=30, base=8, mn=8, mx=120, since=1e9, bad=True)
    info = b6.interval_info()
    ck("interval_info 带 auth_hold", info.get("auth_hold") is True)
    b6.bili._bad = False
    ck("恢复后 auth_hold 为 False",
       b6.interval_info().get("auth_hold") is False)

    # ---- 7. 认证期间偶发真风控，解除后仍能正常退避（不被吞掉）----
    LOG.clear()
    b7 = make_bot(interval=30, base=8, mn=8, mx=120, since=1e9, bad=True)
    b7._adapt_interval()
    b7.bili.risk_hits = 5            # 认证期间也撞了真风控
    b7._adapt_interval()
    ck("认证期间真风控不触发退避（已锁在认证档）", b7.interval == 30)
    b7.bili._bad = False
    b7._iv_last_log = 0.0
    b7._adapt_interval()
    ck("解除后真风控仍会退避", b7.interval > b7.base_interval)

    # ---- 8. 没认证问题时行为完全不变（不回归）----
    LOG.clear()
    b8 = make_bot(interval=24, base=8, mn=8, mx=120, since=1e9, bad=False)
    for _ in range(30):
        b8._adapt_interval()
    ck("正常情况仍回到最快档", b8.interval == 8)
    ck("正常情况仍会报「风控风险已解除」",
       any("风控风险已解除" in m for m in LOG.infos))

    print(f"\n通过 {PASS} / 失败 {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
