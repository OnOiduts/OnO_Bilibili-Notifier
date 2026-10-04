# -*- coding: utf-8 -*-
"""顶栏状态点 + 心率波形。

⚠️ 这里锁的是用户多轮确认下来的语义，别再改回去：
   ① 联动的只有「心率波形框」+「轮询点」——机器人那个点只是普通在线灯，
      千万别再把搏动动画挂上去（挂上去就是"绿点跟着心率乱跳"）。
   ② **搏动固定 1 秒一次**，与轮询间隔无关：轮询点**缩小**、波形框**放大**，
      同一批百分比、同一个 1s 时长 —— 同一拍。
      ⚠️ 以前做成"搏动周期 = 轮询间隔"，8s 一轮就 8 秒才跳一下，看着像卡住。
   ③ **滚动速度 = 轮询间隔的反比**（间隔越大 → 越慢），基准 96/dur，浮点无级。
   ④ **波长（波形密度）必须和速度解耦**：
      ⚠️ 以前 wl = speed × 1 秒 → 每秒换一个波群，与 1 秒的搏动叠在一起就是
         "动一下删一个波形"（用户原话）。现在波长按间隔线性插值、不饱和，
         滚动时长 = 波长/速度，不再是恒定 1 秒。
   ④ 未运行 → 红色直线；运行 → 绿色波形。
"""
import os
import re
import subprocess
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


def _fn(src, name):
    m = re.search(r'function %s\(.*?\n\}' % name, src, re.S)
    return m.group(0) if m else ''


def _rule(src, selector):
    m = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', src, re.S)
    return m.group(1) if m else ''


def _kf(src, name):
    """取某个 @keyframes 的整个块"""
    m = re.search(r'@keyframes ' + name + r'\s*\{(.*?)\n\s*\}', src, re.S)
    return m.group(0) if m else ''


class _HbMath(object):
    """把 app.js 里的心跳数学部分抽出来，用 node 直接求值。"""
    SRC = None

    @classmethod
    def _build(cls):
        js = _read(APP)
        start = js.index('var HB_Y = 12')
        end = js.index('/* 上一次的波长')
        return js[start:end]

    @classmethod
    def eval(cls, expr):
        src = cls._build() + '\nprocess.stdout.write(String(' + expr + '));'
        out = subprocess.run(['node', '-e', src], capture_output=True, text=True)
        if out.returncode != 0:
            raise AssertionError('node 执行失败: ' + out.stderr[:400])
        return out.stdout.strip()

    @classmethod
    def path_d(cls, sec):
        """取运行时真正画出来的那条 path（不是字符串断言，是求值）"""
        src = cls._build() + \
              '\nprocess.stdout.write(buildHbPath(%s, true).d);' % sec
        out = subprocess.run(['node', '-e', src], capture_output=True, text=True)
        if out.returncode != 0:
            raise AssertionError('node 执行失败: ' + out.stderr[:400])
        return out.stdout.strip()


class TestTopbarDots(unittest.TestCase):

    def setUp(self):
        self.html = _read(TPL)
        self.js = _read(APP)
        self.css = _read(os.path.join(ROOT, 'static', 'style.css'))

    # ---------- 1. 顶栏右侧只留刷新 ----------
    def test_badges_removed(self):
        for i in ('badgeSubs', 'badgeCred', 'badgeBili', 'badgeErr'):
            self.assertNotIn('id="%s"' % i, self.html, i + ' 应已移除')

    def test_refresh_kept(self):
        self.assertIn('data-act="refresh-state"', self.html)

    def test_no_stale_badge_refs_in_js(self):
        for i in ('badgeSubs', 'badgeCred', 'badgeBili', 'badgeErr'):
            self.assertNotIn("$('%s')" % i, self.js, i + ' 还有引用')

    # ---------- 2. 机器人的点不参与心跳联动 ----------
    def test_bot_dot_is_plain(self):
        m = re.search(r'<span class="([^"]*)" id="botLed"', self.html)
        self.assertTrue(m, '找不到机器人的状态点')
        self.assertEqual(m.group(1).strip(), 'd',
                         '机器人的点必须是普通状态灯，不挂搏动类')

    def test_poll_dot_has_beat_class(self):
        m = re.search(r'<span class="([^"]*)" id="hbLed"', self.html)
        self.assertTrue(m, '找不到轮询点')
        self.assertIn('hb-led', m.group(1), '轮询点缺 hb-led 类')

    def test_beat_class_used_once(self):
        self.assertEqual(self.html.count('class="d hb-led"'), 1,
                         'hb-led 只能挂在轮询点上')

    # ---------- 3. 搏动固定 1 秒，与轮询无关 ----------
    def test_beat_is_one_second(self):
        frame = _rule(self.html, '.hb-frame')
        led = _rule(self.html, '.tb-i .d.hb-led')
        self.assertIn('animation:hbBeat 1s', frame,
                      '波形框的搏动必须是固定 1s，不能挂 var(--hb-dur)')
        self.assertIn('animation:hbLed 1s', led,
                      '轮询点的搏动必须是固定 1s，不能挂 var(--hb-dur)')
        self.assertNotIn('--hb-dur', frame)
        self.assertNotIn('--hb-dur', led)

    def test_no_cycle_var_left(self):
        # 周期变量已经不用了：留着就会有人再把它接回去
        body = _strip_comments(self.html)
        self.assertNotIn('var(--hb-dur', body, '不该再出现周期变量')

    def test_static_keyframes_only_source(self):
        # ⚠️ 两份同名关键帧会互相覆盖 —— 之前"点对不上拍"就是这么来的。
        #    现在周期固定，只允许 index.html 里这一份静态定义。
        for name in ('hbBeat', 'hbLed'):
            self.assertEqual(self.html.count('@keyframes ' + name), 1,
                             '%s 必须只有一份定义' % name)
            self.assertNotIn('@keyframes ' + name, self.js,
                             'app.js 不许再生成 %s' % name)
            self.assertNotIn('@keyframes ' + name, _strip_comments(self.css))

    # ---------- 4. 同一拍：同一批百分比、反方向 ----------
    def _pcts(self, name):
        kf = _kf(self.html, name)
        self.assertTrue(kf, '找不到 @keyframes ' + name)
        return re.findall(r'(\d+)%\s*\{', kf)

    def test_same_percentages(self):
        self.assertEqual(self._pcts('hbBeat'), ['0', '9', '30', '100'])
        self.assertEqual(self._pcts('hbLed'), ['0', '9', '30', '100'],
                         '点与框必须用同一批百分比，否则不同步')

    def test_peak_at_same_percent(self):
        beat = _kf(self.html, 'hbBeat')
        led = _kf(self.html, 'hbLed')
        # 9% 处：框放到最大、点缩到最小 —— 同一瞬间
        self.assertIn('9%', beat)
        self.assertIn('9%', led)
        m1 = re.search(r'9%\s*\{[^}]*scale\(([\d.]+)\)', beat)
        m2 = re.search(r'9%\s*\{[^}]*scale\(([\d.]+)\)', led)
        self.assertGreater(float(m1.group(1)), 1, '心率必须放大')
        self.assertLess(float(m2.group(1)), 1, '轮询点必须缩小')

    def test_amplitude_is_visible(self):
        # ⚠️ 用户反馈"波动太不明显"：幅度必须给足。
        #    突兀的根源是硬起硬落 + 叠光晕，不是幅度大小。
        beat = _kf(self.html, 'hbBeat')
        led = _kf(self.html, 'hbLed')
        up = float(re.search(r'scale\(([\d.]+)\)', beat[beat.index('9%'):]).group(1))
        dn = float(re.search(r'scale\(([\d.]+)\)', led[led.index('9%'):]).group(1))
        self.assertGreaterEqual(up, 1.15, '波形框放得太小，看不出波动')
        self.assertLessEqual(dn, 0.70, '轮询点缩得太小，看不出波动')

    def test_no_hard_jump_at_loop_edge(self):
        # 0% 与 100% 都必须是静止态，否则每轮开头"啪"地跳一下
        for name in ('hbBeat', 'hbLed'):
            kf = _strip_comments(_kf(self.html, name))
            self.assertIn('0%', kf)
            self.assertIn('scale(1)', kf[kf.index('0%'):kf.index('9%')],
                          name + ' 起点必须是静止态')
            self.assertIn('scale(1)', kf[kf.index('100%'):],
                          name + ' 终点必须是静止态')

    def test_smooth_easing(self):
        for name in ('hbBeat', 'hbLed'):
            kf = _kf(self.html, name)
            self.assertGreaterEqual(kf.count('animation-timing-function'), 2,
                                    name + ' 两段都要有缓动')

    def test_no_opacity_flicker(self):
        for name in ('hbBeat', 'hbLed'):
            self.assertNotIn('opacity', _kf(self.html, name),
                             name + ' 不许再动明暗（会多一层闪烁）')

    def test_reduced_motion_covers_both(self):
        m = re.search(r'\.hb-wave, \.hb-frame[^\n]*', self.html)
        self.assertTrue(m, '找不到减少动效那条规则')
        line = m.group(0)
        self.assertIn('animation:none', line)
        self.assertIn('.tb-i .d.hb-led', line)

    # ---------- 5. 滚动速度随轮询实时变化（无级） ----------
    def test_cycle_is_continuous(self):
        # ⚠️ 以前"个位数奇数就 ×2"是分档（15→30、19→38），中间值被硬掰到档上，
        #    用户看到的是"跳档"不是变速。现在必须原值返回、且保留浮点。
        fn = _fn(self.js, 'hbCycleSec')
        self.assertTrue(fn, '找不到 hbCycleSec')
        self.assertIn('parseFloat', fn, '必须用浮点，不能 parseInt')
        self.assertNotIn('* 2', fn, '不许再翻倍档位')
        self.assertNotIn('Math.round', fn, '不许取整')

    def test_speed_is_inverse_of_interval(self):
        # ⚠️ 用户明确：间隔数越大滚得越慢。
        #    speed = HB_FLOW_REF / dur（基准 96 → 8 秒档 12px/s）
        #    ⚠️ 倍率从 192 降到 96：24px/s 时用户就反馈"8 秒档看着好快"。
        for dur in (8, 12, 24, 38):
            v = float(_HbMath.eval('hbSpeed(%s)' % dur))
            self.assertAlmostEqual(v, 96.0 / dur, places=4,
                                   msg='dur=%s 的速度必须等于 96/%s' % (dur, dur))
        vals = {d: float(_HbMath.eval('hbSpeed(%s)' % d))
                for d in (8, 12, 24, 38, 120)}
        self.assertGreater(vals[8], vals[12], '间隔越大必须越慢')
        self.assertGreater(vals[12], vals[24])
        self.assertGreater(vals[24], vals[38])

    def test_wave_len_is_decoupled_from_speed(self):
        # ⚠️ 病根：以前 wl = speed × 1 秒，于是"每秒正好滚过一个波群"，
        #    和 1 秒的搏动叠起来就是"动一下删一个波形"。现在两者必须各算各的。
        for dur in (8, 12, 24, 38):
            wl = float(_HbMath.eval('hbWaveLen(%s)' % dur))
            sp = float(_HbMath.eval('hbSpeed(%s)' % dur))
            self.assertNotAlmostEqual(
                wl, sp * 1.0, places=2,
                msg='dur=%s 的波长又和速度绑死了（每秒一个波群）' % dur)

    def test_density_follows_poll_speed(self):
        # 密度 = 1/波长：轮询越快（间隔越小）→ 波群越密（波长越小）
        self.assertLess(float(_HbMath.eval('hbWaveLen(8)')),
                        float(_HbMath.eval('hbWaveLen(38)')))
        self.assertLess(float(_HbMath.eval('hbWaveLen(38)')),
                        float(_HbMath.eval('hbWaveLen(120)')))

    def test_wave_len_is_stepless(self):
        # ⚠️ 以前 clamp 到 6px，dur≥64 全挤在同一档 —— 用户说的"跳档"
        seen = set()
        for i in range(80, 121):
            seen.add(round(float(_HbMath.eval('hbWaveLen(%s)' % i)), 3))
        self.assertGreater(len(seen), 30, '波长在大间隔区饱和了（不是无级）')

    def test_speed_is_stepless(self):
        a = float(_HbMath.eval('hbSpeed(8)'))
        b = float(_HbMath.eval('hbSpeed(8.5)'))
        c = float(_HbMath.eval('hbSpeed(9)'))
        self.assertNotEqual(a, b, '8 与 8.5 不该一样（跳档）')
        self.assertLess(b, a, '8.5 必须比 8 慢（反比）')
        self.assertGreater(b, c, '8.5 必须比 9 快')
        self.assertLess(abs(b - a), abs(c - a), '8.5 必须落在 8 与 9 之间')

    def test_speed_stays_reasonable(self):
        # 速度区间：上限 12（dur=8 最快档），下限 2（极慢轮询时不至于完全不动）
        for dur in (8, 12, 38, 120):
            v = float(_HbMath.eval('hbSpeed(%s)' % dur))
            self.assertGreaterEqual(v, 2, 'dur=%s 低于下限' % dur)
            self.assertLessEqual(v, 12, 'dur=%s 超出上限' % dur)
        self.assertAlmostEqual(float(_HbMath.eval('hbSpeed(8)')), 12, places=4)
        self.assertAlmostEqual(float(_HbMath.eval('hbSpeed(120)')), 2, places=4,
                               msg='120s 应被下限夹住')

    def test_roll_period_not_one_second(self):
        # ⚠️ 滚动周期不许等于 1 秒：1 秒正是搏动周期，两者相等就会
        #    "每搏动一次正好换一个波群" —— 用户看到的"动一下删一个波形"。
        for dur in (12, 24, 38):
            r = float(_HbMath.eval('hbRollSec(%s)' % dur))
            self.assertNotAlmostEqual(r, 1.0, places=2,
                                      msg='dur=%s 的滚动周期又撞上 1 秒' % dur)
        self.assertAlmostEqual(float(_HbMath.eval('hbRollSec(8)')), 1.5, places=4)

    def test_roll_shift_is_wave_len(self):
        # 位移必须是一个波长（px），不能用写死的 -50%
        kf = _kf(self.html, 'hbRoll')
        self.assertIn('var(--hb-shift', kf, 'hbRoll 必须走 --hb-shift')
        self.assertNotIn('-50%', _strip_comments(kf).replace('translateX(var(--hb-shift, -50%))', ''),
                         '不许再写死 -50%')
        fn = _fn(self.js, 'setHb')
        self.assertIn("--hb-shift", fn, 'setHb 必须设置位移')
        self.assertIn('-g.wl', fn, '位移必须等于一个波长')
        self.assertIn("--hb-roll-dur", fn, 'setHb 必须设置滚动时长')

    def test_roll_period_uses_variable(self):
        # 滚动时长必须是变量，不能写死 1s
        wave = _rule(self.html, '.hb-wave')
        self.assertIn('var(--hb-roll-dur', wave, '滚动时长必须是变量')
        self.assertNotIn('hbRoll 1s', wave, '不许再写死 1 秒')

    def test_enough_cycles_to_fill_view(self):
        # 总宽必须 ≥ 可视区 + 一个波长：左移一个波长后右侧要有画好的波形顶上，
        # 否则右边露白 —— 那也是"删掉一个波形"的观感来源。
        fn = _fn(self.js, 'buildHbPath')
        self.assertIn('Math.ceil(HB_VIEW / wl) + 2', fn,
                      '周期数必须按可视区宽度算够')
        for sec in (8, 38, 120):
            d = _HbMath.path_d(sec)
            wl = float(_HbMath.eval('hbWaveLen(%s)' % sec))
            total = max(float(x) for x, _ in
                        re.findall(r'[ML]([-\d.]+),([-\d.]+)', d))
            self.assertGreaterEqual(total, 54 + wl,
                                    'sec=%s 的总宽不够，右侧会露白' % sec)

    def test_path_is_periodic(self):
        # 无缝的前提：路径必须严格满足 p(x) === p(x + wl)。
        # ⚠️ 以前把首个 R 峰挪到可视区中心，周期性被打破。
        fn = _strip_comments(_fn(self.js, 'buildHbPath'))
        self.assertIn('x0 = i * wl + wl / 2', fn, '波群必须放在周期正中')
        # 数值验证：第二周期的顶点整体左移一个波长，必须和第一周期逐点重合
        for sec in (8, 12, 38, 120):
            d = _HbMath.path_d(sec)
            wl = float(_HbMath.eval('hbWaveLen(%s)' % sec))
            pts = [(float(a), float(b)) for a, b in
                   re.findall(r'[ML]([-\d.]+),([-\d.]+)', d)]
            cyc1 = [(x, y) for x, y in pts if 0 < x < wl]
            cyc2 = [(x - wl, y) for x, y in pts if wl <= x < 2 * wl]
            self.assertTrue(cyc1 and cyc2, 'sec=%s 取不到两个周期' % sec)
            for px, py in cyc1:
                self.assertTrue(
                    any(abs(px - qx) < 0.02 and abs(py - qy) < 0.02
                        for qx, qy in cyc2),
                    'sec=%s 的波形不是严格周期（滚动会露白/跳格）' % sec)

    # ---------- 6. 未运行 = 红色直线 ----------
    def test_flat_line_when_dead(self):
        fn = _fn(self.js, 'buildHbPath')
        self.assertIn('alive === false', fn, '缺少未运行分支')
        self.assertIn("'M0,' + HB_Y + ' L'", fn, '未运行时必须画成直线')
        self.assertIn('.hb.bad .hb-line', self.html, '未运行时波形没有变红')
        self.assertIn('.hb.bad .hb-wave', self.css, '未运行时滚动没有停')

    def test_green_when_alive(self):
        # 运行时波形是绿色（--hb-c），不是红色
        self.assertIn('stroke:var(--hb-c)', _rule(self.html, '.hb-line'))

    def test_peaks_are_tall(self):
        # "波动不明显"另一半原因：P/T 小波只有 2~3px。现在都加大了。
        fn = _fn(self.js, 'hbBeatSeg')
        for delta in ('3.0', '5.0', '4.2'):
            self.assertIn(delta, fn, '小波振幅被改小了')
        self.assertIn('1.5', fn, 'R 峰必须顶到接近框顶')


if __name__ == '__main__':
    unittest.main()
