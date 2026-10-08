# -*- coding: utf-8 -*-
"""等级徽章类名必须对得上（B站登录卡 .lv-badge.lv-N）。

⚠️ 这个坑踩过一次：app.js 生成的是 "lv-badge lv-5"（带连字符），
   而 index.html 的官方色覆盖写成了 .lv-badge.lv5（无连字符）——
   六条覆盖**一条都没匹配上**，回落到 style.css 的 .lv-5 粉色渐变；
   下面那行又把渐变图干掉、底色也没补回来，徽章变成透明底白字。
   表现：同一个 LV5，侧边栏卡是官方橙红、B站登录卡却没底色。

以下断言守住「JS 生成的类名」与「CSS 覆盖的类名」必须一致。
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(BASE, "src", "static", "app.js")
INDEX = os.path.join(BASE, "src", "templates", "index.html")
STYLE = os.path.join(BASE, "src", "static", "style.css")

OFFICIAL = {
    0: "#C0C0C0", 1: "#C0C0C0", 2: "#8BD29B",
    3: "#7BCDEF", 4: "#FEBB8B", 5: "#EE672A", 6: "#F04C49",
}


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _strip_comments(s):
    """去掉注释后再断言。

    ⚠️ 这个项目的坑：注释里提到的词会被当成真代码，让反向验证变成假通过。
    这里必须剥掉 /* */ 与 // 注释。
    """
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?m)//.*$", "", s)
    return s


def test_js_emits_hyphen_class():
    """app.js 生成的是 lv-badge lv-N（带连字符）。"""
    js = _strip_comments(_read(APP_JS))
    assert "lv-badge lv-" in js, (
        "app.js 应以 'lv-badge lv-' + 数字 生成类名（带连字符）"
    )


def test_css_overrides_all_levels_with_hyphen():
    """index.html 里 0~6 每一级都要有 .lv-badge.lv-N 的覆盖。"""
    html = _strip_comments(_read(INDEX))
    for n in range(7):
        sel = ".lv-badge.lv-%d" % n
        assert sel in html, "缺少覆盖选择器 %s" % sel


def test_css_overrides_use_official_colors():
    """每一级的底色必须是官方色，不是 style.css 那套渐变。"""
    html = _strip_comments(_read(INDEX))
    for n in range(2, 7):
        m = re.search(r"\.lv-badge\.lv-%d\s*\{[^}]*\}" % n, html)
        assert m, "找不到 .lv-badge.lv-%d 规则体" % n
        body = m.group(0)
        assert OFFICIAL[n].lower() in body.lower(), (
            ".lv-badge.lv-%d 底色应为官方色 %s" % (n, OFFICIAL[n])
        )


def test_override_beats_legacy_gradient():
    """style.css 里有 .lv-N 渐变，覆盖必须比它更具体（两个类 > 一个类）。"""
    css = _strip_comments(_read(STYLE))
    assert re.search(r"\.lv-5\s*\{", css), "style.css 里应存在旧规则 .lv-5"
    html = _strip_comments(_read(INDEX))
    # 两个类的选择器（.lv-badge.lv-5）特异性高于单个类（.lv-5），能压住旧渐变
    assert ".lv-badge.lv-5" in html


def test_no_stale_hyphenless_override():
    """不许再出现无连字符的 .lv-badge.lv5 这种写法（匹配不上 JS）。"""
    html = _strip_comments(_read(INDEX))
    bad = re.findall(r"\.lv-badge\.lv\d", html)
    assert not bad, "发现无连字符的失效选择器：%s" % bad


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            bad += 1
            print("FAIL", fn.__name__, "->", e)
    print("\n%d/%d" % (len(fns) - bad, len(fns)))
    sys.exit(1 if bad else 0)
