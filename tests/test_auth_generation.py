# -*- coding: utf-8 -*-
"""令牌世代号：设置/修改/清除口令与登出之后，旧会话必须立即失效。

为什么要锁
----------
签名密钥是**持久化**的（security.get_secret_key() 写进 .webui_secret 文件，
否则每次重启都被登出），而登录令牌有效期 7 天。两者叠加的后果是：

    令牌一旦签发，服务端就再也作废不掉。

这造成过一个真实漏洞：设置口令的接口以前无条件下发令牌，用户设完口令白拿
一个 7 天会话。后来改成「首次设置不下发令牌」，但**已经发出去的那些照样有效**
——表现成「设完口令再打开还是不弹登录页，换什么地址都能进」，而服务端毫无办法。

修法是把世代号编进令牌（admin:{exp}:{gen}:{boot}:{nonce}:{sig}，其中 boot
是进程世代号 —— 关掉程序再开就变，见 test_session_boot_lock.py）：
设置/修改/清除口令、登出时 +1，此后旧令牌因世代号对不上而全部失效。
附带好处：旧格式令牌（4 段）天然失效，老用户升级后必然重新登录一次。

⚠️ 本文件末尾有反向验证：把任意一处改回旧写法，对应断言必须变红。
"""
import io
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")


def _sec_src() -> str:
    with io.open(os.path.join(_SRC, "security.py"), encoding="utf-8") as f:
        return f.read()


def _webui_src() -> str:
    with io.open(os.path.join(_SRC, "webui.py"), encoding="utf-8") as f:
        return f.read()


def _login_html() -> str:
    with io.open(os.path.join(_SRC, "templates", "login.html"),
                 encoding="utf-8") as f:
        return f.read()


def _strip_comments(s: str) -> str:
    """去掉注释行。

    ⚠️ 这一步是吃过亏才加的：断言若直接在整个文件里找字符串，
       注释里提到的旧写法会把断言顶住 —— 代码改坏了测试照样绿。
    """
    out = []
    for line in s.splitlines():
        t = line.strip()
        if t.startswith("#") or t.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


# ─────────────────────────── 1. 令牌层 ───────────────────────────
def test_token_carries_generation():
    """令牌里必须编进世代号（6 段），否则服务端没法作废它。

    6 段 = admin:exp:gen:boot:nonce:sig —— 第 4 段是进程世代号，
    关掉程序再开它就会变，旧令牌因此全部失效。
    """
    sys.path.insert(0, _SRC)
    import security
    tok = security.issue_token("k", gen=3)
    assert len(tok.split(":")) == 6, "令牌应带世代号与进程号（6 段），实际 %r" % tok
    assert tok.split(":")[2] == "3"


def test_old_format_token_rejected():
    """旧格式令牌（4 段）一律失效 —— 老用户升级后必须重新登录一次。"""
    sys.path.insert(0, _SRC)
    import security
    old = "admin:%d:%s" % (9999999999, "0" * 64)   # 4 段：gen 之前签发
    assert security.verify_token("k", old, 0) is False
    assert security.verify_token("k", old, 3) is False


def test_generation_mismatch_rejected():
    """世代号对不上就拒绝 —— 这是「旧会话立即失效」的实现基础。"""
    sys.path.insert(0, _SRC)
    import security
    tok = security.issue_token("k", gen=1)
    assert security.verify_token("k", tok, 1) is True
    assert security.verify_token("k", tok, 2) is False


def test_bump_persists():
    """bump 必须落盘：只加内存里的数，重启后旧令牌又会活过来。"""
    sys.path.insert(0, _SRC)
    import security
    before = security.get_auth_gen()
    after = security.bump_auth_gen()
    assert after == before + 1
    assert security.get_auth_gen() == after


# ─────────────────────────── 2. 接线层 ───────────────────────────
def test_authed_passes_generation():
    """authed() 必须把世代号传给 verify_token。

    少了这一步，世代号机制等于没接 —— 旧令牌照样一路畅通。
    """
    code = _strip_comments(_webui_src())
    assert "verify_token(app.secret_key, request.cookies.get(COOKIE_NAME, \"\")," in code
    assert "get_auth_gen())" in code


def test_issue_passes_generation():
    """签发时必须带上当前世代号，否则新令牌自己就对不上。"""
    code = _strip_comments(_webui_src())
    # ⚠️ 别再写死完整调用串：登录接口后来加了 ttl= 参数，精确串对不上，
    # 这条断言就从"守着世代号"退化成"永远失败/永远假通过"。
    # 真正要保证的是：每一处签发都带 gen=（少一处就作废不掉旧令牌）。
    import re as _re
    calls = _re.findall(r"issue_token\((?:[^()]|\([^()]*\))*\)", code)
    assert calls, "没找到任何 issue_token 调用"
    for c in calls:
        assert "gen=" in c, "签发令牌必须带 gen=，实际 %r" % c


def test_password_change_bumps():
    """改口令必须 bump：否则改完旧会话（含其他设备）还能用。"""
    code = _strip_comments(_webui_src())
    assert "issue_token(app.secret_key, gen=bump_auth_gen())" in code


def test_first_set_bumps():
    """首次设置口令也要 bump —— 治的是「此前白送出去的那些令牌」。"""
    code = _strip_comments(_webui_src())
    i = code.find("if _was_open:")
    assert i > 0, "找不到首次设置分支"
    assert "bump_auth_gen()" in code[i:i + 600]


def test_logout_bumps():
    """登出要让所有已签发会话作废，而不只是清自己这一枚 cookie。"""
    code = _strip_comments(_webui_src())
    i = code.find("def logout():")
    assert i > 0
    assert "bump_auth_gen()" in code[i:i + 400]


def test_clear_bumps():
    """清除口令也要 bump。"""
    code = _strip_comments(_webui_src())
    i = code.find('if action == "clear":')
    assert i > 0
    assert "bump_auth_gen()" in code[i:i + 700]


# ───────────────────── 3. 端到端：旧会话真的进不去 ─────────────────────
def _e2e():
    import tempfile
    home = tempfile.mkdtemp(prefix="authgen_")
    env = dict(os.environ)
    env["BILI_NOTIFY_HOME"] = home
    env["PYTHONPATH"] = _SRC
    script = r'''
import json, sys
sys.path.insert(0, %r)
import webui
from security import issue_token, verify_token, get_auth_gen
app = webui.app
app.config["TESTING"] = True
H = {"X-Requested-With": "bili-notify"}
c = app.test_client()
out = {}

def tok_of(resp):
    sc = resp.headers.get("Set-Cookie", "")
    if "bn_auth=" not in sc:
        return ""
    return sc.split("bn_auth=")[1].split(";")[0]

# 先设口令（首次），再登录拿令牌
c.post("/api/security/password", json={"old": "", "new": "Secret123456"}, headers=H)
t1 = tok_of(c.post("/api/login", json={"password": "Secret123456"}, headers=H))
out["tok_segments"] = len(t1.split(":"))

# 另一个"浏览器"拿着这枚令牌
other = app.test_client()
other.set_cookie("bn_auth", t1, domain="localhost")
out["before_change"] = other.get("/").status_code

# 改口令
c.post("/api/security/password", json={"old": "Secret123456", "new": "NewPass654321"}, headers=H)
out["after_change"] = other.get("/").status_code

# 重新登录 -> 登出 -> 手里那枚应失效
t2 = tok_of(c.post("/api/login", json={"password": "NewPass654321"}, headers=H))
o2 = app.test_client(); o2.set_cookie("bn_auth", t2, domain="localhost")
out["before_logout"] = o2.get("/").status_code
c.get("/logout")
out["after_logout"] = o2.get("/").status_code

# 没设口令时，历史遗留的旧格式令牌也不能蒙混过关
out["need_auth_end"] = webui.need_auth()
print("@@" + json.dumps(out))
''' % _SRC
    p = subprocess.run([sys.executable, "-c", script], env=env,
                       capture_output=True, text=True, timeout=180)
    for line in (p.stdout or "").splitlines():
        if line.startswith("@@"):
            import json as _j
            return _j.loads(line[2:])
    raise AssertionError("端到端脚本没跑出结果：%s" % (p.stderr or p.stdout)[-800:])


_E2E = None


def _e2e_once():
    global _E2E
    if _E2E is None:
        _E2E = _e2e()
    return _E2E


def test_e2e_token_has_generation():
    assert _e2e_once()["tok_segments"] == 6


def test_e2e_old_session_valid_before_change():
    """改口令前，那枚令牌确实是能进的（否则后面的断言等于没测到东西）。"""
    assert _e2e_once()["before_change"] == 200


def test_e2e_old_session_dies_after_password_change():
    assert _e2e_once()["after_change"] == 302


def test_e2e_session_dies_after_logout():
    d = _e2e_once()
    assert d["before_logout"] == 200
    assert d["after_logout"] == 302


# ─────────────────────── 4. 登录页背景（装饰层） ───────────────────────
def test_login_bg_two_images():
    """背景要有两张图（logo + ico）。"""
    h = _login_html()
    assert 'class="login-bg"' in h
    assert 'id="bgLogo"' in h
    assert 'id="bgIco"' in h


def test_login_bg_images_hidden_until_loaded():
    """取不到就不显示，不留坏图图标。

    ⚠️ 这条断言改过：以前背景图默认 `class="bg-a hidden"`，靠 **JS** 赋值
    src 才显示 —— JS 一不执行（老浏览器不认 ES6 语法、被插件拦掉），
    背景就永远是空的，看着像这个效果压根没做。
    现在 src 直接写在 HTML 里，浏览器自己加载，失败则 onerror 隐藏。
    """
    h = _login_html()
    # ⚠️ 断言跟着改过两次，别照着旧形状写：
    #   1) 早期是 `class="bg-a hidden"` + JS 赋 src —— JS 一挂背景就永远空；
    #   2) 后来 src 直接写死成 /api/brand/logo（png）；
    #   3) 现在用**同目录同名字的 svg**（模板变量），取不到再退回同名 png。
    assert 'id="bgLogo"' in h and 'src="{{ bg_logo_svg }}"' in h
    assert 'id="bgIco"' in h and 'src="{{ bg_ico_svg }}"' in h
    # ⚠️ 第三次改：内联 onerror 属性会被 CSP 拦掉（script-src 只允许带
    #    随机数的脚本块），降级链改用捕获阶段监听 + data 属性承载地址。
    assert 'onerror=' not in h
    assert "document.addEventListener('error'" in h


def test_login_bg_floats():
    assert "@keyframes loginFloat" in _login_html()
    assert "animation: loginFloat" in _login_html()


def test_login_bg_has_shadow():
    assert "drop-shadow" in _login_html()


def test_login_bg_no_pointer_events():
    """背景不能挡住输入框。"""
    h = _login_html()
    assert "pointer-events: none" in h


def test_login_card_glass():
    """卡片毛玻璃。"""
    h = _login_html()
    assert "backdrop-filter: blur(" in h
    assert "-webkit-backdrop-filter: blur(" in h


def test_login_card_above_bg():
    """卡片必须抬到背景之上，否则会被盖住、点不到输入框。"""
    import re
    h = _login_html()
    m = re.search(r"\.login-card\s*\{(.*?)\}", h, re.S)
    assert m, "找不到 .login-card"
    body = m.group(1)
    assert "z-index" in body and "position: relative" in body


def test_login_reduced_motion_stops_float():
    """系统开了减少动态效果就停下飘动。"""
    h = _login_html()
    i = h.find("prefers-reduced-motion")
    assert i > 0
    assert "login-bg img { animation: none" in h[i:i + 300]


# ───────────────────────────── 反向验证 ─────────────────────────────
_REVERSE = [
    # (说明, 文件路径, 旧串, 新串)
    ("authed() 不传世代号", "webui.py",
     'verify_token(app.secret_key, request.cookies.get(COOKIE_NAME, ""),\n'
     "                        get_auth_gen())",
     'verify_token(app.secret_key, request.cookies.get(COOKIE_NAME, ""))'),
    ("改口令不 bump", "webui.py",
     "issue_token(app.secret_key, gen=bump_auth_gen())",
     "issue_token(app.secret_key)"),
    ("登出不 bump", "webui.py",
     "    bump_auth_gen()\n    resp = make_response(redirect(\"/login\"))",
     "    resp = make_response(redirect(\"/login\"))"),
    ("登录签发不带世代号", "webui.py",
     "issue_token(app.secret_key, gen=get_auth_gen())",
     "issue_token(app.secret_key)"),
    ("拿掉卡片 z-index", "templates/login.html",
     "    position: relative;\n    z-index: 1;",
     ""),
]


def _run_reverse():
    """把每处改动退回旧写法，对应断言必须变红。"""
    import shutil
    results = []
    for desc, rel, old, new in _REVERSE:
        path = os.path.join(_SRC, rel)
        bak = path + ".rvbak"
        shutil.copy2(path, bak)
        try:
            with io.open(path, encoding="utf-8") as f:
                s = f.read()
            if old not in s:
                results.append((desc, "skip", "旧串不在文件里"))
                continue
            with io.open(path, "w", encoding="utf-8") as f:
                f.write(s.replace(old, new, 1))
            p = subprocess.run(
                [sys.executable, os.path.join(_HERE, "test_auth_generation.py")],
                capture_output=True, text=True, timeout=600,
                env=dict(os.environ, PYTHONPATH=_SRC))
            if p.returncode == 0:
                results.append((desc, "FAIL", "退回旧写法后测试仍是绿的"))
            else:
                n = (p.stdout or "").count("FAIL:") or 1
                results.append((desc, "ok", "%d 项变红" % n))
        finally:
            shutil.move(bak, path)
    return results


if __name__ == "__main__":
    if "--reverse" in sys.argv:
        bad = 0
        for desc, st, extra in _run_reverse():
            print("  [%s] %-24s %s" % (st, desc, extra))
            if st == "FAIL":
                bad += 1
        print("反向验证：%s" % ("全部咬住" if not bad else "%d 处没咬住" % bad))
        sys.exit(1 if bad else 0)

    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = bad = 0
    for name, fn in fns:
        try:
            fn()
            print("  PASS %s" % name)
            ok += 1
        except Exception as e:
            print("  FAIL %s: %s" % (name, e))
            bad += 1
    print("\n%d passed, %d failed" % (ok, bad))
    sys.exit(1 if bad else 0)
