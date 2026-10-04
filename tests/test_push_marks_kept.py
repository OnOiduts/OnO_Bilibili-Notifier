"""v1.83.0：推送**卡片**里必须还看得见表情方括号和话题标记。

背景（用户反馈的真实链路）：
    原动态 https://www.bilibili.com/opus/1242997444260659222
    带话题「小星星的家」、收藏集表情、装扮表情。
    bilibili 那边早已把正文组装成
        #Tag:小星星的家 互动抽奖…[星回纪·时空轮转是我的翅膀]
    但 notify._esc 的正则 `[`*_#\\[\\]<>]` 把 # 和 [ ] **一起删掉**，
    卡片里又变回「Tag:小星星的家…星回纪·时空轮转是我的翅膀」——
    于是"改了还是没区分"。改了组装没用，最后一环必须放行。
"""

import os
import re
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

import bilibili as B          # noqa: E402
import notify as N            # noqa: E402

WEB = os.path.join(ROOT, "webui.py")
BOT = os.path.join(ROOT, "bot.py")


def _opus_item(nodes, desc=None):
    """复刻 opus 动态的真实结构（节点在 major.opus.summary 里）。"""
    return {
        "basic": {"rid_str": "1242997444260659222", "comment_type": 11,
                  "comment_id_str": "1242997444260659222"},
        "modules": {
            "module_author": {"pub_ts": 1780000000,
                              "name": "星瞳_Official", "mid": 401315430},
            "module_dynamic": {
                "desc": desc,
                "major": {"type": "MAJOR_TYPE_OPUS",
                          "opus": {"summary": {"text": "互动抽奖",
                                               "rich_text_nodes": nodes}}},
            },
        },
    }


NODES = [
    {"type": "RICH_TEXT_NODE_TYPE_TOPIC", "text": "小星星的家"},
    {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "互动抽奖 太阳照~花儿笑！"},
    {"type": "RICH_TEXT_NODE_TYPE_EMOJI", "text": "星回纪·时空轮转是我的翅膀",
     "emoji": {"icon_url": "https://i0.hdslb.com/x.png", "type": 3}},
    {"type": "RICH_TEXT_NODE_TYPE_EMOJI", "text": "星瞳粉丝专属",
     "emoji": {"icon_url": "https://i0.hdslb.com/y.png", "type": 2}},
]


class TestEscKeepsMarks(unittest.TestCase):
    """① 转义不能把区分标记削平。"""

    def test_01_esc_keeps_brackets(self):
        out = N._esc("今天[OnO_气死我了]")
        # 下划线转义成 \_（渲染时不显示反斜杠），方括号保留
        self.assertEqual(out, "今天[OnO\\_气死我了]")
        self.assertIn("[", out)
        self.assertIn("]", out)

    def test_02_esc_keeps_topic(self):
        self.assertEqual(N._esc("#Tag:小星星的家"), "#Tag:小星星的家")

    def test_03_esc_still_drops_noise(self):
        """成串的加粗/代码块/HTML 标记照样删。"""
        self.assertEqual(N._esc("**粗** `code` <b>"),
                         "粗 code b")

    def test_03b_esc_keeps_single_underscore(self):
        """单个下划线不能删，但必须转义 —— 留着不转义会变斜体。"""
        out1 = N._esc("今天[OnO_气死我了]")
        self.assertIn("\\_", out1, "下划线被删了，表情名会改坏")
        self.assertNotEqual(out1, "今天[OnO气死我了]")
        out2 = N._esc("[A_B_C]")
        self.assertIn("\\_", out2)
        self.assertNotEqual(out2, "[ABC]")

    def test_04_esc_breaks_link(self):
        """正文自带的 [文字](链接) 不能被渲染成可点链接。"""
        self.assertNotIn("](", N._esc("看这里[点我](http://x.com)"))

    def test_05_esc_regex_no_bracket_hash(self):
        """正则里不许再把 [ ] # 列进删除集合。"""
        src = re.search(r"def _esc\([\s\S]{0,2500}?return s\.strip\(\)",
                        open(os.path.join(ROOT, "notify.py"),
                             encoding="utf-8").read())
        body = src.group(0)
        m = re.search(r're\.sub\(r"\[(.*?)\]"', body)
        self.assertTrue(m, "没找到转义正则")
        for ch in ("#", r"\[", r"\]"):
            self.assertNotIn(ch, m.group(1),
                             "转义又把 %s 删了，表情/话题标记会丢" % ch)


class TestEscNoItalic(unittest.TestCase):
    """③ 推送正文不该有斜体：单个 _ / * 必须转义，不能留着触发强调。"""

    def _no_bare(self, out, ch, esc):
        """把转义过的去掉后，不许再出现裸标记（否则会渲染成斜体/粗体）。"""
        self.assertNotIn(ch, out.replace(esc, ""),
                         "还有没转义的 %s，会被渲染成斜体" % ch)

    def test_10_no_bare_underscore(self):
        self._no_bare(N._esc("[A_B_C]"), "_", "\\_")

    def test_11_no_bare_star(self):
        self._no_bare(N._esc("*斜体*"), "*", "\\*")

    def test_12_pair_underscore_escaped(self):
        self.assertIn("\\_真倒霉\\_", N._esc("今天_真倒霉_啊"))

    def test_13_pair_star_escaped(self):
        self.assertIn("\\*斜体\\*", N._esc("这是*斜体*哦"))

    def test_14_emote_name_intact(self):
        """转义后表情名里的下划线还在（渲染时反斜杠不显示，看到的是原名）。"""
        out = N._esc("今天[OnO_气死我了]")
        self.assertEqual(out.replace("\\", ""), "今天[OnO_气死我了]")


class TestCardKeepsMarks(unittest.TestCase):
    """② 端到端：渲染出来的卡片真的还带标记。"""

    def setUp(self):
        self.d = B._parse_polymer(_opus_item(NODES))

    def test_06_body_has_topic(self):
        self.assertIn("#Tag:小星星的家", self.d.text)

    def test_07_body_has_emote(self):
        self.assertIn("[星回纪·时空轮转是我的翅膀]", self.d.text)

    def test_08_body_no_dress(self):
        self.assertNotIn("星瞳粉丝专属", self.d.text)

    def test_09_md_keeps_marks(self):
        """这一条就是用户看到的那张卡片。"""
        md, _ = N.render_dynamic(self.d, "星瞳_Official", {})
        self.assertIn("[星回纪·时空轮转是我的翅膀]", md)
        self.assertIn("#Tag:小星星的家", md)
        self.assertNotIn("星瞳粉丝专属", md)

    def test_10_plain_keeps_marks(self):
        """markdown 失败退回纯文本，标记也不能丢。"""
        md, _ = N.render_dynamic(self.d, "星瞳_Official", {})
        plain = N.to_plain(md)
        self.assertIn("[星回纪·时空轮转是我的翅膀]", plain)
        self.assertIn("#Tag:小星星的家", plain)

    def test_11_topic_not_heading(self):
        """#Tag:xxx 的 # 后没有空格，且正文每行带 > 前缀，不会变标题。"""
        md, _ = N.render_dynamic(self.d, "星瞳_Official", {})
        for ln in md.splitlines():
            if "Tag:" in ln:
                self.assertTrue(ln.lstrip().startswith(">"),
                                "话题行必须在引用块里：" + ln)
                self.assertFalse(re.match(r"^>\s*#\s", ln))

    def test_12_comment_keeps_marks(self):
        """置顶评论同样走这条渲染链，标记也要保住。"""
        c = {"content": {
            "message": "小星星的家互动抽奖 打卡星回纪·时空轮转是我的翅膀",
            "rich_text_nodes": NODES,
            "emote": {"星回纪·时空轮转是我的翅膀":
                      {"icon_url": "https://i0.hdslb.com/x.png", "type": 3}},
        }}
        # bilibili 那侧返回的 content 已是组装好的字符串（跟动态同一条路）
        content = B._body_text(c["content"]["message"], NODES,
                               B._collect_emotes(c))
        md, _ = N.render_top_comment({"content": content},
                                     "星瞳_Official", {})
        self.assertIn("[星回纪·时空轮转是我的翅膀]", md)
        self.assertIn("#Tag:小星星的家", md)


class TestPushTestSharesPath(unittest.TestCase):
    """③ 推送测试与正式推送走同一条取数路径。"""

    def test_13_webui_and_bot_same_method(self):
        web = open(WEB, encoding="utf-8").read()
        bot = open(BOT, encoding="utf-8").read()
        self.assertIn("get_latest_dynamic", web)
        self.assertIn("get_latest_dynamic", bot)
        self.assertIn("get_pinned_comment", web)
        self.assertIn("get_pinned_comment", bot)

    def test_14_no_side_render(self):
        """推送测试不许另起一套渲染（必须调 notify.render）。"""
        web = open(WEB, encoding="utf-8").read()
        self.assertIn("render(", web)


class TestPlainFallback(unittest.TestCase):
    """④ 没给节点时的纯文本兜底。"""

    def test_15_plain_topic(self):
        t = B._body_text("今天#小星星的家# 打卡", None, [])
        self.assertIn("#Tag:小星星的家", t)

    def test_16_plain_no_double_space(self):
        t = B._body_text("打卡 太阳照 星瞳粉丝专属", None,
                         ["[星瞳粉丝专属]"])
        self.assertNotIn("  ", t)
        self.assertNotIn("星瞳粉丝专属", t)


if __name__ == "__main__":
    unittest.main(verbosity=2)
