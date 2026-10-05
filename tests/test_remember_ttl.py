# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""「记住登录状态」必须真的有区别。

背景（一个真实踩过的坑）：以前 remember 只用来设 cookie 的 max_age，
令牌本身始终按默认 7 天签发。于是「不勾记住」只是让 cookie 变成会话级，
而浏览器（Chrome 的「继续上次的会话」）会把会话 cookie 一并恢复出来，
令牌又是 7 天有效 —— 勾不勾一个样。

所以这里盯两点：
  1. 令牌寿命必须随 remember 变化（勾=7 天，不勾=短）；
  2. 不勾时要有会话标记 cookie，供 after_request 滑动续期。
"""
import os
import sys
import time
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
sys.path.insert(0, _SRC)
sys.path.insert(0, _HERE)

ONOBN = "ONOBN_DATA_DIR"
_old = os.environ.get(ONOBN)
_tmp = tempfile.mkdtemp(prefix="onobn_remember_")
os.environ[ONOBN] = _tmp

import security                                    # noqa: E402
import webui                                       # noqa: E402
from webui import app                              # noqa: E402

app.config["TESTING"] = True
_H = {"X-Requested-With": "bili-notify"}
_PWD = "Pwd123456"


def _set_password():
    """每个用例独立数据目录，避免互相污染（这是之前踩过的坑）。"""
    d = tempfile.mkdtemp(prefix="onobn_remember_")
    os.environ[ONOBN] = d
    import importlib
    importlib.reload(security)
    importlib.reload(webui)
    c = app.test_client()
    c.post("/api/security/password", json={"password": _PWD}, headers=_H)
    return c


def _ttl_of(resp):
    for h in resp.headers.getlist("Set-Cookie"):
        if h.startswith("bn_auth="):
            tok = h.split("=", 1)[1].split(";")[0]
            return int(tok.split(":")[1]) - int(time.time())
    return None


def test_issue_cookie_passes_ttl():
    """_issue_login_cookie 必须把 ttl 传进令牌，而不是用默认的 7 天。"""
    src = open(os.path.join(_SRC, "webui.py"), encoding="utf-8").read()
    i = src.index("def _issue_login_cookie")
    body = src[i:i + 1600]
    assert "ttl=" in body, "令牌没有传 ttl，remember 就只是个摆设"


def test_remember_false_is_short():
    c = _set_password()
    r = c.post("/api/login", json={"password": _PWD, "remember": False},
               headers=_H)
    ttl = _ttl_of(r)
    assert ttl is not None, "没拿到登录 cookie"
    assert ttl <= 3600, f"不勾记住却拿了 {ttl} 秒，等于还是 7 天"


def test_remember_true_is_seven_days():
    c = _set_password()
    r = c.post("/api/login", json={"password": _PWD, "remember": True},
               headers=_H)
    ttl = _ttl_of(r)
    assert ttl is not None
    assert ttl > 86400, f"勾了记住却只有 {ttl} 秒"


def test_two_options_differ():
    c = _set_password()
    a = _ttl_of(c.post("/api/login", json={"password": _PWD, "remember": False},
                       headers=_H))
    b = _ttl_of(c.post("/api/login", json={"password": _PWD, "remember": True},
                       headers=_H))
    assert a and b and b > a * 10, f"勾与不勾差别太小：{a} vs {b}"


def test_session_marker_cookie():
    """不勾记住时要下发会话标记 cookie，滑动续期靠它识别。"""
    c = _set_password()
    r = c.post("/api/login", json={"password": _PWD, "remember": False},
               headers=_H)
    assert any(h.startswith("onobn_sess=")
               for h in r.headers.getlist("Set-Cookie")), "缺少会话标记 cookie"

    r2 = c.post("/api/login", json={"password": _PWD, "remember": True},
                headers=_H)
    sess = [h for h in r2.headers.getlist("Set-Cookie")
            if h.startswith("onobn_sess=")]
    assert not sess or "Max-Age=0" in sess[0], "勾了记住就不该留会话标记"


def test_expired_token_rejected():
    """令牌过期后必须失效 —— 这是『不记住』能生效的根。"""
    c = _set_password()
    tok = security.issue_token(app.secret_key, ttl=-60,
                               gen=webui.get_auth_gen())
    assert security.verify_token(app.secret_key, tok,
                                 gen=webui.get_auth_gen()) is False, \
        "过期令牌竟然还验得过"


def _main():
    fns = [(n, o) for n, o in sorted(globals().items())
           if n.startswith("test_") and callable(o)]
    bad = 0
    for n, fn in fns:
        try:
            fn()
            print(f"  ✓ {n}")
        except Exception as e:
            bad += 1
            print(f"  ✗ {n}: {e}")
    print(f"\n{len(fns) - bad}/{len(fns)} 通过")
    if _old is None:
        os.environ.pop(ONOBN, None)
    else:
        os.environ[ONOBN] = _old
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_main())
