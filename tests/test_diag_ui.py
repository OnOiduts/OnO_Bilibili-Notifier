# -*- coding: utf-8 -*-
"""v1.31.18 诊断页三块（媒体缓存 / 运行日志 / 运行状态）的回归测试。

覆盖用户这一轮报的问题：
  1. 媒体缓存「没有数据存储」→ 必须给出真实缓存位置与长期/临时拆分
  2. 「清空缓存弹出请先登录」→ 没设面板口令时不该要求登录
  3. 「缺少测试上传」→ 要有能真验证富媒体链路的入口
  4. 日志「中文翻译」关掉时整页空白 → 关翻译只是不翻，不是不显示
  5. 「只看问题」是假开关 → 改成按钮，点了要亮
  6. 日志概览行可点 → 跳到对应设置并高亮
  7. 运行状态「太单调」→ 必须渲染真实参数（进程/轮询/推送/心跳/面板）
"""
import io
import os
import sys

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import heartbeat  # noqa: E402
import webui  # noqa: E402

FAILS = []


def ck(name, cond):
    if cond:
        print("  [PASS] %s" % name)
    else:
        print("  [FAIL] %s" % name)
        FAILS.append(name)


def _read(*path):
    return io.open(os.path.join(BASE_DIR, *path), encoding="utf-8").read()


# ─────────────────────────────────────────────
def t_auth():
    """没设面板口令时，写操作不该被 authed() 拦下。"""
    print("\n1) 未设口令时的写操作鉴权")
    src = _read("webui.py")
    ck("有 _auth_ok 兜底函数", "def _auth_ok()" in src)
    ck("_auth_ok 免登录时放行", "if not need_auth():\n        return True" in src)
    # 端点里不该再出现裸的 authed() 守卫（定义处除外）
    body = src.split("def _auth_ok()", 1)[1]
    n = body.count("if not authed():")
    ck("端点里没有裸 authed 守卫（实际 %d 处）" % n, n == 0)
    ck("清空缓存改用 _auth_ok",
       src.split("def api_media_clear")[1][:300].count("_auth_ok") >= 1)


def t_media_api():
    """媒体缓存接口必须给真实数据，而不是只有 0。"""
    print("\n2) 媒体缓存接口")
    webui.app.config["TESTING"] = True
    c = webui.app.test_client()
    r = c.get("/api/media")
    ck("/api/media 可用", r.status_code == 200)
    d = r.get_json() or {}
    for k in ("dir", "long_count", "tmp_count", "long_size", "tmp_size",
              "mode", "ttl_text"):
        ck("返回字段 %s" % k, k in d)
    ck("给了真实缓存路径", bool(str(d.get("dir") or ""))
       and "images" in str(d.get("dir") or ""))
    ck("计数是数字", isinstance(d.get("count"), int)
       and isinstance(d.get("long_count"), int))
    # 没设口令时清空缓存不该要登录
    webui.need_auth = lambda: False
    r2 = c.post("/api/media/clear", headers={"X-Requested-With": "bili-notify"})
    body = (r2.get_json() or {}).get("msg", "")
    ck("未设口令时清空不被拦（msg=%s）" % body[:30], "请先登录" not in body)


def t_test_upload():
    """测试上传：接口存在，且缺凭据/缺沙盒群时给明确提示。"""
    print("\n3) 测试上传接口")
    src = _read("webui.py")
    ck("有 /api/media/test-upload 路由",
       "/api/media/test-upload" in src)
    ck("走完上传再发送", "upload_group_file" in src and "send_media" in src)
    ck("缺凭据时明确提示", "还没填好 AppID / AppSecret" in src)
    ck("缺沙盒群时明确提示", "还没设置沙盒群" in src)
    html = _read("templates", "index.html")
    ck("页面有测试上传按钮", 'data-act="media-test-upload"' in html)
    js = _read("static", "app.js")
    ck("前端有对应动作", "'media-test-upload'" in js)


def t_heartbeat_daily():
    """当日推送/错误计数要跨天归零。"""
    print("\n4) 当日计数")
    ck("有当日推送字段", "pushes_today" in heartbeat._state)
    ck("有当日错误字段", "errors_today" in heartbeat._state)
    ck("有跨天归零函数", "def _rollover_day()" in _read("heartbeat.py"))
    heartbeat._state["day"] = "2000-01-01"
    heartbeat._state["pushes_today"] = 99
    heartbeat._state["errors_today"] = 88
    heartbeat._rollover_day()
    ck("跨天后 pushes_today 归零", heartbeat._state["pushes_today"] == 0)
    ck("跨天后 errors_today 归零", heartbeat._state["errors_today"] == 0)
    heartbeat.bump("pushes")
    ck("累计推送时当日也加", heartbeat._state["pushes_today"] == 1)


def t_log_translate_off():
    """关掉中文翻译时，日志不能整页空白。"""
    print("\n5) 关闭翻译仍要显示")
    src = _read("webui.py")
    seg = src.split("def api_log", 1)[1].split("@app.route", 1)[0]
    ck("关闭翻译时不再返回空 items", '"items": []' not in seg)
    # 实跑一次
    webui.app.config["TESTING"] = True
    old = webui.load_cfg

    def _cfg():
        d = dict(old())
        d["log_translate"] = False
        return d
    # 造一行带时间戳的日志：这正是以前必崩的那种行
    # （explain() 的 date/time 是顶层字符串，旧代码却当字典取）。
    probe = os.path.join(BASE_DIR, "probe_tmp.log")
    io.open(probe, "w", encoding="utf-8").write(
        "2026-09-21 10:11:12 [INFO] (bot.py:100)test 探针日志一行\n")
    webui.load_cfg = _cfg
    try:
        c = webui.app.test_client()
        r = c.get("/api/log?limit=50")
        d = r.get_json() or {}
        ck("接口可用（不再 500）", r.status_code == 200)
        ck("translated=False", d.get("translated") is False)
        ck("有时间戳的日志也能解析（%d 条）" % len(d.get("items") or []),
           len(d.get("items") or []) > 0)
    finally:
        webui.load_cfg = old
        try:
            os.remove(probe)
        except OSError:
            pass


def t_log_ui():
    """日志页：只看问题是按钮，概览行可点。"""
    print("\n6) 日志页交互")
    html = _read("templates", "index.html")
    js = _read("static", "app.js")
    ck("只看问题改成按钮", 'data-act="toggle-log-err"' in html)
    ck("移除只看问题 checkbox", 'id="logErrOnly"' not in html)
    ck("移除日志页翻译开关", 'id="logTrans"' not in html)
    ck("有 toggle-log-err 动作", "'toggle-log-err'" in js)
    ck("按钮有亮起样式", ".btn.on" in html)
    ck("概览行有可点样式", ".log-sum-row { cursor:pointer" in html)
    ck("有通用跳卡片动作", "'goto-card'" in js)
    ck("有日志分类跳转表", "var PART_GO" in js)
    ck("跳转表覆盖 B站接口", "'B站接口'" in js)
    ck("日志页不再绑定已移除的开关", "logTrans').checked" not in js)


def t_run_state():
    """运行状态页必须渲染真实参数。"""
    print("\n7) 运行状态")
    webui.app.config["TESTING"] = True
    c = webui.app.test_client()
    r = c.get("/api/runstate")
    ck("/api/runstate 可用", r.status_code == 200)
    d = r.get_json() or {}
    for k in ("proc", "poll", "pushes_today", "heartbeat", "panel",
              "bot_running"):
        ck("返回字段 %s" % k, k in d)
    ck("轮询带上下限", "min" in (d.get("poll") or {})
       and "max" in (d.get("poll") or {}))
    ck("面板地址取真实请求",
       bool(str((d.get("panel") or {}).get("addr") or "")))

    js = _read("static", "app.js")
    html = _read("templates", "index.html")
    ck("前端有 loadRun", "async function loadRun()" in js)
    ck("前端有 renderRun", "function renderRun()" in js)
    ck("打开诊断页时加载", "loadRun()" in js)
    ck("定时刷新运行状态", "safe('run', loadRun)" in js)
    ck("有运行状态行样式", ".runrow" in html)
    ck("runBox 仍在页面上", 'id="runBox"' in html)
    # bot.py 每轮累计轮次
    bot = _read("bot.py")
    ck("每轮累计轮询次数", 'heartbeat.bump("polls")' in bot)


def main():
    print("=" * 60)
    print("v1.31.18 诊断页回归测试")
    print("=" * 60)
    t_auth()
    t_media_api()
    t_test_upload()
    t_heartbeat_daily()
    t_log_translate_off()
    t_log_ui()
    t_run_state()
    print("\n" + "=" * 60)
    if FAILS:
        print("失败 %d 项：" % len(FAILS))
        for f in FAILS:
            print("  - %s" % f)
        sys.exit(1)
    print("全部通过 OK")


if __name__ == "__main__":
    main()
