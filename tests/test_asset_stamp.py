# -*- coding: utf-8 -*-
"""静态资源缓存串必须跟**文件内容**走，不能跟版本号走。

事故复盘（这个坑反复出现）：
    以前写成 /static/app.js?v={{ app_version }}，缓存串跟着版本号走。
    而修复期间版本号是不动的（小更新和一般修复按规则囤着不涨号），于是：
      · 改了 app.js / style.css，URL 一个字没变
      · 浏览器继续用磁盘里缓存的旧文件
      · 但 HTML 是服务端每次现渲染的，永远是新的
    → 表现：页面上看得到新控件，点了却没反应、也不保存。
    （「登录页背景没变」「首轮静默开关点不动」都是这一个根因。）
"""
import io
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import webui                                                    # noqa: E402
import version as _version                                      # noqa: E402

TPL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "src", "templates")


def _js(path, strip_comments=False):
    """读源码，默认剥掉注释——注释里提到的词不该顶住断言。"""
    s = io.open(path, encoding="utf-8").read()
    if strip_comments:
        s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
        s = re.sub(r"(?m)^\s*//.*$", "", s)
    return s


class TestAssetStamp(unittest.TestCase):

    def test_templates_use_content_stamp(self):
        """模板里静态资源的缓存串必须是 asset_stamp，不是 app_version。"""
        for name in ("index.html", "login.html"):
            html = _js(os.path.join(TPL, name))
            self.assertTrue(
                re.search(r'/static/(app\.js|style\.css|sound\.js)\?v=\{\{\s*asset_stamp\s*\}\}', html),
                "%s 的静态资源没用内容戳" % name)
            # 静态资源不许再挂版本号
            bad = re.findall(r'/static/[^"\']+\?v=\{\{\s*app_version\s*\}\}', html)
            self.assertEqual(bad, [], "%s 仍有静态资源跟着版本号走：%s" % (name, bad))

    def test_webui_supplies_stamp(self):
        """两处 render_template 都必须传 asset_stamp。"""
        src = _js(os.path.join(os.path.dirname(webui.__file__), "webui.py"),
                  strip_comments=True)
        self.assertIn("asset_stamp=_asset_stamp()", src, "首页没传 asset_stamp")
        self.assertIn('"asset_stamp": _asset_stamp()', src, "登录页没传 asset_stamp")
        self.assertIn("def _asset_stamp(", src, "缺少 _asset_stamp 实现")

    def test_stamp_changes_without_version_bump(self):
        """文件内容变了戳就变，哪怕版本号一个字没动。"""
        static = webui.app.static_folder
        self.assertTrue(static and os.path.isdir(static), "定位不到 static 目录")
        before = webui._asset_stamp()
        cur_ver = _version.current()
        path = os.path.join(static, "style.css")
        old = io.open(path, encoding="utf-8").read()
        try:
            io.open(path, "a", encoding="utf-8").write("\n/* stamp-probe */\n")
            os.utime(path, (time.time() + 5, time.time() + 5))
            after = webui._asset_stamp()
        finally:
            io.open(path, "w", encoding="utf-8").write(old)
            os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertNotEqual(after, before,
                            "文件内容变了但戳没变 —— 浏览器缓存不会失效")
        self.assertEqual(_version.current(), cur_ver, "测试期间版本号不该变")
        self.assertEqual(webui._asset_stamp(), before, "还原后戳应回到原值")

    def test_stamp_is_stable(self):
        """内容没动时戳必须稳定，否则每次刷新都在重新下载。"""
        self.assertEqual(webui._asset_stamp(), webui._asset_stamp())

    def test_no_empty_stamp(self):
        """渲染出来的缓存串不能是空的（变量没传就会渲染成空）。"""
        from flask import render_template
        with webui.app.test_request_context("/"):
            html = render_template("index.html",
                                   app_version=_version.current(),
                                   asset_stamp=webui._asset_stamp(),
                                   theme="dark")
        self.assertNotIn('?v="', html, "有空的缓存串")
        self.assertTrue(re.search(r'\?v=[0-9a-f]{10}', html), "没渲染出有效戳")


if __name__ == "__main__":
    unittest.main(verbosity=2)
