# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""清除口令必须先验证原口令。

为什么单独立一个文件：清除口令比修改口令后果更严重 —— 改口令是换锁，
清除口令是**拆锁**。此前后端却比改口令更松：

    if action == "clear":
        if not _auth_ok():      # 只查"有没有登录态"
            return 401
        cfg.pop("web_password", None)

意思是"已登录就免验原口令"。谁手里有一枚有效会话（别人用过的电脑、
借去的令牌、清口令前的旧 cookie），就能把口令直接拆掉。

更早还有一条 is_local_request() 短路：本机请求一律免验证，而面板本就
只监听 127.0.0.1，等于永远免验证。

现在与修改口令同一标准：只要已设过口令，清除一律先验证原口令，
不看请求来源、也不看是否登录。前端也会弹框索要。

保留 botpy 桩的做法：本测试只用到 flask，不依赖机器人 SDK。
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

TMP = None
_SAVED_HOME = os.environ.get("BILI_NOTIFY_HOME")
_SAVED_DATA = os.environ.get("ONOBN_DATA_DIR")


def setUpModule():
    global TMP
    TMP = tempfile.mkdtemp(prefix="onobn_clr_")
    os.environ["ONOBN_DATA_DIR"] = TMP
    os.environ.pop("BILI_NOTIFY_HOME", None)


def tearDownModule():
    if TMP:
        shutil.rmtree(TMP, ignore_errors=True)
    if _SAVED_HOME is None:
        os.environ.pop("BILI_NOTIFY_HOME", None)
    else:
        os.environ["BILI_NOTIFY_HOME"] = _SAVED_HOME
    if _SAVED_DATA is None:
        os.environ.pop("ONOBN_DATA_DIR", None)
    else:
        os.environ["ONOBN_DATA_DIR"] = _SAVED_DATA


def _client():
    import webui
    app = webui.app
    app.config["TESTING"] = True
    return app.test_client()


HDR = {"X-Requested-With": "bili-notify"}


def _post(c, path, payload):
    return c.post(path, json=payload, headers=HDR)


# ---------------------------------------------------------------- 后端行为


def test_clear_without_old_password_rejected():
    """已设口令 + 已登录，但清除时不给原口令 → 必须拒绝。"""
    c = _client()
    _post(c, "/api/security/password", {"action": "set", "new": "TestPwd123"})
    _post(c, "/api/login", {"password": "TestPwd123"})
    r = _post(c, "/api/security/password", {"action": "clear"})
    assert r.status_code == 401, (
        "清除口令不给原口令应当被拒绝，实际 %s" % r.status_code)


def test_clear_with_wrong_old_password_rejected():
    """给错原口令 → 必须拒绝。"""
    c = _client()
    _post(c, "/api/security/password", {"action": "set", "new": "TestPwd123"})
    _post(c, "/api/login", {"password": "TestPwd123"})
    r = _post(c, "/api/security/password", {"action": "clear", "old": "wrong"})
    assert r.status_code == 401, "原口令错误时清除应当被拒绝"
    assert "原口令" in (r.get_json() or {}).get("msg", ""), (
        "报错要说明是原口令的问题，别只说'失败'")


def test_clear_with_correct_old_password_ok():
    """给对原口令 → 清除成功，之后首页不再拦截。"""
    c = _client()
    _post(c, "/api/security/password", {"action": "set", "new": "TestPwd123"})
    _post(c, "/api/login", {"password": "TestPwd123"})
    r = _post(c, "/api/security/password", {"action": "clear", "old": "TestPwd123"})
    assert r.status_code == 200, (r.get_json() or {}).get("msg")
    assert c.get("/").status_code == 200, "清除后首页应当免登录"


def test_clear_before_any_password_ok():
    """从未设过口令时清除（本来就免登录）不该拦。"""
    c = _client()
    r = _post(c, "/api/security/password", {"action": "clear"})
    assert r.status_code == 200, "没设过口令时不该要求验证"


def test_clear_branch_verifies_old_password():
    """源码层面：clear 分支必须调用 verify_password。

    ⚠️ 只查源码文本会被注释顶住（注释里就写着 verify_password），
    所以先剥掉注释再查。
    """
    body = _clear_branch_body()
    assert "verify_password(" in body, (
        "clear 分支必须调用 verify_password 校验原口令")


def test_clear_branch_not_gated_by_local_request():
    """clear 分支不得再用 is_local_request() 短路豁免本机。"""
    body = _clear_branch_body()
    assert "is_local_request" not in body, (
        "不得再用 is_local_request() 给本机开后门：面板只监听 127.0.0.1，"
        "所有访问都算本机，等于永远免验证")


# ---------------------------------------------------------------- 前端行为


def test_frontend_clear_pwd_asks_old_password():
    """前端点「清除口令」必须弹框索要原口令，否则用户没地方填。"""
    src = _read_app_js_nocomments()
    i = src.find("async 'clear-pwd'()")
    assert i >= 0, "找不到 clear-pwd 处理函数"
    body = src[i:i + 800]
    assert "prompt(" in body, (
        "清除口令必须弹 prompt 要原口令 —— 后端要验，前端却不让填，"
        "用户只会拿到一句'原口令不对'")
    assert "'clear'" in body or '"clear"' in body


def test_frontend_clear_pwd_sends_old():
    """弹框拿到的原口令必须真的发给后端。"""
    src = _read_app_js_nocomments()
    i = src.find("async 'clear-pwd'()")
    body = src[i:i + 800]
    assert "old:" in body, "必须把原口令放进 old 字段发给后端"


# ---------------------------------------------------------------- 工具


def _strip_comments(s):
    out = []
    for line in s.split("\n"):
        t = line.strip()
        if t.startswith("#") or t.startswith("//") or t.startswith("/*"):
            continue
        out.append(line)
    return "\n".join(out)


def _clear_branch_body():
    """只取 clear 分支本身，别把后面的 set 分支也算进来。

    ⚠️ 以前用固定 900 字符窗口，结果一路读到 set 分支 —— 那里本来就有
    verify_password，于是"clear 分支没校验"这种退化**根本测不出来**
    （反向验证时它不红）。现在截到分支结束为止。
    """
    src = _read_webui_nocomments()
    i = src.find('if action == "clear":')
    assert i >= 0, "找不到 clear 分支"
    end = src.find('\n    new = ', i)
    assert end > i, "找不到 clear 分支的结束位置"
    return src[i:end]


def _read_webui_nocomments():
    with open(os.path.join(SRC, "webui.py"), encoding="utf-8") as f:
        return _strip_comments(f.read())


def _read_app_js_nocomments():
    with open(os.path.join(SRC, "static", "app.js"), encoding="utf-8") as f:
        return _strip_comments(f.read())
