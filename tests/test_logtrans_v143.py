# -*- coding: utf-8 -*-
"""v1.43.0 日志翻译补漏（群代码 / 小写级别 / 配置键名）+ 按钮失效可定位。

用户截图里那几条日志，翻译后仍然半中半英：
    [info] 127.0.0.1 测试推送 6FBBD9B594F83BBCF92C29B284239945 动态
    [信息] 配置已热更新：image_size_hint_h
三处各自独立的原因，这里逐条锁住，防止改回去。
"""
import os
import sys

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE)

import logtranslate as LT  # noqa: E402

PASS = FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  → " + str(extra)) if extra else ""))


def src(name):
    with open(os.path.join(BASE, name), "r", encoding="utf-8") as f:
        return f.read()


print("=" * 60)
print("v1.43.0 日志翻译补漏 + 按钮失效可定位")
print("=" * 60)

# ── 1) 小写级别 [info] / [warn] ────────────────────────────────
print("[1] 日志级别：审计日志写的是小写方括号")
ck("[info] → [信息]", "[信息]" in LT.translate_symbols("[info] x"))
ck("[warn] → [警告]", "[警告]" in LT.translate_symbols("[warn] x"))
ck("[INFO] 仍正常", "[信息]" in LT.translate_symbols("[INFO] x"))
ck("[ERROR] → [错误]", "[错误]" in LT.translate_symbols("[ERROR] x"))
ck("[CRITICAL] → [严重]", "[严重]" in LT.translate_symbols("[CRITICAL] x"))
ck("[WARNING] → [警告]", "[警告]" in LT.translate_symbols("[WARNING] x"))
ck("级别翻译用 re.I（源码里有 flags=re.I）",
   "flags=re.I" in src("logtranslate.py"))

# ── 2) 配置键名 ────────────────────────────────────────────────
print("[2] 配置键名 → 中文（只在含「配置」的行里替换）")
out = LT.translate_symbols("配置已热更新：image_size_hint_h")
ck("image_size_hint_h → 图片尺寸提示-高", "图片尺寸提示-高" in out, out)
ck("同键名不再残留英文", "image_size_hint_h" not in out, out)
out2 = LT.translate_symbols("配置已热更新：image_send_mode、poll_interval")
ck("image_send_mode → 图片发送方式", "图片发送方式" in out2, out2)
ck("poll_interval → 检查间隔", "检查间隔" in out2, out2)
# 闸门：没有「配置」二字就不翻，避免误伤普通文本
out3 = LT.translate_symbols("poll_interval 出现在没有关键词的行里")
ck("无「配置」时不翻键名（防误伤）", "检查间隔" not in out3, out3)

# ── 3) 不误伤域名 ──────────────────────────────────────────────
print("[3] 不误伤域名 / 图床参数")
for u in ("https://live.bilibili.com/123",
          "https://i0.hdslb.com/bfs/x.jpg@672w_378h_1c.webp"):
    ck("保持原样：" + u, LT.translate_symbols(u) == u, LT.translate_symbols(u))

# ── 4) 群代码 → 群名（含裸群代码） ─────────────────────────────
print("[4] 群代码 → 群名")
_webui = src("webui.py")
ck("_humanize_groups 有裸群代码分支",
   "16,}" in _webui and "_humanize_groups" in _webui)
# 直接复刻该函数验证行为（webui 依赖 flask，这里避免整模块导入）
import re as _re  # noqa: E402


def humanize(text, gmap):
    def _r(m):
        code = m.group(1)
        for pre, name in gmap.items():
            if code.startswith(pre):
                return "群「%s」" % name
        return m.group(0)
    text = _re.sub(r"群\s+([A-Za-z0-9]{6,})…?", _r, text)
    text = _re.sub(r"\b([A-Za-z0-9]{16,})\b", _r, text)
    return text


G = {"6FBBD9B5": "OnOiduts工业区", "DDD81F36": "测试群"}
ck("裸群代码被换成群名",
   "群「OnOiduts工业区」" in humanize(
       "[info] 127.0.0.1 测试推送 6FBBD9B594F83BBCF92C29B284239945 动态", G))
ck("「群 6FBBD9B5…」写法仍生效",
   "群「测试群」" in humanize("已推送 → 群 DDD81F36…", G))
ck("trace_id 不被误伤",
   "2d808ac29c702f426fbc325d7615013b" in humanize(
       "trace_id=2d808ac29c702f426fbc325d7615013b 其它", G))
ck("空映射时原样返回", humanize("测试推送 6FBBD9B594F83BBCF92C29B284239945", {})
   == "测试推送 6FBBD9B594F83BBCF92C29B284239945")

# ── 5) 按钮失效可定位 ──────────────────────────────────────────
print("[5] 按钮点了没反应：现在能自己定位")
js = src("static/app.js")
ck("事件委托在脚本解析时就装（不等 DOMContentLoaded）",
   _re.search(r"try\s*\{\s*initDelegates\(\)", js) is not None)
ck("initDelegates 有只装一次的开关", "DELEGATES_ON" in js)
ck("动作调用被 try/catch 包住", "window.__ACT_FAILS" in js)
ck("动作出错会弹提示", "toast('「' + act" in js or "toast(\"「\" + act" in js)
ck("有前端版本常量 __APP_VER", "window.__APP_VER" in js)
ck("有前端/后端版本比对", "function checkJsVersion" in js)
ck("版本比对在更新日志加载后触发",
   _re.search(r"checkJsVersion\(\(list\[0\]", js) is not None)
ck("横幅提示提到端口被占", "端口被占用" in js)
# ⚠️ v1.44.1 起 __APP_VER 不再硬编码，而是取后端注入的 window.__BE_VER
#    （硬编码就是"打包忘了改"的病根）。静态检查只能查那个兜底值。
ck("__APP_VER 由后端注入（不再是硬编码）",
   _re.search(r"window\.__APP_VER\s*=\s*window\.__BE_VER", js) is not None)
# ⚠️ v1.48.0：兜底值也不能写死版本号 —— 打包时忘了同步就会永远比后端低，
#    页面顶部一直挂着「版本不一致」红色横幅（那是我自己制造的误报）。
#    现在取不到注入值就留空，checkJsVersion 里"前端版本未知时不报警"。
ck("__APP_VER 兜底不写死版本号",
   _re.search(r"window\.__APP_VER\s*=\s*window\.__BE_VER\s*\|\|\s*''", js)
   is not None)
ck("前端版本未知时不报警",
   "if (!be || !fe || be === fe) return;" in js)

# ── 6) 历史默认值迁移 ──────────────────────────────────────────
print("[6] 图片尺寸提示-高：历史默认值 378 一次性迁回 0")
ck("_migrate_cfg_once 存在", "_migrate_cfg_once" in _webui)
ck("只认 378 这一个值", "== 378" in _webui or "== 378" in _webui)
ck("有只做一次的标记", "_cfg_migrated_v141" in _webui)
ck("启动时被调用", "_migrate_cfg_once()" in _webui)

print("-" * 60)
print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
