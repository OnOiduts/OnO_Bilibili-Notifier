# -*- coding: utf-8 -*-
"""专属文案弹层里「可用变量 / 常用短语」的排版样式 锁定测试。

反馈：「点击专属文案里面的有些样式不见了，样子变丑了」

── 为什么样式是"丢的" ──
varButtons() 生成的按钮是 list.map(...).join('') 直接拼的，
**按钮之间连一个空格都没有**（不像手写 HTML 那样有换行/缩进）。
而容器用的是 .row.wrap，可 .row 在模板里只定义了 margin-bottom，
压根没有 display:flex / gap：

  · 没有 flex → .row.wrap 的 flex-wrap 完全不生效；
  · 没有 gap、HTML 里也没有空白 → 所有按钮粘成一坨，零间距；
  · 小标题「可用变量（先点一下要填的输入框）」被按钮挤在中间一行。

所以看着就像"样式没了"。现在：
  · 变量/短语两排改用专门的 .var-row（flex + gap + 胶囊按钮）；
  · 小标题 flex:0 0 100% 独占一行；
  · 顺手给 .row.wrap 补上 display:flex / gap 兜底（保存按钮那排也受益）。
"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
APPJS = os.path.join(ROOT, "static", "app.js")
CSS = os.path.join(ROOT, "static", "style.css")


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def _rule(css, sel):
    """取出某个选择器的规则体（首个）。"""
    m = re.search(re.escape(sel) + r"\s*\{([^{}]*)\}", css)
    return m.group(1) if m else ""


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _read(APPJS)
        cls.css = _read(CSS)

    # ---------- ① 标记层：容器与按钮的 class ----------
    def test_01_container_is_var_row(self):
        """变量/短语两排必须用 .var-row，不再是 .row.wrap。"""
        self.assertIn('class="var-row"', self.js)
        body = re.search(r"function varButtons[\s\S]{0,1600}?\n\}", self.js)
        self.assertTrue(body, "找不到 varButtons")
        self.assertNotIn("row wrap", body.group(0),
                         "varButtons 里不该再用 .row.wrap")

    def test_02_button_has_varbtn(self):
        self.assertIn('class="mini varbtn"', self.js)

    def test_03_phrase_not_dimmed(self):
        """常用短语整句都是要插进去的文本，不能套 .vc 半透明。"""
        self.assertIn('class="mini varbtn phr"', self.js)

    def test_04_var_code_span(self):
        """{xxx} 用 .vc 包起来（压暗一档）。"""
        self.assertIn('class="vc"', self.js)

    # ---------- ② 样式层：这些 class 必须真的有定义 ----------
    def test_05_css_var_row_defined(self):
        body = _rule(self.css, ".var-row")
        self.assertTrue(body, ".var-row 没有定义")
        self.assertIn("display: flex", body)
        self.assertIn("gap", body)
        self.assertIn("flex-wrap: wrap", body)

    def test_06_label_owns_line(self):
        """.var-row 里的小标题独占一行。"""
        body = _rule(self.css, ".var-row .mini-label")
        self.assertTrue(body, ".var-row .mini-label 没有定义")
        self.assertIn("100%", body)

    def test_07_css_varbtn_defined(self):
        body = _rule(self.css, ".var-row .varbtn")
        self.assertTrue(body, ".var-row .varbtn 没有定义")
        self.assertIn("999px", body)          # 胶囊
        self.assertIn("background", body)

    def test_08_css_vc_dimmed(self):
        body = _rule(self.css, ".var-row .varbtn .vc")
        self.assertTrue(body, ".vc 没有定义")
        self.assertIn("opacity", body)

    def test_09_row_wrap_fallback(self):
        """.row.wrap 补上 flex + gap（保存按钮那排也靠它）。"""
        body = _rule(self.css, ".row.wrap")
        self.assertTrue(body, ".row.wrap 没有定义")
        self.assertIn("display: flex", body)
        self.assertIn("gap", body)

    # ---------- ③ 行为不能被动过 ----------
    def test_10_still_ins_var(self):
        """按钮仍然是 data-act="ins-var"，插入行为不变。"""
        n = self.js.count('data-act="ins-var"')
        self.assertGreaterEqual(n, 2, "变量/短语按钮的插入动作丢了")

    def test_11_esc_used(self):
        """拼 HTML 必须转义。"""
        body = re.search(r"function varButtons[\s\S]{0,1600}?\n\}", self.js)
        self.assertIn("esc(", body.group(0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
