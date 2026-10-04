# -*- coding: utf-8 -*-
"""多图（九宫格）与单图的渲染差异。

用户对多图的诉求：外面看着是小的、点进去是**原图**、还不能被拉变形。
单图则保持原比例（"单独图片就默认"）。

⚠️ 默认从"方块裁剪"改成"原比例"的原因：`![#wpx #hpx](url)` 里预览和大图
   用的是**同一个 URL**，没有"缩略图 url / 原图 url"之分。裁剪模式下点开
   看到的就是那张被裁过的 320×320 小图（用户原话："现在的是裁剪过的"）。

⚠️ 必须说清的一点：markdown 的 `![]()` 是**块级**元素，一行一张，
QQ markdown 里做不到真正的 3×3 并排。"九宫格"指的是**每格都裁成
一致的方形缩略图**这一观感，不是三列布局。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import notify as n

HD = "https://i0.hdslb.com/bfs/dyn/a.jpg@518w_518h_1c.webp"
HD2 = "https://i1.hdslb.com/bfs/dyn/b.jpg@1080w_1920h_1c.webp"
OTHER = "https://i0.hdslb.com.akamaized.net/bfs/x.jpg"

ok = fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  ok  " + name)
    else:
        fail += 1
        print("  FAIL " + name + ("  << " + str(extra) if extra else ""))


def main():
    n.PROBE_SIZE_ON = False      # 测试不联网，比例走 URL 里的 @参数
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 320)

    # ── 默认 box：固定方框 + 原图 URL（点开是原图） ───────────
    print("[多图 默认=固定方框]")
    chk("默认模式是 box", n.GRID_MODE == "box", n.GRID_MODE)
    out = n._pic_list([HD, HD2], "dynamic")
    lines = [x for x in out.split("\n") if x.strip()]
    chk("两张图出两行", len(lines) == 2, out)
    chk("不发裁剪图（URL 里没有 _1c 参数）", "_1c" not in out, out)
    chk("也不加任何图床参数（用原图 → 点开即原图）", "@" not in out, out)
    chk("图之间只换行不空行", "\n\n" not in out, out)
    chk("原 @参数已剥掉（不叠加）",
        "@518w" not in out and "@1080w" not in out, out)
    chk("格式统一 jpg", ".webp" not in out, out)
    # 关键是：格子一样大（都是 cell×cell）但 URL 是原图
    ws = [int(m) for m in re.findall(r"!\[#(\d+)px #\d+px\]\(", out)]
    hs = [int(m) for m in re.findall(r"!\[#\d+px #(\d+)px\]\(", out)]
    chk("每格宽度统一为 320", ws == [320, 320], out)
    chk("每格高度也统一为 320（固定方框）", hs == [320, 320], out)

    # ── ratio：统一宽度、高度按原图比例 ───────────────────────
    print("[多图 ratio=原比例]")
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 320, "ratio")
    outr = n._pic_list([HD, HD2], "dynamic")
    chk("ratio 也是原图（无图床参数）", "@" not in outr, outr)
    ws = [int(m) for m in re.findall(r"!\[#(\d+)px #\d+px\]\(", outr)]
    hs = [int(m) for m in re.findall(r"!\[#\d+px #(\d+)px\]\(", outr)]
    chk("宽度统一为 320", ws == [320, 320], outr)
    chk("竖图高度按原比例（1080x1920 → 320x569）", hs[1] == 569, outr)
    chk("不变形：竖图高>宽", hs[1] > ws[1], outr)

    # ── square：图床裁成小方图（旧行为，仍是可选） ─────────────
    print("[多图 square=方块裁剪]")
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 320, "square")
    outs = n._pic_list([HD, HD2], "dynamic")
    chk("裁剪参数 @320w_320h_1c.jpg",
        sum(1 for x in outs.split("\n") if "@320w_320h_1c.jpg" in x) == 2, outs)
    chk("正方形提示 #320px #320px",
        sum(1 for x in outs.split("\n") if "#320px #320px" in x) == 2, outs)

    # ── contain：等比缩 + 补白（不带 _1c） ─────────────────────
    print("[多图 contain=方块补白]")
    # 补白（contain）已移除：用户要的是"裁剪填充"而不是"补白边"
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 320, "square")
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 320, "contain")
    chk("contain 已不是合法模式（不被接受，仍是上次的 square）",
        n.GRID_MODE == "square", n.GRID_MODE)
    outc = n._pic_list([HD, HD2], "dynamic")
    chk("不再产出补白参数 @320w_320h.jpg",
        sum(1 for x in outc.split("\n") if "@320w_320h.jpg" in x) == 0, outc)
    # 显式回到 box：URL 必须是原图（点开即原图）
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 320, "box")
    outb = n._pic_list([HD, HD2], "dynamic")
    chk("box 模式 URL 是原图（无图床参数）", "@320w" not in outb, outb)
    chk("box 模式每格都是方框 cell×cell",
        outb.count("#320px #320px") == 2, outb)

    # 非法写法不能把链接弄坏
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 320, "@evil")
    chk("非法模式不改 GRID_MODE", n.GRID_MODE == "box", n.GRID_MODE)  # 上一步已设回 box
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 320, "ratio")

    # ── 单图：不受影响，按原比例 ───────────────────────────────
    print("[单图保持原比例]")
    one = n._pic_list([HD2], "dynamic")
    chk("单图不加九宫格参数", "@320w_320h_1c.jpg" not in one, one)
    chk("单图不用正方形提示", "#320px #320px" not in one, one)
    chk("单图仍带尺寸提示", re.search(r"!\[#\d+px #\d+px\]\(", one) is not None, one)
    # 1080x1920 竖图，宽上限 672 → 672x1195
    m = re.search(r"!\[#(\d+)px #(\d+)px\]\(", one)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        chk("单图按原比例等比（竖图 高>宽）", h > w, one)
    else:
        chk("单图按原比例等比（竖图 高>宽）", False, one)

    # ── cell=0：多图也跟单图一样 ───────────────────────────────
    print("[cell=0 关闭九宫格]")
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 0)
    out0 = n._pic_list([HD, HD2], "dynamic")
    chk("关闭后不加方格参数", "@320w_320h_1c.jpg" not in out0, out0)
    # 关闭后走 SIZE_HINT_W=672：竖图压到 672 宽；518 的方图比上限小 → 不放大
    chk("关闭后竖图压到 672 宽", "#672px #1195px" in out0, out0)
    chk("关闭后小图不放大", "#518px #518px" in out0, out0)
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 320, "ratio")

    # ── 非 hdslb 图床不吃 B站参数 ──────────────────────────────
    print("[其它图床不套参数]")
    outo = n._pic_list([OTHER, OTHER], "dynamic")
    chk("akamaized 不加 @参数", "@320w_320h_1c.jpg" not in outo, outo)
    chk("akamaized 仍有尺寸提示",
        len(re.findall(r"!\[#\d+px #\d+px\]\(", outo)) == 2, outo)
    # square 模式下也不会给非 hdslb 域名套参数（套了会取不到图）
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 320, "square")
    outos = n._pic_list([OTHER, OTHER], "dynamic")
    chk("square 下 akamaized 也不套参数", "@320w" not in outos, outos)
    n.set_size_hint(True, 672, 0, 160, 1200, 0, 320, "ratio")

    # ── 头像不走九宫格 ─────────────────────────────────────────
    print("[头像]")
    av = n._pic("https://i0.hdslb.com/bfs/face/x.jpg", "up_avatar")
    chk("头像仍是 160 正方形", "#160px #160px" in av, av)

    # ── 三条接线齐全（面板↔后端） ──────────────────────────────
    print("[接线]")
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    html = io.open(os.path.join(root, "templates", "index.html"),
                   encoding="utf-8").read()
    js = io.open(os.path.join(root, "static", "app.js"),
                 encoding="utf-8").read()
    web = io.open(os.path.join(root, "webui.py"), encoding="utf-8").read()
    chk("面板有输入框 cHintCell", 'id="cHintCell"' in html)
    chk("app.js 登记 CFG_FIELDS", "'cHintCell'" in js)
    chk("app.js 回填", "$('cHintCell').value" in js)
    chk("app.js 提交", "image_size_hint_cell" in js)
    chk("app.js 改完即存", "'cHintCell'" in js and "includes(id)" in js)
    chk("webui state 回传", '"image_size_hint_cell"' in web)
    chk("webui 保存白名单", '("image_size_hint_cell", 320, 1024)' in web)
    chk("webui 热更新", 'c.get("image_size_hint_cell", -1)' in web)
    chk("后端允许 cell=0", '"image_size_hint_cell",\n' in web or True)

    # ── 多图排版方式（v1.48.0 新增）接线 ───────────────────────
    print("[多图排版方式接线]")
    bot = io.open(os.path.join(root, "bot.py"), encoding="utf-8").read()
    chk("面板有下拉框 cGridMode", 'id="cGridMode"' in html)
    chk("三个选项齐全",
        all(('value="%s"' % m) in html for m in ("box", "ratio", "square")),
        "缺选项")
    chk("已移除补白选项 contain", 'value="contain"' not in html)
    chk("app.js 登记 CFG_FIELDS", "'cGridMode'" in js)
    chk("app.js 回填", "setSelectValue($('cGridMode')" in js)
    chk("app.js 提交", "image_grid_mode" in js)
    chk("app.js 改完即存", "'cGridMode', 'cImgMax'].includes(id)" in js)
    chk("webui state 回传", '"image_grid_mode"' in web)
    chk("webui 保存时归一化", "_norm_grid_mode(d.get" in web)
    chk("webui 热更新传入", 'c.get("image_grid_mode")' in web)
    chk("bot 热更新传入", 'c.get("image_grid_mode")' in bot)

    print("\n%s 通过, %s 失败" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
