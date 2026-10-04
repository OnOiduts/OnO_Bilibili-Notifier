# -*- coding: utf-8 -*-
"""收起群卡片后，栏位要复位回「👤 UP 主」。

反馈：
  「我希望折叠框折叠后可以回到 up 主栏」

── 行为 ──
群内那两栏（UP 主 / 合集）停在哪一栏是靠 MINI_TAB 记住的 ——
这是为了让「改完合集文案一保存不被打回 UP 页」而加的。

但记忆不该永久有效：卡片都收起来了，再展开时应当从头开始，
停在「👤 UP 主」栏。否则上次看的是合集，收起再展开还是合集，
跟"折叠=收起这个群的内部状态"的直觉不符。

所以 toggle-group 收起分支里要 delete MINI_TAB['grp:'+gid]，
展开分支不能动它。
"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
APPJS = os.path.join(ROOT, "static", "app.js")


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def _toggle_body(js):
    """取 'toggle-group'(el) { ... } 这段的函数体。"""
    m = re.search(r"'toggle-group'\(el\) \{(.*?)\n  \},", js, re.S)
    assert m, "找不到 toggle-group 动作"
    return m.group(1)


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _read(APPJS)
        cls.body = _toggle_body(cls.js)

    def test_01_collapse_clears_tab(self):
        """收起时必须清掉栏位记忆 —— 这是本次反馈的核心。"""
        self.assertRegex(
            self.body,
            r"delete\s+MINI_TAB\['grp:'\s*\+\s*gid\]",
            "toggle-group 收起时必须 delete MINI_TAB['grp:'+gid]",
        )

    def test_02_clear_is_in_collapse_branch(self):
        """清理必须在「收起」分支里，不能在展开分支，也不能无条件清。"""
        m = re.search(r"if\s*\(EXPANDED\.has\(gid\)\)\s*\{(.*?)\n    \}\s*else",
                      self.body, re.S)
        self.assertTrue(m, "toggle-group 里应当有 if (EXPANDED.has(gid)) 分支")
        self.assertIn("EXPANDED.delete(gid)", m.group(1),
                      "这个分支应当是收起（删掉展开状态）")
        self.assertIn("MINI_TAB", m.group(1), "栏位复位必须发生在收起分支里")

    def test_03_expand_branch_keeps_tab(self):
        """展开分支只能加展开状态，不能顺手清记忆（清了就白记了）。"""
        m = re.search(r"\}\s*else\s*EXPANDED\.add\(gid\)", self.body)
        self.assertTrue(m, "展开分支应当是 EXPANDED.add(gid)")
        # else 分支里不该出现 delete MINI_TAB
        tail = self.body[m.start():]
        self.assertNotIn("delete MINI_TAB", tail.split("renderGroups()")[0],
                         "展开分支不能清栏位记忆")

    def test_04_still_renders(self):
        """改完还得重画，不然界面不动。"""
        self.assertIn("renderGroups()", self.body)

    def test_05_default_falls_back_to_up(self):
        """清掉之后没有记忆，重画要回落 UP 主栏。"""
        m = re.search(
            r"const mvOn = MINI_TAB\['grp:' \+ g\.gid\] \|\| mvUp;", self.js)
        self.assertTrue(m, "没有记忆时必须回落到 mvUp（UP 主栏）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
