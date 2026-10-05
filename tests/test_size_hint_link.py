# -*- coding: utf-8 -*-
"""图片尺寸提示 —— 高度上限 + 面板进程联动。

覆盖两件事：
 1. 超长图（672x2225）按 image_size_hint_max_h 等比压到上限内，比例不变
 2. 面板进程渲染测试卡片前必须按磁盘配置刷一次渲染层
    （否则"改了没反应"——热更新只在 bot 进程跑）
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import notify as N


def _setup(on=True, w=672, h=0, avatar=160, max_h=1200):
    N.set_size_hint(on, w, h, avatar, max_h)


def test_max_h_default_is_1200():
    assert N.SIZE_HINT_MAX_H == 1200


def test_long_image_squeezed_to_max_h():
    """672x2225 的超长图 → 高度压到 1200，宽按比例缩小。"""
    _setup()
    # 1080x3575 → 宽 672 时高 = round(672*3575/1080) = 2224
    w, h = N._hint_size("dynamic", 1080, 3575)
    assert h == 1200, h
    assert w == int(round(672 * 1200 / 2224.0)), w
    # 比例保持一致（允许 1px 取整误差）
    assert abs((w / float(h)) - (1080 / 3575.0)) < 0.01


def test_normal_image_not_affected():
    """普通 16:9 封面不该被最大高度影响。"""
    _setup()
    w, h = N._hint_size("video", 1920, 1080)
    assert (w, h) == (672, 378), (w, h)


def test_portrait_under_max_h_untouched():
    """竖图高度没超上限 → 原样。"""
    _setup()
    w, h = N._hint_size("dynamic", 1080, 1920)
    assert (w, h) == (672, 1195), (w, h)


def test_max_h_zero_means_unlimited():
    _setup(max_h=0)
    w, h = N._hint_size("dynamic", 1080, 3575)
    assert h == 2224, h


def test_max_h_applies_to_explicit_h():
    """用户显式给的高度也受上限约束。"""
    _setup(h=2225, max_h=1200)
    w, h = N._hint_size("dynamic", 1080, 3575)
    assert h == 1200, h


def test_set_size_hint_accepts_max_h():
    _setup(max_h=800)
    assert N.SIZE_HINT_MAX_H == 800
    _setup(max_h=-1)      # -1 = 不改，保持上次的值
    assert N.SIZE_HINT_MAX_H == 800


def test_img_line_format_unchanged():
    """关掉开关 → 标准 markdown；开着 → 必须带 # 和空格。"""
    _setup(on=False)
    assert N._img("https://x/y.jpg", "video") == "![视频封面](https://x/y.jpg)"
    _setup(on=True)
    line = N._img("https://x/y.jpg", "video", 1920, 1080)
    assert line == "![#672px #378px](https://x/y.jpg)", line


# ── 面板进程联动 ──────────────────────────────────────────────
SRC = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src")


def _read(p):
    # ⚠️ 原来直接用相对路径 'webui.py'，只有把工作目录切到 src/ 才找得到。
    #    从仓库根跑就全是 FileNotFoundError —— 这些用例其实一直没真跑过。
    if not os.path.isabs(p):
        p = os.path.join(SRC, p)
    return io.open(p, encoding="utf-8").read()


def test_apply_render_cfg_exists_in_webui():
    src = _read("webui.py")
    assert "def _apply_render_cfg" in src
    # 必须在 api_test 里被调用
    i = src.find("def api_test():")
    assert i != -1
    assert "_apply_render_cfg(cfg)" in src[i:i + 1500]


def test_apply_render_cfg_sets_hint():
    """面板进程：把配置灌进去后，渲染输出必须跟着变。"""
    _setup(on=True, w=672)
    import importlib
    webui = importlib.import_module("webui")
    webui._apply_render_cfg({"image_size_hint": False})
    assert N.SIZE_HINT_ON is False
    assert N._img("https://x/y.jpg", "video", 1920, 1080) == \
        "![视频封面](https://x/y.jpg)"
    webui._apply_render_cfg({"image_size_hint": True,
                             "image_size_hint_w": 400})
    assert N.SIZE_HINT_ON is True
    assert N._img("https://x/y.jpg", "video", 1920, 1080) == \
        "![#400px #225px](https://x/y.jpg)"
    _setup()   # 复原，别影响别的用例


def test_hot_key_includes_max_h():
    src = _read("bot.py")
    assert "image_size_hint_max_h" in src


def test_webui_state_and_save_include_max_h():
    src = _read("webui.py")
    assert '"image_size_hint_max_h"' in src
    assert "image_size_hint_max_h" in src


def test_panel_has_max_h_input():
    html = _read("templates/index.html")
    assert 'id="cHintMaxH"' in html
    js = _read("static/app.js")
    for needle in ("'cHintMaxH'", "image_size_hint_max_h"):
        assert needle in js


def test_backend_accepts_zero_max_h():
    """0 = 不限制，不能被钳成 1。"""
    src = _read("webui.py")
    m = re.search(r'for _k, _dflt, _mx in \(\("image_size_hint_w".*?\)\):',
                  src, re.S)
    assert m, "找不到尺寸提示的保存白名单"
    assert '"image_size_hint_max_h", 1200, 4096' in m.group(0)
    assert 'max(0 if _k in ("image_size_hint_h",\n' in src or \
        'image_size_hint_max_h' in src.split("cfg[_k] = max(")[1][:200]
