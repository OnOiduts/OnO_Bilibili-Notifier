#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录页背景：四角分散 + 每次打开随机。

为什么需要这个测试
------------------
背景原本四张图都从"页面中心偏一点"出发，飘起来挤成一坨，看着就是叠在一起。
要求是：

  1. 四张图各占一个角（左上 / 右上 / 左下 / 右下），中间留给卡片；
  2. **每次打开都不一样** —— 哪张图落哪个角随机、大小随机、
     角内位置有抖动、动画时长与方向也随机。

这里盯两件事：

  * CSS 里那套四角定位是**兜底**（JS 没跑起来时也得是不重叠的四个角）；
  * JS 里真的做了随机，而不是写死一套位置。

⚠️ 剥注释：注释里提到的旧写法会被当成真代码，导致"把改动退回旧写法、
   断言应该变红"的反向验证变成假通过 —— 这个坑在本项目里踩过不止一次。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOGIN = os.path.join(HERE, "..", "src", "templates", "login.html")

_results = []


def _check(name, fn):
    try:
        fn()
        _results.append((True, name, ""))
    except AssertionError as e:
        _results.append((False, name, str(e)))
    except Exception as e:  # noqa: BLE001
        _results.append((False, name, "%s: %s" % (type(e).__name__, e)))


def _read(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read()


def _strip_js_comments(src: str) -> str:
    """去掉 JS 的 /* */ 与 // 注释（不含字符串里的 //）。"""
    out = []
    i = 0
    n = len(src)
    q = None
    while i < n:
        ch = src[i]
        if q:
            if ch == "\\":
                out.append(src[i:i + 2])
                i += 2
                continue
            if ch == q:
                q = None
            out.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            q = ch
            out.append(ch)
            i += 1
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if src.startswith("//", i):
            end = src.find("\n", i)
            i = n if end < 0 else end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _html():
    return _read(LOGIN)


def _js():
    """只取登录页最后那块脚本（背景随机化与登录逻辑都在里面）。"""
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", _html(), re.S)
    assert blocks, "登录页里没有 <script>"
    return _strip_js_comments(blocks[-1])


def _css():
    """只取 login.html 内联 <style> 里的 .login-bg 部分。"""
    styles = re.findall(r"<style>(.*?)</style>", _html(), re.S)
    assert styles, "登录页里没有 <style>"
    return styles[-1]


def _corner_list():
    """解析四角基准：[[17, 20], [83, 20], [17, 80], [83, 80]] 这样的定义。"""
    m = re.search(r"corners\s*=\s*(\[\[.*?\]\])", _js(), re.S)
    assert m, "没找到四角基准 corners = [[..],[..],[..],[..]]"
    pts = re.findall(r"\[\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\]", m.group(1))
    return [(float(a), float(b)) for a, b in pts]


# ------------------------------------------------------------ 1. 四角基准

def test_four_corners_defined():
    pts = _corner_list()
    assert len(pts) == 4, "四角基准应有 4 个点，实际 %d 个" % len(pts)


def test_four_corners_are_distinct_quadrants():
    """四个点必须落在四个不同象限（左上/右上/左下/右下），不能有两个在同一角。"""
    pts = _corner_list()
    quad = set(("L" if x < 50 else "R") + ("T" if y < 50 else "B") for x, y in pts)
    assert len(quad) == 4, "四角基准落在了同一区域：%s（应覆盖 LT/RT/LB/RB）" % sorted(quad)


def test_corners_not_near_center():
    """角点必须是**真正的角**，不能只是"靠近四个方位"。

    ⚠️ 这条断言以前写得太松（只要求 <=25%% 或 >=75%%），于是 17%%/83%% ×
       20%%/80%% 这种"四个方位"式的布局照样通过 —— 看着像四个角，
       实际全挤在中间一大块里，飘起来就叠在一起，正是被指出的毛病。
       现在收紧到 <=15%% / >=85%%（横）与 <=20%% / >=80%%（纵）。
    """
    for x, y in _corner_list():
        assert abs(x - 50) >= 35, "横向 %s%% 不是角（应 <=15%% 或 >=85%%）" % x
        assert abs(y - 50) >= 30, "纵向 %s%% 不是角（应 <=20%% 或 >=80%%）" % y


# ------------------------------------------------------------ 2. 随机

def test_corners_shuffled():
    """角落必须洗牌 —— 不洗的话每次都是同一张图在同一个角，等于没随机。

    ⚠️ 必须连**循环头**一起查，只查循环体里的交换语句是不够的：
    把 `for (k = len-1; k > 0; k--)` 改成 `for (k = 0; k < 0; k--)` 之后循环根本
    不执行、四张图永远落固定角，但交换语句还在 —— 只查语句会假通过。
    """
    js = _js()
    assert "Math.random" in js, "整段脚本没有任何随机"
    m = re.search(r"for\s*\(\s*var\s+k\s*=\s*([^;]+);\s*k\s*([><]=?)\s*([^;]+);", js)
    assert m, "没找到洗牌循环"
    start, op, end = m.group(1).strip(), m.group(2), m.group(3).strip()
    assert "corners.length" in start and "- 1" in start, \
        "洗牌应从最后一个下标开始，实际起始条件：%s" % start
    assert op == ">" and end.strip() == "0", \
        "洗牌循环应递减到 0（k > 0），实际：k %s %s" % (op, end)
    assert re.search(r"corners\[k\]\s*;\s*corners\[k\]\s*=\s*corners\[j\]", js), \
        "没看到交换 corners[k] 与 corners[j] 的写法"


def test_width_randomized():
    """大小随机：140~370px 区间由 Math.random 决定，不能写死。"""
    js = _js()
    m = re.search(r"var\s+w\s*=\s*([^;]+);", js)
    assert m, "没找到宽度赋值 var w = ..."
    assert "Math.random" in m.group(1), "宽度是写死的：%s" % m.group(1).strip()


def test_position_jitter_randomized():
    """角内抖动：jx / jy 由 Math.random 决定。"""
    js = _js()
    for name in ("jx", "jy"):
        m = re.search(r"var\s+%s\s*=\s*([^;]+);" % name, js)
        assert m, "没找到抖动变量 %s" % name
        assert "Math.random" in m.group(1), "%s 是写死的：%s" % (name, m.group(1).strip())


def test_jitter_keeps_images_in_corners():
    """角内抖动幅度不能大 —— 角点已经贴边，抖太多会飘回中间。

    解析 jx / jy 的半幅：Math.random() * 8 - 4 → 半幅 4。
    """
    js = _js()
    mx = re.search(r"var\s+jx\s*=.*?Math\.random\(\)\s*\*\s*([\d.]+)\s*-\s*([\d.]+)", js, re.S)
    my = re.search(r"var\s+jy\s*=.*?Math\.random\(\)\s*\*\s*([\d.]+)\s*-\s*([\d.]+)", js, re.S)
    assert mx and my, "没解析到 jx / jy 的抖动幅度"
    ax, ay = float(mx.group(2)), float(my.group(2))
    assert ax <= 5, "横向抖动 ±%s%% 太大，会把图拽回中间（应 <=5%%）" % ax
    assert ay <= 4, "纵向抖动 ±%s%% 太大，会把图拽回中间（应 <=4%%）" % ay


def test_motion_randomized():
    """动画时长与方向也随机 —— 全都同步飘会像一整块在动。"""
    js = _js()
    assert re.search(r"animationDuration\s*=\s*[^;]*Math\.random", js), "动画时长没随机"
    assert re.search(r"animationDirection\s*=\s*[^;]*Math\.random", js), "动画方向没随机"


def test_all_four_images_randomized():
    """四张图都要参与随机，不能只随机其中两张（另外两张仍叠着）。"""
    js = _js()
    m = re.search(r"ids\s*=\s*\[([^\]]*)\]", js)
    assert m, "没找到参与随机的图片 id 列表"
    ids = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
    assert len(ids) == 4, "参与随机的图应有 4 张，实际 %d 张：%s" % (len(ids), ids)


# ------------------------------------------------------------ 3. 真跑一遍

_RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
function el() {
  return {style: {}, classList: {add: function () {}, remove: function () {}},
          checked: false, value: '', addEventListener: function () {},
          textContent: '', submit: function () {}};
}
const out = [];
for (let n = 0; n < 120; n++) {
  const els = {};
  ['bgLogo', 'bgIco', 'bg1', 'bg2', 'form', 'btn', 'pwd', 'remember', 'err']
    .forEach(function (i) { els[i] = el(); });
  const sb = {
    document: {getElementById: function (i) { return els[i] || null; }},
    window: {fetch: function () {
      return {then: function () { return {then: function () {},
                                          catch: function () {}}; }};},
             location: {href: '/'}},
    Math: Math, JSON: JSON, console: console
  };
  vm.createContext(sb);
  vm.runInContext(code, sb);
  const rec = {};
  ['bgLogo', 'bgIco', 'bg1', 'bg2'].forEach(function (i) {
    const e = els[i];
    // 按角锚定后 left/right 二选一：能取到 left 就是靠左，否则看 right。
    const l = (e.style.left && e.style.left !== 'auto')
      ? parseFloat(e.style.left)
      : 100 - parseFloat(e.style.right);
    const t = (e.style.top && e.style.top !== 'auto')
      ? parseFloat(e.style.top)
      : 100 - parseFloat(e.style.bottom);
    rec[i] = (l < 50 ? 'L' : 'R') + (t < 50 ? 'T' : 'B');
  });
  out.push(rec);
}
console.log(JSON.stringify(out));
"""


def test_shuffle_changes_each_load():
    """真的把脚本跑 120 次：同一张图在不同次打开应落到**不同**的角。

    静态检查查不出"循环写了但没执行"这种退化 —— 只有真跑才知道。

    环境没有 node 就跳过：静态断言仍在，不算漏。
    """
    import json
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        return

    raw = re.findall(r"<script[^>]*>(.*?)</script>", _html(), re.S)[-1]
    d = tempfile.mkdtemp()
    try:
        p_src = os.path.join(d, "s.js")
        p_run = os.path.join(d, "r.js")
        with io.open(p_src, "w", encoding="utf-8") as f:
            f.write(raw)
        with io.open(p_run, "w", encoding="utf-8") as f:
            f.write(_RUNNER)
        r = subprocess.run([node, p_run, p_src], capture_output=True, text=True)
        assert r.returncode == 0, "脚本跑不起来：%s" % r.stderr[:200]
        recs = json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    assert len(recs) == 120, "应跑 120 次"
    ids = ["bgLogo", "bgIco", "bg1", "bg2"]
    for i in ids:
        seen = set(rec[i] for rec in recs)
        assert len(seen) > 1, \
            "%s 每次都落在同一个角（%s）—— 洗牌没起作用" % (i, sorted(seen))
    for n, rec in enumerate(recs):
        assert len(set(rec.values())) == 4, \
            "第 %d 次四张图有重叠：%s（应四角各一个）" % (n, rec)


# ------------------------------------------------------------ 4. 定位正确

def test_no_calc_centering():
    """⚠️ 不许再用 calc(角点% - 半个宽度) 做中心补偿。

    这个写法被真实分辨率复算推翻过：1366×768 上 11% 只有 150px，
    减去半个宽度 185px 得到 -35px，图片被推到屏幕外 —— 表现就是
    「左下/右下两个角空着、能看见的几张挤在左上角」。
    现在改成按角锚定（靠右用 right、靠下用 bottom），数学上出不了界。
    """
    js = _js()
    assert not re.search(r"style\.left\s*=\s*'calc\(", js), \
        "left 又用了 calc 中心补偿 —— 会算出负值把图推出屏幕"
    assert not re.search(r"style\.top\s*=\s*'calc\(", js), \
        "top 又用了 calc 中心补偿 —— 会算出负值把图推出屏幕"


def test_anchors_by_corner_not_center():
    """按角锚定：靠左的角用 left、靠右的角用 right（100 - 角点），
    靠上用 top、靠下用 bottom。另一侧必须清成 auto，否则两边打架。"""
    js = _js()
    assert re.search(r"px\s*<\s*50", js), "没有按角点判断靠左还是靠右"
    assert re.search(r"py\s*<\s*50", js), "没有按角点判断靠上还是靠下"
    assert re.search(r"100\s*-\s*px", js), "靠右的角没换算成 right = 100 - px"
    assert re.search(r"100\s*-\s*py", js), "靠下的角没换算成 bottom = 100 - py"
    # 四个分支都要清掉对侧
    assert js.count("= 'auto'") >= 4, "对侧属性没清干净，left/right 会打架"


def test_width_uses_vw_not_px():
    """宽度用 vw 而不是 px：px 写死在小屏上会占满整边，在大屏上又太小。"""
    js = _js()
    assert re.search(r"style\.width\s*=\s*w\.toFixed\(\d\)\s*\+\s*'vw'", js), \
        "宽度不是 vw —— 小屏会撑爆、大屏会缩成一点"
    assert not re.search(r"style\.width\s*=\s*w\s*\+\s*'px'", js), \
        "宽度还在用 px"


# ------------------------------------------------------------ 4. CSS 兜底

def _bg_class_positions():
    """返回 {类名: (水平边, 垂直边)}，只认 login.html 内联样式里的 .bg-* 规则。"""
    css = _css()
    out = {}
    for cls in ("bg-a", "bg-b", "bg-c", "bg-d"):
        m = re.search(r"\.login-bg\s+\." + cls + r"\s*\{(.*?)\}", css, re.S)
        assert m, "CSS 里没有 .login-bg .%s 的兜底定位" % cls
        body = m.group(1)
        h = "left" if "left:" in body else ("right" if "right:" in body else "?")
        v = "top" if "top:" in body else ("bottom" if "bottom:" in body else "?")
        out[cls] = (h, v)
    return out


def test_css_fallback_four_distinct_corners():
    """JS 不执行时也必须四角分散 —— 兜底本身不能是叠在一起的中心布局。"""
    pos = _bg_class_positions()
    combos = set(pos.values())
    assert len(combos) == 4, \
        "CSS 兜底有重叠：%s（四张图应分别占 左上/右上/左下/右下）" % pos


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            _check(name, fn)
    bad = [r for r in _results if not r[0]]
    for ok, name, err in _results:
        print("%s %s%s" % ("✅" if ok else "❌", name, ("  → " + err) if err else ""))
    print("\n%d/%d 通过" % (len(_results) - len(bad), len(_results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
