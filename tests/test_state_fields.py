# -*- coding: utf-8 -*-
"""锁定测试：面板能改的字段，/api/state 必须回；logo 规则不许改回去。

背景（v1.31.15）：
  「测试 UID」保存后 toast 是对的，输入框却跳回旧值 —— 根因不是前端，
  而是 /api/state 的 config 里**根本没有 selfcheck_uid 这个键**，
  前端一律回退默认常量，表现成"一直回弹"。同类字段还有
  min_request_interval / poll_interval_max / poll_interval_min。

  logo 底纹：原图白色透明底，日间白底上等于白线；且 right:-6px 会被
  .brand 的 overflow:hidden 裁掉，看着"显示不全"。
"""
import io
import os
import sys

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE_DIR)

def _os_path(rel):
    """随包文件：新版在 src/ 或 docs/，老布局摊在程序根目录 —— 三处都找。

    v1.96 起根目录只留启动脚本，config.example.yaml / VERSION /
    requirements 都搬进了子目录；老包解开是扁平布局，所以都要认。
    """
    import os as _o
    for _base in (BASE_DIR, _o.path.join(_o.path.dirname(BASE_DIR), "docs"),
                  _o.path.dirname(BASE_DIR)):
        _p = _o.path.join(_base, rel)
        if _o.path.exists(_p):
            return _p
    return _o.path.join(BASE_DIR, rel)



FAILS = []


def ck(name, cond):
    print(("  [PASS] " if cond else "  [FAIL] ") + name)
    if not cond:
        FAILS.append(name)


def t_state_returns_editable_fields():
    """面板能改的字段，state 必须回 —— 否则保存就是假的。"""
    import webui as W
    W.app.config["TESTING"] = True
    c = W.app.test_client()
    r = c.get("/api/state", headers={"X-Requested-With": "bili-notify"})
    d = r.get_json() or {}
    cfg = d.get("config") or {}
    ck("state 返回 config", bool(cfg))
    for k in ("selfcheck_uid", "min_request_interval",
              "poll_interval_max", "poll_interval_min",
              "log_translate", "log_debug"):
        ck("state 回 %s" % k, k in cfg)
    # 自检 UID 必须是非空数字串，前端回退默认只作兜底
    ck("selfcheck_uid 非空", str(cfg.get("selfcheck_uid") or "").strip() != "")


def t_selfcheck_uid_roundtrip():
    """保存 → state 回读，值要一致（以前保存成功但读回来是默认）。"""
    import webui as W
    W.app.config["TESTING"] = True
    c = W.app.test_client()
    H = {"X-Requested-With": "bili-notify"}
    sent = "401315430"
    r = c.post("/api/config", json={"selfcheck_uid": sent}, headers=H)
    ck("保存 selfcheck_uid 成功", (r.get_json() or {}).get("ok") is True)
    d = c.get("/api/state", headers=H).get_json() or {}
    got = str((d.get("config") or {}).get("selfcheck_uid") or "")
    ck("state 回读等于保存值", got == sent)


def t_frontend_uses_backend_value():
    """前端不能只读自己那个默认常量。"""
    src = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                  encoding="utf-8").read()
    ck("loadChkUid 支持强制回写", "function loadChkUid(force)" in src)
    ck("保存后强制刷新输入框", "loadChkUid(true)" in src)
    ck("回退默认值只作兜底", "|| CHK_UID_DEFAULT" in src)


def t_logo_rules():
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    ck("日间 logo 反色", 'filter:invert(1)' in html)
    # 断言只针对**真实声明**：源码里提到 right:-6px 的那行是说明为什么改掉，
    # 属于注释，不该算违规。
    bad = [ln for ln in html.splitlines()
           if "right:-6px" in ln and not ln.strip().startswith(("*", "/*"))]
    ck("logo 不再负偏移出血", not bad)
    ck("logo 限制高度防裁切", "max-height:calc(100% - 8px)" in html)
    ck("logo 完整显示不裁切", "background-size:contain" in html)


def t_changelog_date_is_ymd():
    """更新日志日期只要年月日，不能带时分秒。"""
    import version as V
    for e in V.changelog()[:30]:
        d = (e.get("date") or "").strip()
        if not d:
            continue
        if len(d) != 10 or ":" in d:
            ck("日期 %r 只要年月日" % d, False)
            return
    ck("全部条目日期都是 YYYY-MM-DD", True)


def t_changelog_ui():
    """v1.31.16：更新日志折叠框 + 条数三档 + 时间到秒 + 版本行居中。

    这几个坑都是"看着像做了、其实没生效"：
      · .cl-body 只有基础样式没有 display 规则 → 压根不是折叠框
      · 条数是 number 输入框，而后端还卡着 min(n,50) → "全部"取不全
      · 前端 slice(0,10) → 时分秒就算解析对了也被砍掉
    """
    import version as V
    import webui as W

    html = open(os.path.join(BASE_DIR, "templates", "index.html"),
                encoding="utf-8").read()
    js = open(os.path.join(BASE_DIR, "static", "app.js"),
              encoding="utf-8").read()

    # ① 折叠框：默认收起，点开才展开
    ck(".cl-body 默认隐藏", ".cl-body { display:none; }" in html)
    ck("展开后才显示内容", ".cl-item.open .cl-body { display:block; }" in html)
    ck("折叠箭头会转", ".cl-item.open .cl-arrow" in html)

    # ② 条数三档
    for v in ("3", "5", "all"):
        ck("条数档位 %s 存在" % v, 'data-cl="%s"' % v in html)
    ck("条数不再是 number 输入框",
       'id="clCount" type="number"' not in html)

    # ③ 日期带时分秒
    es = V.changelog()
    ck("最新条目有 time", bool((es[0].get("time") or "").strip()))
    ck("date 仍是纯年月日", len(es[0]["date"]) == 10)
    # ⚠️ 前端这句会把时分秒砍掉，和"要显示时分秒"直接冲突。
    #    但别全文件朴素扫 "slice(0, 10)" —— waitLabel() 里就有一处
    #    （剥 emoji 后取前 10 个字做"正在…"提示），跟日期无关，会误报。
    #    只看 cl-date 那一段渲染代码。
    _cld = js[js.index("'<span class=\"cl-date\">'"):]
    _cld = _cld[:_cld.index("'</span>'") + 10]
    ck("前端不再砍掉时分秒", "slice(0, 10)" not in _cld)
    ck("日期段把 date 和 time 拼回去", "e.time" in _cld and "e.date" in _cld)

    # ④ 选"全部"要能一次取完
    W.app.config["TESTING"] = True
    c = W.app.test_client()
    d = c.get("/api/version?limit=999",
              headers={"X-Requested-With": "bili-notify"}).get_json() or {}
    cl = d.get("changelog") or []
    ck("全部一次取完（%d 条，>50 才说明没被截断）" % len(cl), len(cl) > 50)

    # ⑤ 左下角版本行
    seg = html.split(".side-foot {")[1][:220]
    ck("版本行居中", "text-align:center" in seg)
    ck("版本行有品牌前缀", "OnOiduts工业" in html)


def t_switch_visual_sync():
    """v1.31.17：开关外观必须跟着 checked 走。

    一批开关写成 <label class="sw-btn"><input type="checkbox" hidden>>，
    开/关外观靠 .on 类。以前**没有任何代码把 checked 同步成 .on** ——
    点一下值变了外观不动，刷新后停在 HTML 写死的样子，和后台真实值对不上。
    这就是「图片 / 提醒开关板块失效」「沙箱环境无法交互」的根因。
    """
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    ck("有开关外观同步函数", "function paintSwitches()" in js)
    ck("有开关初始化", "function initSwitches()" in js)
    ck("同步已注册到启动列表", "safe('sw', initSwitches)" in js)
    for sid in ("cImage", "cOffline", "cOnlyUpCmt", "cPlainMsg",
                "cSandbox2", "cLogTrans", "cDebugLog"):
        ck("开关 %s 已登记" % sid, "'%s'" % sid in js.split("const SW_IDS = [")[1][:400])
    # v1.31.18：「只看问题」从假开关改成真按钮 —— 按下亮起、再按熄灭。
    # ⚠️ 断言跟着改，不能为了过测试把按钮改回 checkbox：
    #    以前它只有 label 没有联动，点了根本不切换。
    ck("只看问题是按钮", 'data-act="toggle-log-err"' in html
       or "toggle-log-err" in js)
    ck("只看问题不再是 checkbox", 'id="logErrOnly"' not in html)
    ck("有只看问题动作", "'toggle-log-err'" in js)
    ck("按钮有亮起样式", ".btn.on" in html)
    # 沙箱环境必须真的保存，否则"点了没反应"
    ck("cSandbox2 会自动保存", "'cSandbox2', 'cOffline', 'cImage', 'cPlainMsg'," in js)


def t_debug_log_is_real():
    """v1.31.17：「输出调试日志」从虚构项变成真功能。"""
    bot = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("bot.py 有 apply_log_level", "def apply_log_level():" in bot)
    ck("bot.py 有 sync_log_level", "def sync_log_level():" in bot)
    ck("轮询里会对一次级别", "sync_log_level()" in bot)
    ck("按 log_debug 切 DEBUG", '"log_debug", False' in bot)
    cfg = io.open(_os_path("config.example.yaml"),
                  encoding="utf-8").read()
    ck("示例配置有 log_debug", "log_debug:" in cfg)
    ck("示例配置有 log_translate", "log_translate:" in cfg)

    import webui as W
    W.app.config["TESTING"] = True
    c = W.app.test_client()
    H = {"X-Requested-With": "bili-notify"}
    d = c.get("/api/state", headers=H).get_json() or {}
    ck("state 回 log_debug", "log_debug" in (d.get("config") or {}))
    ck("state 回 log_translate", "log_translate" in (d.get("config") or {}))
    # 关着的时候点了应该被拒绝，而不是假装成功
    r = c.post("/api/debug/dump", json={}, headers=H).get_json() or {}
    ck("未开开关时拒绝输出", r.get("ok") is not True or "log_debug" not in str(r))


def t_sandbox_jump_highlight():
    """v1.31.17：跳沙盒群要滚到卡片并高亮，不能只停在板块顶部。"""
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("有 goto-sandbox 动作", "'goto-sandbox'" in js)
    ck("跳转后会滚动到卡片", "scrollIntoView" in js)
    ck("会加高亮类", "classList.add('flash')" in js)
    ck("卡片有 flash 动画", ".card.flash" in html and "@keyframes flashCard" in html)
    ck("尊重减少动效", ".card.flash { animation:none;" in html)
    # 4 个入口都要走新动作（"通用"选项卡按钮除外）
    ck("顶栏沙盒群走新动作", 'id="tbSb" data-act="goto-sandbox"' in html)
    ck("测试页去设置走新动作", 'data-act="goto-sandbox">去设置' in html)


def t_poll_range_and_presets():
    """v1.31.17：检查节奏显示真实上下限；文案模板有预设按钮。"""
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("范围提示有 id", 'id="pollRangeHint"' in html)
    ck("前端按真实上下限渲染", "pollRangeHint" in js and "_pmin" in js)
    ck("投稿动态显示后台值", 'id="slowNow"' in html and "slowNow" in js)
    ck("中文翻译开关存在", 'id="cLogTrans"' in html)
    ck("调试日志开关存在", 'id="cDebugLog"' in html)
    ck("有文案预设按钮", "data-act=\"tpl-preset\"" in html or "tpl-preset" in html)
    ck("有预设表", "const TPL_PRESETS" in js)
    for k in ("default", "simple", "detail"):
        ck("预设 %s 存在" % k, 'data-preset="%s"' % k in html)


def main():
    print("== /api/state 字段与 logo 规则 ==")
    t_state_returns_editable_fields()
    t_selfcheck_uid_roundtrip()
    t_frontend_uses_backend_value()
    t_logo_rules()
    t_changelog_date_is_ymd()
    t_changelog_ui()
    t_switch_visual_sync()
    t_debug_log_is_real()
    t_sandbox_jump_highlight()
    t_poll_range_and_presets()
    print("")
    if FAILS:
        print("失败 %d 项：" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("全部通过 OK")


if __name__ == "__main__":
    main()
