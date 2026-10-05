#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录页的三处细节：禁用选取、版本号旁的品牌名、背景四张图。

为什么需要这个测试
------------------
1. **禁用选取**：登录页除口令框外全是装饰，能被鼠标拖蓝的部分既没用又难看。
   但输入框必须单独放开，否则粘贴不进口令。
2. **版本号旁必须是「OnOiduts工业」**：面板左下角写的是这个，登录页却写成了
   缩写 OnOBN —— 同一个程序两个页面两个名字，看着像跑的不是一个东西。
3. **背景四张图**：必须是 logo.svg / ico.svg / bg1.svg / bg2.svg 这四个。
   另外 CSS 兜底的四角顺序要和 JS 的 corners 一致，否则 JS 没跑起来时
   左下/右下是反的。

⚠️ 剥注释：注释里提到的旧写法会被当成真代码，导致"把改动退回旧写法、
   断言应该变红"的反向验证变成假通过 —— 这个坑在本项目里踩过不止一次。
   本文件对 HTML / CSS / JS 三种注释全部先剥再查。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOGIN = os.path.join(HERE, "..", "src", "templates", "login.html")
WEBUI = os.path.join(HERE, "..", "src", "webui.py")

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


def _strip_c_comments(src: str) -> str:
    """去掉 /* */ 与 // 注释。"""
    out = []
    i, n, q = 0, len(src), None
    while i < n:
        if q:
            if src[i] == "\\":
                out.append(src[i:i + 2]); i += 2; continue
            if src[i] == q:
                q = None
            out.append(src[i]); i += 1; continue
        c = src[i]
        if c in "\"'":
            q = c; out.append(c); i += 1; continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        out.append(c); i += 1
    return "".join(out)


def _html():
    """login.html 去掉 <!-- --> 注释后的正文。"""
    return re.sub(r"<!--.*?-->", "", _read(LOGIN), flags=re.S)


def _css():
    """login.html 内联 <style> 去掉 CSS 注释后的内容。"""
    m = re.search(r"<style>(.*?)</style>", _html(), re.S)
    assert m, "login.html 里没有内联 <style>"
    return _strip_c_comments(m.group(1))


def _js():
    """login.html 里所有 <script> 去掉 JS 注释后的内容。"""
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", _html(), re.S)
    return "\n".join(_strip_c_comments(b) for b in blocks)


def _css_blocks():
    """返回 [(选择器, 声明体)]，只取一层（登录页没有嵌套规则）。"""
    out = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _css()):
        out.append((sel.strip(), body))
    return out


# ------------------------------------------------------- 1. 禁用文本选取

def test_body_disables_selection():
    """整页禁用选取：html/body 必须有 user-select: none。"""
    hit = [b for sel, b in _css_blocks()
           if re.search(r"\bhtml\b", sel) and re.search(r"\bbody\b", sel)
           and re.search(r"user-select\s*:\s*none", b)]
    assert hit, "html/body 上没有 user-select: none，登录页仍可被鼠标拖蓝"


def test_selection_disabled_has_vendor_prefixes():
    """带厂商前缀，否则旧版 Safari / Firefox 上不生效（只有一条标准属性）。"""
    hit = [b for sel, b in _css_blocks()
           if re.search(r"\bhtml\b", sel) and re.search(r"\bbody\b", sel)
           and "user-select" in b]
    assert hit, "没找到禁用选取的规则"
    body = hit[0]
    for pre in ("-webkit-", "-moz-", "-ms-"):
        assert re.search(pre + r"user-select\s*:\s*none", body), \
            "缺 %suser-select: none" % pre


def test_password_input_keeps_selection():
    """输入框必须单独放开：口令要能粘贴、能全选修改。"""
    hit = [b for sel, b in _css_blocks()
           if sel == '.login-card input[type="password"]'
           and re.search(r"user-select\s*:\s*text", b)]
    assert hit, \
        "口令框没放开 user-select —— 一刀切禁掉会让输入框点不出光标、粘贴不进口令"


# ------------------------------------------------------- 2. 版本号旁的品牌名

def _ver_line():
    m = re.search(r'<div class="login-ver">(.*?)</div>', _html(), re.S)
    assert m, "login.html 里没有登录页版本行"
    return m.group(1)


def test_version_line_uses_brand_name():
    """必须是「OnOiduts工业」，与面板左下角一致。"""
    line = _ver_line()
    assert "OnOiduts工业" in line, \
        "版本行品牌名不对：%r（应为 OnOiduts工业 v{{ app_version }}）" % line


def test_version_line_not_abbreviation():
    """不能是缩写 OnOBN —— 那样两个页面两个名字。"""
    line = _ver_line()
    assert "OnOBN" not in line, \
        "版本行用了缩写 OnOBN：%r（应与面板一致写 OnOiduts工业）" % line


def test_version_line_has_version_var():
    assert "{{ app_version }}" in _ver_line(), \
        "版本行没有 app_version，无法确认浏览器里这份是不是刚装的"


def test_brand_name_matches_panel():
    """与 index.html 左下角那行使用同一个品牌名，不许各写各的。"""
    idx = _read(os.path.join(HERE, "..", "src", "templates", "index.html"))
    idx = re.sub(r"<!--.*?-->", "", idx, flags=re.S)
    assert "OnOiduts工业" in idx, "面板里也没有 OnOiduts工业，比对基准不成立"
    assert _ver_line().strip().startswith("OnOiduts工业"), \
        "登录页与面板品牌名不一致"


# ------------------------------------------------------- 3. 背景四张图

def _webui_const(name):
    """取出 webui.py 里 `NAME = ("..." "...")` 拼接后的字符串字面量。"""
    src = _read(WEBUI)
    m = re.search(r"\b" + name + r"\s*=\s*\((.*?)\)", src, re.S)
    assert m, "webui.py 里没有 %s" % name
    parts = re.findall(r'"([^"]*)"', m.group(1))
    return "".join(parts)


def _svg_variant(url):
    """复刻 webui.svg_variant：同目录同文件名，只换扩展名。"""
    if not url or "." not in url.rsplit("/", 1)[-1]:
        return ""
    base, _d, _e = url.rpartition(".")
    return base + ".svg" if base else ""


def test_four_bg_images_are_the_named_svgs():
    """四张图必须是 Logo/logo.svg、Web/OnOBN/ico.svg、bg1.svg、bg2.svg。"""
    want = {
        "bg_logo_svg": "/Logo/logo.svg",
        "bg_ico_svg": "/Web/OnOBN/ico.svg",
        "bg1_svg": "/Web/OnOBN/bg1.svg",
        "bg2_svg": "/Web/OnOBN/bg2.svg",
    }
    const = {
        "bg_logo_svg": "LOGO_URL_DEFAULT",
        "bg_ico_svg": "ICO_URL_DEFAULT",
        "bg1_svg": "BG1_URL_DEFAULT",
        "bg2_svg": "BG2_URL_DEFAULT",
    }
    for var, const_name in const.items():
        got = _svg_variant(_webui_const(const_name))
        assert got.endswith(want[var]), \
            "%s 解析成 %r，应以 %s 结尾" % (const_name, got, want[var])


def test_login_page_uses_all_four_bg_vars():
    """登录页真的把这四张图都挂上了，不能少一张。"""
    html = _html()
    for var in ("bg_logo_svg", "bg_ico_svg", "bg1_svg", "bg2_svg"):
        assert "{{ " + var + " }}" in html, "登录页没有引用 {{ %s }}" % var


def test_exactly_four_bg_images():
    """背景层里正好四张图 —— 多了会叠，少了不够。"""
    m = re.search(r'<div class="login-bg".*?</div>', _html(), re.S)
    assert m, "没有 .login-bg 背景层"
    n = len(re.findall(r"<img\b", m.group(0)))
    assert n == 4, "背景层里有 %d 张 <img>，应为 4 张" % n


# ------------------------------------------- 4. CSS 兜底四角与 JS corners 同序

def _corner_list():
    m = re.search(r"corners\s*=\s*(\[\[.*?\]\])", _js(), re.S)
    assert m, "没找到四角基准 corners"
    pts = re.findall(r"\[\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\]", m.group(1))
    return [(float(a), float(b)) for a, b in pts]


def _bg_class_positions():
    out = {}
    for cls in ("bg-a", "bg-b", "bg-c", "bg-d"):
        m = re.search(r"\.login-bg\s+\." + cls + r"\s*\{(.*?)\}", _css(), re.S)
        assert m, "CSS 里没有 .login-bg .%s 的兜底定位" % cls
        body = m.group(1)
        h = "left" if "left:" in body else ("right" if "right:" in body else "?")
        v = "top" if "top:" in body else ("bottom" if "bottom:" in body else "?")
        out[cls] = (h, v)
    return out


def test_css_fallback_order_matches_js_corners():
    """CSS 兜底的四角顺序要和 corners 一致（左上/右上/左下/右下）。

    JS 会覆盖 left/top 并清掉 right/bottom，所以顺序不一致时并不报错，
    但 JS 没跑起来就会左下/右下颠倒 —— 那种情况下没人会去看代码。
    """
    pos = _bg_class_positions()
    want = [("left", "top"), ("right", "top"), ("left", "bottom"), ("right", "bottom")]
    got = [pos[c] for c in ("bg-a", "bg-b", "bg-c", "bg-d")]
    assert got == want, "CSS 兜底顺序 %s 与 JS corners 的 左上/右上/左下/右下 不符" % got

    pts = _corner_list()
    jsq = [("L" if x < 50 else "R") + ("T" if y < 50 else "B") for x, y in pts]
    cssq = [("L" if h == "left" else "R") + ("T" if v == "top" else "B")
            for h, v in got]
    assert jsq == cssq, "JS 四角 %s 与 CSS 兜底 %s 象限顺序不一致" % (jsq, cssq)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            _check(name, fn)
    bad = [r for r in _results if not r[0]]
    for ok, name, err in _results:
        print("%s %s%s" % ("✅" if ok else "❌", name, ("  → " + err) if err else ""))
    print("\n%d/%d 通过" % (len(_results) - len(bad), len(_results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
