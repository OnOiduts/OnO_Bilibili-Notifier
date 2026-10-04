# -*- coding: utf-8 -*-
"""动画性能 + 折叠展开动画。

反馈：「动画有点卡」「折叠卡片展开的时候也可以有动画」。

折叠没动画的根因：toggle-group 调 renderGroups() 整块重画，
新卡片是带着 .open 一起插入 DOM 的 —— 浏览器把它当**初始状态**，
grid-template-rows 的 0fr→1fr 根本没有起点可插值，于是硬切。

卡顿的根因（按影响排序）：
1. initGlow 的 rAF 循环**永不停止**，且每帧改的是全屏 radial-gradient
   的坐标 → 鼠标不动也在每帧重绘整屏。
2. 背景光斑带 filter:blur(72px) 又带 scale 动画 → blur 每帧重新光栅化。
3. 入场动画用 filter:blur() 做"从模糊到清晰" → 同上。
4. 上百个 button 的渐变伪元素一直参与绘制。
5. 列表行全部跑入场动画（日志一页几十上百行，10 秒重画一次）。
"""
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
HTML = os.path.join(ROOT, 'templates', 'index.html')
CSS = os.path.join(ROOT, 'static', 'style.css')
JS = os.path.join(ROOT, 'static', 'app.js')


def _read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def _no_css_comment(s):
    """去掉 CSS 注释再断言 —— 注释里提到的旧写法会被朴素子串判断误伤。"""
    return re.sub(r'/\*.*?\*/', '', s, flags=re.S)


def _no_js_comment(s):
    """去掉 JS 注释。注释里出现 renderGroups() 会被当成"仍在重画"。"""
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)
    return re.sub(r'(?m)^\s*//.*$', '', s)


# ---------------- ① 折叠展开必须有动画 ----------------

def test_toggle_group_does_not_rerender():
    """点群标题只切 class，不整块重画 —— 否则展开动画被吃掉。"""
    js = _no_js_comment(_read(JS))
    m = re.search(r"'toggle-group'\(el\)\s*\{(.*?)\n  \},", js, re.S)
    assert m, '找不到 toggle-group'
    body = m.group(1)
    # ⚠️ 允许 renderGroups 只作为**兜底**（卡片找不到时），不能是主路径
    calls = re.findall(r'\brenderGroups\(\)', body)
    assert len(calls) <= 1 and re.search(r'\}\s*else\s+renderGroups\(\)', body), \
        'toggle-group 仍在整块重画：新卡片带着 .open 插入，过渡没有起点，点开是硬切'
    assert 'classList.toggle' in body, '没有直接切换卡片的 class'
    assert "classList.toggle('open'" in body, 'toggle 的必须是 .open'


def test_toggle_group_still_updates_expanded():
    """只切 class 不代表状态可以不记：EXPANDED 仍要同步，
    否则下次真重画（15 秒刷新）会把展开状态丢掉。"""
    js = _read(JS)
    m = re.search(r"'toggle-group'\(el\)\s*\{(.*?)\n  \},", js, re.S)
    body = m.group(1)
    assert 'EXPANDED.add' in body and 'EXPANDED.delete' in body
    # 收起要复位栏位（v1.60 的行为，不能因为改了这里就丢）
    assert "delete MINI_TAB['grp:'" in body


def test_grp_body_has_transition():
    """高度过渡本身要在（grid-template-rows 0fr→1fr）。

    ⚠️ 不能只写 `\\.grp-body\\s*\\{`：文件里更早出现的是
    `.grp > .grp-head, .grp > .gbg-pick, .grp > .grp-body { z-index:1 }`
    那条（层叠用的），匹配到它就拿不到过渡声明。"""
    html = _no_css_comment(_read(HTML))
    found = False
    for body in re.findall(r'\.grp-body\s*\{([^}]*)\}', html):
        if 'grid-template-rows' in body:
            assert 'transition' in body, '高度没有过渡'
            found = True
            break
    assert found, '找不到 .grp-body 的高度过渡定义'


def test_grp_inner_enter_animation():
    """内容淡入：即使浏览器不支持 fr 插值（硬切），也看得到动画。"""
    html = _no_css_comment(_read(HTML))
    assert re.search(r'\.grp\.open\s+\.grp-inner\s*\{', html), \
        '展开时内容没有入场动画'
    assert '@keyframes grpIn' in html


def test_grp_in_blocked_by_reduced_motion():
    """减少动效：grpIn 的 delay 必须被清掉，
    否则 backwards 填充会留一小段 opacity:0 的空窗。"""
    html = _read(HTML)
    m = re.search(r'@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\n  \}',
                  html, re.S)
    assert m, 'index.html 没有减少动效块'
    assert 'animation-delay' in m.group(1), \
        '减少动效没有清 animation-delay：带 backwards 的动画会先空一段'


# ---------------- ② 指针柔光：不能永远占着每一帧 ----------------

def test_glow_raf_stops_when_settled():
    """rAF 追上目标必须停 —— 以前无条件 loop()，鼠标不动也每帧重绘整屏。"""
    js = _read(JS)
    m = re.search(r'function initGlow\(\)\s*\{(.*?)\n\}', js, re.S)
    assert m, '找不到 initGlow'
    body = m.group(1)
    assert 'raf = 0; return;' in body, \
        '光斑循环没有收敛即停：鼠标不动也在每帧重绘'
    assert re.search(r'Math\.abs\(tx - cx\)', body), '没有收敛判断'
    assert re.search(r'if\s*\(!raf\)\s*raf = requestAnimationFrame', body), \
        '收敛停止后，鼠标再动必须能重新点起来'


def test_glow_no_initial_loop_call():
    """不能在初始化时直接 loop() —— 那等于一进页面就开始每帧重绘。"""
    js = _read(JS)
    m = re.search(r'function initGlow\(\)\s*\{(.*?)\n\}', js, re.S)
    body = m.group(1)
    assert not re.search(r'^\s*loop\(\);', body, re.M), \
        '初始化时无条件启动 loop()，页面空闲也在烧帧'


def test_glow_uses_transform_not_fullscreen_gradient():
    """柔光必须是固定大小的圆 + 位移，不能是整屏渐变跟着坐标重绘。"""
    html = _no_css_comment(_read(HTML))
    m = re.search(r'\.bg-glow\s*\{([^}]*)\}', html)
    assert m, '找不到 .bg-glow'
    assert 'radial-gradient' not in m.group(1), \
        '.bg-glow 本体仍在画整屏渐变：鼠标每动一帧就重绘一次全屏'
    assert re.search(r'\.bg-glow::before[^}]*\}', html), \
        '柔光没有改成固定大小的发光圆'
    assert re.search(r'\.bg-glow::(before|after)[^}]*translate3d', html), \
        '发光圆没有用 translate3d 位移'


# ---------------- ③ 背景光斑：blur 元素不能带 scale ----------------

def test_bgfx_keyframes_have_no_scale():
    """光斑挂着 filter:blur(72px)，带 scale 会让 blur 每帧重新光栅化。"""
    html = _read(HTML)
    for name in ('drift1', 'drift2', 'drift3'):
        m = re.search(r'@keyframes %s\s*\{(.*?)\n  \}' % name, html, re.S)
        assert m, '找不到 @keyframes %s' % name
        body = m.group(1)
        assert 'scale(' not in body, \
            '@keyframes %s 带 scale：blur 元素每帧重新光栅化，是卡顿主因' % name


def test_bgfx_has_will_change():
    """光斑要提示浏览器提前提升合成层，位移才不会触发重绘。"""
    html = _no_css_comment(_read(HTML))
    m = re.search(r'\.bgfx span\s*\{([^}]*)\}', html)
    assert m and 'will-change' in m.group(1), '.bgfx span 缺 will-change'


# ---------------- ④ 入场动画不能用 filter:blur ----------------

def test_fx_in_has_no_blur():
    """fxIn 是几十个元素同时跑的入场动画，带 blur 会明显掉帧。"""
    html = _read(HTML)
    m = re.search(r'@keyframes fxIn\s*\{(.*?)\n  \}', html, re.S)
    assert m, '找不到 @keyframes fxIn'
    assert 'blur(' not in m.group(1), 'fxIn 仍在用 filter:blur 做渐入'


def test_rise_has_no_blur():
    css = _read(CSS)
    m = re.search(r'@keyframes rise\s*\{(.*?)\n\}', css, re.S)
    assert m, '找不到 @keyframes rise'
    assert 'blur(' not in m.group(1), 'rise 仍在用 filter:blur（7 个宫格同时跑）'


# ---------------- ⑤ 列表行入场动画要限量 ----------------

def test_row_in_limited_to_first_rows():
    """日志一页几十上百行、10 秒重画一次，全部跑动画会掉帧。"""
    css = _no_css_comment(_read(CSS))
    m = re.search(r'\.tl-item:nth-child\(-n\+\d+\)\s*\{\s*animation:\s*rowIn', css)
    assert m, '.tl-item 的入场动画没有限量（每行都跑一遍）'
    n = int(re.search(r'-n\+(\d+)', m.group(0)).group(1))
    assert n <= 20, '限量 %d 行太多了' % n


# ---------------- ⑥ 按钮光泽：平时不绘制 ----------------

def test_shine_hidden_until_hover():
    """按钮的渐变伪元素：平时 visibility:hidden 才不参与绘制。

    ⚠️ 光泽挂在 .btn 上，**不是**裸 button —— 裸 button 会命中
    开关滑块（.sw-btn::after）和页签下划线（.mini-tab.on::after），
    把它们的样式整条覆盖掉。
    """
    css = _no_css_comment(_read(CSS))
    m = re.search(r'\.btn::after\s*\{([^}]*)\}', css)
    assert m, '找不到按钮光泽定义'
    assert 'visibility: hidden' in m.group(1), \
        '光泽伪元素平时可见：上百个渐变一直参与每帧绘制'
    m2 = re.search(r'\.btn:hover::after[^}]*\}', css)
    assert m2 and 'visibility: visible' in m2.group(0), 'hover 时没有显示光泽'



def test_backdrop_blur_radius_bounded():
    """backdrop-filter 是按像素半径平方级涨成本的，
    卡片数量多时 22px 会明显拖慢滚动/重绘。"""
    for path in (HTML, CSS):
        s = _no_css_comment(_read(path))
        for r in re.findall(r'backdrop-filter:\s*blur\((\d+)px\)', s):
            assert int(r) <= 12, \
                '%s 里 backdrop-filter blur(%spx) 过大' % (
                    os.path.basename(path), r)


# ---------------- ⑧ 心跳/主循环没被误伤 ----------------

def test_heartbeat_animations_intact():
    """性能改动不能把心跳波形、轮询点一起关掉。"""
    html = _read(HTML)
    assert 'hbRoll' in html and 'hbBeat' in html and 'hbLed' in html
    assert 'initGlow' in _read(JS) and 'initTilt' in _read(JS)
