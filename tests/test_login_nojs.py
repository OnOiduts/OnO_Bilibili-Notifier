#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录页必须在**没有 JavaScript** 的情况下也能登录。

背景
----
以前登录页只走 JS 的 fetch：脚本一旦没执行，点「进入」就毫无反应，
连错误提示都显示不出来 —— 因为显示提示的代码本身也是 JS。
脚本失效的常见原因：

  * 用了 ES6+ 语法（const / let / async / await / 箭头函数），
    老浏览器整块语法报错，所有 JS 一行都不跑；
  * 被插件、缓存或 CSP 拦掉。

所以现在登录页是一个真正的 <form method="post" action="/login">，
按钮是 submit；JS 只是"增强"，能用 fetch 就免刷新，不能用就原样提交。

背景同理：原来两张图靠 JS 赋值 src，还指向 jsDelivr CDN ——
JS 不跑或 CDN 取不到，背景就是空的，等于这个效果压根没做。
现在光斑是纯 CSS 画的，远程图只是锦上添花。
"""

import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
LOGIN = os.path.join(SRC, "templates", "login.html")

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, extra))


def read_login():
    with open(LOGIN, encoding="utf-8") as f:
        return f.read()


def _card_open_tag(h):
    """返回卡片元素的完整开标签，如 `<form class="login-card" ... action="/login">`。"""
    i = h.find('class="login-card"')
    if i < 0:
        return ""
    a = h.rfind("<", 0, i)
    b = h.find(">", i)
    return h[a:b + 1] if a >= 0 and b > i else ""


def test_form_is_native():
    """提交必须是一个真正的表单，按钮是 submit —— 不靠 JS 就能提交。

    ⚠️ 不要去全文匹配 "<form" 这个词：CSS 注释里为了说明问题，写了
    `<form method="post" action="/login">` 这样的示例文字，改回 div
    照样能匹配到注释里的 form —— 断言形同虚设。直接看**卡片元素本身的
    标签名**：只有真实标签才带 class="login-card"。
    """
    h = read_login()
    m = re.search(r'<(\w+)[^>]*class="login-card"', h)
    tag = m.group(1) if m else None
    check("卡片元素是 form（不是 div）", tag == "form", "got %s" % tag)
    open_tag = _card_open_tag(h)
    check('form 有 method="post"', 'method="post"' in open_tag, open_tag)
    check('form 提交到 /login', 'action="/login"' in open_tag, open_tag)
    check("按钮是 type=submit", re.search(r'<button[^>]*type="submit"', h) is not None)
    check("不再依赖 onclick 调用 JS", 'onclick="doLogin()"' not in h)


def test_script_is_es5():
    """脚本只用 ES5 —— ES6+ 语法会让老浏览器整块报错，全部 JS 失效。"""
    h = read_login()
    m = re.search(r"<script[^>]*>(.*?)</script>", h, re.S)
    check("存在脚本块", m is not None)
    if not m:
        return
    s = m.group(1)
    for kw in ("const ", "let ", "async ", "await ", "=>", "?."):
        check("脚本不含 ES6+ 语法 %r" % kw.strip(), kw not in s)


def test_background_not_dependent_on_js():
    """背景必须不依赖 JS、不依赖网络：光斑纯 CSS，图片 src 直接写在 HTML。"""
    h = read_login()
    check("有纯 CSS 光斑 orb-1", 'class="orb orb-1"' in h)
    check("有纯 CSS 光斑 orb-2", 'class="orb orb-2"' in h)
    check("有纯 CSS 光斑 orb-3", 'class="orb orb-3"' in h)
    # src 写在 HTML 里，不是交给 JS 赋值
    check("背景图 src 写在 HTML 中", 'id="bgLogo"' in h and 'src="/api/brand/logo"' in h)
    check("logo src 写在 HTML 中", 'id="logo"' in h and 'src="/api/brand/logo"' in h)
    # 取不到就隐藏，不留坏图图标。
    # ⚠️ 不能退回内联 onerror 属性：CSP 的 script-src 只放行带随机数的
    #    脚本块，随机数对内联事件属性无效，写了也会被浏览器拦掉。
    #    现在由 <head> 里那段带随机数的脚本统一处理（捕获阶段监听 error）。
    check("取不到时隐藏，不留坏图", "el.style.display = 'none'" in h)
    check("禁用内联事件属性（CSP 会拦）", "onerror=" not in h)


def test_card_background_fallback():
    """color-mix 之前必须先有一条老浏览器也认的背景，否则卡片全透明。

    ⚠️ 必须先剥掉 CSS 注释再找：注释正文里就会提到 "color-mix" 和 "rgba"
    这两个词（说明为什么要有兜底），不剥的话注释里的词会先被命中，
    顺序判断完全失真 —— 注释顶住断言，改坏了也测不出来。
    """
    h = read_login()
    i = h.find(".login-card {")
    check("存在 .login-card 规则", i >= 0)
    if i < 0:
        return
    block = re.sub(r"/\*.*?\*/", "", h[i:i + 900], flags=re.S)
    cm = block.find("color-mix")
    fb = block.find("rgba(")
    check("有 rgba 兜底背景", fb >= 0)
    check("有 color-mix 现代背景", cm >= 0)
    check("兜底写在 color-mix 之前", 0 <= fb < cm, "fb=%d cm=%d" % (fb, cm))


def _client():
    """起一个带口令的测试客户端。"""
    d = tempfile.mkdtemp(prefix="lgnojs_")
    os.environ["BILI_NOTIFY_HOME"] = d
    sys.path.insert(0, SRC)
    import yaml
    import security
    h = security.hash_password("TestPwd123")
    with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump({"web_password": h}, f, allow_unicode=True)
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


def test_native_post_login():
    """服务端必须接受原生表单提交：正确口令 → 跳转 + 下发会话。"""
    c = _client()
    r = c.post("/login", data={"password": "TestPwd123"})
    check("POST /login 正确口令 → 302", r.status_code == 302,
          "got %s" % r.status_code)
    check("跳转目标为 /", r.headers.get("Location", "").endswith("/"),
          r.headers.get("Location", ""))
    check("下发了会话 cookie",
          "bn_auth=" in (r.headers.get("Set-Cookie") or ""))
    check("登录后 GET / → 200", c.get("/").status_code == 200)


def test_native_post_wrong_password():
    """口令错误时，原因必须写进页面（服务端渲染，不靠 JS 显示）。"""
    c = _client()
    r = c.post("/login", data={"password": "definitely-wrong"})
    check("错误口令 → 401", r.status_code == 401, "got %s" % r.status_code)
    body = r.data.decode("utf-8")
    check("页面内嵌错误提示", 'class="login-err show"' in body)
    check("提示写明口令不对", "口令不对" in body)


def test_json_api_still_works():
    """JSON 接口继续可用（JS 可用时走这条路，免刷新）。"""
    c = _client()
    r = c.post("/api/login", json={"password": "TestPwd123"},
               headers={"X-Requested-With": "bili-notify"})
    check("POST /api/login → 200", r.status_code == 200, "got %s" % r.status_code)
    check("返回 ok:true", b'"ok": true' in r.data or b'"ok":true' in r.data)


def main():
    for fn in (test_form_is_native, test_script_is_es5,
               test_background_not_dependent_on_js, test_card_background_fallback,
               test_native_post_login, test_native_post_wrong_password,
               test_json_api_still_works):
        print("%s" % fn.__name__)
        try:
            fn()
        except Exception as e:
            global FAIL
            FAIL += 1
            print("  [ERROR] %s: %s" % (fn.__name__, e))
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
