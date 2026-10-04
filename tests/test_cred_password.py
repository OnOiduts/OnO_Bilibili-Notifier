#!/usr/bin/env python3
"""凭据板块「访问口令」能不能真的设置 / 保存 / 清除。

背景（用户反馈）：
    凭据 → 安全 那块的安全功能完全用不了 —— 点了「💾 设置口令」
    报错或没反应，也存不下来。

本测试用 Flask 测试客户端**真发请求**来验，不靠读代码猜。
每段都先 reset_cfg()，场景之间互不污染。
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
sys.path.insert(0, SRC)

HOME = tempfile.mkdtemp(prefix="t_pwd_")
os.environ["BILI_NOTIFY_HOME"] = HOME

import webui  # noqa: E402

FAILS = []
PASSES = []

HDR = {"X-Requested-With": "bili-notify"}


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILS.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name} :: {detail}")


def reset_cfg():
    """清空配置，保证每段从「未设口令」开始。"""
    try:
        os.remove(webui.CFG_PATH)
    except OSError:
        pass
    webui.save_cfg({})


def call(client, payload):
    return client.post("/api/security/password", json=payload, headers=HDR)


def main():
    print("== 凭据板块：访问口令 ==")

    # ── 1. 前端 set-pwd 按钮实际发的字段 ──────────────────────────
    # app.js:  post('/api/security/password', { password: v })
    reset_cfg()
    c = webui.app.test_client()
    d = call(c, {"password": "abc123456"}).get_json()
    check("set-pwd 按钮的写法能设置成功", bool(d and d.get("ok")), f"返回 {d}")

    cfg = webui.load_cfg()
    pw = str(cfg.get("web_password") or "")
    check("口令已落盘", bool(pw), f"web_password={pw[:12]!r}")
    check("存的是哈希不是明文",
          pw.startswith("pbkdf2$") and "abc123456" not in pw,
          f"实际 {pw[:24]!r}")

    # ── 2. 用这个口令能登录 ───────────────────────────────────────
    d2 = webui.app.test_client().post(
        "/api/login", json={"password": "abc123456"}, headers=HDR).get_json()
    check("设置后能用该口令登录", bool(d2 and d2.get("ok")), f"返回 {d2}")

    # ── 3. 前端 clear-pwd 按钮实际发的字段 ────────────────────────
    # app.js:  post('/api/security/password', { password: '', clear: true })
    # 用上面那个 client（带着登录 cookie）
    reset_cfg()
    c3 = webui.app.test_client()
    call(c3, {"action": "set", "new": "temp123456"})     # 先设上
    d3 = call(c3, {"password": "", "clear": True}).get_json()
    check("clear-pwd 按钮的写法能清除", bool(d3 and d3.get("ok")), f"返回 {d3}")
    check("清除后配置里没有口令",
          not str(webui.load_cfg().get("web_password") or ""),
          f"残留 {str(webui.load_cfg().get('web_password'))[:12]!r}")

    # ── 4. 后端文档化的写法仍要兼容 ───────────────────────────────
    reset_cfg()
    c4 = webui.app.test_client()
    d4 = call(c4, {"action": "set", "new": "newpass123"}).get_json()
    check("{action:set, new:...} 写法仍可用", bool(d4 and d4.get("ok")), f"返回 {d4}")
    d5 = call(c4, {"action": "clear"}).get_json()
    check("{action:clear} 写法仍可用", bool(d5 and d5.get("ok")), f"返回 {d5}")

    # ── 5. 太短的口令必须拒绝 ─────────────────────────────────────
    reset_cfg()
    d6 = call(webui.app.test_client(), {"password": "123"}).get_json()
    check("少于 6 位被拒绝", not (d6 and d6.get("ok")), f"返回 {d6}")
    check("短口令没被写进配置",
          not str(webui.load_cfg().get("web_password") or ""), "居然写进去了")

    # ── 6. 空提交不能把已有口令清掉 ───────────────────────────────
    reset_cfg()
    c7 = webui.app.test_client()
    call(c7, {"action": "set", "new": "keepme123"})
    d7 = call(c7, {"password": ""}).get_json()
    check("空口令提交被拒绝而不是清掉",
          not (d7 and d7.get("ok")), f"返回 {d7}")
    check("已有口令仍在",
          bool(str(webui.load_cfg().get("web_password") or "")), "口令被清掉了")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(HOME, ignore_errors=True)
    print()
    print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
    for f in FAILS:
        print("  ✗", f)
    sys.exit(1 if FAILS else 0)
