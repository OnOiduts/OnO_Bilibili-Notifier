# -*- coding: utf-8 -*-
"""专属文案弹层里「保存」按钮的样式 锁定测试。

反馈：「没修好啊，保存还没修呢」

── 为什么只修了清空、保存还是丑的 ──
上一轮把「全部清空」从**连一个 class 都没有的裸按钮标签**改成 .btn.dg，
它立刻有了 .btn 提供的基础尺寸（padding 8px 15px / 圆角 9px / 字号 13px）。

但旁边的「保存」挂的是 **btn-primary**，而 btn-primary 只是"主推色"修饰类：

    button.btn-primary {
      border: 0; color: #fff;
      background: linear-gradient(...);   ← 只有颜色
    }

**padding / 圆角 / 字号 / 字体它一个都没给**，而样式表里也没有针对
裸 button 标签的基础规则（跟 .btn-primary 的情况一样）。

于是「保存」只有渐变底和白字，其余全用浏览器默认值 ——
变成一颗**又小又方角的按钮**，紧挨着旁边正经的 .btn.dg，
两个按钮大小风格完全不搭，看着就像"样式丢了"。

现在给 button.btn-primary 补齐与 .btn 完全一致的尺寸：
保留渐变"主推"特色，又跟旁边的按钮协调。

⚠️ 这条测试盯的是**尺寸属性**，不是颜色：
   以后谁再把 padding / border-radius / font-size 删掉，这里立刻红。
"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
CSS = os.path.join(ROOT, "static", "style.css")
HTML = os.path.join(ROOT, "templates", "index.html")
APPJS = os.path.join(ROOT, "static", "app.js")
LOGIN = os.path.join(ROOT, "templates", "login.html")


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def _rule(css, sel):
    """取出某个选择器的规则体（首个）。"""
    m = re.search(re.escape(sel) + r"\s*\{([^{}]*)\}", css)
    return m.group(1) if m else ""


def _rule_with(css, sel, must):
    """取出**含指定内容**的那个规则体。

    ⚠️ .btn 在模板里出现多次（还有一条 @media 里的 .btn{min-height:38px}），
    取首个会拿到那条只有 min-height 的，基准就错了。
    """
    for m in re.finditer(re.escape(sel) + r"\s*\{([^{}]*)\}", css):
        if must in m.group(1):
            return m.group(1)
    return ""


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = _read(CSS)
        cls.html = _read(HTML)
        cls.js = _read(APPJS)
        cls.login = _read(LOGIN)
        cls.primary = _rule(cls.css, "button.btn-primary")
        cls.btn = _rule_with(cls.html, ".btn", "padding")

    # ---------- ① 尺寸：这是本轮真正修的东西 ----------
    def test_01_primary_rule_exists(self):
        self.assertTrue(self.primary, "button.btn-primary 规则没了")

    def test_02_primary_has_padding(self):
        """.btn 是 8px 15px，btn-primary 必须一致。"""
        self.assertIn("padding", self.primary)
        self.assertIn("8px 15px", self.primary)

    def test_03_primary_has_radius(self):
        self.assertIn("border-radius", self.primary)
        self.assertIn("var(--r-sm)", self.primary)

    def test_04_primary_has_font(self):
        self.assertIn("font-size", self.primary)
        self.assertIn("13px", self.primary)
        self.assertIn("font-family: inherit", self.primary)

    def test_05_primary_keeps_gradient(self):
        """补齐尺寸的同时不能把渐变特色弄丢。"""
        self.assertIn("linear-gradient", self.primary)
        self.assertIn("border: 0", self.primary)

    # ---------- ② 跟 .btn 对齐（两个按钮必须一样大） ----------
    def test_06_sizes_match_btn(self):
        """逐个比对：padding / 圆角 / 字号三者与 .btn 完全一致，
        否则「保存」和「全部清空」又会一大一小。"""
        for key in ("8px 15px", "var(--r-sm)", "13px"):
            self.assertIn(key, self.btn, ".btn 里没有 %s（基准变了）" % key)
            self.assertIn(key, self.primary,
                          "btn-primary 与 .btn 的 %s 不一致" % key)

    # ---------- ③ 弹层里那两个按钮本身 ----------
    def test_07_tpl_save_uses_primary(self):
        """专属文案弹层的「保存」，UP 主版 + 合集版各一处。"""
        self.assertGreaterEqual(
            self.js.count('class="btn-primary" data-act="save-'), 2,
            "专属文案保存按钮丢了")

    def test_08_tpl_clear_uses_btn_dg(self):
        """「全部清空」必须是 .btn.dg（上轮修的，别回退成裸标签）。"""
        self.assertGreaterEqual(
            self.js.count('class="btn dg" data-act="clear-'), 2,
            "全部清空按钮又变回裸标签了")

    def test_09_no_naked_clear_button(self):
        """裸按钮标签（<button data-act=）不允许再出现。
        ⚠️ 别在注释里写这个字面量，否则这条会被自己的注释触发
        （这个坑踩过不止一次）。"""
        bad = re.findall(r"<button\s+data-act=", self.js)
        self.assertEqual(bad, [], "又出现没挂 class 的按钮：%r" % bad[:3])

    # ---------- ④ 其它用到 btn-primary 的地方同样受益 ----------
    def test_10_login_button_primary(self):
        """登录页的「进入」也是 btn-primary，尺寸一起修好。"""
        # ⚠️ 断言跟着登录页重写改过：登录页是独立模板，按钮类早就从
        #    btn-primary（面板的类）换成了自包含的 login-btn ——
        #    继续认 btn-primary 等于一直红，而且会误导人把面板的类加回来。
        self.assertIn('class="login-btn"', self.login)
        self.assertIn('type="submit"', self.login)

    def test_11_goto_setup_primary(self):
        """没填凭据时的「👉 现在去填写」。"""
        self.assertIn('class="btn-primary" data-act="goto-setup"', self.js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
