#!/usr/bin/env python3
# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""v2.2.0「把没用的都移掉」清理专项测试。

这一版删掉了三类东西：
  1. 扫码登录整条链路（B 站扫码接口常被风控，返回残缺 cookie）
  2. 零引用的死代码（函数、类、常量、HTTP 接口）
  3. 混淆工具（决定只发源码可读版）

⚠️ 这些测试的目的不是"验证功能能用"，而是**防止它们被加回来**。
   重构时最容易犯的错就是：翻到旧代码/旧文档，以为"这是遗失的功能"，
   于是把已经判死的东西重新写回去。所以每一条都要能反向验证。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _src(name):
    return _read(os.path.join(SRC, name))


def _strip_comments(src_text):
    """去掉注释与文档字符串，只留下真正的代码。

    ⚠️ 为什么必须做这一步：
    删掉的东西往往要在注释里交代「为什么删、以后别加回来」。
    如果测试直接全文匹配 "qrcode/poll"，那些解释性注释会被误判成残留代码，
    结果要么逼着人把说明写含糊，要么测试永远红。所以只检查代码本体。
    """
    import ast
    import io
    import tokenize

    # 1) tokenize：把所有 # 注释换成空
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src_text).readline):
            if tok.type == tokenize.COMMENT:
                continue
            out.append(tok)
        # tokenize 无法直接还原源码，改用行号精确处理
    except Exception:
        return src_text

    lines = src_text.splitlines()
    for tok in out:
        if tok.type == tokenize.COMMENT:
            continue
    # 逐行剔除注释（只在 # 不在字符串内的简单情形，够用且安全）
    cleaned = []
    for line in lines:
        # 粗略但稳妥：找第一个 #，若其左侧引号成对则视为注释
        idx = line.find("#")
        while idx != -1:
            left = line[:idx]
            if left.count('"') % 2 == 0 and left.count("'") % 2 == 0:
                line = left
                break
            idx = line.find("#", idx + 1)
        cleaned.append(line)
    src_text = "\n".join(cleaned)

    # 2) ast：剥掉模块 / 类 / 函数的 docstring
    try:
        tree = ast.parse(src_text)
    except SyntaxError:
        return src_text
    drops = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(
                    getattr(first, "value", None), ast.Constant) and \
                    isinstance(first.value.value, str):
                drops.append((first.lineno, first.end_lineno))
    ls = src_text.split("\n")
    for a, b in sorted(set(drops), reverse=True):
        del ls[a - 1:b]
    return "\n".join(ls)


def _code(name):
    """读源码并剥掉注释与文档字符串 —— 只看真正的代码。"""
    return _strip_comments(_src(name))


# ─────────────────────────────────────────────────────────────
# 一、扫码登录整条链路必须消失
# ─────────────────────────────────────────────────────────────

def test_no_qr_in_bili_login():
    """bili_login.py 里不许再有二维码生成 / 扫码器 / 扫码常量。"""
    s = _code("bili_login.py")
    for frag in ("def make_qr_png(", "def make_qr_ascii(",
                 "def _png_from_matrix(", "class BiliLogin:",
                 "QR_GENERATE", "QR_POLL", "--qr",
                 "qrcode/generate", "qrcode/poll"):
        assert frag not in s, "bili_login.py 仍含 %s" % frag
    # 浏览器登录这条正路必须还在
    assert "async def cmd_browser_login(" in s, "浏览器登录入口被误删"


def test_no_qr_route_in_webui():
    """webui.py 里不许再有扫码路由（连 deprecated 桩也不要）。"""
    s = _code("webui.py")
    for frag in ("api/bili/qrcode", "api/bili/poll",
                 "def api_bili_qrcode(", "def api_bili_poll(",
                 "_start_bili_login", "_do_bili_login"):
        assert frag not in s, "webui.py 仍含 %s" % frag
    # 浏览器登录的三个接口必须还在
    for frag in ("api/bili/browser-login", "api/bili/browser-cancel",
                 "api/bili/browser-status"):
        assert frag in s, "浏览器登录接口 %s 被误删" % frag


def test_frontend_has_no_qr_call():
    """前端不许再调用扫码接口，也不该再检查 qrcode 依赖。"""
    js = ""
    for name in ("app.js", "sound.js"):
        p = os.path.join(SRC, "static", name)
        if os.path.isfile(p):
            js += _read(p)
    assert "bili/qrcode" not in js, "前端仍在调用 /api/bili/qrcode"
    assert "bili/poll" not in js, "前端仍在调用 /api/bili/poll"
    # 依赖检查里不该再把 qrcode 列为可选项
    assert "'qrcode'" not in js, "前端仍在检查 qrcode 依赖（它已从依赖清单移除）"


def test_qr_deps_gone():
    """qrcode / Pillow 随扫码登录一起从依赖清单移除。"""
    opt = _src("requirements-optional.txt")
    for line in opt.splitlines():
        body = line.split("#", 1)[0].strip()
        assert not body.lower().startswith("qrcode"), "qrcode 仍在可选依赖里"
        assert not body.lower().startswith("pillow"), "Pillow 仍在可选依赖里"
    # playwright（浏览器登录）必须还在
    assert "playwright" in opt, "playwright 被误删，浏览器登录会废掉"
    # 主依赖里也不该有它们
    main = _src("requirements.txt")
    for line in main.splitlines():
        body = line.split("#", 1)[0].strip()
        assert not body.lower().startswith("qrcode"), "qrcode 仍在主依赖里"
        assert not body.lower().startswith("pillow"), "Pillow 仍在主依赖里"


# ─────────────────────────────────────────────────────────────
# 二、零引用死代码必须消失
# ─────────────────────────────────────────────────────────────

def test_dead_functions_gone():
    """v2.2.0 删掉的这批零引用函数不许再出现。"""
    cases = {
        "bilibili.py": ["class BiliAuthError"],
        "bili_login.py": ["class BiliLoginError", "async def check_cookie("],
        "db.py": ["class _FileLock", "def _FileLock("],
        "media.py": ["def _load_index(", "def _save_index(", "INDEX_PATH ="],
        "webui.py": ["def _no_cache(", "def inject_version("],
        "bot.py": ["def _parse_uid("],
        "check.py": ["def ask("],
        "paths.py": ["def app_file(", "def src_file(", "def docs_file(",
                     "def deploy_dir(", "def cache_dir("],
    }
    for fname, frags in cases.items():
        s = _src(fname)
        for frag in frags:
            assert frag not in s, "%s 仍含死代码 %s" % (fname, frag)


def test_member_role_dead_code_gone():
    """qqapi 的 get_member_role 系列已删（官方接口内邀制，永远取不到）。"""
    s = _src("qqapi.py")
    for frag in ("async def get_member_role(", "def get_member_role_detail("):
        assert frag not in s, "qqapi.py 仍含 %s" % frag
    # 群信息接口（能用的那个）必须还在
    assert "async def get_group" in s or "/v2/groups/" in s, \
        "群信息接口被误删"


# ─────────────────────────────────────────────────────────────
# 三、混淆工具必须消失（只发源码可读版）
# ─────────────────────────────────────────────────────────────

def test_obfuscator_gone():
    """tools/obfuscate.py 已删，文档也不该再教人用它。"""
    assert not os.path.exists(os.path.join(ROOT, "tools", "obfuscate.py")), \
        "混淆工具又回来了 —— 本项目只发源码可读版"
    use = _read(os.path.join(ROOT, "docs", "使用说明.md"))
    assert "python tools/obfuscate.py" not in use, \
        "使用说明仍在教人跑混淆工具"


# ─────────────────────────────────────────────────────────────
# 四、面板口令环境变量必须真的生效
# ─────────────────────────────────────────────────────────────

def test_panel_password_env_works():
    """.env.example 承诺了 ONOBN_PANEL_PASSWORD，代码就必须真的读它。

    ⚠️ 这一条之前是空头承诺：envcfg 里有读取函数、文档里写了配置项，
       但没有任何地方调用它 —— 照着填了完全不生效。
    """
    import tempfile
    import shutil
    saved = os.environ.get("ONOBN_DATA_DIR")
    saved_pw = os.environ.get("ONOBN_PANEL_PASSWORD")
    tmp = tempfile.mkdtemp()
    sys.path.insert(0, SRC)
    try:
        os.environ["ONOBN_DATA_DIR"] = tmp
        os.environ.pop("ONOBN_PANEL_PASSWORD", None)
        for m in ("cfgutil", "security", "envcfg", "paths"):
            sys.modules.pop(m, None)
        import cfgutil
        import security

        assert not cfgutil.load_cfg().get("web_password"), \
            "没设环境变量时不该凭空冒出口令"

        os.environ["ONOBN_PANEL_PASSWORD"] = "testpanelpw"
        # envcfg 有缓存标记，需要重新加载
        sys.modules.pop("envcfg", None)
        cfg = cfgutil.load_cfg()
        pw = cfg.get("web_password")
        assert pw, "设了 ONOBN_PANEL_PASSWORD 却没生效"
        assert cfg.get("_web_password_from_env"), "缺少来自环境变量的标记"
        assert security.verify_password("testpanelpw", pw), "正确口令没通过"
        assert not security.verify_password("wrong", pw), "错误口令反而通过了"
        assert "testpanelpw" not in str(pw), "口令明文出现在哈希里？"

        # 保存后不得写回文件（口令不落盘）
        cfgutil.save_cfg(cfg)
        disk = ""
        if os.path.exists(cfgutil.cfg_path()):
            disk = _read(cfgutil.cfg_path())
        assert "testpanelpw" not in disk, "环境变量里的口令被写进了 config.yaml"
        assert "web_password" not in disk, "口令字段被写进了 config.yaml"
    finally:
        for m in ("cfgutil", "security", "envcfg", "paths", "secretbox"):
            sys.modules.pop(m, None)
        if saved is None:
            os.environ.pop("ONOBN_DATA_DIR", None)
        else:
            os.environ["ONOBN_DATA_DIR"] = saved
        if saved_pw is None:
            os.environ.pop("ONOBN_PANEL_PASSWORD", None)
        else:
            os.environ["ONOBN_PANEL_PASSWORD"] = saved_pw
        shutil.rmtree(tmp, ignore_errors=True)


def test_env_example_documents_password():
    """.env.example 里写了这项，代码就该支持；反过来也别写不支持的项。"""
    ex = _read(os.path.join(ROOT, ".env.example"))
    assert "ONOBN_PANEL_PASSWORD" in ex, ".env.example 缺少口令配置项"
    # 占位符，不是真值
    assert "your_app_secret_here" in ex or "your_" in ex, \
        ".env.example 应当只有占位符"
    for bad in ("SESSDATA=", "bili_jct="):
        assert bad not in ex, ".env.example 里出现了真实凭据形态：%s" % bad


# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    fails = 0
    total = 0
    for k in sorted(globals()):
        v = globals()[k]
        if k.startswith("test_") and callable(v):
            total += 1
            try:
                v()
                print("  PASS %s" % k)
            except AssertionError as e:
                fails += 1
                print("  FAIL %s: %s" % (k, e))
            except Exception as e:
                fails += 1
                print("  ERROR %s: %s: %s" % (k, type(e).__name__, e))
    print("v2.2.0 清理专项：%d 项，失败 %d" % (total, fails))
    sys.exit(1 if fails else 0)
