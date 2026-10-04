#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开关滑块（.sw-btn）动画回归。

用户反馈：「开关动画有对冲原点移动到元素外了」——
滑块会从按钮里滑出去、滑到按钮外边。

两个真因，都是 v1.78.0 加"按钮光泽扫过"时带进来的：
  1) 光泽规则写成 button::after（裸 button），而滑块正是 .sw-btn::after。
     inset:0 把 16x16 的圆点撑成填满整块的方块，
     hover 时的 translateX(130%) 再把它顶到按钮外面。
     同一条规则还会把页签下划线（.mini-tab.on::after）换成一整块渐变。
  2) 滑块靠改 left（3px → 21px）移动，跟光泽的 translateX 抢同一个属性，
     两个过渡叠在一起 = 对冲。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
CSS = os.path.join(ROOT, "static", "style.css")
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


def _noc(s):
    """去掉 CSS 注释，避免注释里的字样被当成真规则。"""
    return re.sub(r"/\*.*?\*/", "", s, flags=re.S)


# ---------------------------------------------------------------------------
def t_knob_uses_transform():
    """滑块必须靠 transform 位移，不能靠改 left。"""
    print("[滑块位移方式]")
    html = _noc(_read(HTML))
    m = re.search(r"\.sw-btn\.on::after\s*\{([^}]*)\}", html)
    ck("存在 .sw-btn.on::after", bool(m))
    if not m:
        return
    body = m.group(1)
    ck("开到位用 transform", "transform" in body, body)
    ck("开到位不再改 left", not re.search(r"\bleft\s*:", body), body)
    # 位移量：18px = 21 - 3，跟原来 left 的起终点一致
    ck("位移量 18px", re.search(r"translateX\(\s*18px\s*\)", body) is not None,
       body)
    base = re.search(r"\.sw-btn::after\s*\{([^}]*)\}", html)
    ck("滑块基础定义存在", bool(base))
    if base:
        ck("基础态停在 left:3px", re.search(r"left\s*:\s*3px", base.group(1))
           is not None, base.group(1))
        ck("滑块是圆的", "border-radius:50%" in base.group(1).replace(" ", ""),
           base.group(1))


def t_knob_transition():
    """滑块要有过渡（不是硬切），且过渡的是 transform。"""
    print("[滑块过渡]")
    css = _noc(_read(CSS))
    m = re.search(r"\.sw-btn::after\s*\{([^}]*)\}", css)
    ck("style.css 里有 .sw-btn::after 过渡规则", bool(m))
    if m:
        ck("过渡 transform", "transform" in m.group(1), m.group(1))
    html = _noc(_read(HTML))
    b = re.search(r"\.sw-btn::after\s*\{([^}]*)\}", html)
    ck("index.html 里也过渡 transform", b and "transform" in b.group(1))
    # 死样式检查：早期草稿的 .knob / .switch 页面上根本没有
    ck("不再写死样的 .knob", ".knob" not in css)


def t_gloss_not_on_bare_button():
    """光泽不许挂裸 button —— 那会把滑块顶出去。"""
    print("[光泽挂载点]")
    css = _noc(_read(CSS))
    ck("裸 button::after 不再挂光泽",
       re.search(r"(?<![\w.])button::after", css) is None)
    ck("光泽挂在 .btn::after 上",
       re.search(r"\.btn::after\s*\{", css) is not None)
    ck("hover 也只在 .btn 上",
       re.search(r"(?<![\w.])button:hover::after", css) is None)


def t_knob_not_excluded():
    """滑块不能被 content:none 的排除名单删掉。

    排除名单里已经有 .sw::after / .grp-bg::after 这些；
    把 .sw-btn::after 也加进去，滑块本身就消失了（开关看不见把手）。
    """
    print("[滑块不能被删]")
    css = _noc(_read(CSS))
    for m in re.finditer(r"([^{}]*::after[^{}]*)\{([^}]*content\s*:\s*none[^}]*)\}",
                         css):
        sel = m.group(1)
        if ".sw-btn" in sel:
            ck("排除名单不含 .sw-btn::after", False, sel)
            return
    ck("排除名单不含 .sw-btn::after", True)


def t_underline_safe():
    """页签下划线的 ::after 也不该被光泽污染。"""
    print("[页签下划线]")
    css = _noc(_read(CSS))
    ck("下划线规则还在",
       re.search(r"\.mini-tab\.on::after", css) is not None)
    ck("下划线是 2px 高",
       re.search(r"\.mini-tab\.on::after[^}]*height\s*:\s*2px", css, re.S)
       is not None)


def main():
    t_knob_uses_transform()
    t_knob_transition()
    t_gloss_not_on_bare_button()
    t_knob_not_excluded()
    t_underline_safe()
    print("")
    if FAILS:
        print("FAILED %d: %s" % (len(FAILS), FAILS))
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
