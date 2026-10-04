# -*- coding: utf-8 -*-
"""B站登录态：面板显示的必须和 B站 实测一致。

背景（v1.88.0）：
    面板的「已登录」以前只看 config 里有没有 "SESSDATA=" —— 这是**本地**
    判断。cookie 过期、被风控踢下线、粘了半串，它照样是 True；
    而接口一直返回 -352 / 412（明确要求登录态）。
    结果就是「前端显示已登录、后端显示未登录」，两边说法完全相反。

    bilibili.py 里其实早就有 _note_auth / auth_bad / AUTH_CODES，
    但**从来没人调用** —— 机器人一直知道登录态失效，却从不告诉面板。
"""
import asyncio
import os
import re
import sys

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE)

FAILS = []


def chk(name, cond, extra=""):
    if cond:
        print(f"  [ok] {name}")
    else:
        print(f"  [FAIL] {name} {extra}")
        FAILS.append(name)


# ---------------------------------------------------------------- 前端口径
def test_front_uses_verified_state():
    """面板各处必须按「B站 实测」显示，不能再用本地 has_login 直接判定。"""
    p = os.path.join(BASE, "static", "app.js")
    js = open(p, encoding="utf-8").read()

    chk("有统一的口径函数 biliEff/biliText",
        "function biliEff()" in js and "function biliText()" in js)
    # 顶栏那行以前是 BILI_STATE.has_login ? '已登录' : '未登录'
    chk("顶栏登录文案走 biliText()",
        re.search(r"tbBiliTx'\);?\s*\n?\s*var _be = biliEff\(\);", js) is not None
        or "bTx.textContent = biliText()" in js)
    chk("顶栏不再直接用 has_login 判定",
        "BILI_STATE.has_login\n    ? (BILI_STATE.expired" not in js)
    chk("健康度 B站登录项走 biliEff()",
        "var _bes = biliEff();" in js and "var biliOk = (_bes === 'logged');" in js)
    chk("待办项走 biliEff()",
        "var _bt = biliEff();" in js and "if (_bt !== 'logged')" in js)
    # 凭据页那格以前无条件写「已登录」，而它进来的前提恰恰是 B站 没认
    chk("凭据页不再无条件显示「已登录」",
        "'<div class=\"acc-name\">已登录</div>'" not in js)
    chk("未生效时给出明确说法",
        "未生效" in js and "-352/412" in js)
    chk("机器人实测（心跳）优先于本地判断",
        "hb.bili_login" in js and "bl.ok === false" in js)


# ---------------------------------------------------------------- 后端归类
def test_webui_state_helper():
    """state 必须以 B站 的回答为准，问不到时不能当成"已登录"。"""
    try:
        import webui
    except Exception as e:
        print(f"  [skip] webui 导入失败（缺依赖）：{e}")
        return
    f = webui._bili_login_state

    chk("没有 cookie → none",
        f("", {})[0] == "none")
    chk("B站 说已登录 → logged",
        f("SESSDATA=abc", {"ok": True})[0] == "logged")
    chk("B站 说无效 → invalid",
        f("SESSDATA=abc", {"ok": False, "msg": "cookie 无效或未登录"})[0] == "invalid")
    chk("code=-101（未登录）→ invalid",
        f("SESSDATA=abc", {"ok": False, "msg": "查询失败（code=-101）"})[0] == "invalid")
    chk("网络错误 → unknown（不能当失效）",
        f("SESSDATA=abc", {"ok": False, "msg": "网络错误：timeout"})[0] == "unknown")
    chk("空 msg → unknown",
        f("SESSDATA=abc", {})[0] == "unknown")
    chk("⚠️ 有 SESSDATA 但 B站 没认，绝不能归类为 logged",
        f("SESSDATA=abc", {"ok": False, "msg": "cookie 无效或未登录"})[0] != "logged")

    sp = webui._sessdata_suspicious
    chk("占位符 SESSDATA 要能识别", sp("SESSDATA=xxx") is True)
    chk("过短的 SESSDATA 要能识别", sp("SESSDATA=abc") is True)
    chk("正常长度的 SESSDATA 不误判",
        sp("SESSDATA=" + "8" * 40 + "%2Cabc") is False)


# ---------------------------------------------------------------- 客户端
class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload if payload is not None else {}

    async def json(self, **kw):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSess:
    def __init__(self, resp):
        self.resp = resp
        self.closed = False

    def get(self, *a, **kw):
        return self.resp


def _client(resp):
    from bilibili import BiliClient
    c = BiliClient(cookie="SESSDATA=test")
    c._min_interval = 0.0
    c._session = _FakeSess(resp)
    return c


def test_client_notes_auth():
    """-352 / HTTP 412 必须被记成「登录态不被认」，且成功时自动解除。"""
    from bilibili import BiliError

    c = _client(_FakeResp(200, {"code": -352}))
    try:
        asyncio.run(c._get("http://x", {}))
    except BiliError:
        pass
    chk("-352 记为认证类失败", c.auth_bad() is True)
    chk("-352 记下了原因", "-352" in (c.auth_reason() or ""))

    c2 = _client(_FakeResp(412, {}))
    try:
        asyncio.run(c2._get("http://x", {}))
    except BiliError:
        pass
    chk("HTTP 412 记为认证类失败", c2.auth_bad() is True)

    c3 = _client(_FakeResp(200, {"code": -403, "message": "访问权限不足"}))
    try:
        asyncio.run(c3._get("http://x", {}))
    except BiliError:
        pass
    chk("-403 记为认证类失败", c3.auth_bad() is True)

    c4 = _client(_FakeResp(200, {"code": 0, "data": {}}))
    c4._note_auth("预热")
    asyncio.run(c4._get("http://x", {}))
    chk("接口恢复正常后自动解除", c4.auth_bad() is False)


# ---------------------------------------------------------------- 机器人侧
def test_sync_bili_auth():
    """写心跳 + 汇总告警节流（不能每个 UP 每轮刷一条）。"""
    import bot as botmod
    from bot import NotifyBot

    captured = {}
    warns = []

    class _Log:
        def warning(self, *a, **kw):
            warns.append(a[0] if a else "")

        def info(self, *a, **kw):
            pass

        def debug(self, *a, **kw):
            pass

        def error(self, *a, **kw):
            pass

    class _Bili:
        def __init__(self, bad):
            self._bad = bad

        def auth_bad(self):
            return self._bad

        def auth_reason(self):
            return "code=-352"

    old_touch = botmod.heartbeat.touch
    old_log = botmod._log

    def fake_touch(**kw):
        captured.update(kw)

    botmod.heartbeat.touch = fake_touch
    botmod._log = _Log()
    try:
        b = NotifyBot.__new__(NotifyBot)
        b.bili = _Bili(True)
        b._auth_warn_at = 0.0
        b._sync_bili_auth()
        chk("登录态失效 → 心跳里记 ok=False",
            captured.get("bili_login", {}).get("ok") is False)
        chk("心跳带上失败原因",
            "-352" in str(captured.get("bili_login", {}).get("reason") or ""))
        chk("第一次会告警（根因要说清楚）", len(warns) == 1)
        chk("告警里点明要重新登录", any("重新登录" in w or "重新扫码" in w
                                        for w in warns))

        b._sync_bili_auth()
        b._sync_bili_auth()
        chk("10 分钟内不重复告警", len(warns) == 1, f"实际 {len(warns)} 条")

        # 5 个 UP 同时失败时，逐条告警必须降级，别刷屏
        dbg, wn = [], []
        lg = _Log()
        lg.debug = lambda *a, **kw: dbg.append(a[0] if a else "")
        lg.warning = lambda *a, **kw: wn.append(a[0] if a else "")
        botmod._log = lg
        for i in range(5):
            b._log_check_fail(f"UP{i}", "投稿", "B 站风控拦截（code=-352）")
        chk("认证类失败逐条降级为 debug", len(dbg) == 5 and len(wn) == 0,
            f"debug={len(dbg)} warn={len(wn)}")

        b2 = NotifyBot.__new__(NotifyBot)
        b2.bili = _Bili(False)
        b2._auth_warn_at = 0.0
        wn2 = []
        lg2 = _Log()
        lg2.warning = lambda *a, **kw: wn2.append(a[0] if a else "")
        botmod._log = lg2
        b2._log_check_fail("某UP", "投稿", "code=123 其它错误")
        chk("非认证类失败照常告警（不能静默）", len(wn2) == 1)

        captured.clear()
        b2._sync_bili_auth()
        chk("恢复后心跳写 ok=True",
            captured.get("bili_login", {}).get("ok") is True)
    finally:
        botmod.heartbeat.touch = old_touch
        botmod._log = old_log


def main():
    print("== B站登录态一致性 ==")
    test_front_uses_verified_state()
    test_webui_state_helper()
    test_client_notes_auth()
    test_sync_bili_auth()
    print()
    if FAILS:
        print(f"FAIL {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
