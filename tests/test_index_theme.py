# -*- coding: utf-8 -*-
"""面板首页主题：日间模式进去不能先闪一下夜间。

守住四件事：
1. index.html 的初始主题来自服务端变量，不再是硬编码 dark
2. 主题脚本在 <head>、早于 <body>（晚一步就是先把深色画出来再改）
3. 主题脚本带 CSP nonce（否则整块被拦，等于没有这段脚本）
4. 前后两端兜底逻辑一致：都按 prefers dark → dark，否则 light

顺带守住登录页版本行：四角随机确认生效后构建戳已移除，
别再挂回来（版本行的职责就是「品牌 + 版本」这一件事）。

⚠️ 所有对源码的文本检查都要**先剥掉注释**再查：注释里出现同样的词会让断言
假通过 —— 反向验证时退化测不出来。这个项目已经踩过好几次这个坑。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")


def read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def strip_comments(s):
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    return s


failed = []


def ck(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        failed.append(name)


index_raw = read(os.path.join(SRC, "templates", "index.html"))
index = strip_comments(index_raw)
login_raw = read(os.path.join(SRC, "templates", "login.html"))
login = strip_comments(login_raw)
webui = strip_comments(read(os.path.join(SRC, "webui.py")))
appjs = strip_comments(read(os.path.join(SRC, "static", "app.js")))

print("面板首页主题（日间模式不闪夜间）")

# --- 初始主题 ---
ck("初始主题来自服务端变量（不是硬编码 dark）",
   re.search(r'<html[^>]*data-theme="\{\{\s*theme\s*\}\}"', index_raw) is not None)
ck("index.html 里没有硬编码 data-theme=\"dark\"",
   re.search(r'<html[^>]*data-theme="dark"', index_raw) is None)
m_idx = re.search(r'render_template\(\s*"index\.html"(.{0,240})', webui, flags=re.S)
ck("webui 的首页渲染传了 theme",
   m_idx is not None and re.search(r"theme\s*=", m_idx.group(1)) is not None)
ck("theme 取自 _theme_from_cookie",
   re.search(r'theme\s*=\s*_theme_from_cookie\(\)', webui) is not None)

# --- 脚本位置与 CSP ---
ck("主题脚本在 <body> 之前",
   index.index("setAttribute('data-theme'") < index.index("<body"))
ck("主题脚本带 CSP nonce",
   re.search(r'<script[^>]*nonce="\{\{\s*csp_nonce\s*\}\}"[^>]*>\s*\(function\s*\(\)\s*\{\s*var\s+t\s*=\s*null;\s*'
             r"try\s*\{\s*t\s*=\s*localStorage\.getItem\('bili-theme'\)", index) is not None)

# --- 两端兜底一致 ---
ck("首页兜底按 prefers dark → dark",
   re.search(r"prefers-color-scheme:\s*dark\)'\)\.matches\)\s*\?\s*'dark'\s*:\s*'light'",index) is not None)
ck("app.js 兜底按 prefers dark → dark",
   re.search(r"prefers-color-scheme:\s*dark\)'\)\.matches\)\s*\?\s*'dark'\s*:\s*'light'",appjs) is not None)
ck("登录页兜底与首页一致（跨页不会一深一浅）",
   re.search(r"prefers-color-scheme:\s*dark\)'\)\.matches\)\s*\?\s*'dark'\s*:\s*'light'",login) is not None)

# --- 登录页版本行 ---
ck("登录页版本行不再挂构建戳",
   re.search(r'<div class="login-ver">\s*OnOiduts工业 v\{\{ app_version \}\}\s*</div>',
             login_raw) is not None)

print("")
if failed:
    print("FAILED %d: %s" % (len(failed), ", ".join(failed)))
    sys.exit(1)
print("全部通过")
