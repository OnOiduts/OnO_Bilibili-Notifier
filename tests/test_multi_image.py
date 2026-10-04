# -*- coding: utf-8 -*-
"""一条动态/评论带多张图时，要全都发出来（v1.46.0）。

以前 render_dynamic / render_top_comment 都只取 `imgs[0]` ——
一条 9 图的动态在群里只显示第一张，其余全丢。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import notify as N  # noqa: E402
import bilibili as B  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
IMG_RE = re.compile(r"^!\[[^\]]*\]\([^)\s]+\)$")


def _dyn(n):
    d = B.DynamicInfo()
    d.title = "测试标题"
    d.text = "正文"
    d.images = [f"https://i0.hdslb.com/bfs/dyn/{i}.jpg@1080w_1920h_1c.webp"
                for i in range(n)]
    return d


def _img_lines(md):
    return [l for l in md.split("\n") if IMG_RE.match(l.strip())]


def setup_module():
    N.PROBE_SIZE_ON = False      # 测试不联网
    N.SIZE_HINT_ON = True
    N.IMG_MAX_PER_MSG = 0


def test_multi_image_all_sent():
    """4 图动态 → 4 行图片，不是只有 1 行。"""
    md, _ = N.render_dynamic(_dyn(4), "星瞳Official", {})
    assert len(_img_lines(md)) == 4


def test_multi_image_respects_limit():
    """image_max_per_msg=2 时只发 2 张。"""
    old = N.IMG_MAX_PER_MSG
    try:
        N.IMG_MAX_PER_MSG = 2
        md, _ = N.render_dynamic(_dyn(5), "星瞳Official", {})
        assert len(_img_lines(md)) == 2
    finally:
        N.IMG_MAX_PER_MSG = old


def test_no_blank_line_between_images():
    """多张图之间只换行、不空行（用户对空行有明确要求）。"""
    md, _ = N.render_dynamic(_dyn(3), "星瞳Official", {})
    lines = md.split("\n")
    idx = [i for i, l in enumerate(lines) if IMG_RE.match(l.strip())]
    assert len(idx) == 3
    for a, b in zip(idx, idx[1:]):
        assert b - a == 1, "相邻图片行之间不应出现空行"


def test_image_block_still_owns_its_own_para():
    """整块图片前后仍留空行 —— 紧贴标题会被当成行内内容而看不到图。"""
    md, _ = N.render_dynamic(_dyn(2), "星瞳Official", {})
    lines = md.split("\n")
    idx = [i for i, l in enumerate(lines) if IMG_RE.match(l.strip())]
    assert idx[0] >= 1 and lines[idx[0] - 1].strip() == ""
    assert lines[idx[-1] + 1].strip() == ""


def test_single_image_unchanged():
    """单图动态仍然是 1 行（不回归）。"""
    md, _ = N.render_dynamic(_dyn(1), "星瞳Official", {})
    assert len(_img_lines(md)) == 1


def test_comment_multi_image():
    """评论也能带多张图。"""
    c = {"content": "评论正文", "url": "https://x",
         "images": [f"https://i0.hdslb.com/bfs/cmt/{i}.jpg" for i in range(3)]}
    md, _ = N.render_top_comment(c, "星瞳Official", {})
    assert len(_img_lines(md)) == 3


def test_set_size_hint_applies_max_per_msg():
    """0 是合法值（不限），不能因为 falsy 被当成"没给配置"。"""
    N.set_size_hint(True, 672, 0, 160, 1200, 0)
    assert N.IMG_MAX_PER_MSG == 0
    N.set_size_hint(True, 672, 0, 160, 1200, 3)
    assert N.IMG_MAX_PER_MSG == 3
    # 没传这个参数（默认 -1）时不应改动
    N.set_size_hint(True, 672, 0, 160, 1200)
    assert N.IMG_MAX_PER_MSG == 3
    N.set_size_hint(True, 672, 0, 160, 1200, 0)


def test_dyn_image_cap_is_nine():
    """数据层取图上限是 9（B站一条动态最多 9 张）。"""
    assert B.DYN_IMG_MAX == 9


def test_panel_wiring():
    """面板：输入框存在 + 登记进 CFG_FIELDS + 会进提交体。"""
    html = open(os.path.join(ROOT, "templates", "index.html"),
                encoding="utf-8").read()
    js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
    assert 'id="cImgMax"' in html
    assert "'cImgMax'" in js                       # CFG_FIELDS 登记
    assert "image_max_per_msg:" in js              # 进提交体
    assert "c.image_max_per_msg" in js             # 回填


def test_backend_wiring():
    """后端：state 返回 + 保存接收 + 热更新传到渲染层。"""
    webui = open(os.path.join(ROOT, "webui.py"), encoding="utf-8").read()
    bot = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
    assert '"image_max_per_msg"' in webui
    assert '("image_max_per_msg", 0, 9)' in webui
    assert 'c.get("image_max_per_msg", -1)' in bot
