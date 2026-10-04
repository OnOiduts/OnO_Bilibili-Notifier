#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""品牌区（logo）动效回归。

用户反馈：「logo 现在的动效太土了，我只需要鼠标移上去后面的图片
往左斜和往左偏移一点大一点就行了」。

于是把原来三处常驻动画（底纹 14s 漂浮 + 品牌点 3.4s 呼吸 + 掠过扫
光泽）全部去掉，只留**鼠标掠过**才有的一组变换，作用在后面的图片
（.brand::after）上：

    translateY(-50%) 垂直居中  →  translateX(-4px) 往左挪
    →  rotate(-8deg) 往左斜     →  scale(1.18) 大一点

⚠️ 三个坑，写测试盯住，防止以后改回去：
  · .brand::after 靠 translateY(-50%) 垂直居中，hover 的 transform 会
    整条覆盖它 —— 漏了这个位移，logo 会掉到下半部分。
  · translateX 必须写在 rotate **前面**，否则位移会沿旋转后的坐标系
    斜着跑，"往左"就变成"往左上"。
  · 常驻的循环动画（漂浮 / 呼吸 / 扫光）一律不许再出现：用户的原话
    是"只需要鼠标移上去"。那套是"土"的来源，也容易让文字看着在晃。
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

HTML = os.path.join(ROOT, "templates", "index.html")

FAILS = []


def ck(name, cond, extra=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, extra))
        FAILS.append(name)


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _strip_comments(s):
    return re.sub(r"/\*.*?\*/", "", s, flags=re.S)


def _block(s, sel):
    """取出某条规则的花括号内容（去掉注释后的文本里找）。"""
    m = re.search(re.escape(sel) + r"\s*\{([^{}]*)\}", s)
    return m.group(1) if m else ""


def t_hover_transform():
    """hover 时图片：往左斜 + 往左挪 + 放大。"""
    print("[悬停变换]")
    s = _strip_comments(_read(HTML))
    hov = _block(s, ".brand:hover::after")
    ck("hover 规则存在", hov != "", "找不到 .brand:hover::after")
    ck("保留垂直居中位移", "translateY(-50%)" in hov, hov)
    ck("往左斜（负角度）", bool(re.search(r"rotate\(-\s*\d", hov)), hov)
    ck("往左挪（负位移）", bool(re.search(r"translateX\(-\s*\d", hov)), hov)
    ck("放大一点", bool(re.search(r"scale\(1\.\d+\)", hov)), hov)
    # 顺序：translateX 必须在 rotate 之前
    ix = hov.find("translateX")
    ir = hov.find("rotate")
    ck("位移写在旋转之前", ix >= 0 and ir >= 0 and ix < ir,
       "translateX@%s rotate@%s" % (ix, ir))
    # 过渡：别太快也别太慢
    base = _block(s, ".brand::after")
    ck("有过渡", "transition" in base, base[-80:])
    ck("过渡含 transform", "transform" in base, base[-80:])
    ck("静止态无变换", "translateY(-50%)" in base and "rotate" not in base,
       base[-80:])


def t_no_legacy_loop():
    """旧的常驻循环动画必须彻底清掉（这是"土"的来源）。"""
    print("[去掉常驻动画]")
    s = _strip_comments(_read(HTML))
    for name in ("logoFloat", "brandDot"):
        ck("%s 关键帧已删" % name,
           ("@keyframes " + name) not in s)
        ck("%s 不再被引用" % name,
           not re.search(r"animation[^;]*\b%s\b" % name, s))
    ck("底纹不再挂 animation",
       "animation" not in _block(s, ".brand::after"))
    ck("品牌点不再挂 animation",
       "animation" not in _block(s, ".brand .dot"))
    ck("不再有 animation-duration 提速",
       "animation-duration" not in _block(s, ".brand:hover .dot"))
    ck("扫光伪元素已删", ".brand::before" not in s)
    ck("扫光 hover 已删", ".brand:hover::before" not in s)


def t_no_regression():
    """别把已有的居中 / 主题色 / 结构改坏。"""
    print("[不回归]")
    s = _strip_comments(_read(HTML))
    base = _block(s, ".brand::after")
    ck("底纹仍居中", "translateY(-50%)" in base, base[-80:])
    ck("底纹仍靠 --logo 变量", "--logo" in base, base[:120])
    ck("日间仍反色", 'data-theme="light"] .brand::after' in s)
    ck("文字层仍在上", "z-index:1" in _block(s, ".brand > *"))
    # 文字不能被倾斜：倾斜只作用在伪元素（图片）上
    ck("倾斜没落到文字上", "rotate" not in _block(s, ".brand > *"))
    ck("品牌区本身不倾斜", "rotate" not in _block(s, ".brand"))
    ck("掠过时状态点有反馈",
       "scale(" in _block(s, ".brand:hover .dot"))
    ck("尊重减少动效", "prefers-reduced-motion" in s)


def t_structure():
    """品牌区结构还在（动效挂的是真实存在的元素）。"""
    print("[结构]")
    raw = _read(HTML)
    ck("品牌区存在", 'class="brand"' in raw)
    ck("品牌点存在", 'class="dot"' in raw)
    ck("底纹靠 --logo 变量", "--logo" in raw)


def main():
    t_hover_transform()
    t_no_legacy_loop()
    t_no_regression()
    t_structure()
    print("")
    if FAILS:
        print("FAILED %d: %s" % (len(FAILS), FAILS))
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
