# -*- coding: utf-8 -*-
"""文案模板的面板显示名必须齐全，不能露出英文键名。

回归背景：面板「UP 主专属文案 / 合集专属文案」弹层里，输入框的名字取的是
后端返回的 titles，而后端给的是 HEAD_TITLE —— 那是**发出去那条消息的卡片
标题**，只有 8 项，三种「无标题」变体（dynamic_no_title /
dynamic_pinned_no_title / dynamic_live_no_title）没有独立卡片标题，
于是前端 `titles[k] || k` 拿不到就退回键名原文，用户看到三行英文。

修法：notify 里新增 TPL_LABEL（覆盖全部模板键，只用于面板显示），
后端三个接口改返回它，前端再兜一层 labels。本测试把这条链路整段锁住。
"""
import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import notify  # noqa: E402

n = 0
bad = 0


def ck(name, cond, extra=""):
    global n, bad
    n += 1
    if cond:
        print("  ok   %s" % name)
    else:
        bad += 1
        print("  FAIL %s%s" % (name, ("  <- " + str(extra)) if extra else ""))


_HAN = re.compile(r"[\u4e00-\u9fff]")

print("== 文案模板面板显示名 ==")

# 1) 每个模板键都要有显示名
keys = list(notify.DEFAULT_TEMPLATES.keys())
missing = [k for k in keys if k not in notify.TPL_LABEL]
ck("TPL_LABEL 覆盖全部 %d 个模板键" % len(keys), not missing, missing)

# 2) 显示名必须是中文（含汉字），且不能等于键名原文
non_han = [k for k in keys if not _HAN.search(notify.TPL_LABEL.get(k, ""))]
ck("显示名都是中文", not non_han, non_han)

raw = [k for k in keys if notify.TPL_LABEL.get(k) == k]
ck("显示名不等于键名原文", not raw, raw)

# 3) 三种「无标题」变体单独点名——就是这三行露出过英文
for k in ("dynamic_no_title", "dynamic_pinned_no_title",
          "dynamic_live_no_title"):
    ck("%s 有中文显示名" % k,
       _HAN.search(notify.TPL_LABEL.get(k, "")) is not None,
       notify.TPL_LABEL.get(k))

# 4) 面板分组里出现的键，一个都不能漏
group_keys = [k for _g, _l, ks in notify.TPL_GROUPS for k in ks]
gap = [k for k in group_keys if k not in notify.TPL_LABEL]
ck("TPL_GROUPS 里的键全有名", not gap, gap)

# 5) 变量说明同理（顺带一起守住，同样是面板显示）
gap_v = [v for v in set(v for vs in notify.TPL_VARS.values() for v in vs)
         if v not in notify.VAR_LABEL]
ck("TPL_VARS 里的变量全有中文说明", not gap_v, gap_v)

# 6) HEAD_TITLE 是卡片标题，不能被拿去当显示名用（否则又会漏）
src_js = io.open(os.path.join(_ROOT, "src", "static", "app.js"),
                 encoding="utf-8").read()
ck("前端不再直接拿 titles 兜底（有 labels 兜一层）",
   "labels[k] || titles[k] || k" in src_js)
ck("前端取标签的地方不再只剩 || k",
   src_js.count("esc(titles[k] || k)") == 0)

src_webui = io.open(os.path.join(_ROOT, "src", "webui.py"),
                    encoding="utf-8").read()
ck("后端接口返回 TPL_LABEL 而非 HEAD_TITLE",
   '"titles": TPL_LABEL' in src_webui and '"tpl_titles": TPL_LABEL' in src_webui)
ck("后端不再把 HEAD_TITLE 当显示名下发",
   '"titles": HEAD_TITLE' not in src_webui
   and '"tpl_titles": HEAD_TITLE' not in src_webui)

# 7) 端到端：模拟前端取值，保证任何键都拿得到中文
labels = notify.TPL_LABEL
titles = notify.HEAD_TITLE
shown = {k: (labels.get(k) or titles.get(k) or k) for k in group_keys}
still_raw = [k for k, v in shown.items() if v == k]
ck("模拟前端取值后没有键名裸奔", not still_raw, still_raw)

print("\n%d 项，通过 %d，失败 %d" % (n, n - bad, bad))
sys.exit(1 if bad else 0)
