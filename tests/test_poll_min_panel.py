#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「最快档」面板可改 —— 真值测试（脚本式，不依赖 pytest）。

背景：面板只有一个「检查间隔」(poll_interval / 基线)，而 bot.py 自动加速
的下限是另一个键 poll_interval_min（默认 8）。用户填 12 仍会稳下来后掉到
8s，看着像"设定没生效"。本测试把「面板 → 保存 → 落盘 → 回读 → bot 读取」
整条链用真实 HTTP + 真实 yaml 落盘验一遍，不靠"源码里有这段字"。

运行：PYTHONPATH=<deps>:<src> python3 tests/test_poll_min_panel.py
退出码 0 = 全过；非 0 = 有用例失败（逐条打印 PASS/FAIL，供反向验证解析）。
"""
import os
import re
import sys
import tempfile
import traceback

# 唯一真源：按 __file__ 相对推导，不写死任何绝对路径（收敛第 1 步约定）
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
# bot.py 顶部 import botpy（公共源没有）；热更新真值测试要真的导入它，
# 用项目自带的桩顶上。放最后，环境里真装了 botpy 就优先用真的。
_STUBS = os.path.join(HERE, "_stubs")
if os.path.isdir(_STUBS) and _STUBS not in sys.path:
    sys.path.append(_STUBS)

# webui 在 import 期会读数据目录；先给一个干净临时目录，避免污染真机配置
os.environ.setdefault("BILI_NOTIFY_HOME",
                      tempfile.mkdtemp(prefix="onobn_pollmin_"))

import cfgutil  # noqa: E402
import webui  # noqa: E402


# ---------------------------------------------------------------- 工具

def _read(rel):
    with open(os.path.join(SRC, rel), encoding="utf-8") as f:
        return f.read()


def _fresh():
    """每个用例一个全新的数据目录 + 全新 Flask client。"""
    home = tempfile.mkdtemp(prefix="onobn_pollmin_case_")
    os.environ["BILI_NOTIFY_HOME"] = home
    os.environ["BILI_NOTIFY_POINTER"] = os.path.join(home, "pointer")
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


def _post(client, **kw):
    # guard() 要求写操作带这个头，否则 403（模拟真实前端）
    return client.post("/api/config", json=kw,
                       headers={"X-Requested-With": "bili-notify"})


def _disk():
    """直接读落盘配置 —— 真值，不是"源码里有没有这段字"。"""
    return cfgutil.load_cfg()


# ---------------------------------------------------------------- 结构层

def test_html_has_min_input():
    h = _read("templates/index.html")
    assert 'id="cPollMin"' in h, "设置页没有「最快档」输入框"
    assert re.search(r'<label>最快档（秒）</label>', h), "标签必须叫「最快档」"
    assert 'id="pollMinHint"' in h, "最快档要有自己的说明文案"


def test_js_registers_field():
    a = _read("static/app.js")
    m = re.search(r"const CFG_FIELDS = \[(.*?)\];", a, re.S)
    assert m and "'cPollMin'" in m.group(1), \
        "cPollMin 没登记 CFG_FIELDS，loadState() 会把正在填的值冲掉"


def test_js_fillcfg_reads_min():
    a = _read("static/app.js")
    assert "['cPollMin', c.poll_interval_min]" in a, \
        "fillCfg 不回读后台最快档 → 保存成功但刷新又跳回 8"


def test_js_savecfg_posts_min():
    a = _read("static/app.js")
    assert "poll_interval_min: $('cPollMin')" in a, \
        "saveCfg 的 body 不提交最快档 → 后端永远收不到"


def test_js_autosave_includes_min():
    a = _read("static/app.js")
    assert re.search(r"\['cAppid', 'cPoll', 'cPollMin'", a), \
        "最快档没进防抖自动保存列表"


def test_js_range_and_hint_distinguish_base():
    a = _read("static/app.js")
    assert "_pme.min = 3" in a and "_pme.max = _pmax" in a, \
        "最快档输入框要按后台上限设范围"
    assert "这是「基线」" in a, "文案必须说清「检查间隔」是基线，不是最快档"
    assert "平稳后自动加快（最快 " not in a, \
        "旧文案写死了'最快 X s'，会让人以为改检查间隔就能改最快档"


# ---------------------------------------------------------------- 真值层

def test_state_returns_min_key():
    c = _fresh().get("/api/state")
    assert c.status_code == 200
    assert "poll_interval_min" in c.get_json()["config"], \
        "/api/state 不回这个键 → 前端永远拿到 undefined → 兜回 8"


def test_post_12_persists():
    c = _fresh()
    r = _post(c, poll_interval_min=12)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert _disk().get("poll_interval_min") == 12, \
        "面板提交的 12 没有真的落盘"


def test_post_12_state_readback():
    c = _fresh()
    _post(c, poll_interval_min=12)
    cfg = c.get("/api/state").get_json()["config"]
    assert cfg["poll_interval_min"] == 12, "落盘了但 state 不回，属于假保存"


def test_default_12_not_30():
    """空配置时面板默认值必须等于 bot.py 的 12，不能显示 30。"""
    c = _fresh()
    cfg = c.get("/api/state").get_json()["config"]
    assert cfg["poll_interval"] == 12
    rs = c.get("/api/runstate")
    if rs.status_code == 200:
        j = rs.get_json()
        if isinstance(j, dict) and "base" in j:
            assert j["base"] == 12


def test_clamp_lower():
    c = _fresh()
    _post(c, poll_interval_min=1)
    assert _disk().get("poll_interval_min") == 3, \
        "下界要与 bot.py 的 max(3,..) 对齐"


def test_clamp_upper():
    c = _fresh()
    _post(c, poll_interval_min=99999)
    assert _disk().get("poll_interval_min") == 3600


def test_invalid_value_keeps_old():
    c = _fresh()
    _post(c, poll_interval_min=12)
    _post(c, poll_interval_min="abc")
    assert _disk().get("poll_interval_min") == 12, \
        "非法输入不能把配置写成垃圾"


def test_invariant_min_le_base_le_max():
    """最快档调高时，基线与上限必须跟着抬，不能留下 min > base。"""
    c = _fresh()
    _post(c, poll_interval_min=20, poll_interval=12, poll_interval_max=120)
    d = _disk()
    assert d["poll_interval_min"] == 20
    assert d["poll_interval"] >= 20, "基线仍是 12，比最快档还快，语义反了"
    assert d["poll_interval_max"] >= 20


def test_roundtrip_set_12_disables_speedup():
    """用户真正要的效果：两个都设 12，加速分支不再有机会触发。"""
    c = _fresh()
    _post(c, poll_interval_min=12, poll_interval=12)
    d = _disk()
    # 与 bot.py 完全相同的两个表达式
    base = max(5, int(d.get("poll_interval", 12)))
    mn = max(3, int(d.get("poll_interval_min", 8)))
    assert base == 12 and mn == 12
    assert not (base > mn), "base > min 时 _adapt_interval 才会往下探"


def test_bot_reads_the_same_key():
    """闭环：面板写的键，确实是 bot 用来定 min_interval 的那个键。"""
    b = _read("bot.py")
    assert re.search(
        r"self\.min_interval\s*=\s*max\(3,\s*int\(cfg\.get\(\"poll_interval_min\",\s*8\)\)\)",
        b), "bot.py 不再读 poll_interval_min → 面板改了也不生效"


# ---------------------------------------------------------------- 热更新

def _hot_stub(min_now, cur, base=12, mx=120):
    """造一个只带轮询节奏字段的 bot 桩，不跑真实机器人。"""
    import types
    return types.SimpleNamespace(
        cfg={"poll_interval_min": min_now},
        min_interval=min_now, base_interval=base,
        max_interval=mx, interval=cur)


def _sync_with_disk(new_min, stub):
    """把配置写成 new_min，再按真实每轮的方式调一次 sync_cfg_from_disk。"""
    home = tempfile.mkdtemp(prefix="onobn_pollmin_hot_")
    os.environ["BILI_NOTIFY_HOME"] = home
    os.environ["BILI_NOTIFY_POINTER"] = os.path.join(home, "pointer")
    cfgutil.save_cfg({"poll_interval_min": new_min,
                      "poll_interval": 12, "poll_interval_max": 120})
    import bot as botmod
    # 每个用例都从"没读过配置"开始，否则 mtime 相同会直接 return
    botmod._CFG_MTIME[0] = 0.0
    botmod.sync_cfg_from_disk(stub)
    return stub


def test_hot_reload_min_interval():
    """用户真正要的闭环：面板填 12 保存后，不用重启就已经按 12 跑。"""
    s = _sync_with_disk(12, _hot_stub(8, 8))
    assert s.min_interval == 12, \
        "最快档没热更新 → 面板能改了但运行中的机器人仍按 8s 跑"
    assert s.cfg.get("poll_interval_min") == 12, "bot.cfg 没同步"
    assert s.interval >= 12, "当前间隔还停在 8s，比新下限还快"


def test_hot_reload_keeps_invariant():
    """下限调高时 base/max 不能比它还小，否则日志与面板会自相矛盾。"""
    s = _sync_with_disk(20, _hot_stub(8, 12))
    assert s.min_interval == 20
    assert s.base_interval >= 20 and s.max_interval >= 20
    assert s.interval >= 20


def test_hot_reload_can_lower_min():
    """也能调回去；调低时不要把当前间隔硬抬。"""
    s = _sync_with_disk(8, _hot_stub(12, 16))
    assert s.min_interval == 8
    assert s.interval == 16, "调低下限时不该改动当前实际间隔"


def test_hot_reload_no_change_is_noop():
    """值没变不该每轮都报"配置已热更新"，也不该动 interval。"""
    s = _sync_with_disk(12, _hot_stub(12, 16))
    assert s.min_interval == 12 and s.interval == 16


def test_hint_no_restart_required():
    h = _read("templates/index.html")
    assert "改完需重启机器人后生效" not in h, \
        "还写着要重启，用户改完会以为没生效又去重启一次"
    assert "即时生效" in h, "说明文案要写清保存后即时生效"


# ---------------------------------------------------------------- runner

def main():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)
           and f.__code__.co_argcount == 0]
    failed = []
    for n, f in fns:
        try:
            f()
            print("PASS " + n)
        except Exception as e:
            failed.append(n)
            print("FAIL %s : %s" % (n, e))
            traceback.print_exc(limit=2)
    print("---- %d passed, %d failed, %d total ----"
          % (len(fns) - len(failed), len(failed), len(fns)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
