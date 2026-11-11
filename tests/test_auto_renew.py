# -*- coding: utf-8 -*-
"""续期「更稳口径」+ 到期提醒自动跑一次 —— 真值测试。

两件事钉死（都是用户实际踩过的）：

1. 没换到新 cookie 时，**不能**把 bili_cookie_ts 重算成 now()。
   原来无条件重算，等于把本地提醒计时偷偷往后推 7 天：B 站明天
   把旧会话踢了，面板还显示「还剩 7 天」，第 5/6 天提醒反而来晚。
   断言不只比时间戳，还把续期后的 config 喂回 _auto_renew_tick，
   验「用户看到的世界」：旧写法会误判成健康档、不再提醒。

2. 提醒触发时自动在后台跑一次续期。面板与机器人是两个独立子进程，
   所以放在面板进程内直接复用 _start_refresh，不走 HTTP（不用处理
   端口文件 / CSRF 头 / 面板口令三条会坏的路）。

⚠️ 全部做过反向验证：把改动退回旧写法，断言必须变红（见 _rv_auto_renew.py）。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

FAILS = []
NOW = time.time()
DAY = 86400.0


def chk(name, cond, extra=""):
    if cond:
        print(f"  [ok] {name}")
    else:
        print(f"  [FAIL] {name} {extra}")
        FAILS.append(name)


import bili_expiry_alert as EA
import webui


# ─────────────────────────────────────────── 1. Gate 状态机
def test_gate():
    g = EA.AutoRefreshGate(now_fn=lambda: NOW)
    chk("gate: 健康档不触发", g.evaluate(EA.LEVEL_OK, NOW) is False)
    chk("gate: 首次进入 UI 档触发一次", g.evaluate(EA.LEVEL_UI, NOW) is True)
    chk("gate: 同周期不重复", g.evaluate(EA.LEVEL_UI, NOW) is False)
    chk("gate: 升到 QQ 档也不再触发", g.evaluate(EA.LEVEL_QQ, NOW) is False)
    g.mark_done()
    chk("gate: mark_done 后 done=True", g.done is True)
    chk("gate: mark_done 后仍不触发", g.evaluate(EA.LEVEL_QQ, NOW) is False)
    chk("gate: 回到健康档重置", (g.evaluate(EA.LEVEL_OK, NOW) is False
                                 and g.done is False))
    chk("gate: 下个周期可再触发", g.evaluate(EA.LEVEL_UI, NOW) is True)

    g2 = EA.AutoRefreshGate(now_fn=lambda: NOW)
    chk("gate: 失败前先触发", g2.evaluate(EA.LEVEL_UI, NOW) is True)
    g2.mark_failed(NOW)
    chk("gate: 失败后 600s 内不重试",
        g2.evaluate(EA.LEVEL_UI, NOW + 599) is False)
    chk("gate: 失败后 600s 给一次机会",
        g2.evaluate(EA.LEVEL_UI, NOW + 601) is True)


# ─────────────────────────────────────────── 2. _auto_renew_tick
def _reset_tick():
    webui._auto_renew["alert"] = EA.ExpiryAlert()
    webui._auto_renew["gate"] = EA.AutoRefreshGate(now_fn=lambda: NOW)
    webui._auto_renew["last"] = ""


def _cfg(ts, exp, cookie="SESSDATA=abcdefghijklmn; bili_jct=x"):
    return {"bili_cookie": cookie,
            "bili_cookie_ts": ts,
            "bili_cookie_expires": exp}


def test_tick():
    _reset_tick()
    r = webui._auto_renew_tick(_cfg(NOW, NOW + 5 * DAY), NOW)
    chk("tick: 健康档不触发", r["fired"] is False and r["level"] == EA.LEVEL_OK)

    _reset_tick()
    r = webui._auto_renew_tick(_cfg(NOW - 5 * DAY, NOW + 2 * DAY), NOW)
    chk("tick: 第 5 天 UI 档触发一次",
        r["fired"] is True and r["level"] == EA.LEVEL_UI,
        str(r))

    _reset_tick()
    r = webui._auto_renew_tick(_cfg(NOW - 6 * DAY, NOW + DAY), NOW)
    chk("tick: 第 6 天 QQ 档触发一次",
        r["fired"] is True and r["level"] == EA.LEVEL_QQ,
        str(r))

    r2 = webui._auto_renew_tick(_cfg(NOW - 6 * DAY, NOW + DAY), NOW)
    chk("tick: 同一时刻不会连开两个续期", r2["fired"] is False)

    _reset_tick()
    r = webui._auto_renew_tick(_cfg(NOW - 6 * DAY, NOW + DAY, "bili_jct=x"), NOW)
    chk("tick: 没登录不触发", r["fired"] is False and r["reason"] == "未登录")


# ─────────────────────────────── 3. _run_refresh 的真实写盘结果
def _run_refresh_case(changed, via="browser"):
    """真调 _run_refresh，把依赖换成假的，检查 save_cfg 收到的内容。"""
    T0 = NOW - 6 * DAY
    base = _cfg(T0, T0 + 7 * DAY)
    saved = {}
    calls = []
    new_cs = "SESSDATA=NEWNEWNEWNEWNEW; bili_jct=y; DedeUserID=123"
    res = {"ok": True, "cookie": new_cs, "expires": 0.0,
           "via": via, "changed": changed}

    fakes = {
        "load_cfg": lambda: dict(base),
        "save_cfg": lambda c: saved.update(c),
        "_bili_login_state": lambda cs, acc: ("logged", ""),
    }
    orig = {k: getattr(webui, k) for k in ("load_cfg", "save_cfg",
                                           "run_async", "_bili_login_state")}

    def fake_run_async(co):
        co.close()                       # 别留"协程未 await"的警告
        calls.append(co)
        return res if len(calls) == 1 else {"ok": True, "uname": "孜岚"}

    audit_orig = webui.AUDIT.log
    try:
        webui.load_cfg = fakes["load_cfg"]
        webui.save_cfg = fakes["save_cfg"]
        webui.run_async = fake_run_async
        webui._bili_login_state = fakes["_bili_login_state"]
        webui.AUDIT.log = lambda *a, **kw: None
        webui._brefresh.update(epoch=777, running=False, done=False,
                               ok=False, msg="", stage="", changed=False)
        webui._run_refresh(777)
    finally:
        webui.load_cfg = orig["load_cfg"]
        webui.save_cfg = orig["save_cfg"]
        webui.run_async = orig["run_async"]
        webui._bili_login_state = orig["_bili_login_state"]
        webui.AUDIT.log = audit_orig
    return saved, dict(webui._brefresh), T0


def test_refresh_ts():
    s, st, T0 = _run_refresh_case(True)
    chk("续期: 换到新 cookie → ts 从这一刻重算",
        s.get("bili_cookie_ts", 0) > T0, str(s.get("bili_cookie_ts")))
    chk("续期: 换到新 cookie → 文案说重新计算",
        "重新计算" in st.get("msg", ""), st.get("msg", ""))
    chk("续期: changed 回显 True", st.get("changed") is True)

    # 换到新 cookie 之后，到期状态应回到健康档（不再提醒）
    _reset_tick()
    r = webui._auto_renew_tick(s, NOW + 60)
    chk("续期: 真换到后回到健康档，不再触发",
        r["fired"] is False and r["level"] == EA.LEVEL_OK, str(r))

    s2, st2, T0b = _run_refresh_case(False)
    chk("续期: 没换到 → ts 保持原登录时刻",
        s2.get("bili_cookie_ts") == T0b,
        "%r != %r" % (s2.get("bili_cookie_ts"), T0b))
    chk("续期: 没换到 → expires 不被重算",
        s2.get("bili_cookie_expires") == T0b + 7 * DAY,
        str(s2.get("bili_cookie_expires")))
    chk("续期: 没换到 → 明确说提醒不会延后",
        "不会延后" in st2.get("msg", ""), st2.get("msg", ""))
    chk("续期: 没换到 → 不再谎报「有效期已重新计算」",
        "有效期已重新计算" not in st2.get("msg", ""), st2.get("msg", ""))
    chk("续期: changed 回显 False", st2.get("changed") is False)

    # 关键联动：旧写法把 ts 推到 now 之后，这里会被误判成健康档
    _reset_tick()
    r2 = webui._auto_renew_tick(s2, NOW + 60)
    chk("续期: 没换到 → 到期档位不后移（仍会提醒）",
        r2["fired"] is True and r2["level"] != EA.LEVEL_OK, str(r2))


# ─────────────────────────────── 4. 自动续期真的挂上、真的会跑
def test_thread_wired():
    import threading
    orig = threading.Thread
    made = {}

    class FakeThread:
        def __init__(self, target=None, daemon=False):
            made["target"] = target
            made["daemon"] = daemon
            made["started"] = False

        def start(self):
            made["started"] = True

    try:
        threading.Thread = FakeThread
        webui._start_auto_renew_thread()
    finally:
        threading.Thread = orig
    chk("线程: 目标就是 _auto_renew_loop",
        made.get("target") is webui._auto_renew_loop)
    chk("线程: daemon=True", made.get("daemon") is True)
    chk("线程: start() 真被调用", made.get("started") is True)


def test_loop_runs_refresh():
    """跑一轮 loop：提醒触发时真的去拉起 _start_refresh。"""
    calls = []
    orig = {}
    for k in ("load_cfg", "_auto_renew_tick", "_start_refresh", "sleep"):
        obj = time if k == "sleep" else webui
        orig[k] = getattr(obj, k)

    class Stop(BaseException):
        pass

    def stop_sleep(_):
        raise Stop()

    try:
        webui.load_cfg = lambda: {}
        webui._auto_renew_tick = lambda cfg: {"fired": True, "level": EA.LEVEL_UI}
        webui._start_refresh = lambda ip, reason: (
            calls.append((ip, reason)) or {"ok": True, "started": True})
        time.sleep = stop_sleep
        try:
            webui._auto_renew_loop()
        except Stop:
            pass
    finally:
        webui.load_cfg = orig["load_cfg"]
        webui._auto_renew_tick = orig["_auto_renew_tick"]
        webui._start_refresh = orig["_start_refresh"]
        time.sleep = orig["sleep"]
    chk("loop: 提醒触发时真的调了 _start_refresh", len(calls) == 1, str(calls))
    chk("loop: 标注为自动触发", bool(calls) and "auto:" in calls[0][1], str(calls))
    chk("loop: 成功后本周期记账", webui._auto_renew["gate"].done is True)


def test_main_wired():
    """线程真的被 main() 拉起，而不是只定义了函数没人调。

    ⚠️ 只测 _start_auto_renew_thread 本身会漏掉「忘了在 main 里调用」——
       那正是之前几轮『代码写了、包里也有、真机就是不生效』的同类坑。
       这里用 AST 看 main() 里的调用顺序，不靠子串匹配。
    """
    import ast
    src = open(os.path.join(SRC, "webui.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    chk("webui.main 存在", fn is not None)
    if fn is not None:
        calls = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                f = n.func
                name = (f.attr if isinstance(f, ast.Attribute)
                        else f.id if isinstance(f, ast.Name) else "")
                calls.append(name)
        chk("main() 真的调用了 _start_auto_renew_thread",
            "_start_auto_renew_thread" in calls, str(calls[-12:]))
        if "_start_auto_renew_thread" in calls and "run" in calls:
            chk("自动续期线程在 app.run 之前拉起",
                calls.index("_start_auto_renew_thread") < calls.index("run"))


def test_status_exposes_auto():
    """状态接口把自动续期结果带出来，用户在面板上能看到。"""
    webui._auto_renew["last"] = "到期提醒触发，已自动开始续期"
    try:
        with webui.app.test_request_context("/api/bili/refresh/status"):
            d = webui.api_bili_refresh_status().get_json()
        chk("status: 回显 auto 字段",
            d.get("auto") == "到期提醒触发，已自动开始续期", str(d.get("auto")))
    finally:
        webui._auto_renew["last"] = ""


def main():
    print("== 1. AutoRefreshGate ==")
    test_gate()
    print("== 2. _auto_renew_tick ==")
    test_tick()
    print("== 3. _run_refresh 真实写盘 ==")
    test_refresh_ts()
    print("== 4. 自动续期挂上与执行 ==")
    test_thread_wired()
    test_main_wired()
    test_loop_runs_refresh()
    test_status_exposes_auto()
    print()
    if FAILS:
        print("[FAIL] %d 项：%s" % (len(FAILS), "; ".join(FAILS)))
        return 1
    print("[PASS] 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
