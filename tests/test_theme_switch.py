# -*- coding: utf-8 -*-
"""日间/夜间切换不闪屏。

⚠️ 这里锁的是排查出来的三个真因，别再退回去：
   ① ::view-transition 规则只能有**一份**。
      以前有两份：前一份要求 html 带 .wipe，而 app.js 从来没加过这个 class，
      是死规则；真正生效的是后一份。两份同名并存，改的时候极易改错地方。
   ② 切主题期间必须锁掉元素自身的过渡（html.theme-lock）。
      ::view-transition-new(root) 是**实时快照**，新主题一应用，页面里几十个
      元素（transition:.18s/.2s/.26s 的 background、color、border-color）
      就各自跑补间 —— 圆形刚铺开那一片颜色还是半成品，铺一半才追上，
      看着就是"闪一下、颜色错位"。
   ③ 动画结束那一帧 clip-path 会弹回 circle(0)（整屏被裁掉，露出下面），
      所以 fill-mode 必须显式写 forwards，不能只靠 UA 默认。
   ④ 不支持 View Transitions 的浏览器（旧内核 / 部分 Electron）不能硬切：
      深↔浅一帧翻转就是白闪，必须退回 .theming 的整页颜色交叉淡入。
      ⚠️ 加 .theming 和换主题之间**必须强制回流**，否则两次变更在同一次
         样式计算里完成，浏览器当成初始状态，根本不补间。
"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
TPL = os.path.join(ROOT, 'templates', 'index.html')
APP = os.path.join(ROOT, 'static', 'app.js')


def _read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def _strip_comments(s):
    """去掉 /* ... */ 注释再断言。

    ⚠️ 这个坑踩过不止一次（v1.57 / v1.58）：注释里提到旧名字，
       会被朴素的子串断言当成"还在用"，于是注释怎么写都是红。
    """
    return re.sub(r'/\*.*?\*/', '', s, flags=re.S)


class TestThemeSwitch(unittest.TestCase):

    def setUp(self):
        self.css = _strip_comments(_read(TPL))
        self.js = _strip_comments(_read(APP))

    # ---------- ① 规则不许两份并存 ----------
    def test_only_one_view_transition_block(self):
        # ⚠️ 只数"独立"的那条：合并选择器 ::view-transition-old(root), ::view-transition-new(root){}
        #    也会被子串匹配到，必须从行首（或前一个 } 之后）起算。
        n = len(re.findall(r'(?:^|\})\s*::view-transition-new\(root\)\s*\{', self.css))
        self.assertEqual(n, 1, '::view-transition-new(root) 只能有一份，发现 %d 份' % n)

    def test_dead_wipe_rule_removed(self):
        self.assertNotIn('html.wipe', self.css)
        self.assertNotIn('themeWipe', self.css)
        # 那份"整页淡一下"也从没被用过，一并清掉（改走 .theming 交叉淡入）
        self.assertNotIn('fallback-fade', self.css)
        self.assertNotIn('themeFade', self.css)

    # ---------- ② 切主题期间锁掉元素自身过渡 ----------
    def test_theme_lock_kills_transition(self):
        m = re.search(r'html\.theme-lock\s*\*[^{]*\{([^}]*)\}', self.css)
        self.assertTrue(m, '缺少 html.theme-lock * 规则')
        body = m.group(1)
        self.assertIn('transition:none', body.replace(' ', ''))
        self.assertIn('!important', body)

    def test_theme_lock_keeps_icon_rotation(self):
        """锁的是颜色补间，图标旋转要放行，否则图标硬切。"""
        m = re.search(r'html\.theme-lock\s+\.theme-btn\s+\.tg\s*\{([^}]*)\}', self.css)
        self.assertTrue(m, '锁过渡时把图标旋转也锁死了')
        self.assertIn('transform', m.group(1))

    def test_theme_lock_not_animation(self):
        """只锁 transition，不能锁 animation（光斑飘移、心跳照常跑）。"""
        m = re.search(r'html\.theme-lock\s*\*[^{]*\{([^}]*)\}', self.css)
        self.assertNotIn('animation', m.group(1).replace('animation-name', ''))

    # ---------- ③ fill-mode 必须显式 forwards ----------
    def test_reveal_uses_forwards(self):
        m = re.search(r'(?:^|\})\s*::view-transition-new\(root\)\s*\{([^}]*)\}', self.css)
        self.assertTrue(m)
        self.assertIn('forwards', m.group(1),
                      'clip-path 动画结束会弹回 circle(0) → 整屏被裁 → 闪一下')

    def test_reveal_starts_from_zero(self):
        m = re.search(r'(?:^|\})\s*::view-transition-new\(root\)\s*\{([^}]*)\}', self.css)
        self.assertIn('clip-path:circle(0', m.group(1).replace(' ', ''))

    # ---------- ④ 不支持 VT 时不能硬切 ----------
    def test_theming_rule_exists(self):
        m = re.search(r'html\.theming[^{]*\{([^}]*)\}', self.css)
        self.assertTrue(m, '缺少整页颜色过渡规则')
        self.assertIn('background-color', m.group(1))
        self.assertIn('!important', m.group(1))

    def test_js_actually_adds_theming(self):
        """以前 .theming 是死规则（没人加过 class），必须真加。"""
        self.assertIn("classList.add('theming')", self.js)

    def test_forced_reflow_between(self):
        """加过渡和换主题之间必须强制回流，否则不补间。"""
        i_add = self.js.index("classList.add('theming')")
        i_sw = self.js.index('doSwitch();', i_add)
        seg = self.js[i_add:i_sw]
        self.assertTrue('offsetWidth' in seg or 'getComputedStyle' in seg,
                        '加 .theming 后没有强制回流，过渡不会生效')

    def test_theming_removed_after(self):
        self.assertIn("classList.remove('theming')", self.js)

    # ---------- VT 路径 ----------
    def test_js_adds_theme_lock(self):
        self.assertIn("classList.add('theme-lock')", self.js)
        self.assertIn("classList.remove('theme-lock')", self.js)

    def test_lock_removed_on_finished(self):
        """finished 是 Promise，不能直接 .then 完事，reject 也要解锁。"""
        i = self.js.index('theme-lock')
        seg = self.js[i:i + 900]
        self.assertIn('finished', seg)
        self.assertIn('unlock', seg)

    def test_lock_has_timeout_fallback(self):
        """VT 卡住时不能把过渡永久锁死。"""
        i = self.js.index("classList.add('theme-lock')")
        seg = self.js[i:i + 900]
        self.assertIn('setTimeout(unlock', seg)

    def test_reduce_motion_is_hard_switch(self):
        """系统开了减少动效就硬切，不加任何补间。"""
        i = self.js.index('prefers-reduced-motion')
        seg = self.js[i:i + 800]
        self.assertIn('doSwitch();', seg)

    # ---------- ⑤ 闪在「收尾」那一下 ----------
    def test_snapshots_have_explicit_zindex(self):
        """旧快照全程不透明，叠放顺序不能只靠 UA 默认。

        新快照必须显式压在旧快照上面，否则收尾看到的是旧主题那一层。
        """
        m = re.search(r'::view-transition-old\(root\)\s*\{([^}]*)\}', self.css)
        self.assertTrue(m, '缺少 ::view-transition-old(root) 独立规则')
        self.assertIn('z-index:1', m.group(1).replace(' ', ''))
        m2 = re.search(r'(?:^|\})\s*::view-transition-new\(root\)\s*\{([^}]*)\}',
                       self.css)
        self.assertIn('z-index:2', m2.group(1).replace(' ', ''))

    def test_reveal_keyframe_has_both_from_and_to(self):
        """只写 to 时起点是隐式值，终态差 1px 就在最后一帧露出旧快照。"""
        m = re.search(r'@keyframes\s+themeReveal\s*\{(.*?)\n\s*\}',
                      self.css, flags=re.S)
        self.assertTrue(m, '缺少 @keyframes themeReveal')
        body = m.group(1)
        self.assertIn('from', body)
        self.assertIn('to', body)
        self.assertIn('circle(0', body.replace(' ', ''))
        self.assertIn('--theme-r', body)

    def test_reveal_radius_has_margin(self):
        """半径必须留余量，不能正好等于视口对角。"""
        i = self.js.index("--theme-r")
        seg = self.js[i:i + 200]
        self.assertTrue('1.06' in seg or '1.05' in seg or '* 1.0' in seg,
                        '半径没留余量，最后一帧角落会露出旧快照')
        self.assertIn('Math.ceil', seg)

    def test_unlock_is_deferred_two_frames(self):
        """解锁不能和伪元素销毁挤在同一帧。

        真 DOM 先在「仍然锁着」的状态下画满两帧，再恢复元素自身的
        transition，否则几十个元素在同一帧起一批颜色补间 → 收尾时
        整页又染一遍色。
        """
        i = self.js.index("classList.add('theme-lock')")
        seg = self.js[i:i + 900]
        self.assertIn('requestAnimationFrame', seg,
                      '解锁没有延迟，会和首帧绘制撞在一起')
        # 两帧：requestAnimationFrame 里再套一层 requestAnimationFrame
        self.assertGreaterEqual(seg.count('requestAnimationFrame'), 2)

    def test_reduce_motion_clears_clip(self):
        """关掉动画时必须同时撤掉裁切，否则新快照被裁成 0，整屏只剩旧快照。"""
        # ⚠️ 页面里有不止一处 prefers-reduced-motion，必须定位到
        #    管 ::view-transition-new(root) 的那一处。
        i = self.css.rindex('::view-transition-new(root)')
        j = self.css.rindex('prefers-reduced-motion', 0, i)
        seg = self.css[j:i + 260]
        self.assertIn('clip-path:none', seg.replace(' ', ''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
