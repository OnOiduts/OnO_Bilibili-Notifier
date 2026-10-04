# -*- coding: utf-8 -*-
"""全面自检：只留「重新自检」一个按钮 + 直播状态说人话。

历史背景：曾经给全面自检加过一个「⚡ 立即检测」按钮，会把自检结果
单独开一块卡片显示。来回改了好几版，卡片里的七行跟上面那七行怎么调
都对不齐（连复制 DOM 都不是想要的效果），所以按用户要求**整个移除**。
这里既保留直播状态中文的校验，也锁死"移除后不能再被加回来"。
"""
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ---------- 1. 直播状态：不写 0/1/2，说人话 ----------

def test_live_status_cn_basic():
    from bilibili import live_status_cn
    assert live_status_cn(0) == "未开播"
    assert live_status_cn(1) == "已开播"
    assert live_status_cn(2) == "轮播中"


def test_live_status_cn_str_and_legacy():
    """字符串键、以及历史存下来的 '1（直播中）' 这类写法都要认。"""
    from bilibili import live_status_cn
    assert live_status_cn("1") == "已开播"
    assert live_status_cn("1（直播中）") == "已开播"
    assert live_status_cn("1(已开播)") == "已开播"


def test_live_status_cn_already_cn():
    from bilibili import live_status_cn
    assert live_status_cn("直播中") == "已开播"
    assert live_status_cn("已开播") == "已开播"
    assert live_status_cn("未开播") == "未开播"
    assert live_status_cn("下播") == "未开播"
    assert live_status_cn("轮播中") == "轮播中"


def test_live_status_cn_unknown_keeps():
    """认不出来就原样返回，绝不瞎编一个状态。"""
    from bilibili import live_status_cn
    assert live_status_cn(9) == "9"
    assert live_status_cn("") == ""


def test_probe_bili_no_raw_number():
    """webui 的直播项不能再输出 live_status=1 这种原始数字。"""
    s = _read("webui.py")
    assert "live_status={status}，" not in s
    assert "live_status_cn(live.live_status)" in s


def test_logtranslate_uses_yikaibo():
    """日志翻译要跟面板同口径：1=已开播，不能一处说已开播一处说直播中。"""
    s = _read("logtranslate.py")
    m = re.search(r"LIVE_STATUS\s*=\s*\{(.*?)\}", s, re.S)
    assert m, "找不到 LIVE_STATUS"
    body = m.group(1)
    assert '"0": "未开播"' in body
    assert '"1": "已开播"' in body
    assert '"2": "轮播中"' in body


def test_check_py_no_raw_number():
    s = _read("check.py")
    assert "（1=直播中）" not in s
    assert "live_status_cn(" in s


def test_frontend_live_cn():
    """前端也要兜一层：历史基线里存的仍是数字，显示前必须翻成中文。"""
    s = _read("static", "app.js")
    assert "function liveStatusCn" in s
    assert "'1': '已开播'" in s


# ---------- 2. 全面自检：只有一个按钮，不再开结果卡片 ----------

def test_no_diag_now_action():
    """「⚡ 立即检测」（diag-now）已从全面自检移除，不能再出现。"""
    s = _read("static", "app.js")
    assert "diag-now" not in s, "app.js 里还在处理 diag-now"
    assert "function pushDiagCard" not in s, "开卡片的函数还在"
    assert "DIAG_CARD_MAX" not in s, "卡片上限常量还在"


def test_no_diag_cards_dom():
    html = _read("templates", "index.html")
    assert "diagCards" not in html, "卡片容器还在"
    assert 'data-act="diag-now"' not in html, "立即检测按钮还在"


def test_no_dead_card_style():
    """.dcard-row / .dcard-qq 只服务于那张卡片，一并清掉，别留死样式。"""
    html = _read("templates", "index.html")
    assert ".dcard-row" not in html
    assert ".dcard-qq" not in html


def test_selfcheck_panel_single_button():
    """全面自检区只剩「重新自检」这一个按钮。"""
    html = _read("templates", "index.html")
    i = html.find("🧪 全面自检")
    assert i > 0, "找不到全面自检面板"
    # ⚠️ 得截到**下一个面板**为止，不能拍脑袋取固定长度 ——
    #    固定窗口会越过面板边界，把运行状态的按钮也算进来（误报）。
    j = html.find("<h3>", i)
    seg = html[i:j if j > 0 else i + 1500]
    assert 'data-act="diagnose"' in seg
    assert seg.count("data-act=") == 1, \
        "全面自检里还有别的按钮，应该只剩「重新自检」"


def test_diagnose_renders_rows():
    """「重新自检」仍然是刷上面那七行，不开块、不弹卡片。"""
    s = _read("static", "app.js")
    m = re.search(r"async 'diagnose'\(\)\s*\{(.*?)\n  \},", s, re.S)
    assert m, "找不到 diagnose action"
    body = m.group(1)
    assert "renderDiag(b)" in body, "重新自检没有刷新七行"
    assert "pushDiagCard" not in body, "重新自检不该开结果块"


def test_seven_rows_kept():
    """七项一项都不能少。"""
    s = _read("static", "app.js")
    m = re.search(r"var DIAG_ROWS\s*=\s*\[(.*?)\];", s, re.S)
    assert m
    assert m.group(1).count("[") >= 7


def test_diag_row_style_unchanged():
    """七行的样式保持原样：右边只显示 通过/异常/未检测，
       详细参数在 data-tip 里鼠标移上去才看，值右对齐。"""
    s = _read("static", "app.js")
    assert "function diagRowHtml" in s
    assert "class=\"diag-row\"" in s
    assert "data-tip=" in s
    html = _read("templates", "index.html")
    m = re.search(r"\.diag-row \.dv \{(.*?)\}", html, re.S)
    assert m and "text-align:right" in m.group(1), "值没有右对齐"


def test_run_page_keeps_check_now():
    """运行状态页的「立即检测」仍然是巡查所有订阅（check-now）。"""
    s = _read("templates", "index.html")
    i = s.find('id="dg-run"')
    assert i > 0, "找不到运行状态面板"
    assert 'data-act="check-now"' in s[i:i + 2000]
