# -*- coding: utf-8 -*-
"""群卡片「👤 UP 主 / 📦 合集」栏位在重画后要停回原处。

反馈：
  「点完合集的专属文案修改完后点击保存，回到的页面应该是合集页面
    而不是回到 UP 页面」

── 根因 ──
栏位当前停在哪一栏，只存在于 DOM 的 class 上（.mini-tab.on / .mini-view.on）。
而保存成功后会 loadState() → renderGroups() 整块重画，新 HTML 里
「👤 UP 主」那栏写死带 on —— 于是在合集页改完文案一保存，就被打回 UP 页，
还得再点一次「📦 合集」才看得到刚改的那条。

class 在 DOM 里，重画就没了，所以必须单独记一份（MINI_TAB），
按群 gid 存，重画时按它还原。
"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
APPJS = os.path.join(ROOT, "static", "app.js")


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _read(APPJS)

    def test_01_mini_tab_store(self):
        self.assertIn("const MINI_TAB", self.js, "必须有跨重画的栏位记忆")

    def test_02_default_is_up(self):
        """没点过就停在 UP 主栏。"""
        m = re.search(r"const mvOn = MINI_TAB\['grp:' \+ g\.gid\] \|\| mvUp;",
                      self.js)
        self.assertTrue(m, "群卡片的栏位默认值必须取自 MINI_TAB，且回落到 mvUp")

    def test_03_group_card_tab_not_hardcoded(self):
        """两个栏位的 on 都不能写死 —— 写死就等于每次重画都回 UP 栏。"""
        m = re.search(r"const mvUp = 'mv-up-' \+ gid, mvSea = 'mv-season-' \+ gid;",
                      self.js)
        self.assertTrue(m, "找不到 mvUp / mvSea 定义")
        start = self.js.index("'<div class=\"mini-tabs\">'", m.end())
        seg = self.js[start:start + 700]
        self.assertIn("(mvOn === mvUp ? ' on' : '')", seg)
        self.assertIn("(mvOn === mvSea ? ' on' : '')", seg)
        self.assertNotIn('class="mini-tab on"', seg, "栏位 on 不能写死")

    def test_04_click_records(self):
        """点栏位时要记下来，否则重画照样丢。"""
        m = re.search(r"MINI_TAB\['grp:' \+ \(card\.getAttribute\('data-gid'\)[^;]+;",
                      self.js)
        self.assertTrue(m, "点击 mini-tab 时必须按 gid 记录当前栏位")

    def test_05_lookup_records(self):
        """查询结果那块（lk）也要记，订阅成功后会重画。"""
        self.assertIn("MINI_TAB['lk']", self.js)
        m = re.search(r"MINI_TAB\[card\.id === 'lookupInfo' \? 'lk' : card\.id\]",
                      self.js)
        self.assertTrue(m, "非群卡片的栏位也要记录")

    def test_06_season_save_keeps_season_tab(self):
        """合集文案保存后必须停在合集栏、且群保持展开。"""
        m = re.search(r"function saveSeasonTpl\(.*?\n\}", self.js, re.S)
        self.assertTrue(m, "找不到 saveSeasonTpl")
        b = m.group(0)
        self.assertIn("MINI_TAB['grp:' + gid] = 'mv-season-'", b)
        self.assertIn("EXPANDED.add(gid)", b, "保存后群必须还是展开的")

    def test_07_up_save_keeps_up_tab(self):
        m = re.search(r"function saveUpTpl\(.*?\n\}", self.js, re.S)
        self.assertTrue(m, "找不到 saveUpTpl")
        self.assertIn("MINI_TAB['grp:' + gid] = 'mv-up-'", m.group(0))

    def test_08_lookup_render_not_hardcoded(self):
        m = re.search(r"const lkOn = MINI_TAB\['lk'\] \|\| 'lk-up';", self.js)
        self.assertTrue(m, "查询结果重画时要按记忆还原栏位")
        start = m.end()
        seg = self.js[start:start + 800]
        self.assertIn("(lkOn === 'lk-up' ? ' on' : '')", seg)
        self.assertIn("(lkOn === 'lk-season' ? ' on' : '')", seg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
