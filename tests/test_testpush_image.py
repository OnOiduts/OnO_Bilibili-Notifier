# -*- coding: utf-8 -*-
"""推送测试的图片链路（v1.35.0：图片**内嵌**在卡片里）。

用户反馈两件事：
 1. 用各类推送测试发出来的消息，该带图的却一张图都没有
 2. 图是插在消息内的，不是单独发出来

根因：
 以前走的是 send_image 单独发一条图 + strip_images 把卡片里的
 ![](url) 剥掉 —— 于是群里是「一条孤零零的图 + 一张没图的卡片」，
 上传一旦失败（沙箱 / 域名没报备）图就彻底消失。

现在：卡片里的 ![](url) 原样发出去（内嵌），不再单独发图。
内嵌必须是端上能拉到的真 URL，所以「用真实数据」默认打开，
保证测试卡片拿得到真实封面 / 配图。

下面把这条链路锁住，防止再退化。
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

import media  # noqa: E402


def _src(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def _api_test_body():
    """取出 /api/test 函数体（到下一个 @app.route 为止）。"""
    w = _src("webui.py")
    i = w.index('@app.route("/api/test"')
    j = w.index("@app.route", i + 10)
    return w[i:j]


def test_make_png_is_valid_png():
    """生成的字节必须是**真 PNG**，否则会被 usable() 判为不可用。"""
    d = media._make_png(seed=1)
    assert d[:8] == b"\x89PNG\r\n\x1a\n", "要有 PNG 签名"
    assert media._looks_png(d)
    assert media.usable(d), "必须过 usable() 才能发出去"
    assert len(d) > 100


def test_make_png_various_seeds():
    for s in range(5):
        assert media.usable(media._make_png(seed=s))


def test_make_png_no_pillow_needed():
    """不能依赖 Pillow —— 它是可选依赖。"""
    saved = sys.modules.pop("PIL", None)
    try:
        assert media.usable(media._make_png(seed=3))
    finally:
        if saved is not None:
            sys.modules["PIL"] = saved


def test_placeholder_url_primes_cache():
    """占位 URL 必须能在短期缓存命中 —— 这样才不走网络。"""
    u = media.local_placeholder("video")
    assert u, "要给出 URL"
    assert u.startswith(media.PLACEHOLDER_HOST)
    data = media.load_tmp(u)
    assert data, "缓存必须命中"
    assert media.usable(data)


def test_placeholder_url_stable_per_kind():
    a = media.local_placeholder("video")
    b = media.local_placeholder("video")
    c = media.local_placeholder("live")
    assert a == b
    assert a != c


def test_placeholder_host_never_resolves_real_site():
    """.invalid 是保留域名，防止哪天真的发起网络请求。"""
    assert media.PLACEHOLDER_HOST.endswith(".invalid")


def test_cover_of_placeholder_data_is_empty():
    """占位数据本身没图 —— 所以真实数据默认要开，否则测试看不到图。"""
    import webui
    for kind in ("video", "season", "live", "offline"):
        info = webui._demo(kind)
        assert not webui.cover_of(kind, info), kind


def test_push_test_does_not_send_image_separately():
    """核心：测试推送不再单独发图，只发一条内嵌图片的 markdown。"""
    body = _api_test_body()
    assert "send_image(" not in body, \
        "又走回单独发图了 —— 那样卡片会被剥成没图的样子"
    # 只发一条 markdown
    assert body.count("send_markdown(") == 1, \
        "应该只有一次 send_markdown，图随卡片一起走"


def test_push_test_keeps_inline_image():
    """只有在全局关图（send_image=false）时才剥掉卡片里的图。"""
    body = _api_test_body()
    assert "strip_images(" in body, "关图时要剥掉"
    i = body.index("strip_images(")
    guard = body[max(0, i - 200):i]
    assert "send_image" in guard, \
        "strip_images 必须只在「关图」分支里，平时不能剥"


def test_use_real_defaults_on():
    """「用真实数据」默认开：占位数据没封面，测试就看不出图通不通。"""
    body = _api_test_body()
    assert 'd.get("real") is not False' in body, \
        "real 默认必须是 True（用 is not False 判）"
    assert "bool(d.get(\"real\"))" not in body, "不能退回默认关"


def test_panel_real_switch_defaults_on():
    appjs = _src("static/app.js")
    assert "real: true" in appjs, "前端默认也要开"
    html = _src("templates/index.html")
    i = html.index('data-opt="real"')
    assert 'class="sw-btn on"' in html[max(0, i - 120):i], \
        "面板上「用真实数据」开关默认要亮着"


def test_render_keeps_inline_image_for_real_content():
    """真实内容渲染出来，卡片里必须带着内嵌图（![alt](url)）。

    ⚠️ alt 不能为空：写成 `![](url)` 时部分解析器不认，
       群里就变成"卡片里明明有图却看不到"。
    """
    from bilibili import DynamicInfo, LiveInfo, SeasonPush, VideoInfo
    from notify import render
    from webui import _apply_mark_md

    def _img_line(md):
        for line in str(md or "").splitlines():
            if line.strip().startswith("![") and "](" in line:
                return line.strip()
        return ""

    cases = [
        ("video", VideoInfo(bvid="BV1", title="t",
                            pic="https://i0.hdslb.com/bfs/a.jpg")),
        ("live", LiveInfo(room_id=1, live_status=1, title="t",
                          cover="https://i0.hdslb.com/bfs/c.jpg")),
        ("season", SeasonPush(bvid="BV1", title="t",
                              pic="https://i0.hdslb.com/bfs/s.jpg",
                              season_title="合集", section="正片")),
        ("dynamic", DynamicInfo(dyn_id="1", title="t", text="x",
                                images=["https://i0.hdslb.com/bfs/d.jpg"])),
    ]
    for kind, info in cases:
        md, _ = render(kind, info, "UP", {})
        assert _img_line(md), kind + " 卡片里没有内嵌图"
        assert not "![](" in md, kind + " 图片 alt 不能为空"
        # 加测试标记也不能把图弄丢
        assert _img_line(_apply_mark_md(md, True)), kind + " 标记后图丢了"


def test_mark_line_stays_above_card_head():
    """「🧪 测试」在卡片标题**上面一行**，不是塞进标题里。"""
    from bilibili import VideoInfo
    from notify import render
    from webui import _apply_mark_md

    md, _ = render("video", VideoInfo(
        bvid="BV1", title="t", pic="https://i0.hdslb.com/bfs/a.jpg"), "UP", {})
    lines = _apply_mark_md(md, True).splitlines()
    assert lines[0].startswith("「🧪 测试」"), lines[0]
    assert lines[1].startswith("**📺"), lines[1]


def test_inline_mode_supported_everywhere():
    """内嵌模式：配置校验要认，正式推送要认。"""
    w = _src("webui.py")
    assert '"inline"' in w, "配置校验不认 inline，面板上设了会被丢掉"
    b = _src("bot.py")
    assert 'img_mode == "inline"' in b, "正式推送没实现内嵌模式"


def test_inline_mode_keeps_card_image():
    """内嵌模式下 cover 置空 → 既不剥图也不单独发。

    走的还是 md_g_no_img = ... if cover else md_g 这条既有逻辑，
    所以只要 cover 为空，卡片就原样带着图发出去。
    """
    b = _src("bot.py")
    i = b.index('img_mode == "inline"')
    seg = b[i:i + 500]
    assert "cover = \"\"" in seg, "内嵌模式必须把 cover 置空"
    # 置空后这两条既有逻辑自动生效
    assert "md_g_no_img = strip_images(md_g) if cover else md_g" in b
    assert "if cover:" in b
