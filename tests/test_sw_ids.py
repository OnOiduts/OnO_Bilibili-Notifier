# -*- coding: utf-8 -*-
"""所有 sw-btn 开关的 id 都必须登记进 SW_IDS。

事故复盘（v1.41.0 ~ v1.43.0）：
  v1.41.0 在「设置 → 通用 → 图片」新增了一个开关 cSizeHint（图片尺寸提示），
  HTML 写好了、fillCfg 回填了、saveCfg 也提交了，**唯独忘了同步 SW_IDS 数组**。

  后果不是报错，而是静默的：
    1. paintSwitches() 只遍历 SW_IDS → 不给 cSizeHint 的 label 加 .on 类
       → 后台明明是"开"（checked=true），界面却永远画成"关"
    2. initSwitches() 也只遍历 SW_IDS → 不绑 change → 点了不重画
       → 用户看到的就是"点了没动画没反应"

  值其实保存成功了，但界面毫无反馈，用户完全无法判断到底生效没有 ——
  这种"值对、显示错"的问题靠肉眼排查几乎查不出来。

这个测试**自动扫描 HTML**，不靠人手工维护名单：以后再新增开关忘了登记，
这里会直接指名道姓报出来。
"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
HTML = os.path.join(ROOT, "templates", "index.html")
APPJS = os.path.join(ROOT, "static", "app.js")


def _html_sw_ids():
    """扫出 HTML 里所有 `<label class="sw-btn">` 包裹的 checkbox id。"""
    if not os.path.exists(HTML):
        return []
    s = open(HTML, encoding="utf-8").read()
    ids = []
    # 形如 <label class="sw-btn"><input type="checkbox" id="cXxx" ...>
    for m in re.finditer(
        r'<label[^>]*class="[^"]*\bsw-btn\b[^"]*"[^>]*>\s*'
        r'<input[^>]*type="checkbox"[^>]*>',
        s,
        re.S,
    ):
        block = m.group(0)
        im = re.search(r'\bid="([A-Za-z0-9_\-]+)"', block)
        if im:
            ids.append(im.group(1))
    return ids


def _js_sw_ids():
    """从 app.js 里解析 SW_IDS 数组的内容。"""
    if not os.path.exists(APPJS):
        return None
    s = open(APPJS, encoding="utf-8").read()
    m = re.search(r"const\s+SW_IDS\s*=\s*\[(.*?)\]", s, re.S)
    if not m:
        return None
    return re.findall(r"'([A-Za-z0-9_\-]+)'", m.group(1))


class TestSwitchIds(unittest.TestCase):
    def test_sw_ids_parsed(self):
        """SW_IDS 必须能解析出来（防改动写法后扫描失效）。"""
        self.assertIsNotNone(_js_sw_ids(), "app.js 里找不到 SW_IDS")
        self.assertTrue(len(_js_sw_ids()) > 0, "SW_IDS 是空的")

    def test_every_html_switch_registered(self):
        """HTML 里每个 sw-btn 开关都必须登记进 SW_IDS。"""
        js_ids = _js_sw_ids()
        missing = [i for i in _html_sw_ids() if i not in (js_ids or [])]
        self.assertEqual(
            [], missing,
            "这些开关在 HTML 里存在，却没登记进 SW_IDS，"
            "会导致外观永远显示'关'、点了没反应：" + ", ".join(missing),
        )

    def test_csizehint_registered(self):
        """cSizeHint（图片尺寸提示）必须在册 —— 这是本次事故的具体案例。"""
        self.assertIn("cSizeHint", _js_sw_ids() or [],
                      "cSizeHint 没登记进 SW_IDS，「图片」区块会看起来失灵")

    def test_no_duplicate(self):
        """SW_IDS 里不应有重复项（重复会绑两次 change，保存两次）。"""
        ids = _js_sw_ids() or []
        dup = [i for i in set(ids) if ids.count(i) > 1]
        self.assertEqual([], dup, "SW_IDS 有重复项：" + ", ".join(dup))

    def test_registered_ids_exist_in_html(self):
        """SW_IDS 里登记的 id 必须在 HTML 里真的存在（防登记了不存在的 id）。"""
        html_ids = set(_html_sw_ids())
        # 沙箱是"一对"开关，两个都在 HTML 里
        orphan = [i for i in (_js_sw_ids() or []) if i not in html_ids]
        self.assertEqual([], orphan,
                         "SW_IDS 登记了 HTML 里不存在的 id：" + ", ".join(orphan))


if __name__ == "__main__":
    unittest.main(verbosity=2)
