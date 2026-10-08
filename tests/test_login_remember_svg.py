# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""登录页三项改动：记住登录状态 / 改口令必须验证原口令 / 背景用同名 svg。

这三条都是**安全问题或显示问题**，不是观感微调：

1. 记住登录状态：以前 cookie 一律 7 天，没有"只登一次"的选项。
   现在不勾就是会话 cookie（关掉浏览器即失效）。
2. 改口令必须验证原口令：以前写成 `need_auth() and not authed()`，
   意思是"已登录就不用验旧口令"—— 谁手里有一枚有效会话就能改掉门锁，
   把真正的主人锁在门外。
3. 背景飘的图换成同目录同名字的 svg：矢量放大不糊、体积小。
"""
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

flask = pytest.importorskip("flask")

import webui  # noqa: E402

REPO = os.path.join(os.path.dirname(__file__), "..")
LOGIN_HTML = os.path.join(REPO, "src", "templates", "login.html")
INDEX_HTML = os.path.join(REPO, "src", "templates", "index.html")
APP_JS = os.path.join(REPO, "src", "static", "app.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    return _strip_html_comments(raw) if path.endswith(".html") else raw


def _strip_html_comments(s):
    """去掉 HTML 注释。

    ⚠️ 必须剥：注释里写的示例标签（比如 `<input name="remember">`）会被
       `re.search('<input[^>]*name="remember"')` 之类直接命中，于是就算
       真的标签被删了，断言照样"通过" —— 反向验证形同虚设。
    """
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


def _strip_comments_js(s):
    """去掉 JS 注释，避免注释里的示例文字把断言顶住（踩过三次）。"""
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?m)^\s*//.*$", "", s)
    return s


def _client():
    """测试客户端统一带上自定义头，否则会被 CSRF 拦截（403）。

    ⚠️ 真实前端 app.js 每个请求都带 X-Requested-With，这里必须还原，
       否则测的是"被 CSRF 挡住"，不是要测的口令逻辑。
    """
    c = webui.app.test_client()
    c.environ_base["HTTP_X_REQUESTED_WITH"] = "bili-notify"
    return c


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """每个用例独立数据目录。

    ⚠️ 不隔离的话，前一个用例设过的口令会漏到后面 —— 后续用例走的是
    "修改口令"分支，根本碰不到要测的那条早返回，反向验证会假通过。
    """
    home = tmp_path / "data"
    home.mkdir()
    monkeypatch.setenv("BILI_NOTIFY_HOME", str(home))
    monkeypatch.setenv("ONOBN_HOME", str(home))
    return str(home)


# ---------------- 1. 记住登录状态 ----------------

def test_login_page_has_remember_checkbox():
    """登录页必须有「记住登录状态」勾选框，且是原生 checkbox（能点、能聚焦）。"""
    html = _read(LOGIN_HTML)
    m = re.search(r'<input[^>]*name="remember"[^>]*>', html)
    assert m, "登录页缺少 name=remember 的勾选框"
    assert 'type="checkbox"' in m.group(0), "必须是原生 checkbox，自己画的方框点了没反应"


def test_remember_checked_by_default_false():
    """默认不勾：登录态默认给短的更安全。"""
    html = _read(LOGIN_HTML)
    m = re.search(r'<input[^>]*name="remember"[^>]*>', html)
    assert "checked" not in m.group(0), "默认不该勾上"


def test_remember_has_hint_text():
    html = _read(LOGIN_HTML)
    assert "记住登录状态" in html
    # 要写清勾与不勾的区别，否则用户不知道自己在选什么
    assert "7" in html and ("不勾" in html or "关掉浏览器" in html)


def test_fetch_body_carries_remember():
    """fetch 提交不走原生表单序列化，remember 必须手动塞进 body。"""
    js = _strip_comments_js(_read(LOGIN_HTML))
    assert "remember:" in js.replace(" ", ""), "fetch 的 body 里没有带 remember"


def test_issue_cookie_remember_true_is_persistent():
    """勾上 → 7 天持久 cookie。"""
    with webui.app.test_request_context("/"):
        from flask import make_response
        r = make_response("ok")
        webui._issue_login_cookie(r, remember=True)
        cookie = r.headers.get("Set-Cookie", "")
    assert "Max-Age=604800" in cookie, f"记住登录应是 7 天持久 cookie：{cookie}"


def test_issue_cookie_remember_false_is_session():
    """不勾 → 会话 cookie（不带 Max-Age），关掉浏览器即失效。"""
    with webui.app.test_request_context("/"):
        from flask import make_response
        r = make_response("ok")
        webui._issue_login_cookie(r, remember=False)
        cookie = r.headers.get("Set-Cookie", "")
    assert "Max-Age" not in cookie, f"不记住登录不该带 Max-Age：{cookie}"
    assert webui.COOKIE_NAME in cookie


@pytest.mark.parametrize("remember_flag", [True, False])
def test_api_login_respects_remember(isolated, remember_flag):
    """JSON 接口：body 里的 remember 决定 cookie 寿命。"""
    import cfgutil
    import security
    cfgutil.save_cfg({"web_password": security.hash_password("OldPass123")})

    c = _client()
    r = c.post("/api/login", json={"password": "OldPass123",
                                   "remember": remember_flag})
    assert r.status_code == 200, r.get_data(as_text=True)
    cookie = r.headers.get("Set-Cookie", "")
    if remember_flag:
        assert "Max-Age=604800" in cookie
    else:
        assert "Max-Age" not in cookie, f"没勾记住登录却发了持久 cookie：{cookie}"


def test_native_form_post_carries_remember(isolated):
    """原生表单提交（没有 JS 的那条路）也要能带上 remember。"""
    import cfgutil
    import security
    cfgutil.save_cfg({"web_password": security.hash_password("OldPass123")})

    c = _client()
    r = c.post("/login", data={"password": "OldPass123", "remember": "1"},
               follow_redirects=False)
    assert r.status_code == 302, r.get_data(as_text=True)
    assert "Max-Age=604800" in r.headers.get("Set-Cookie", "")

    r2 = c.post("/login", data={"password": "OldPass123"},
                follow_redirects=False)
    assert "Max-Age" not in r2.headers.get("Set-Cookie", "")


# ---------------- 2. 改口令必须验证原口令 ----------------

def test_index_has_old_password_input():
    html = _read(INDEX_HTML)
    assert 'id="oldPwd"' in html, "面板缺少「原口令」输入框"


def test_app_js_sends_old_password():
    js = _strip_comments_js(_read(APP_JS))
    m = re.search(r"async 'set-pwd'\(\)\s*\{(.*?)\n  \}", js, flags=re.S)
    assert m, "找不到 set-pwd 处理"
    body = m.group(1)
    assert "old:" in body.replace(" ", ""), "设置口令时没有把原口令发出去"


def test_change_password_requires_old_even_when_logged_in(isolated):
    """已登录状态下改口令，不填原口令必须被拒。

    这是本轮的核心安全问题：以前 `need_auth() and not authed()` 让已登录的
    会话可以跳过原口令校验 —— 拿到会话的人能直接改掉门锁。
    """
    import cfgutil
    import security
    cfgutil.save_cfg({"web_password": security.hash_password("OldPass123")})

    c = _client()
    # 先正常登录，拿到有效会话
    r = c.post("/api/login", json={"password": "OldPass123"})
    assert r.status_code == 200

    # 已登录，但不给原口令 → 必须拒绝
    r2 = c.post("/api/security/password",
                json={"action": "set", "new": "BrandNew456"})
    assert r2.status_code == 401, (
        f"已登录却不验原口令就改成了 —— 拿会话的人能把主人锁在门外："
        f"{r2.get_data(as_text=True)}")

    # 给了正确原口令 → 通过
    r3 = c.post("/api/security/password",
                json={"action": "set", "new": "BrandNew456",
                      "old": "OldPass123"})
    assert r3.status_code == 200, r3.get_data(as_text=True)


def test_change_password_wrong_old_rejected(isolated):
    import cfgutil
    import security
    cfgutil.save_cfg({"web_password": security.hash_password("OldPass123")})

    c = _client()
    c.post("/api/login", json={"password": "OldPass123"})
    r = c.post("/api/security/password",
               json={"action": "set", "new": "BrandNew456", "old": "WrongOne"})
    assert r.status_code == 401
    assert "原口令" in r.get_data(as_text=True)


def test_first_set_password_still_no_old_needed(isolated):
    """首次设置（此前无口令）不该要求原口令 —— 否则无从设置。"""
    import cfgutil
    cfgutil.save_cfg({})

    c = _client()
    r = c.post("/api/security/password", json={"action": "set",
                                               "new": "FirstPass123"})
    assert r.status_code == 200, r.get_data(as_text=True)


# ---------------- 3. 背景用同目录同名字的 svg ----------------

def test_svg_variant_same_dir_same_name():
    """同目录、同文件名，只换扩展名。"""
    got = webui.svg_variant(
        "https://cdn.jsdelivr.net/gh/OnOiduts/OnO-ImageHost-@main/Logo/logo.png")
    assert got == ("https://cdn.jsdelivr.net/gh/OnOiduts/"
                   "OnO-ImageHost-@main/Logo/logo.svg"), got

    got2 = webui.svg_variant(
        "https://cdn.jsdelivr.net/gh/OnOiduts/"
        "OnO-ImageHost-@main/Web/OnOBN/ico.png")
    assert got2.endswith("/Web/OnOBN/ico.svg"), got2


def test_svg_variant_empty_safe():
    assert webui.svg_variant("") == ""
    assert webui.svg_variant("noext") == ""


def test_login_bg_uses_svg():
    html = _read(LOGIN_HTML)
    assert 'src="{{ bg_logo_svg }}"' in html, "背景 logo 没有用 svg 地址"
    assert 'src="{{ bg_ico_svg }}"' in html, "背景 ico 没有用 svg 地址"


def test_login_bg_has_fallback_chain():
    """svg 取不到 → 退回同名 png → 内置图形 → 再失败才隐藏。

    ⚠️ 断言按「当前实现」写：降级跑在 <head> 里带随机数的脚本块中，靠
    document 上的捕获阶段 error 监听统一处理，图片自身带 data-fb /
    data-shape / data-fb-step 三个属性描述降级链。
    不能退回成内联 onerror 属性 —— CSP 的 script-src 只允许带随机数的
    内联脚本块，随机数对内联事件属性无效，会被一并拦掉，四个角直接空。
    """
    html = _read(LOGIN_HTML)
    head = html[: html.find("<body>")]
    body = html[html.find("<body>"):]

    # 1) 四张背景图都带降级描述，缺一张那个角就会空
    for attr in ("data-fb=", "data-shape="):
        assert body.count(attr) >= 4, "背景图 %s 少于 4 张，有角会空" % attr
    assert 'data-fb="/api/brand/logo"' in html
    assert 'data-fb="/api/brand/ico"' in html
    assert 'data-fb="{{ bg1_png }}"' in html
    assert 'data-fb="{{ bg2_png }}"' in html

    # 2) 内置兜底图形（不联网），四个角各一个
    assert "var BG_SHAPES = [" in head
    assert head.count("data:image/svg+xml,") >= 4, "内置兜底图形少于 4 个"

    # 3) 降级逻辑：先退回 data-fb，再退回内置图形，最后才隐藏
    assert "document.addEventListener('error'" in head
    assert "data-fb-step" in head
    assert "el.style.display = 'none'" in head


def test_bgfallback_defined_before_body():
    """降级监听必须在 <body> 之前注册，而且是**捕获阶段**。

    背景 <img> 在 body 里，图片请求解析阶段就发出去了，失败回调可能早于
    body 末尾那段脚本执行 —— 监听没注册，图就永远空着。
    ⚠️ 捕获阶段是硬要求：error 事件不冒泡，只能在捕获阶段从 document
    往下传递时接住，用冒泡阶段监听接不到任何东西。
    """
    html = _read(LOGIN_HTML)
    i_fn = html.find("document.addEventListener('error'")
    i_body = html.find("<body>")
    assert i_fn > 0 and i_body > 0
    assert i_fn < i_body, "降级监听注册晚于 <body>，图片失败时还没接住"
    # 第三个参数 true = 捕获阶段；写成 false/省略就接不到不冒泡的 error
    tail = html[i_fn: i_fn + 1500]
    assert "}, true);" in tail, "error 监听必须是捕获阶段（第三参数 true）"


def test_login_ctx_passes_svg_urls(isolated):
    """两条渲染路径（GET 和 POST 失败）都必须带上 svg 地址，漏了就渲染成空。"""
    import cfgutil
    import security
    cfgutil.save_cfg({"web_password": security.hash_password("OldPass123")})

    c = _client()
    r = c.get("/login")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Logo/logo.svg" in body, "GET /login 没渲染出 svg 背景地址"
    assert "Web/OnOBN/ico.svg" in body

    r2 = c.post("/login", data={"password": "bad"})
    assert r2.status_code == 401
    body2 = r2.get_data(as_text=True)
    assert "Logo/logo.svg" in body2, "POST 失败重渲染时漏了 svg 背景地址"


def test_bg_still_has_css_orbs():
    """纯 CSS 光斑必须保留：不看网络、不看 JS，保证背景一定有东西在飘。"""
    html = _read(LOGIN_HTML)
    for cls in ("orb-1", "orb-2", "orb-3"):
        assert cls in html, f"缺少光斑 {cls}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
