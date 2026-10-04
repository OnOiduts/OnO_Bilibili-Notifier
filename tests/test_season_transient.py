# -*- coding: utf-8 -*-
"""合集检测遇到 -504 服务调用超时的处理。

背景（用户实测反馈）：
    合集 3175959 报 code=-504 msg=服务调用超时，日志却甩出
    「①UP 主删掉了这个合集；②填的是 series 的 ID；③uid 对不上」
    —— 这是**临时故障**，照着提示去删订阅反而把好端端的订阅弄丢了。
    而且临时故障当时既不退避也不降频，每轮照打，把轮询间隔顶到 24s。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import bilibili
from bilibili import (BiliError, BiliTransientError, SeasonGoneError,
                      is_transient)
import bot as botmod


# ---------- 1. 错误分类 ----------

def test_504_is_transient_not_gone():
    """-504 服务调用超时 = 临时故障，绝不能说成"合集没了"。"""
    assert is_transient(BiliError("code=-504 msg=服务调用超时"))
    assert is_transient(BiliTransientError("code=-504 msg=服务调用超时"))


def test_502_503_500_transient():
    for c in (-500, -502, -503):
        assert is_transient(BiliError(f"code={c} msg=x")), c


def test_network_timeout_transient():
    assert is_transient(BiliError("网络请求失败: TimeoutError()"))


def test_404_is_not_transient():
    """-404 这类是真的取不到，不能被当成"等等就好"。"""
    assert not is_transient(BiliError("code=-404 msg=啥都木有"))


def test_transient_codes_defined():
    assert -504 in bilibili.BiliClient.TRANSIENT_CODES
    assert -412 in bilibili.BiliClient.RISK_CODES


# ---------- 2. 接口层：临时故障要抛出去，不能静默当空合集 ----------

class _BiliStub:
    """只替换 _get，其余走真实 get_season_archives。"""

    def __init__(self, err):
        self.err = err
        self.calls = 0

    async def _get(self, url, params, referer="", retry=2):
        self.calls += 1
        raise self.err

    async def _wbi_sign(self, base):
        raise BiliError("no wbi")

    async def _throttle(self, key):
        return None

    async def ensure_buvid3(self):
        return None


def _season_call(err):
    b = bilibili.BiliClient(cookie="")
    stub = _BiliStub(err)
    b._get = stub._get
    b._wbi_sign = stub._wbi_sign
    b._throttle = stub._throttle
    b.ensure_buvid3 = stub.ensure_buvid3

    async def run():
        return await b.get_season_archives(280242571, 3175959, max_pages=2)
    return asyncio.get_event_loop().run_until_complete(run())


def test_season_504_raises_transient():
    try:
        _season_call(BiliError("code=-504 msg=服务调用超时"))
    except SeasonGoneError as e:
        raise AssertionError(f"-504 被误判成合集永久失效：{e}")
    except BiliTransientError:
        pass
    else:
        raise AssertionError("-504 被静默吞掉了：既不抛错也没结果，"
                             "上层收不到日志也不会退避")


def test_season_504_msg_has_no_misleading_hint():
    try:
        _season_call(BiliError("code=-504 msg=服务调用超时"))
    except BiliTransientError as e:
        s = str(e)
        assert "UP 主删掉了" not in s, "临时故障不该劝用户删订阅"
        assert "series" not in s
        assert "重新查一次" not in s
        assert "超时" in s or "繁忙" in s


def test_season_gone_keeps_hint():
    try:
        _season_call(BiliError("code=-404 msg=啥都木有"))
    except SeasonGoneError as e:
        assert "UP 主删掉了" in str(e) or "重新查一次" in str(e)
    else:
        raise AssertionError("永久错误没抛 SeasonGoneError")


# ---------- 3. 上层：退避，不再每轮猛打 ----------

class _BotStub:
    """只驱动 _check_seasons 的失败分支。"""

    def __init__(self, err):
        self.err = err
        self.slow_every = 1
        self._round = 1
        self._season_fail = {}
        self._season_skip = {}
        self.tried = 0

    class store:
        @staticmethod
        def all_season_keys():
            return [("G1", "280242571", "3175959")]

    async def _check_one_season(self, uid, sid):
        self.tried += 1
        raise self.err


def _run_rounds(err, n=6):
    b = _BotStub(err)
    b._check_seasons = botmod.NotifyBot._check_seasons.__get__(b)
    for _ in range(n):
        asyncio.get_event_loop().run_until_complete(b._check_seasons())
    return b


def test_transient_backs_off():
    """连续临时失败 → 逐渐少查（1、2、4、8 轮），不是每轮照打。"""
    b = _run_rounds(BiliTransientError("code=-504 msg=服务调用超时"), 6)
    # 6 轮里只真查了 3 次（第1轮、隔1轮、隔2轮…），而不是 6 次
    assert b.tried <= 4, f"退避没生效，6 轮里查了 {b.tried} 次"
    assert b.tried >= 1
    assert b._season_fail[(280242571, 3175959)] == b.tried


def test_transient_skip_caps_at_8():
    b = _run_rounds(BiliTransientError("code=-504 msg=超时"), 40)
    assert max(b._season_skip.values(), default=0) <= 8


def test_success_clears_backoff():
    """恢复之后不能还在歇着 —— 否则合集更新了永远查不到。"""
    class _Flaky(_BotStub):
        async def _check_one_season(self, uid, sid):
            self.tried += 1
            if self.tried <= 2:
                raise BiliTransientError("code=-504 msg=服务调用超时")
            return None

    b = _Flaky(BiliTransientError("x"))
    b._check_seasons = botmod.NotifyBot._check_seasons.__get__(b)
    for _ in range(10):
        asyncio.get_event_loop().run_until_complete(b._check_seasons())
    assert b.tried >= 3, "恢复后仍被退避挡住，合集更新会漏"
    assert b._season_skip.get((280242571, 3175959), 0) == 0
    assert (280242571, 3175959) not in b._season_fail


def test_gone_does_not_back_off():
    """真·合集没了：重试也没用，按老规则计数（不占退避位）。"""
    b = _run_rounds(SeasonGoneError("合集 3175959 取不到"), 5)
    assert b.tried == 5
    assert b._season_skip.get((280242571, 3175959), 0) == 0


# ---------- 4. 日志说清楚是"超时"还是"被拦" ----------

def _why(reason):
    class _B:
        def risk_reason(self):
            return reason
    b = _BotStub(BiliTransientError("x"))
    b.bili = _B()
    b._risk_why = botmod.NotifyBot._risk_why.__get__(b)
    return b._risk_why()


def test_risk_why_timeout():
    """-504 是对面没扛住，不能说成"我们请求太猛"。"""
    w = _why("code=-504")
    assert "超时" in w or "繁忙" in w, w
    assert "风控" not in w


def test_risk_why_blocked():
    assert "拦截" in _why("code=-412")


def test_risk_why_default():
    assert _why("") == "撞到 B 站风控（请求过频）"


if __name__ == "__main__":
    import traceback
    ok = fail = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                ok += 1
                print(f"  PASS {name}")
            except Exception as e:
                fail += 1
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print(f"\n{ok} 通过 / {fail} 失败")
    sys.exit(1 if fail else 0)
