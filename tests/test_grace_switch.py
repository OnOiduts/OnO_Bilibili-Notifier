# -*- coding: utf-8 -*-
"""「启动后首轮静默」开关必须点得动、外观会变、改完自动保存。

事故复盘（v2.3.4）：功能本身（bot.py / webui.py 那半）是好的，
但面板这一侧**三处登记全漏了**，于是用户看到的是"按钮无法交互、没有自动保存"：

  1. SW_IDS 没登记 cGraceOn
     → paintSwitches() 不给 label 加 .on：点了外观纹丝不动
     → initSwitches() 不绑 change：不重画、不出声

  2. CFG_FIELDS 没登记 cGraceOn / cGraceRounds
     → 不进 FORM_DIRTY：loadState() 每 15 秒一刷就用后台值把框覆盖回去
       （改了看着像没保存，刷新就变回原样）

  3. 自动保存列表没登记这两个 id
     → 改完不提交，必须再去点「保存」；而用户根本不知道要手动保存

这三处都是"漏登记"，逻辑本身没写错，所以只测功能测不出来。
这个测试直接查这三份名单 + 用 jsdom 真跑一遍点击。
"""
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
APPJS = os.path.join(ROOT, "static", "app.js")
HTML = os.path.join(ROOT, "templates", "index.html")


def _js():
    return open(APPJS, encoding="utf-8").read()


def _html():
    return open(HTML, encoding="utf-8").read()


def _strip(s):
    """去掉注释再匹配：注释里提到某个 id 不等于真的登记了。"""
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?m)^\s*//.*$", "", s)
    return s


def _sw_ids():
    m = re.search(r"const\s+SW_IDS\s*=\s*\[(.*?)\]", _js(), re.S)
    return re.findall(r"'([A-Za-z0-9_\-]+)'", m.group(1)) if m else []


def _cfg_fields():
    m = re.search(r"const\s+CFG_FIELDS\s*=\s*\[(.*?)\]", _js(), re.S)
    return re.findall(r"'([A-Za-z0-9_\-]+)'", m.group(1)) if m else []


def _autosave_ids():
    """initFormDirty 里那个"改完立刻保存"的 id 名单。

    判据不是"有个数组 .includes(id)"，而是**这个数组后面紧跟 saveCfg(true)**
    —— 页面里还有别的 `.includes(id)`（比如折叠摘要那个），只取"最近的数组"
    会取错。
    """
    s = _js()
    for m in re.finditer(r"\[([^\[\]]*?)\]\s*\.includes\(id\)", s, re.S):
        tail = s[m.end():m.end() + 60]
        if "saveCfg(true)" in tail:
            return re.findall(r"'([A-Za-z0-9_\-]+)'", m.group(1))
    return []


class TestGraceSwitchRegistered(unittest.TestCase):
    def test_html_has_switch(self):
        self.assertIn('id="cGraceOn"', _html(), "HTML 里没有这个开关")
        self.assertIn('id="cGraceRounds"', _html(), "HTML 里没有轮数框")

    def test_in_sw_ids(self):
        """漏了这条就"点了没动画、外观不动"。"""
        self.assertIn("cGraceOn", _sw_ids(),
                      "cGraceOn 没登记进 SW_IDS，开关外观永远画成'关'")

    def test_in_cfg_fields(self):
        """漏了这条就"改完被后台值冲掉"。"""
        f = _cfg_fields()
        self.assertIn("cGraceOn", f, "cGraceOn 不在 CFG_FIELDS，改完不进 FORM_DIRTY")
        self.assertIn("cGraceRounds", f,
                      "cGraceRounds 不在 CFG_FIELDS，改完会被 loadState 覆盖回去")

    def test_in_autosave_list(self):
        """漏了这条就"改完不会自动保存"。"""
        a = _autosave_ids()
        self.assertIn("cGraceOn", a, "改开关不会自动保存")
        self.assertIn("cGraceRounds", a, "改轮数不会自动保存")

    def test_sync_grace_paints(self):
        """syncGraceOn 改完 checked 必须手动重画（程序赋值不触发 change）。"""
        s = _strip(_js())
        m = re.search(r"function syncGraceOn\(\)\s*\{(.*?)\n\}", s, re.S)
        self.assertTrue(m, "找不到 syncGraceOn")
        self.assertIn("paintSwitches()", m.group(1),
                      "syncGraceOn 里没调 paintSwitches："
                      "改轮数让开关跟着开/关时，滑块不会动")

    def test_savecfg_syncs_before_reading(self):
        """保存前先对齐一次：两个监听器的先后顺序不定。"""
        s = _strip(_js())
        m = re.search(r"startup_grace_rounds:\s*\(function\s*\(\)\s*\{(.*?)\}\)\(\)",
                      s, re.S)
        self.assertTrue(m, "saveCfg 里没有 startup_grace_rounds")
        self.assertIn("syncGraceOn()", m.group(1),
                      "saveCfg 读值前没先 syncGraceOn，可能读到不一致的中间态")


class TestGraceSwitchE2E(unittest.TestCase):
    """jsdom 真跑：点一下开关，看外观、轮数、自动保存对不对。"""

    SCRIPT = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tests", "_e2e_grace.js")

    def test_click_toggles_and_saves(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("没有 node")
        if not os.path.exists(self.SCRIPT):
            self.skipTest("没有 e2e 脚本")
        try:
            subprocess.run([node, "-e", "require('jsdom')"],
                           capture_output=True, timeout=60)
        except Exception:
            self.skipTest("没有 jsdom")
        r = subprocess.run([node, self.SCRIPT], capture_output=True,
                           text=True, timeout=180)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertIn("全部通过: true", out,
                      "真跑未通过：\n" + out[-1500:])
