# -*- coding: utf-8 -*-
"""品牌名拼写：全站必须是 OnOiduts（结尾带 s），不许再出现 OnOidust。

回归背景：新群第一次 @ 机器人回复的欢迎语里写成了「感谢使用OnOidust产品」，
漏了结尾的 s。单点改完容易再漏，所以这里加一条全站兜底扫描。
"""
import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

SRC = os.path.join(_ROOT, "src")
# 历史更新日志是记录，不篡改其中的旧文字
_EXCLUDE = {os.path.join(_ROOT, "docs", "CHANGELOG.md")}

_SCAN_EXT = (".py", ".js", ".html", ".md")
_BAD = re.compile(r"OnOidust(?!s)")

n = 0
bad = 0


def ck(name, cond, extra=""):
    global n, bad
    n += 1
    if cond:
        print("  ok   %s" % name)
    else:
        bad += 1
        print("  FAIL %s %s" % (name, extra))


def _read(p):
    with io.open(p, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


print("[品牌名拼写]")

bot_src = _read(os.path.join(SRC, "bot.py"))
ck("欢迎语带 s", "感谢使用OnOiduts产品" in bot_src, "bot.py 欢迎语")
ck("欢迎语不再漏 s", "感谢使用OnOidust产品" not in bot_src)

html = _read(os.path.join(SRC, "templates", "index.html"))
# ⚠️ v2.0.5 起页脚品牌名回退成最早那版「OnOiduts工业」：v2.0.1 更名时换成过
#    缩写 OnOBN，作者要求原样留在左下角。所以这里断言的是旧名，不是 OnOBN。
#    文字改回去了，但整块做成了通往 GitHub 的链接 —— 位置与排版都不动。
ck("页脚品牌名是 OnOiduts工业", ">OnOiduts工业<" in html)
ck("页脚品牌是链接（a.sf-brand）", 'class="sf-brand"' in html
   and re.search(r'<a[^>]*class="sf-brand"', html) is not None)
# ⚠️ 品牌名叫 OnOiduts，点开就该是【组织】主页。
#    注意：关于页「署名」那条仍指向个人账号（见下），是许可证要求的作者署名，
#    两者刻意不同，别统一成一个。所以这里必须精确匹配 a.sf-brand 的 href，
#    不能只判"html 里有没有组织 URL"。
_m_brand_href = re.search(r'<a[^>]*class="sf-brand"[^>]*href="([^"]+)"', html)
ck("品牌链接指向 OnOiduts 组织主页",
   _m_brand_href is not None and _m_brand_href.group(1) == "https://github.com/OnOiduts",
   (_m_brand_href.group(1) if _m_brand_href else "没找到 a.sf-brand 的 href"))
ck("品牌链接不再指向个人账号",
   _m_brand_href is not None and _m_brand_href.group(1) != "https://github.com/XxBoLuoxX")
ck("品牌链接新窗口打开且防反向劫持",
   'target="_blank"' in html and 'rel="noopener noreferrer"' in html)
# 项目正式名（OnOBN 系列）不能因为页脚回退就整个消失
ck("仍保留缩写 OnOBN", "OnOBN" in html)
# 署名仍必须是个人账号（许可证条款要求标注作者）
ck("关于页署名仍指向个人账号", "github.com/XxBoLuoxX" in html)

# ---- 全站兜底扫描 ----
hits = []
for base in (SRC, os.path.join(_ROOT, "tests"), _ROOT):
    if not os.path.isdir(base):
        continue
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "data")]
        for fn in filenames:
            if not fn.endswith(_SCAN_EXT):
                continue
            p = os.path.join(dirpath, fn)
            ap = os.path.abspath(p)
            # 排除历史日志，以及本文件自身（正则里就写着那个错拼）
            if ap in _EXCLUDE or ap == os.path.abspath(__file__):
                continue
            if _BAD.search(_read(p)):
                hits.append(os.path.relpath(p, _ROOT))
ck("全站无 OnOidust 残留", not hits, str(hits[:5]))

# 不许叠加成 OnOiduts工业工业
ck("无叠加重复", "OnOiduts工业工业" not in html)

print("\n通过 %d / 失败 %d" % (n - bad, bad))
sys.exit(1 if bad else 0)
