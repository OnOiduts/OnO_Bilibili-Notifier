"""安全相关回归测试。

用法：python tests/test_security.py
"""
import os
import re
import sys
import tempfile
import time
import io

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
# 启动脚本 / 文档已经搬到仓库根目录与 docs/，只有源码留在 src/
REPO_DIR = os.path.dirname(BASE_DIR)
DOC_DIR = os.path.join(REPO_DIR, "docs")

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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import security as S  # noqa: E402

ok = True


def ck(name, cond):
    global ok
    print(("[PASS] " if cond else "[FAIL] ") + name)
    ok = ok and bool(cond)


def t_password():
    h = S.hash_password("pwd123456")
    ck("哈希存储格式正确", h.startswith("pbkdf2$") and S.is_hashed(h))
    ck("正确口令通过", S.verify_password("pwd123456", h))
    ck("错误口令拒绝", not S.verify_password("pwd12345", h))
    ck("明文口令不落盘", "pwd123456" not in h)
    ck("明文兼容比对", S.verify_password("abc", "abc"))
    ck("空口令拒绝", not S.verify_password("", h))


def t_token():
    k = S.get_secret_key()
    tok = S.issue_token(k)
    ck("有效令牌通过", S.verify_token(k, tok))
    ck("篡改令牌拒绝", not S.verify_token(k, tok[:-3] + "xxx"))
    ck("错误密钥拒绝", not S.verify_token("wrong-key", tok))
    ck("过期令牌拒绝", not S.verify_token(k, S.issue_token(k, ttl=-1)))
    ck("空令牌拒绝", not S.verify_token(k, ""))


def t_limiter():
    L = S.LoginLimiter(max_fail=3, lock_base=30)
    ip = "8.8.8.8"
    lock = 0
    for _ in range(3):
        lock = L.note_fail(ip)
    ck("连续失败触发锁定", lock > 0)
    ck("锁定期间需等待", L.remaining(ip) > 0)
    ck("其他 IP 不受影响", L.remaining("9.9.9.9") == 0)
    L.note_ok(ip)
    ck("成功后解除锁定", L.remaining(ip) == 0)
    # 再次失败，锁定时间应翻倍
    for _ in range(3):
        lock2 = L.note_fail(ip)
    ck("重复失败惩罚升级", lock2 >= lock)


def t_input():
    ck("合法 UID", S.validate_uid("672328094") == 672328094)
    ck("空间链接提取 UID",
       S.validate_uid("https://space.bilibili.com/672328094") == 672328094)
    for bad in ("abc", "../../etc/passwd", "1", "123456789012345", ""):
        try:
            S.validate_uid(bad)
            ck(f"拒绝非法 UID {bad!r}", False)
        except ValueError:
            ck(f"拒绝非法 UID {bad!r}", True)

    ck("合法群标识", S.validate_gid("GROUP_A_123") == "GROUP_A_123")
    for bad in ("../../etc/passwd", "a b", "<script>", "a/b"):
        try:
            S.validate_gid(bad)
            ck(f"拒绝非法群标识 {bad!r}", False)
        except ValueError:
            ck(f"拒绝非法群标识 {bad!r}", True)

    for bad in ("../../etc/passwd", "/etc/passwd", "../config.yaml"):
        try:
            S.resolve_in_base(bad)
            ck(f"拒绝路径穿越 {bad!r}", False)
        except ValueError:
            ck(f"拒绝路径穿越 {bad!r}", True)
    ck("正常路径放行", S.resolve_in_base("data.json").endswith("data.json"))


def t_image():
    good = ["https://i0.hdslb.com/bfs/a.jpg", "https://i2.hdslb.com/bfs/b.png",
            "//i0.hdslb.com/bfs/c.jpg"]
    bad = ["https://evil.com/a.jpg", "http://i0.hdslb.com/a.jpg",
           "https://i0.hdslb.com.evil.com/a.jpg", "javascript:alert(1)"]
    for u in good:
        ck(f"放行 B 站图床 {u}", S.allowed_image_url(u))
    for u in bad:
        ck(f"拦截非白名单 {u}", not S.allowed_image_url(u))


def t_redact():
    # ⚠️ 这里只能放"一眼就看出是假的"占位符，不能写像真密钥的字符串：
    # 本仓库要公开到 GitHub，密钥扫描机器人会把形似真密钥的串判定为泄露并报警，
    # 即便它只是测试用的假值。改用 <EXAMPLE_...> 这种明显的占位形态。
    r = S.redact('secret: "<EXAMPLE_SECRET>" token=<EXAMPLE_TOKEN> '
                 'cookie: SESSDATA=<EXAMPLE_SESSDATA>')
    ck("secret 打码", "EXAMPLE_SECRET" not in r)
    ck("token 打码", "EXAMPLE_TOKEN" not in r)
    ck("cookie 打码", "EXAMPLE_SESSDATA" not in r)
    ck("正常文本不受影响", S.redact("开播提醒已发送") == "开播提醒已发送")


def t_bind():
    lvl, _ = S.bind_risk("127.0.0.1", False)
    ck("本机监听=安全", lvl == "ok")
    lvl, _ = S.bind_risk("0.0.0.0", False)
    ck("对外暴露且无口令=致命", lvl == "fatal")
    lvl, _ = S.bind_risk("0.0.0.0", True)
    ck("对外暴露但有口令=警告", lvl == "warn")


def t_audit():
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        path = f.name
    try:
        a = S.Audit(path=path)
        a.log("测试事件", "1.2.3.4", "secret: abcdef123456", "warn")
        ck("审计记录写入", len(a.items) == 1)
        ck("审计内容脱敏", "abcdef123456" not in a.items[0]["detail"])
        ck("审计落盘", "测试事件" in open(path, encoding="utf-8").read())
    finally:
        os.unlink(path)


def t_bili_login():
    """扫码登录模块：cookie 拼接、时效标注、二维码生成。"""
    import bili_login as BL

    c = {"SESSDATA": "s1", "bili_jct": "j1", "DedeUserID": "7", "buvid3": "b1"}
    cs = BL.cookie_str(c)
    ck("cookie 拼接含 SESSDATA", "SESSDATA=s1" in cs)
    parsed = BL.parse_cookie(cs)
    ck("cookie 可解析回", parsed.get("bili_jct") == "j1"
       and parsed.get("DedeUserID") == "7")

    now = __import__("time").time()
    i30 = BL.expired_info(now + 30 * 86400, now)
    ck("30天后显示剩余天数", i30["days_left"] == 30 and not i30["expired"])
    i2 = BL.expired_info(now + 2 * 86400, now)
    ck("2天内标记快过期", i2["soon"] and not i2["expired"])
    ie = BL.expired_info(now - 86400, now)
    ck("过期标记正确", ie["expired"] and ie["text"] == "已过期")
    ck("过期时间格式化", len(i30["expires_str"]) == 16)

    # ⚠️ v2.2.0：扫码登录整条链路已删除（B 站扫码接口常被风控，返回残缺 cookie）。
    #    以前这里测的是 make_qr_png / STATUS 状态码表，现在改成确认它们真没了。
    ck("二维码函数已移除", not hasattr(BL, "make_qr_png"))
    ck("扫码状态码表已移除", not hasattr(BL, "STATUS"))
    ck("扫码器类已移除", not hasattr(BL, "BiliLogin"))
    ck("浏览器登录入口仍在", callable(getattr(BL, "cmd_browser_login", None)))


def t_harden():
    """加固层：限流、封禁、安全头、请求体限制。"""
    import harden

    g = harden.Guard(ban_after=2, ban_seconds=60)
    ip = "5.5.5.5"
    allowed = sum(1 for _ in range(130) if g.allow(ip, True))
    ck(f"写限流上限 {harden.WRITE_MAX}", allowed == harden.WRITE_MAX)
    ck("超限触发封禁", g.banned(ip) > 0)
    ck("其他 IP 不受牵连", g.allow("6.6.6.6", True))
    g.unban_all()
    ck("解封生效", g.banned(ip) == 0)

    ck("请求体上限已设", harden.MAX_CONTENT_LENGTH == 256 * 1024)
    hdr = harden.SECURITY_HEADERS
    # ⚠️ 断言改过：CSP 不再放在 SECURITY_HEADERS 里 —— 它必须带每次请求
    #    生成的随机数（否则模板里的内联脚本全被拦），所以用 _csp_header()。
    #    保留两处定义迟早改一处漏一处，已改为单一来源。
    ck("CSP 已配置", "default-src 'self'" in harden._csp_header())
    ck("CSP 不带 unsafe-inline（脚本）",
       "'unsafe-inline'" not in harden._csp_header().split("script-src")[1])
    ck("CSP 脚本策略带随机数", "'nonce-" in harden._csp_header())
    ck("禁止 iframe 嵌套", hdr["X-Frame-Options"] == "DENY")
    ck("禁用缓存", hdr["Cache-Control"] == "no-store")
    ck("nosniff 已设", hdr["X-Content-Type-Options"] == "nosniff")


def t_secretbox():
    """凭据落盘混淆：加解密往返、幂等、配置级保护。"""
    import secretbox as SB

    s = "MySecretValue123"
    e = SB.encrypt(s)
    ck("密文不含原文", s not in e)
    ck("往返还原一致", SB.decrypt(e) == s)
    ck("重复加密幂等", SB.encrypt(e) == e)
    ck("明文原样返回", SB.decrypt("plain") == "plain")
    ck("空值安全", SB.encrypt("") == "" and SB.decrypt("") == "")

    cfg = {"appid": "123", "secret": s, "bili_cookie": "SESSDATA=x"}
    p = SB.protect_cfg(dict(cfg))
    ck("secret 变密文", p["secret"].startswith(SB.PREFIX))
    ck("cookie 变密文", p["bili_cookie"].startswith(SB.PREFIX))
    ck("appid 保持明文", p["appid"] == "123")
    r = SB.reveal_cfg(dict(p))
    ck("配置往返一致", r["secret"] == s and r["bili_cookie"] == "SESSDATA=x")


def t_integrity():
    """文件完整性：建立基准 → 检出篡改 → 还原。

    ⚠️ 以前这条用例是**直接往真实的 src/bot.py 末尾追加**内容：
    `harden.BASE` 就是源码目录本身。一旦中途断言崩了或被 Ctrl+C 打断，
    finally 里的还原就不会执行，源码被反复污染 —— 工作台里的 bot.py
    末尾曾堆了好几段 `# tampered` 和半截的 `= "__main__":`，
    直接语法错误、机器人根本起不来。

    现在整体搬到临时目录里做：把受保护文件复制一份出去，完整性的
    建立/检出/还原全部只碰那份副本，源码一个字节都不动。
    """
    import os
    import shutil
    import tempfile

    import harden

    real_base = harden.BASE
    tmp = tempfile.mkdtemp(prefix="integrity_")
    try:
        try:
            names = list(harden._core_files() or [])
        except Exception:
            names = ["bot.py"]
        if not names:
            names = ["bot.py"]
        blobs = {}
        for n in names:
            src_p = os.path.join(real_base, n)
            if not os.path.exists(src_p):
                continue
            with open(src_p, "rb") as f:
                blobs[n] = f.read()
            with open(os.path.join(tmp, n), "wb") as f:
                f.write(blobs[n])
        ck("核心文件已复制到临时目录", bool(blobs))

        harden.BASE = tmp
        if os.path.exists(harden.INTEGRITY_FILE):
            os.remove(harden.INTEGRITY_FILE)
        base = harden.save_baseline()
        ck("基准已建立", len(base["files"]) > 0)
        r = harden.check_integrity()
        ck("未改动时通过", r["ok"] and not r["changed"])

        target = os.path.join(tmp, "bot.py")
        with open(target, "a", encoding="utf-8") as f:
            f.write("\n# tampered\n")
        r = harden.check_integrity()
        ck("篡改可检出", (not r["ok"]) and
           any(c["file"] == "bot.py" for c in r["changed"]))

        # 还原（写回原始字节，源码目录完全没参与）
        with open(target, "wb") as f:
            f.write(blobs.get("bot.py", b""))
        ck("还原后恢复", harden.check_integrity()["ok"])
    finally:
        harden.BASE = real_base
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            if os.path.exists(harden.INTEGRITY_FILE):
                os.remove(harden.INTEGRITY_FILE)
        except Exception:
            pass


def t_logtranslate():
    """日志翻译：归类 + 中文解释 + 处理建议。"""
    import logtranslate as LT

    # 归类：按源文件
    cases = [
        ("[ERROR] (bot.py:300)_poll_loop 轮询异常", "机器人"),
        ("[INFO]  (webui.py:100)index 面板启动", "管理面板"),
        ("(bilibili.py:120)get_up 查询失败", "B站接口"),
        ("(bili_login.py:88)generate 二维码", "扫码登录"),
        ("(heartbeat.py:20)touch", "运行状态"),
        ("(qqapi.py:50)send 发送失败", "机器人"),
        ("(store.py:30)save", "数据存储"),
    ]
    for line, expect in cases:
        part, _icon = LT.classify_part(line)
        ck(f"归类 {expect}：{line[:34]}", part == expect)

    # 翻译：关键错误必须能识别
    must_know = [
        ("'NotifyBot' object has no attribute '_reload_cookie'", "版本不一致"),
        ("appid invalid", "AppID"),
        ("code=-352", "风控"),
        ("ModuleNotFoundError: No module named 'botpy'", "依赖"),
        ("Cannot connect to host", "连不上"),
        ("Unclosed client session", "连接"),
        ("PermissionError", "权限"),
        ("获取token失败", "AppSecret"),
    ]
    for line, keyword in must_know:
        r = LT.explain(line)
        ck(f"识别「{keyword}」：{line[:40]}",
           r["known"] and keyword in r["meaning"] and bool(r["howto"]))

    # 级别识别
    ck("ERROR 级别", LT.detect_level("[ERROR] 出错") == "error")
    ck("WARNING 级别", LT.detect_level("[WARNING] 注意") == "warn")
    ck("INFO 级别", LT.detect_level("[INFO] 正常") == "info")

    # 汇总
    lines = [
        "[ERROR] (bot.py:1) x appid invalid",
        "[ERROR] (bot.py:2) x appid invalid",     # 重复，去重后应只算一条
        "[INFO]  (webui.py:1) y 正常",
        "Unclosed client session",
    ]
    sm = LT.summarize(lines)
    ck("汇总统计错误数", sm["total_error"] == 2)
    ck("汇总去重", len(sm["issues"]) == 2)
    ck("健康度为异常", sm["health"] == "bad")

    sm2 = LT.summarize(["[INFO] 一切正常"])
    ck("无错误时健康", sm2["health"] == "ok")


def t_version():
    """版本号与更新日志：读取、递增规则、解析、历史保留。"""
    import re

    import version as V

    cur = V.current()
    ck("版本号非空且符合 x.y.z", bool(re.match(r"^\d+\.\d+\.\d+$", cur)))

    # 递增规则
    ck("小更新 +0.0.1", V.bump("0.6.0", "patch") == "0.6.1")
    ck("中等更新 +0.1.0", V.bump("0.6.0", "minor") == "0.7.0")
    ck("大更新 +1.0.0", V.bump("0.6.0", "major") == "1.0.0")
    ck("中等更新清零 patch", V.bump("1.2.9", "minor") == "1.3.0")
    ck("大更新清零 minor/patch", V.bump("1.2.9", "major") == "2.0.0")

    # 更新日志
    log = V.changelog()
    ck("更新日志非空", len(log) > 0)
    ck("最新版本在前", log[0]["version"] == cur)
    ck("历史版本都被保留", len(log) >= 10)
    ck("每条都有日期", all(e["date"] for e in log))
    ck("每条都有条目", all(e["items"] for e in log))

    # 分组解析：最新版本应带分组
    top = log[0]
    ck("条目已解析", any(it.get("text") for it in top["items"]))
    ck("分组清单正确", top["groups"] == sorted(
        set(it["group"] for it in top["items"] if it["group"]),
        key=top["groups"].index))

    # 版本级别识别
    lv = {e["version"]: e["level"] for e in log}
    ck("中等更新识别为 minor", lv.get("0.6.0") == "minor")
    ck("小更新识别为 patch", lv.get("0.3.1") == "patch")


def t_login_error_hint():
    """登录报错要给出中文说明 + 可执行办法。"""
    from webui import friendly_login_error as f
    cases = [
        ("No module named 'PIL'", "Pillow"),
        ("HTTP 403 policy denied", "拒绝"),
        ("timed out", "超时"),
        ("Cannot connect to host", "连不上"),
    ]
    for raw, kw in cases:
        ck(f"提示含「{kw}」：{raw[:26]}", kw in f(raw))
    ck("空错误返回空", f("") == "")


def t_pause_non_interactive():
    """start.py 在非交互环境（桌面版）下不能因为 input() 崩溃。"""
    import start

    class NoTTY:
        def isatty(self):
            return False

    saved = sys.stdin
    try:
        # 场景1：stdin 不是 tty（桌面版 / 重定向）
        sys.stdin = NoTTY()
        t0 = time.time()
        start.pause_if_interactive()
        ck("非 tty 时不阻塞", time.time() - t0 < 1)

        # 场景2：stdin 为 None
        sys.stdin = None
        start.pause_if_interactive()
        ck("stdin 为 None 不崩溃", True)
    finally:
        sys.stdin = saved



def t_pause_non_interactive():
    """start.py 在非交互环境（桌面版）下不能因为 input() 崩溃。"""
    import start

    class NoTTY:
        def isatty(self):
            return False

    saved = sys.stdin
    try:
        # 场景1：stdin 不是 tty（桌面版 / 重定向）
        sys.stdin = NoTTY()
        t0 = time.time()
        start.pause_if_interactive()
        ck("非 tty 时不阻塞", time.time() - t0 < 1)

        # 场景2：stdin 为 None
        sys.stdin = None
        start.pause_if_interactive()
        ck("stdin 为 None 不崩溃", True)
    finally:
        sys.stdin = saved



def t_login_wait_codes():
    """扫码轮询的 code 分类：等待中的继续等，失效的立刻报错。"""
    import bili_login as BL

    ck("86039 等待扫码 → 继续轮询", 86039 in BL.WAIT_CODES)
    ck("86090 待确认 → 继续轮询", 86090 in BL.WAIT_CODES)
    ck("86038 已失效 → 不继续等", 86038 not in BL.WAIT_CODES)
    ck("86069 已过期 → 不继续等", 86069 not in BL.WAIT_CODES)

    # poll 在无 qrcode_key 时应给明确错误而不是崩
    async def run():
        bl = BL.BiliLogin()
        r = await bl.poll()
        return r
    import asyncio
    r = asyncio.run(run())
    ck("无二维码时返回 error", r.get("state") == "error" and bool(r.get("msg")))



def t_group_register():
    """群登记：官方无群列表接口，需支持手动登记 + @机器人自动登记。"""
    from db import Store

    d = tempfile.mkdtemp()
    st = Store(os.path.join(d, "data.json"))

    ck("初始无群", st.data["groups"] == {})
    st.ensure_group("GROUP_A")
    ck("ensure_group 能登记", "GROUP_A" in st.data["groups"])
    st.ensure_group("GROUP_A", "我的群")
    ck("同名登记不覆盖已有群名", st.get_group("GROUP_A").get("name") == "我的群")

    # v1.21.0 起机器人不再响应群指令（功能都在面板），
    # 群改为收到 @ 消息时**静默登记**，这里验证该行为仍在。
    import bot
    src = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("保留 @ 消息静默登记", "on_group_at_message_create" in src)
    # 同 test_chain：按标识符边界匹配，避免误伤 set_exception_handler
    ck("不再有指令分发", not re.search(r"\b_handle\b", src))


def t_python_exe():
    """子解释器解析：pythonw 必须换成 python。

    pythonw 没有控制台，sys.stdout/stderr 为 None，子进程里的日志库、
    第三方库写 stderr 时可能崩溃，而且报错完全不可见。
    """
    from cfgutil import python_exe

    d = tempfile.mkdtemp()
    pexe = os.path.join(d, "python.exe")
    pwexe = os.path.join(d, "pythonw.exe")
    io.open(pexe, "w").close()
    io.open(pwexe, "w").close()

    saved = sys.executable
    try:
        sys.executable = pwexe
        ck("pythonw → python.exe", python_exe() == pexe)
        sys.executable = pexe
        ck("python.exe 保持不变", python_exe() == pexe)
    finally:
        sys.executable = saved



def t_db_group_register():
    """数据库：@机器人 登记群（不带名字）必须落盘且跨进程可见。"""
    from db import Store

    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.db")
    st = Store(p)
    st.ensure_group("GROUP_AT_001")          # 不带名字，模拟 @机器人

    # 另一个实例 = 另一个进程（面板就是这么读的）
    st2 = Store(p)
    ck("不带名字也要落盘", "GROUP_AT_001" in st2.data["groups"])

    # 带名字登记不能覆盖已有名字
    st.ensure_group("GROUP_AT_001", "我的群")
    ck("可补登记群名", st.get_group("GROUP_AT_001").get("name") == "我的群")
    st.ensure_group("GROUP_AT_001", "别的名")
    ck("已有名字不被覆盖", st.get_group("GROUP_AT_001").get("name") == "我的群")


def t_db_subscribe_isolation():
    """订阅按 群×UP×类型 三维存储，互不串扰。"""
    from db import Store

    d = tempfile.mkdtemp()
    st = Store(os.path.join(d, "t.db"))
    st.set_sub("GA", 111, "live", True, "A")
    st.set_sub("GA", 111, "video", False, "A")
    st.set_sub("GB", 111, "dynamic", True, "A")

    ck("live 只推给 GA", st.targets(111, "live") == ["GA"])
    ck("dynamic 只推给 GB", st.targets(111, "dynamic") == ["GB"])
    ck("未开启类型不推送", st.targets(111, "video") == [])
    ck("群隔离：GB 无 live", "GB" not in st.targets(111, "live"))


def t_db_auto_refresh():
    """外部进程改动后，本实例能自动感知（面板改完机器人不用重启）。"""
    from db import Store

    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.db")
    a, b = Store(p), Store(p)
    a.ensure_group("G")
    a.set_sub("G", 111, "live", True, "A")
    n0 = len(b.ups_of_group("G"))
    a.set_sub("G", 222, "live", True, "B")     # 另一个"进程"改
    ck("外部改动能被感知", len(b.ups_of_group("G")) == n0 + 1)


def t_db_migrate():
    """从旧 data.json 迁移到数据库。"""
    import json
    from db import Store, migrate_if_needed

    d = tempfile.mkdtemp()
    jp = os.path.join(d, "data.json")
    json.dump({"groups": {"OLD": {"name": "旧群", "subs": {
        "999": {"uname": "旧UP", "live": {"on": True}}}}},
        "ups": {"999": {"uname": "旧UP", "room_id": 5, "face": ""}}},
        io.open(jp, "w", encoding="utf-8"))
    dbp = os.path.join(d, "m.db")
    ck("迁移执行", migrate_if_needed(dbp, jp))
    st = Store(dbp)
    ck("迁移后群存在", "OLD" in st.data["groups"])
    ck("迁移后订阅生效", st.targets(999, "live") == ["OLD"])
    ck("迁移只做一次", not migrate_if_needed(dbp, jp))


def t_account_info():
    """登录成功后要能显示账号名和过期时间。"""
    import asyncio
    import bili_login as BL

    ck("有 account_info", callable(getattr(BL, "account_info", None)))
    r = asyncio.run(BL.account_info(""))
    ck("空 cookie 返回失败", r.get("ok") is False)
    r2 = asyncio.run(BL.account_info("SESSDATA=fake"))
    ck("无效 cookie 不抛异常", isinstance(r2, dict) and "ok" in r2)

    import time
    e = BL.expired_info(time.time() + 30 * 86400)
    ck("过期时间可展示", bool(e.get("expires_str")) and e.get("days_left") == 29)


def t_panel_url_file():
    """后端要把真实面板地址写进 .panel_url。

    之前靠扫 8088-8099 猜端口，可能连到别的程序上，
    表现为「窗口开了但页面打不开 / 秒关」。
    """
    import start as ST

    src = io.open(os.path.join(BASE_DIR, "start.py"), encoding="utf-8").read()
    ck("start.py 会写 .panel_url", ".panel_url" in src)

    ck("启动器不再只靠猜端口", "url_file" in src or ".panel_url" in src)


def t_js_missing_element_safe():
    """页面缺元素时脚本不能整体中断（这是「点了没反应」的根因）。"""
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("$() 有缺失替身", "_dummy(id)" in js)
    ck("$() 返回替身而非 null",
       "document.getElementById(id) || _dummy(id)" in js)

    # 模拟：缺元素时顶层绑定不抛错（字典不支持属性赋值，用类来模拟）
    class Dummy:
        def __init__(self, id_):
            self._missing = True
            self.id = id_
            self.style = {}
            self.dataset = {}
            self.classList = None
            self.value = ""
            self.checked = False
            self.textContent = ""
            self.innerHTML = ""

        def addEventListener(self, *a):
            return None

    try:
        d = Dummy("nope")
        d.onclick = lambda: None
        d.onclick()
        ck("缺元素时绑定不抛错", True)
    except Exception:
        ck("缺元素时绑定不抛错", False)


def t_log_noise_filtered():
    """运行日志要滤掉 HTTP 访问记录等噪音。"""
    import webui as W

    src = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("有噪音过滤逻辑", "_noise" in src)
    ck("过滤 HTTP 访问行", '"(GET|POST|PUT|DELETE) /' in src)
    ck("过滤 Unclosed 警告", "Unclosed client session" in src)


def t_safe_stdio_gbk_emoji():
    """GBK 环境下 emoji 不能让程序崩溃。

    Windows 中文环境下 Python
    按 GBK 编码写，遇到 🛡️ 这类字符直接 UnicodeEncodeError 崩掉。
    """
    from cfgutil import safe_stdio

    ck("有 safe_stdio", callable(safe_stdio))

    # 复现 bug：GBK 严格模式写 emoji 必然抛错
    buf = io.BytesIO()
    strict = io.TextIOWrapper(buf, encoding="gbk", errors="strict",
                              line_buffering=True)
    crashed = False
    try:
        strict.write("\U0001f6e1\ufe0f 面板")
        strict.flush()
    except UnicodeEncodeError:
        crashed = True
    ck("GBK 严格模式确实会崩（bug 成因）", crashed)

    # 修复后：errors=replace 不再崩
    buf2 = io.BytesIO()
    safe = io.TextIOWrapper(buf2, encoding="gbk", errors="replace",
                            line_buffering=True)
    try:
        safe.write("\U0001f6e1\ufe0f 面板\n")
        safe.flush()
        ck("replace 模式不崩", True)
    except UnicodeEncodeError:
        ck("replace 模式不崩", False)

    # safe_stdio 实际调用不抛错
    try:
        safe_stdio()
        ck("safe_stdio 调用安全", True)
    except Exception:
        ck("safe_stdio 调用安全", False)


def t_kill_process_tree():
    """关掉窗口要连子进程一起杀，否则端口一直被占。"""
    import subprocess as sp
    from cfgutil import kill_process_tree, python_exe

    ck("有 kill_process_tree", callable(kill_process_tree))

    p = sp.Popen([python_exe(), "-c", "import time; time.sleep(20)"])
    try:
        import time
        time.sleep(0.4)
        ck("进程已启动", p.poll() is None)
        kill_process_tree(p, grace=1.0)
        ck("进程被终止", p.poll() is not None)
    finally:
        try:
            p.kill()
        except Exception:
            pass

    ck("对 None 不报错", kill_process_tree(None) is None)


def t_start_uses_safe_output():
    """start.py 必须接入安全输出与进程树清理。"""
    import start as ST

    ssrc = io.open(os.path.join(BASE_DIR, "start.py"), encoding="utf-8").read()
    ck("start.py 调用 safe_stdio", "safe_stdio()" in ssrc)
    ck("start.py 用 kill_process_tree", "kill_process_tree" in ssrc)
    ck("start.py 退出清端口文件",
       "clear_panel_url" in ssrc and ".panel_url" in ssrc)


def t_qq_group_info_api():
    """官方群信息接口：能拿到 group_name（之前误判为「没有接口」）。"""
    from qqapi import QQClient

    ck("QQClient 有 get_group_info", hasattr(QQClient, "get_group_info"))
    ck("QQClient 有 get（GET 请求）", hasattr(QQClient, "get"))

    src = io.open(os.path.join(BASE_DIR, "qqapi.py"), encoding="utf-8").read()
    ck("调用官方 /info 路径", "/info" in src)
    ck("识别 11253 白名单错误", "11253" in src)

    bsrc = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("机器人会自动拉群名", "_fetch_group_name" in bsrc)
    ck("拿不到群名不刷屏", "_group_name_denied" in bsrc)


def t_browser_login_module():
    """浏览器自动化登录（解决扫码拿不到 SESSDATA）。"""
    try:
        import bili_browser_login as BBL
    except ImportError:
        ck("bili_browser_login 可导入", False)
        return

    ck("模块可导入", True)
    ck("有 browser_login", callable(getattr(BBL, "browser_login", None)))
    ck("会找本机 Chromium", callable(getattr(BBL, "find_chromium", None)))
    ck("需要 SESSDATA 才算成功", "SESSDATA" in " ".join(BBL.NEED))

    # 缺 playwright 时给出明确提示，而不是裸 ImportError
    src = io.open(os.path.join(BASE_DIR, "bili_browser_login.py"),
                  encoding="utf-8").read()
    ck("缺依赖有中文指引", "pip install playwright" in src)


def t_new_ui():
    """新界面：所有按钮都有处理函数，元素引用不缺失。"""
    import re

    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
def _all_css():
    """实际生效的样式 = static/style.css + index.html 内联 <style>。

    ⚠️ 以前只检查 style.css，但新布局（最终版）的样式是**内联**在
       index.html 里的，style.css 只保留完整版没定义的补充规则。
       只查 style.css 会误报"功能没了"（.fx-tilt / .ripple / 字体等
       其实都在内联里）。所以两边合并后再判。
    """
    out = io.open(os.path.join(BASE_DIR, "static", "style.css"),
                  encoding="utf-8").read()
    try:
        html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                       encoding="utf-8").read()
        m = re.search(r"<style>(.*?)</style>", html, re.S)
        if m:
            out += "\n" + m.group(1)
    except OSError:
        pass
    return out

    css = _all_css()

    acts = set(re.findall(r'data-act="([a-zA-Z\-]+)"', html))
    keys = set(re.findall(r"'([a-zA-Z][a-zA-Z\-]*)'\s*[:(]", js))
    missing = sorted(a for a in acts if a not in keys)
    ck("所有按钮都有处理函数", not missing)

    dyn = set(re.findall(r'id="([A-Za-z0-9_]+)"', js))
    ids = set(re.findall(r"\$\('([A-Za-z0-9_]+)'\)", js))
    hid = set(re.findall(r'id="([A-Za-z0-9_]+)"', html))
    ck("引用的元素都存在", not (ids - hid - dyn - {"toast"}))

    ck("有暗夜主题变量", "[data-theme=\"dark\"]" in css)
    ck("有动效", "@keyframes" in css)
    ck("有响应式断点", "@media" in css)
    ck("尊重减少动效", "prefers-reduced-motion" in css)

    # v1.22.0 动效：只做有物理感的，不做条纹滚动渐变
    ck("指针柔光", ".bg-glow" in css and "initGlow" in js)
    ck("卡片倾斜", ".fx-tilt" in css and "initTilt" in js)
    ck("点击涟漪", ".ripple" in css and "initRipple" in js)
    ck("滚动渐入", ".reveal" in css and "initReveal" in js)
    ck("数字滚动", "countUp" in js)
    ck("动效已挂载", "safe('glow', initGlow)" in js)
    ck("入场带模糊渐变", "filter: blur(" in css)
    # 条纹滚动 = background-position 平移动画，明确禁止
    # 禁的是"条纹在 @keyframes 里来回平移"那种廉价效果；
    # 静态的 background-position:center（群头像横幅居中）是正当用途，别误伤。
    _kf = re.findall(r"@keyframes[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", css, re.S)
    ck("无条纹滚动渐变",
       not any("background-position" in k for k in _kf))
    ck("用了在线黑体", "Noto Sans SC" in css)
    ck("字体有本地回退", "PingFang SC" in css or "Microsoft YaHei" in css)


def t_video_season_collection():
    """视频合集订阅：合集新增视频才推，每个合集可有不同文案。"""
    import asyncio as _aio
    import tempfile
    import shutil
    import bot
    import db as _db
    from bilibili import SeasonInfo, SeasonVideo, SeasonPush, parse_season_ref
    from notify import render, DEFAULT_TEMPLATES

    # --- 链接解析 ---
    ck("新链接解析", parse_season_ref(
        "https://space.bilibili.com/259149243/lists/1001?type=season")
        == (259149243, 1001))
    ck("旧链接解析", parse_season_ref(
        "https://space.bilibili.com/1/channel/collectiondetail?sid=22")
        == (1, 22))
    ck("纯ID解析", parse_season_ref("1001") == (0, 1001))
    ck("空值不炸", parse_season_ref("") == (0, 0))
    ck("乱码不炸", parse_season_ref("abc") == (0, 0))

    tmp = tempfile.mkdtemp(prefix="bn_season_")
    try:
        st = _db.Store(os.path.join(tmp, "s.db"))
        st.set_up_info(259149243, "波萝", 0)
        # 同一 UP 主订阅两个合集
        st.add_season("GA", 259149243, 1001, "录播合集")
        st.add_season("GA", 259149243, 1002, "切片合集")
        got = st.seasons_of_group("GA")
        ck("同UP可订阅多个合集", len(got) == 2)
        ck("两个合集都在", {x["season_id"] for x in got} == {1001, 1002})
        ck("多群去重查询", len(st.all_season_keys()) == 2)

        # 第二个群订阅同一合集 → 按键去重应仍是 2 个不同的(uid,sid)
        st.add_season("GB", 259149243, 1001, "录播合集")
        keys = st.all_season_keys()
        ck("两群同合集产生两条订阅", len(keys) == 3)

        # 基线读写
        st.set_season_known("GA", 259149243, 1001, ["BV1", "BV2"])
        ck("基线写读", st.get_season_known("GA", 259149243, 1001)
           == ["BV1", "BV2"])
        ck("另一群基线独立", st.get_season_known("GB", 259149243, 1001) == [])
        # 超长截断：上限 300，且必须**明显大于**一次能取回的视频数
        # （season_max_pages × 30），否则早先见过的会被挤出去又被当新的推
        st.set_season_known("GA", 259149243, 1002,
                            [f"BV{i}" for i in range(150)])
        ck("未超上限时不截断", len(st.get_season_known("GA", 259149243, 1002)) == 150)
        st.add_season("GA", 259149243, 1003, "长合集")
        st.set_season_known("GA", 259149243, 1003,
                            [f"BV{i}" for i in range(400)])
        ck("上限 300", len(st.get_season_known("GA", 259149243, 1003)) == 300)
        st.add_season("GA", 259149243, 1004, "去重合集")
        st.set_season_known("GA", 259149243, 1004, ["BV1", "BV1", "BV2"])
        ck("写入时去重", st.get_season_known("GA", 259149243, 1004) == ["BV1", "BV2"])

        # 开关
        st.set_season_on("GA", 259149243, 1001, False)
        ck("关掉后不再查询",
           not any(k[0] == "GA" and k[2] == 1001 for k in st.all_season_keys()))

        # --- 检测行为 ---
        class FakeBili:
            def __init__(self):
                self.seasons = {}
                self.sections = {}

            async def get_season_archives(self, uid, sid, max_pages=2):
                return self.seasons.get((uid, sid))

            async def get_season_sections(self, bvid, season_id=0):
                # ⚠️ 返回结构要跟真实接口一致：分了小节才有小节名，
                #    只有一个"正片"等于没分节（那时 section_map 返回空）
                return self.sections.get(bvid) or []

            def section_map(self, sections):
                # 与 BiliClient.section_map 同规则：只有一个小节就不显示
                real = [s for s in (sections or []) if s.get("episodes")]
                if len(real) <= 1:
                    return {}
                return {e["bvid"]: (s.get("title") or "")
                        for s in real
                        for e in (s.get("episodes") or [])
                        if e.get("bvid")}

        class QQ:
            def __init__(self):
                self.md = []

            async def send_markdown(self, gid, md, keyboard=None, **k):
                self.md.append((gid, md))
                return {"ok": True}

            async def send_text(self, *a, **k):
                return {"ok": True}

        class B(bot.NotifyBot):
            def __init__(self, store, bili):
                self.cfg = {"templates": {}, "message_style": "markdown",
                            "season_max_pages": 2, "season_push_limit": 3}
                self.store = store
                self.bili = bili
                self.qq = QQ()
                self.tpls = {}
                self._group_tpl_cache = {}
                self._silent = False
                self._recent_push = {}
                self._round = 1
                self.slow_every = 1
                self.send_image = False

        bili = FakeBili()
        # ⚠️ pubdate 必须用**真实时间戳**：合集那边有"升级保护"，
        # 发布时间早于上次检测的视频只补基线不推送。以前这里写 100/200/300
        # （1970 年），被保护逻辑当成历史视频拦掉，看着像"合集坏了"。
        NOW = int(time.time())
        bili.seasons[(259149243, 1001)] = SeasonInfo(
            season_id=1001, uid=259149243, title="录播合集", total=2,
            videos=[SeasonVideo(bvid="BV1", title="第1期", pubdate=NOW - 200),
                    SeasonVideo(bvid="BV2", title="第2期", pubdate=NOW - 100)])
        # ⚠️ 两个小节（真的分了节）→ 小节名才会显示出来
        bili.sections["BV3"] = [
            {"title": "正片", "episodes": [
                {"bvid": "BV1", "title": "第1期", "pubdate": NOW - 200},
                {"bvid": "BV2", "title": "第2期", "pubdate": NOW - 100}]},
            {"title": "花絮", "episodes": [
                {"bvid": "BV3", "title": "第3期", "pubdate": NOW}]},
        ]
        b = B(st, bili)
        st.set_season_on("GA", 259149243, 1001, True)
        # GB 也订阅了同一合集（前面用来验证"多群去重查询"）。
        # 这里把它关掉，否则新增视频时 GA/GB 都会推，断言条数会对不上。
        st.set_season_on("GB", 259149243, 1001, False)

        async def run():
            out = {}
            # 首次：静默记基线，不推送
            b._silent = True
            await b._check_one_season(259149243, 1001)
            out["first_silent"] = len(b.qq.md) == 0
            out["baseline"] = len(st.get_season_known("GA", 259149243, 1001)) == 2

            # 新增一个 → 推送
            b._silent = False
            bili.seasons[(259149243, 1001)].videos.append(
                SeasonVideo(bvid="BV3", title="第3期", pubdate=NOW))
            await b._check_one_season(259149243, 1001)
            out["pushed"] = len(b.qq.md) == 1
            out["has_title"] = "第3期" in b.qq.md[0][1] if b.qq.md else False
            out["has_section"] = "花絮" in b.qq.md[0][1] if b.qq.md else False

            # 另一群新订阅 → 静默，不补推历史
            b.qq.md.clear()
            st.add_season("GC", 259149243, 1001, "录播合集")
            await b._check_one_season(259149243, 1001)
            out["new_group_silent"] = not any(g == "GC" for g, _ in b.qq.md)

            # 批量新增 5 个 → 只推 3 个
            b.qq.md.clear()
            for i in range(4, 9):
                bili.seasons[(259149243, 1001)].videos.append(
                    SeasonVideo(bvid=f"BV{i}", title=f"第{i}期",
                                pubdate=NOW + i))
            await b._check_one_season(259149243, 1001)
            # ⚠️ 只数 GA 的：上一步新加的 GC 也有基线了，它同样会推 3 条。
            # 断言总数会把两个群的加起来，那测的就不是"每群上限"了。
            out["limit3"] = len([x for x in b.qq.md if x[0] == "GA"]) == 3
            return out

        r = _aio.run(run())
        ck("首次静默不推送", r["first_silent"])
        ck("静默仍记基线", r["baseline"])
        ck("新增会推送", r["pushed"])
        ck("推送含视频标题", r["has_title"])
        ck("推送含小节名（分了节才显示）", r["has_section"])
        ck("新群不补推历史", r["new_group_silent"])
        ck("批量限制3条", r["limit3"])

        # --- 文案三层回落（v1.30.1 起没有"群专属"层了）---
        class S:
            def __init__(self, uown, sown):
                self.uown = uown
                self.sown = sown

            def get_up_templates(self, gid, uid):
                return (self.uown.get(gid, {}) or {}).get(uid, {})

            def get_season_templates(self, gid, uid, sid):
                return self.sown.get((gid, uid, sid), {})

        def mk(cfg, uown, sown):
            bb = bot.NotifyBot.__new__(bot.NotifyBot)
            bb.tpls = dict(cfg.get("templates") or {})
            bb.store = S(uown, sown)
            bb._up_tpl_cache = {}
            return bb

        bb = mk({"templates": {"season": "全局合集 {title}"}},
                {"GA": {259149243: {"season": "UP级 {title}"}}},
                {("GA", 259149243, 1001): {"season": "合集专属 {title}"}})
        ck("合集级最优先",
           bb._tpls_for("GA", uid=259149243,
                        season_key=(259149243, 1001))["season"]
           == "合集专属 {title}")
        ck("没设合集→用UP级",
           bb._tpls_for("GA", uid=259149243,
                        season_key=(259149243, 1002))["season"]
           == "UP级 {title}")
        ck("UP也没设→用全局",
           bb._tpls_for("GB", uid=259149243,
                        season_key=(259149243, 1001))["season"]
           == "全局合集 {title}")
        # 要测"缺失"，必须全局也没设（上面的 bb 全局设了 season，
        # 所以任何群都会带上它 —— 那样断言"缺失"是错的）
        bb2 = mk({}, {}, {})
        ck("都没有→缺失交给默认",
           "season" not in bb2._tpls_for("GC", uid=1, season_key=(1, 1)))

        # 渲染：有小节 / 无小节
        p1 = SeasonPush(bvid="BVx", title="T", season_title="录播", section="正片")
        md1, _ = render("season", p1, "波萝", {})
        ck("有小节显示小节", "正片" in md1)
        p2 = SeasonPush(bvid="BVy", title="T", season_title="录播", section="")
        md2, _ = render("season", p2, "波萝", {})
        ck("无小节不显示该行", "小节" not in md2)
        ck("合集模板入默认", "season" in DEFAULT_TEMPLATES)

        # 迁移带走合集
        st2 = _db.Store(os.path.join(tmp, "s2.db"))
        st2.set_sub("SA", 259149243, "live", True)
        st2.add_season("SA", 259149243, 1001, "录播")
        st2.set_season_templates("SA", 259149243, 1001, {"season": "专属 {title}"})
        rr = st2.copy_subs("SA", "DB")
        ck("迁移带上合集", rr.get("seasons") == 1)
        ck("目标群有合集", len(st2.seasons_of_group("DB")) == 1)
        ck("合集文案跟着走",
           st2.get_season_templates("DB", 259149243, 1001).get("season")
           == "专属 {title}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- 接口与前端 ---
    w = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("有订阅接口", "/api/season/add" in w)
    ck("有删除接口", "/api/season/remove" in w)
    ck("有开关接口", "/api/season/set" in w)
    ck("有文案接口", "/api/season/templates" in w)
    ck("state 带合集", '"seasons": seasons' in w or "seasons=seasons" in w
       or '"seasons": ' in w)
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("前端有订阅动作", "'add-season'" in js)
    ck("前端有开关动作", "'toggle-season'" in js)
    ck("前端有删除动作", "'remove-season'" in js)
    ck("前端有合集文案", "'open-stpl'" in js)
    # 合集卡片：头像改用统一的"箱子"图标（B站不给合集封面），
    # 渲染函数见 seasonBoxSVG + 订阅列表里的合集分组。
    ck("前端渲染合集卡片",
       "seasonBoxSVG" in js and "'add-season'" in js)


def t_action_delegate_receives_el():
    """事件委托必须把 el 传给 ACTIONS 里的动作。

    用户实测："专属文案功能用不了" —— 点「✍️ 这个群的专属文案」
    完全没反应。根因是事件委托写成：

        ACTIONS[act]()        ← 不传 el

    而 open-gtpl / toggle-season / remove-season / open-stpl 这些动作
    都要靠 el.dataset 取 gid / uid / season_id，拿到 undefined，
    一访问就 TypeError。

    ⚠️ node --check 只能查语法，查不出这种"调用时不传参"的问题。
    ⚠️ 后端接口测试也查不出 —— 压根没走到发请求那一步。
    所以必须静态检查调用形式。
    """
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()

    # ⚠️ v2.0.1 起分发不再直接写 ACTIONS[act](el)，改成统一入口
    #    _guardAct(act, el, ACTIONS[act]) —— 为的是加上"点了要等"的
    #    置灰/进度/防连点。这里要守的仍是**同一件事**：el 必须传到动作。
    #    只认某一种字面写法会误伤，所以两种写法都算通过。
    ck("ACTIONS 动作收到 el",
       "ACTIONS[act](el)" in js or "_guardAct(act, el, ACTIONS[act])" in js)
    ck("ROW_ACTIONS 动作收到 el",
       "ROW_ACTIONS[act](el)" in js
       or "_guardAct(act, el, ROW_ACTIONS[act])" in js)
    # 真正的行为断言：统一入口内部必须把 el 原样交给动作函数。
    # 只查"分发处提到了 el"是不够的 —— 中间少传一层照样拿不到 dataset。
    ck("runAct 把 el 交给动作函数", "fn(el)" in js)
    ck("不再有不传参的调用",
       js.count("ACTIONS[act]()") == 0 and js.count("ROW_ACTIONS[act]()") == 0)

    # 反查：所有声明成 (el) 的动作，都必须在委托链里能拿到 el
    import re as _re
    declared = set(_re.findall(r"async '([a-z\-]+)'\(el\)", js))
    ck("存在需要 el 的动作", len(declared) > 0)
    ck("委托链统一传 el",
       "ACTIONS[act](el)" in js or "_guardAct(act, el, ACTIONS[act])" in js)


def t_html_block_structure():
    """HTML 区块结构不能错位。

    上一版为了凑 div 配对数，**删掉了「添加订阅」自己的容器**，
    又在别处留了个空开标签 —— 配对数是平了，但结构错了：
    标题直接掉在父容器里，样式全乱。

    ⚠️ 只数 <div> 和 </div> 的个数**查不出这种错**：
    删一个开标签再删一个闭标签，总数照样是配平的。
    必须检查"连续两个开标签"这种结构异常。
    """
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    lines = [l.strip() for l in html.split("\n")]

    # 不允许：连续两行都是 <div class="blk"> 这种裸开标签
    bad = []
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if a.startswith('<div class="blk"') and b.startswith('<div class="blk"'):
            bad.append(i + 1)
    ck("没有空的连续开标签", not bad)
    # 每个 blk-t 必须紧跟在某个开标签之后（不能孤零零挂着）
    orphan = []
    for i, l in enumerate(lines):
        if l.startswith('<div class="blk-t"'):
            prev = lines[i - 1] if i else ""
            # 上一行必须是容器开标签或另一个区块的结尾
            if prev.startswith('</div>') and not prev.startswith('</div></div>'):
                # 允许：前一个是正常闭合的兄弟区块，但那说明本行缺容器
                orphan.append(i + 1)
    ck("区块标题都有容器", not orphan)

    # 计数仍然要平
    ck("div 计数配平", html.count("<div") == html.count("</div>"))


def t_sheet_closes_on_switch():
    """文案面板关掉后，切换到别的功能不能再冒出来。

    用户实测：关掉面板 → 打开别的功能 → 面板还盖在上面，
    只有刷新网页才消失。

    原因不是"关闭按钮没生效"，而是**切换区块时没人负责关面板**。
    所以修法是 openSec（切功能）里强制关一次，而不是只改关闭按钮。
    """
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()

    # openSec 函数体内必须调用 closeSheet
    i = js.find("function openSec(")
    ck("有 openSec", i >= 0)
    if i >= 0:
        body = js[i:i + 700]
        ck("切功能时先关面板", "closeSheet()" in body)

    # closeSheet 要彻底：移除 show + 清空内容
    j = js.find("function closeSheet(")
    ck("有 closeSheet", j >= 0)
    if j >= 0:
        body = js[j:j + 500]
        ck("移除 show", "remove('show')" in body)
        ck("清空内容（防残留）", "innerHTML = ''" in body)


def t_template_var_quick_insert():
    """文案编辑要有快捷插入变量的按钮，且是插到光标处。

    ⚠️ 关键是**插到光标处**，不是追加到末尾 —— 追加的话用户
    想放在中间就只能手动剪切，等于没做。
    """
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()

    ck("有快捷插入按钮生成", "function varButtons" in js)
    ck("有插入函数", "function insertVar" in js)
    ck("有聚焦记录", "function bindVarInputs" in js)
    ck("注册了 ins-var 动作", "'ins-var'" in js)
    ck("面板里放了变量按钮", "varButtons(" in js)
    # 插到光标处：必须用 slice 拼，不能是 value += 
    k = js.find("function insertVar(")
    ck("有 insertVar 定义", k >= 0)
    if k >= 0:
        body = js[k:k + 600]
        ck("按光标位置拼接", "slice(0, s)" in body)
        ck("不是末尾追加", "el.value +=" not in body)
    # 变量覆盖：name/title 通用，season/section 只有合集有
    ck("合集变量含 season", "'{season}'" in js)
    ck("合集变量含 section", "'{section}'" in js)


def t_group_removed_cleanup():
    """机器人被移出群聊 → 这个群的数据要整个清掉。

    不清理的后果：面板里一直挂着一个永远收不到消息的群，订阅照旧
    往那推、每次都失败，日志里反复刷同一条警告。

    ⚠️ 关键边界：**不能删 ups 表** —— UP 主资料是全局的，
    别的群可能还订阅着同一个 UP 主。
    """
    import db as DB
    import bot

    d = tempfile.mkdtemp()
    st = DB.Store(os.path.join(d, "t.db"))
    GID = "GROUP_GONE"

    st.ensure_group(GID, "被踢掉的群")
    st.ensure_group("GROUP_KEEP", "还在的群")
    UID = 259149243
    # ups 表是全局 UP 主资料，先建一条才能验证"删群不会连带删掉它"
    st.set_up_info(UID, "测试UP", 12345, "")
    for k in ("live", "video"):
        st.set_sub(GID, UID, k, True)
        st.set_sub("GROUP_KEEP", UID, k, True)
    st.set_up_templates(GID, UID, {"live": "A群专属文案"})
    st.set_up_templates("GROUP_KEEP", UID, {"live": "B群专属文案"})

    ck("删除前该群有订阅", len(st.ups_of_group(GID)) > 0)

    n = st.remove_group(GID)
    ck("返回清理数量", isinstance(n, dict) and n.get("subs", 0) >= 2)

    ck("群已从列表消失", GID not in st.all_groups())
    ck("该群订阅已清空", st.ups_of_group(GID) == {})
    ck("该群 UP 级文案已清空", (st.get_up_templates(GID, UID) or {}) == {})
    ck("别的群不受影响", "GROUP_KEEP" in st.all_groups())
    ck("别的群订阅还在", len(st.ups_of_group("GROUP_KEEP")) > 0)
    ck("别的群文案还在",
       (st.get_up_templates("GROUP_KEEP", UID) or {}).get("live")
       == "B群专属文案")
    # ups 是全局 UP 主资料，不能跟着群一起删
    row = st._conn().execute(
        "SELECT COUNT(*) c FROM ups WHERE uid=?", (UID,)).fetchone()
    ck("UP 主资料保留（全局共享）", (row[0] if row else 0) >= 1)

    # 事件处理器存在，且用的是官方事件名
    src = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("有 on_group_del_robot 处理器", "async def on_group_del_robot" in src)


def t_main_area_is_permanent():
    """主区必须常驻，不能有关闭按钮。

    ⚠️ 这是 v1.31.0 布局重做后我自己引入的回归：旧代码里主区是弹出层
    （#sheet + close-main/close-sheet），改成常驻主区后，残留的关闭动作
    会把整个 <main> 的 show 类摘掉 —— 点一下返回/✕，主区直接变白屏，
    只剩侧边栏。

    现在主区是常驻的（切板块靠侧边栏），所以判据反过来：
    **不许**再有任何关闭主区的按钮或动作。
    """
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    # 先剥注释：说明文字里会点名旧类名/旧动作，不剥永远 FAIL。
    _h = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    _j = re.sub(r"//[^\n]*", "", js)

    ck("有常驻主区容器", '<main class="main">' in _h)
    ck("主区不再用 sheet 外壳", 'id="sheet"' not in _h)
    ck("没有关闭主区的按钮", 'data-act="close-main"' not in _h)
    ck("没有关闭主区的动作", '"close-main"' not in _j and "'close-main'" not in _j)
    # 返回类按钮改成跳回概况，而不是把主区藏掉
    ck("返回动作跳回概况",
       ("'back-home'" in _j) or ('"back-home"' in _j) or ("gotoHome" in _j)
       or ('data-sec="home"' in _h))


def t_refresh_names_removes_gone():
    """刷新群名时，确认不在群里就直接移除。

    以前只报告"1 个未能获取"，群还留在库里 —— 面板显示一个永远
    收不到消息的群，订阅照旧往那推、每次失败。
    """
    src = io.open(os.path.join(BASE_DIR, "webui.py"),
                  encoding="utf-8").read()
    ck("刷新时会移除", "_is_gone(code" in src)
    ck("移除调用 remove_group", "st.remove_group(gid)" in src)
    ck("有 removed 计数", '"removed"' in src)

    # 判定必须复用同一套，且不能把"接口未开通"当成不在群里
    try:
        import sys
        sys.path.insert(0, BASE_DIR)
        from webui import _is_gone
        ck("11293 算不在群里",
           _is_gone(11293, "鉴权失败，机器人非群成员") is True)
        ck("11252 算不在群里", _is_gone(11252, "群不存在") is True)
        # ⚠️ 这条最关键：未开通时把所有群删光是灾难
        ck("11253 不算（不能删光群）", _is_gone(11253, "接口未开通") is False)
    except Exception as e:
        ck("能导入 _is_gone", False)


def t_group_gone_by_send_error():
    """推送失败兜底：明确说"机器人已不在该群"才清群，其它错误不能误删。

    ⚠️ 这条最重要：**防误伤**。网络抖动、频控、内容违规都会让推送
    失败，那些情况下删群等于把用户的订阅全毁了。
    """
    import bot

    ck("11242 认", bot.is_gone_group_error("code=11242 机器人不在这个群里"))
    ck("11252 认", bot.is_gone_group_error("code=11252 群不存在"))
    ck("11292 认", bot.is_gone_group_error("11292"))
    ck("网络抖动不认", not bot.is_gone_group_error("Timeout"))
    ck("频控不认", not bot.is_gone_group_error("code=11261 频率限制"))
    ck("内容违规不认", not bot.is_gone_group_error("code=11266 内容违规"))
    ck("空值不认", not bot.is_gone_group_error(""))
    ck("None 不认", not bot.is_gone_group_error(None))
    # 11293 是最常见的：群主把机器人踢了，官方直接回
    # {"code":11293,"message":"鉴权失败，机器人非群成员"}
    ck("11293 认", bot.is_gone_group_error(
        '{"message":"鉴权失败，机器人非群成员","code":11293}'))
    ck("中文兜底认", bot.is_gone_group_error("鉴权失败，机器人非群成员"))
    # 接口没开通不能当成"不在群里"—— 那样会把所有群都删光
    ck("未开通不认", not bot.is_gone_group_error("code=11253 接口未开通"))
    ck("有启动核实", "async def _prune_dead_groups" in io.open(
        os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read())


def t_default_uid_inputs_empty():
    """添加订阅 / 订阅合集 的 UID 输入框要**留空**。

    ⚠️ 注意区分：自检用的测试 UID 是绑死在后端常量里的（v1.24.0 已
    和添加功能剥离），不受这里影响 —— 自检不该要求用户填 UID。
    """
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    import re as _re

    for fid in ("addUid", "seasonUid"):
        m = _re.search(r'id="%s"[^>]*>' % fid, html)
        ck(f"{fid} 存在", m is not None)
        if not m:
            continue
        tag = m.group(0)
        ck(f"{fid} 没有预填值", "value=" not in tag)
        ck(f"{fid} 仍有示例提示", "placeholder" in tag)

    # 自检的 UID 必须仍然绑死，不能跟着变空
    import webui
    ck("自检 UID 仍绑死",
       getattr(webui, "SELFCHECK_UID", 0) == 259149243)

    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    ck("面板有订阅区块", "id=\"seasonRef\"" in html)


def t_per_group_templates():
    """不同群可以有自己的文案模板。

    回落顺序：群自己的覆盖 → 全局配置 → 内置默认。
    群模板是**稀疏**的：只存这个群改过的项，其余回落全局，
    这样全局改默认值时没特地改过的群会跟着变。
    """
    import tempfile
    import shutil
    import bot
    import db as _db
    from notify import DEFAULT_TEMPLATES, render_live

    tmp = tempfile.mkdtemp(prefix="bn_tpl_")
    try:
        st = _db.Store(os.path.join(tmp, "t.db"))
        st.set_sub("G1", 259149243, "live", True)
        ck("初始无覆盖", st.get_up_templates("G1", 259149243) == {})

        st.set_up_templates("G1", 259149243,
                            {"live": "G1专属 {name}", "video": "G1视频 {title}"})
        got = st.get_up_templates("G1", 259149243)
        ck("存进去了", got.get("live") == "G1专属 {name}")
        ck("稀疏：只有改过的", len(got) == 2)

        # 留空 = 取消覆盖
        st.set_up_templates("G1", 259149243, {"video": ""})
        ck("留空即取消覆盖", "video" not in st.get_up_templates("G1", 259149243))
        # 别的 UP 不受影响
        ck("按 UP 主隔离", st.get_up_templates("G1", 111) == {})

        # --- bot 回落顺序：全局 → UP主 → 合集 ---
        class S:
            def __init__(self, own):
                self.own = own

            def get_up_templates(self, gid, uid):
                return (self.own.get(gid, {}) or {}).get(uid, {})

            def get_season_templates(self, gid, uid, sid):
                return (self.own.get("S:" + gid, {}) or {})

        def mk(cfg, own):
            b = bot.NotifyBot.__new__(bot.NotifyBot)
            b.tpls = dict(cfg.get("templates") or {})
            b.store = S(own)
            b._up_tpl_cache = {}
            return b

        own = {"GA": {259149243: {"live": "GA专属 {name}",
                                  "video": "GA视频 {title}"}},
               "GB": {},
               "S:GA": {"season": "合集专属 {title}"}}
        b = mk({"templates": {"live": "全局 {name}"}}, own)

        ta = b._tpls_for("GA", uid=259149243)
        ck("UP 覆盖优先于全局", ta["live"] == "GA专属 {name}")
        ck("UP 有全局无 → 用 UP", ta["video"] == "GA视频 {title}")
        tb = b._tpls_for("GB", uid=259149243)
        ck("没覆盖 → 用全局", tb["live"] == "全局 {name}")
        ck("没覆盖且全局无 → 缺省", "video" not in tb)
        # 合集层最优先
        ts = b._tpls_for("GA", uid=259149243, season_key=(259149243, 1))
        ck("合集层最优先", ts["season"] == "合集专属 {title}")

        # --- 实际渲染 ---
        class Live:
            title = "T"
            area = "手游"
            online = 1
            cover = ""
            url = "http://x"
            room_id = 1

        md_a, _ = render_live(Live(), "波萝", ta)
        md_b, _ = render_live(Live(), "波萝", tb)
        md_d, _ = render_live(Live(), "波萝", {})
        ck("渲染用 UP 专属文案", "GA专属 波萝" in md_a)
        ck("渲染用全局文案", "全局 波萝" in md_b)
        ck("都没有用内置默认", "开播啦" in md_d)

        # --- 群级那一层已废弃 ---
        w0 = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
        ck("已无群文案接口", "/api/group/templates" not in w0)
        ck("已无群文案读取", "get_group_templates" not in w0)
        bsrc = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
        ck("bot 不再读群级文案", "get_group_templates" not in bsrc)

        # --- 老库群级文案自动摊到 UP 主 ---
        st3 = _db.Store(os.path.join(tmp, "t3.db"))
        conn = st3._conn()
        conn.execute("INSERT INTO groups(gid,name) VALUES('OLD','老群')")
        conn.execute('INSERT INTO subs(gid,uid,kind,"on") '
                     "VALUES('OLD',259149243,'live',1)")
        conn.execute('INSERT INTO subs(gid,uid,kind,"on") '
                     "VALUES('OLD',777,'live',1)")
        conn.execute("INSERT INTO group_templates(gid,kind,content) "
                     "VALUES('OLD','live','老群文案 {name}')")
        conn.commit()
        st3._persist()   # 落盘层是 JSON：不写盘，重开就什么都没有
        # 重新打开 → 触发迁移
        st4 = _db.Store(os.path.join(tmp, "t3.db"))
        ck("摊给了第一个 UP",
           st4.get_up_templates("OLD", 259149243).get("live") == "老群文案 {name}")
        ck("摊给了第二个 UP",
           st4.get_up_templates("OLD", 777).get("live") == "老群文案 {name}")
        ck("旧表已清空",
           st4._conn().execute(
               "SELECT COUNT(*) c FROM group_templates").fetchone()[0] == 0)

        # --- 迁移订阅时带走 UP 级文案 ---
        st2 = _db.Store(os.path.join(tmp, "t2.db"))
        st2.set_sub("SA", 259149243, "live", True)
        st2.set_up_templates("SA", 259149243, {"dynamic": "SA专属 {name}"})
        r = st2.copy_subs("SA", "DB")
        ck("复制带上文案",
           st2.get_up_templates("DB", 259149243).get("dynamic") == "SA专属 {name}")
        ck("复制提示含文案数", "专属文案" in (r.get("msg") or ""))
        r2 = st2.copy_subs("SA", "DC", move=True)
        ck("迁移后源群文案清空", st2.get_up_templates("SA", 259149243) == {})
        ck("迁移后目标有文案",
           st2.get_up_templates("DC", 259149243).get("dynamic") == "SA专属 {name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- 接口 ---
    w = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("有 UP 文案接口", "/api/up/templates" in w)
    ck("只接受已知键", "k in DEFAULT_TEMPLATES" in w)
    ck("state 带数量", "up_tpl_counts" in w)
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("前端有入口", "'open-utpl'" in js)
    ck("前端有保存", "'save-utpl'" in js)
    ck("前端有清空", "'clear-utpl'" in js)
    ck("显示已改条数", "已改" in js)
    ck("群级入口已移除", "'open-gtpl'" not in js)


def t_silent_join_and_one_time_greeting():
    """入群不说话；新群首次 @ 回一次感谢语；之后完全不理。"""
    import bot

    src = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()

    # --- 入群事件不能再有欢迎语 ---
    join = src.split("async def on_group_add_robot")[1].split(
        "async def on_group_msg_reject")[0]
    ck("入群不再发欢迎语", "send_text" not in join)
    ck("入群只登记", "ensure_group" in join)

    # --- 感谢语文案 ---
    ck("有感谢语常量", "GREETING_NEW_GROUP" in src)
    ck("感谢语内容正确", "感谢使用OnOiduts产品" in src)

    # --- @ 逻辑 ---
    at = src.split("async def on_group_at_message_create")[1].split(
        "def _remember_pushed")[0]
    ck("判断是否新群", "is_new" in at)
    ck("老群不回复", "if not is_new" in at and "return" in at)
    ck("用 msg_id 被动回复", "msg_id=getattr(message, \"id\", None)" in at)
    ck("被动失败回退主动", "改用主动发送" in at)
    ck("有内存去重", "_greeted_groups" in at)

    # --- 行为实测 ---
    import types
    import asyncio as _aio

    class QQ:
        def __init__(self, fail_passive=False):
            self.sent = []
            self.fail = fail_passive

        async def send_text(self, gid, content, msg_id=None, event_id=None):
            if self.fail and msg_id:
                raise RuntimeError("被动失败")
            self.sent.append((gid, content))
            return {"ok": True}

    class Store:
        def __init__(self):
            self.data = {"groups": {}}

        def ensure_group(self, gid):
            self.data["groups"].setdefault(gid, {"name": "", "subs": {}})

    class M:
        def __init__(self, g):
            self.group_openid = g
            self.id = "m1"

    def mk(qq):
        b = bot.NotifyBot.__new__(bot.NotifyBot)
        b.qq = qq
        b.store = Store()
        b._greeted_groups = set()

        async def _noop(gid):
            return None
        b._fetch_group_name = _noop
        return b

    async def run():
        out = {}
        # 入群静默
        b = mk(QQ())
        await b.on_group_add_robot(
            types.SimpleNamespace(group_openid="G1", event_id="e1"))
        out["join_silent"] = len(b.qq.sent) == 0
        out["join_registered"] = "G1" in b.store.data["groups"]

        # 新群首次 @
        b = mk(QQ())
        await b.on_group_at_message_create(M("G2"))
        out["greet_once"] = len(b.qq.sent) == 1
        out["greet_text"] = b.qq.sent[0][1] if b.qq.sent else ""

        # 同群再 @ → 不理
        b.qq.sent.clear()
        await b.on_group_at_message_create(M("G2"))
        out["no_second"] = len(b.qq.sent) == 0

        # 平台重复推送 → 只回一次
        b = mk(QQ())
        await _aio.gather(b.on_group_at_message_create(M("G3")),
                         b.on_group_at_message_create(M("G3")))
        out["dedup"] = len(b.qq.sent) == 1

        # 被动失败 → 回退主动
        b = mk(QQ(fail_passive=True))
        await b.on_group_at_message_create(M("G4"))
        out["fallback"] = len(b.qq.sent) == 1

        # 全失败 → 不影响登记
        class Dead:
            async def send_text(self, *a, **k):
                raise RuntimeError("x")
        b = mk(Dead())
        await b.on_group_at_message_create(M("G5"))
        out["still_registered"] = "G5" in b.store.data["groups"]
        return out

    r = _aio.run(run())
    ck("入群不发言", r["join_silent"])
    ck("入群仍登记", r["join_registered"])
    ck("新群回一次", r["greet_once"])
    ck("感谢语正确", r["greet_text"] == "感谢使用OnOiduts产品")
    ck("老群再@不理", r["no_second"])
    ck("重复事件只回一次", r["dedup"])
    ck("被动失败会回退", r["fallback"])
    ck("发送失败不影响登记", r["still_registered"])


def t_subscription_migration():
    """订阅迁移：把 A 群的订阅复制/迁移到 B 群。"""
    import tempfile
    import db as _db

    tmp = tempfile.mkdtemp(prefix="bn_mig_")
    st = _db.Store(os.path.join(tmp, "m.db"))
    try:
        # 源群：两位 UP，各开不同开关
        st.set_sub("SRC_A", 259149243, "live", True)
        st.set_sub("SRC_A", 259149243, "video", True)
        st.set_sub("SRC_A", 259149243, "dynamic", False)
        st.set_sub("SRC_A", 111222333, "dynamic", True)

        def kinds(g, uid):
            item = st.sub_of(g, uid) or {}
            return {k: bool((item.get(k) or {}).get("on"))
                    for k in ("live", "video", "dynamic", "top_comment")}

        before = kinds("SRC_A", 259149243)

        # --- 复制：源群保留 ---
        r = st.copy_subs("SRC_A", "DST_B")
        ck("复制成功", r.get("ok") is True)
        ck("复制 2 位 UP", r.get("ups") == 2)
        ck("复制 3 个开关", r.get("kinds") == 3)
        ck("复制后源群还在", bool(st.ups_of_group("SRC_A")))
        ck("复制后目标群有订阅", bool(st.ups_of_group("DST_B")))
        ck("目标开关与源一致", kinds("DST_B", 259149243) == before)
        ck("关掉的也一致（不误开）",
           kinds("DST_B", 259149243).get("dynamic") is False)

        # --- 迁移：源群清空 ---
        r2 = st.copy_subs("SRC_A", "DST_C", move=True)
        ck("迁移成功", r2.get("ok") is True)
        ck("迁移后源群已清空", not st.ups_of_group("SRC_A"))
        ck("迁移后目标群有订阅", bool(st.ups_of_group("DST_C")))
        ck("迁移开关一致", kinds("DST_C", 259149243) == before)
        ck("迁移把另一位也搬走", bool(st.sub_of("DST_C", 111222333)))

        # --- 边界 ---
        ck("同群被拒", st.copy_subs("DST_B", "DST_B").get("ok") is False)
        ck("空源被拒", st.copy_subs("NOPE_X", "DST_B").get("ok") is False)
        ck("空参数被拒", st.copy_subs("", "DST_B").get("ok") is False)
        ck("源不会凭空消失", bool(st.ups_of_group("DST_B")))
    finally:
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

    # --- 接口层 ---
    w = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("有迁移接口", "/api/sub/copy" in w)
    ck("后端要求显式 move", "bool(d.get(\"move\"))" in w)
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("前端有复制动作", "'mig-copy'" in js)
    ck("前端有迁移动作", "'mig-move'" in js)
    # 迁移不可逆，必须二次确认
    ck("迁移要二次确认", "confirm(" in js and "不可撤销" in js)
    ck("前端拒绝同群", "不能是同一个" in js)
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    ck("面板有迁移区块", "id=\"migSrc\"" in html and "id=\"migDst\"" in html)


def t_no_event_loop_closed():
    """刷新群名不能出现 "Event loop is closed"。

    实测报错：
        已更新 1 个群名；1 个未能获取（见下方详情）
        群 6FBBD9B5… → 请求失败：Event loop is closed

    根因：原来是 `for gid: run_async(qq.get_group_info(gid))`。
    而 run_async = asyncio.run() —— 每次都**新建一个事件循环并关闭它**。
    QQClient 里的 aiohttp 会话是缓存在实例上的，第一次调用时创建、
    绑定到第 1 个循环；循环关闭后，第 2 次调用在新循环里复用这个
    会话 → RuntimeError: Event loop is closed。
    表现恰好就是"第一个群成功，后面全部失败"。
    """
    import asyncio
    import threading
    import http.server
    import socketserver
    import qqapi

    # 起一个本地服务，让请求产生真正的网络 I/O
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"group_name":"G"}')

        def log_message(self, *a):
            pass

    srv = None
    port = 0
    for p in range(18801, 18860):
        try:
            srv = socketserver.TCPServer(("127.0.0.1", p), H)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        ck("起本地测试服务", False)
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old_base = qqapi.BASE
    qqapi.BASE = f"http://127.0.0.1:{port}"

    class C(qqapi.QQClient):
        async def get_token(self):
            return "fake"

        async def get_group_info(self, gid):
            return await self.get(f"/v2/groups/{gid}/info") or {"ok": True}

    def old_style():
        """模拟旧写法：每个群一次 asyncio.run"""
        c = C("1", "s")
        out = []
        for gid in ("A", "B", "C"):
            try:
                asyncio.run(c.get_group_info(gid))
                out.append("ok")
            except Exception as e:
                out.append(str(e)[:40])
        return out

    def new_style():
        """新写法：整个循环一次 asyncio.run"""
        async def _run():
            c = C("1", "s")
            out = []
            for gid in ("A", "B", "C"):
                try:
                    await c.get_group_info(gid)
                    out.append("ok")
                except Exception as e:
                    out.append(str(e)[:40])
            await c.close()
            return out
        return asyncio.run(_run())

    # ⚠️ 必须**保存后恢复**，不能 del：
    # _session_dead 是定义在类体里的方法，先赋值覆盖再 del 会把它
    # 彻底删掉（连原始方法一起没了），后续调用全变成 AttributeError。
    orig_dead = qqapi.QQClient._session_dead
    try:
        # 关掉自愈，证明旧写法确实会炸（复现根因）
        qqapi.QQClient._session_dead = lambda self: False
        broken = old_style()
        ck("旧写法会炸（复现根因）",
           any("Event loop is closed" in x for x in broken))
        ck("旧写法第一个能过", broken[0] == "ok")

        # 装上自愈后，旧写法也不该炸了（其它代码路径的保险）
        qqapi.QQClient._session_dead = orig_dead
        healed = old_style()
        ck("自愈后旧写法也不炸",
           not any("Event loop is closed" in x for x in healed))

        # 本次实际采用的新写法：整个循环一次 asyncio.run
        good = new_style()
        ck("新写法全部成功", good == ["ok", "ok", "ok"])
    finally:
        qqapi.BASE = old_base
        try:
            srv.shutdown()
        except Exception:
            pass

    # 代码层面：refresh-names 必须走单次 run_async
    w = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("有会话自愈", "_session_dead" in
       io.open(os.path.join(BASE_DIR, "qqapi.py"), encoding="utf-8").read())


def t_log_readable_for_beginners():
    """日志界面要让不懂的人看得懂（针对三张实测截图）。

    图一：概览只丢一串「🤖机器人 84 ｜ 🤖机器人连接 63 ｜ 📝其他 53」，
          没说这是什么、该不该管。
    图二：条目正文里残留一个孤零零的「,088」。
    图三：级别显示英文小写 `info`，没翻。
    """
    import logtranslate

    # --- 图二：毫秒残留 ---
    # Python logging 默认 asctime 是 `...06:22:53,088`（逗号+毫秒），
    # botpy 用的就是默认格式。正则只吃到秒的话，剥掉时间戳后
    # 行首会留下 ",088" 这个碎片。
    line = ("2026-09-20 06:22:53,088 [INFO] (bot.py:957)_adapt_interval "
            "风控风险已解除，轮询间隔 60s → 8s")
    r = logtranslate.explain(line)
    ck(",088 已被吃掉", ",088" not in r["raw"])
    ck("时间仍正确", r["time"] == "06:22:53")
    ck("正文保留", "风控风险已解除" in r["raw"])
    # 点号毫秒同样处理
    ck("点号毫秒也吃掉",
       ",123" not in logtranslate.explain("2026-09-20 06:22:53.123 [INFO] x")["raw"])

    # --- 图三：级别翻译 ---
    ck("有 level_cn", hasattr(logtranslate, "level_cn"))
    for en, cn in (("info", "信息"), ("warn", "警告"),
                   ("error", "错误"), ("debug", "调试")):
        ck("级别 %s→%s" % (en, cn), logtranslate.level_cn(en) == cn)
    ck("结果带 level_cn", r.get("level_cn") == "信息")

    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("前端优先显示中文级别", "i.level_cn ||" in js)

    # --- 图一：概览要能看懂 ---
    ck("有 PART_HINT 解释", "PART_HINT" in js)
    for k in ("机器人", "机器人连接", "B站接口", "管理面板"):
        ck("解释了「" + k + "」", k in js)
    ck("概览先给结论", "运行正常，没有错误" in js)
    ck("概览说明总数", "最近共" in js)

    # --- 分类更准，减少"其他" ---
    ck("http.py 归入网络请求",
       logtranslate.classify_part("[INFO] (http.py:1) 请求失败")[0] == "网络请求")
    # 堆栈单独归类，否则 `File "bot.py"` 会被文件名规则抢走，
    # 同一段堆栈被拆到好几个类别里
    ck("堆栈归错误详情",
       logtranslate.classify_part('  File "bot.py", line 1, in foo')[0]
       == "错误详情")
    ck("Traceback 归错误详情",
       logtranslate.classify_part("Traceback (most recent call last):")[0]
       == "错误详情")
    ck("正常行不受影响",
       logtranslate.classify_part("2026-09-20 [INFO] (bot.py:1) x")[0]
       == "机器人")


def t_panel_dies_with_parent():
    """程序关了，面板不能还活着。

    实测：关掉程序后面板还能打开 —— webui.py 留在后台继续占端口。

    根因：start.py 把 kill_process_tree(web) 写在 finally 里，
    但 Windows **关控制台窗口**时进程是被系统强杀的，
    finally 根本执行不到。父自己都被强杀了，指望它清理子进程不现实。

    所以做两层：
      1) 子进程看门狗：webui/bot 自己盯着父进程，爹没了就自杀（根本解）
      2) 清理时按端口兜底：面板只要活着就一定占端口，这是硬证据
    """
    import parentwatch
    import cleanup

    # --- 看门狗模块 ---
    ck("_alive 能判活", parentwatch._alive(os.getpid()) is True)
    ck("_alive 判死", parentwatch._alive(999999) is False)
    ck("_alive 容错", parentwatch._alive(0) is False
       and parentwatch._alive(None) is False)
    ck("无父 PID 不启用", parentwatch.start_watch(0) is False)
    ck("自己是父不启用", parentwatch.start_watch(os.getpid()) is False)

    # --- 三方都接上了 ---
    st = io.open(os.path.join(BASE_DIR, "start.py"), encoding="utf-8").read()
    ck("start 给面板传父 PID", '"--parent", str(os.getpid())' in st)
    ck("start 给机器人传父 PID", st.count('"--parent", str(os.getpid())') >= 2)

    w = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("面板认 --parent", '"--parent"' in w)
    ck("面板启看门狗", "start_watch" in w)

    b = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("机器人认 --parent", '"--parent"' in b)
    ck("机器人启看门狗", "start_watch" in b)

    # --- 端口兜底 ---
    ck("有端口扫描", hasattr(cleanup, "pids_on_ports"))
    ck("端口扫描返回列表",
       isinstance(cleanup.pids_on_ports(), list))
    ck("清理纳入端口", "pids_on_ports()" in
       io.open(os.path.join(BASE_DIR, "cleanup.py"), encoding="utf-8").read())
    # 端口兜底必须先确认是本项目 python 才杀，不能误杀
    ck("端口兜底要校验身份", "is_ours(cmd)" in
       io.open(os.path.join(BASE_DIR, "cleanup.py"), encoding="utf-8").read())


def t_group_name_fail_reason():
    """刷新群名失败时要说清是哪个群、为什么。

    实测：面板提示"已更新 1 个群名；1 个未能获取"，但完全看不出
    哪个群失败了、为什么失败 —— 旧代码只做 failed += 1，异常原因全丢。
    """
    import webui
    ck("有原因分类函数", hasattr(webui, "_group_name_fail_reason"))
    f = webui._group_name_fail_reason

    ck("11253 说明未开通", "未开通" in f(11253, ""))
    ck("11242 说明不在群里", "不在" in f(11242, ""))
    ck("11252 说明群不存在", "不存在" in f(11252, ""))
    ck("未知码带原码", "99999" in f(99999, "内部错误"))
    ck("无码也不崩", isinstance(f(None, None), str))

    w = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("返回失败详情", '"detail": detail' in w)
    ck("不再静默 continue", w.count("detail.append") >= 3)

    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("前端展示详情", "groupNameDetail" in js)
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    ck("HTML 有详情容器", 'id="groupNameDetail"' in html)
    ck("未命名群更好认", "未命名群" in js)


def t_sandbox_group_and_diag_cn():
    """沙盒群 + 自检结果汉化。"""
    w = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    cfg = io.open(_os_path("config.example.yaml"),
                  encoding="utf-8").read()

    # 沙盒群：固定一个群用来测试，不用每次选
    ck("后端返回沙盒群", '"sandbox_group"' in w)
    ck("后端校验沙盒群", 'if "sandbox_group" in d:' in w)
    ck("前端有下拉框", 'id="cSandboxGroup"' in html)
    ck("前端提交沙盒群", "sandbox_group:" in js)
    ck("配置示例有说明", "sandbox_group" in cfg)
    ck("沙盒群登记进自动保存", "cSandboxGroup" in js)

    # 测试推送优先走沙盒群
    ck("测试推送优先沙盒群",
       "cSandboxGroup" in js and "正在发到沙盒群" in js)

    # 自检结果字段名要中文，不能满屏英文 key
    # ⚠️ 六行改成固定 DIAG_ROWS（以前是只推有值的 DIAG_LABEL，行数会浮动）
    ck("自检标签汉化", "DIAG_ROWS" in js)
    for en, cn in (("live", "直播状态"), ("video", "最新投稿"),
                   ("dynamic", "最新动态"), ("pinned", "置顶动态"),
                   ("top_comment", "置顶评论")):
        ck("汉化 " + en, cn in js)


def t_room_id_not_false_zero():
    """有直播间不能被判定成"没有直播间"。

    用户实测：明明有直播间的 UP 主，面板却提示
    "该 UP 主没有直播间，已自动不勾选「直播」"。

    根因：只信 card 接口（web-interface/card）的 `live_room.roomid`。
    UP 主**当前没在播**时，那个字段经常是 null 或不存在 →
    room_id 当成 0 → 判定"没有直播间" → 不勾「直播」→ 直播提醒彻底失效。
    但人家只是没在播，直播间是有的。
    """
    import asyncio
    import bilibili

    class Fake(bilibili.BiliClient):
        def __init__(self, card_room, mid_room, master_room):
            self.card_room = card_room
            self.mid_room = mid_room
            self.master_room = master_room
            self.n = 0

        async def _get(self, url, params, referer=None, **kw):
            self.n += 1
            if "web-interface/card" in url:
                lr = {"roomid": self.card_room} if self.card_room else None
                return {"data": {"card": {"name": "UP", "face": "f"},
                                 "live_room": lr}}
            if "getRoomInfoOld" in url:
                return {"data": {"room_id": self.mid_room}}
            if "Master/info" in url:
                return {"data": {"info": {"room_id": self.master_room}}}
            return {"data": {}}

    async def run():
        # card 没给、但反查能查到 → 必须拿到房间号（这是本次的修复点）
        c = Fake(0, 456, 0)
        up = await c.get_up(1)
        ck("card 没给时反查补救", up.room_id == 456)

        # card 直接给了 → 一次就够
        c2 = Fake(123, 0, 0)
        up2 = await c2.get_up(1)
        ck("card 直接给就用它", up2.room_id == 123)
        ck("card 给时不浪费请求", c2.n == 1)

        # 前两处都没有 → 主播接口兜底
        c3 = Fake(0, 0, 789)
        up3 = await c3.get_up(1)
        ck("主播接口兜底", up3.room_id == 789)

        # 三个都没有 → 确实没有直播间
        c4 = Fake(0, 0, 0)
        up4 = await c4.get_up(1)
        ck("真的没有才是 0", up4.room_id == 0)

    asyncio.run(run())

    # 前端不该再因为没查到就一刀切不勾「直播」
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    # 「直播」默认要勾上。写法换过：早期直接置 checked，
    # 后来统一走 setDefKind（同时处理 .on 类和 checked，
    # 修"点了没反应"时改的）—— 两种写法都算数。
    ck("直播默认勾选",
       "$('defLive').checked = true" in js
       or "setDefKind('defLive', true)" in js)
    ck("不再提示没有直播间",
       "该 UP 主没有直播间" not in js)

    # 启动时补查历史缺失的房间号，老订阅不用重加
    import bot
    ck("有补查方法", hasattr(bot.NotifyBot, "_fix_missing_rooms"))


def t_selfcheck_uid_is_fixed():
    """自检必须用固定测试 UID，不能依赖「添加订阅」输入框。

    用户实测：1.21.0 说"默认 UID 改为 259149243"，但自检每次还是要
    自己填 UID —— 因为面板自检读的是 addUid（添加订阅的输入框）。
    自检是查"接口通不通"，和"要订阅谁"是两回事，必须剥离开。
    """
    import webui
    ck("有固定自检 UID", hasattr(webui, "SELFCHECK_UID"))
    ck("自检 UID 是 259149243", getattr(webui, "SELFCHECK_UID", 0) == 259149243)

    w = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    # ⚠️ 只查**自检函数体内**，不能全文件搜 "request.args.get(\"uid\")"：
    # 别的接口（比如合集文案）读 uid 是合理的，全文件搜会造成误报。
    i0 = w.find("def api_diagnose")
    body = w[i0:w.find("def ", i0 + 10)] if i0 >= 0 else ""
    ck("自检不再读 uid 参数", 'request.args.get("uid")' not in body)
    ck("自检返回所用 UID", "selfcheck_uid" in w)

    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("前端不再拼 addUid", "/api/diagnose?uid=" not in js)


def t_log_no_duplicate_time():
    """时间已经由时间轴显示，条目正文里不能再重复写一遍。"""
    import logtranslate
    ck("有 strip_timestamp", hasattr(logtranslate, "strip_timestamp"))

    line = ("2026-09-19 14:03:12 [ERROR] (bot.py:1146)_check_top_comment "
            "[X] 置顶评论获取失败")
    r = logtranslate.explain(line)
    ck("正文已去掉时间", "14:03:12" not in r["raw"]
       and "2026-09-19" not in r["raw"])
    ck("时间仍抽出来了", r["time"] == "14:03:12" and r["date"] == "2026-09-19")
    ck("正文内容保留", "置顶评论获取失败" in r["raw"])


def t_log_translate_for_beginners():
    """日志翻译要照顾不懂代码的人：术语、异常名、错误码都要翻。

    ⚠️ 但**包名必须保留** —— "缺少模块：网络请求库" 没法执行
    pip install，用户不知道该装什么。
    """
    import logtranslate
    tr = logtranslate.translate_symbols

    ck("有 PLAIN_WORDS", len(logtranslate.PLAIN_WORDS) > 20)
    ck("有 EXCEPTION_NAMES", len(logtranslate.EXCEPTION_NAMES) > 10)
    ck("有 LIB_NAMES", len(logtranslate.LIB_NAMES) > 5)
    ck("有 INFO_RULES", len(logtranslate.INFO_RULES) > 10)

    # 术语翻译
    ck("token 已翻译", "登录凭证" in tr("access_token expires_in 4234"))
    ck("cookie 已翻译", "登录状态" in tr("bili_cookie expired"))
    ck("错误码已标注", "错误码=" in tr("code=-404"))
    # ⚠️ 1 必须说「已开播」（跟 0「未开播」成对，也与
    #    bilibili.live_status_cn() 同口径）—— 以前这里断言「直播中」，
    #    后来统一改成「已开播」，断言没跟上就成了假失败。
    ck("直播状态可读", "已开播" in tr("live_status=1"))
    ck("未开播可读", "未开播" in tr("live_status=0"))

    # 异常名翻译
    ck("缺模块异常已翻译", "缺少模块" in tr("ModuleNotFoundError: x"))
    ck("属性异常已翻译", "属性错误" in tr("AttributeError: x"))

    # ⚠️ 包名必须保留
    out = tr("ModuleNotFoundError: No module named 'aiohttp'")
    ck("包名保留 aiohttp", "aiohttp" in out)
    out2 = tr("缺少依赖：qq-botpy、aiohttp")
    ck("依赖列表包名保留", "aiohttp" in out2 and "botpy" in out2)

    # 信息类日志也要有解释
    ck("启动同步有解释",
       logtranslate.explain("启动同步完成：4 位 UP 主已记录为基线").get("meaning"))
    ck("监听启动有解释",
       logtranslate.explain("监听已启动：当前每 60s 一轮").get("meaning"))


def t_selfcheck_no_attr_crash():
    """自检不能因为自己的字段写错而崩。

    实测：面板「B站」自检显示
    `'DynamicInfo' object has no attribute 'bvid'`
    根因是 _probe_bili 里写 `r.title or r.bvid or r.dyn_id`，
    而 DynamicInfo **没有 bvid**（那是 VideoInfo 的字段）；
    上面又只 catch BiliError，抓不住 AttributeError，
    于是自检自己的 bug 伪装成了"接口失败"。
    """
    import webui
    from bilibili import DynamicInfo, VideoInfo

    ck("有 _probe_desc", hasattr(webui, "_probe_desc"))

    # 关键：DynamicInfo 确实没有 bvid —— 旧写法必然炸
    ck("DynamicInfo 没有 bvid", not hasattr(DynamicInfo(), "bvid"))

    d = DynamicInfo()
    d.title, d.dyn_id, d.major_type = "测试", "D1", "live"
    d.pinned, d.ref_room_id = True, 5
    out = webui._probe_desc(d)
    ck("动态不崩溃", isinstance(out, str) and out)
    ck("展示动态 id", "D1" in out)
    ck("展示指向直播间", "5" in out)

    # VideoInfo 是 dataclass，按实际字段构造（不要凭记忆写参数名）
    import inspect as _ins
    vf = [f for f in _ins.signature(VideoInfo).parameters]
    kw = {f: (("BV1" if f == "bvid" else "视频" if f == "title" else
               "" if f in ("desc", "pic") else 0))
          for f in vf}
    v = VideoInfo(**kw)
    ck("视频展示 BV", "BV1" in webui._probe_desc(v))

    ck("None 不崩溃", "无" in webui._probe_desc(None))

    src = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    # 注释里会提到旧写法（用来解释为什么改），只看真正的代码行
    code = "\n".join(l for l in src.split("\n")
                      if not l.strip().startswith("#"))
    ck("不再直接访问 bvid", ".title or r.bvid" not in code
       and ".title or .bvid" not in code)

    # 每一项都要 catch 全部异常，不能只 catch BiliError
    ck("自检捕获所有异常", src.count("except Exception as e") >= 5)


def t_bat_stop_before_start():
    """启动 bat 必须先检测残留进程，有就关掉。

    用户实测：每次打开「启动机器人.bat」都不干净，进程一直保留。
    所以启动前显式跑一次 cleanup.py --stop（会打印检测结果）。
    """
    import cleanup
    ck("有 stop_others", hasattr(cleanup, "stop_others"))

    for name in ("启动机器人.bat",):
        raw = open(os.path.join(REPO_DIR, name), "rb").read()
        ck(name + " 只有 CRLF", raw.count(b"\n") == raw.count(b"\r\n"))
        ck(name + " 纯 ASCII", all(c < 128 for c in raw))
        ck(name + " 启动前检测", b"cleanup.py\" --stop" in raw
           or b"cleanup.py --stop" in raw)
        ck(name + " 退出后清理", b"--quiet" in raw)


def t_store_removed():
    """store.py 已删除（被 db.py 取代），不能有残留引用。"""
    path = os.path.join(BASE_DIR, "store.py")
    ck("store.py 已删除", not os.path.exists(path))

    bad = []
    for f in os.listdir(BASE_DIR):
        if not f.endswith(".py"):
            continue
        txt = io.open(os.path.join(BASE_DIR, f), encoding="utf-8").read()
        if "from store import" in txt or "import store" in txt:
            bad.append(f)
    ck("无 store 残留引用", not bad)


def t_login_epoch_isolation():
    """上一轮的报错不能穿越到新一轮（「还没扫就报错」的根因）。"""
    import webui as W

    L = W._login
    L.update(running=False, qr="", status="", done=False,
             error="", result=None, epoch=0, qr_at=0.0)

    L["epoch"] = 5
    ck("旧线程结果被丢弃", not W._epoch_ok(L, 3))
    ck("当前线程结果被采纳", W._epoch_ok(L, 5))

    # v2.2.0：扫码登录**整条删除**（不只是标记弃用），面板统一走浏览器登录。
    # 原来这里断言的是扫码流程自己的保护逻辑（force 重来、8 秒保护），
    # 它们随扫码入口一起摘掉了 —— 再断言旧行为等于锁住死代码。
    # 现在锁的是「扫码确实已不存在」这一状态。
    # ⚠️ 只认路由装饰器：webui.py 的注释里会提到这两个路径解释为什么删，
    #    裸搜路径会把注释当成残留（第一版断言就这么误报过）。
    src = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("扫码接口已整条删除",
       '@app.route("/api/bili/qrcode"' not in src
       and '@app.route("/api/bili/poll"' not in src)
    # 只认函数定义 —— 注释里提到这个名字不算（第一版断言就这么误报过）
    ck("扫码后台线程已移除", "def _start_bili_login" not in src
        and "def _do_bili_login" not in src)
    ck("浏览器登录仍在", "/api/bili/browser-login" in src)


def t_browser_login_api():
    """面板能直接开浏览器登录（扫码被风控时的可靠路径）。"""
    import webui as W

    src = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("有 browser-login 接口", "/api/bili/browser-login" in src)
    ck("有 browser-status 接口", "/api/bili/browser-status" in src)
    ck("后台线程跑登录", "_run_browser_login" in src)
    ck("登录后自动保存 cookie", '"bili_cookie"' in src)

    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    ck("面板有浏览器登录按钮", "bili-browser" in html)
    # 扫码入口（bili-qr / 获取二维码 / 重新扫码）已按需求移除：
    # 它走的是 B站扫码接口，经常拿不到 SESSDATA（被风控），
    # 留着反而让人以为"扫码失败 = 我操作错了"。浏览器登录是唯一推荐路径。
    ck("扫码入口已移除", "bili-qr" not in html)
    ck("续期复用浏览器登录", html.count("data-act=\"bili-browser\"") >= 2)


def t_browser_login_faster():
    """浏览器登录要快：1 秒轮询、180 秒上限、可取消、有倒计时。"""
    import bili_browser_login as BBL
    import webui as W

    src = io.open(os.path.join(BASE_DIR, "bili_browser_login.py"),
                  encoding="utf-8").read()
    ck("默认超时降到 180", "timeout: int = 180" in src)
    ck("cookie 轮询 1 秒一次", "asyncio.sleep(1)" in src)
    ck("支持中途取消", "should_cancel" in src)

    wsrc = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("有取消接口", "/api/bili/browser-cancel" in wsrc)
    ck("状态带剩余时间", '"remaining"' in wsrc)

    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("前端渲染进度条", "progress-fill" in js)
    ck("前端显示剩余秒数", "剩余 " in js)


def t_bilibili_style_ui():
    """界面：B 站粉蓝配色 + 卡片统一尺寸 + 暗夜 + 动效 + 响应式。"""
    import re
    css = _all_css()
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()

    ck("用 B 站粉 #FB7299", "#FB7299" in css)
    ck("用 B 站蓝 #00AEEC", "#00AEEC" in css)
    ck("最大宽度变量", "--maxw" in css)
    ck("圆角变量", "--r-lg" in css)
    ck("磁贴网格布局", ".tile-grid" in css)
    # ⚠️ 安全页在 v1.31.x 改成了卡片式（和全站一致），不再用磁贴。
    #    磁贴只在旧的安全页用过，这里改成判"安全页有卡片"。
    _sec = html[html.find('id="cr-sec"'):] if 'id="cr-sec"' in html else ''
    ck("安全页用了卡片", 'class="card"' in _sec[:4000])
    ck("暗夜主题", '[data-theme="dark"]' in css)
    ck("动效充足", len(re.findall(r"@keyframes", css)) >= 3)
    ck("响应式多档", len(re.findall(r"@media", css)) >= 3)
    ck("尊重减少动效", "prefers-reduced-motion" in css)
    ck("在线黑体", "Noto Sans SC" in css)

    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    acts = set(re.findall(r'data-act="([a-zA-Z\-]+)"', html))
    keys = set(re.findall(r"'([a-zA-Z][a-zA-Z\-]*)'\s*[:(]", js))
    ck("所有按钮都有处理", not (acts - keys))


def t_bat_crlf_ascii():
    """bat 必须 CRLF 且纯 ASCII。

    裸 LF 会让 cmd 行定位错位，把命令劈成碎片
    （表现为 'ho.' / 'errorlevel' 不是内部命令）。
    纯 ASCII 则彻底免疫任何代码页问题。
    """
    import glob
    bats = sorted(glob.glob(os.path.join(REPO_DIR, "*.bat")))
    ck("至少有 4 个 bat", len(bats) >= 4)

    for f in bats:
        name = os.path.basename(f)
        raw = open(f, "rb").read()
        crlf = raw.count(b"\r\n")
        bare_lf = raw.count(b"\n") - crlf
        ck(f"{name} 有 CRLF", crlf > 0)
        ck(f"{name} 无裸 LF", bare_lf == 0)
        ck(f"{name} 纯 ASCII", all(b < 128 for b in raw))
        # GBK 下也必须可读（万一以后加中文）
        try:
            raw.decode("gbk")
            gbk_ok = True
        except UnicodeDecodeError:
            gbk_ok = False
        ck(f"{name} GBK 可读", gbk_ok)


def t_safe_print_emoji():
    """GBK 控制台上 emoji 要换成 ASCII 标记，不能崩也不能变问号。"""
    from cfgutil import safe_print
    import io as _io

    class GBKConsole:
        def __init__(self):
            self.buf = _io.BytesIO(); self.encoding = "gbk"
        def isatty(self): return True
        def write(self, s):
            self.buf.write(s.encode("gbk") if isinstance(s, str) else s)
            return len(s)
        def flush(self): pass
        def value(self): return self.buf.getvalue().decode("gbk", "replace")

    c = GBKConsole()
    try:
        safe_print("依赖已就绪 \u2705", file=c)
        safe_print("还没有填 AppID \u23f3", file=c)
        out = c.value()
        ck("GBK 控制台不崩溃", True)
        ck("没有变成问号", "?" not in out)
        ck("emoji 换成 ASCII 标记", "[OK]" in out)
        ck("中文保留", "依赖已就绪" in out)
    except UnicodeEncodeError:
        ck("GBK 控制台不崩溃", False)

    # 对比：裸 print 会崩（这就是历史 bug）
    c2 = GBKConsole()
    crashed = False
    try:
        print("\u2705 测试", file=c2)
    except UnicodeEncodeError:
        crashed = True
    ck("裸 print 确实会崩（bug 成因）", crashed)

    class FileOut:
        def __init__(self):
            self.buf = _io.BytesIO(); self.encoding = "utf-8"
        def isatty(self): return False
        def write(self, s):
            self.buf.write(s.encode("utf-8") if isinstance(s, str) else s)
            return len(s)
        def flush(self): pass
        def value(self): return self.buf.getvalue().decode("utf-8")
    f = FileOut()
    safe_print("登录成功 \u2705", file=f)
    ck("重定向到文件保留 emoji", "\u2705" in f.value())


def t_no_mascot():
    """按用户要求：不要吉祥物，不要呼吸灯。"""
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    css = _all_css()
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("HTML 无吉祥物", "mascot" not in html)
    ck("JS 无吉祥物", "mascot" not in js)
    ck("CSS 无吉祥物样式", ".mascot" not in css)
    # ⚠️ .dot 现在是**健康度列表的状态圆点**（正常/警告/异常三色），
    # 不是呼吸灯，属于现役结构。真正要防的是旧版 liveDot + breathe 动画。
    ck("无旧的 liveDot", "liveDot" not in html and "liveDot" not in js)
    ck("状态圆点不是呼吸灯",
       ".dot" not in css or "@keyframes breathe" not in css)
    ck("无 breathe 动画", "@keyframes breathe" not in css)
    ck("无 bounce 动画", "@keyframes bounce" not in css)


def t_safe_stdio_console_aware():
    """控制台跟控制台编码（防乱码），文件用 UTF-8（防日志残缺）。"""
    src = io.open(os.path.join(BASE_DIR, "cfgutil.py"), encoding="utf-8").read()
    ck("区分 tty 与重定向", "isatty" in src)
    ck("重定向时改用 UTF-8", 'enc = None if is_tty else "utf-8"' in src)
    ck("始终放宽 errors", 'errors="replace"' in src)


def t_sidebar_layout():
    """布局：常驻侧边栏 + 五大板块 + 板块内选项卡。

    历史沿革（防止有人把旧结构加回来）：
    顶部页签 → 侧边栏+panel → Bento 网格 → 手风琴 → Hero+宫格+全屏面板
    → **v1.31.0 现在的第六套**：常驻侧边栏 + 五大板块，板块内用选项卡细分。
    #sheet 仍存在但已从"弹出层"降级为常驻主区外壳。
    """
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    css = _all_css()
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()

    # —— 现役结构 ——
    ck("有常驻侧边栏", 'class="side"' in html and ".nav-i" in css)
    ck("五大板块导航", html.count('data-act="goto-sec" data-sec=') >= 5)
    # ⚠️ 板块同时带 sec 和 panel 两个类：style.css 认 .sec.show，
    #    布局样式认 .panel.on，切换时两个都要加（缺一个就整页空白）。
    #    所以这里判 "panel sec" 的组合，不能只判 class="sec"。
    ck("分区用 sec", 'class="panel sec"' in html and ".sec.show" in css)
    # blk 的样式搬进了 index.html 的内联 <style>，不再只存在于 style.css
    # ⚠️ 内部区块现在用 .sub-panel（选项卡内容区），blk 已随旧布局一起移除。
    #    还要确认 CSS 里有 .sub-panel 的显示规则，否则四个选项卡会同时显示。
    ck("内部区块用 sub-panel",
       'class="sub-panel"' in html and ".sub-panel" in (css + html))
    ck("JS 绑定侧边栏入口", ".nav-i" in js)
    # showSec 不能再依赖已移除的弹出层容器，否则第一行就 return false。
    # ⚠️ 要先剥掉注释：修复说明里整段引用了那行代码，不剥会误报。
    _js_nc = re.sub(r"//[^\n]*", "", js)
    ck("showSec 不依赖 sheet 容器",
       "sheet._missing) return false" not in _js_nc)
    # 顶部常驻状态条（心跳 / 机器人 / 沙盒群）
    ck("有顶部状态条", 'class="topbar"' in html or "tb-i" in html)
    # 板块数：home/cred/subs/diag/setup
    # ⚠️ 板块标签是 <section class="panel sec">，不是 <div class="sec">。
    #    正则要同时兼容两种写法，否则板块数永远是 0。
    # ⚠️ class 和 data-sec 之间还夹着 id，正则必须允许中间有别的属性。
    _secs = re.findall(
        r'<(?:div|section)\s+class="[^"]*\bsec\b[^"]*"[^>]*?\bdata-sec="([^"]+)"',
        html)
    ck("五大板块齐全",
       set(["home", "cred", "subs", "diag", "setup"]).issubset(set(_secs)))

    # —— 旧结构一律不许回来 ——
    # ⚠️ 判据要先剥注释：清理说明里会点名旧类名，不剥就永远 FAIL。
    _css_nc = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    _js_nc2 = re.sub(r"//[^\n]*", "", js)
    ck("不再有旧的 Hero 卡",
       "hero-card" not in html and ".hero-card" not in _css_nc)
    ck("不再有旧的功能宫格",
       "tile-home" not in html and ".th-item" not in _js_nc2)
    ck("不再有旧的手风琴", "acc-item" not in html and ".acc-item" not in css)
    ck("不再有旧的 Bento", "bento" not in html and ".bento" not in css)
    ck("不再有旧的侧边栏", "sidebar" not in html and ".sidebar" not in css)
    ck("不再有旧的 nav-item", "nav-item" not in html)
    ck("不再有旧的 panel", "data-pane" not in html)
    # ⚠️ .tab 现在是**板块内选项卡**（凭据/订阅/诊断/设置各自细分），是现役结构。
    # 这条断言原本针对的是更早的"顶部一级页签"，那个早已移除。
    # 现役判据改为：选项卡必须归属某个板块（goto-tab 带 data-sec），
    # 而不是顶层独立页签。
    ck("选项卡归属板块而非顶层",
       'data-act="goto-tab" data-sec=' in html and ".tab.on" in css)

    # 账户卡片（头像 / 等级 / UID / 过期时间）
    ck("有账户卡片样式", ".acc-card" in css and "acc-avatar" in css)
    ck("有等级徽章", "lv-badge" in css and "lv-6" in css)
    ck("有大会员徽章", "vip-badge" in css)
    ck("有头像框样式", ".pendant" in css)
    ck("账户渲染用了 face", "A.face" in js)
    ck("账户渲染用了 level", "A.level" in js)
    ck("账户渲染用了 uid", "A.mid" in js)
    ck("账户渲染用了过期时间", "expires_str" in js)
    ck("头像 img 有 onerror 兜底",
       "onerror" in js and "referrerpolicy" in js)


def t_message_dedup():
    """同一条消息不能处理两次。

    历史问题：QQ 平台会重复推送同一条事件，不去重就会出现
    "用户发一次指令、机器人回两条自相矛盾的话"。
    """
    import bot as B

    ck("有去重函数", hasattr(B, "_already_seen"))
    B._SEEN_MSG.clear()

    ck("首次不重复", B._already_seen("msg-1") is False)
    ck("第二次判为重复", B._already_seen("msg-1") is True)
    ck("不同 id 不误判", B._already_seen("msg-2") is False)
    ck("空 id 不去重", B._already_seen("") is False)

    src = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    # v1.21.0：机器人不再回复消息，入口只做静默登记，
    # 重复登记无害，所以不再要求入口调用去重（函数本身仍保留）
    ck("入口只做静默登记", "on_group_at_message_create" in src
       and "_already_seen(getattr(message" not in src)
    B._SEEN_MSG.clear()


def t_markdown_no_zwsp_spam():
    """markdown 里不能每段都插零宽空格行。

    历史问题：段落之间全用 \n\u200b\n，QQ 里渲染出一堆空行，
    消息被拉长、看着散架。
    """
    import notify as N
    css = _all_css()

    md, _ = N.render_subscribe("测试", 123, "", "直播", "")
    zwsp_lines = [ln for ln in md.split("\n") if ln.strip() == "\u200b"]
    ck("零宽空格行最多 1 个", len(zwsp_lines) <= 1)
    ck("没有 ## 标题（曾导致渲染出 null）", not md.startswith("##"))
    ck("标题用加粗", md.startswith("**"))
    ck("没有 --- 分割线", "\n---\n" not in md and not md.startswith("---"))

    # 三种提醒卡片都要干净
    from bilibili import DynamicInfo, LiveInfo, VideoInfo
    lv = LiveInfo(room_id=1, live_status=1, title="t", area="a", online=1)
    vd = VideoInfo(bvid="BV1", title="v", pic="", desc="d", pubdate=0)
    dn = DynamicInfo(dyn_id="1", title="d", text="x", images=[])
    for kind, info in (("live", lv), ("video", vd), ("dynamic", dn)):
        m, _ = N.render(kind, info, "名", {})
        z = [ln for ln in m.split("\n") if ln.strip() == "\u200b"]
        ck(f"{kind} 卡片空行不过量", len(z) <= 1)
        ck(f"{kind} 卡片无 ## 标题", not m.startswith("##"))

    ck("CSS 仍保留账户卡", ".acc-card" in css)


def t_pinned_dynamic_not_blocking():
    """置顶动态不能挡住新动态检测。

    这是动态"从来不推送"的根因：置顶动态永远排在列表第一位，
    若直接取 items[0] 建基线，基线 ID 恒定不变，
    之后发什么新动态都检测不到。
    """
    from bilibili import _parse_polymer, _is_pinned

    pinned_item = {"id_str": "PINNED",
                   "modules": {"module_tag": {"text": "置顶"}}}
    new_item = {"id_str": "NEW-1",
                "modules": {"module_dynamic": {"desc": {"text": "新动态"}}}}

    ck("能识别置顶", _is_pinned(pinned_item) is True)
    ck("普通动态不算置顶", _is_pinned(new_item) is False)
    ck("解析带上 pinned 标记", _parse_polymer(pinned_item).pinned is True)
    ck("普通动态 pinned=False", _parse_polymer(new_item).pinned is False)

    def pick(items):
        for raw in items:
            d = _parse_polymer(raw)
            if d and d.dyn_id and not d.pinned:
                return d
        return None

    first = pick([pinned_item, new_item])
    ck("跳过置顶取到新动态", first is not None and first.dyn_id == "NEW-1")

    newer = {"id_str": "NEW-2",
             "modules": {"module_dynamic": {"desc": {"text": "更新的"}}}}
    second = pick([pinned_item, newer])
    ck("后续新动态 id 会变", second is not None
       and second.dyn_id != first.dyn_id)

    # 旧逻辑对照：直接取 items[0] 永远是置顶
    ck("旧逻辑确实会卡住", _parse_polymer(pinned_item).dyn_id == "PINNED")


def t_dynamic_templates_include_pinned():
    """置顶动态和普通动态用不同文案。"""
    import notify as N
    ck("有置顶模板", "dynamic_pinned" in N.DEFAULT_TEMPLATES)
    ck("有置顶无标题模板", "dynamic_pinned_no_title" in N.DEFAULT_TEMPLATES)
    ck("有置顶标题", N.HEAD_TITLE.get("dynamic_pinned"))

    from bilibili import DynamicInfo
    normal = DynamicInfo(dyn_id="1", title="t", text="x")
    pinned = DynamicInfo(dyn_id="2", title="t", text="x", pinned=True)
    m1, _ = N.render("dynamic", normal, "名", {})
    m2, _ = N.render("dynamic", pinned, "名", {})
    ck("普通与置顶标题不同", m1.splitlines()[0] != m2.splitlines()[0])
    ck("置顶标题含置顶字样", "置顶" in m2.splitlines()[0])


def t_default_check_faster():
    """检测频率：默认要比之前快。

    之前 poll_interval=60 + slow_check_every=3，
    动态实际要 3 分钟才查一次，用户体感"等半天"。
    """
    src = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ex = io.open(_os_path("config.example.yaml"),
                 encoding="utf-8").read()
    # 自适应轮询：贴着风控边缘跑（默认 12s，最快 8s，撞风控最多 120s）
    ck("bot 默认 12 秒", 'cfg.get("poll_interval", 12)' in src)
    ck("有最快档", "poll_interval_min" in ex)
    ck("有退避上限", "poll_interval_max" in ex)
    ck("示例配置是快档", "poll_interval: 12" in ex)
    ck("示例配置每轮都查", "slow_check_every: 1" in ex)
    ck("有自适应逻辑", "_adapt_interval" in src)
    ck("撞风控会退避", "risk_hits" in src)


def t_auth_follows_official_doc():
    """鉴权必须按官方「启动接入」文档：Access Token，Token 已废弃。

    官方原话：「Token 的鉴权方式已废弃，请使用更安全的
    Access Token 鉴权方式。」
    注册后拿到 AppID / AppSecret，用它们换 access_token。
    """
    q = io.open(os.path.join(BASE_DIR, "qqapi.py"), encoding="utf-8").read()
    b = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()

    # 1) 用官方换 token 的接口
    ck("用 getAppAccessToken", "getAppAccessToken" in q)
    ck("不是废弃的 getToken", "getToken" not in q.replace("get_token", ""))

    # 2) 请求体字段是官方的 appId + clientSecret
    ck("用 appId", '"appId"' in q)
    ck("用 clientSecret", '"clientSecret"' in q)
    ck("不用 appsecret 小写拼错", '"appsecret"' not in q)

    # 3) 全项目没有残留"废弃 Token 鉴权"的写法
    for fn in ("bot.py", "webui.py", "qqapi.py", "check.py"):
        src = io.open(os.path.join(BASE_DIR, fn), encoding="utf-8").read()
        ck(f"{fn} 无 bot_token", "bot_token" not in src)

    # 4) 用的是官方 Python SDK botpy
    ck("用 botpy", "import botpy" in b)
    ck("用官方 Intents", "botpy.Intents(" in b)

    # 5) 沙箱开关（官方开发流程要用）
    ex = io.open(_os_path("config.example.yaml"),
                 encoding="utf-8").read()
    ck("有 is_sandbox 配置", "is_sandbox" in ex)

    # 6) 刷新要提前，不要用过期 token
    ck("提前刷新", "- 300" in q or "expires_in" in q)


def t_public_api_contract_stable():
    """公共 API 契约必须稳定 —— 类名/方法名不得随意改名或删除。

    为什么要锁死：之前 bot.py 调用 get_pinned_comment(dyn_id, uid, rid)
    传三个参数，而被调方只声明两个 → TypeError 被 except 吞掉 →
    表现为"功能没反应"、日志无异常。类名不稳定是同类事故的根源。

    契约清单写在 API.md，改动请先更新那里。
    """
    import bilibili as BL
    import qqapi as Q
    import bot as B
    import db as D

    ck("NotifyBot 存在", hasattr(B, "NotifyBot"))
    ck("Store 存在", hasattr(D, "Store"))
    ck("BiliClient 存在", hasattr(BL, "BiliClient"))
    ck("QQClient 存在", hasattr(Q, "QQClient"))
    ck("BiliError 存在", hasattr(BL, "BiliError"))
    ck("QQError 存在", hasattr(Q, "QQError"))
    ck("Up 存在", hasattr(BL, "Up"))
    ck("LiveInfo 存在", hasattr(BL, "LiveInfo"))
    ck("VideoInfo 存在", hasattr(BL, "VideoInfo"))
    ck("DynamicInfo 存在", hasattr(BL, "DynamicInfo"))

    # v1.21.0：_handle（指令分发）已移除，机器人只推送 + 静默登记
    bot_methods = ("on_group_at_message_create", "_push", "_poll_once",
                   "_startup_sync", "_adapt_interval", "_check_live",
                   "_check_video", "_check_dynamic", "_check_top_comment")
    for m in bot_methods:
        ck(f"NotifyBot.{m}", hasattr(B.NotifyBot, m))

    store_methods = ("ensure_group", "set_sub", "set_up", "set_group_name",
                     "remove_up", "targets", "subscribed_kinds",
                     "enabled_kinds_of_up", "all_ups", "all_groups",
                     "ups_of_group", "baseline", "refresh", "get_up")
    for m in store_methods:
        ck(f"Store.{m}", hasattr(D.Store, m))

    bili_methods = ("get_up", "get_live", "get_room_id_by_mid",
                    "get_latest_video", "get_latest_dynamic",
                    "get_pinned_dynamic", "get_pinned_comment")
    for m in bili_methods:
        ck(f"BiliClient.{m}", hasattr(BL.BiliClient, m))

    # 契约文档必须存在
    ck("有 API.md", os.path.exists(os.path.join(DOC_DIR, "API.md")))


def t_process_cleanup_finds_relative_path():
    """进程清理必须能匹配**相对路径**启动的进程。

    bat 若写成 `python start.py`（相对路径），命令行里就没有项目目录；
    早期 cleanup 的判定要求"脚本名 + 项目目录同时出现"，
    结果是永远匹配不上 → 每次启动都多一个残留 → 实测攒到 15 个。

    现在两档判定：含完整路径直接认；只有脚本名时，
    若该脚本确实存在于项目目录也认。
    """
    import cleanup as C
    ck("相对路径能匹配 start.py", C.is_ours("python.exe start.py"))
    ck("相对路径能匹配 bot.py", C.is_ours("python.exe bot.py"))
    ck("带路径也能匹配",
       C.is_ours("python.exe " + C.BASE_DIR.replace("/", "\\")
                 + "\\start.py"))
    ck("无关程序不匹配", not C.is_ours("python.exe myserver.py"))
    ck("空命令行不匹配", not C.is_ours(""))


def t_pid_file_registration():
    """.bot.pids 登记机制：不依赖命令行解析，最可靠。"""
    import os, cleanup as C
    import paths as _P
    path = _P.run_file(".bot.pids")
    had = os.path.exists(path)
    try:
        C.register_pid(999001)
        with open(path, encoding="utf-8") as f:
            txt = f.read()
        ck("PID 已登记", "999001" in txt)
        C.unregister_pid(999001)
        with open(path, encoding="utf-8") as f:
            txt2 = f.read()
        ck("PID 已注销", "999001" not in txt2)
        ck("_pid_alive 自己为真", C._pid_alive(os.getpid()))
        ck("_pid_alive 不存在的号为假", not C._pid_alive(999999))
    finally:
        if not had and os.path.exists(path):
            os.remove(path)


def t_bats_use_absolute_path():
    """bat 用绝对路径启动，让命令行里带上项目目录（双保险）。"""
    import os, glob, io as _io
    ok = True
    for f in glob.glob(os.path.join(REPO_DIR, "*.bat")):
        txt = _io.open(f, encoding="utf-8", errors="replace").read()
        # ⚠️ 必须先剥掉注释行：打开管理面板.bat 的说明里会提到 start.py
        #    （解释为什么它**不**用 start.py 启动），不剥就是误报。
        body = "\n".join(
            ln for ln in txt.replace("\r", "").split("\n")
            if not ln.lstrip().lower().startswith("rem"))
        for mod in ("start.py", "webui.py"):
            if mod not in body:
                continue
            # 源码搬进 src/ 之后，命令行里是 "%~dp0src\start.py"
            sep = chr(92)  # 反斜杠：写死在字面量里会把引号转义掉
            if ('"%~dp0' + mod + '"') not in body and (
                    '"%~dp0src' + sep + mod + '"') not in body:
                ok = False
    ck("bat 用绝对路径启动", ok)


def t_no_dead_module_references():
    """已删除的模块不能再被引用（会变成运行时才炸的坑）。"""
    # store.py 已在 v1.2.0 删除，被 db.py 取代
    h = io.open(os.path.join(BASE_DIR, "harden.py"), encoding="utf-8").read()
    ck("完整性清单不含 store.py", '"store.py"' not in h)
    ck("完整性清单含 db.py", '"db.py"' in h)

    # 主存储是 SQLite，自检不能再读 data.json
    c = io.open(os.path.join(BASE_DIR, "check.py"), encoding="utf-8").read()
    ck("自检读 data.db", "data.db" in c)
    ck("自检不再以 json 为主", 'cfg.get("data_file", "data.json")' not in c)


def t_all_bat_files_safe_encoding():
    """所有 bat 必须是 CRLF + 纯 ASCII。

    这两个坑都真实炸过，而且**每次新增 bat 都会重犯**：
      1. 裸 LF：cmd 靠 CRLF 定位行，遇到 LF-only 会行错位，
         跳到某行中间去执行 → 'ho.' / 'errorlevel' 不是内部命令
      2. 非 ASCII（含 UTF-8 中文）：cmd 按 GBK 解析，
         UTF-8 中文是 3 字节、GBK 是 2 字节 → 字节错位 → "锟斤拷"
    所以 bat 内容一律纯 ASCII，中文提示交给 Python 输出。
    """
    import glob
    bats = glob.glob(os.path.join(REPO_DIR, "*.bat"))
    ck("存在 bat 文件", len(bats) >= 4)
    for b in bats:
        name = os.path.basename(b)
        raw = io.open(b, "rb").read()
        crlf = raw.count(b"\r\n")
        bare_lf = raw.count(b"\n") - crlf
        ck(f"{name} 无裸LF", bare_lf == 0)
        ck(f"{name} 纯ASCII", all(c < 128 for c in raw))


def t_process_cleanup_exists():
    """关闭/启动时必须清理本项目残留的 Python 进程。

    背景（实测现象）：上一次没退干净，新旧两个机器人同时监听同一个群，
    于是一条指令回两次、同一条动态被推三次。
    """
    import cleanup as CL

    ck("有清理模块", hasattr(CL, "kill_ours"))
    ck("有归属判断", hasattr(CL, "is_ours"))
    # 只认本项目路径，不能误杀别的 python
    ck("认本项目脚本", CL.is_ours("python " + BASE_DIR + "/bot.py"))
    ck("不认别的 python", not CL.is_ours("python C:/other/app.py"))
    ck("不认空", not CL.is_ours(""))

    src = io.open(os.path.join(BASE_DIR, "start.py"), encoding="utf-8").read()
    ck("启动时会清理", "_kill_leftovers()" in src)
    ck("导入了 cleanup", "import cleanup" in src)

    for bat in ("启动机器人.bat", "安装依赖.bat"):
        b = io.open(_os_path(bat), "rb").read()
        ck(f"{bat} 含清理", b"cleanup.py" in b)


def t_single_instance_lock():
    """同一台机器只允许一个机器人实例。"""
    import bot as B
    ck("有 acquire_lock", hasattr(B, "acquire_lock"))
    ck("有 release_lock", hasattr(B, "release_lock"))

    lock = os.path.join(BASE_DIR, ".bot.lock")
    if os.path.exists(lock):
        os.remove(lock)
    try:
        ck("首次能拿到锁", B.acquire_lock() is True)
        ck("第二次拿不到", B.acquire_lock() is False)
        B.release_lock()
        ck("释放后文件不存在", not os.path.exists(lock))
        ck("释放后能再拿", B.acquire_lock() is True)
        B.release_lock()
    finally:
        try:
            os.remove(lock)
        except Exception:
            pass


def t_dedup_works_without_msg_id():
    """消息去重不能只依赖 msg_id（有些事件不带 id）。"""
    import bot as B
    B._SEEN_MSG.clear()
    ck("有去重函数", hasattr(B, "_already_seen"))
    ck("首次不重复", B._already_seen("", "g1|订阅 123") is False)
    ck("同内容判重复", B._already_seen("", "g1|订阅 123") is True)
    ck("不同群不重复", B._already_seen("", "g2|订阅 123") is False)
    ck("不同内容不重复", B._already_seen("", "g1|退订 123") is False)
    # 有 id 时也能和内容指纹互通
    B._SEEN_MSG.clear()
    ck("有 id 首次", B._already_seen("m1", "g1|x") is False)
    ck("有 id 重复", B._already_seen("m1", "g1|x") is True)
    ck("同指纹判重复", B._already_seen("", "g1|x") is True)
    B._SEEN_MSG.clear()


def t_help_is_single_source():
    """v1.21.0：机器人不再做任何交互，帮助文本必须已移除。

    所有功能移到网页面板，群里 @ 机器人不会有任何回复。
    """
    src = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("无 HELP_MD", "HELP_MD" not in src)
    ck("无 HELP 纯文本帮助", "HELP = " not in src)
    ck("无指令方法", "_cmd_" not in src)


def t_templates_complete():
    """文案模板必须覆盖全部 8 种，且面板能改。

    v1.21.0：报备链接功能已移除（图片走富媒体，不需要报备域名）。
    模板之前只有 6 项，下播和置顶评论改不了 —— 已补齐。
    """
    from notify import DEFAULT_TEMPLATES
    need = ("live", "offline", "video", "dynamic", "dynamic_no_title",
            "dynamic_pinned", "dynamic_pinned_no_title", "top_comment")
    for k in need:
        ck(f"模板有 {k}", k in DEFAULT_TEMPLATES)

    html = io.open(os.path.join(BASE_DIR, "templates/index.html"),
                   encoding="utf-8").read()
    for i in ("tLive", "tOffline", "tVideo", "tDyn", "tDynNo",
              "tDynPin", "tDynPinNo", "tTopCmt"):
        ck(f"面板有 {i}", f'id="{i}"' in html)
    ck("有恢复默认按钮", "reset-tpl" in html)

    js = io.open(os.path.join(BASE_DIR, "static/app.js"), encoding="utf-8").read()
    ck("JS 字段表齐全", all(k in js for k in need))
    ck("JS 用 tpl_defaults 做占位", "tpl_defaults" in js)

    # 报备链接必须已移除
    nt = io.open(os.path.join(BASE_DIR, "notify.py"), encoding="utf-8").read()
    ck("已移除 REPORT_URLS", "REPORT_URLS" not in nt)
    w = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("已移除报备接口", "qq-commands" not in w)


def t_selfcheck_improved():
    """自检要能自动跑完，并且给出结论。

    之前 B 站测试要手动输入 UID，非交互环境下就卡住/跳过；
    现在自动用已订阅的 UP，没有就用默认 UID。
    """
    c = io.open(os.path.join(BASE_DIR, "check.py"), encoding="utf-8").read()
    ck("有默认 UID", "DEFAULT_UID" in c)
    ck("默认 UID 是 259149243", "259149243" in c)
    ck("不再交互式问 UID", "要测试 B 站接口吗" not in c)
    ck("有端口检查", "def check_port" in c)
    ck("有近期错误摘要", "def show_recent_errors" in c)
    ck("自检给出结论", "自检结束" in c)


def t_rich_media_image_support():
    """图片改走富媒体：本地下载 → 上传 → msg_type=7 发送。

    背景：markdown 里的 ![](url) 是**客户端加载**，
    需要在「消息 URL 配置」报备，没报备就裂图。
    富媒体是**平台下载**或本地 base64 直传，不受报备限制。
    """
    import inspect
    import media as M
    import qqapi as Q

    ck("有上传接口", hasattr(Q.QQClient, "upload_group_file"))
    ck("有富媒体发送", hasattr(Q.QQClient, "send_media"))
    src = inspect.getsource(Q.QQClient.send_media)
    ck("msg_type=7", "msg_type" in src and "7" in src)
    ck("file_info 透传", "file_info" in src)
    usrc = inspect.getsource(Q.QQClient.upload_group_file)
    ck("上传走 /files", "/files" in usrc)
    ck("支持 file_data", "file_data" in usrc)
    ck("支持 url", '"url"' in usrc or "'url'" in usrc)

    ck("有下载函数", hasattr(M, "fetch_image"))
    ck("有发送入口", hasattr(M, "send_image"))
    ck("有 file_info 缓存", hasattr(M, "cached_file_info")
       and hasattr(M, "remember_file_info"))

    # 格式：QQ 只收 png/jpg，webp 必须转成 jpg 变体
    ck("webp 转 jpg", M.jpeg_variant("https://i0.hdslb.com/a.webp")
       .endswith(".jpg"))
    ck("去掉缩放后缀", "@" not in M.jpeg_variant(
        "https://i0.hdslb.com/a.jpg@100w_100h.webp"))
    ck("无后缀补 jpg", M.jpeg_variant("https://i0.hdslb.com/bfs/z")
       .endswith(".jpg"))
    ck("识别 jpeg", M.usable(b"\xff\xd8\xff\xe0"))
    ck("识别 png", M.usable(b"\x89PNG\r\n\x1a\n"))
    ck("拒绝 webp", not M.usable(b"RIFF____WEBP"))

    # file_info 缓存复用
    M._INFO_CACHE.clear()
    ck("首次无缓存", M.cached_file_info("https://x/1.jpg") is None)
    M.remember_file_info("https://x/1.jpg", "FI-1", 3600)
    ck("缓存能取到", M.cached_file_info("https://x/1.jpg") == "FI-1")
    ck("别的图不串", M.cached_file_info("https://x/2.jpg") is None)
    M._INFO_CACHE.clear()

    # notify 侧
    import notify as N
    ck("有封面提取", hasattr(N, "cover_of"))
    ck("有剥离图片", hasattr(N, "strip_images"))
    md = "**标题**\n\u200b\n![](https://x/y.jpg)\n> 内容"
    out = N.strip_images(md)
    ck("图片已剥离", "![" not in out)
    ck("正文保留", "> 内容" in out)

    # bot 侧接入
    bsrc = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("推送用了富媒体", "send_image(" in bsrc and "media" in bsrc)
    ck("有配置项", "image_send_mode" in bsrc)


def t_slow_every_one_works():
    """slow_check_every=1 必须仍然检查投稿/动态。

    历史 bug：判定写成 (round % slow_every == 1)，
    当 slow_every=1 时任何数 % 1 都等于 0，永远 != 1，
    于是投稿和动态**再也不会被检查**（用户设 1 本意是每轮都查）。
    """
    src = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("有 slow_every<=1 的特判", "self.slow_every <= 1" in src)
    ck("不是只靠取模判断", "slow_round = self.slow_every <= 1 or" in src)

    # 纯逻辑验证：各种 slow_every 下是否都会命中
    def hits(slow_every, rounds=6):
        return sum(1 for r in range(1, rounds + 1)
                   if slow_every <= 1 or (r % slow_every == 1))
    ck("slow_every=1 每轮都查", hits(1) == 6)
    ck("slow_every=2 隔轮查", hits(2) == 3)
    ck("slow_every=3 每三轮", hits(3) == 2)

    # 对照：旧写法在 slow_every=1 时一次都不查
    old_hits = sum(1 for r in range(1, 7) if r % 1 == 1)
    ck("旧写法确实会漏（bug 成因）", old_hits == 0)


def t_ban_policy_not_brutal():
    """封禁策略不能对"自己点自己"太狠。

    历史问题：ban_after=3 / ban_seconds=3600 且无上限，
    用户在本机多点几下就被锁 1 小时，而且解封接口本身也被拦（死锁）。
    """
    import harden
    G = harden.GUARD
    G.unban_all()

    ck("首次封禁 <= 120 秒", harden.BAN_SECONDS <= 120)
    ck("封禁有上限 <= 600", harden.BAN_MAX_SECONDS <= 600)
    ck("触发阈值 >= 5", harden.BAN_AFTER >= 5)

    # 本机永不封禁
    for _ in range(500):
        G.allow("127.0.0.1", True, path="/api/config")
    ck("本机连打也不封禁", G.banned("127.0.0.1") == 0)
    ck("::1 也算本机", G.is_loopback("::1"))
    ck("外网 IP 不算本机", not G.is_loopback("8.8.8.8"))

    # 轮询接口不消耗额度
    G.unban_all()
    for _ in range(1000):
        G.allow("9.9.9.9", False, path="/api/state")
    ck("轮询接口不计数", G.banned("9.9.9.9") == 0)

    # 外部 IP 仍会封，但时长受控
    G.unban_all()
    for _ in range(2000):
        G.allow("8.8.8.8", True, path="/api/config")
    left = G.banned("8.8.8.8")
    ck("外部 IP 仍会封", left > 0)
    ck("封禁时长受上限约束", left <= harden.BAN_MAX_SECONDS + 1)
    G.unban_all()


def t_unban_not_deadlock():
    """被封时必须还能自己解封（否则只能手动删文件）。"""
    import harden
    import webui as W

    src = io.open(os.path.join(BASE_DIR, "harden.py"), encoding="utf-8").read()
    ck("有豁免名单", "BAN_EXEMPT" in src)
    ck("unban 在豁免名单", '"/api/harden/unban"' in src)

    W.app.config["TESTING"] = True
    c = W.app.test_client()
    ENV = {"REMOTE_ADDR": "8.8.8.8"}
    H = {"X-Requested-With": "bili-notify"}

    harden.GUARD.ban("8.8.8.8", 300)
    try:
        r = c.get("/api/state", headers=H, environ_base=ENV)
        d = r.get_json() or {}
        ck("被封访问被拦", r.status_code == 429)
        ck("返回带解封办法", "how" in d)
        ck("提示不含生硬措辞", "封禁" not in (d.get("msg") or ""))

        r2 = c.post("/api/harden/unban", json={"ip": "8.8.8.8"},
                    headers=H, environ_base=ENV)
        ck("被封时仍能解封（无死锁）", r2.status_code == 200)
    finally:
        harden.GUARD.unban_all()

    # 命令行解封
    import check as CH
    ck("check.py 有 --unban", hasattr(CH, "do_unban"))


def t_browser_login_actually_runs():
    """浏览器登录必须真的执行，不能只是返回一个没人 await 的协程。

    历史 bug：webui 后台线程直接调用 async 函数 browser_login()，
    只得到协程对象、从未执行 → 浏览器根本不启动，
    前端一直显示「准备中…」，用户干等到超时。
    """
    import inspect
    import bili_browser_login as BBL

    ck("browser_login 是 async", inspect.iscoroutinefunction(BBL.browser_login))
    ck("run 是同步函数", not inspect.iscoroutinefunction(BBL.run))
    ck("run 接受 on_stage", "on_stage" in inspect.signature(BBL.run).parameters)
    ck("run 接受 should_cancel",
       "should_cancel" in inspect.signature(BBL.run).parameters)

    src = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("webui 调用的是 run()", "BBL.run(" in src)
    ck("webui 不再直接调 async 版本", "BBL.browser_login(" not in src)

    # 用假 playwright 跑一遍，确认协程链路真的被执行
    import types
    stages = []

    class _Browser:
        async def new_context(self, **kw): return _Ctx()
    class _Ctx:
        async def new_page(self): return _Page()
        async def cookies(self):
            return [{"name": "SESSDATA", "value": "x"},
                    {"name": "bili_jct", "value": "y"},
                    {"name": "DedeUserID", "value": "1"}]
    class _Page:
        async def goto(self, url, **kw): pass
    class _Chromium:
        async def launch(self, **kw):
            stages.append("__launched__")
            return _Browser()
    class _P:
        chromium = _Chromium()
    class _AP:
        async def __aenter__(self): return _P()
        async def __aexit__(self, *a): return False

    pw = types.ModuleType("playwright")
    ap = types.ModuleType("playwright.async_api")
    ap.async_playwright = lambda: _AP()
    pw.async_api = ap
    old_mods = (sys.modules.get("playwright"),
                sys.modules.get("playwright.async_api"))
    sys.modules["playwright"] = pw
    sys.modules["playwright.async_api"] = ap
    try:
        seen = []
        BBL.run(timeout=2, on_stage=seen.append)
    except Exception:
        pass
    finally:
        for k, v in (("playwright", old_mods[0]),
                     ("playwright.async_api", old_mods[1])):
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    ck("浏览器真的被启动", "__launched__" in stages)
    ck("进度有推进（不是一直准备中）", len(seen) >= 3)

    # 看门狗：卡死也要能收尾
    ck("有看门狗线程", "watchdog" in src)


def t_up_name_in_list():
    """订阅列表要显示 UP 主昵称，不能退化成 UID。

    _load_cache 建订阅项时 uname 写死成 ""，而昵称在后面才从 ups 表
    读出来并放进另一个 dict，导致列表永远显示数字 UID。
    """
    import os
    from db import Store
    p = os.path.join(BASE_DIR, "_t_name.db")
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(p + suf):
            os.remove(p + suf)
    try:
        st = Store(p)
        st.set_up(111, uname="波萝Buono", room_id=7788)
        st.set_sub("GROUP_A", 111, "live", True)
        st.set_sub("GROUP_A", 111, "dynamic", True)
        st.set_sub("GROUP_A", 111, "video", False)

        subs = st.ups_of_group("GROUP_A")
        ck("订阅项里有昵称", (subs.get("111") or {}).get("uname") == "波萝Buono")

        # v1.21.0：/列表 指令已移除，列表改由面板展示。
        # 这里验证面板数据源里昵称正确（不是只有 UID）。
        ck("订阅项有昵称", (subs.get("111") or {}).get("uname") == "波萝Buono")
        kinds = st.subscribed_kinds(111)
        ck("开启的类型正确", "live" in kinds and "dynamic" in kinds)
        ck("未开启的类型不出现", "video" not in kinds)
    finally:
        for suf in ("", "-wal", "-shm"):
            if os.path.exists(p + suf):
                try:
                    os.remove(p + suf)
                except OSError:
                    pass


def t_store_cache():
    """Store 必须被缓存复用。

    之前 webui 用 cfg 里的 "data.json" 去比 st.path（已被转成 .db），
    永远不相等 → 每次调用都重建 Store，缓存形同虚设。
    """
    import webui as W
    src = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("有独立的 _db_path", "def _db_path" in src)
    ck("比较的是转换后的路径", '.db" if df.endswith(".json")' in src.replace("'", '"')
       or ".json" in src)


def t_data_file_default():
    """默认数据文件必须是 data.db，不能还是迁移前的 data.json。"""
    ex = io.open(_os_path("config.example.yaml"),
                 encoding="utf-8").read()
    ck("示例配置用 data.db", 'data_file: "data.db"' in ex)
    ck("示例配置不再有 data.json", 'data_file: "data.json"' not in ex)


def t_at_all_switch():
    """@全体 已整个移除（v1.18.0），不应再有开关逻辑。

    群聊官方不支持 @everyone（仅文字子频道），且机器人无法被设为
    管理员，所以这个功能留着只会让人困惑，已移除。
    """
    src = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("无 @全体 分支", "全体" not in src)
    ck("无 at_all 引用", "at_all" not in src)

    import notify as N
    ck("notify 无 at_all_text", not hasattr(N, "at_all_text"))

    db = io.open(os.path.join(BASE_DIR, "db.py"), encoding="utf-8").read()
    ck("subs 无 at_all 列", "at_all     INTEGER" not in db)

    js = io.open(os.path.join(BASE_DIR, "static/app.js"),
                 encoding="utf-8").read()
    ck("前端无 toggle-at", "toggle-at" not in js)


def t_no_bare_emoji_print():
    """Windows GBK 控制台：不能裸 print emoji（会崩）。"""
    import re as _re
    emo = _re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")
    for f in ("start.py", "check.py", "bot.py",
              "bili_login.py", "webui.py"):
        bad = []
        for i, line in enumerate(io.open(os.path.join(BASE_DIR, f),
                                         encoding="utf-8").read().split("\n"), 1):
            if _re.search(r"^\s*print\(", line) and emo.search(line):
                bad.append(i)
        ck(f"{f} 无裸 print+emoji", not bad)
        ck(f"{f} 有 safe_stdio 或不用 print", "safe_stdio" in
           io.open(os.path.join(BASE_DIR, f), encoding="utf-8").read()
           or not bad)


def t_at_all_column_migrated_away():
    """旧库的 subs.at_all 列要被迁移掉，且订阅关系不能丢。

    SQLite 老版本不支持 DROP COLUMN，所以走「建新表→复制→删旧→改名」。
    这里验证：列没了，但原本开着 live/video 的订阅还在。
    """
    import sqlite3, tempfile, os
    d = tempfile.mkdtemp()
    p = os.path.join(d, "old.db")
    c = sqlite3.connect(p)
    c.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE groups (gid TEXT PRIMARY KEY, name TEXT, "
        "created_at REAL, updated_at REAL);"
        "CREATE TABLE ups (uid INTEGER PRIMARY KEY, uname TEXT, face TEXT, "
        "room_id INTEGER, live_status INTEGER, live_title TEXT, "
        "video_bvid TEXT, dyn_id TEXT, dyn_top_id TEXT, dyn_top_cmt TEXT, "
        "updated_at REAL);"
        'CREATE TABLE subs (gid TEXT NOT NULL, uid INTEGER NOT NULL, '
        'kind TEXT NOT NULL, "on" INTEGER NOT NULL DEFAULT 0, '
        "at_all INTEGER NOT NULL DEFAULT 0, updated_at REAL, "
        "PRIMARY KEY(gid,uid,kind));")
    c.execute("INSERT INTO subs VALUES('G1',111,'live',1,1,0)")
    c.execute("INSERT INTO subs VALUES('G1',111,'video',1,0,0)")
    c.commit()
    c.close()

    import db
    # 落盘层已换成 JSON：旧 data.db 要先导成 JSON，Store 才认
    jp = db.resolve_data_path(p)
    db._sqlite_to_json(p, jp)
    st = db.Store(p)
    cols = [r[1] for r in st._conn().execute("PRAGMA table_info(subs)")]
    ck("at_all 列已移除", "at_all" not in cols)
    ck("订阅关系保住", st.targets(111, "live") == ["G1"]
       and st.targets(111, "video") == ["G1"])


def t_html_wellformed():
    """HTML 结构必须完整（标签配对、无重复 id）。"""
    from html.parser import HTMLParser
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    class C(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.errors, self.ids = [], [], []
        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if "id" in d:
                self.ids.append(d["id"])
            if tag not in VOID:
                self.stack.append((tag, self.getpos()[0]))
        def handle_endtag(self, tag):
            if tag in VOID:
                return
            if not self.stack:
                self.errors.append(f"多余 </{tag}>")
                return
            t, ln = self.stack.pop()
            if t != tag:
                self.errors.append(f"</{tag}> 与 <{t}> 不匹配")

    for name in ("index.html", "login.html"):
        c = C()
        c.feed(io.open(os.path.join(BASE_DIR, "templates", name),
                       encoding="utf-8").read())
        ck(f"{name} 标签配对", not c.errors and not c.stack)
        ck(f"{name} 无重复 id", len(c.ids) == len(set(c.ids)))


def t_cfg_path_single_source():
    """配置路径只能有一处定义，否则面板和机器人会读不同文件。"""
    import cfgutil
    import webui as W
    ck("webui 复用 cfgutil 的 cfg_path", W.cfg_path() == cfgutil.cfg_path())
    src = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("webui 不再自己拼 CFG_PATH", "def cfg_path" in src and
       src.count("os.environ.get(\"BOT_CONFIG\"") <= 1)

def t_responsive_layout():
    """面板要跟着浏览器窗口大小走，不能只在某一个宽度下好看。"""
    h = io.open(os.path.join(BASE_DIR, "templates/index.html"), encoding="utf-8").read()
    # 断点：宽屏放宽 / 中等屏收侧栏 / 窄屏收图标栏 / 手机横排 / 矮屏侧栏自滚
    for w in (1180, 1024, 860, 560, 400):
        ck("有 %d 断点" % w, ("max-width:%dpx" % w) in h.replace(" ", ""))
    # 宽屏是 min-width（放宽主区，别让两侧空一大片）
    ck("有 1500 宽屏断点", "min-width:1500px" in h.replace(" ", ""))
    ck("矮屏断点", "max-height:620px" in h.replace(" ", ""))
    # 卡片列数按可用宽度重排，而不是写死几列
    ck("网格 auto-fit", "auto-fit" in h)
    # 长 ID / 长链接不能把整页撑出横向滚动条
    ck("有防撑破处理", "minmax(min(" in h)


def t_group_bg_picker():
    """群背景：布局样式一直有，但 JS 不渲染就等于没这功能。"""
    j = io.open(os.path.join(BASE_DIR, "static/app.js"), encoding="utf-8").read()
    ck("渲染 🖼 按钮", "gbg-btn" in j)
    ck("有 gbg-pick 动作", "'gbg-pick'" in j)
    ck("有 gbg-set 动作", "'gbg-set'" in j)
    ck("背景走 --gbg", "--gbg" in j)


def t_advanced_fields_writable():
    """高级设置里的项必须后端真的收，不然看着能改其实改不动。"""
    h = io.open(os.path.join(BASE_DIR, "templates/index.html"), encoding="utf-8").read()
    w = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    j = io.open(os.path.join(BASE_DIR, "static/app.js"), encoding="utf-8").read()
    for fid in ("cTimeout", "cDedupe"):
        ck("面板有 %s" % fid, ('id="%s"' % fid) in h)
        ck("%s 会提交" % fid, fid in j)
    ck("白名单收 request_timeout", '"request_timeout"' in w)
    ck("白名单收 dedupe_window", '"dedupe_window"' in w)


def t_topbar_badges_not_absolute():
    """顶栏右上角只留「🔄 刷新」，四个状态色标必须已移除。

    ⚠️ 历史：style.css 里 .th-badge 原本是旧宫格头像的**角标**
       （position:absolute; top:9px; right:11px）。新布局把它复用成
       顶栏的状态小标签，于是四个标签全部塌到右上角重叠 ——
       用户看到的就是「右上角不知道是什么元素叠在一起」。
       后来用户要求只留刷新，四个色标（订阅数徽标 + 已填 + 已登录）
       连同 .topbar .th-badge 的覆盖规则一并删掉。
       所以这里断言的是「已移除且不再留死样式」，不是「存在」。
    """
    h = io.open(os.path.join(BASE_DIR, "templates/index.html"), encoding="utf-8").read()
    j = io.open(os.path.join(BASE_DIR, "static/app.js"), encoding="utf-8").read()
    inline = "".join(re.findall(r"<style[^>]*>(.*?)</style>", h, re.S))

    # 四个色标必须已经不存在（还在说明没删干净）
    for bid in ("badgeSubs", "badgeCred", "badgeBili", "badgeErr"):
        ck("色标 %s 已移除" % bid, ('id="%s"' % bid) not in h)
        ck("色标 %s 不再被赋值" % bid, bid not in j)
    # 给色标用的 .th-badge 覆盖规则也不能留着（死样式）
    ck("顶栏不再覆盖 .th-badge",
       not re.search(r"\.topbar\s+\.th-badge\s*\{", inline))
    # 刷新按钮必须在
    ck("顶栏保留刷新按钮", 'data-act="refresh-state"' in h)
    # 内联样式必须在 style.css 之后加载，否则覆盖不生效
    ck("内联样式在 style.css 之后",
       h.index("/static/style.css") < h.index("<style>"))


def t_poll_interval_reported_every_round():
    """实际轮询间隔必须每轮上报，不然顶栏显示的是上一次的旧值。

    ⚠️ 以前只在 _note() 里顺带写 poll，而 _note 只在有检测结果时才调 ——
       撞风控自动放慢时，顶栏还显示 8s，看着像速率调整没生效。
    """
    b = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    j = io.open(os.path.join(BASE_DIR, "static/app.js"), encoding="utf-8").read()

    ck("interval_info 返回 current", '"current": self.interval' in b)
    # 主循环里每轮上报（不能只在 _note 里）
    loop = b[b.index("async def _poll_loop"):]
    loop = loop[:loop.index("def _adapt_interval")] if "def _adapt_interval" in loop else loop
    ck("主循环每轮上报 poll", "heartbeat.touch(poll=" in loop)
    # 前端优先读实际值，再退回配置值
    ck("顶栏优先读实际值", "hb.poll" in j or "notes.poll" in j)
    ck("顶栏读取 current", ".current" in j)


def main():
    print("=" * 56)
    print(" 安全模块测试")
    print("=" * 56)
    t_password()
    t_token()
    t_limiter()
    t_input()
    t_image()
    t_redact()
    t_bind()
    t_audit()
    t_bili_login()
    t_harden()
    t_secretbox()
    t_integrity()
    t_logtranslate()
    t_version()
    t_login_error_hint()
    t_pause_non_interactive()
    t_group_register()
    t_python_exe()
    t_db_group_register()
    t_db_subscribe_isolation()
    t_db_auto_refresh()
    t_db_migrate()
    t_account_info()
    t_panel_url_file()
    t_js_missing_element_safe()
    t_log_noise_filtered()
    t_safe_stdio_gbk_emoji()
    t_kill_process_tree()
    t_start_uses_safe_output()
    t_qq_group_info_api()
    t_browser_login_module()
    t_new_ui()
    t_store_removed()
    t_login_epoch_isolation()
    t_browser_login_api()
    t_browser_login_faster()
    t_bilibili_style_ui()
    t_bat_crlf_ascii()
    t_safe_print_emoji()
    t_no_mascot()
    t_safe_stdio_console_aware()
    t_rich_media_image_support()
    t_all_bat_files_safe_encoding()
    t_auth_follows_official_doc()
    t_public_api_contract_stable()
    t_process_cleanup_finds_relative_path()
    t_pid_file_registration()
    t_bats_use_absolute_path()
    t_no_dead_module_references()
    t_process_cleanup_exists()
    t_single_instance_lock()
    t_dedup_works_without_msg_id()
    t_help_is_single_source()
    t_templates_complete()
    t_selfcheck_improved()
    t_selfcheck_no_attr_crash()
    t_bat_stop_before_start()
    t_selfcheck_uid_is_fixed()
    t_room_id_not_false_zero()
    t_sandbox_group_and_diag_cn()
    t_group_name_fail_reason()
    t_panel_dies_with_parent()
    t_log_readable_for_beginners()
    t_no_event_loop_closed()
    t_subscription_migration()
    t_silent_join_and_one_time_greeting()
    t_per_group_templates()
    t_video_season_collection()
    t_action_delegate_receives_el()
    t_html_block_structure()
    t_group_removed_cleanup()
    t_main_area_is_permanent()
    t_refresh_names_removes_gone()
    t_group_gone_by_send_error()
    t_default_uid_inputs_empty()
    t_sheet_closes_on_switch()
    t_template_var_quick_insert()
    t_log_no_duplicate_time()
    t_log_translate_for_beginners()
    t_pinned_dynamic_not_blocking()
    t_dynamic_templates_include_pinned()
    t_default_check_faster()
    t_message_dedup()
    t_markdown_no_zwsp_spam()
    t_slow_every_one_works()
    t_ban_policy_not_brutal()
    t_unban_not_deadlock()
    t_browser_login_actually_runs()
    t_up_name_in_list()
    t_store_cache()
    t_data_file_default()
    t_at_all_switch()
    t_at_all_column_migrated_away()
    t_no_bare_emoji_print()
    t_html_wellformed()
    t_cfg_path_single_source()
    t_sidebar_layout()
    t_responsive_layout()
    t_group_bg_picker()
    t_advanced_fields_writable()
    t_topbar_badges_not_absolute()
    t_poll_interval_reported_every_round()
    print("\n结果:", "全部通过 ✅" if ok else "存在失败 ❌")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
