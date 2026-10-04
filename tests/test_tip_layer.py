# -*- coding: utf-8 -*-
"""详情浮层抬到顶层 + 群背景走图片代理 的锁定测试。

反馈：
  ①「图一的 UI 被遮挡，不是顶层」
  ②「选了 QZQ_Studio 当背景，但界面上什么图都没有」

── ① 浮层为什么会被遮 ──
以前 .curdata / .diag-row .dv 的详情浮层是 CSS ::after 伪元素。
伪元素属于元素自己的层叠上下文，而：
  · .grp 群卡片带 overflow:hidden（折叠动画 grid 0fr→1fr 必须有），
    浮层向上弹出（bottom:calc(100% + 7px)）的那部分会被卡片**直接裁掉**；
  · 它也压不住后面的卡片 → "不是顶层"。
改成挂在 body 下的 #tipLayer（position:fixed + z-index:9999），
由 JS 算位置，不受任何祖先的 overflow / 层叠上下文影响。

── ② 群背景为什么没图 ──
initAvatars() 早就走了 /api/img 代理（B站图床有防盗链，
浏览器直连拿不到图），但 setGroupBg() 一直用 B站 原地址 ——
同一份 face，头像能显示、当背景就空白。
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

    # ---------- ① 顶层浮层 ----------
    def test_01_tip_layer_dom(self):
        self.assertIn('id="tipLayer"', self.html, "body 末尾必须有 #tipLayer")

    def test_02_tip_layer_fixed_top(self):
        m = re.search(r"#tipLayer\s*\{(.*?)\}", self.html, re.S)
        self.assertTrue(m, "缺少 #tipLayer 样式")
        b = m.group(1)
        self.assertIn("position:fixed", b, "必须固定定位，否则跟着页面滚走")
        self.assertIn("z-index:9999", b, "必须是最顶层")

    def test_03_tip_layer_no_pointer(self):
        m = re.search(r"#tipLayer\s*\{(.*?)\}", self.html, re.S)
        self.assertIn("pointer-events:none", m.group(1),
                      "浮层不能吃鼠标事件，否则移到它上面就闪没")

    def test_04_old_pseudo_disabled(self):
        """开了 JS 就关掉老的 ::after，不然两处都弹。"""
        self.assertIn("html.js-tips", self.html)
        self.assertIn("classList.add('js-tips')", self.js)

    def test_05_tip_show_and_hide(self):
        self.assertIn("function tipShow(", self.js)
        self.assertIn("function tipHide(", self.js)
        # 位置：优先上方，上方放不下翻下方
        self.assertIn("r.top - h - 7", self.js)
        self.assertIn("r.bottom + 7", self.js)

    def test_06_delegation(self):
        """群列表每 15 秒重画，逐节点绑事件会漏掉新节点 → 必须委托。"""
        self.assertIn("e.target.closest('[data-tip]')", self.js)

    def test_07_init_registered(self):
        self.assertIn("safe('tips', initTips)", self.js)

    def test_08_idempotent(self):
        self.assertIn("initTips._done", self.js, "重复初始化会绑两遍事件")

    # ---------- ② 群背景走代理 ----------
    def test_09_group_bg_proxied(self):
        m = re.search(r"function setGroupBg\(.*?\n\}", self.js, re.S)
        self.assertTrue(m, "找不到 setGroupBg")
        self.assertIn("proxyImg(face)", m.group(0),
                      "群背景必须走 /api/img 代理，否则防盗链拿不到图")

    def test_10_clear_bg(self):
        """选「无」时不能留下 url("") 这种残余。"""
        self.assertIn("face ? 'url(\"' + proxyImg(face) + '\")' : ''", self.js)

    # ---------- 顺带：直播状态说人话 ----------
    def test_11_live_status_cn_in_tip(self):
        """历史基线里存过 "live_status=2"，浮层里必须翻成「轮播中」。"""
        m = re.search(r"function curDataTip\(.*?\n\}", self.js, re.S)
        self.assertTrue(m, "找不到 curDataTip")
        self.assertIn("liveStatusCn", m.group(0))

    def test_12_live_status_cn_parse(self):
        m = re.search(r"function liveStatusCn\(.*?\n\}", self.js, re.S)
        self.assertTrue(m, "找不到 liveStatusCn")
        self.assertTrue(re.search(r"match\(/live\[.*?status.*?\\d", m.group(0), re.S),
                        "必须从 'live_status=2' 里抠出数字")


if __name__ == "__main__":
    unittest.main(verbosity=2)
