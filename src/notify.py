# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""消息渲染：把 B 站数据变成 QQ 官方 Markdown 样式的卡片。

排版依据是 QQ 开放平台官方 Markdown 能力说明，主要有这些约束/特性：

  1. 自定义 Markdown（msg_type=2）在群聊、单聊已开放给所有机器人，无需申请模板。
  2. 支持：标题(# ##)、加粗、斜体、删除线、链接、图片、有序/无序列表、
     嵌套列表、块引用(>)、水平分割线(---)。
  3. 「换多行」要用零宽空格 \\u200B 单独成行，直接写 \\n\\n 会被压缩掉
     —— 这是消息挤成一坨的真正原因。
  4. 「列表前是普通文本，则需要在列表前用空行隔开，否则无法识别」。
  5. 图片必须是公网可访问的 URL，平台会下载转存。
  6. 内嵌格式（<@everyone> 等）只在纯文本 content 里生效，markdown 里不生效。
     ⚠️ 且 @everyone 官方仅「文字子频道」可用，群聊不支持 —— 本项目的
        @全体 功能已在 v1.18.0 整个移除。

参考：https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html
"""
import re
import time
from urllib.parse import urlparse

from bilibili import DynamicInfo, LiveInfo, VideoInfo
from security import allowed_image_url

# 全角空格，用于块引用内的对齐（半角空格在 QQ 里会被吃掉）
INDENT = "　　"

# 零宽空格。⚠️ 仅作"兼容常量"保留，消息正文里**不再使用**：
#   用它单独撑一行就是用户说的"换空的一行"，v1.31.21 起全部去掉。
ZWSP = "​"

# 换行策略（v1.31.21）：
#   · v1.7.0 之前：每段之间都插零宽空格行 → QQ 里一堆空行、消息被拉长
#   · v1.7.0：只在标题下留一个空行 → 仍有"空的一行"
#   · 现在：全程只用单个 \n，不出现任何形式的空行。
#     块引用(>) 是块级标记、可以中断段落，不需要前导空行
#     （官方强调"需要前导空行"的是**列表**，不是块引用）。
SEP = "\n"

# 独立成行的图片：`![alt](url)`。
# ⚠️ alt **不要留空**：部分解析器对 `![](url)` 的处理不一致，
#    给个中文 alt 更稳（图万一取不到，还会显示 alt 文字而不是一片空白）。
IMG_LINE_RE = re.compile(r"^!\[[^\]]*\]\([^)\s]+\)$")

# 各类卡片里图片的 alt 文本
IMG_ALT = {
    "live": "直播封面",
    "offline": "直播封面",
    "video": "视频封面",
    "dynamic": "动态图片",
    "dynamic_pinned": "动态图片",
    "top_comment": "评论图片",
    "season": "视频封面",
    "up_avatar": "UP头像",
}


def _img(url: str, kind: str = "", ow: int = 0, oh: int = 0) -> str:
    """生成一行图片 markdown（_squeeze 会让它独占一块）。

    ⚠️ QQ 的 markdown 图片**必须带尺寸提示**，格式是官方私有的：

        ![#宽px #高px](url)

    不是标准 markdown 的 `![alt](url)`。少了尺寸提示，客户端不会为图片
    预留空间，表现就是"电脑端能看、手机端一片空白"——这不是防盗链，
    因为平台转存明明成功了（电脑端看得到就是证据）。

    参考：QQ 官方插件 openclaw-qqbot 的 formatQQBotMarkdownImage() 就是
    这么拼的；koishi 插件的更新日志里也把"图片必须带尺寸参数"作为
    "markdown 图片不显示"的根因单独修过一次。

    ow/oh 是**原图**的宽高（由 _pic 从原始 URL 的 @参数里解析出来）。
    给了就按原图比例等比缩，不给才按 16:9 兜底 —— 否则竖图、方图会被
    提示成 16:9，客户端按 16:9 预留空间，预览就被拉变形。
    """
    if not url:
        return ""
    if not SIZE_HINT_ON:
        return f"![{IMG_ALT.get(kind, '图片')}]({url})"
    w, h = _hint_size(kind, ow, oh)
    return f"![#{w}px #{h}px]({url})"


def _pic(src: str, kind: str = "") -> str:
    """一步到位：从原始 URL 解析原图比例 → 归一化 URL → 生成图片行。

    ⚠️ 比例来源的优先级（v1.44.1 修正）：
       动态/评论图 → 探测**发出去那张图**的真实尺寸（@参数可能是裁剪比例）
       其余        → 用 src 的 @参数比例（封面本就 16:9，不必联网）
    """
    if not src:
        return ""
    # ⚠️ 先算出**实际会发出去的** URL，尺寸提示必须跟它一致。
    #    _cover 会剥掉 @参数（发原图），而 src 里的 @参数常是图床**裁剪后**
    #    的尺寸（竖图被裁成 560w_560h 的方图很常见）—— 拿裁剪比例去给原图
    #    做提示，客户端就按方图预留空间，竖图被压变形。
    url = _cover(src, scale=(kind != "up_avatar"))
    if not url:
        return ""
    ow, oh = _src_size(src)
    if kind in ("dynamic", "dynamic_pinned", "top_comment"):
        # 动态配图 / 评论图：比例五花八门，且 @参数可能是裁剪尺寸 → 读真实图
        pw, ph = _probe_size(url)
        if pw and ph:
            return _img(url, kind, pw, ph)
    elif not (ow and oh):
        # 封面类只在 URL 里没有 @参数 时才探测（封面本就 16:9，省一次网络）
        pw, ph = _probe_size(url)
        if pw and ph:
            ow, oh = pw, ph
    return _img(url, kind, ow, oh)


# ── 尺寸提示的开关与取值 ────────────────────────────────────────
# 默认开。config.yaml 里设 image_size_hint: false 可退回 ![alt](url)。
SIZE_HINT_ON = True
SIZE_HINT_W = 672        # 封面/配图：最大宽度（等比缩，不放大）
SIZE_HINT_H = 0          # 0 = 自动按原图比例算（16:9 只是兜底）
SIZE_HINT_AVATAR = 160   # 头像：正方形，别套 16:9 会变形
SIZE_HINT_MAX_H = 1200   # 高度硬上限：超长图（如 672x2225）整体等比压到这个高度
                         # ⚠️ 只按比例缩、不裁不变形；设 0 表示不限制
IMG_MAX_PER_MSG = 0      # 一条消息里最多带几张图（动态/评论可能有多张）；
                         # 0 = 不限（受数据层取到的张数限制，动态最多 9 张）
SIZE_HINT_CELL = 320     # 多图（>=2 张）时每格的尺寸。含义随 GRID_MODE 变：
                         #   box    （默认）= 每格都是 cell×cell 的**固定方框**，
                         #                   但 URL 用原图 → 点开是原图
                         #   ratio          = 统一**宽度**，高度按原图比例，
                         #                   URL 用原图 → 点开就是原图
                         #   square         = 图床**裁剪**成这个边长的方图
                         # ⚠️ 单图**不受影响**，仍按原图比例走 SIZE_HINT_W。
                         # 设 0 = 多图也跟单图一样按 SIZE_HINT_W 的宽度走。
GRID_MODE = "box"        # box / ratio / square，见上面说明。

# ⚠️ 默认为什么是 box（用户原话："多图片的时候固定长宽，但是点进去是原图"）：
#    markdown 里预览和大图用的是**同一个 URL** —— `![#wpx #hpx](url)` 里没有
#    "缩略图 url / 原图 url"之分，点了打开的就是这个 url。
#      square  → URL 是图床裁过的 320×320 小图，点开也是裁剪小图 ❌
#      ratio   → URL 是原图、高度跟着原图走，但各格**高度不齐**
#      box     → URL 是原图（点开即原图 ✅），尺寸提示统一写成 cell×cell
#                让客户端按固定方框预留空间（外面一样大 ✅）
#    方框里怎么放那张原图由客户端决定（通常是居中裁剪填充、不是拉伸），
#    我们能做的就是"给方框 + 给原图 URL"，这两件事 box 模式都做到了。
#    ⚠️ 做不到真正的 3×3 并排方阵：markdown 图片是块级、一行一张，
#       没有并排语法（拼图合成又没有公网 URL，无法内嵌）。


def _square_param(url: str, cell: int) -> str:
    """给 B站图床 URL 加**正方形裁剪**参数，让图床直接返回方格缩略图。

    这是"九宫格"观感的关键：不是靠客户端拉伸，而是让图床**真的裁出**
    一张 cell×cell 的方图，尺寸提示也写 cell×cell，两边一致，预览就不会
    被拉变形。

    ⚠️ 非 hdslb 图床（比如 akamaized）不吃 B站的 @参数，套上去反而取不到
       图，所以只认 host 后缀。
    """
    if not url or cell <= 0:
        return url
    try:
        _host = (urlparse(url).hostname or "").lower()
    except Exception:
        _host = ""
    if not (_host == "hdslb.com" or _host.endswith(".hdslb.com")):
        return url
    u = re.sub(r"@[^/@]*$", "", url)   # ⚠️ 先剥掉已有参数，否则会 @..@.. 叠加
    return u + f"@{cell}w_{cell}h_1c.jpg"


def _img_fixed(url: str, kind: str, w: int, h: int) -> str:
    """按**给定**宽高生成图片行（不走 _hint_size 的等比规则）。

    多图各格已经自己算好比例了，再进 _hint_size 会被 SIZE_HINT_W 二次缩放，
    比例又乱了。宽高都保证是正整数 —— 客户端不认 0 或负数。
    """
    if not url:
        return ""
    if not SIZE_HINT_ON:
        return f"![{IMG_ALT.get(kind, '图片')}]({url})"
    try:
        w, h = int(w), int(h)
    except (TypeError, ValueError):
        return _img(url, kind)
    return f"![#{max(w, 1)}px #{max(h, 1)}px]({url})"


def _pic_cell(src: str, kind: str = "", cell: int = 0) -> str:
    """多图里的一格。具体排版由 GRID_MODE 决定（单图不走这里）。"""
    if not src:
        return ""
    try:
        cell = int(cell or 0)
    except (TypeError, ValueError):
        cell = 0
    if cell <= 0:
        return _pic(src, kind)
    url = _cover(src, scale=(kind != "up_avatar"))
    if not url:
        return ""
    mode = (GRID_MODE or "box").strip().lower()

    if mode == "box":
        # ── box（默认）：**固定方框** + 原图 URL ──
        # 尺寸提示统一写成 cell×cell，让客户端按一样大的方框预留空间；
        # url 此时已是原图（_cover 剥掉了 @参数）→ 点开就是完整原图，
        # 不是图床裁过的那张小图。方框里怎么放由客户端决定。
        return _img_fixed(url, kind, cell, cell)
    if mode == "square":
        # 固定正方形后不必再联网探测原图比例 —— 省一次请求，且比例已知
        return _img(_square_param(url, cell), kind, cell, cell)

    # ── ratio（默认）：发**原图**、按真实比例缩到统一宽度 ──
    ow, oh = _src_size(src)
    if kind in ("dynamic", "dynamic_pinned", "top_comment") or not (ow and oh):
        # 动态配图/评论图比例五花八门，且 @参数常是裁剪后的尺寸 → 读真实图
        pw, ph = _probe_size(url)
        if pw and ph:
            ow, oh = pw, ph
    if not (ow and oh):
        # 比例实在拿不到（探测被关/网络不通）→ 退回单图那套规则，宁可
        # 16:9 兜底也不要发出 0×0 这种客户端不认的提示。
        return _pic(src, kind)
    # 不放大：原图比上限窄就保持原尺寸
    if ow <= cell:
        w, h = ow, oh
    else:
        w, h = cell, max(1, int(round(cell * oh / float(ow))))
    try:
        _mh = int(SIZE_HINT_MAX_H or 0)
    except (TypeError, ValueError):
        _mh = 0
    if _mh > 0 and h > _mh:
        # 超长图整体等比压到上限内（比例不变，只是变小）
        try:
            w = max(1, int(round(w * _mh / float(h))))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        h = _mh
    return _img_fixed(url, kind, w, h)


def _pic_list(srcs, kind: str = "") -> str:
    """把一组图片 URL 渲染成连续的多行图片块。

    ⚠️ 以前动态/评论**只取第一张**（`imgs[0]`），一条带 9 图的动态在群里
    只显示一张，其余全丢 —— 用户问"如果是一个动态多图片呢"就是这个。

    各图之间**只换行、不空行**（用户对空行有明确要求）；`_squeeze` 会保证
    整块图片前后各留一个空行、让它独占一块，块内部则保持紧凑。
    """
    urls = [u for u in (srcs or []) if u]
    if not urls:
        return ""
    try:
        n = int(IMG_MAX_PER_MSG or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        urls = urls[:n]
    lines = []
    try:
        _cell = int(SIZE_HINT_CELL or 0)
    except (TypeError, ValueError):
        _cell = 0
    # ⚠️ 只有**多图**才走九宫格；单图（你要求的"单独图片就默认"）保持原比例。
    #    markdown 的图片是块级元素、一行一张，做不到真正的 3×3 并排，
    #    "九宫格"指的就是每格都裁成一致的小方图这一观感。
    grid = _cell > 0 and len(urls) >= 2
    for u in urls:
        p = _pic_cell(u, kind, _cell) if grid else _pic(u, kind)
        if p:
            lines.append(p)
    return "\n".join(lines)


def _src_size(url: str) -> tuple:
    """从 B站图床 URL 的 @参数里解析**原图**宽高。

    形如 `//i0.hdslb.com/bfs/xxx.jpg@518w_518h_1c.webp`，
    其中 518w/518h 就是图床给这张图裁出来的尺寸 —— 也就是原图比例。

    ⚠️ 动态配图、评论图大多是竖图或方图，只有拿到真实比例才不会把预览
       拉变形；视频封面本来就是 16:9，解析不到也无所谓。
    解析不到返回 (0, 0)，由调用方按 16:9 兜底。
    """
    try:
        m = re.search(r"@(\d+)w_(\d+)h", url or "")
    except (TypeError, re.error):
        return (0, 0)
    if not m:
        return (0, 0)
    try:
        w, h = int(m.group(1)), int(m.group(2))
    except (TypeError, ValueError):
        return (0, 0)
    return (w, h) if w > 0 and h > 0 else (0, 0)


def _parse_size_from_bytes(b: bytes) -> tuple:
    """从图片文件头解析真实宽高（PNG / GIF / WebP / JPEG），解析不到返回 (0,0)。"""
    try:
        import struct
    except Exception:
        return (0, 0)
    try:
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            if len(b) >= 24 and b[12:16] == b"IHDR":
                w, h = struct.unpack(">II", b[16:24])
                return (int(w), int(h))
            return (0, 0)
        if b[:3] == b"GIF" and len(b) >= 10:
            w, h = struct.unpack("<HH", b[6:10])
            return (int(w), int(h))
        if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
            fmt = b[12:16]
            if fmt == b"VP8X" and len(b) >= 30:
                return (int.from_bytes(b[24:27], "little") + 1,
                        int.from_bytes(b[27:30], "little") + 1)
            if fmt == b"VP8 " and len(b) >= 30:
                return (struct.unpack("<H", b[26:28])[0] & 0x3FFF,
                        struct.unpack("<H", b[28:30])[0] & 0x3FFF)
            if fmt == b"VP8L" and len(b) >= 25:
                bits = int.from_bytes(b[21:25], "little")
                return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            return (0, 0)
        if b[:2] == b"\xff\xd8":            # JPEG：扫描 SOFn 段
            i = 2
            n = len(b)
            while i < n - 9:
                if b[i] != 0xFF:
                    i += 1
                    continue
                mk = b[i + 1]
                if mk in (0xD8, 0x01) or 0xD0 <= mk <= 0xD7:
                    i += 2
                    continue
                if i + 4 > n:
                    break
                seglen = struct.unpack(">H", b[i + 2:i + 4])[0]
                if 0xC0 <= mk <= 0xCF and mk not in (0xC4, 0xC8, 0xCC):
                    if i + 9 <= n:
                        h = struct.unpack(">H", b[i + 5:i + 7])[0]
                        w = struct.unpack(">H", b[i + 7:i + 9])[0]
                        return (int(w), int(h))
                    break
                if seglen <= 0:
                    break
                i += 2 + seglen
    except Exception:
        return (0, 0)
    return (0, 0)


# 探测结果缓存：同一张图只探一次，之后零开销。
# 负结果也缓存，避免每次推送都白等一次超时。
_SIZE_CACHE = {}

# 是否允许联网探测真实尺寸。config.yaml 里 image_probe_size: false 可关。
PROBE_SIZE_ON = True
PROBE_TIMEOUT = 2.0


def _probe_size(url: str) -> tuple:
    """拿不到 @参数 时，下载图片头部读真实宽高。

    B站很多动态配图/评论图的 URL 是**原图直链**，不带 `@1080w_1920h` 参数，
    这时 _src_size() 解析不到比例，只能 16:9 兜底 —— 竖图、方图预览就
    全被拉成宽屏（用户报的"怎么都是 16:9"）。

    只在解析不到参数时才走这条路，且结果缓存；失败一律返回 (0,0) 由调用方
    兜底，**绝不影响消息能不能发出去**。
    """
    if not PROBE_SIZE_ON or not url:
        return (0, 0)
    try:
        u = url
        if u.startswith("//"):
            u = "https:" + u
        if not u.startswith("http"):
            return (0, 0)
        if not allowed_image_url(u):
            return (0, 0)
    except Exception:
        return (0, 0)
    if u in _SIZE_CACHE:
        return _SIZE_CACHE[u]
    sz = (0, 0)
    try:
        from urllib.request import Request, urlopen
        req = Request(u, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://www.bilibili.com/",
            "Range": "bytes=0-65535",
        })
        with urlopen(req, timeout=PROBE_TIMEOUT) as r:
            sz = _parse_size_from_bytes(r.read(65536))
    except Exception:
        sz = (0, 0)
    _SIZE_CACHE[u] = sz
    return sz


def _hint_size(kind: str, ow: int = 0, oh: int = 0) -> tuple:
    """按图片类型 + 原图比例给出尺寸提示。宽高都必须是正整数，否则客户端不认。

    等比规则（等比例缩小，不放大）：
      · 已知原图宽高 → 按 SIZE_HINT_W 等比缩，原图比上限小就直接用原尺寸
      · 未知 → 按 16:9 兜底（视频封面本来就是这个比例）
      · 用户在 config 里显式给了 h → 尊重用户，不做等比
    """
    try:
        ow, oh = int(ow or 0), int(oh or 0)
    except (TypeError, ValueError):
        ow, oh = 0, 0
    if kind == "up_avatar":
        # 头像本就是方的，套 16:9 会变形；也不跟着原图放大
        return (SIZE_HINT_AVATAR, SIZE_HINT_AVATAR)
    w = SIZE_HINT_W
    h = SIZE_HINT_H
    if w > 0 and h <= 0:
        if ow > 0 and oh > 0:
            # ⚠️ 不放大：原图比上限小就保持原尺寸，避免小图被拉糊
            if ow <= w:
                return (ow, oh)
            h = int(round(w * oh / float(ow)))
        else:
            h = int(round(w * 9 / 16))
    try:
        _mh = int(SIZE_HINT_MAX_H or 0)
    except (TypeError, ValueError):
        _mh = 0
    if _mh > 0 and h > _mh:
        # 超长图（竖图/长条图）整体等比压到上限内 —— 比例不变，只是整体变小，
        # 否则客户端按真实高度预留空间，手机上要滑好几屏才看得全。
        try:
            w = int(round(w * _mh / float(h)))
        except (TypeError, ValueError, ZeroDivisionError):
            w = w
        h = _mh
    return (max(w, 1), max(h, 1))


def set_size_hint(on, w: int = 0, h: int = 0, avatar: int = 0,
                  max_h: int = -1, max_per_msg: int = -1,
                  cell: int = -1, grid_mode=None) -> None:
    """按 config 应用尺寸提示设置（热更新，不用重启）。"""
    global SIZE_HINT_ON, SIZE_HINT_W, SIZE_HINT_H, SIZE_HINT_AVATAR
    global SIZE_HINT_MAX_H, IMG_MAX_PER_MSG, SIZE_HINT_CELL, GRID_MODE
    SIZE_HINT_ON = bool(on)
    # 只认这几种写法，写错就当没给（沿用默认 box），
    # 免得拼进 URL 参数里弄出取不到图的怪链接。
    _gm = str(grid_mode or "").strip().lower()
    if _gm in ("box", "ratio", "square"):
        GRID_MODE = _gm
    try:
        _cell = int(cell)
    except (TypeError, ValueError):
        _cell = -1
    if _cell >= 0:
        SIZE_HINT_CELL = max(0, _cell)
    try:
        _mp = int(max_per_msg)
    except (TypeError, ValueError):
        _mp = -1
    # 0 是合法值（不限），只有负数才表示"没给这个配置项"
    if _mp >= 0:
        IMG_MAX_PER_MSG = max(0, _mp)
    try:
        _mh = int(max_h)
    except (TypeError, ValueError):
        _mh = -1
    if _mh >= 0:
        SIZE_HINT_MAX_H = max(0, _mh)
    for name, val in (("w", w), ("h", h), ("avatar", avatar)):
        try:
            v = int(val)
        except (TypeError, ValueError):
            continue
        # ⚠️ h 的语义是"不设就自动"：0 表示**按原图比例算**，不是"保持旧值"。
        #    以前 h<=0 时直接跳过、沿用模块默认的 378，结果永远固定 16:9，
        #    竖图/方图照样被拉变形 —— 这正是"预览全是 16:9"的根因之一。
        if name == "h":
            SIZE_HINT_H = max(v, 0)
            continue
        if v <= 0:
            continue
        if name == "w":
            SIZE_HINT_W = v
        else:
            SIZE_HINT_AVATAR = v

DEFAULT_TEMPLATES = {
    "live": '【直播订阅】"{name}"开播啦！记得看噢~',
    "video": '【视频订阅】"{name}"新视频投稿了《{title}》，记得看噢~',
    "dynamic": '【动态订阅】"{name}"新动态更新了《{title}》',
    "dynamic_no_title": '【动态订阅】"{name}"新动态更新了',
    "dynamic_pinned": '【动态订阅】"{name}"置顶了一条动态《{title}》',
    "dynamic_pinned_no_title": '【动态订阅】"{name}"置顶了一条动态',
    # 开播动态（B 站开播时自动发的那条，正文常常只有一个表情勋章）。
    # 它本质就是"正在直播"这件事，不能再显示成"新动态"。
    "dynamic_live": '【直播订阅】"{name}"开播中《{title}》',
    "dynamic_live_no_title": '【直播订阅】"{name}"开播中，记得看噢~',
    "offline": '【直播订阅】"{name}"已下播啦~',
    "top_comment": '【置顶评论】"{name}"置顶动态下有新置顶评论',
    # 合集新增视频。变量：{name}=UP主 {title}=视频标题
    # {season}=合集名 {section}=小节名（没有小节时为空）
    "season": '【合集订阅】"{name}"的合集《{season}》新增了视频《{title}》',
}

# 卡片标题（## 二号标题）
HEAD_TITLE = {
    "live": "🔴 开播提醒",
    "video": "📺 新视频投稿",
    "dynamic": "📝 新动态",
    "dynamic_pinned": "📌 置顶动态",
    "dynamic_live": "🔴 直播中",
    "offline": "⚫ 已下播",
    "top_comment": "💬 置顶评论",
    "season": "📦 合集更新",
}

BUTTON_LABEL = {
    "live": "进入直播间",
    "video": "观看视频",
    "dynamic": "查看动态",
    # ⚠️ 下播后直播间已经关了，再给「进入直播间」按钮不合逻辑
    #   （点了要么进不去、要么进了看到的是黑屏/轮播）。
    #   下播卡片改指向 UP 主空间主页 —— 想看回放、看投稿都从这里走。
    "offline": "进入个人空间",
    "top_comment": "查看评论",
    "season": "观看视频",
}

# 按钮点击后显示的文字
BUTTON_VISITED = {
    "live": "已打开直播间",
    "video": "已打开视频",
    "dynamic": "已查看动态",
    "offline": "已打开空间",
    "space": "已打开空间",
    "top_comment": "已查看评论",
    "season": "已打开视频",
}


def room_label(room_id, short_room_id=0) -> str:
    """把直播间号显示成人话。

    B 站直播间有两个号，都能进同一个直播间：
      - 长号（room_id）  ：永远存在
      - 短号（short_id）：可选，很多主播没有（官方接口返回 0 表示没有）

    两个都有 → 都显示，长号标「原」、短号标「短」
    只有一个 → 直接显示数字，不加标签（多一个标签反而像出了bug）
    """
    try:
        long_id = int(room_id or 0)
    except (TypeError, ValueError):
        long_id = 0
    try:
        short_id = int(short_room_id or 0)
    except (TypeError, ValueError):
        short_id = 0
    # 短号和长号一样时，其实只有这一个号
    if short_id and short_id == long_id:
        short_id = 0
    if not long_id and not short_id:
        return ""
    if long_id and short_id:
        return f"原：{long_id}　短：{short_id}"
    return str(long_id or short_id)


def safe_format(tpl: str, ctx: dict) -> str:
    """填模板变量，**未知变量绝不能让整条推送挂掉**。

    ⚠️ 这是刚需：以前是 `tpl.format(name=..., title=...)`，
    用户如果在文案里写了 `{room}`（直播间号），而那个类目只传了
    name/title → KeyError → 渲染失败 → **整条提醒发不出去**，
    而且用户完全不知道是自己文案填错了。

    现在：未知变量**保留原样**（用户能一眼看出哪个没生效），
    缺值的已知变量填空，语法写坏（比如 `{` 没闭合）就原样返回。
    """
    if not tpl:
        return ""
    try:
        import string
        class _D(dict):
            def __missing__(self, k):
                return "{" + k + "}"
        return string.Formatter().vformat(str(tpl), (), _D(ctx or {}))
    except Exception:
        # 括号写坏了（比如 "{name" 没闭合）：原样返回，别把推送搞挂
        return str(tpl)


# 每个模板键**可用哪些变量**。面板按这个生成快捷插入按钮，
# 也用来提示用户"这个类目能用哪些变量"。
#
# ⚠️ 以前只有 {name} 和 {title} 两个，想在开播提醒里写直播间号、
# 分区、人气都做不到 —— 写了还会让整条消息发不出去（见 safe_format）。
TPL_VARS = {
    "live": ["{name}", "{title}", "{area}", "{online}", "{room}", "{url}"],
    "offline": ["{name}", "{title}", "{room}", "{url}"],
    "video": ["{name}", "{title}", "{bvid}", "{desc}", "{time}", "{url}"],
    "dynamic": ["{name}", "{title}", "{text}", "{url}"],
    "dynamic_no_title": ["{name}", "{text}", "{url}"],
    "dynamic_pinned": ["{name}", "{title}", "{text}", "{url}"],
    "dynamic_pinned_no_title": ["{name}", "{text}", "{url}"],
    "dynamic_live": ["{name}", "{title}", "{text}", "{url}", "{room}"],
    "dynamic_live_no_title": ["{name}", "{text}", "{url}", "{room}"],
    "top_comment": ["{name}", "{text}", "{url}"],
    "season": ["{name}", "{title}", "{season}", "{section}",
               "{bvid}", "{url}"],
}

# 变量的中文说明，面板上显示用
VAR_LABEL = {
    "{name}": "UP主名",
    "{title}": "标题",
    "{area}": "分区",
    "{online}": "人气",
    "{room}": "直播间号",
    "{url}": "链接",
    "{bvid}": "BV号",
    "{desc}": "简介",
    "{time}": "发布时间",
    "{text}": "正文",
    "{season}": "合集名",
    "{section}": "小节名",
}

# 文案按**类目**分组（面板上分开编辑，互不干扰）
# 例：只想改直播文案，就只看到 live/offline 两项
TPL_GROUPS = [
    ("live", "🔴 直播", ["live", "offline"]),
    ("video", "📺 视频投稿", ["video"]),
    ("dynamic", "📝 动态", ["dynamic", "dynamic_no_title",
                          "dynamic_pinned", "dynamic_pinned_no_title",
                          "dynamic_live", "dynamic_live_no_title"]),
    ("top_comment", "💬 置顶评论", ["top_comment"]),
    ("season", "📦 合集更新", ["season"]),
]


def _esc(text: str) -> str:
    """Markdown 最小转义：去掉会破坏排版的符号，并压掉多余空行。

    ⚠️ 方括号和井号**不能删**：表情是 [xxx]、话题是 #Tag:xxx，
    以前连它们一起删，bilibili 那边格式化好的正文到卡片里又被打回
    裸名（「星回纪·时空轮转是我的翅膀」「Tag:小星星的家」），
    用户反馈"改了还是没区分"的根因就在这一行 ——
    正文组装做对了，最后一环把标记全削平了。

    会不会出问题：
      · `[xxx]` 后面没有紧跟 `(`，不是链接语法，渲染成字面文本，安全
      · `#Tag:xxx` 的 `#` 后没有空格，不是 ATX 标题；且正文每行都带
        `> 　　全角空格` 前缀，井号永远不在行首，不会变成大字号标题
    """
    s = (text or "").replace("\r", "")
    # ⚠️ 单个 * / _ 也必须处理：B站表情名大量带下划线（OnO_气死我了），
    #    一删就把表情名改坏了（[OnO气死我了]），所以不能删。
    #    但"单个的触发不了斜体"这个旧结论是**错的** —— Markdown 里
    #    *斜体* / _斜体_ 用单个符号成对出现就能触发强调，表情名里的
    #    下划线（[a_b_c]）会让中间的字变斜，用户看到的就是"有些字斜着"。
    #    所以改成**转义**（加反斜杠）：既不触发强调，又保留原字符。
    s = re.sub(r"\*{2,}", "", s)
    s = re.sub(r"_{2,}", "", s)
    s = s.replace("*", r"\*")
    s = s.replace("_", r"\_")
    s = re.sub(r"[`<>]", "", s)
    # 正文里自带的 [文字](链接) 会被渲染成可点链接 —— 陌生人发的链接有风险，
    # 在 ] 与 ( 之间插一个空格打断链接语法，视觉上几乎看不出来。
    s = s.replace("](", "] (")
    s = s.replace("​", "")      # 内容里不能混进零宽空格，会干扰分隔
    return s.strip()


# ── 内嵌图片的缩放尺寸 ──────────────────────────────────────────
# 内嵌模式（inline）下，![](url) 留在卡片里，由**开放平台下载转存**再发给
# 客户端。官方 markdown 文档的原话是"请使用可在公网访问的资源 url，开放平台
# 会下载转存该资源"——所以这一步的成功率取决于平台能不能顺利下载。
#
# ⚠️ 之前这里写的是"去掉 @参数、给原图最稳"，那个推理错了。真正会踩的坑
#    有两个，而且**互相独立**，别混为一谈：
#
#      1. 尺寸：去掉 @ 参数 = 原图。B站封面/动态图原图动辄 1920×1080、好几
#         百 KB 甚至几 MB，平台转存或手机端加载大图容易失败。
#      2. 格式：B站图床默认给的是 `@672w_378h_1c.webp` —— **webp**，手机 QQ
#         对 webp 的支持明显差于电脑端，这是最经典的"电脑能看手机不能看"。
#
#    第 2 条是硬伤（QQ 只收 png/jpg），所以 **webp → jpg 无条件生效**。
#    第 1 条是"可能"：压小了更稳，但也可能压完画质糊、甚至个别图床参数
#    不兼容。所以默认 **不压缩**（INLINE_PIC_W = 0，用原图），只做格式转换；
#    想压就在 config.yaml 设 image_inline_width: 672（热更新，不用重启）。
INLINE_PIC_W = 0        # 0 = 不加 CDN 参数，用原图（默认，不压缩）
INLINE_PIC_H = 0


def set_inline_pic_size(w: int, h: int = 0) -> None:
    """按 config 的 image_inline_width 调整；w <= 0 表示不加参数（用原图）。

    只给 w 时按 16:9 算出高，否则高度会是 0、退化成正方形参数。
    """
    global INLINE_PIC_W, INLINE_PIC_H
    try:
        w = int(w)
    except (TypeError, ValueError):
        return
    INLINE_PIC_W = max(w, 0)
    try:
        h = int(h or 0)
    except (TypeError, ValueError):
        h = 0
    # ⚠️ 只给宽度时必须自己算高度：留成 0 的话 _cover 里 `h or w`
    #    会退化成正方形参数（@672w_672h_1c.jpg），封面被裁成方的。
    if INLINE_PIC_W > 0 and h <= 0:
        h = int(round(INLINE_PIC_W * 9 / 16))
    INLINE_PIC_H = max(h, 0)


def _cover(url: str, scale: bool = True) -> str:
    """给 B 站图片补 https、统一成 jpg，并按配置可选压尺寸；非白名单域名丢弃。

    ⚠️ 格式转换（webp/gif → jpg）**无条件生效**，因为 QQ 只收 png/jpg；
       尺寸压缩则取决于 INLINE_PIC_W，默认 0 = 不压缩、用原图。

    scale=False 用于头像：头像原图本就很小，不需要压，而且套 16:9 参数
    会把方头像裁变形。
    """
    if not url:
        return ""
    u = ("https:" + url) if url.startswith("//") else url
    # ⚠️ B站部分接口返回的图是 **http://** 开头。
    #    下面 allowed_image_url 只放行 https，http 会被整条丢掉 ——
    #    于是"明明有封面，消息里却一张图都没有"。
    #    图床本身支持 https，统一升级即可，别直接丢弃。
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    if not allowed_image_url(u):
        return ""
    u = re.sub(r"@[^/@]*$", "", u)              # 先去掉已有的 @参数（可能是 webp）
    u = re.sub(r"\.(webp|gif)$", ".jpg", u, flags=re.I)
    if scale and INLINE_PIC_W:
        # ⚠️ 必须按 **host** 判断，不能用 "hdslb.com" in u 这种子串判断：
        #    `i0.hdslb.com.akamaized.net` 也含 hdslb.com，但它不是 hdslb 图床，
        #    套 B站的 @参数上去会取不到内容。
        try:
            _host = (urlparse(u).hostname or "").lower()
        except Exception:
            _host = ""
        if _host == "hdslb.com" or _host.endswith(".hdslb.com"):
            h = INLINE_PIC_H or INLINE_PIC_W
            u = u + f"@{INLINE_PIC_W}w_{h}h_1c.jpg"
    return u




def _kv_block(rows) -> str:
    """把「标签:值」排成对齐的块引用。

    用块引用而不是列表，是因为列表前必须空行才能识别，
    而块引用更稳、视觉上也更像卡片的信息区。
    """
    rows = [(k, str(v)) for k, v in rows if v not in (None, "")]
    if not rows:
        return ""
    width = max(len(k) for k, _ in rows)
    # +1 保证标签和值之间至少有一个全角空格，否则会粘在一起
    return "\n".join(f"> **{k}**{'　' * (width - len(k) + 1)}{v}"
                     for k, v in rows)


def _indent_body(text: str, limit: int = 200) -> list:
    """动态正文：每段缩进两格，放进块引用里。"""
    out = []
    for para in (text or "").split("\n"):
        line = para.strip()
        if line:
            out.append(f"> {INDENT}{line[:limit]}")
    return out


def _first_line(text: str, limit: int = 120) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def _pubdate_str(ts: int) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _squeeze(text: str) -> str:
    """压掉任何形式的"空的一行"，只保留真正的换行。

    用户要求：消息可以换行，但不要空行。这里统一兜底，防止将来
    某个渲染分支又插回空行没人发现。要压掉的有三类：
      · \\n\\n          QQ 会直接压缩掉，等于白写
      · 零宽空格单独成行  之前用它撑空行，正是"换空的一行"的来源
      · 只有 > 的引用行   渲染出来就是一条空白，看着也像空行

    ⚠️ **唯一例外：图片行**（见 IMG_LINE_RE）。
    图片在 markdown 里是**块级**元素，紧贴在粗体标题后面会被当成
    同一段落的行内内容 —— 表现就是"卡片里明明写了 ![](url)，
    群里却看不到图（或被压成一条小图挤在标题后面）"。
    所以它前后各留一个空行，让它独占一块。
    这是全篇唯一保留的空行，其余一律压掉。
    """
    if not text:
        return ""
    out = []
    prev_img = False
    for line in str(text).replace("\r", "").split("\n"):
        s = line.strip().replace(ZWSP, "").strip()
        # 空行 / 纯零宽空格行 → 丢掉
        if not s:
            continue
        # 只有块引用标记的"空引用行" → 丢掉
        if s == ">":
            continue
        line = line.rstrip()
        is_img = bool(IMG_LINE_RE.match(s))
        # ⚠️ 连续的图片行之间**不空行**（用户明确要求：可以换行，但不要
        #    空的一行）—— 一条多图动态里 9 张图各空一行会拉得很长。
        #    空行只加在"整块图片"的头尾，让它独占一块。
        if is_img and prev_img:
            pass
        elif (is_img or prev_img) and out and out[-1].strip():
            out.append("")
        out.append(line)
        prev_img = is_img
    return "\n".join(out)


# 测试卡片标记：单独一行，放在卡片标题**前面**（v1.32.1 定的样子）：
#     「🧪 测试」
#     **🔴 开播提醒**
# ⚠️ 以前是拼进 info.title 里，结果标记跑到"标题"那一行去了
#    （跟 🔴 开播提醒 对不上，用户要的是独立一行在最顶上）。
#    必须是**真字符** U+300C / U+300D（全角），别写成半角的 ｢｣。
MARK_LINE = "\u300c\U0001F9EA 测试\u300d"


def apply_test_mark(md: str, mark: bool = True) -> str:
    """给渲染好的卡片加 / 去「🧪 测试」标记行（幂等，不会叠加）。

    放在这里而不是塞进 info.title：标题会被 render 拼进块引用的
    「标题」行里，标记跟卡片标题就对不上了。
    """
    md = str(md or "")
    lines = md.split("\n")
    # 幂等：先把已有的标记行全部摘掉（不会重复叠加）
    while lines and lines[0].strip() == MARK_LINE:
        lines.pop(0)
    body = "\n".join(lines)
    if mark and body.strip():
        return MARK_LINE + SEP + body
    return body


def _compose(head: str, parts: list) -> str:
    """拼装卡片：段落之间一律单个换行，**不留空行**。

    不用 `---` 水平分割线 —— 它在部分客户端会渲染成一条难看的横线，
    块引用本身已有视觉分隔，没必要再加。

    ⚠️ 用索引判断第一段，不要用 `out == blocks[0]`：
       一旦后面的段落文本和标题完全相同，那个判断会误命中。
    """
    blocks = [b for b in ([head] + [p for p in parts if p]) if b]
    if not blocks:
        return ""
    out = blocks[0]
    for i, b in enumerate(blocks[1:], start=1):
        out += SEP + b
    return _squeeze(out)


def render_live(info: LiveInfo, name: str, tpls: dict) -> tuple:
    """返回 (markdown, [(按钮文字, 链接), ...])"""
    tpl = tpls.get("live") or DEFAULT_TEMPLATES["live"]
    head = f"**{HEAD_TITLE['live']}**"

    parts = []
    _p = _pic(info.cover, "live")
    if _p:
        parts.append(_p)
    ctx = {"name": name, "title": info.title, "area": info.area,
           "online": f"{info.online:,}" if info.online else "",
           "room": getattr(info, "room_id", "") or "", "url": info.url}
    parts.append(f"**{_esc(safe_format(tpl, ctx))}**")
    block = _kv_block([
        ("标题", _esc(info.title)),
        ("分区", _esc(info.area)),
        ("人气", f"{info.online:,}" if info.online else ""),
    ])
    if block:
        parts.append(block)

    return _compose(head, parts), [button("live", BUTTON_LABEL["live"], info.url)]


def render_video(info: VideoInfo, name: str, tpls: dict) -> tuple:
    tpl = tpls.get("video") or DEFAULT_TEMPLATES["video"]
    head = f"**{HEAD_TITLE['video']}**"

    parts = []
    _p = _pic(info.pic, "video")
    if _p:
        parts.append(_p)
    ctx = {"name": name, "title": info.title, "bvid": info.bvid,
           "desc": _first_line(info.desc, 80),
           "time": _pubdate_str(info.pubdate), "url": info.url}
    parts.append(f"**{_esc(safe_format(tpl, ctx))}**")
    block = _kv_block([
        ("简介", _esc(_first_line(info.desc, 80))),
        ("发布时间", _pubdate_str(info.pubdate)),
    ])
    if block:
        parts.append(block)

    return _compose(head, parts), [button("video", BUTTON_LABEL["video"], info.url)]


def _dyn_ctx(name: str, info) -> dict:
    """动态类文案的变量上下文。"""
    ctx = {"name": name, "title": info.title or "",
           "text": info.text or "", "url": getattr(info, "url", "") or ""}
    # 开播动态额外给直播间号（文案里可以写 {room}）
    room = int(getattr(info, "ref_room_id", 0) or 0)
    if room:
        ctx["room"] = str(room)
    return ctx


def render_dynamic(info: DynamicInfo, name: str, tpls: dict) -> tuple:
    # 置顶动态和普通动态用不同文案（用户要求区分开）
    pinned = bool(getattr(info, "pinned", False))
    # 开播动态：B 站开播时自动发的那条，正文常常只有一个表情勋章。
    # 它本质就是"正在直播"这件事 —— 以前按普通动态渲染，推出来是
    # 「📝 新动态 … > 动态内容 > [伊万勋章]」，用户根本认不出这是在播。
    is_live = ((getattr(info, "major_type", "") or "") == "live"
               and not pinned)
    if is_live:
        key = "dynamic_live" if info.title else "dynamic_live_no_title"
        tpl = tpls.get(key) or DEFAULT_TEMPLATES[key]
        head_text = safe_format(tpl, _dyn_ctx(name, info))
    elif pinned:
        key = "dynamic_pinned" if info.title else "dynamic_pinned_no_title"
        tpl = tpls.get(key) or DEFAULT_TEMPLATES[key]
        head_text = safe_format(tpl, _dyn_ctx(name, info))
    elif info.title:
        tpl = tpls.get("dynamic") or DEFAULT_TEMPLATES["dynamic"]
        head_text = safe_format(tpl, _dyn_ctx(name, info))
    else:
        tpl = tpls.get("dynamic_no_title") or DEFAULT_TEMPLATES["dynamic_no_title"]
        head_text = safe_format(tpl, _dyn_ctx(name, info))

    if is_live:
        head = f"**{HEAD_TITLE['dynamic_live']}**"
    else:
        head = f"**{HEAD_TITLE['dynamic_pinned' if pinned else 'dynamic']}**"

    parts = []
    imgs = [u for u in (info.images or []) if u]
    _p = _pic_list(imgs, "dynamic_pinned" if pinned else "dynamic")
    if _p:
        parts.append(_p)
    parts.append(f"**{_esc(head_text)}**")

    body = _esc(info.text)
    if body:
        # ⚠️ 以前这里插了一个空的 ">" 行当分隔，渲染出来就是一条空白
        #    —— 正是用户说的"换空的一行"。现在紧贴标签行，不留空。
        quoted = ["> **动态内容**"] + _indent_body(body)
        parts.append("\n".join(quoted))

    btns = [button("dynamic", BUTTON_LABEL["dynamic"], info.url)] if info.url else []
    # 开播动态：再给一个直达直播间的按钮 —— 这才是这条推送真正想让人点的
    room = int(getattr(info, "ref_room_id", 0) or 0)
    if room:
        btns.append(button("live", BUTTON_LABEL["live"],
                           f"https://live.bilibili.com/{room}"))
    return _compose(head, parts), btns


def _fmt_duration(sec) -> str:
    """把秒数说成人话：2 小时 5 分 / 23 分 / 45 秒。

    ⚠️ 秒只在不足一分钟时才显示 —— 几小时的直播报"2 小时 5 分 12 秒"
    既啰嗦也没意义，用户要的是个大概。
    """
    try:
        sec = int(sec or 0)
    except (TypeError, ValueError):
        return ""
    if sec <= 0:
        return ""
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h} 小时 {m} 分"
    if m:
        return f"{m} 分"
    return f"{s} 秒"


def render_offline(info: LiveInfo, name: str, tpls: dict, uid: int = 0) -> tuple:
    tpl = tpls.get("offline") or DEFAULT_TEMPLATES["offline"]
    head = f"**{HEAD_TITLE['offline']}**"
    ctx = {"name": name, "title": info.title,
           "room": getattr(info, "room_id", "") or "", "url": info.url}
    parts = [f"**{_esc(safe_format(tpl, ctx))}**"]
    # ⚠️ 下播卡片**不给任何按钮**。
    #    以前给的是"进入 UP 主空间"：人都下播了，一个跳去主页的按钮
    #    既不是用户此刻想要的，也跟"下播"这件事没关系（用户原话：
    #    "都下播了怎么还要有进入空间的按钮，这不符合逻辑"）。
    #    改成显示本场直播的时长 —— 这才是下播时真正想知道的信息。
    dur = _fmt_duration(getattr(info, "duration", 0) or 0)
    if dur:
        parts.append(_kv_block([("直播时长", dur)]))
    # uid 参数保留只为兼容既有调用方（render(..., uid=uid)），不再用于按钮
    return _compose(head, parts), []


def button(kind: str, label: str, url: str) -> tuple:
    """带「点击后文字」的按钮三元组。"""
    return (label, url, BUTTON_VISITED.get(kind, ""))


def space_url(uid: int) -> str:
    """UP 主空间主页。"""
    return f"https://space.bilibili.com/{uid}"


def render_subscribe(name: str, uid: int, face: str, opened: str,
                     hint: str = "") -> tuple:
    """订阅成功的卡片：带头像。

    只在「本群从无到有」订阅这个 UP 主时才用它（首次订阅、或退订后再订阅）。
    重复订阅、改类型、退订等操作都走纯文本不插图，避免刷屏，也少一次图片请求。
    """
    head = "**✅ 订阅成功**"

    parts = []
    _p = _pic(face, "up_avatar")
    if _p:
        parts.append(_p)
    parts.append(f"**已订阅【{_esc(name)}】**")
    block = _kv_block([
        ("UID", str(uid)),
        ("开启提醒", _esc(opened)),
    ])
    if block:
        parts.append(block)
    if hint:
        parts.append(f"> {_esc(hint.strip())}")

    btns = [button("space", "TA 的空间", space_url(uid))]
    return _compose(head, parts), btns


def subscribe_plain_text(name: str, uid: int, opened: str, hint: str = "") -> str:
    """订阅成功的纯文本兜底（卡片发不出去时用它）。"""
    tail = f"（{hint}）" if hint else ""
    return (f"✅ 已订阅【{name}】（UID {uid}）\n"
            f"开启提醒：{opened}{tail}")


def cover_of(kind: str, info) -> str:
    """取这张卡片要用的图片 URL（直播封面 / 视频封面 / 动态插图 / UP 头像）。"""
    try:
        if kind == "live":
            return _cover(getattr(info, "cover", "") or "")
        if kind == "video":
            return _cover(getattr(info, "pic", "") or "")
        if kind == "season":
            # 合集新增的视频也有封面（SeasonPush.pic）
            return _cover(getattr(info, "pic", "") or "")
        if kind == "dynamic":
            for u in (getattr(info, "images", None) or []):
                c = _cover(u)
                if c:
                    return c
            return ""
        if kind == "subscribe":
            return _cover(getattr(info, "face", "") or "", scale=False)
    except Exception:
        return ""
    return ""


def strip_images(md: str) -> str:
    """去掉 markdown 里的图片行。

    图片改走富媒体（msg_type=7）单独发送后，
    卡片里再留 ![](url) 就是两条图，而且那条还是会裂。
    """
    if not md:
        return md
    out = re.sub(r"!\[[^\]]*\]\([^)]*\)\n?", "", md)
    # 去掉剥离后留下的空行堆（不只是 3 连以上，单个空行也要压掉）
    return _squeeze(out)


def to_plain(md: str) -> str:
    """把 markdown 卡片转成「纯文本但好看」的样子。

    为什么要这个：自定义 Markdown 需要申请权限。
    没申请时，直接把 `**加粗**`、`> 引用`、`## 标题` 当文本发出去，
    群里看到的就是一堆星号和大于号，非常难看。

    这里用 Unicode 符号 + 全角空格对齐来还原层次感：
    - `## 标题`      → 单独一行，加装饰线
    - `**加粗**`     → 去掉星号，标题加【】强调
    - `> 引用`       → 换成竖线 │，信息区更像卡片
    - `---` 分割线   → 换成 ────── 细线
    """
    if not md:
        return ""
    out = md
    # 图片行先去掉（图片走富媒体单独发）
    out = re.sub(r"!\[[^\]]*\]\([^)]*\)\n?", "", out)

    lines = out.split("\n")
    result = []
    for ln in lines:
        raw = ln.rstrip()
        if not raw.strip():
            continue
        # 分割线
        if re.fullmatch(r"-{3,}", raw.strip()):
            result.append("─" * 18)
            continue
        # 块引用 → 竖线
        m = re.match(r"^>\s?(.*)$", raw)
        if m:
            body = m.group(1)
            # 引用里的加粗标签
            body = re.sub(r"\*\*([^*]+)\*\*", r"\1", body)
            result.append("│ " + body.strip())
            continue
        # 标题
        m = re.match(r"^#{1,6}\s*(.*)$", raw)
        if m:
            t = re.sub(r"\*\*([^*]+)\*\*", r"\1", m.group(1)).strip()
            # ⚠️ 这里以前先 append("") 给标题垫一个空行，
            #    与"不要空行"的要求冲突，已去掉（上面的 ── 细线已能分隔）。
            result.append("【" + t + "】")
            result.append("─" * 18)
            continue
        # 普通行：去掉加粗符号
        t = re.sub(r"\*\*([^*]+)\*\*", r"\1", raw).strip()
        result.append(t)

    # 去掉开头多余空行、合并连续空行、收尾
    while result and not result[0].strip():
        result.pop(0)
    compact = []
    for ln in result:
        if not ln.strip() and (not compact or not compact[-1].strip()):
            continue
        compact.append(ln)
    return "\n".join(compact).strip()


def render_top_comment(c: dict, name: str, tpls: dict) -> tuple:
    """置顶动态下的置顶评论。

    排版要求（用户指定）：
      · **不显示评论者**（因为已经限定只推 UP 主本人的评论，
        再显示昵称是多余的）
      · 直接写评论正文，换行分段
      · 末尾加跳转评论的按钮（opus 页 + #reply{rpid} 可精确定位）
    """
    tpl = tpls.get("top_comment") or DEFAULT_TEMPLATES["top_comment"]
    ctx = {"name": name, "title": (c or {}).get("content") or "",
           "text": (c or {}).get("content") or "",
           "url": (c or {}).get("url") or ""}
    head_text = safe_format(tpl, ctx)
    head = f"**{HEAD_TITLE['top_comment']}**"

    parts = []
    # ⚠️ 评论也能带图，内嵌在卡片里（和视频封面、动态配图一个规则）。
    #    以前只渲染纯文字，评论里的图全都丢了。
    _cimgs = [u for u in ((c or {}).get("images") or []) if u]
    _p = _pic_list(_cimgs, "top_comment")
    if _p:
        parts.append(_p)
    parts.append(f"**{_esc(head_text)}**")
    body = _esc((c or {}).get("content") or "")
    if body:
        # 只放正文，不再有「评论内容 / 评论者 / 点赞」这些标签行
        parts.append("\n".join(_indent_body(body, limit=300)))

    url = (c or {}).get("url") or ""
    buttons = []
    if url:
        buttons = [button("top_comment", BUTTON_LABEL["top_comment"], url)]
    return _compose(head, parts), buttons


def render_season(info, name: str, tpls: dict) -> tuple:
    """合集里新增了视频。

    info 是 SeasonPush：视频本体 + 所属合集名 + 小节名。
    ⚠️ 小节名可能取不到（接口没返回），那时就不显示"小节"这一行，
    而不是显示个空的。
    """
    tpl = tpls.get("season") or DEFAULT_TEMPLATES["season"]
    head_text = safe_format(tpl, {
        "name": name,
        "title": getattr(info, "title", "") or "",
        "season": getattr(info, "season_title", "") or "",
        "section": getattr(info, "section", "") or "",
        "bvid": getattr(info, "bvid", "") or "",
        "url": getattr(info, "url", "") or "",
    })
    head = f"**{HEAD_TITLE['season']}**"

    parts = []
    _p = _pic(getattr(info, "pic", ""), "season")
    if _p:
        parts.append(_p)
    parts.append(f"**{_esc(head_text)}**")

    rows = []
    section = getattr(info, "section", "") or ""
    if section:
        rows.append(("小节", _esc(section)))
    season_title = getattr(info, "season_title", "") or ""
    if season_title:
        rows.append(("合集", _esc(season_title)))
    block = _kv_block(rows)
    if block:
        parts.append(block)

    return _compose(head, parts), [
        button("season", BUTTON_LABEL["season"], getattr(info, "url", ""))]


def render(kind: str, info, name: str, tpls: dict, uid: int = 0) -> tuple:
    # uid 只有下播卡片用得到（按钮要指向 UP 主空间，而不是关掉的直播间），
    # 其余类型保持原行为，不传也不影响。
    if kind == "season":
        return render_season(info, name, tpls)
    if kind == "top_comment":
        return render_top_comment(info, name, tpls)
    if kind == "live":
        return render_live(info, name, tpls)
    if kind == "video":
        return render_video(info, name, tpls)
    if kind == "dynamic":
        return render_dynamic(info, name, tpls)
    if kind == "offline":
        return render_offline(info, name, tpls, uid=uid)
    raise ValueError(f"未知类型 {kind}")


def short_hint(kind: str, name: str) -> str:
    """简短提示：哪种提醒发生了什么。"""
    if kind == "top_comment":
        return f"{name} 置顶动态有新置顶评论"
    tips = {
        "live": f'{name} 开播啦！',
        "video": f'{name} 投稿了新视频！',
        "dynamic": f'{name} 更新了动态！',
        "offline": f'{name} 已下播。',
    }
    return tips.get(kind, f"{name} 有新动态！")
