# -*- coding: utf-8 -*-
"""日间（light）主题可读性 + 动效存在性。

背景：用户反馈"日间有些 UI 看不清"。查下来不是某几个元素写错，
而是**整套配色是按深色底调的**，搬到白底上对比度全线偏低：

    --tx3   #8B94AC  3.01   ← 三级文字
    --warn  #E8A33D  2.14   ← 警告文字，最糟
    --blue  #00AEEC  2.52   ← 链接色
    --pink  #FB7299  2.61
    --ok    #2BAE6B  2.83
    --danger #E5544B 3.65
    --line  rgba(20,30,60,.11)  1.25  ← 卡片边框，几乎不存在

这里守住两件事：
  1. light 下所有"当文字用"的色，对比度 ≥ 4.5（正文可读线）
  2. 卡片/边框/行悬浮这些"层次"元素，对比度 ≥ 1.4（看得见边界）
  3. 深色主题一个像素都不许变（改日间不能顺手改坏夜间）
  4. 品牌色本身不变 —— 渐变底、光晕、淡背景都还用它们

另外顺带盯住动效：ripple / 错峰入场 / 减少动效兜底 都必须在。
"""
import os
import re
import sys

import pytest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
CSS = os.path.join(ROOT, 'static', 'style.css')
HTML = os.path.join(ROOT, 'templates', 'index.html')
JS = os.path.join(ROOT, 'static', 'app.js')

# ---------- WCAG 相对亮度 / 对比度 ----------


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(hexstr):
    h = hexstr.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg, bg):
    l1, l2 = _lum(fg), _lum(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def blend(fg_hex, alpha, bg_hex):
    """把 rgba(fg, alpha) 混到 bg 上，返回等效 hex。"""
    f = [int(fg_hex.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(bg_hex.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)]
    return '#%02X%02X%02X' % tuple(
        round(f[i] * alpha + b[i] * (1 - alpha)) for i in range(3))


# ---------- 解析 index.html 内联样式里的 light 变量 ----------


def _read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def _theme_block(text, theme):
    """取出 [data-theme="light"] { ... } 的变量块（:root 之后的第一个）。"""
    m = re.search(r'\[data-theme="%s"\]\s*\{(.*?)\}' % theme, text, re.S)
    assert m, '没找到 [data-theme="%s"] 块' % theme
    return m.group(1)


def _var(block, name, default=None):
    """取变量值。⚠️ 同一个块里可能对同一个变量赋值两次
    （文件里就是这么写的：先给一个值，下面带注释再修正一次）。
    CSS 规则是**后者胜出**，所以必须取最后一次，取首个会拿到被作废的旧值。"""
    hits = re.findall(r'--%s\s*:\s*([^;]+);' % re.escape(name), block)
    return hits[-1].strip() if hits else default


def _var_global(text, name, default=None):
    """有些变量（如 --li-hv）只写在根块里、light/dark 各自按需覆盖。
    light 若不覆盖，生效的就是根块那个值，因此从全文取首个。"""
    m = re.search(r'--%s\s*:\s*([^;]+);' % re.escape(name), text)
    return m.group(1).strip() if m else default


LIGHT = _theme_block(_read(HTML), 'light')
DARK = _theme_block(_read(HTML), 'dark')

# 卡片实际呈现色：rgba(255,255,255,.90) 压在 #EEF2F8 上
_BG_L = _var(LIGHT, 'bg', '#EEF2F8')
_CARD_ALPHA = 0.90
CARD_L = blend('#FFFFFF', _CARD_ALPHA, _BG_L)

TEXT_MIN = 4.5     # 正文可读线
EDGE_MIN = 1.4     # 边框/层次：低于这个等于看不见


# ============================================================
# 1. light：文字色对比度
# ============================================================

@pytest.mark.parametrize('name', ['tx', 'tx2', 'tx3'])
def test_light_text_levels(name):
    c = _var(LIGHT, name)
    assert c, '--%s 未定义' % name
    assert contrast(c, CARD_L) >= TEXT_MIN, \
        '日间 --%s %s 对卡片 %.2f < %.1f' % (name, c, contrast(c, CARD_L), TEXT_MIN)


@pytest.mark.parametrize('name', ['ok-tx', 'warn-tx', 'danger-tx', 'blue-tx', 'pink-tx'])
def test_light_semantic_text(name):
    """状态色当文字用：必须走 -tx 版，且对比度达标。"""
    c = _var(LIGHT, name)
    assert c, '日间缺少 --%s（状态色直接当文字会在白底上看不清）' % name
    got = contrast(c, CARD_L)
    assert got >= TEXT_MIN, \
        '日间 --%s %s 对比度 %.2f < %.1f' % (name, c, got, TEXT_MIN)


def test_light_semantic_text_differs_from_brand():
    """文字态必须**比品牌色更深**，否则等于没换。"""
    for base, tx in (('ok', 'ok-tx'), ('warn', 'warn-tx'),
                     ('danger', 'danger-tx'), ('blue', 'blue-tx'),
                     ('pink', 'pink-tx')):
        b = _var(LIGHT, base) or _var(_theme_block(_read(CSS), 'light'), base)
        t = _var(LIGHT, tx)
        if not b:
            continue
        assert _lum(t) < _lum(b), \
            '--%s (%s) 应比品牌色 --%s (%s) 更深' % (tx, t, base, b)


# ============================================================
# 2. light：层次（卡片 / 边框 / 行悬浮）
# ============================================================

def test_light_card_stands_out():
    """白卡片必须比背景亮出一截，否则"浮不起来"。"""
    got = contrast(CARD_L, _BG_L)
    assert got >= 1.05, \
        '日间卡片 %.2f 与背景分不开（卡片 %s / 背景 %s）' % (got, CARD_L, _BG_L)


def test_light_border_visible():
    for name, floor in (('line', EDGE_MIN), ('line-2', 1.20)):
        raw = _var(LIGHT, name)
        assert raw, '--%s 未定义' % name
        m = re.match(r'rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)', raw)
        assert m, '--%s 不是 rgba()，无法计算：%s' % (name, raw)
        r, g, b, a = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
        eff = blend('#%02X%02X%02X' % (r, g, b), a, CARD_L)
        got = contrast(eff, CARD_L)
        assert got >= floor, \
            '日间 --%s %s 对比度 %.2f < %.2f（边界看不见）' % (name, raw, got, floor)


def test_light_hardcoded_card_border_not_too_weak():
    """.card/.kpi 的 background 与 border-color 在 index.html 里是**硬编码**的，
    不走 --card / --line 变量 —— 只改变量对它们无效，必须一起改。"""
    html = _read(HTML)
    m = re.search(r'\[data-theme="light"\] \.card,\s*'
                  r'\[data-theme="light"\] \.kpi \{(.*?)\}', html, re.S)
    assert m, '未找到 light 下 .card/.kpi 的硬编码块'
    blk = m.group(1)
    bm = re.search(r'background\s*:\s*rgba\(255,\s*255,\s*255,\s*([\d.]+)\)', blk)
    assert bm, '.card 背景不是 rgba(255,255,255,x)'
    a = float(bm.group(1))
    assert a >= 0.85, \
        '.card 背景不透明度 %.2f 太低，白卡片压不住背景光斑' % a

    cm = re.search(r'\[data-theme="light"\] \.card \{(.*?)\}', html, re.S)
    assert cm
    b2 = re.search(r'border-color\s*:\s*rgba\(20,\s*30,\s*60,\s*([\d.]+)\)', cm.group(1))
    assert b2, '.card border-color 不是 rgba(20,30,60,x)'
    a2 = float(b2.group(1))
    assert a2 >= 0.18, '.card 边框 %.2f 太淡，卡片糊在背景里' % a2


def test_light_row_hover_visible():
    # --li-hv 只写在根块里（light 不覆盖它），所以从全文取
    hv = _var_global(_read(HTML), 'li-hv')
    assert hv, '--li-hv 未定义'
    m = re.match(r'rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)', hv)
    assert m, '--li-hv 不是 rgba(): %s' % hv
    r, g, b, a = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
    eff = blend('#%02X%02X%02X' % (r, g, b), a, CARD_L)
    assert contrast(eff, CARD_L) >= 1.10, \
        '日间行悬浮高亮 %.2f 太淡，鼠标划过没有反馈' % contrast(eff, CARD_L)


# ============================================================
# 3. 深色主题不许被改动
# ============================================================

@pytest.mark.parametrize('name', ['bg', 'card', 'line', 'tx', 'tx2', 'tx3'])
def test_dark_untouched(name):
    """改日间不能顺手改坏夜间：关键变量保持原值。"""
    expect = {
        'bg': '#0B0D14',
        'card': 'rgba(9,12,19,.62)',
        'line': 'rgba(255,255,255,.11)',
        'tx': '#F4F7FD',
        'tx2': '#BCC6DE',
        'tx3': '#8892AC',
    }[name]
    got = _var(DARK, name)
    assert got == expect, \
        '夜间 --%s 被改动了：%s != %s' % (name, got, expect)


def test_dark_semantic_text_equals_brand():
    """夜间文字态 = 品牌色（外观不变）。"""
    for base, tx in (('ok', 'ok-tx'), ('warn', 'warn-tx'),
                     ('danger', 'danger-tx'), ('blue', 'blue-tx'),
                     ('pink', 'pink-tx')):
        b = _var(DARK, base)
        t = _var(DARK, tx)
        if b and t:
            assert _lum(b) == pytest.approx(_lum(t)), \
                '夜间 --%s 与 --%s 不一致' % (tx, base)


def test_brand_colors_unchanged():
    """品牌色本身不许动：渐变底、光晕、淡背景都在用。"""
    css = _read(CSS)
    m = re.search(r':root \{(.*?)\}', css, re.S)
    assert m
    root = m.group(1)
    for name, expect in (('pink', '#FB7299'), ('blue', '#00AEEC'),
                         ('ok', '#2BAE6B'), ('warn', '#E8A33D'),
                         ('danger', '#E5544B')):
        got = _var(root, name)
        assert got == expect, '品牌色 --%s 被改动：%s != %s' % (name, got, expect)


# ============================================================
# 4. 文字态必须真的被用上
# ============================================================

def test_no_brand_color_as_text():
    """凡是 color: 的地方不许再用品牌色（必须用 -tx 版）。
    用 (?<![-\\w]) 排除 border-color / background-color 等。"""
    pat = re.compile(r'(?<![-\w])color:\s*var\(--(ok|warn|danger|blue|pink)\)')
    for p in (CSS, HTML):
        s = _read(p)
        hit = pat.search(s)
        assert not hit, \
            '%s 仍有把品牌色直接当文字用的地方：%s' % (os.path.basename(p), hit.group(0))


def test_no_border_color_rewritten():
    """替换时不能误伤 border-color / background-color（它们仍该用品牌色）。"""
    pat = re.compile(r'(?:border|background)-color:\s*var\(--(?:ok|warn|danger|blue|pink)-tx\)')
    for p in (CSS, HTML):
        s = _read(p)
        assert not pat.search(s), \
            '%s 里 border/background-color 被误换成 -tx 版' % os.path.basename(p)


def test_tx_vars_defined_in_css_root():
    """:root 必须有默认值，否则没匹配到 light/dark 时会取不到。"""
    css = _read(CSS)
    root = re.search(r':root \{(.*?)\}', css, re.S).group(1)
    for name in ('ok-tx', 'warn-tx', 'danger-tx', 'blue-tx', 'pink-tx'):
        assert _var(root, name), '--%s 在 :root 里没有默认值' % name


# ============================================================
# 5. 动效：必须真的挂上，且尊重"减少动效"
# ============================================================

def test_ripple_style_exists():
    """@keyframes rippleOut 曾经是死样式（没人用），现在必须有 .ripple 规则。"""
    css = _read(CSS)
    assert '@keyframes rippleOut' in css
    assert re.search(r'\.ripple\s*\{', css), '缺少 .ripple 样式'


def test_ripple_implemented_in_js():
    js = _read(JS)
    assert 'initRipple' in js, 'JS 里没有 initRipple'
    assert "className = 'ripple'" in js or "className='ripple'" in js, \
        'initRipple 没有实际生成 ripple 元素'


def test_stagger_animation_exists():
    css = _read(CSS)
    assert '.fx-in' in css, '缺少列表错峰入场 .fx-in'
    assert '@keyframes fxIn' in css


def test_stagger_only_when_set_changes():
    """错峰动画不能每次重画都跑 —— 展开/收起群也会重画，
    那样每点一下整列都浮一遍，反而像闪屏。"""
    js = _read(JS)
    assert '_idsKey' in js, '没有用 gid 集合做键来区分"集合变了"与"只是展开"'
    m = re.search(r'const _fxOn = ([^;]+);', js)
    assert m and '_idsKey' in m.group(1), \
        '_fxOn 必须依赖 _idsKey 是否变化'


def test_shine_exists():
    """光泽不能只是个没人用的类 —— 必须真的挂在元素上。

    ⚠️ .th-item（旧首页宫格）已随旧首页移除，模板和 JS 里都没有了，
    所以光泽改挂在 .btn 上 —— 那是页面上真正在用的按钮类。
    ⚠️ 更重要的：光泽**绝不能**挂裸 button。
    开关滑块是 .sw-btn::after、页签下划线是 .mini-tab.on::after，
    都是 button 的 ::after；裸 button 一命中，inset:0 会把滑块
    撑成方块、hover 的 translateX 再把它顶到按钮外（滑块飞出去），
    下划线则被整块渐变盖掉。这条断言就是那次事故的防线。"""
    css = _read(CSS)
    assert re.search(r'\.btn::after\s*\{', css), '缺少按钮光泽 .btn::after'
    assert re.search(r'\.btn:hover::after', css), \
        '.btn 的 hover 规则没了（会变成死样式）'
    # 裸 button 不许再挂光泽 —— 见上：会把开关滑块顶出按钮外
    bare = [m for m in re.findall(r'(?<![\w.])button::after', css)]
    assert not bare, \
        '光泽挂到了裸 button::after 上：会把开关滑块顶出按钮外'



def test_reduced_motion_blocks_new_fx():
    """减少动效：新加的动画必须全部静止。
    ⚠️ 文件里原有三个 @media (prefers-reduced-motion) 块，其中两个是空的，
    所以"减少动效"一直没生效。这里要求至少有一个块把新动画关掉。"""
    css = _read(CSS)
    blocks = re.findall(r'@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}',
                        css, re.S)
    assert blocks, '没有 prefers-reduced-motion 块'
    joined = '\n'.join(blocks)
    for cls in ('.fx-in', '.fx-reveal', '.ripple'):
        assert cls in joined, '减少动效时没有关掉 %s' % cls
    assert 'animation: none' in joined


def test_toast_kind_applied():
    """toast(text, kind) 的 kind 以前**收了没用**，错误提示不会变红。"""
    js = _read(JS)
    m = re.search(r'function toast\(text, kind(?:, sticky)?\) \{(.*?)\n\}', js, re.S)
    assert m, '未找到 toast 函数'
    body = m.group(1)
    assert 'kind' in body and 'className' in body, \
        'toast 的 kind 参数没有被用到 class 上'


# ============================================================
# 6. 结构完整性
# ============================================================

def test_files_exist():
    for p in (CSS, HTML, JS):
        assert os.path.isfile(p), '缺少文件 %s' % p


def test_html_div_balanced():
    """改动不能破坏标签配平。"""
    s = _read(HTML)
    opens = len(re.findall(r'<div\b', s))
    closes = len(re.findall(r'</div>', s))
    assert opens == closes, 'div 不配平：%d 开 / %d 闭' % (opens, closes)
