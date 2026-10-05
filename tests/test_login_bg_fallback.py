#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录页背景：降级链 + 内联四角定位 + 内置兜底图形。

为什么需要这个测试
------------------
用户反馈「左下角和右下角没有图」。查出来是两个真 bug：

  1. **降级链等于没做**。bg1 / bg2 的默认地址被直接写成了 `.svg`，
     于是 <img src="...bg1.svg" onerror="..('...bg1.svg')"> ——
     回退地址**就是它自己**。svg 取不到时用同一个取不到的地址再试一次，
     再失败就隐藏。表现正是那两个角空着。

  2. **远程图一取不到，角就空了**。logo / ico 的回退走本地接口（还能救），
     两张装饰图只能靠图床。CDN 交替 502 / 超时是这个项目的常态，
     所以必须有一层**不联网**的兜底。

修法是三级降级：svg → 同名 png → 内置 data URI 图形 → 才隐藏。
并且把四角定位写进 <img> 的 inline style（CSS 之外的第二层兜底）。

⚠️ 本文件所有断言都做过反向验证：把改动退回旧写法，断言必须变红。
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


def _html():
    return _read(LOGIN)


def _py():
    return _read(WEBUI)


def _bg_defaults():
    """取出 BG1_URL_DEFAULT / BG2_URL_DEFAULT 的实际字符串。"""
    src = _py()
    out = {}
    for key in ("BG1_URL_DEFAULT", "BG2_URL_DEFAULT"):
        m = re.search(key + r"\s*=\s*\((.*?)\)\s*\n", src, re.S)
        assert m, "webui.py 里没找到 " + key
        parts = re.findall(r'"([^"]*)"', m.group(1))
        out[key] = "".join(parts)
    return out


def _style_map(style: str):
    """inline style 解析成 {属性: 值}。"""
    out = {}
    for item in style.split(";"):
        if ":" in item:
            k, v = item.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _img_attrs(cls):
    """取某个背景 <img> 的 style 与 onerror。"""
    m = re.search(r'<img class="bg-' + cls + r'\b[^>]*>', _html(), re.S)
    assert m, "登录页里没有 bg-" + cls
    tag = m.group(0)
    style = re.search(r'style="([^"]*)"', tag)
    onerror = re.search(r'onerror="([^"]*)"', tag)
    return (style.group(1) if style else ""), (onerror.group(1) if onerror else "")


# ---------------------------------------------------- 1. 降级链不能是自己
def test_bg_defaults_are_png():
    """BG1/BG2 默认地址必须是 .png —— 才能推出真正的 .svg 与 .png 两级。"""
    d = _bg_defaults()
    for k, v in d.items():
        assert v.endswith(".png"), \
            "%s 必须以 .png 结尾（现在是 %s）：写 .svg 会让回退地址等于自己" % (k, v)


def test_fallback_url_differs_from_src():
    """降级链核心：回退地址不能和 src 是同一个字符串。"""
    src = _py()
    # svg_variant(...) 得到 svg，png 用原值 —— 两者必须不同源
    assert "svg_variant(_bg1_remote_url())" in src, "bg1_svg 应由 svg_variant 换算"
    assert "svg_variant(_bg2_remote_url())" in src, "bg2_svg 应由 svg_variant 换算"
    d = _bg_defaults()
    for k, v in d.items():
        svg = v[:-4] + ".svg"
        assert svg != v, "%s 的 svg 与 png 不能相同" % k


def test_deco_images_have_png_fallback():
    """两张装饰图的第一级回退必须是 png 地址。

    ⚠️ 断言改过：原来解析的是内联 onerror 属性里的 bgFallback(this, '...')，
    而内联事件属性会被 CSP 的 script-src 拦掉（只有带随机数的脚本块能执行），
    现在改为 data-fb 属性承载地址、由捕获阶段的监听读取。
    """
    h = _html()
    for cls, var in (("c", "bg1_png"), ("d", "bg2_png")):
        pat = 'class="bg-' + cls + '[^"]*"[^>]*?data-fb="([^"]*)"'
        m = re.search(pat, h, re.S)
        assert m, ("bg-" + cls + " 没有 data-fb 回退地址")
        fb = m.group(1)
        good = (("{{ " + var + " }}") in fb or fb.endswith(".png")
                or fb.startswith("/api/"))
        assert good, ("bg-" + cls + " 的回退地址应是 png 变量（现在是 " +
                      fb + "）")


# ---------------------------------------------------- 2. 内置兜底图形
def test_builtin_shapes_exist():
    """必须有不联网的内置图形，四个角各一个。"""
    h = _html()
    n = h.count("data:image/svg+xml")
    assert n >= 4, "内置兜底图形少于 4 个（现在 %d 个）" % n


def test_bgfallback_takes_third_arg():
    """降级必须到第三级（内置图形），否则取不到还是直接消失。

    ⚠️ 断言改过：原来是按 function bgFallback(...) 的参数个数判断，
    现在降级逻辑在捕获阶段的监听里，按 data-fb-step 的三级分支判断。
    """
    h = _html()
    assert "document.addEventListener('error'" in h, \
        "没有用捕获阶段监听接管图片降级"
    tail = h[h.index("document.addEventListener('error'"):]
    cut = tail.index("}, true);")
    js = tail[0:(cut + len("}, true);"))]
    assert js.count("data-fb-step") >= 3, \
        "降级分支不足三级：%d 处" % js.count("data-fb-step")
    assert "display = 'none'" in js, "三级都失败后应隐藏，不留坏图"


def test_each_image_has_its_own_shape():
    """四张图各用一个内置图形，全用同一个会显得重复。"""
    h = _html()
    idx = re.findall(r'data-shape="(\d)"', h)
    assert len(set(idx)) == 4, \
        "内置图形没有四张各异：%s" % idx


# ---------------------------------------------------- 3. 内联四角定位
def test_inline_style_anchors_each_corner():
    """每个 <img> 自带 inline 定位 —— CSS 之外的第二层兜底。"""
    for cls in ("a", "b", "c", "d"):
        style, _oe = _img_attrs(cls)
        has_h = ("left:" in style) or ("right:" in style)
        has_v = ("top:" in style) or ("bottom:" in style)
        assert has_h and has_v, \
            "bg-%s 的 inline style 缺横/纵向定位：%s" % (cls, style)


def test_inline_corners_are_four_distinct():
    """四个 inline 定位必须正好是 左上/右上/左下/右下。"""
    combos = set()
    for cls in ("a", "b", "c", "d"):
        style, _oe = _img_attrs(cls)
        sm = _style_map(style)
        h = "left" if sm.get("left", "auto") != "auto" else "right"
        v = "top" if sm.get("top", "auto") != "auto" else "bottom"
        combos.add((h, v))
    assert combos == {("left", "top"), ("right", "top"),
                      ("left", "bottom"), ("right", "bottom")}, \
        "四张图的 inline 定位不是四个不同的角：%s" % sorted(combos)


def test_inline_style_clears_opposite_side():
    """设 left 时必须把 right 置 auto，否则两边同时生效会互相打架。"""
    for cls in ("a", "b", "c", "d"):
        style, _oe = _img_attrs(cls)
        sm = _style_map(style)
        if sm.get("left", "auto") != "auto":
            assert sm.get("right") == "auto", "bg-%s 用 left 定位却没把 right 置 auto" % cls
        if sm.get("right", "auto") != "auto":
            assert sm.get("left") == "auto", "bg-%s 用 right 定位却没把 left 置 auto" % cls
        if sm.get("top", "auto") != "auto":
            assert sm.get("bottom") == "auto", "bg-%s 用 top 定位却没把 bottom 置 auto" % cls
        if sm.get("bottom", "auto") != "auto":
            assert sm.get("top") == "auto", "bg-%s 用 bottom 定位却没把 top 置 auto" % cls


# ---------------------------------------------------- 4. 版本行带构建戳
def test_version_line_no_build_marker():
    """版本行回归「品牌 + 版本」，不再挂构建戳。

    曾几何时版本号长期停在 2.3.3，为区分"装的是哪一份登录页"在版本号后面
    加过构建戳「· 背景4角随机」。四角随机确认生效后按用户要求移除 ——
    版本行的职责回到一件事：确认页面版本 == 刚装的版本。

    ⚠️ 这条断言守的是"别再挂回来"：附加文字会让版本行变成信息杂烩，
       也让按前缀匹配的检查（见 test_login_page.test_version_line_shows_current）
       失去意义。
    """
    m = re.search(r'class="login-ver">([^<]*)<', _html())
    assert m, "没找到版本行"
    assert "背景4角" not in m.group(1), \
        "版本行不该再挂构建戳（现在是 %s）" % m.group(1)
    assert m.group(1).strip() == "OnOiduts工业 v{{ app_version }}", \
        "版本行应只有品牌与版本（现在是 %s）" % m.group(1)


def test_version_line_brand():
    m = re.search(r'class="login-ver">([^<]*)<', _html())
    assert m and "OnOiduts工业" in m.group(1), \
        "版本行品牌名必须是 OnOiduts工业（现在是 %s）" % (m.group(1) if m else "无")


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
