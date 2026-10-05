# -*- coding: utf-8 -*-
"""登录页：背景装饰图 + 主题跟随。

守住三件事：
1. 背景除 logo/ico 外还有 bg1/bg2 两张装饰图（嫌单调才加的，别被当冗余删掉）
2. 主题脚本必须在 <head>，且执行位置早于 <body>
3. 初始主题由服务端按 cookie 决定，不再是硬编码 dark

⚠️ 所有对源码的文本检查都要**先剥掉注释**再查：注释里出现同样的词会让断言
假通过 —— 反向验证时退化测不出来。这个项目已经踩过三次这个坑。
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


login_raw = read(os.path.join(SRC, "templates", "login.html"))
login = strip_comments(login_raw)
webui = strip_comments(read(os.path.join(SRC, "webui.py")))
appjs = strip_comments(read(os.path.join(SRC, "static", "app.js")))
envcfg = strip_comments(read(os.path.join(SRC, "envcfg.py")))

print("登录页背景装饰图与主题跟随")

# --- 背景图 ---
ck("login.html 有 bg1 图", 'id="bg1"' in login and "{{ bg1_svg }}" in login)
ck("login.html 有 bg2 图", 'id="bg2"' in login and "{{ bg2_svg }}" in login)
ck("bg1/bg2 有各自样式类", ".bg-c" in login and ".bg-d" in login)
ck("装饰图动画时长错开",
   re.search(r"\.bg-c\s*\{[^}]*animation-duration:\s*29s", login) is not None
   and re.search(r"\.bg-d\s*\{[^}]*animation-duration:\s*26s", login) is not None)

# --- 主题 ---
ck("初始主题来自服务端变量（不是硬编码 dark）",
   re.search(r'<html[^>]*data-theme="\{\{\s*theme\s*\}\}"', login_raw) is not None)
ck("主题脚本在 <body> 之前",
   login.index("setAttribute('data-theme'") < login.index("<body"))
ck("webui 有 _theme_from_cookie", "_theme_from_cookie" in webui)
ck("主题 cookie 名为 bili-theme",
   '"bili-theme"' in webui or "'bili-theme'" in webui)
ck("面板切主题时写 cookie",
   "document.cookie" in appjs and "bili-theme=" in appjs)

# --- 后端与环境变量 ---
ck("envcfg 有 bg1/bg2 环境变量",
   "ONOBN_BG1_URL" in envcfg and "ONOBN_BG2_URL" in envcfg)
ck("envcfg 有 bg1/bg2 读取函数",
   "def bg1_url_from_env" in envcfg and "def bg2_url_from_env" in envcfg)
ck("webui 有 bg1/bg2 默认地址",
   "BG1_URL_DEFAULT" in webui and "Web/OnOBN/bg1.svg" in webui
   and "BG2_URL_DEFAULT" in webui and "Web/OnOBN/bg2.svg" in webui)
ck("登录页上下文传了 bg1/bg2 与 theme",
   '"bg1_svg"' in webui and '"bg2_svg"' in webui and '"theme"' in webui)

print("")
if failed:
    print("FAILED %d: %s" % (len(failed), ", ".join(failed)))
    sys.exit(1)
print("全部通过")
