# -*- coding: utf-8 -*-
"""内嵌图片的 URL 规范化 ——「电脑看得见、手机看不见」的处理。

背景：inline 模式下 ![](url) 留在卡片里，由**开放平台下载转存**再发给
客户端（官方 markdown 文档原话："开放平台会下载转存该资源"）。

这里有两个**互相独立**的坑，别混为一谈：

  1. 格式（硬伤）：B站图床默认给的是 `@672w_378h_1c.webp` —— **webp**，
     手机 QQ 对 webp 的支持明显差于电脑端。QQ 只收 png/jpg，
     所以 **webp/gif → jpg 无条件生效**。
  2. 尺寸（可选项）：去掉 @ 参数 = 原图，动辄 1920×1080 好几 MB，
     加载可能失败；但压小了画质会糊。

用户明确要求**不压缩**，所以默认 = 原图不压缩（INLINE_PIC_W = 0），
只做格式转换；想压就在 config.yaml 设 image_inline_width: 672。
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import notify as N  # noqa: E402

FAIL = []


def ck(name, cond):
    print(("  [PASS] " if cond else "  [FAIL] ") + name)
    if not cond:
        FAIL.append(name)
    return bool(cond)


def _p(name):
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", name)


def _default():
    """把尺寸恢复成默认（不压缩）。"""
    N.set_inline_pic_size(0)


# ── 1. 默认：不压缩，但格式必须是 jpg ────────────────────────────
def t_default_no_compress():
    """核心诉求：默认不压缩，用原图。"""
    _default()
    u = "https://i0.hdslb.com/bfs/archive/abc.jpg"
    ck("默认原图不带 CDN 参数", N._cover(u) == u)


def t_default_strips_bili_webp_param():
    """B站自带的 @...webp 参数要去掉（因为那是 webp，手机不支持）。

    去掉之后按"不压缩"策略**不再补**尺寸参数，但扩展名仍是 jpg。
    """
    _default()
    out = N._cover("https://i0.hdslb.com/bfs/archive/abc.jpg@672w_378h_1c.webp")
    ck("去掉了 webp 参数", "@" not in out)
    ck("结果是 jpg", out.endswith(".jpg"))
    ck("域名路径保留", out == "https://i0.hdslb.com/bfs/archive/abc.jpg")


def t_webp_suffix_always_jpg():
    """格式转换无条件生效：webp / gif 都要转成 jpg。"""
    _default()
    ck("webp 后缀转 jpg",
       N._cover("https://i0.hdslb.com/bfs/x.webp").endswith(".jpg"))
    ck("gif 后缀转 jpg", ".gif" not in N._cover("https://i0.hdslb.com/bfs/x.gif"))
    ck("结果无 webp",
       ".webp" not in N._cover("https://i0.hdslb.com/bfs/x.webp@100w.webp"))


def t_http_upgraded():
    """http 开头要升级成 https（否则会被白名单整条丢掉）。"""
    _default()
    out = N._cover("http://i0.hdslb.com/bfs/archive/abc.jpg")
    ck("http 升级为 https", out.startswith("https://"))
    ck("升级后不压缩", "@" not in out)


def t_protocol_relative():
    _default()
    out = N._cover("//i0.hdslb.com/bfs/archive/abc.jpg")
    ck("补上 https", out.startswith("https://i0.hdslb.com"))


# ── 2. 头像不能套 16:9 ───────────────────────────────────────────
def t_avatar_not_scaled():
    """头像原图本就很小，套 16:9 参数会把方头像裁变形。"""
    _default()
    out = N._cover("https://i0.hdslb.com/bfs/face/abc.webp", scale=False)
    ck("头像不加缩放参数", "@" not in out)
    ck("头像仍是 jpg", out.endswith(".jpg"))
    ck("头像非空", bool(out))
    # 就算开了压缩，头像那一路也仍然不压
    N.set_inline_pic_size(672)
    try:
        ck("开了压缩头像也不压",
           "@" not in N._cover("https://i0.hdslb.com/bfs/face/abc.webp",
                               scale=False))
    finally:
        _default()


def t_subscribe_card_avatar_untouched():
    from notify import render_subscribe
    _default()
    md, _ = render_subscribe("小狐狸", 12345,
                             "https://i0.hdslb.com/bfs/face/abc.jpg", "直播")
    img = [ln for ln in md.splitlines() if ln.startswith("![")]
    ck("订阅卡片有图", bool(img))
    if img:
        ck("卡片里的头像没被裁", "@" not in img[0])


# ── 3. 尺寸可配置：默认 0，设了才压 ──────────────────────────────
def t_size_applied():
    N.set_inline_pic_size(672)
    try:
        out = N._cover("https://i0.hdslb.com/bfs/archive/abc.jpg")
        ck("宽度生效", "@672w_378h_1c.jpg" in out)
        ck("压完是 jpg", out.endswith(".jpg"))
    finally:
        _default()
    ck("恢复默认后不压",
       "@" not in N._cover("https://i0.hdslb.com/bfs/archive/abc.jpg"))


def t_size_custom_width():
    N.set_inline_pic_size(480)
    try:
        out = N._cover("https://i0.hdslb.com/bfs/archive/abc.jpg")
        ck("自定义宽度生效", "@480w_270h_1c.jpg" in out)
    finally:
        _default()


def t_set_size_bad_input_no_crash():
    for bad in (None, "", "abc", [], -5):
        N.set_inline_pic_size(bad)  # type: ignore[arg-type]
    _default()
    ck("非法输入不崩且能恢复",
       "@" not in N._cover("https://i0.hdslb.com/bfs/a.jpg"))


def t_apply_from_cfg_default_zero():
    """配置没写这一项时，必须是不压缩（0），不能沿用旧默认 672。"""
    import bot as B
    B.apply_inline_pic_size({})
    ck("cfg 缺省 = 不压缩",
       "@" not in N._cover("https://i0.hdslb.com/bfs/a.jpg"))
    B.apply_inline_pic_size({"image_inline_width": 320})
    try:
        ck("从 cfg 应用宽度",
           "@320w_180h_1c.jpg" in N._cover("https://i0.hdslb.com/bfs/a.jpg"))
    finally:
        B.apply_inline_pic_size({})
    B.apply_inline_pic_size({"image_inline_width": "oops"})
    ck("cfg 非法值不崩", True)
    B.apply_inline_pic_size({})
    # 上限钳制
    B.apply_inline_pic_size({"image_inline_width": 99999})
    ck("超上限被钳制",
       "@1920w_" in N._cover("https://i0.hdslb.com/bfs/a.jpg"))
    B.apply_inline_pic_size({})


# ── 4. 域名边界 ──────────────────────────────────────────────────
def t_non_hdslb_no_param():
    """白名单内但非 hdslb 的图床（如 akamaized）不加 CDN 参数。

    B站的 @参数只对 hdslb 图床有效，套到别的域名上可能取不到内容。
    """
    _default()
    out = N._cover("https://i0.hdslb.com.akamaized.net/bfs/x.jpg")
    ck("akamaized 放行", bool(out))
    ck("akamaized 不加参数", "@" not in out)
    # 就算开了压缩也不加
    N.set_inline_pic_size(672)
    try:
        ck("开了压缩 akamaized 也不加",
           "@" not in N._cover("https://i0.hdslb.com.akamaized.net/bfs/x.jpg"))
    finally:
        _default()


def t_blocked_host_dropped():
    _default()
    ck("非白名单丢弃", N._cover("https://evil.com/a.jpg") == "")
    ck("空值安全", N._cover("") == "")


def t_no_double_param():
    """反复规范化不能叠出两个 @。"""
    N.set_inline_pic_size(672)
    try:
        u = "https://i0.hdslb.com/bfs/x.jpg@672w_378h_1c.webp"
        once = N._cover(u)
        twice = N._cover(once)
        ck("不叠加参数", twice.count("@") == 1)
        ck("幂等", once == twice)
    finally:
        _default()


# ── 5. 各类型封面都走同一条路 ────────────────────────────────────
def t_cover_of_all_kinds():
    class I:
        cover = "https://i0.hdslb.com/bfs/live/a.webp"
        pic = "https://i0.hdslb.com/bfs/archive/b.webp"
        images = ["https://i0.hdslb.com/bfs/dynamic/c.webp"]
        face = "https://i0.hdslb.com/bfs/face/d.webp"

    i = I()
    _default()
    for kind in ("live", "video", "season", "dynamic", "subscribe"):
        out = N.cover_of(kind, i)
        ck(f"{kind} 有图", bool(out))
        ck(f"{kind} 非 webp", out and ".webp" not in out)
    ck("subscribe 头像不带参数", "@" not in (N.cover_of("subscribe", i) or ""))
    ck("live 封面默认不压缩", "@" not in (N.cover_of("live", i) or ""))
    ck("live 封面是 jpg", (N.cover_of("live", i) or "").endswith(".jpg"))


# ── 6. 源码注释不再自相矛盾 ──────────────────────────────────────
def t_comments_not_contradictory():
    """notify 里不该再留"给原图最稳"这种与实现相反的旧结论。"""
    src = io.open(_p("notify.py"), encoding="utf-8").read()
    ck("不再声称原图最稳", "原图 URL** 最稳" not in src)
    ck("说明了格式与尺寸的区别", "手机" in src and "hdslb.com" in src)


def main():
    print("=== 内嵌图片 URL 规范化（默认不压缩）===")
    for fn in (
        t_default_no_compress,
        t_default_strips_bili_webp_param,
        t_webp_suffix_always_jpg,
        t_http_upgraded,
        t_protocol_relative,
        t_avatar_not_scaled,
        t_subscribe_card_avatar_untouched,
        t_size_applied,
        t_size_custom_width,
        t_set_size_bad_input_no_crash,
        t_apply_from_cfg_default_zero,
        t_non_hdslb_no_param,
        t_blocked_host_dropped,
        t_no_double_param,
        t_cover_of_all_kinds,
        t_comments_not_contradictory,
    ):
        print(f"--- {fn.__name__}")
        try:
            fn()
        except Exception as e:
            ck(f"{fn.__name__} 未抛异常", False)
            print(f"      {type(e).__name__}: {e}")
    print("\n结果:", "全部通过 ✅" if not FAIL else f"存在失败 ❌ {FAIL}")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
