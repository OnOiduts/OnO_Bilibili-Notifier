# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""用本机浏览器打开 B 站登录页，用户扫码登录后自动读取 cookie。

为什么需要这个：
    二维码登录接口（passport.bilibili.com/x/passport-login/web/qrcode/poll）
    现在经常拿不到 SESSDATA —— 服务端风控会拦掉这一步，
    表现为「已扫码确认，但没拿到 SESSDATA」。

    最稳的办法就是回到人本身：打开浏览器，让用户像平常一样登录 B 站，
    登录成功后直接从浏览器里读 cookie。这正是手工
    「登录 → 打开主页 → F12 找 Cookie」的流程，只是自动化了。

依赖：
    pip install playwright
    再用本机已装的 Chrome / Edge（不需要下载内核）；
    若本机没有，再执行 playwright install chromium。
"""
import asyncio
import os
import sys
import time

# 至少要有这几个才算登录成功
NEED = ("SESSDATA", "bili_jct", "DedeUserID")

LOGIN_URL = "https://passport.bilibili.com/login"
HOME_URL = "https://www.bilibili.com"


def find_chromium():
    """在本机找 Chromium 内核浏览器（Chrome / Edge / Chromium）。"""
    cands = []
    if sys.platform == "win32":
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        rel = [
            ("Google", "Chrome", "Application", "chrome.exe"),
            ("Microsoft", "Edge", "Application", "msedge.exe"),
            ("Chromium", "Application", "chrome.exe"),
        ]
        for root in roots:
            if not root:
                continue
            for parts in rel:
                cands.append(os.path.join(root, *parts))
    elif sys.platform == "darwin":
        cands = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:
        cands = [
            "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium", "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge", "/snap/bin/chromium",
        ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return ""


class BrowserLoginError(Exception):
    pass


def _pick_from_cookies(cookies: list) -> dict:
    out = {}
    for c in cookies or []:
        k = c.get("name") or ""
        v = c.get("value") or ""
        if k:
            out[k] = v
    return out


def _expires_from_cookies(cookies: list) -> float:
    """从 cookie 列表里读出 SESSDATA 的真实过期时间（Unix 秒）。

    ⚠️ 为什么必须读真值而不是写死 30 天：
        B 站 SESSDATA 的名义有效期约 1 个月，但**实际会短很多**
        （服务端主动踢、长期不活跃、风控都会让它提前失效）。
        以前这里一律写 `time.time() + 30*86400`，面板于是永远显示
        "还剩 29 天"——到期提醒的阈值（2~5 天）永远进不去，
        等于提醒功能整体失效。所以必须以 cookie 自带的时间为准。

    Playwright 给的是秒级时间戳（-1 或 0 表示会话 cookie，没有明确过期）；
    浏览器 DOM 里取到的则是毫秒，两种都兼容一下。
    """
    best = 0.0
    for c in cookies or []:
        if (c.get("name") or "") != "SESSDATA":
            continue
        try:
            raw = float(c.get("expires") or 0)
        except (TypeError, ValueError):
            continue
        if raw > 1e12:          # 毫秒
            raw = raw / 1000.0
        if raw <= 0:            # 会话 cookie：没有明确过期时间
            continue
        best = max(best, raw)
    return best


def _has_login(cookies: dict) -> bool:
    return all(cookies.get(k) for k in NEED)


async def browser_login(timeout: int = 180, headless: bool = False,
                        on_stage=None, should_cancel=None) -> dict:
    """异步实现。同步调用方请用 run()，不要直接调用本函数。

    直接调用会返回协程对象而不执行任何逻辑
    （Python 会警告 "coroutine was never awaited"）。
    """
    """打开浏览器让用户登录 B 站，登录后自动读取 cookie。

    返回 {"cookies": {...}, "cookie_str": "...", "expires": 时间戳}
    抛出 BrowserLoginError 时会带明确的「怎么办」。
    """
    def stage(msg):
        if on_stage:
            try:
                on_stage(msg)
            except Exception:
                pass

    stage("检查 playwright…")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise BrowserLoginError(
            "缺少 playwright。请执行：\n"
            "    pip install playwright\n"
            "（若本机没有 Chrome / Edge，再执行 playwright install chromium）\n"
            "也可以直接用面板里的「手动粘贴 cookie」，不依赖这个。")

    stage("查找本机浏览器…")
    exe = find_chromium()
    stage(f"使用浏览器：{os.path.basename(exe) if exe else 'Playwright 自带内核'}")

    launch_kwargs = {"headless": headless}
    if exe:
        launch_kwargs["executable_path"] = exe
    else:
        # 本机没装，尝试 playwright 自带内核
        pass

    async with async_playwright() as p:
        stage("启动浏览器…（第一次会慢一点）")
        try:
            browser = await p.chromium.launch(**launch_kwargs)
        except Exception as e:
            msg = str(e)
            if "Executable doesn't exist" in msg or "please run" in msg.lower():
                raise BrowserLoginError(
                    "Playwright 浏览器内核没安装。执行：\n"
                    "    playwright install chromium\n"
                    "或者先安装 Google Chrome 再重试。")
            raise BrowserLoginError(f"浏览器启动失败：{msg[:200]}")

        try:
            ctx = await browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"))
            page = await ctx.new_page()

            stage("正在打开 B 站登录页…")
            try:
                # 不等整页资源，domcontentloaded 就够了，能明显加快首屏
                await page.goto(LOGIN_URL, timeout=30000,
                                wait_until="domcontentloaded")
            except Exception as e:
                raise BrowserLoginError(
                    f"打不开 B 站（{str(e)[:120]}）。\n"
                    "请检查网络，或改用面板里的「手动粘贴 cookie」。")

            stage("请在浏览器里扫码 / 账号密码登录 B 站，登录成功后自动继续")

            # 轮询 cookie：1 秒一次（原来是 2 秒，感觉明显慢）。
            # 检测到 SESSDATA 立刻返回，不会干等到 timeout。
            deadline = time.time() + timeout
            found = {}
            found_raw = []
            waited = 0
            while time.time() < deadline:
                if should_cancel and should_cancel():
                    raise BrowserLoginError("已取消登录")
                raw = await ctx.cookies()
                cookies = _pick_from_cookies(raw)
                if _has_login(cookies):
                    found = cookies
                    found_raw = raw
                    break
                await asyncio.sleep(1)
                waited += 1
                if waited % 10 == 0:
                    left = int(deadline - time.time())
                    stage(f"等待登录中… 已等 {waited}s，剩余 {left}s")

            if not found:
                raise BrowserLoginError(
                    f"等待 {timeout} 秒仍未检测到登录。\n"
                    "如果已经登录成功，请关闭本窗口重新运行一次；\n"
                    "或改用面板里的「手动粘贴 cookie」。")

            # 顺便确认一下登录的是哪个号（等价于「打开账号主页看一眼」）
            uname = ""
            try:
                await page.goto(HOME_URL, timeout=20000,
                                wait_until="domcontentloaded")
                # 只等 cookie 落定，不等整页
                await asyncio.sleep(0.6)
                # 再次取一次，确保拿到主页种下的 cookie
                raw2 = await ctx.cookies()
                cookies2 = _pick_from_cookies(raw2)
                if _has_login(cookies2):
                    found = cookies2
                    found_raw = raw2
            except Exception:
                pass

            stage("登录成功，正在读取账号信息…")

            from bili_login import account_info, cookie_str
            cs = cookie_str(found)
            acc = {}
            try:
                acc = await account_info(cs) or {}
            except Exception:
                acc = {}
            if acc.get("ok"):
                uname = acc.get("uname", "")

            return {
                "cookies": found,
                "cookie_str": cs,
                "uname": uname,
                "account": acc,
                # 真实过期时间；取不到（会话 cookie）才退回保守估计
                "expires": (_expires_from_cookies(found_raw)
                            or time.time() + 30 * 86400),
            }
        finally:
            try:
                await browser.close()
            except Exception:
                pass


def run(timeout: int = 180, on_stage=None, should_cancel=None) -> dict:
    """同步入口。

    面板的后台线程必须走这里 —— browser_login 是 async 函数，
    直接调用只会得到一个从未被执行的协程对象，
    浏览器根本不会启动（这就是「点了没反应、一直准备中」的原因）。
    """
    return asyncio.run(browser_login(timeout=timeout, on_stage=on_stage,
                                     should_cancel=should_cancel))


async def browser_refresh(cookie: str, timeout: int = 45,
                          on_stage=None) -> dict:
    """静默续期：把现有 cookie 喂进真实浏览器，让 B 站自己把登录态续上。

    为什么需要这条退路：
        passport 的 /x/passport-login/web/cookie/refresh 接口现在经常
        直接失败（面板上看到的就是「续期失败：请求错误」），原因大多是
        服务端风控，裸接口很难过。但在真实浏览器里访问 B 站时，登录态
        是 B 站前端自己续的 —— 带着真实浏览器环境，成功率高得多。
        所以接口换不动时，就退回"开个浏览器让它自己换"。

    ⚠️ 前提是旧 cookie 还没彻底失效：
        已经 -101 的话，浏览器里同样是未登录状态，谁都续不出来。那种
        情况本函数返回 need_relogin=True，由调用方引导用户重新登录。
        判据是「我们注入的 SESSDATA 被页面清掉了」——页面会主动删掉
        不被认可的登录态 cookie，删了就说明它已经不被认。

    返回：
        {"cookies": {...}, "cookie_str": "...", "expires": 时间戳,
         "changed": SESSDATA 是否被换成了新的}
    """
    def stage(msg):
        if on_stage:
            try:
                on_stage(msg)
            except Exception:
                pass

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise BrowserLoginError(
            "缺少 playwright，没法用浏览器续期。请执行：\n"
            "    pip install playwright\n"
            "或者直接用「浏览器登录」重新登录一次。")

    from bili_login import parse_cookie, cookie_str, sessdata_expires

    old = parse_cookie(cookie or "")
    if not old.get("SESSDATA"):
        raise BrowserLoginError("现有 cookie 里没有 SESSDATA，没法续期")
    old_sess = old.get("SESSDATA", "")
    old_exp = sessdata_expires(cookie or "")

    stage("查找本机浏览器…")
    exe = find_chromium()
    launch_kwargs = {"headless": True}          # 续期不需要人看，静默跑
    if exe:
        launch_kwargs["executable_path"] = exe

    deadline = time.time() + max(5, int(timeout or 45))
    async with async_playwright() as p:
        stage("启动浏览器…")
        try:
            browser = await p.chromium.launch(**launch_kwargs)
        except Exception as e:
            raise BrowserLoginError(f"浏览器启动失败：{str(e)[:200]}")

        try:
            ctx = await browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"))

            stage("把现有登录态放进浏览器…")
            jar = [{"name": k, "value": v, "domain": ".bilibili.com",
                    "path": "/"} for k, v in old.items() if v]
            try:
                await ctx.add_cookies(jar)
            except Exception as e:
                raise BrowserLoginError(f"写入 cookie 失败：{str(e)[:120]}")

            page = await ctx.new_page()
            stage("访问 B 站，等它自己续登录态…")
            for url in (HOME_URL, "https://api.bilibili.com/x/web-interface/nav"):
                if time.time() > deadline:
                    break
                try:
                    await page.goto(url, timeout=20000,
                                    wait_until="domcontentloaded")
                except Exception:
                    # 打不开不一定就失败：cookie 可能已经落定，继续往下判断
                    pass

            # 轮询：B 站前端续期是异步的，多看几眼
            cur = {}
            cur_raw = []
            cur_exp = 0.0
            changed = False
            while time.time() < deadline:
                cur_raw = await ctx.cookies()
                cur = _pick_from_cookies(cur_raw)
                cur_exp = _expires_from_cookies(cur_raw)
                if not cur.get("SESSDATA"):
                    # 注入的登录态被页面清掉了 —— 它已经不被 B 站认可
                    return {"need_relogin": True,
                            "msg": ("这串 cookie 已经不被 B 站认可了（放进浏览器"
                                    "就被清掉），续不出来。请重新登录一次。")}
                if (cur.get("SESSDATA") != old_sess
                        or (cur_exp and cur_exp > old_exp + 60)):
                    changed = True
                    break
                await asyncio.sleep(2)

            if not cur.get("SESSDATA"):
                return {"need_relogin": True,
                        "msg": "浏览器里读不到登录态，请重新登录一次。"}

            return {
                "cookies": cur,
                "cookie_str": cookie_str(cur),
                "expires": cur_exp or 0.0,
                "changed": changed,
            }
        finally:
            try:
                await browser.close()
            except Exception:
                pass


def run_refresh(cookie: str, timeout: int = 45, on_stage=None) -> dict:
    """同步入口（同 run()：直接 await 协程是不会执行的）。"""
    return asyncio.run(browser_refresh(cookie=cookie, timeout=timeout,
                                       on_stage=on_stage))
