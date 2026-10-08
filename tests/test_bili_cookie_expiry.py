# -*- coding: utf-8 -*-
"""B站 cookie 的「还剩多少天」必须是真值，不能是写死的 30 天估算。

背景（v2.3.4 之后）：
    面板一直显示「还剩 29 天」，重新登录也没用。根因是过期时间被写死：

        webui.py 手动保存处   cfg["bili_cookie_expires"] = now + 30*86400

    30 天是硬编码，所以刚登完显示 30、过一天 29 —— 看着像在走，其实
    B站 真正给的有效期根本没参与计算。后果不只是数字难看：到期提醒的
    「剩 2~5 天」「剩 <2 天」两个区间永远进不去，提醒整个失效。

    其实 SESSDATA 自己就带着过期时间：`<签名>,<过期时间戳>,<校验>*<序号>`
    （存进 cookie 时逗号被编码成 %2C）。所以**已经登录着的用户不用重新
    登录**也能立刻纠正过来 —— 这正是本文件要锁住的东西。
"""
import os
import re
import sys
import time

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE)

FAILS = []


def chk(name, cond, extra=""):
    if cond:
        print(f"  [ok] {name}")
    else:
        print(f"  [FAIL] {name} {extra}")
        FAILS.append(name)


def _src(name):
    return open(os.path.join(BASE, name), encoding="utf-8").read()


# 一个三天后到期的 SESSDATA（相对 now 构造，永远不过期/不写死）
def _sess(days_ahead=3):
    ts = int(time.time()) + days_ahead * 86400
    return f"SESSDATA=cb0e6c8c%2C{ts}%2Cb7f9f%2A41; bili_jct=deadbeef"


# ------------------------------------------------------------ 解析本身
def test_sessdata_expires_parses():
    import bili_login as b
    ts = int(time.time()) + 3 * 86400
    for s in (f"SESSDATA=a%2C{ts}%2Cc*1",           # URL 编码（真实形态）
              f"SESSDATA=a,{ts},c*1",                # 明文逗号
              f"SESSDATA=a%252C{ts}%252Cc*1"):       # 双重编码
        v = b.sessdata_expires(s)
        chk(f"能解出过期时间：{s[:34]}", abs(v - ts) < 2, f"得到 {v}")
    # 跟随其它 cookie 一起出现也不能解错
    chk("混在整串 cookie 里也认得",
        abs(b.sessdata_expires(_sess(3)) - ts) < 2)


def test_sessdata_expires_rejects():
    import bili_login as b
    for s in ("", "bili_jct=abc", "SESSDATA=", "SESSDATA=xxx",
              "SESSDATA=%2C%2C", "SESSDATA=你的SESSDATA"):
        chk(f"解不出就返回 0（不瞎猜）：{s[:28]!r}", b.sessdata_expires(s) == 0.0)


def test_effective_expires_priority():
    import bili_login as b
    real = b.sessdata_expires(_sess(3))
    # 1) 有真值就用真值，哪怕配置里存着别的值
    exp, est = b.effective_expires(_sess(3), time.time() + 30 * 86400)
    chk("真值优先于配置里的旧值", abs(exp - real) < 2 and est is False)
    # 2) 解不出才用配置里那个（并标明是估算）
    saved = time.time() + 10 * 86400
    exp, est = b.effective_expires("SESSDATA=xxx", saved)
    chk("解不出时退回已记录值并标估算", abs(exp - saved) < 2 and est is True)
    # 3) 都没有才按 30 天估
    exp, est = b.effective_expires("SESSDATA=xxx", 0)
    chk("都没有时按 30 天估算且标记", est is True and exp > time.time())


# ------------------------------------------------------------ 两处硬编码
def test_no_hardcoded_30d_on_save():
    """手动保存 cookie 不能再写死 30 天。"""
    w = _src("webui.py")
    chk("手动保存处不再写 time.time() + 30 天",
        not re.search(r"bili_cookie_expires\"\]\s*=\s*time\.time\(\)\s*\+\s*"
                      r"bili_login\.DEFAULT_TTL_DAYS", w))
    chk("手动保存处改用 effective_expires",
        "effective_expires" in w)
    # 浏览器登录那条也不许再兜底写死 30 天
    chk("浏览器登录处不再兜底写 time.time() + 30*86400",
        not re.search(r"res\.get\(\"expires\"\)\s*or\s*\(time\.time\(\)\s*\+\s*30", w))


def test_api_returns_estimated_flag():
    """估算值必须标出来，否则『还剩 29 天』会被当成准信儿。"""
    w = _src("webui.py")
    chk("接口返回 expires_estimated", "expires_estimated" in w)
    chk("估算时文案带（估算）", "（估算）" in w)


def test_bot_and_check_use_real():
    for f in ("bot.py", "check.py"):
        s = _src(f)
        chk(f"{f} 用 effective_expires 而不是只读配置",
            "effective_expires" in s)


# ------------------------------------------------------------ 端到端
def test_api_reports_real_days(tmp_path, monkeypatch=None):
    """已登录用户不重新登录，接口就该报出真实剩余天数（并回写配置）。"""
    monkeypatch.setenv("ONOBN_DATA_DIR", str(tmp_path))
    sys.path.insert(0, BASE)
    import importlib
    import cfgutil
    import webui
    for m in ("cfgutil", "paths", "webui", "bili_login"):
        try:
            importlib.reload(sys.modules[m])
        except Exception:
            pass

    # 配置里故意留一个旧的 30 天估算 —— 正是用户现在的状态
    cfg = cfgutil.load_cfg() or {}
    cfg["bili_cookie"] = _sess(3)
    cfg["bili_cookie_ts"] = time.time()
    cfg["bili_cookie_expires"] = time.time() + 30 * 86400
    cfgutil.save_cfg(cfg)

    try:
        webui.app.config["TESTING"] = True
        cl = webui.app.test_client()
        r = cl.get("/api/bili/cookie")
        d = r.get_json() or {}
    except Exception as e:
        chk("接口能正常返回", False, str(e))
        return

    chk("接口返回 200", r.status_code == 200, str(r.status_code))
    days = d.get("days_left")
    chk("报出真实剩余天数（不是 29/30）",
        days is not None and 2 <= days <= 4, f"days_left={days}")
    chk("标记为非估算", d.get("expires_estimated") is False,
        f"estimated={d.get('expires_estimated')}")

    after = cfgutil.load_cfg() or {}
    chk("真值已回写进配置",
        abs(float(after.get("bili_cookie_expires") or 0)
            - float(cfg["bili_cookie_expires"])) > 3600)


class _MP(object):
    """脱离 pytest 跑 main() 时用的最小替身。"""
    def setenv(self, k, v):
        os.environ[k] = v

    def delenv(self, k, raising=True):
        os.environ.pop(k, None)


def main():
    test_sessdata_expires_parses()
    test_sessdata_expires_rejects()
    test_effective_expires_priority()
    test_no_hardcoded_30d_on_save()
    test_api_returns_estimated_flag()
    test_bot_and_check_use_real()
    import tempfile
    td = tempfile.mkdtemp(prefix="onobn_exp_")
    try:
        test_api_reports_real_days(td, _MP())
    except Exception as e:
        chk("端到端能跑起来", False, str(e))
    print(f"\n通过 {len(FAILS)==0}  失败 {len(FAILS)}")
    for f in FAILS:
        print("   ✗", f)


if __name__ == "__main__":
    main()
