# -*- coding: utf-8 -*-
"""订阅迁移：默认一律不选 + 目标群已订过的划线置灰。

── ① 默认不能预先勾上 ──
迁移是破坏性操作。以前清单渲染出来就是"源群开着的类型预先勾上"，
等于把"确认"这一步省掉了：手滑点一下「全体迁移」整个群就搬走了。
现在一律默认不选，想要哪些自己勾。

── ② 两个群有共同的 UP 主 / 合集时 ──
搬过去只是覆盖一遍同样的东西，没有意义。这类卡片直接划线 + 置灰 + 标
「目标群已有」，让人一眼看出"剩下来这些才是真的要搬的"。
    ⚠️ 而且是**禁止勾选**的，不只是提示：
       以前"留着覆盖的口子"看着贴心，实际是坑 —— 目标群那边的提醒设置
       会被源群悄悄盖掉，而界面上看不出自己改了什么。真要覆盖就去目标群
       那边改，别借迁移的手。

── ③ 换目标群要重画 ──
哪些算"目标群已有"取决于目标群选的是哪个。换了目标群不重画，
划线就会停在旧目标群的结果上。
"""
import io
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "src")
APPJS = os.path.join(ROOT, "static", "app.js")
CSS = os.path.join(ROOT, "static", "style.css")
JSRUN = os.path.join(HERE, "_js_mig_default.js")


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def _strip(s):
    """去掉注释再匹配：注释里提到某个类名不等于真的写了。"""
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?m)^\s*//.*$", "", s)
    return s


def _fn(name):
    """取某个顶层函数的函数体（去注释后）。"""
    s = _strip(_read(APPJS))
    m = re.search(r"function\s+" + name + r"\s*\(\)\s*\{(.*?)\n\}\n", s, re.S)
    return m.group(1) if m else None


class TestMigPickDefaultOff(unittest.TestCase):
    """① 清单渲染出来必须一张都没勾。"""

    def test_卡片默认不带_on(self):
        b = _fn("renderMigPick")
        self.assertTrue(b, "找不到 renderMigPick")
        m = re.search(r"'<div class=\"mig-card\"(.*?)\"\>'", b, re.S)
        # 拼装形如 '<div class="mig-card' + (isDup ? ' dup' : '') + '"'
        m2 = re.search(r"return '<div class=\"mig-card'(.*?)'\>'", b, re.S)
        seg = (m2.group(1) if m2 else (m.group(1) if m else b))
        self.assertNotIn("' on'", seg,
                         "卡片拼装里带了 ' on'：渲染出来就是预先勾上的，"
                         "等于把迁移的确认步骤省掉了")
        self.assertNotIn('" on"', seg, "卡片拼装里带了 ' on'")

    def test_不再按源群开关预先勾(self):
        b = _fn("renderMigPick")
        # 旧写法：var on = ...kinds[k].on ...(on ? ' on' : '') 直接拼进卡片
        self.assertNotRegex(
            b, r"mig-card'\s*\+\s*\(\s*on\s*\?",
            "还在按源群的开关状态给卡片预先加 .on")

    def test_合集栏同样默认不选(self):
        b = _fn("renderMigSea")
        self.assertTrue(b, "找不到 renderMigSea")
        self.assertNotRegex(b, r"mig-card'\s*\+\s*\(\s*on\s*\?",
                            "合集卡片还在预先勾上")


class TestMigPickDupMarked(unittest.TestCase):
    """② 目标群已订过的要划线置灰、标出来。"""

    def test_有取重集合的函数(self):
        b = _fn("migDupSet")
        self.assertTrue(b, "找不到 migDupSet：没有'目标群已订过'的判定")
        self.assertIn("ups", b)
        self.assertIn("seas", b)

    def test_UP主栏标重(self):
        b = _fn("renderMigPick")
        self.assertIn("migDupSet()", b, "UP 主栏没算目标群已有")
        self.assertIn("isDup ? ' dup'", b, "没有给重复卡片加 .dup")
        self.assertIn("目标群已有", b, "没有「目标群已有」标签")

    def test_合集栏标重(self):
        b = _fn("renderMigSea")
        self.assertIn("migDupSet()", b, "合集栏没算目标群已有")
        self.assertIn("isDup ? ' dup'", b, "合集重复卡片没有加 .dup")

    def test_源群等于目标群时不标重(self):
        b = _fn("migDupSet")
        self.assertIn("migSrcGroup()", b,
                      "源群 == 目标群时没排除：会把自己订的全划成'已有'")

    def test_点击入口拦住重复卡片_UP主(self):
        """点 UP 主卡片：目标群已订过的必须选不中。"""
        s = _strip(_read(APPJS))
        m = re.search(r"'mig-card'\(el\) \{(.*?)\n  \}", s, re.S)
        self.assertTrue(m, "找不到 mig-card 处理")
        b = m.group(1)
        self.assertIn("contains('dup')", b, "mig-card 没判重：重复卡片仍会被勾上")
        # 拦下之后必须 return，否则照样 toggle
        self.assertIn("return", b, "判重之后没 return：提示完还是勾上了")

    def test_点击入口拦住重复卡片_合集(self):
        s = _strip(_read(APPJS))
        m = re.search(r"'mig-sea-card'\(el\) \{(.*?)\n  \}", s, re.S)
        self.assertTrue(m, "找不到 mig-sea-card 处理")
        b = m.group(1)
        self.assertIn("contains('dup')", b, "合集卡片没判重")
        self.assertIn("return", b, "合集判重之后没 return")

    def test_提醒小卡片也拦住重复(self):
        s = _strip(_read(APPJS))
        m = re.search(r"'mig-chip'\(el\) \{(.*?)\n  \}", s, re.S)
        self.assertTrue(m, "找不到 mig-chip 处理")
        self.assertIn("contains('dup')", m.group(1), "重复卡片里的提醒开关没拦")

    def test_全选不勾重复项(self):
        """「全选」也不能把目标群已有的勾上。"""
        s = _strip(_read(APPJS))
        m = re.search(r"'mig-pick'\(el\) \{(.*?)\n  \}", s, re.S)
        b = m.group(1)
        # 旧写法：if (mode === '1') want = true; —— 全选会把重复项一起带上
        self.assertNotRegex(
            b, r"mode === '1'\)\s*want = true",
            "「全选」还在无条件勾上所有卡片，目标群已有的也被带走了")
        self.assertRegex(b, r"mode === '1'\)\s*want = !isDup",
                         "「全选」没排除目标群已有的")

    def test_收集时过滤重复项(self):
        """最后一道闸：万一卡片同时带着 on 和 dup，收集时也要滤掉。"""
        s = _strip(_read(APPJS))
        self.assertRegex(
            s, r"#migPickList \.mig-card\.on:not\(\.dup\)",
            "collectMigPicks 没滤掉重复项")
        self.assertRegex(
            s, r"#migSeaList \.mig-card\.on:not\(\.dup\)",
            "collectMigSeasons 没滤掉重复项")

    def test_css_cursor_提示不可点(self):
        c = _read(CSS)
        self.assertRegex(
            c, r"\.mig-card\.dup\s*\{[^}]*cursor\s*:\s*not-allowed",
            "重复卡片没改鼠标指针：看着能点，点了却没反应，会以为卡了")

    def test_css_有划线和置灰(self):
        c = _read(CSS)
        self.assertRegex(c, r"\.mig-card\.dup\s*\.\s*t1\s*\{[^}]*line-through",
                         "没有划线样式")
        self.assertRegex(c, r"\.mig-card\.dup:not\(\.on\)\s*\{[^}]*opacity",
                         "没有置灰样式")


class TestMigPickRerender(unittest.TestCase):
    """③ 换源/目标群要重画，否则划线停在旧结果上。"""

    def setUp(self):
        self.js = _strip(_read(APPJS))

    def _bind(self, eid):
        m = re.search(r"id === '" + eid + r"'(.*?)\}\);", self.js, re.S)
        return m.group(1) if m else ""

    def test_换目标群重画(self):
        b = self._bind("migDst")
        self.assertTrue(b, "migDst 没绑 change")
        self.assertIn("renderMigPick()", b,
                      "换目标群不重画：划线会停在旧目标群的结果上")

    def test_换源群重画(self):
        b = self._bind("migSrc")
        self.assertTrue(b, "migSrc 没绑 change")
        self.assertIn("renderMigPick()", b, "换源群不重画")


class TestMigPickOnlyOnSkipsDup(unittest.TestCase):
    """「只选已开启的」要跳过目标群已经订过的。"""

    def test_UP主栏跳过重复(self):
        s = _strip(_read(APPJS))
        m = re.search(r"'mig-pick'\(el\) \{(.*?)\n  \}", s, re.S)
        self.assertTrue(m, "找不到 mig-pick 处理")
        b = m.group(1)
        self.assertIn("dup", b, "「只选已开启的」没算目标群已有")
        self.assertIn("isDup", b, "「只选已开启的」没跳过重复项")

    def test_合集栏跳过重复(self):
        s = _strip(_read(APPJS))
        m = re.search(r"'mig-pick'\(el\) \{(.*?)\n  \}", s, re.S)
        b = m.group(1)
        self.assertIn("#migSeaList", b, "「只选已开启的」不作用于合集栏")
        self.assertIn("contains('dup')", b, "合集栏没跳过重复项")


class TestMigPickE2E(unittest.TestCase):
    """jsdom 真跑：渲染出来到底勾没勾、划没划线。"""

    def test_jsdom(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("没有 node")
        if not os.path.exists(JSRUN):
            self.skipTest("没有 e2e 脚本")
        # jsdom 装在某个临时目录里，node 从那个目录起才会去 lib/node_modules 找
        ws = os.path.abspath(os.path.join(HERE, "..", ".."))
        cands = [os.path.join(ws, "_jsdom2"), os.path.join(ws, "_jsdomtest"),
                 "/tmp/jd", ws, HERE]
        cwd = None
        for c in cands:
            if not os.path.isdir(c):
                continue
            try:
                p = subprocess.run([node, "-e", "require('jsdom')"],
                                   capture_output=True, timeout=60, cwd=c)
            except Exception:
                continue
            if p.returncode == 0:
                cwd = c
                break
        if not cwd:
            self.skipTest("没有 jsdom")
        r = subprocess.run([node, JSRUN], capture_output=True,
                           text=True, timeout=180, cwd=cwd)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertIn("ALL PASS", out, "真跑未通过：\n" + out[-2000:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
