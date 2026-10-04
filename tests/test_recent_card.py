# -*- coding: utf-8 -*-
"""概况 → 「最近动态」卡片 锁定测试（v1.63.0）

反馈：最近动态应该长成「头像 + UP 主名 + 动作 / X 分钟前推送到 N 个群 / 类型标签」。

── 根因 ──
以前 renderRecent() 拿的是 notes（每轮检测都写、没推送也更新，里面还是
dyn_id / BV 号这种原始值），拼出来是：

    星瞳_Official 1251251813057822761
    OnOiduts工业区

既没有时间、也没有"推给了几个群"，而且**没推送也会显示**。

现在改成读心跳里的 recent —— 机器人**真的发出去**才写一条，
记着 kind / uid / name / 推了几个群 / 时间戳。
"""
import os
import re
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
APPJS = os.path.join(ROOT, "static", "app.js")
CSS = os.path.join(ROOT, "static", "style.css")
BOT = os.path.join(ROOT, "bot.py")


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


class HeartbeatRecent(unittest.TestCase):
    """push_event 的合并 / 上限行为。"""

    def setUp(self):
        import heartbeat
        self.hb = heartbeat
        self._old = heartbeat.PATH
        fd, self.path = tempfile.mkstemp(suffix=".hb")
        os.close(fd)
        os.remove(self.path)
        heartbeat.PATH = self.path
        heartbeat._state["recent"] = []

    def tearDown(self):
        self.hb.PATH = self._old
        self.hb._state["recent"] = []
        try:
            os.remove(self.path)
        except OSError:
            pass

    def _recent(self):
        return (self.hb.read() or {}).get("recent") or []

    def test_01_write_one(self):
        self.hb.push_event("live", 401315430, "伊万酱哒油", 1)
        r = self._recent()
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["kind"], "live")
        self.assertEqual(r[0]["uid"], "401315430")
        self.assertEqual(r[0]["name"], "伊万酱哒油")
        self.assertEqual(r[0]["groups"], 1)
        self.assertTrue(r[0]["ts"] > 0)

    def test_02_merge_multi_group(self):
        """同一次更新推给 3 个群：每个群各调一次，应累加成一条。"""
        for _ in range(3):
            self.hb.push_event("video", 111, "菠萝Buono", 1)
        r = self._recent()
        self.assertEqual(len(r), 1, "同一次更新不能插成多条")
        self.assertEqual(r[0]["groups"], 3)

    def test_03_different_kind_not_merged(self):
        self.hb.push_event("live", 111, "A", 1)
        self.hb.push_event("video", 111, "A", 1)
        r = self._recent()
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["kind"], "video")   # 最新的在前

    def test_04_different_uid_not_merged(self):
        self.hb.push_event("live", 111, "A", 1)
        self.hb.push_event("live", 222, "B", 1)
        self.assertEqual(len(self._recent()), 2)

    def test_05_outside_window_new_entry(self):
        """超过合并窗口（比如隔了很久又开播）→ 另起一条，不要一直累加。"""
        self.hb.push_event("live", 111, "A", 1)
        r = self._recent()
        r[0]["ts"] = time.time() - (self.hb.RECENT_MERGE_SEC + 60)
        self.hb.touch(recent=r)
        self.hb.push_event("live", 111, "A", 1)
        r2 = self._recent()
        self.assertEqual(len(r2), 2)
        self.assertEqual(r2[0]["groups"], 1)

    def test_06_cap(self):
        for i in range(self.hb.RECENT_MAX + 8):
            self.hb.push_event("video", 1000 + i, "UP%d" % i, 1)
        r = self._recent()
        self.assertEqual(len(r), self.hb.RECENT_MAX)

    def test_07_bad_name_no_crash(self):
        """名字带奇怪字符 / uid 是 None 都不能把心跳写崩。"""
        self.hb.push_event("season", None, "x" * 200, 2)
        r = self._recent()
        self.assertEqual(len(r), 1)
        self.assertEqual(len(r[0]["name"]), 40)


class BotWiring(unittest.TestCase):
    """_push 与 _push_season 都要记。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(BOT)

    def test_11_push_records(self):
        self.assertIn("heartbeat.push_event(kind, uid, name, 1)", self.src)

    def test_12_season_records(self):
        """合集走独立的 _push_season，不记的话最近动态里永远没有合集。"""
        seg = self.src.split("async def _push_season", 1)[1].split(
            "async def _check_live", 1)[0]
        self.assertIn('heartbeat.push_event("season", uid, name, 1)', seg)

    def test_13_after_bump(self):
        """必须写在 bump("pushes") 之后 —— 那是"确实发出去了"的唯一标志。"""
        i1 = self.src.index('heartbeat.bump("pushes")')
        i2 = self.src.index("heartbeat.push_event(kind, uid, name, 1)")
        self.assertGreater(i2, i1)


class Frontend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _read(APPJS)
        cls.css = _read(CSS)

    def _fn(self):
        m = re.search(r"function renderRecent\(\) \{.*?\n\}\n", self.js,
                      re.S)
        self.assertTrue(m, "renderRecent 不见了")
        return m.group(0)

    def test_21_reads_heartbeat_recent(self):
        self.assertIn("STATE.heartbeat || {}).recent", self._fn())

    def test_22_no_longer_notes(self):
        """不再拿 notes 拼（没推送也会显示、时间是错的）。"""
        fn = self._fn()
        self.assertNotIn("s.last", fn)
        self.assertNotIn("'dyn'", fn)

    def test_23_kind_table(self):
        for k in ("live", "offline", "video", "dynamic",
                  "dynamic_pinned", "top_comment", "season"):
            self.assertIn(k + ":", self.js.split("var RECENT_KIND")[1][:900])

    def test_24_avatar_and_tag(self):
        fn = self._fn()
        self.assertIn('class="av', fn)
        self.assertIn("season-av", fn)
        self.assertIn("rc-tag", fn)
        self.assertIn("fmtAgo(", fn)

    def test_25_groups_text(self):
        fn = self._fn()
        self.assertIn("推送到 ", fn)
        self.assertIn("个群", fn)

    def test_26_init_avatars(self):
        """不调 initAvatars 的话 data-face 永远变不成背景图。"""
        self.assertIn("initAvatars();", self._fn())

    def test_27_empty_hint(self):
        self.assertIn("下一次成功推送后自动出现", self._fn())

    def test_28_css_tag(self):
        self.assertIn(".tag.rc-tag", self.css)

    def test_29_sorted_by_ts(self):
        self.assertIn("sort(", self._fn())


if __name__ == "__main__":
    unittest.main(verbosity=2)
