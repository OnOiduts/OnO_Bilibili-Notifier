#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录页完整性检查。

为什么需要这个测试
------------------
登录页是**独立模板**，拿不到 index.html 内联 <style> 里的任何东西。
面板那些 .card / .wrap / .f / .hint 的视觉样式全在 index.html 里定义，
登录页引用它们等于引用空气 —— 页面渲染成裸 HTML：透明卡片、不居中、
标签和说明没有样式。v2.0.0 换整体 UI 时登录页没跟着改，一直是坏的。

所以这里盯死三件事：
  1. 登录页不再引用那批"只有面板才有的"类；
  2. 登录页自带完整样式（不依赖 index.html 的内联 style）；
  3. /login 传了 app_version（不传 CSS 缓存串就是空的，改了不生效）。

另外 /api/brand/ 必须在未登录时放行 —— 登录页自己要显示 logo，
标签页图标更是既不带 cookie 也不带自定义头，挡住就永远取不到。
"""
import io
import os
import re
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
WEBUI = os.path.join(SRC, "webui.py")
LOGIN = os.path.join(SRC, "templates", "login.html")
CSS = os.path.join(SRC, "static", "style.css")

_results = []


def _check(name, fn):
    try:
        fn()
        _results.append((True, name, ""))
    except AssertionError as e:
        _results.append((False, name, str(e)))
    except Exception as e:  # noqa: BLE001
        _results.append((False, name, "%s: %s" % (type(e).__name__, e)))


def _read(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read()


def _strip_comments(src: str) -> str:
    """去掉 Python 行注释后再查关键字。

    ⚠️ 不剥注释会踩坑：注释里提到的旧写法/关键字会被当成真代码，于是
    「把改动退回旧写法、断言应该变红」的反向验证变成假通过 —— 这个坑
    在同一个项目里踩过三次。所以凡是要判断"代码里有没有某段写法"，
    一律先剥注释。字符串里的 # 不算注释开头，别误伤。
    """
    out = []
    for line in src.splitlines():
        q = None
        cut = len(line)
        i = 0
        while i < len(line):
            ch = line[i]
            if q:
                if ch == "\\":
                    i += 2
                    continue
                if ch == q:
                    q = None
            elif ch in ("'", '"'):
                q = ch
            elif ch == "#":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def _login_html():
    return _read(LOGIN)


# ---------------------------------------------------------------- 1. 旧类

def test_no_panel_only_classes():
    """登录页不能再用只有 index.html 内联样式才有的类。"""
    html = _login_html()
    for cls in ("wrap", "card", "hint", "row"):
        # 只在 class="..." 里才算引用（避免匹配到 login-card 这类前缀）
        for m in re.findall(r'class="([^"]+)"', html):
            for c in m.split():
                assert c != cls, "登录页仍在使用只有面板才有样式的类 .%s" % cls


def test_old_f_label_gone():
    """.f 这个标签类在 style.css 里根本不存在。"""
    html = _login_html()
    assert 'class="f"' not in html, "登录页仍在用 style.css 里不存在的 .f"


def test_style_css_has_no_hint():
    """.hint 在 style.css 里不存在 —— 登录页若引用它说明又回到旧写法。"""
    css = _read(CSS)
    assert not re.search(r'^\s*\.hint\s*[,{]', css, re.M), \
        "style.css 里出现了 .hint（登录页曾引用它但没有样式）"
    assert "login-hint" in _login_html(), "登录页应改用自带的 .login-hint"


# ---------------------------------------------------------------- 2. 自带样式

def test_login_has_own_style_block():
    """登录页必须自带 <style>，不能裸依赖外部类。"""
    html = _login_html()
    assert "<style>" in html, "登录页没有自带样式块，会渲染成裸 HTML"
    assert ".login-card" in html, "缺少 .login-card 样式"


def test_login_card_has_visual_props():
    """登录卡片必须自己有背景 / 边框 / 内边距。

    面板的 .card 这三样挂在 index.html 的内联样式里，登录页拿不到。
    """
    html = _login_html()
    m = re.search(r'\.login-card\s*\{([^}]*)\}', html)
    assert m, "找不到 .login-card 的样式定义"
    body = m.group(1)
    for prop in ("background", "border", "padding"):
        assert prop in body, ".login-card 缺少 %s（会渲染成透明无框的裸块）" % prop


def test_login_body_centered():
    """页面要居中 —— 旧的 .wrap 没有布局定义，卡片会贴左边。"""
    html = _login_html()
    assert re.search(r'justify-content:\s*center', html), "登录页没有居中布局"
    assert re.search(r'align-items:\s*center', html), "登录页没有垂直居中"


def test_login_uses_css_variables():
    """复用 style.css 的变量，才能跟随浅色/深色主题。"""
    html = _login_html()
    for var in ("--bg", "--tx", "--line", "--card-solid"):
        assert var in html, "登录页没有复用 CSS 变量 %s（主题会不一致）" % var


def test_err_box_has_own_padding():
    """错误提示必须自带 padding / background / border。

    style.css 里这三项挂在 .msg.show 上，.msg.err 只有 color 和 border-color。
    只靠 .msg.err 显示出来是一行没有框、没有底的红字，看着像"提示没出来"。
    """
    html = _login_html()
    m = re.search(r'\.login-err\s*\{([^}]*)\}', html)
    assert m, "找不到 .login-err 样式"
    body = m.group(1)
    for prop in ("padding", "background", "border"):
        assert prop in body, ".login-err 缺少 %s（错误提示会变成裸红字）" % prop
    assert ".login-err.show" in html, "缺少 .login-err.show 显示规则"
    assert "classList.add('show')" in html, "JS 出错时应加上 show 类再显示"


def test_theme_reads_localstorage():
    """主题要和面板一致：面板存在 localStorage['bili-theme']。

    以前这里读的是系统 prefers-color-scheme —— 面板设了浅色、登录页却是
    深色，跳过去会闪一下。
    """
    html = _login_html()
    # ⚠️ 必须查**具体的调用形式**而不是裸字符串 'bili-theme'：上面那句
    #    说明注释里写的是 localStorage['bili-theme']（方括号），裸查的话
    #    注释就能把断言顶住 —— 反向验证时"改回读系统"居然全绿，才发现。
    assert "localStorage.getItem('bili-theme')" in html, \
        "登录页没有真正读面板的主题设置（只有注释里提到不算），主题会跳变"


def test_logo_uses_img_src_not_json():
    """/api/brand/logo 返回的是图片本身，不是 JSON。

    fetch 再 .json() 永远解析失败，logo 就永远不显示。
    """
    html = _login_html()
    # ⚠️ 断言跟着改过两次：最早是 JS 里 fetch(...).json()（永远解析失败），
    #    后来改成 JS 赋值 img.src，现在是**直接写在 <img> 的 src 属性里**
    #    （JS 一挂图就没了，属性则由浏览器自己加载，最稳）。
    assert 'src="/api/brand/logo"' in html, "logo 的 src 应直接写在 <img> 上"
    assert "onerror" in html, "logo 取不到时应隐藏（不留空白块）"


# ---------------------------------------------------------------- 3. 后端

def test_login_route_passes_app_version():
    """/login 必须传 app_version。

    不传就渲染成空，style.css?v= 的缓存串失效，改了样式看不出来。

    ⚠️ 这里必须**先切出 /login 这个函数块**再检查。第一版写成了一条跨行
    正则，而 /login 和 index 两个路由只隔几行 —— 正则直接跨到隔壁 index
    的 render_template 上，那里本来就有 app_version，于是去掉 /login 的
    app_version 测试照样全绿。反向验证时才发现这条是假通过。
    """
    src = _read(WEBUI)
    start = src.find('@app.route("/login"')
    assert start >= 0, "找不到 /login 路由"
    # 函数块结束于**下一个装饰器**（行首的 @app.xxx）。
    # ⚠️ 不能用 '\ndef ' 当边界：def login(): 本身就在 start 之后几行，
    #    那样会把函数体整个切掉，只剩装饰器一行。
    nxt = src.find("\n@app.", start + 10)
    if nxt < 0:
        nxt = len(src)
    block = src[start:nxt]
    # ⚠️ 改成**真的渲染一次**再查，不再扫源码。
    #    模板变量后来收进了 _login_ctx() 这个共用函数（GET 与 POST 失败
    #    两条路都要带），而它定义在装饰器之前 —— 按"函数块"切就切不到，
    #    于是断言白红一次。直接看渲染结果才是真行为：CSS 链接上的 ?v=
    #    有值，就说明 app_version 真的传进去了。
    # ⚠️ webui 必须在这里现导入：本模块顶部没引它（其余断言只扫源码文本），
    #    直接写 `webui.app...` 就是 NameError —— 这个测试之前一直报红。
    # ⚠️ 同时必须把数据目录切到临时目录：下面的 save_cfg 会真的写一份口令
    #    进配置，不隔离就污染本机数据，还会让同批次里排在后面的测试
    #    （它们以为没设口令）随机变红 —— 这类"测试间污染"在本项目里出现过。
    import tempfile

    # ⚠️ 先把 src 挂进 sys.path 再 import：cfgutil / security / webui 都在那里。
    #    顺序反了就是 ModuleNotFoundError —— 刚才第一次改就栽在这。
    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    import cfgutil   # noqa: E402
    import security  # noqa: E402

    saved_home = os.environ.get("BILI_NOTIFY_HOME")
    tmp_home = tempfile.mkdtemp(prefix="onobn_login_ver_")
    os.environ["BILI_NOTIFY_HOME"] = tmp_home
    try:
        import webui  # noqa: E402
        cfgutil.save_cfg({"web_password": security.hash_password("TmpPass123")})
        html = webui.app.test_client().get("/login").get_data(as_text=True)
    finally:
        if saved_home is None:
            os.environ.pop("BILI_NOTIFY_HOME", None)
        else:
            os.environ["BILI_NOTIFY_HOME"] = saved_home
        try:
            shutil.rmtree(tmp_home, ignore_errors=True)
        except Exception:
            pass
    assert 'render_template("login.html"' in block, "块内没有 login.html 的渲染调用"
    m = re.search(r'style\.css\?v=([^"]*)"', html)
    assert m, "登录页没渲染出 style.css 的缓存串"
    assert m.group(1).strip(), "app_version 是空的（CSS 缓存串失效）"


def test_guard_allows_brand_when_locked():
    """设了口令后 /api/brand/ 必须仍能访问。

    登录页自己要显示 logo，favicon 更是既不带 cookie 也不带自定义头，
    挡住就永远取不到。
    """
    src = _read(WEBUI)
    m = re.search(r'request\.path\.startswith\(\((.*?)\)\)', src, re.S)
    assert m, "找不到 guard 的白名单"
    whitelist = m.group(1)
    assert "/api/brand" in whitelist, \
        "guard 白名单里没有 /api/brand（设口令后登录页的 logo 会被 401）"


# ---------------------------------------------------------------- 4. 打包

def test_pack_includes_login_template():
    """发布包里必须带登录页模板。"""
    hits = []
    for fn in sorted(os.listdir(HERE)):
        if not (fn.startswith("OnO_Bilibili-Notifier_v") and fn.endswith(".zip")):
            continue
        zp = os.path.join(HERE, fn)
        try:
            with zipfile.ZipFile(zp) as z:
                names = z.namelist()
        except Exception:  # noqa: BLE001
            continue
        if any(n.endswith("templates/login.html") for n in names):
            hits.append(fn)
    if hits:
        # 抽查最新一个包，确认是新版（含 login-card）
        with zipfile.ZipFile(os.path.join(HERE, hits[-1])) as z:
            html = z.read([n for n in z.namelist()
                           if n.endswith("templates/login.html")][0]).decode("utf-8")
        assert ".login-card" in html, \
            "包 %s 里的登录页还是旧版（没有 .login-card）" % hits[-1]
        return
    # 没有历史包可查时，至少源码里的必须是新版
    assert ".login-card" in _login_html(), "源码登录页不是新版"


def test_login_page_shows_version():
    """登录页必须显示版本号。

    ⚠️ 这是「改了没生效」的唯一凭据，别当成装饰删掉。

    登录页是这个问题的重灾区：Flask 默认**不热加载模板**
    （jinja_env.auto_reload=False，已实测确认）—— 覆盖解压之后如果不重启
    机器人进程，浏览器里拿到的依然是旧模板，用户看到旧样式、旧按钮，
    却没有任何报错，无从判断到底加载了哪一份。

    页面上印出版本号之后，一眼就能确认：页面上的版本 == 刚装的版本。
    """
    html = _login_html()
    assert "login-ver" in html, "登录页没有版本号块（.login-ver）"
    assert "{{ app_version }}" in html, \
        "版本号块里没有渲染 app_version，会显示成空的 OnOBN v"
    # 必须在卡片内、hint 之外独立成块，别混进说明文字里
    # ⚠️ 品牌名必须是「OnOiduts工业」，与面板左下角那行一致。
    #    以前这里锁的是缩写 OnOBN，同一个程序两个页面两个名字，
    #    看着像跑的不是一个东西。改品牌名时这条断言要跟着改。
    # ⚠️ 按前缀匹配，不锁死整行：版本号后面曾经挂过构建戳
    #    （「· 背景4角随机」，用于区分装的是哪一份登录页），四角随机确认
    #    生效后已移除。前缀匹配保证无论挂不挂都能确认"品牌 + 版本"这半截。
    m = re.search(r'<div class="login-ver">\s*OnOiduts工业 v\{\{ app_version \}\}',
                  html)
    assert m, ("版本号块的写法不对，应为 "
               "<div class=\"login-ver\">OnOiduts工业 v{{ app_version }}…</div>")


def test_webui_prints_program_dir():
    """启动时必须打印面板程序目录。

    用户覆盖解压到 A 目录、机器人却还在跑 B 目录（旧的那个），是「改了没生效」
    最常见的原因。把实际跑的目录打出来，一比对就知道。
    """
    src = io.open(WEBUI, encoding="utf-8").read()
    src_nc = _strip_comments(src)
    assert "面板程序目录" in src_nc, \
        "webui.py 启动时没打印程序目录，用户无从确认跑的是哪一份"


# ---------------------------------------------------------------- 主流程

if __name__ == "__main__":
    checks = [(k, v) for k, v in sorted(globals().items())
              if k.startswith("test_") and callable(v)]
    for name, fn in checks:
        _check(name, fn)
    ok = sum(1 for r in _results if r[0])
    for good, name, msg in _results:
        print("%s %s%s" % ("PASS" if good else "FAIL", name,
                           "" if good else "  <- " + msg))
    print("\n%d/%d 通过" % (ok, len(_results)))
    sys.exit(0 if ok == len(_results) else 1)
