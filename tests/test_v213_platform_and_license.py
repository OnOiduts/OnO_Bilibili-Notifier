#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.1.3 三项改动的回归锁：官方/第三方路线对比、qrcode 降级、扫码接口弃用。

为什么单独立一个测试：

1. README 只写一句「本项目用官方 SDK」是不够的 —— 在 QQ 上做机器人有
   「官方开放平台」和「第三方逆向协议」两条完全不同的路线，合规风险、
   能力边界、账号要求都不一样。不点明，外人会按自己熟悉的那条去理解，
   甚至误以为这是 go-cqhttp 类工具。

2. `qrcode` 曾经是**必装依赖**，但它只服务于「扫码登录」这一种方式，
   而面板的扫码入口早就摘掉了（B 站扫码接口经常被风控拿不到 SESSDATA），
   登录已统一走浏览器（playwright）。结果：没人用的库被当成必装项，
   没装就拦住启动、装了又完全用不上。必须锁住它已经降级为可选。

3. 扫码的两个 HTTP 接口前端已无调用点，必须确认它们不再偷偷启动后台线程。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")


def _read(p):
    return open(p, encoding="utf-8").read()


def _section(title):
    """取 README 里某个 ## 章节的正文。

    ⚠️ 之前直接 `s.split(title)[1]`，章节被删掉时会抛 IndexError，
       而主循环只捕获 AssertionError —— 结果整脚本崩溃、后面的断言全不跑，
       反向验证里看起来像"没咬住"。这里改成显式 AssertionError。
    """
    s = _read(README)
    parts = s.split(title)
    if len(parts) < 2:
        raise AssertionError("README 缺少章节：%s" % title)
    return parts[1].split("\n## ")[0]


# ─────────────────────────────────────────────────────────────
# 一、README 必须讲清官方路线与第三方路线的差别
# ─────────────────────────────────────────────────────────────

def test_readme_has_route_compare_section():
    """必须有一个专门讲「官方 vs 第三方」的章节，而不只是提一句。"""
    s = _read(README)
    assert "官方机器人 vs 第三方协议实现" in s, \
        "README 缺少「官方 vs 第三方」对比章节"


def test_compare_table_lists_both_routes():
    """对比表里必须同时出现两条路线。

    ⚠️ 这条断言曾经写坏过：只在全文各查一次 `go-cqhttp` 和「官方」，
       结果把 go-cqhttp 删掉后测试照样全绿 —— 因为文档别处也有"官方"二字，
       等于没守住。现在要求**同一行内**同时出现两者。
    """
    s = _read(README)
    hit = [ln for ln in s.split("\n")
           if "go-cqhttp" in ln and ("官方" in ln and "第三方" in ln)]
    assert hit, "对比表里没有把「官方路线」和「go-cqhttp 等第三方」并列写在一行"


def test_compare_has_dimensions():
    """差异点必须覆盖关键维度，不能只写一句「官方更合规」。"""
    sec = _section("官方机器人 vs 第三方协议实现")
    for kw in ("协议来源", "账号", "合规性", "能力范围", "频率限制"):
        assert kw in sec, "对比章节缺少维度：%s" % kw


def test_compare_has_pros_and_cons():
    """优缺点两条路线都要写，且官方路线的缺点不能回避。"""
    sec = _section("官方机器人 vs 第三方协议实现")
    assert "官方路线的优点" in sec and "官方路线的缺点" in sec, \
        "没有分别列出官方路线的优点与缺点"
    assert "第三方路线的优点" in sec and "第三方路线的缺点" in sec, \
        "没有分别列出第三方路线的优点与缺点"
    # 缺点里必须点出根本性的那条
    assert "封号" in sec, "没有点出第三方路线的封号风险"
    assert "频控" in sec or "频率限制" in sec, "没有写明官方路线的频控限制"


def test_readme_states_no_third_party_support():
    """必须明说本项目不支持第三方协议，免得有人白等。"""
    s = _read(README)
    assert "不提供" in s and "第三方" in s, \
        "README 没有说明本项目不提供第三方协议支持"


def test_docs_readme_synced():
    """docs/README.md 应与根目录 README 保持一致（之前同步过）。"""
    a = _read(README)
    b = _read(os.path.join(ROOT, "docs", "README.md"))
    assert "官方机器人 vs 第三方协议实现" in b, "docs/README.md 未同步对比章节"


# ─────────────────────────────────────────────────────────────
# 二、qrcode 必须降级为可选
# ─────────────────────────────────────────────────────────────

def test_qrcode_not_in_main_requirements():
    """qrcode 不能出现在必装清单里 —— 没人用却会拦住启动。"""
    s = _read(os.path.join(ROOT, "src", "requirements.txt"))
    for ln in s.split("\n"):
        if ln.strip().startswith("#"):
            continue
        assert not ln.strip().lower().startswith("qrcode"), \
            "qrcode 仍在必装清单 src/requirements.txt 里"


def test_qrcode_not_in_start_required():
    """start.py 的 REQUIRED 决定「缺了就不让启动」，qrcode 不能在其中。"""
    s = _read(os.path.join(ROOT, "src", "start.py"))
    m = re.search(r"REQUIRED\s*=\s*\((.*?)\)\s*\n", s, re.S)
    assert m, "start.py 里找不到 REQUIRED 定义"
    assert "qrcode" not in m.group(1), \
        "start.py 的 REQUIRED 里仍有 qrcode（缺了会拦住启动）"


def test_qrcode_fully_removed_v220():
    """v2.2.0 把扫码登录整条链路删了（含命令行 --qr），qrcode 也不再是可选依赖。

    ⚠️ 这条断言在 v2.1.3 时是「qrcode 应保留在可选清单里」——
       v2.2.0 删除 --qr 后该写法过时，已改为「qrcode 完全移除」。
       保留这段说明，免得日后有人照旧稿把它加回来。
    """
    opt = _read(os.path.join(ROOT, "src", "requirements-optional.txt"))
    req = _read(os.path.join(ROOT, "src", "requirements.txt"))
    for line in (opt, req):
        assert not re.search(r"^qrcode\s*>=", line, re.M), \
            "qrcode 应已从依赖清单移除（扫码整条链路已删）"
        assert not re.search(r"^Pillow\s*>=", line, re.M), \
            "Pillow 应已从依赖清单移除（只服务于二维码出图）"
    # playwright 是浏览器登录用的，必须在**必装**清单里。
    # ⚠️ v2.2.0 曾把它留在可选清单，结果「🌐 浏览器登录」缺依赖 ——
    #    两条登录路线最终都依赖它，v2.2.1 已移回必装。
    assert re.search(r"^playwright\s*>=", req, re.M), \
        "playwright 必须在必装清单里（两条登录路线都依赖它）"
    assert not re.search(r"^playwright\s*>=", opt, re.M), \
        "playwright 不该留在可选清单里"


def test_browser_login_still_available():
    """登录功能本身不能废：浏览器登录与手动粘贴两条路都还在。"""
    html = _read(os.path.join(ROOT, "src", "templates", "index.html"))
    assert "bili-browser" in html, "面板缺少「🌐 浏览器登录」入口"
    assert "cookieToggle" in html or "手动填写" in html, \
        "面板缺少手动粘贴 Cookie 入口"


def test_no_qr_button_in_panel():
    """面板上不该再有二维码扫码按钮（B 站扫码接口经常被风控）。

    ⚠️ 这条断言第一次写错了：直接在整个 HTML 里搜「获取二维码」，
       命中了那段解释"为什么摘掉扫码按钮"的注释，误报。
       现在先剥掉 HTML 注释，只看真正会渲染出来的部分。
    """
    html = _read(os.path.join(ROOT, "src", "templates", "index.html"))
    visible = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    assert "获取二维码" not in visible, "面板仍有「获取二维码」按钮"
    assert "重新扫码" not in visible, "面板仍有「重新扫码」按钮"
    # 真按钮都会挂在 data-act 上，一并确认没有扫码相关的 action
    assert "bili-qr" not in visible and "bili-scan" not in visible, \
        "面板仍有扫码相关的按钮 action"


# ─────────────────────────────────────────────────────────────
# 三、扫码 HTTP 接口必须标记弃用
# ─────────────────────────────────────────────────────────────

def test_qrcode_api_removed():
    """⚠️ v2.2.0：两个扫码接口已整条删除（不是标记弃用，是删掉）。

    之前只是返回 deprecated 提示，留着仍是死代码；现在连路由一起去掉。
    """
    s = _read(os.path.join(ROOT, "src", "webui.py"))
    # ⚠️ 只查「真实存在的路由」，不查注释：v2.2.0 的删除说明里会提到旧路由名，
    #    裸子串匹配会把注释当成残留（误报）。这里认带引号的完整路径。
    for fn in ("def api_bili_qrcode(", "def api_bili_poll(",
               '"/api/bili/qrcode"', "'/api/bili/qrcode'",
               '"/api/bili/poll"', "'/api/bili/poll'"):
        assert fn not in s, "%s 仍残留在 webui.py 里" % fn
    assert "_start_bili_login" not in s, "后台扫码线程仍在"
    # bili_login.py 里也不该再有二维码生成与扫码器
    b = _read(os.path.join(ROOT, "src", "bili_login.py"))
    for fn in ("def make_qr_png(", "def make_qr_ascii(",
               "class BiliLogin:", "qrcode/generate"):
        assert fn not in b, "%s 仍残留在 bili_login.py 里" % fn


def test_frontend_does_not_call_qr_api():
    """前端不应再有扫码接口的调用点。"""
    js = ""
    for name in ("app.js", "sound.js"):
        p = os.path.join(ROOT, "src", "static", name)
        if os.path.isfile(p):
            js += _read(p)
    assert "bili/qrcode" not in js, "前端仍在调用 /api/bili/qrcode"
    assert "bili/poll" not in js, "前端仍在调用 /api/bili/poll"


# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    fails = 0
    total = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_") and callable(v):
            total += 1
            try:
                v()
                print("  PASS %s" % k)
            except AssertionError as e:
                fails += 1
                print("  FAIL %s: %s" % (k, e))
    print("v2.1.3 三项改动：%d 项，失败 %d" % (total, fails))
    sys.exit(1 if fails else 0)
