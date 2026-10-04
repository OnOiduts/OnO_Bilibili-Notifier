# -*- coding: utf-8 -*-
"""订阅数去重 / 按 UP 主·合集筛群 / 设置长卡片折叠。

── ① 订阅数必须去重 ──
同一个 UP 主常被好几个群同时订阅。以前统计卡片直接把每个群的订阅条数
加起来，N 个群订同一个 UP 就数成 N 条 —— 卡片上写着 10，
实际去重后可能只有 4 个。看着像"订阅变多了"，其实是重复计数。

── ② 按已订阅的 UP 主 / 合集反查是哪些群订了它 ──
群一多，"这个 UP 主到底订在哪几个群里"只能一个个群点开翻。
现在把已订阅条目去重列成一张表（顺带标出订了它的群有几个），
挑一个就筛出对应的群。

── ③ 设置里过长的卡片折起来 ──
「图片」「数据与备份」参数多、展开后把下面的项全挤到屏幕外。
折起来只占一行，摘要里仍能看到关键值。
"""
import io
import os
import re
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "src")
APPJS = os.path.join(ROOT, "static", "app.js")
HTML = os.path.join(ROOT, "templates", "index.html")
CSS = os.path.join(ROOT, "static", "style.css")
JSRUN = os.path.join(HERE, "_js_subfilter.js")


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


class TestSubStatsDedupe(unittest.TestCase):
    """订阅数按 uid / (uid+合集ID) 去重，不再按群累加。"""

    @classmethod
    def setUpClass(cls):
        cls.js = _read(APPJS)

    def _stats_body(self):
        m = re.search(r"function renderStats\(\) \{(.*?)\n\}\n", self.js, re.S)
        self.assertTrue(m, "找不到 renderStats")
        return m.group(1)

    def test_用集合去重(self):
        b = self._stats_body()
        self.assertIn("new Set()", b, "统计应改用 Set 去重")
        self.assertIn("upSet.size", b)
        self.assertIn("seaSet.size", b)

    def test_不再按群累加(self):
        b = self._stats_body()
        # 旧写法：nUp += (x.subs || []).length
        self.assertNotRegex(
            b, r"nUp\s*\+=\s*\(x\.subs",
            "还在按群累加订阅条数，同一个 UP 会被数多次")

    def test_小字标注已去重(self):
        b = self._stats_body()
        self.assertIn("已去重", b)


class TestSubFilterUI(unittest.TestCase):
    """按已订阅的 UP 主 / 合集筛群。"""

    @classmethod
    def setUpClass(cls):
        cls.js = _read(APPJS)
        cls.html = _read(HTML)

    def test_页面有筛选下拉(self):
        for i in ("subFilterKind", "subFilterPick", "subFilterPickRow"):
            self.assertIn('id="%s"' % i, self.html, "页面缺少 #" + i)

    def test_筛选方式包含_up_与合集(self):
        self.assertIn('value="up"', self.html)
        self.assertIn('value="season"', self.html)
        self.assertIn('value="name"', self.html)

    def test_四个函数都在(self):
        for fn in ("subFilterKindVal", "subFilterPickVal", "subFilterItems",
                   "renderSubFilterPick", "subFilterMatch", "subFilterActive"):
            self.assertIn("function %s(" % fn, self.js, "缺少 " + fn)

    def test_条目去重并数出群数(self):
        b = re.search(r"function subFilterItems\(kind\) \{(.*?)\n\}\n",
                      self.js, re.S)
        self.assertTrue(b, "找不到 subFilterItems")
        self.assertIn("it.n += 1", b.group(1), "没数出订了它的群有几个")
        self.assertIn("new Map()", b.group(1), "同一条目被多个群订阅应只列一次")

    def test_搜群时先过筛选(self):
        b = re.search(r"function subMatch\(g, kw\) \{(.*?)\n  const kind",
                      self.js, re.S)
        self.assertTrue(b, "subMatch 结构变了")
        self.assertIn("subFilterMatch(g)", b.group(1),
                      "subMatch 没先过筛选，筛了等于没筛")

    def test_指纹带筛选状态(self):
        """不带上就会出现「换了筛选但列表纹丝不动」。"""
        b = re.search(r"function groupsSig\(\) \{(.*?)\n\}\n", self.js, re.S)
        self.assertTrue(b, "找不到 groupsSig")
        self.assertIn("subFilterKindVal()", b.group(1))
        self.assertIn("subFilterPickVal()", b.group(1))

    def test_清空连筛选一起复位(self):
        b = re.search(r"'sub-search-clear'\(\) \{(.*?)\n  \},", self.js, re.S)
        self.assertTrue(b, "找不到清空动作")
        self.assertIn("subFilterKind", b.group(1),
                      "清空没复位筛选方式，点完还是只看到一小撮群")
        self.assertIn("renderSubFilterPick()", b.group(1))

    def test_换筛选要重新渲染(self):
        b = re.search(r"function initFormDirty\(\) \{(.*?)\n\}\n",
                      self.js, re.S)
        self.assertTrue(b, "找不到 initFormDirty")
        self.assertIn("subFilterKind", b.group(1), "没给筛选下拉绑 change")
        self.assertIn("subFilterPick", b.group(1))

    def test_数据刷新时重填候选(self):
        self.assertIn("subfilterpick", self.js,
                      "订阅数据变了，候选条目要跟着变")


class TestFoldCards(unittest.TestCase):
    """设置里「图片」「数据与备份」两张长卡片可折叠。"""

    @classmethod
    def setUpClass(cls):
        cls.js = _read(APPJS)
        cls.html = _read(HTML)
        cls.css = _read(CSS)

    def _card(self, cid):
        m = re.search(r'<details[^>]*id="%s".*?</details>' % cid,
                      self.html, re.S)
        self.assertTrue(m, "#%s 不是 details 卡片" % cid)
        return m.group(0)

    def test_两张卡片都是_details(self):
        for cid in ("imgCard", "bkCard"):
            c = self._card(cid)
            self.assertIn('class="card fold"', c)
            self.assertIn("summary", c)

    def test_默认收起(self):
        for cid in ("imgCard", "bkCard"):
            self.assertNotIn('<details class="card fold" id="%s" open' % cid,
                             self.html, "#%s 不该默认展开" % cid)

    def test_有标题和摘要位(self):
        for cid, sid in (("imgCard", "imgCardSum"), ("bkCard", "bkCardSum")):
            c = self._card(cid)
            self.assertIn('class="fold-hd"', c)
            self.assertIn('class="fold-ic"', c)
            self.assertIn('id="%s"' % sid, c, "#%s 缺摘要位" % sid)
            self.assertIn('class="fold-bd"', c)

    def test_图片摘要会跟着设置变(self):
        self.assertIn("function syncImgCardSum(", self.js)
        b = re.search(r"function fillCfg\(\) \{(.*?)\n\}\n", self.js, re.S)
        self.assertTrue(b, "找不到 fillCfg")
        self.assertIn("syncImgCardSum()", b.group(1),
                      "填完配置没刷新摘要，折着看不出当前设置")

    def test_备份摘要有位置和份数(self):
        b = re.search(r"async function loadBackupInfo\(\) \{(.*?)\n\}\n",
                      self.js, re.S)
        self.assertTrue(b, "找不到 loadBackupInfo")
        self.assertIn("bkCardSum", b.group(1))
        self.assertIn("备份 '", b.group(1))

    def test_有折叠样式(self):
        self.assertIn("details.fold", self.css)
        self.assertIn("fold-ic", self.css)
        self.assertIn("fold-sum", self.css)

    def test_details_标签配平(self):
        # 注释里也有 <details> 字样，只数真正开标签
        self.assertEqual(self.html.count("<details class="),
                         self.html.count("</details>"))
        self.assertEqual(self.html.count("<summary"),
                         self.html.count("</summary>"))


class TestJsdomRun(unittest.TestCase):
    """真跑一遍页面：去重后的数字、筛选结果、折叠行为。"""

    def test_浏览器实跑(self):
        if not os.path.exists(JSRUN):
            self.skipTest("缺少 jsdom 脚本")
        try:
            out = subprocess.run(["node", JSRUN], capture_output=True,
                                 text=True, timeout=180,
                                 cwd=os.path.dirname(JSRUN))
        except (OSError, subprocess.SubprocessError) as e:
            self.skipTest("node 跑不起来：%s" % e)
        if out.returncode != 0:
            bad = [l for l in out.stdout.splitlines() if l.startswith("[FAIL]")]
            if not bad:
                self.skipTest("jsdom 不可用（%s）"
                              % (out.stderr.strip()[:120] or "未知"))
            self.fail("浏览器实跑失败：\n" + "\n".join(bad))
        print("\n    jsdom 实跑：" + (out.stdout.strip().splitlines() or [""])[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
