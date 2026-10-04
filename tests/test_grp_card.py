# -*- coding: utf-8 -*-
"""「订阅 → 群与订阅 / 登记群」三项反馈的锁定测试。

1. 群卡片打不开（v1.51.0 引入，v1.52.0 修）
   v1.51.0 为了治「页面老是自己刷新」，给 renderGroups 加了指纹 groupsSig()，
   内容没变就**不重画**。但指纹里只放了 STATE.groups，
   **没放展开状态 EXPANDED** —— 而点群卡片改的恰恰只有 EXPANDED。

   于是：点一下 → EXPANDED 变了 → 数据没变 → 指纹相同 → 直接 return
   → 卡片永远打不开。这正是反馈里的「卡片打不开了」。

   这个测试用**旧写法复现一遍**再验证新写法，确认根因就是它。

2. 已登记群的头像要跟着「群与订阅」里选的那张走
   以前 renderGrpList 取的是"第一个有头像的订阅"，而群里 🖼 选的可能是
   另一位 UP 主 —— 两边不一致，看着像两个不同的群。

3. 群背景挂在卡片层（不是头部层）
   挂头部：高度被头部锁死，展开也看不出变化。
   挂卡片：撑满外框，展开变高时正方形整体等比放大。
"""
import io
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
APPJS = os.path.join(ROOT, "static", "app.js")
HTML = os.path.join(ROOT, "templates", "index.html")


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def _js():
    return _read(APPJS)


def _html():
    return _read(HTML)


class TestGroupCardExpand(unittest.TestCase):
    """指纹里必须带上展开状态，否则点了不重画。"""

    def test_sig_includes_expanded(self):
        s = _js()
        m = re.search(r"function\s+groupsSig\s*\(\)\s*\{(.*?)\n\}", s, re.S)
        self.assertIsNotNone(m, "找不到 groupsSig()")
        body = m.group(1)
        # ⚠️ 只查代码行：注释里为了说明事故必然提到 EXPANDED，
        #    朴素子串匹配会把"注释里写了"当成"代码里用了"。
        code = "\n".join(
            ln for ln in body.splitlines() if not ln.strip().startswith(("*", "//", "/*"))
        )
        self.assertIn("EXPANDED", code, "groupsSig() 的指纹里没有展开状态")

    def test_toggle_group_rerenders(self):
        """点群卡片后必须真的重画（不能只改集合就完事）。"""
        s = _js()
        m = re.search(r"'toggle-group'\s*\(el\)\s*\{(.*?)\n  \}", s, re.S)
        self.assertIsNotNone(m, "找不到 toggle-group 动作")
        body = m.group(1)
        self.assertIn("EXPANDED", body)
        self.assertIn("renderGroups(", body)


class TestGroupListFace(unittest.TestCase):
    """已登记群的头像跟着「群与订阅」选的那张。"""

    def test_grp_face_helper_exists(self):
        self.assertIn("function grpFaceOf(", _js())

    def test_helper_reads_selected_uid(self):
        s = _js()
        m = re.search(r"function\s+grpFaceOf\s*\(g\)\s*\{(.*?)\n\}", s, re.S)
        self.assertIsNotNone(m, "找不到 grpFaceOf()")
        body = m.group(1)
        self.assertIn("gbg:", body, "没读群里选中的 uid（localStorage 键 gbg:<gid>）")
        self.assertIn("s.face", body)
        # 选过就用选的，没选过退回第一个有头像的
        self.assertIn("find(", body)

    def test_render_grp_list_uses_helper(self):
        s = _js()
        m = re.search(r"function\s+renderGrpList\s*\(\)\s*\{(.*?)\n\}", s, re.S)
        self.assertIsNotNone(m, "找不到 renderGrpList()")
        body = m.group(1)
        self.assertIn("grpFaceOf(", body)
        # ⚠️ 旧的"取第一个"写法必须彻底消失，不能两套并存
        self.assertNotIn("first.face", body)


class TestGroupBgLayer(unittest.TestCase):
    """群背景挂在卡片层，且正方形等比。"""

    def test_bg_is_child_of_card(self):
        s = _js()
        # .grp-bg 必须出现在 .grp 之后、.grp-head 之前
        i_card = s.find('"\' + \'" data-gid="\' + gid + \'">\'')
        # 直接找渲染串：找一次 '<div class="grp-bg">'
        i_bg = s.find('\'<div class="grp-bg"></div>\'')
        if i_bg < 0:
            i_bg = s.find('<div class="grp-bg"></div>')
        self.assertGreater(i_bg, 0, "渲染串里没有 .grp-bg")
        head = s.find('\'<div class="grp-head"')
        self.assertGreater(head, 0, "渲染串里没有 .grp-head")
        self.assertLess(i_bg, head, ".grp-bg 必须在 .grp-head 之前（卡片层）")

    def test_css_card_is_positioned(self):
        h = _html()
        m = re.search(r"\.grp\s*\{(.*?)\}", h, re.S)
        self.assertIsNotNone(m)
        self.assertIn("position:relative", m.group(1),
                      ".grp 没有 position:relative，背景会找错定位祖先")

    def test_css_bg_square_and_capped(self):
        h = _html()
        m = re.search(r"\.grp-bg\s*\{(.*?)\}", h, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("aspect-ratio", body, "背景不是正方形，展开会拉变形")
        self.assertIn("max-height", body, "没有高度上限，卡片很高时会糊满屏")
        # ⚠️ 100% 100% 会把非正方形头像拉变形，改用 contain
        self.assertNotIn("background-size:100% 100%", body)

    def test_css_children_above_bg(self):
        h = _html()
        self.assertIn(".grp > .grp-head, .grp > .gbg-pick, .grp > .grp-body", h,
                      "卡片内容没有抬到背景之上")


if __name__ == "__main__":
    unittest.main()
