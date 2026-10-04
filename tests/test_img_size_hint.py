# -*- coding: utf-8 -*-
"""QQ markdown 图片的尺寸提示 ——「电脑看得见、手机看不见」的真正根因。

关键推理（别走岔路）：
  电脑端**看得到**图，说明开放平台那一步「下载转存」已经成功了 ——
  转存要是失败，两端都看不到。所以这不是防盗链、也不是域名报备问题，
  而是**客户端渲染**层面的差异。

QQ 的 markdown 图片用的是私有的尺寸提示格式：

    ![#宽px #高px](url)

不是标准 markdown 的 `![alt](url)`。少了尺寸提示，客户端不为图片预留
空间，表现正是"电脑端正常、手机端一片空白"。

依据：
  · QQ 官方插件 openclaw-qqbot 的 formatQQBotMarkdownImage()
    就是拼 `![#${w}px #${h}px](${url})`，探测不到尺寸时回落 512x512。
  · koishi 插件更新日志把"图片必须带尺寸参数（如 #240px #240px）"
    单独作为"markdown 图片不显示"的根因修过一次。

本文件锁住：默认开、格式正确、头像正方形、可关闭、宽高恒为正整数。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import notify as N  # noqa: E402

FAIL = []


def ck(name, cond):
    print(("  [PASS] " if cond else "  [FAIL] ") + name)
    if not cond:
        FAIL.append(name)
    return bool(cond)


# QQ 私有图片语法：![...#宽px #高px...](url)
QQ_IMG_RE = re.compile(r"^!\[[^\]]*#(\d+)px\s+#(\d+)px[^\]]*\]\(([^)\s]+)\)$")


def _reset():
    N.set_size_hint(True, 672, 378, 160)


# ── 1. 默认必须带尺寸提示 ────────────────────────────────────────
def t_default_has_hint():
    _reset()
    md = N._img("https://i0.hdslb.com/bfs/archive/abc.jpg", "video")
    m = QQ_IMG_RE.match(md)
    ck("默认输出是 QQ 尺寸提示格式", bool(m))
    if m:
        ck("默认宽=672", int(m.group(1)) == 672)
        ck("默认高=378", int(m.group(2)) == 378)
        ck("URL 原样保留", m.group(3).endswith("abc.jpg"))


def t_all_kinds_have_hint():
    """七类卡片的图都要带提示，一个都不能漏。"""
    _reset()
    for kind in ("live", "offline", "video", "dynamic", "dynamic_pinned",
                 "top_comment", "season", "up_avatar"):
        md = N._img("https://i0.hdslb.com/bfs/x.jpg", kind)
        ck(f"{kind} 带尺寸提示", bool(QQ_IMG_RE.match(md)))


def t_avatar_square():
    """头像必须是正方形，套 16:9 会变形。"""
    _reset()
    m = QQ_IMG_RE.match(N._img("https://i0.hdslb.com/bfs/x.jpg", "up_avatar"))
    ck("头像是正方形", bool(m) and m.group(1) == m.group(2) == "160")


def t_empty_url():
    _reset()
    ck("空 URL 返回空串", N._img("", "video") == "")


# ── 2. 可关闭（退回标准 markdown） ───────────────────────────────
def t_can_disable():
    """万一尺寸提示在某端反而出问题，要能退回 !![alt](url)。"""
    _reset()
    N.set_size_hint(False, 0, 0, 0)
    md = N._img("https://i0.hdslb.com/bfs/x.jpg", "video")
    ck("关掉后不带 #px", "#" not in md)
    ck("关掉后仍带 alt", "视频封面" in md)
    _reset()


# ── 3. 宽高恒为正整数（0 会让客户端不认） ────────────────────────
def t_positive_ints():
    N.set_size_hint(True, 0, 0, 0)
    m = QQ_IMG_RE.match(N._img("https://i0.hdslb.com/bfs/x.jpg", "video"))
    ck("宽高仍为正整数", bool(m) and int(m.group(1)) > 0 and int(m.group(2)) > 0)
    _reset()


def t_only_width_given():
    """只给宽时高度要按 16:9 自己算，不能是 0。"""
    N.set_size_hint(True, 800, 0, 0)
    m = QQ_IMG_RE.match(N._img("https://i0.hdslb.com/bfs/x.jpg", "video"))
    ck("只给宽时高非 0", bool(m) and int(m.group(2)) > 0)
    ck("只给宽时宽=800", bool(m) and int(m.group(1)) == 800)
    _reset()


# ── 4. 不能被后续处理破坏 ────────────────────────────────────────
def t_squeeze_keeps_img_line():
    """_squeeze 必须把图片行当块级元素保留，不能被当成普通文字并到段落里。"""
    _reset()
    md = N._img("https://i0.hdslb.com/bfs/x.jpg", "video")
    out = N._squeeze("**标题**\n" + md + "\n> **分区**　虚拟主播")
    ck("图片行在 _squeeze 后独立成行", md in out.split("\n"))
    ck("_squeeze 不破坏 #px 语法", "#672px" in out)


def t_img_line_re_matches():
    """IMG_LINE_RE 要能认出带尺寸提示的图片行。"""
    _reset()
    md = N._img("https://i0.hdslb.com/bfs/x.jpg", "video")
    ck("IMG_LINE_RE 匹配尺寸提示格式", bool(N.IMG_LINE_RE.match(md)))


def t_strip_images_removes_it():
    """关图时整行要被剥掉，不能留下 #672px 这种裸文字。"""
    _reset()
    md = N._img("https://i0.hdslb.com/bfs/x.jpg", "video")
    out = N.strip_images("**标题**\n" + md)
    ck("strip_images 剥掉图片行", "#672px" not in out and "x.jpg" not in out)


def t_cover_still_jpg():
    """尺寸提示和 webp→jpg 是两件事，都要生效。"""
    _reset()
    N.set_inline_pic_size(0)
    u = N._cover("https://i0.hdslb.com/bfs/archive/abc.jpg@672w_378h_1c.webp")
    ck("封面仍是 jpg（非 webp）", u.endswith(".jpg") and "webp" not in u)


# ── 5. 面板接线（缺一处就还是会"改了不生效"） ──────────────────
def t_panel_wired():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    js = open(os.path.join(root, "static", "app.js"), encoding="utf-8").read()
    html = open(os.path.join(root, "templates", "index.html"),
                encoding="utf-8").read()
    ck("面板有尺寸提示开关", 'id="cSizeHint"' in html)
    ck("开关回填读后台值", "image_size_hint" in js and "cSizeHint" in js)
    ck("宽高会提交", "image_size_hint_w" in js and "image_size_hint_h" in js)
    ck("改动立即保存", "'cSizeHint'" in js and "saveCfg(true)" in js)

    bot = open(os.path.join(root, "bot.py"), encoding="utf-8").read()
    ck("加入热更新名单", "image_size_hint" in bot)
    ck("bot 有 apply_size_hint", "def apply_size_hint" in bot)


def main():
    print("\n=== QQ markdown 图片尺寸提示 ===")
    _reset()
    for fn in (t_default_has_hint, t_all_kinds_have_hint, t_avatar_square,
               t_empty_url, t_can_disable, t_positive_ints,
               t_only_width_given, t_squeeze_keeps_img_line,
               t_img_line_re_matches, t_strip_images_removes_it,
               t_cover_still_jpg, t_panel_wired):
        fn()
    print("\n" + "=" * 46)
    if FAIL:
        print(f"[FAIL] {len(FAIL)} 项未通过：")
        for n in FAIL:
            print("   - " + n)
        return 1
    print("[OK] 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ── 5. 按原图比例等比（预览不能被拉成 16:9） ──────────────────────
# 尺寸提示是给客户端**预留空间**用的。统一填 16:9 的话，竖图、方图的
# 预览就会被强行拉成 16:9 —— 点开是原图没错，但预览看着是变形的。
def _auto():
    """高度设 0 = 按原图比例自动。"""
    N.set_size_hint(True, 672, 0, 160)


def t_src_size_parses_cdn_param():
    """B站图床 URL 的 @参数里就带着裁好的宽高，也就是原图比例。"""
    ck("解析 @518w_518h", N._src_size("//i0.hdslb.com/bfs/x.jpg@518w_518h_1c.webp") == (518, 518))
    ck("解析 @1080w_1920h", N._src_size("//i0.hdslb.com/bfs/x.jpg@1080w_1920h_1c.webp") == (1080, 1920))
    ck("URL 不带参数时返回 (0,0)", N._src_size("https://i0.hdslb.com/bfs/archive/a.jpg") == (0, 0))
    ck("空串不抛异常", N._src_size("") == (0, 0))
    ck("非字符串不抛异常", N._src_size(None) == (0, 0))


def t_portrait_keeps_ratio():
    """竖图（1080x1920）：等比缩到宽 672，高应约 1192，而不是 378。"""
    _auto()
    w, h = N._hint_size("video", 1080, 1920)
    ck("竖图宽=672", w == 672)
    ck("竖图高按 10:16 等比（约1192）", 1190 <= h <= 1196)
    ck("竖图不是 16:9", h != 378)


def t_square_keeps_ratio():
    """方图：等比后仍应是方的。"""
    _auto()
    w, h = N._hint_size("dynamic", 518, 518)
    ck("方图宽=高", w == h)
    ck("方图不是 16:9", not (w == 672 and h == 378))


def t_no_upscale():
    """原图比上限小就不放大，避免小图被拉糊。"""
    _auto()
    w, h = N._hint_size("top_comment", 320, 240)
    ck("小图用原尺寸", (w, h) == (320, 240))


def t_unknown_ratio_falls_back_169():
    """拿不到原图比例时兜底 16:9（视频封面本来就是这个比例）。"""
    _auto()
    ck("无比例时 16:9", N._hint_size("video", 0, 0) == (672, 378))


def t_explicit_height_wins():
    """用户在 config 里显式给了高，就尊重用户、不做等比。"""
    N.set_size_hint(True, 672, 378, 160)
    ck("显式高时固定用它", N._hint_size("video", 1080, 1920) == (672, 378))
    _auto()


def t_pic_end_to_end():
    """_pic 必须先解析比例再交给 _cover（_cover 会把 @参数剥掉）。"""
    _auto()
    md = N._pic("//i0.hdslb.com/bfs/dyn/x.jpg@1080w_1920h_1c.webp", "top_comment")
    m = QQ_IMG_RE.match(md)
    ck("_pic 输出带提示", bool(m))
    if m:
        ck("_pic 竖图高≈1192", 1190 <= int(m.group(2)) <= 1196)
        ck("_pic 已转 https+jpg", m.group(3).startswith("https://") and m.group(3).endswith(".jpg"))
    md2 = N._pic("//i0.hdslb.com/bfs/dyn/y.jpg@518w_518h_1c.webp", "dynamic")
    m2 = QQ_IMG_RE.match(md2)
    ck("_pic 方图宽=高", bool(m2) and m2.group(1) == m2.group(2))


def t_avatar_never_169():
    _auto()
    ck("头像始终正方形", N._hint_size("up_avatar", 1080, 1920) == (160, 160))


def t_render_dynamic_portrait():
    """动态配图是竖图时，卡片里的提示必须跟着竖。"""
    _auto()
    info = N.__dict__.get("_mk_dyn")
    d = None
    try:
        from bilibili import DynamicInfo
        d = DynamicInfo(text="测试", images=["//i0.hdslb.com/bfs/dyn/x.jpg@1080w_1920h_1c.webp"])
    except Exception:
        pass
    if d is None:
        print("  [SKIP] 构造 DynamicInfo 失败")
        return
    md, _ = N.render_dynamic(d, "测试UP", {})
    # ⚠️ 卡片是多行文本，QQ_IMG_RE 带 ^$ 锚点，必须按行匹配
    m = None
    for _ln in (md or "").split("\n"):
        if QQ_IMG_RE.match(_ln):
            m = QQ_IMG_RE.match(_ln)
            break
    ck("动态卡片带图片提示", bool(m))
    if m:
        ck("动态竖图高≈1192", 1190 <= int(m.group(2)) <= 1196)
