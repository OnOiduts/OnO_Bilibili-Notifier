# -*- coding: utf-8 -*-
"""订阅迁移：全体迁移 / 选择迁移。

反馈：「订阅迁移加个全体迁移和选择迁移」。

以前只有一种粒度：整群照搬（源群 → 目标群，源群清空）。
只想搬某位 UP 主、或只想搬"直播"不想搬"投稿"时只能整群搬完再手动删，
而「迁移」是清空源群的，等于先丢一次再重建。

现在：
  · 全体迁移 = 老行为（整群搬，move 时清空源群）
  · 选择迁移 = 只搬勾中的「UP 主 × 提醒类型」

⚠️ 选择模式下 move **绝不清空源群**：只删除真正搬走的那几个 (uid, kind)，
没勾选的必须原样留着 —— 否则就是不可逆的数据丢失，
而且界面上完全看不出自己丢了什么。这条是这个测试的重点。
"""

import io
import os
import re
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from db import Store  # noqa: E402

APPJS = os.path.join(ROOT, "static", "app.js")
HTML = os.path.join(ROOT, "templates", "index.html")
WEBUI = os.path.join(ROOT, "webui.py")


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def _store(tmp, name="t.db"):
    return Store(os.path.join(tmp, name))


def _seed(st):
    """源群 A：两位 UP 主，各开了两类提醒；目标群 B 空。"""
    st.set_sub("A", 111, "live", True, uname="甲")
    st.set_sub("A", 111, "video", True, uname="甲")
    st.set_sub("A", 111, "dynamic", False, uname="甲")
    st.set_sub("A", 222, "live", True, uname="乙")
    st.set_sub("A", 222, "video", True, uname="乙")
    st.set_group_name("A", "源群")
    st.set_group_name("B", "目标群")


class TestPickCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mig_")
        self.st = _store(self.tmp)
        _seed(self.st)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_copy_keeps_src(self):
        r = self.st.copy_subs("A", "B", move=False)
        self.assertTrue(r["ok"], r.get("msg"))
        self.assertEqual(r["ups"], 2)
        # 目标群拿到两位 UP 主（4 个"开着的"类：111 两个、222 两个）
        self.assertEqual(sorted(self.st.ups_of_group("B").keys()), ["111", "222"])
        # 复制：源群原样保留
        self.assertEqual(sorted(self.st.ups_of_group("A").keys()), ["111", "222"])

    def test_all_move_clears_src(self):
        r = self.st.copy_subs("A", "B", move=True)
        self.assertTrue(r["ok"], r.get("msg"))
        self.assertEqual(sorted(self.st.ups_of_group("B").keys()), ["111", "222"])
        self.assertEqual(self.st.ups_of_group("A"), {})   # 全体迁移 → 清空

    def test_part_move_only_removes_picked(self):
        """选择迁移 + move：源群只少掉搬走的那一项，其余必须还在。"""
        r = self.st.copy_subs("A", "B", move=True,
                              picks={111: {"live"}})
        self.assertTrue(r["ok"], r.get("msg"))
        self.assertEqual(r["ups"], 1)
        b = self.st.ups_of_group("B")
        self.assertEqual(list(b.keys()), ["111"])
        self.assertTrue(b["111"]["live"]["on"])
        # 111 的 video 没勾 → 目标群不该凭空多出一条
        self.assertEqual(b["111"].get("video", {}).get("on"), False)
        # 源群：111 的 live 没了，别的都还在
        a = self.st.ups_of_group("A")
        self.assertEqual(a["111"].get("live", {}).get("on"), False)
        self.assertTrue(a["111"]["video"]["on"])      # 没勾 → 留着
        self.assertTrue(a["222"]["live"]["on"])       # 没勾 → 留着
        self.assertTrue(a["222"]["video"]["on"])

    def test_part_copy_leaves_src_intact(self):
        self.st.copy_subs("A", "B", move=False, picks={111: {"live", "video"}})
        a = self.st.ups_of_group("A")
        self.assertTrue(a["111"]["live"]["on"])
        self.assertTrue(a["222"]["video"]["on"])
        b = self.st.ups_of_group("B")
        self.assertEqual(list(b.keys()), ["111"])

    def test_empty_picks_rejected(self):
        r = self.st.copy_subs("A", "B", move=True, picks={})
        self.assertFalse(r["ok"])
        self.assertIn("勾选", r.get("msg") or "")

    def test_picks_not_in_src_rejected(self):
        """勾了源群里没有的 UP → 明确说没有可搬的，别回"已复制 0 位"。"""
        r = self.st.copy_subs("A", "B", move=True, picks={999: {"live"}})
        self.assertFalse(r["ok"])
        self.assertIn("没有", r.get("msg") or "")

    def test_bad_kind_ignored(self):
        r = self.st.copy_subs("A", "B", move=True,
                              picks={111: {"live", "not_a_kind"}})
        self.assertTrue(r["ok"], r.get("msg"))
        self.assertTrue(self.st.ups_of_group("B")["111"]["live"]["on"])

    def test_templates_follow_pick(self):
        self.st.set_up_templates("A", 111, {"live": "甲直播文案"})
        self.st.set_up_templates("A", 222, {"live": "乙直播文案"})
        self.st.copy_subs("A", "B", move=True, picks={111: {"live"}})
        self.assertEqual(self.st.get_up_templates("B", 111).get("live"),
                         "甲直播文案")
        # 没勾的 UP 的文案必须留在源群
        self.assertEqual(self.st.get_up_templates("A", 222).get("live"),
                         "乙直播文案")
        self.assertEqual(self.st.get_up_templates("B", 222), {})

    def test_seasons_follow_pick(self):
        self.st.add_season("A", 111, 5001, title="甲的合集", uname="甲")
        self.st.add_season("A", 222, 5002, title="乙的合集", uname="乙")
        r = self.st.copy_subs("A", "B", move=True, picks={111: {"live"}})
        self.assertTrue(r["ok"], r.get("msg"))
        b = [s["season_id"] for s in self.st.seasons_of_group("B")]
        a = [s["season_id"] for s in self.st.seasons_of_group("A")]
        self.assertEqual(b, [5001])          # 只搬勾中的 UP 的合集
        self.assertEqual(a, [5002])          # 没勾的留在源群

    def test_seasons_can_be_skipped(self):
        self.st.add_season("A", 111, 5001, title="甲的合集", uname="甲")
        self.st.copy_subs("A", "B", move=False, picks={111: {"live"}},
                          with_seasons=False)
        self.assertEqual(self.st.seasons_of_group("B"), [])
        self.assertEqual(len(self.st.seasons_of_group("A")), 1)

    def test_copy_never_touches_src_when_part(self):
        """选择 + copy：源群一个字节都不能少。"""
        self.st.add_season("A", 222, 5002, title="乙的合集", uname="乙")
        self.st.set_up_templates("A", 222, {"video": "乙投稿文案"})
        self.st.copy_subs("A", "B", move=False, picks={111: {"live"}})
        a = self.st.ups_of_group("A")
        self.assertEqual(sorted(a.keys()), ["111", "222"])
        self.assertTrue(a["111"]["video"]["on"])
        self.assertEqual(len(self.st.seasons_of_group("A")), 1)
        self.assertEqual(self.st.get_up_templates("A", 222).get("video"),
                         "乙投稿文案")


class TestSeasonPicks(unittest.TestCase):
    """合集单独挑：选择迁移里想"只搬某几个合集"（甚至一个 UP 都不搬）。

    反馈：「应该搞个 UP 与合集分类迁移」。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="migsea_")
        self.st = _store(self.tmp)
        self.st.set_sub("A", 111, "live", True, uname="甲")
        self.st.add_season("A", 111, 5001, title="甲的合集一", uname="甲")
        self.st.add_season("A", 111, 5002, title="甲的合集二", uname="甲")
        self.st.add_season("A", 222, 5003, title="乙的合集", uname="乙")
        self.st.set_group_name("B", "目标群")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_only_picked_seasons_moved(self):
        r = self.st.copy_subs("A", "B", move=False, picks={},
                              season_picks={5002})
        self.assertTrue(r["ok"], r.get("msg"))
        got = [s["season_id"] for s in self.st.seasons_of_group("B")]
        self.assertEqual(got, [5002])
        # UP 主一个都没勾 → 目标群不该凭空多出订阅
        self.assertEqual(self.st.ups_of_group("B"), {})

    def test_season_picks_bypasses_uid_filter(self):
        """勾了的合集属于**没勾的** UP 主时，也必须搬得走。"""
        r = self.st.copy_subs("A", "B", move=False, picks={111: {"live"}},
                              season_picks={5003})
        self.assertTrue(r["ok"], r.get("msg"))
        got = sorted(s["season_id"] for s in self.st.seasons_of_group("B"))
        self.assertEqual(got, [5003])

    def test_empty_season_picks_moves_none(self):
        r = self.st.copy_subs("A", "B", move=False, picks={111: {"live"}},
                              season_picks=set())
        self.assertTrue(r["ok"], r.get("msg"))
        self.assertEqual(self.st.seasons_of_group("B"), [])
        self.assertEqual(len(self.st.seasons_of_group("A")), 3)

    def test_move_removes_only_picked_seasons(self):
        r = self.st.copy_subs("A", "B", move=True, picks={},
                              season_picks={5001})
        self.assertTrue(r["ok"], r.get("msg"))
        left = sorted(s["season_id"] for s in self.st.seasons_of_group("A"))
        self.assertEqual(left, [5002, 5003])       # 没勾的原样留着
        self.assertEqual([s["season_id"]
                          for s in self.st.seasons_of_group("B")], [5001])

    def test_nothing_picked_rejected(self):
        r = self.st.copy_subs("A", "B", move=True, picks={}, season_picks=set())
        self.assertFalse(r["ok"])
        self.assertIn("勾选", r.get("msg") or "")

    def test_season_picks_not_in_src(self):
        r = self.st.copy_subs("A", "B", move=True, picks={},
                              season_picks={9999})
        self.assertFalse(r["ok"])
        self.assertIn("没有", r.get("msg") or "")

    def test_no_ups_but_seasons_only(self):
        """源群一个 UP 都没订、只订了合集 —— 不该被"没有任何订阅"拦下。"""
        st = _store(self.tmp, "only.db")
        st.add_season("A", 111, 7001, title="甲合集", uname="甲")
        r = st.copy_subs("A", "B", move=False, picks={}, season_picks={7001})
        self.assertTrue(r["ok"], r.get("msg"))
        self.assertEqual([s["season_id"] for s in st.seasons_of_group("B")],
                         [7001])


class TestApiWiring(unittest.TestCase):
    """后端接口与前端接线：少了哪一段，用户界面上就是"点了没反应"。"""

    def test_webui_parses_scope_and_picks(self):
        src = _read(WEBUI)
        self.assertIn('scope = str(d.get("scope")', src)
        self.assertIn('scope == "part"', src)
        self.assertIn('picks=picks', src)
        self.assertIn('with_seasons=with_seasons', src)
        # 没勾选要在后端也拦一次（前端拦了不等于后端安全）
        self.assertIn("还没勾选任何要搬的内容", src)

    def test_db_accepts_picks(self):
        src = _read(os.path.join(ROOT, "db.py"))
        self.assertIn("def copy_subs(self, src_gid: str, dst_gid: str, "
                      "move: bool = False,\n                  picks=None", src)
        self.assertIn("def copy_seasons(self, src_gid: str, dst_gid: str, "
                      "move: bool = False,\n                     uids=None", src)
        self.assertIn("def copy_up_templates(self, src_gid: str, dst_gid: str,\n"
                      "                          move: bool = False, uids=None)",
                      src)

    def test_html_has_scope_switch(self):
        h = _read(HTML)
        for kw in ('data-act="mig-scope" data-scope="all"',
                   'data-act="mig-scope" data-scope="part"',
                   'id="migPick"', 'id="migPickList"',
                   'data-act="mig-pick"', 'data-act="mig-seasons"'):
            self.assertIn(kw, h, kw)

    def test_js_has_pick_logic(self):
        js = _read(APPJS)
        for kw in ("function renderMigPick", "function collectMigPicks",
                   "function migExtra", "'mig-chip'", "'mig-scope'",
                   "scope: 'part'", "seasons: MIG_SEASONS"):
            self.assertIn(kw, js, kw)

    def test_html_has_up_season_tabs(self):
        """迁移清单要分「UP 主 / 合集」两栏，跟群卡片里那套 mini-tab 一样。"""
        h = _read(HTML)
        for kw in ('data-mv="mv-pick-up"', 'data-mv="mv-pick-season"',
                   'id="migSeaList"', 'id="mv-pick-up"',
                   'id="mv-pick-season"'):
            self.assertIn(kw, h, kw)

    def test_js_has_card_model(self):
        """外层大卡片 = 选中；小卡片 = 提醒类型；未选中时小卡片点不动。"""
        js = _read(APPJS)
        for kw in ("'mig-card'(el)", "'mig-sea-card'(el)",
                   "function renderMigSea", "function collectMigSeasons",
                   "function migActiveTab",
                   "season_picks: sp", "class=\"mig-card",
                   "先在卡片上点一下选中它"):
            self.assertIn(kw, js, kw)

    def test_css_has_selected_state(self):
        css = _read(os.path.join(ROOT, "static", "style.css"))
        self.assertIn(".mig-card:not(.on)", css)   # 未选中 = 灰
        self.assertIn(".mig-kinds", css)

    def test_webui_parses_season_picks(self):
        src = _read(WEBUI)
        self.assertIn("season_picks", src)
        self.assertIn("season_picks=season_picks", src)

    def test_clear_btn_has_class(self):
        """「全部清空」以前是光秃秃的 <button>（没有 .btn 就是浏览器默认样式）。"""
        js = _read(APPJS)
        self.assertIn('<button class="btn dg" data-act="clear-stpl"', js)
        self.assertIn('<button class="btn dg" data-act="clear-utpl"', js)
        # 不能还留着没 class 的裸 button（id 那个带 inline style 的除外）
        for m in re.finditer(r"<button(?!.*class=)(?!.*style=)[^>]*>", js):
            self.fail("裸 button 没有样式类：" + m.group(0))

    def test_move_confirm_differs_by_scope(self):
        """全体迁移的二次确认必须说"清空源群"；选择迁移不能这么说。"""
        js = _read(APPJS)
        self.assertIn("迁移后源群会被清空", js)
        self.assertIn("源群只会移除这几项", js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
