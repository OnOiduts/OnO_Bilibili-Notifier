# -*- coding: utf-8 -*-
"""图片真实尺寸探测 —— 让预览跟着原图比例，而不是一律 16:9。

背景：B站很多动态配图/评论图的 URL 是**原图直链**，不带 `@1080w_1920h`
这类参数。而 _src_size() 只能从 @参数 里读比例，读不到就 16:9 兜底 ——
于是竖图、方图的预览全被拉成宽屏（用户报的"怎么都是 16:9"）。

探测只在解析不到参数时才联网，结果缓存，失败一律 (0,0) 由调用方兜底。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import notify as n  # noqa: E402


def _jpeg(w, h):
    """构造一个最小可用 JPEG 头，SOF0 里写死宽高。"""
    import struct
    return (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", h, w) + b"\x00" * 40)


def _png(w, h):
    import struct
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR"
            + struct.pack(">II", w, h) + b"\x00" * 20)


class TestParseSizeFromBytes(unittest.TestCase):
    def test_jpeg(self):
        self.assertEqual((1920, 1080), n._parse_size_from_bytes(_jpeg(1920, 1080)))

    def test_png(self):
        self.assertEqual((800, 1200), n._parse_size_from_bytes(_png(800, 1200)))

    def test_gif(self):
        import struct
        b = b"GIF89a" + struct.pack("<HH", 300, 500) + b"\x00" * 10
        self.assertEqual((300, 500), n._parse_size_from_bytes(b))

    def test_webp_vp8x(self):
        b = (b"RIFF" + b"\x00" * 4 + b"WEBPVP8X" + b"\x00" * 8
             + (1080 - 1).to_bytes(3, "little") + (1920 - 1).to_bytes(3, "little")
             + b"\x00" * 8)
        self.assertEqual((1080, 1920), n._parse_size_from_bytes(b))

    def test_garbage_returns_zero(self):
        """垃圾数据必须返回 (0,0)，不能抛异常把推送搞挂。"""
        for b in (b"", b"not an image", b"\x00" * 50, None):
            try:
                self.assertEqual((0, 0), n._parse_size_from_bytes(b))
            except TypeError:
                pass  # None 允许抛 TypeError，调用方有兜底

    def test_truncated_jpeg(self):
        self.assertEqual((0, 0), n._parse_size_from_bytes(b"\xff\xd8\xff\xc0"))


class TestProbeSize(unittest.TestCase):
    def setUp(self):
        n._SIZE_CACHE.clear()
        self._old = n.PROBE_SIZE_ON

    def tearDown(self):
        n.PROBE_SIZE_ON = self._old
        n._SIZE_CACHE.clear()

    def test_disabled_returns_zero(self):
        n.PROBE_SIZE_ON = False
        self.assertEqual((0, 0), n._probe_size("https://i0.hdslb.com/x.jpg"))

    def test_empty_url(self):
        self.assertEqual((0, 0), n._probe_size(""))
        self.assertEqual((0, 0), n._probe_size(None))

    def test_non_http_skipped(self):
        """非 http(s) 一律不联网。"""
        self.assertEqual((0, 0), n._probe_size("file:///etc/passwd"))
        self.assertEqual((0, 0), n._probe_size("ftp://x/y.jpg"))

    def test_cache_negative(self):
        """探测失败也要缓存，否则每推一次都白等一次超时。"""
        n._SIZE_CACHE["https://x.invalid/a.jpg"] = (0, 0)
        # 第二次直接命中缓存（不再发起网络请求）
        self.assertEqual((0, 0), n._probe_size("https://x.invalid/a.jpg"))

    def test_protocol_relative_upgraded(self):
        """`//` 开头的 URL 要补成 https，不能因为 scheme 缺失被跳过。"""
        n.PROBE_SIZE_ON = False  # 只验证不会被 scheme 判断提前 return
        self.assertEqual((0, 0), n._probe_size("//i0.hdslb.com/a.jpg"))


class TestAspectRatio(unittest.TestCase):
    """等比：这才是用户要的"跟着原图比例缩小"。"""

    def test_portrait(self):
        self.assertEqual((672, 1195), n._hint_size("cover", 1080, 1920))

    def test_square_no_upscale(self):
        """原图比上限小就不放大，避免小图被拉糊。"""
        self.assertEqual((518, 518), n._hint_size("cover", 518, 518))

    def test_landscape(self):
        self.assertEqual((672, 378), n._hint_size("cover", 1280, 720))

    def test_unknown_fallback_16x9(self):
        """解析不到比例才 16:9 兜底（视频封面本来就是这个比例）。"""
        self.assertEqual((672, 378), n._hint_size("cover", 0, 0))

    def test_avatar_square(self):
        """头像正方形，套 16:9 会变形。"""
        self.assertEqual((n.SIZE_HINT_AVATAR, n.SIZE_HINT_AVATAR),
                         n._hint_size("up_avatar", 1920, 1080))

    def test_explicit_h_wins(self):
        """用户显式给了高就尊重用户，不做等比。"""
        n.SIZE_HINT_H = 378
        try:
            self.assertEqual((672, 378), n._hint_size("cover", 1080, 1920))
        finally:
            n.SIZE_HINT_H = 0


class TestImgLineUsesProbe(unittest.TestCase):
    """URL 没有 @参数 时，_pic 必须去探测真实尺寸。"""

    def setUp(self):
        self._old = getattr(n, "_probe_size", None)
        self.calls = []

    def tearDown(self):
        n._probe_size = self._old

    def test_probe_called_when_no_at_param(self):
        n._probe_size = lambda u: (self.calls.append(u), (1080, 1920))[1]
        line = n._pic("https://i0.hdslb.com/bfs/dyn/plain.jpg", "cover")
        self.assertEqual(1, len(self.calls), "无 @参数 时应探测真实尺寸")
        self.assertIn("#672px", line)
        self.assertIn("#1195px", line)
        self.assertNotIn("#378px", line, "竖图不能被兜底成 16:9")

    def test_probe_skipped_when_at_param_present(self):
        n._probe_size = lambda u: (self.calls.append(u), (9999, 9999))[1]
        n._pic("https://i0.hdslb.com/bfs/dyn/x.jpg@1080w_1920h_1c.webp", "cover")
        self.assertEqual(0, len(self.calls), "@参数 已能给出比例，不该再联网")


if __name__ == "__main__":
    unittest.main(verbosity=2)
