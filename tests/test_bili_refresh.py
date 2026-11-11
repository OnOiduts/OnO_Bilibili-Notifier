# -*- coding: utf-8 -*-
"""点「续期」必须真的换到新 cookie —— 不能只弹一句「请求错误」就完事。

背景：
    面板上的「续期」原来只走 passport 的
    /x/passport-login/web/cookie/refresh。这个接口现在经常直接失败
    （用户看到的就是「续期失败：请求错误」），大多是服务端风控，
    裸接口很难过 —— 结果就是按钮点了等于没点，cookie 一直旧着。

    真实浏览器里访问 B 站时，登录态是 B 站前端自己续的，带着真实浏览器
    环境，成功率远高于裸接口。所以现在两条路：接口换不动，就开浏览器
    让 B 站自己换。

    ⚠️ 换到的新 cookie 只是**候选**：必须先用 account_info 向 B 站求证，
    它说不认就保留原来那串不动。直接覆盖会把好 cookie 换成残的 ——
    面板照样显示「已登录」，接口却一直 -352，那种最难排查。
"""
import asyncio
import os
import sys
import time
import types

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE)

FAILS = []


def chk(name, cond, extra=""):
    if cond:
        print(f"  [ok] {name}")
    else:
        print(f"  [FAIL] {name} {extra}")
        FAILS.append(name)


SLOW = 5   # 模拟"开浏览器"的耗时（>前端 30s 超时做不到，够验秒回即可）


def _old_cookie():
    return "SESSDATA=oldoldoldold; bili_jct=jtold; DedeUserID=123"


def _new_cookie():
    return "SESSDATA=newnewnewnew; bili_jct=jtnew; DedeUserID=123; buvid3=xyz"


# ------------------------------------------------------- 假的 playwright
def install_fake_playwright(monkey, results):
    """给 bili_browser_login 装一个假 playwright。

    results：每次 ctx.cookies() 依次返回的 cookie 列表（不够了就用最后一个）。
    每项形如 [{"name": "SESSDATA", "value": "...", "expires": 秒级时间戳}]
    """
    seq = list(results)

    class FakeContext:
        def __init__(self):
            self.added = []
            self.visited = []

        async def add_cookies(self, jar):
            self.added = list(jar)

        async def new_page(self):
            return FakePage(self)

        async def cookies(self):
            if len(seq) > 1:
                return seq.pop(0)
            return list(seq[0])

    class FakePage:
        def __init__(self, ctx):
            self.ctx = ctx

        async def goto(self, url, **kw):
            self.ctx.visited.append(url)

    class FakeBrowser:
        def __init__(self):
            self.ctx = None

        async def new_context(self, **kw):
            self.ctx = FakeContext()
            return self.ctx

        async def close(self):
            pass

    holder = {}

    class FakeChromium:
        async def launch(self, **kw):
            b = FakeBrowser()
            holder["browser"] = b
            return b

    class FakePW:
        chromium = FakeChromium()

    class FakeAP:
        async def __aenter__(self):
            return FakePW()

        async def __aexit__(self, *a):
            return False

    mod = types.ModuleType("playwright")
    sub = types.ModuleType("playwright.async_api")
    sub.async_playwright = lambda: FakeAP()
    mod.async_api = sub
    sys.modules["playwright"] = mod
    sys.modules["playwright.async_api"] = sub
    holder["get_ctx"] = lambda: holder.get("browser").ctx if holder.get("browser") else None
    return holder


def uninstall_fake_playwright():
    sys.modules.pop("playwright", None)
    sys.modules.pop("playwright.async_api", None)


# --------------------------------------------------- 1. 浏览器静默续期
def _fresh(days=7, value="newnewnewnew"):
    exp = time.time() + days * 86400
    return [{"name": "SESSDATA", "value": value, "expires": exp},
            {"name": "bili_jct", "value": "jtnew", "expires": exp},
            {"name": "DedeUserID", "value": "123", "expires": exp},
            {"name": "buvid3", "value": "xyz", "expires": exp}]


def test_browser_refresh_got_new():
    """浏览器里 B 站给了新 SESSDATA → 换到了。"""
    import bili_browser_login as BBL
    holder = install_fake_playwright(None, [_fresh(value="newnewnewnew")])
    try:
        res = asyncio.run(BBL.browser_refresh(_old_cookie(), timeout=6))
    finally:
        uninstall_fake_playwright()
    chk("浏览器续期成功", bool(res.get("cookie_str")), str(res))
    chk("拿到的是新的 SESSDATA", "newnewnewnew" in (res.get("cookie_str") or ""),
        str(res.get("cookie_str")))
    chk("标记为换过了", res.get("changed") is True, str(res))
    chk("没有要求重新登录", not res.get("need_relogin"), str(res))
    ctx = holder["get_ctx"]()
    chk("旧 cookie 被放进了浏览器",
          any(c.get("name") == "SESSDATA" for c in (ctx.added if ctx else [])))
    chk("确实访问了 B 站",
          any("bilibili.com" in u for u in (ctx.visited if ctx else [])))


def test_browser_refresh_longer_expiry_counts():
    """SESSDATA 没变、但过期时间变长了，也算换到新的。"""
    import bili_browser_login as BBL
    old = f"SESSDATA=oldold%2C{int(time.time() + 3600)}%2Cabc*1; bili_jct=jtold"
    holder = install_fake_playwright(
        None, [_fresh(days=7, value=f"oldold%2C{int(time.time() + 7 * 86400)}%2Cabc*1")])
    try:
        res = asyncio.run(BBL.browser_refresh(old, timeout=6))
    finally:
        uninstall_fake_playwright()
    chk("过期时间变长也算换到", res.get("changed") is True, str(res))


def test_browser_refresh_unchanged_still_ok():
    """B站 这会儿没给新的：登录态还在，如实说没换。"""
    import bili_browser_login as BBL
    ts = int(time.time() + 7 * 86400)
    old = f"SESSDATA=oldold%2C{ts}%2Cabc*1; bili_jct=jtold"
    same = [{"name": "SESSDATA", "value": f"oldold%2C{ts}%2Cabc*1", "expires": float(ts)},
            {"name": "bili_jct", "value": "jtold", "expires": float(ts)},
            {"name": "DedeUserID", "value": "123", "expires": float(ts)}]
    holder = install_fake_playwright(None, [same])
    try:
        res = asyncio.run(BBL.browser_refresh(old, timeout=4))
    finally:
        uninstall_fake_playwright()
    chk("没换到也返回可用结果", bool(res.get("cookie_str")), str(res))
    chk("如实说没换", res.get("changed") is False, str(res))
    chk("不要把没换说成失效", not res.get("need_relogin"), str(res))


def test_browser_refresh_dead_cookie():
    """注入的 SESSDATA 被页面清掉 = 已经不被认可 → 要人重新登录。"""
    import bili_browser_login as BBL
    holder = install_fake_playwright(None, [[{"name": "buvid3", "value": "x", "expires": 0.0}]])
    try:
        res = asyncio.run(BBL.browser_refresh(_old_cookie(), timeout=4))
    finally:
        uninstall_fake_playwright()
    chk("认出 cookie 已死", res.get("need_relogin") is True, str(res))
    chk("给出去重新登录的提示", "重新登录" in (res.get("msg") or ""), str(res))


# ------------------------------------------------------- 2. 两级编排
def test_renew_via_passport():
    """接口能换就用接口（快，不用开浏览器）。"""
    import bili_login as b

    async def fake_passport(cookie):
        return {"ok": True, "cookie": _new_cookie(), "expires": time.time() + 7 * 86400}

    orig = b.refresh_cookie
    b.refresh_cookie = fake_passport
    try:
        res = asyncio.run(b.renew_cookie(_old_cookie()))
    finally:
        b.refresh_cookie = orig
    chk("接口续期成功", res.get("ok") is True, str(res))
    chk("走的是接口", res.get("via") == "passport", str(res))
    chk("拿到了新 cookie", "newnewnewnew" in res.get("cookie", ""), str(res))


def test_renew_falls_back_to_browser():
    """接口换不动 → 自动开浏览器换（这才是"点了就该换到"）。"""
    import bili_login as b
    import bili_browser_login as BBL

    async def fake_passport(cookie):
        return {"ok": False, "msg": "续期失败：请求错误"}

    holder = install_fake_playwright(None, [_fresh(value="newnewnewnew")])
    orig = b.refresh_cookie
    b.refresh_cookie = fake_passport
    try:
        res = asyncio.run(b.renew_cookie(_old_cookie()))
    finally:
        b.refresh_cookie = orig
        uninstall_fake_playwright()
    chk("接口失败后浏览器接力成功", res.get("ok") is True, str(res))
    chk("记下走的是浏览器", res.get("via") == "browser", str(res))
    chk("真的换到了新 cookie", "newnewnewnew" in res.get("cookie", ""), str(res))
    chk("把接口那次失败记下来了",
          any("请求错误" in t for t in res.get("tried", [])), str(res))


def test_renew_reports_when_dead():
    """两条路都换不动（cookie 已经死了）→ 明确说要重新登录。"""
    import bili_login as b

    async def fake_passport(cookie):
        return {"ok": False, "msg": "续期失败：请求错误"}

    holder = install_fake_playwright(None, [[{"name": "buvid3", "value": "x", "expires": 0.0}]])
    orig = b.refresh_cookie
    b.refresh_cookie = fake_passport
    try:
        res = asyncio.run(b.renew_cookie(_old_cookie()))
    finally:
        b.refresh_cookie = orig
        uninstall_fake_playwright()
    chk("两条路都失败时明确失败", res.get("ok") is False, str(res))
    chk("提示要重新登录", res.get("need_relogin") is True, str(res))


# ------------------------------------------------- 3. 面板：验证后才写盘
def _drain_refresh(webui, limit=600):
    """等后台续期任务跑完（前端就是这么轮询的）。"""
    for _ in range(limit):
        if webui._brefresh.get("done"):
            return True
        time.sleep(0.02)
    return False


def _run_refresh_api(passport_res, browser_results, acc_res):
    """真起 Flask 跑一次 /api/bili/refresh，返回 (响应 json, 保存过的 cfg)。

    续期是后台任务：POST 只负责启动，结果要从 status 里取 —— 这里的
    等待方式和前端轮询一致，不是同步拿返回值。
    """
    import bili_login as b
    import webui

    async def fake_passport(cookie):
        return passport_res

    async def fake_acc(cookie):
        return acc_res

    saved = {}

    def fake_load_cfg():
        return {"bili_cookie": _old_cookie(), "bili_cookie_ts": time.time() - 86400}

    def fake_save_cfg(cfg):
        saved.update(cfg)

    holder = install_fake_playwright(None, browser_results)
    op, oa, ol, os_ = (b.refresh_cookie, b.account_info,
                       webui.load_cfg, webui.save_cfg)
    b.refresh_cookie = fake_passport
    b.account_info = fake_acc
    webui.load_cfg = fake_load_cfg
    webui.save_cfg = fake_save_cfg
    try:
        with webui.app.test_request_context():
            from flask import jsonify  # noqa: F401
            resp = webui.api_bili_refresh()
            data = resp.get_json()
            if data.get("ok") and data.get("started"):
                _drain_refresh(webui)
                data = {"ok": bool(webui._brefresh.get("ok")),
                        "msg": webui._brefresh.get("msg", ""),
                        "via": webui._brefresh.get("via", "")}
    finally:
        b.refresh_cookie, b.account_info = op, oa
        webui.load_cfg, webui.save_cfg = ol, os_
        uninstall_fake_playwright()
    return data, saved


def test_api_saves_when_bilibili_ok():
    """B站 认新 cookie → 写盘，且有效期重新算。"""
    data, saved = _run_refresh_api(
        {"ok": False, "msg": "续期失败：请求错误"},
        [_fresh(value="newnewnewnew")],
        {"ok": True, "uname": "孜岚", "mid": 123})
    chk("返回成功", data.get("ok") is True, str(data))
    chk("说明是浏览器换的", "浏览器" in (data.get("msg") or ""), str(data))
    chk("新 cookie 已写盘", "newnewnewnew" in (saved.get("bili_cookie") or ""), str(saved))
    chk("到期时间已刷新", float(saved.get("bili_cookie_expires") or 0) > time.time(),
        str(saved.get("bili_cookie_expires")))
    chk("时间戳已重置为现在",
        abs(float(saved.get("bili_cookie_ts") or 0) - time.time()) < 60)


def test_api_refuses_bad_new_cookie():
    """换到的新 cookie B站 不认 → 不覆盖，原来的留在那儿。"""
    data, saved = _run_refresh_api(
        {"ok": False, "msg": "续期失败：请求错误"},
        [_fresh(value="newnewnewnew")],
        {"ok": False, "msg": "查询失败（code=-101）"})
    chk("拒绝保存", data.get("ok") is False, str(data))
    chk("说清为什么", "无效" in (data.get("msg") or ""), str(data))
    chk("提示去重新登录", "浏览器登录" in (data.get("msg") or ""), str(data))
    chk("原来的 cookie 没被动过", not saved, str(saved))


def test_api_no_cookie():
    """压根没 cookie → 直接说没有，别去开浏览器。"""
    import webui
    orig = webui.load_cfg
    webui.load_cfg = lambda: {}
    try:
        with webui.app.test_request_context():
            data = webui.api_bili_refresh().get_json()
            if data.get("ok") and data.get("started"):
                _drain_refresh(webui)
                data = {"ok": bool(webui._brefresh.get("ok")),
                        "msg": webui._brefresh.get("msg", "")}
    finally:
        webui.load_cfg = orig
    chk("没 cookie 时明确报错", data.get("ok") is False, str(data))
    chk("提示还没有 cookie", "还没有" in (data.get("msg") or ""), str(data))


# ------------------------------------------- 3b. 点一下不能把面板点超时
def test_post_returns_immediately():
    """点「续期」必须立刻返回 —— 不能让前端等到超时。

    用户实际遇到的症状：按钮变「正在续期…」，然后弹出
    「请求超时——面板这会儿没响应，等几秒再试」。
    原因就是续期（含开浏览器）同步跑在 HTTP 请求里，前端 30 秒一到就 abort。

    这里故意让续期睡 20 秒（比真实浏览器还短得多），POST 仍必须秒回。
    """
    import bili_login as b
    import webui

    async def slow_renew(cookie, allow_browser=True, on_stage=None):
        time.sleep(SLOW)        # 模拟开浏览器 + 等页面
        return {"ok": True, "cookie": _new_cookie(), "expires": 0,
                "via": "browser", "changed": True}

    async def fake_acc(cookie):
        return {"ok": True, "uname": "孜岚", "mid": 123}

    def fake_load_cfg():
        return {"bili_cookie": _old_cookie(), "bili_cookie_ts": time.time() - 86400}

    saved = {}

    op, oa, ol, os_ = (b.renew_cookie, b.account_info,
                       webui.load_cfg, webui.save_cfg)
    b.renew_cookie = slow_renew
    b.account_info = fake_acc
    webui.load_cfg = fake_load_cfg
    webui.save_cfg = lambda cfg: saved.update(cfg)
    t0 = time.time()
    try:
        with webui.app.test_request_context():
            data = webui.api_bili_refresh().get_json()
        elapsed = time.time() - t0
        # 重复点击要挡住（还在跑的时候）
        guard = _repeat_click_guard(webui)
        # ⚠️ 必须在还原 mock 之前等后台线程跑完：否则线程醒来后会去用
        #    真实的 account_info 发网络请求。
        _drain_refresh(webui, limit=1200)
    finally:
        b.renew_cookie, b.account_info = op, oa
        webui.load_cfg, webui.save_cfg = ol, os_
    chk("POST 立刻返回（< 3s）", elapsed < 3, f"实得 {elapsed:.1f}s")
    chk("返回的是「已启动」", data.get("started") is True, str(data))
    chk("已经在跑时重复点不会重开", guard is True, str(guard))
    chk("慢任务跑完后结果是对的",
        webui._brefresh.get("done") is True and webui._brefresh.get("ok") is True,
        str(webui._brefresh.get("msg")))


def _repeat_click_guard(webui):
    """任务跑着的时候再点一次，应当回 already 而不是再开一个线程。"""
    if not webui._brefresh.get("running"):
        return None
    with webui.app.test_request_context():
        d = webui.api_bili_refresh().get_json()
    return d.get("already") is True


# ------------------------------------------------------- 4. 源码守门
def test_sources_wired():
    """接线要在：renew_cookie 存在、面板走它、浏览器有静默续期。"""
    bl = open(os.path.join(BASE, "bili_login.py"), encoding="utf-8").read()
    wl = open(os.path.join(BASE, "webui.py"), encoding="utf-8").read()
    bbl = open(os.path.join(BASE, "bili_browser_login.py"), encoding="utf-8").read()
    chk("bili_login 有两级编排", "async def renew_cookie" in bl)
    chk("编排里会接力浏览器", "browser_refresh" in bl)
    chk("面板走的是两级编排", "bili_login.renew_cookie(" in wl)
    chk("面板保存前先向 B站 求证", "account_info(new_cs)" in wl)
    chk("面板无效时不写盘", "原来的没动" in wl)
    # 后台任务：不能让「开浏览器」把 HTTP 请求拖超时
    chk("续期有独立的状态容器", "_brefresh = {" in wl)
    chk("续期跑在后台线程里",
        "threading.Thread(target=_run_refresh" in wl)
    chk("有进度查询端点", "/api/bili/refresh/status" in wl)
    chk("有看门狗兜底", "_run_refresh" in wl and "watchdog" in wl)
    js = open(os.path.join(BASE, "static", "app.js"), encoding="utf-8").read()
    chk("前端有轮询函数", "async function pollRefresh" in js)
    chk("前端点续期后改为轮询",
        "setInterval(pollRefresh" in js)
    chk("前端不再同步等结果",
        "post('/api/bili/refresh', {})" in js
        and "d.ok ? '已续期" not in js)
    chk("浏览器模块有静默续期", "async def browser_refresh" in bbl)
    chk("浏览器续期认得已死的 cookie", "need_relogin" in bbl)


def main():
    test_browser_refresh_got_new()
    test_browser_refresh_longer_expiry_counts()
    test_browser_refresh_unchanged_still_ok()
    test_browser_refresh_dead_cookie()
    test_renew_via_passport()
    test_renew_falls_back_to_browser()
    test_renew_reports_when_dead()
    test_api_saves_when_bilibili_ok()
    test_api_refuses_bad_new_cookie()
    test_api_no_cookie()
    test_post_returns_immediately()
    test_sources_wired()
    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)} -> {FAILS}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
