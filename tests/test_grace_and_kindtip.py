# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""启动后首轮静默开关 + 四类提醒开关的悬停说明。

两件事，一个文件，因为它们来自同一条反馈：

1. **启动后首轮静默**
   启动同步有 25s 总时限，订阅多了会有 UP 主被留到轮询再对齐。轮询里
   的 `_init_baseline` 只补**缺失**的基线（only_missing=True，正常运行时
   这个保护是必须的），而那些被跳过的 UP 基线是**停机前的旧值**——不是
   缺失，于是第一轮 prev != cur → 当成新内容补推一遍。日志明明写着
   "已记为基线（静默）"，结果还是推了一条。
   宽限轮次把它们强制刷新（only_missing=False）并拦掉推送。

2. **四类开关的悬停说明**
   以前完全没有说明，「置顶评论」尤其容易被理解成"视频评论区被顶上去的
   那条"。文案里必须写清：它检测的是【置顶动态】下【UP 主自己置顶的评论】。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

APP_JS = os.path.join(SRC, "static", "app.js")
INDEX_HTML = os.path.join(SRC, "templates", "index.html")
BOT_PY = os.path.join(SRC, "bot.py")
WEBUI_PY = os.path.join(SRC, "webui.py")


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def _strip_comments(s):
    """去掉 // 行注释与 /* */ 块注释。

    ⚠️ 必须剥：注释里举例提到的旧写法会顶住断言，
       让"改坏了"看起来像"改好了"。这个坑本项目踩过不止一次。
    """
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?m)^\s*//.*$", "", s)
    s = re.sub(r"(?m)(?<![:'\"])//.*$", "", s)
    return s


def _strip_py_comments(s):
    """去掉 Python 的 # 行注释。

    ⚠️ 同样是防"注释顶住断言"：_grace_align 的 docstring 里写了
       only_missing=False，退化成 only_missing=True 之后，
       只剥 # 注释的话这条断言**照样是绿的** —— 反向验证实测踩到过。
       所以 docstring（三引号块）也要一并剥掉。
    """
    s = re.sub(r'""".*?"""', "", s, flags=re.S)
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in s.splitlines())


# ─────────────────────────── 悬停说明 ───────────────────────────

def test_kind_tip_has_all_four():
    """四类都要有说明，缺一类就等于那一类没有解释。"""
    js = _read(APP_JS)
    m = re.search(r"var KIND_TIP = \{(.*?)\n\};", js, flags=re.S)
    assert m, "找不到 KIND_TIP 定义"
    body = m.group(1)
    for k in ("live", "video", "dynamic", "top_comment"):
        assert re.search(r"\b%s\s*:" % k, body), f"KIND_TIP 缺少 {k}"


def test_top_comment_tip_clears_the_misconception():
    """置顶评论的说明必须写清"不是什么"和"适用场景"。

    这几句是这个功能被误解的主要来源，少一句就会有人勾错然后抱怨。
    """
    js = _read(APP_JS)
    m = re.search(r"var KIND_TIP = \{(.*?)\n\};", js, flags=re.S)
    body = m.group(1)
    seg = body.split("top_comment:", 1)[1]
    for must in ("置顶动态", "不是「所有视频的置顶评论」", "星瞳_Official",
                 "瞳瞳空间", "普通动态", "默认不勾"):
        assert must in seg, f"置顶评论说明缺少关键句：{must}"
    # 必须点明"动态本身没变" —— 这是为什么动态检测覆盖不到的原因
    assert ("动态本身没变" in seg) or ("动态本身没变" in seg), \
        "没说明普通动态检测为什么覆盖不到"


def test_subscribed_chip_has_tip():
    """已订阅 UP 主卡片里的四个开关都要带 data-tip。"""
    js = _strip_comments(_read(APP_JS))
    m = re.search(r'toggle-sub(.*?)esc\(k\.label\)', js, flags=re.S)
    assert m, "找不到订阅卡片里 chip-tgl 的渲染"
    assert "data-tip" in m.group(1), "订阅卡片的开关没有 data-tip"
    assert "kindTip(" in m.group(1), "data-tip 没走 kindTip()"


def test_migrate_chip_has_tip():
    """迁移页的四个开关同样要有说明（同一套文案）。"""
    js = _strip_comments(_read(APP_JS))
    m = re.search(r'mig-chip(.*?)migKindName\(k\)', js, flags=re.S)
    assert m, "找不到迁移页 chip-tgl 的渲染"
    assert "data-tip" in m.group(1), "迁移页的开关没有 data-tip"
    assert "kindTip(" in m.group(1), "迁移页 data-tip 没走 kindTip()"


def test_add_page_buttons_get_tip_via_js():
    """添加订阅页那四个按钮写在 HTML 里，靠 initKindTips 统一补。"""
    js = _strip_comments(_read(APP_JS))
    assert "function initKindTips()" in js, "缺少 initKindTips()"
    m = re.search(r"function initKindTips\(\) \{(.*?)\n\}", js, flags=re.S)
    assert m, "initKindTips 体没解析到"
    body = m.group(1)
    assert 'toggle-defkind' in body, "没按 data-act=toggle-defkind 去找按钮"
    assert "setAttribute('data-tip'" in body or \
        'setAttribute("data-tip"' in body, "没有真正写入 data-tip"
    # 幂等：群列表每 15 秒重画，别重复绑
    assert "initKindTips._done" in body, "initKindTips 没有幂等保护"


def test_add_page_buttons_exist_in_html():
    """HTML 里四个按钮都在，data-kind 齐全（否则 JS 补不上去）。"""
    html = _read(INDEX_HTML)
    for kind in ("live", "video", "dynamic", "top_comment"):
        assert 'data-act="toggle-defkind" data-kind="%s"' % kind in html, \
            f"添加订阅页缺少 {kind} 按钮"


def test_kind_tip_called_on_boot():
    """initKindTips 必须在启动时真的被调用，不然写了也不执行。"""
    js = _strip_comments(_read(APP_JS))
    assert re.search(r"initTips\(\);\s*\n\s*initKindTips\(\);", js), \
        "initKindTips 没在 bootHome 里跟着 initTips 一起调"


# ─────────────────────────── 启动后首轮静默 ───────────────────────────

def test_grace_align_forces_full_refresh():
    """宽限对齐必须强制刷新（only_missing=False）。

    只补缺失的话，被跳过的 UP 基线仍是旧值，照样补推 —— 正是要修的现象。
    """
    py = _strip_py_comments(_read(BOT_PY))
    m = re.search(r"async def _grace_align\(self\).*?(?=\n    async def )",
                  py, flags=re.S)
    assert m, "找不到 _grace_align"
    body = m.group(0)
    assert "only_missing=False" in body, \
        "宽限对齐没有强制刷新基线（only_missing=False）"
    assert "_startup_synced" in body, "没跳过启动同步已覆盖的 UP"
    assert "self._startup_synced.add(uid)" in body or "synced.add(uid)" in body, \
        "对齐完没记进已同步集合，下一轮会重复对齐"


def test_startup_sync_records_synced_uids():
    """启动同步成功一位就记一位，宽限轮次才知道该补谁。"""
    py = _strip_py_comments(_read(BOT_PY))
    m = re.search(r"async def _do_startup_sync\(self\).*?(?=\n    async def )",
                  py, flags=re.S)
    assert m, "找不到 _do_startup_sync"
    assert "self._startup_synced.add(uid)" in m.group(0), \
        "启动同步没把已对齐的 UP 记进 _startup_synced"


def test_grace_round_sets_silent_then_clears():
    """宽限轮内 _silent=True，过了就 False。

    ⚠️ else 分支不能少：少了 _silent 就永远是 True，机器人再也不推送。
    """
    py = _strip_py_comments(_read(BOT_PY))
    m = re.search(r"self\._round \+= 1(.*?)await self\._poll_once\(\)",
                  py, flags=re.S)
    assert m, "找不到轮询里的轮次推进"
    body = m.group(1)
    assert "startup_grace_rounds" in body, "没有读 startup_grace_rounds 配置"
    assert "self._silent = True" in body, "宽限轮没有置 _silent"
    assert "else:" in body and "self._silent = False" in body, \
        "过了宽限轮没有把 _silent 置回 False（会永远不推送）"
    assert "_grace_align()" in body, "宽限轮没有调用强制对齐"


def test_grace_round_clamped():
    """轮数必须钳在 0~5：填 999 会一直静默，填负数会出怪行为。"""
    py = _strip_py_comments(_read(BOT_PY))
    m = re.search(r"_grace = max\(0, min\(5, int\((.*?)\)\)\)", py, flags=re.S)
    assert m, "宽限轮数没有做 0~5 钳制"


def test_config_whitelist_accepts_grace_rounds():
    """后端白名单要收这个字段，否则面板改了不落盘。"""
    py = _read(WEBUI_PY)
    m = re.search(r'if d\.get\("startup_grace_rounds"\) is not None:(.*?)pass',
                  py, flags=re.S)
    assert m, "webui 没有处理 startup_grace_rounds"
    body = m.group(1)
    assert "min(" in body and "max(" in body, "接收了但没做范围钳制"


def test_panel_has_switch_and_round_input():
    """面板要有开关和轮数输入框。"""
    html = _read(INDEX_HTML)
    assert 'id="cGraceOn"' in html, "面板缺少首轮静默开关"
    assert 'id="cGraceRounds"' in html, "面板缺少静默轮数输入框"


def test_panel_switch_off_means_zero():
    """关掉开关必须真的提交 0，不能"关着却填 3"。"""
    js = _strip_comments(_read(APP_JS))
    m = re.search(r"startup_grace_rounds: \(function \(\) \{(.*?)\}\)\(\),",
                  js, flags=re.S)
    assert m, "保存时没有带上 startup_grace_rounds"
    body = m.group(1)
    assert "cGraceOn" in body, "没看开关状态"
    assert "return on ?" in body or "on ?" in body, "开关关掉时没有返回 0"
    assert ": 0" in body, "开关关掉时应提交 0"


def test_grace_switch_sync_function():
    """开关与轮数框要联动：关掉填 0 并禁用，打开恢复。"""
    js = _strip_comments(_read(APP_JS))
    m = re.search(r"function syncGraceOn\(\) \{(.*?)\n\}", js, flags=re.S)
    assert m, "缺少 syncGraceOn"
    body = m.group(1)
    assert "num.disabled = true" in body, "关掉时没禁用轮数框"
    assert "num.disabled = false" in body, "打开时没恢复轮数框"
    assert "num.value = 0" in body, "关掉时没把轮数归零"


def test_cfg_fills_back_grace_rounds():
    """打开面板要回填已保存的轮数，否则每次都显示默认 1。"""
    js = _strip_comments(_read(APP_JS))
    assert "startup_grace_rounds != null" in js or \
        "c.startup_grace_rounds" in js, "回填时没有读 startup_grace_rounds"


def test_grace_align_end_to_end():
    """真跑一遍 _grace_align，确认只补未同步的、且是强制刷新。

    静态断言只能证明"代码里写了 only_missing=False"，证明不了
    _startup_synced 的跳过真的生效 —— 这里用假 store 真调一次。
    """
    try:
        import asyncio
        sys.path.insert(0, os.path.join(HERE, "_stubs"))
        import botpy  # noqa: F401
        from bot import NotifyBot
    except Exception as e:                      # botpy 不在就算了，别拖垮整组
        print("SKIP 端到端（依赖缺失：%s）" % type(e).__name__)
        return

    import tempfile
    tmp = tempfile.mkdtemp(prefix="grace_")
    bot = NotifyBot({"data_file": os.path.join(tmp, "d.json")},
                    intents=botpy.Intents(public_messages=True))

    class FakeStore:
        def all_ups(self):
            return [1, 2, 3]

        def get_up(self, uid):
            return {"uname": "UP%s" % uid}

        def enabled_kinds_of_up(self, uid):
            return ["live"]

    calls = []

    async def fake_init(uid, only_missing=True):
        calls.append((uid, only_missing))

    bot._init_baseline = fake_init
    bot.store = FakeStore()
    bot._startup_synced = {2}          # 2 号已在启动同步里对齐过

    n = asyncio.get_event_loop().run_until_complete(bot._grace_align())
    assert n == 2, f"应只补 2 位未同步的，实际 {n}"
    assert calls == [(1, False), (3, False)], \
        f"必须跳过已同步的且强制刷新，实际 {calls}"

    # 再跑一次：已全部同步，一个都不该调
    calls.clear()
    n2 = asyncio.get_event_loop().run_until_complete(bot._grace_align())
    assert n2 == 0 and calls == [], "已同步的不该被重复对齐"


if __name__ == "__main__":
    bad = 0
    for name, fn in sorted(list(globals().items())):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print("PASS %s" % name)
        except AssertionError as e:
            bad += 1
            print("FAIL %s\n     %s" % (name, e))
    print("\n%d 项" % len([k for k in globals() if k.startswith("test_")]))
    sys.exit(1 if bad else 0)
