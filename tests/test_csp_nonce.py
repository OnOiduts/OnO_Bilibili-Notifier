# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""CSP 随机数（nonce）守护。

为什么要单独盯这一条：面板的 CSP 写的是 script-src 'self'，
**不含** 'unsafe-inline'。于是模板里所有 <script>...</script> 内联块、
以及 onerror= 这类内联事件属性，都会被浏览器**全部拦掉**。

这个故障极其隐蔽：页面打得开、样式也在（style-src 单独放了
'unsafe-inline'），服务端日志一切正常，但所有 JS 一行都不执行 ——
表现出来就是"按钮点了没反应""背景不随机""图片降级链没生效"，
看着像用户没刷新，实际是浏览器主动拒绝执行。

正确做法不是放开 'unsafe-inline'（那等于 CSP 对脚本形同虚设），
而是每次响应生成一个不可预测的随机数，响应头与模板两边对上才执行。

下面每条断言都做过反向验证：把代码退化回旧写法，断言确实变红。
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
TPL = os.path.join(SRC, "templates")
sys.path.insert(0, SRC)
sys.path.insert(0, BASE)

FAILS = []


def ok(cond, name, detail=""):
    if cond:
        print("  PASS  " + name)
    else:
        FAILS.append(name)
        print("  FAIL  " + name + ("  -> " + detail if detail else ""))


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _real_inline_handlers(html):
    """真正的内联事件属性 —— 排除注释里提到的字样。"""
    out = []
    for line in html.split("\n"):
        s = line.strip()
        # 跳过 HTML 注释、JS 块注释与行注释
        if s.startswith("<!--") or s.startswith("/*") or s.startswith("*"):
            continue
        if "<!--" in s and s.index("<!--") < (s.find("on") if "on" in s else 1e9):
            continue
        # ⚠️ 必须允许行首：属性常出现在续行开头（前面没有空格），
        #    只写 \son 会漏掉，退回旧写法也就测不出来。
        for m in re.findall(r"(?:^|\s)on[a-z]+=", s):
            out.append(m.strip())
    return out


# ── 1. 源码层面：策略本身 ──────────────────────────────────
harden = _read(os.path.join(SRC, "harden.py"))

ok("script-src" in harden, "策略里有 script-src")
# 脚本策略绝不能放 'unsafe-inline'：放了就等于所有内联脚本都能跑，
# 注入的脚本也能跑，这一层防护就没了。
# ⚠️ 只看 _csp_header() 的函数体：上面那段说明文字里也出现了
#    script-src / unsafe-inline 的字样，全文件裸匹配会被注释顶住 ——
#    结果是策略真的放开了 unsafe-inline 也测不出来。
_i = harden.index("def _csp_header")
_body = harden[_i:harden.index("def ", _i + 10)] if "def " in harden[_i + 10:] else harden[_i:]
_body = "\n".join(l for l in _body.split("\n") if not l.strip().startswith("#"))
m = re.search(r"script-src\s+'self'([^;]*)", _body)
ok(m is not None, "能定位脚本策略片段")
ok(m and "'unsafe-inline'" not in m.group(1),
   "脚本策略不含 'unsafe-inline'",
   m.group(1) if m else "")
ok(m and "nonce-" in m.group(1),
   "脚本策略带随机数",
   m.group(1) if m else "")
ok("secrets.token_urlsafe" in harden,
   "随机数用加密安全生成器（不是 random，猜不到）")

# 上下文变量必须是**调用结果**，不是函数对象
ok(re.search(r"\"csp_nonce\":\s*csp_nonce\(\)", harden) is not None,
   "模板变量传的是调用结果而非函数本身")


# ── 2. 模板层面：每个内联 script 都得带 nonce ──────────────
for name in ("login.html", "index.html"):
    html = _read(os.path.join(TPL, name))
    tags = re.findall(r"<script[^>]*>", html)
    inline = [t for t in tags if "src=" not in t]
    bare = [t for t in inline if "nonce=" not in t]
    ok(len(inline) > 0, name + " 有内联脚本", "")
    ok(not bare, name + " 的内联脚本都带 nonce", str(bare))
    ok(all('nonce="{{ csp_nonce }}"' in t for t in inline),
       name + " nonce 取值写法正确", str(inline))
    ok(not _real_inline_handlers(html),
       name + " 没有内联事件属性（会被拦）",
       str(_real_inline_handlers(html)))

# 图片降级链不能靠内联 onerror —— 改用捕获阶段监听
login = _read(os.path.join(TPL, "login.html"))
ok("document.addEventListener('error'" in login,
   "登录页用捕获阶段监听接管图片降级")
ok(re.search(r"data-fb=\"[^\"]+\"", login) is not None,
   "降级地址写在 data 属性上（不是内联事件）")

# 面板前端同样不能靠内联 onerror
appjs = _read(os.path.join(SRC, "static", "app.js"))
ok("document.addEventListener('error'" in appjs,
   "面板前端用捕获阶段监听处理图片失败")
ok(not re.search(r'onerror="this\.remove\(\)"', appjs),
   "面板前端已无内联 onerror 属性")


# ── 3. 运行层面：头里的随机数与模板里的必须一致 ────────────
try:
    os.environ.setdefault("ONOBN_DATA_DIR", "/tmp/_csp_nonce_t")
    import webui
    import harden as _h
    from flask import render_template

    app = webui.app
    _h.init_app(app)

    with app.test_request_context("/login"):
        page = render_template("login.html", **webui._login_ctx())
        nonce = _h.csp_nonce()
    n_inline = len([t for t in re.findall(r"<script[^>]*>", page)
                    if "src=" not in t])
    ok(page.count(nonce) == n_inline,
       "模板里的随机数与本次请求一致且覆盖全部内联块",
       "出现 %d 次 / 内联块 %d 个" % (page.count(nonce), n_inline))
    ok(nonce and len(nonce) >= 16, "随机数长度足够（不可猜）", str(nonce))
    ok("<function" not in page, "没有把函数对象渲染进 nonce")

    # 每次请求都换一个
    with app.test_request_context("/login"):
        render_template("login.html", **webui._login_ctx())
        n2 = _h.csp_nonce()
    ok(nonce != n2, "相邻两次请求的随机数不同")

    # 真实响应头里也要带上
    app.config["TESTING"] = True
    c = app.test_client()
    r = c.get("/")
    csp = r.headers.get("Content-Security-Policy", "")
    mm = re.search(r"'nonce-([^']+)'", csp)
    ok(mm is not None, "响应头带随机数", csp)
    if mm:
        body = r.get_data(as_text=True)
        ok(body.count(mm.group(1)) >= 1,
           "响应头随机数能在页面里找到",
           "%s / %d" % (mm.group(1), body.count(mm.group(1))))
except Exception as e:                                   # pragma: no cover
    ok(False, "运行层面校验", "%s: %s" % (type(e).__name__, e))


print("")
if FAILS:
    print("FAILED %d: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("全部通过")
