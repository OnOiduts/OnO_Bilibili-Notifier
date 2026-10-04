# -*- coding: utf-8 -*-
"""「凭据」板块四项 UI 修复 锁定测试（v1.62.0）

反馈：
  1. 安全体检卡片应该和概况-健康度卡片类似，可编辑的可以点击，
     没安全风险的显示「安全」
  2. B站登录里「手动填写 Cookie」应该是个单独按钮，点了才展开
  3. 登录状态无法读取（永远「读取中…」）
  4. QQ 凭据：已有凭据应该呈现绿色

── 三条根因 ──
③ 最阴：#sessState 在 HTML 里写死「读取中…」，而**全项目没有任何一处
   给它赋过值** —— 它不是"读不出来"，是压根没人写。loadBili() 里
   补上赋值（含失败分支，不留"读取中"）。

① 以前是一堆 .tile 方块：大小不齐、看不出哪些能处理；而且"没问题"
   只能复用蓝色 .tag.on（蓝色在本项目里是"可点/进行中"的语义），
   所以新增绿色 .tag.ok，状态词统一说「安全」。

④ 「当前状态」以前永远是灰字，填没填看不出来。两个都齐了才说"已填写"
   并变绿；只填一半用橙色点名还差哪个（只填 AppID 是跑不起来的）。
"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
APPJS = os.path.join(ROOT, "static", "app.js")
HTML = os.path.join(ROOT, "templates", "index.html")


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _read(APPJS)
        cls.html = _read(HTML)

    # ---------- ① 安全体检：行式布局 + 可点 + 「安全」绿标 ----------
    def test_01_no_more_tiles(self):
        """安全体检不能再用 .tile 方块（要和健康度一样是行）。"""
        m = re.search(r"function loadSecurity\(\)[\s\S]{0,2600}?\n\}", self.js)
        self.assertTrue(m, "找不到 loadSecurity")
        b = m.group(0)
        self.assertNotIn("tile-k", b, "安全体检卡片不该再用 tile 方块")
        self.assertIn("class=\"sw", b, "必须改用 .sw 行（和健康度一致）")

    def test_02_row_has_tag(self):
        """每行右边要有状态标签。"""
        m = re.search(r"const row = \(nm, ds, lv, fix\) => \{[\s\S]{0,1200}?\n  \};", self.js)
        self.assertTrue(m, "找不到 row() 渲染函数")
        self.assertIn("class=\"tag", m.group(0))

    def test_03_safe_means_anquan(self):
        """没风险的项显示「安全」，不是「正常」。"""
        m = re.search(r"function secTagTxt\(lv\)[^;]+;", self.js)
        self.assertTrue(m, "找不到 secTagTxt")
        self.assertIn("安全", m.group(0))

    def test_04_green_tag_class(self):
        """绿色标签的 CSS 必须真的存在，否则等于没写。"""
        self.assertIn(".tag.ok {", self.html)
        m = re.search(r"\.tag\.ok\s*\{([^}]*)\}", self.html)
        # ⚠️ 文字色走的是 --ok-tx 而不是 --ok：--ok(#2BAE6B) 是按深色底调的，
        # 直接当文字用在日间白底上对比度只有 2.83，看不清。-tx 是同色系的
        # 日间可读版，深色主题下与 --ok 同值。
        self.assertTrue(
            re.search(r"var\(--ok(-tx)?\)", m.group(1)),
            "绿标文字要用到 --ok 色系（日间为 --ok-tx）")

    def test_05_editable_items_clickable(self):
        """能在本面板里改的项才可点：访问口令 / 机器人凭据。"""
        m = re.search(r"const SEC_FIX = \{[\s\S]{0,400}?\};", self.js)
        self.assertTrue(m, "找不到 SEC_FIX 映射")
        b = m.group(0)
        self.assertIn("访问口令", b)
        self.assertIn("机器人凭据", b)
        self.assertIn("newPwd", b)
        self.assertIn("cAppid", b)

    def test_06_unfixable_not_clickable(self):
        """改不了的项（文件权限等）不能出现在 SEC_FIX 里 —— 点了没处去。"""
        m = re.search(r"const SEC_FIX = \{[\s\S]{0,400}?\};", self.js)
        b = m.group(0)
        for nm in ("配置文件权限", "会话密钥", "数据文件位置", "面板暴露面"):
            self.assertNotIn(nm, b, "%s 在面板里改不了，不该做成可点" % nm)

    def test_07_focus_field_action(self):
        """点了要真跳过去并聚焦，光切选项卡不够。"""
        self.assertIn("'focus-field':", self.js)
        m = re.search(r"'focus-field': \(el\) => \{[\s\S]{0,1200}?\n  \},", self.js)
        self.assertTrue(m, "找不到 focus-field 动作")
        b = m.group(0)
        self.assertIn("scrollIntoView", b)
        self.assertIn("t.focus(", b)

    # ---------- ② 手动填写默认收起 ----------
    def test_08_manual_cookie_hidden(self):
        m = re.search(r'<div id="cookieManual"[^>]*>', self.html)
        self.assertTrue(m, "找不到 #cookieManual")
        self.assertIn("hidden", m.group(0), "手动填写区必须默认收起")

    def test_09_toggle_button_exists(self):
        m = re.search(r'<button[^>]*id="cookieToggle"[^>]*>', self.html)
        self.assertTrue(m, "找不到「手动填写」按钮")
        self.assertIn('data-act="toggle-cookie"', m.group(0))

    def test_10_toggle_action(self):
        self.assertIn("'toggle-cookie':", self.js)
        m = re.search(r"'toggle-cookie': \(el\) => \{[\s\S]{0,900}?\n  \},", self.js)
        self.assertTrue(m)
        b = m.group(0)
        self.assertIn("hasAttribute('hidden')", b)
        self.assertIn("aria-expanded", b, "可访问性状态要跟着变")

    def test_11_textarea_inside_manual(self):
        """输入框必须在可折叠区里面，否则收起也藏不住。"""
        i = self.html.index('id="cookieManual"')
        j = self.html.index('id="cookieText"')
        self.assertGreater(j, i, "cookieText 必须在 cookieManual 内")

    # ---------- ③ 登录状态不再是「读取中」 ----------
    def test_12_sess_state_is_filled(self):
        """必须有人给 #sessState 赋值。"""
        i = self.js.index("async function loadBili()")
        b = self.js[i:i + 4000]
        m = b if "sessState" in b else None
        self.assertTrue(m)
        self.assertTrue(m, "loadBili 必须更新 #sessState")
        self.assertIn("setSess(", b)

    def test_13_sess_state_covers_fail(self):
        """接口失败也要有明确说法，不能留着「读取中」。"""
        m = re.search(r"if \(!d\.ok\) \{\s*\n\s*box\.innerHTML[\s\S]{0,200}?\n\s*\}",
                      self.js)
        self.assertTrue(m, "找不到 loadBili 的失败分支")
        self.assertIn("setSess(", m.group(0))

    def test_14_not_logged_in_says_so(self):
        m = re.search(r"if \(!d\.has_login\) setSess\('未登录'", self.js)
        self.assertTrue(m, "未登录要明说，不能停在读取中")

    # ---------- ④ QQ 凭据已填写显示绿色 ----------
    def test_15_cred_saved_colored(self):
        # ⚠️ "$('badgeCred')" 在 renderHero 里也出现，取 credSaved 附近那处
        i = self.js.index("$('credSaved')")
        seg = self.js[max(0, i - 600):i + 900]
        self.assertIn("credSaved", seg)
        self.assertIn("var(--ok)", seg, "两项都齐了必须显示绿色")
        self.assertIn("var(--warn)", seg, "只填一半要用橙色提醒")

    def test_16_half_filled_named(self):
        """只填 AppID 要点名还差 AppSecret（只填一个是跑不起来的）。"""
        i = self.js.index("$('credSaved')")
        seg = self.js[max(0, i - 600):i + 900]
        self.assertIn("还差 AppSecret", seg)

    def test_17_no_hardcoded_reading(self):
        """HTML 里「读取中…」只能是初值 —— JS 必须覆盖它。"""
        self.assertIn('id="sessState">读取中…</b>', self.html)
        self.assertIn("ss.textContent = txt", self.js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
