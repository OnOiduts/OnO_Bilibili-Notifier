# -*- coding: utf-8 -*-
"""app.js 必须能被 JS 引擎完整解析。

事故复盘：一次批量改动后 src/static/app.js **文件尾部多出一串重复碎片**
（同一个 `})();` 尾巴被追加了四次），`node --check` 直接报
`SyntaxError: Unexpected token '}'`。

后果是**整份面板脚本一行都跑不起来** —— 不是某个按钮失灵，而是：
  · 所有开关点了没反应、不自动保存
  · 群列表 / 状态 / 日志全部画不出来
  · 控制台未必有红字，页面看着"像是加载完了"

这类问题靠肉眼看代码几乎发现不了（碎片在 6000 行之后，没人会翻到那），
而且**代码逻辑本身一点没错**，所以针对功能的测试全绿。必须用"整份文件
能不能被解析"来兜住。
"""
import os
import shutil
import subprocess
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
STATIC = os.path.join(ROOT, "static")


def _node():
    return shutil.which("node") or shutil.which("nodejs")


class TestAppJsSyntax(unittest.TestCase):
    def test_appjs_parses(self):
        """app.js 必须能被解析 —— 这份一挂，整个面板的脚本全废。"""
        exe = _node()
        if not exe:
            self.skipTest("环境里没有 node，跳过语法检查")
        p = os.path.join(STATIC, "app.js")
        self.assertTrue(os.path.exists(p), "找不到 app.js")
        r = subprocess.run([exe, "--check", p],
                           capture_output=True, text=True)
        self.assertEqual(
            0, r.returncode,
            "app.js 解析失败，整个面板的 JS 都不会执行：\n" +
            (r.stderr or "")[-800:])

    def test_all_static_js_parse(self):
        """static 下每个 js 都要能解析（sound.js 挂了同样会拖死页面）。"""
        exe = _node()
        if not exe:
            self.skipTest("环境里没有 node，跳过语法检查")
        if not os.path.isdir(STATIC):
            self.skipTest("没有 static 目录")
        bad = []
        for fn in sorted(os.listdir(STATIC)):
            if not fn.endswith(".js"):
                continue
            r = subprocess.run([exe, "--check", os.path.join(STATIC, fn)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(fn)
        self.assertEqual([], bad, "这些脚本解析失败：" + ", ".join(bad))

    def test_appjs_tail_not_duplicated(self):
        """文件结尾不能出现重复碎片（本次事故的直接形态）。

        判据：最后一行必须是收尾的 `})();`，且全文里这个结尾串
        只出现在末尾一次。
        """
        p = os.path.join(STATIC, "app.js")
        if not os.path.exists(p):
            self.skipTest("没有 app.js")
        s = open(p, encoding="utf-8").read()
        tail = s.rstrip()
        self.assertTrue(tail.endswith("})();"),
                        "app.js 结尾不是 `})();`，尾部可能被重复追加了碎片")
        self.assertEqual(
            0, tail.count("})();\n})();"),
            "结尾出现连续的 `})();`，八成是重复追加产生的垃圾")
        # 事故形态是"半个语句的碎片"，这种东西一出现必然没被解析
        self.assertNotIn(
            "ventListener('input', sync);",
            s.replace("p.addEventListener('input', sync);", ""),
            "文件里出现了被截断的语句碎片（本次事故的直接形态）")
