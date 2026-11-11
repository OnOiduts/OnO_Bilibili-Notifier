# -*- coding: utf-8 -*-
"""给滚动条预留位置 —— 用真实 Chromium 量宽度，不验"源码里有没有这段字"。

背景（用户反馈）：页面高度随页签切换 / 日志加载 / 折叠展开变化，
滚动条出现或消失会让视口可用宽度少 9px，侧边栏、卡片、表格整体横向跳。
修法是 scrollbar-gutter: stable —— 无论有没有滚动条都把轨道留出来。

本测试锁三件事：
  1. 真浏览器里根滚动容器和 .log-list 的计算值确实是 stable；
  2. 内容少 / 内容多两种情况下，容器 clientWidth 都等于 offsetWidth - 9
     （轨道真的被预留了 9px，不是只写了个属性名），且两种情况下相等；
  3. 反向验证：把声明去掉后，计算值回到 auto、clientWidth 回到 offsetWidth。
     —— 这一步保证上面两条不是"怎么改都绿"的假断言。

⚠️ 沙盒的 headless Chromium 用 overlay 滚动条（长内容时 clientWidth 也不掉），
   所以这里验证的是「轨道确实被预留」，不是模拟真机 Windows 的 9px 跳动。
   真机（非 overlay）上，预留后短/长内容宽度恒定，跳动才会消失。
"""
import os
import re
import shutil
import sys
import pathlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
FAILS = []


def ck(name, cond):
    if cond:
        print("  [ok]", name)
    else:
        print("  [FAIL]", name)
        FAILS.append(name)


def load():
    css = pathlib.Path(SRC, "static", "style.css").read_text(encoding="utf-8")
    idx = pathlib.Path(SRC, "templates", "index.html").read_text(encoding="utf-8")
    inline = re.search(r"<style>(.*?)</style>", idx, re.S).group(1)
    inline = re.sub(r"\{\{[^}]*\}\}", "0", inline)
    return css, idx, inline


def reverse(css, inline):
    """把三处声明逐条摘掉（保留 @supports 条件本身），用于反向验证。"""
    pairs = [
        ("html { scrollbar-gutter: stable; }", "html { }"),
        (".sheet-body, .log-list { scrollbar-gutter: stable; }",
         ".sheet-body, .log-list { }"),
        ("#tplSheetBody { padding:15px; overflow:auto; scrollbar-gutter:stable; }",
         "#tplSheetBody { padding:15px; overflow:auto; }"),
    ]
    for old, new in pairs:
        css = css.replace(old, new)
        inline = inline.replace(old, new)
    return css, inline


def make_page(css, inline, content):
    return ("<!doctype html><html lang=\"zh-CN\" data-theme=\"dark\">"
            "<head><meta charset=\"utf-8\"><style>" + css + "</style><style>"
            + inline + "</style></head><body>"
            "<div id=\"box\" class=\"log-list\" style=\"width:300px;height:200px\">"
            "<div id=\"inner\" style=\"width:100%\">" + content + "</div></div>"
            "</body></html>")


JS = """() => {
  const b = document.getElementById('box'), i = document.getElementById('inner');
  const t = document.createElement('div'); t.className = 'tabs';
  document.body.appendChild(t);
  const tabsGutter = getComputedStyle(t).scrollbarGutter;
  t.remove();
  return {
    offset: b.offsetWidth, client: b.clientWidth,
    inner: i.getBoundingClientRect().width,
    gutter: getComputedStyle(b).scrollbarGutter,
    rootGutter: getComputedStyle(document.documentElement).scrollbarGutter,
    tabsGutter: tabsGutter,
    hasBar: b.scrollHeight > b.clientHeight
  };
}"""


def measure(pw, kw, css, inline):
    """返回 (短内容, 长内容) 两组真实测量值。"""
    b = pw.chromium.launch(**kw)
    out = []
    try:
        for content in ("<p>hi</p>", "<div style='height:1200px'></div>"):
            pg = b.new_page(viewport={"width": 1280, "height": 800})
            pg.set_content(make_page(css, inline, content))
            out.append(pg.evaluate(JS))
            pg.close()
    finally:
        b.close()
    return out


def main():
    css, idx, inline = load()

    # ── 静态部分：只查结构，真正结论靠下面的浏览器实测 ──
    ck("style.css 里根容器声明了 stable",
       "html { scrollbar-gutter: stable; }" in css)
    ck("有 @supports not 的老浏览器兜底",
       "@supports not (scrollbar-gutter: stable)" in css
       and "html { overflow-y: scroll; }" in css)
    ck("面板主体 / 日志列表同样预留",
       ".sheet-body, .log-list { scrollbar-gutter: stable; }" in css)
    ck("文案编辑弹层也预留",
       "#tplSheetBody { padding:15px; overflow:auto; scrollbar-gutter:stable; }" in idx)
    ck("登录页外链同一份 style.css（自动受益）",
       'href="/static/style.css' in pathlib.Path(SRC, "templates", "login.html")
       .read_text(encoding="utf-8"))
    tabs_rule = re.search(r"\.tabs\s*\{[^}]*\}", inline)
    ck("隐藏滚动条的 .tabs 没被误加 gutter",
       tabs_rule is not None and "scrollbar-gutter" not in tabs_rule.group(0))

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        ck("沙盒具备 playwright（缺了就没法实测）", False)
        return 1 if FAILS else 0
    exe = (shutil.which("chromium") or shutil.which("google-chrome")
           or shutil.which("chrome"))
    kw = {"args": ["--no-sandbox"]}
    if exe:
        kw["executable_path"] = exe

    with sync_playwright() as pw:
        short, long = measure(pw, kw, css, inline)
        ck("构造出了真的『无滚动条 / 有滚动条』两种情况",
           short["hasBar"] is False and long["hasBar"] is True)
        ck("根容器计算值 = stable", short["rootGutter"] == "stable")
        ck(".log-list 计算值 = stable", short["gutter"] == "stable")
        ck("短内容时真的预留了 9px 轨道",
           short["client"] == short["offset"] - 9)
        ck("长内容时轨道宽度不变",
           long["client"] == long["offset"] - 9)
        ck("内容由少变多，可用宽度恒定（不跳）",
           short["client"] == long["client"])
        ck("内部元素按预留后的宽度排布",
           short["inner"] == short["client"] and long["inner"] == long["client"])
        ck(".tabs 仍是 auto（没被连带预留）",
           short["tabsGutter"] == "auto")

        # ── 反向验证：去掉声明后必须全部不成立 ──
        rcss, rinline = reverse(css, inline)
        rshort, rlong = measure(pw, kw, rcss, rinline)
        ck("[反向] 去掉声明后计算值回到 auto",
           rshort["rootGutter"] == "auto" and rshort["gutter"] == "auto")
        ck("[反向] 不再预留轨道（clientWidth 回到 offsetWidth）",
           rshort["client"] == rshort["offset"]
           and rlong["client"] == rlong["offset"])
        ck("[反向] 正向与反向测得不同，测试确实咬得住",
           short["client"] != rshort["client"])

    print("结果：", "全部通过" if not FAILS else "FAIL %d 项" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
