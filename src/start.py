#!/usr/bin/env python3
# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""一键启动：先起网页面板并自动打开浏览器，配置填好后再自动拉起机器人。

设计要点（针对小白）：
  1. 没填 AppID/AppSecret 时，机器人必然登录失败。所以此时**只开面板**，
     不启动机器人，避免它崩掉把整个程序带走。
  2. 面板起来后自动打开浏览器，省得复制地址。
  3. 用户在面板里填完保存，这里检测到凭据有效就自动启动机器人，不用手动重启。
  4. 机器人因凭据错误崩溃时，不会反复重启刷屏，等凭据变了再试。
"""
import os
import subprocess
import sys
import cleanup
import threading
import time
import webbrowser

import heartbeat
from cfgutil import (BASE, cfg_path, creds_fingerprint, creds_ready,
                     child_cmd, is_frozen, kill_process_tree, load_cfg,
                     pick_port, python_exe, safe_print, safe_stdio)
import version as _version
from security import restrict_perms
import paths
import db

# ⚠️ 这里曾经列着 ("qrcode", "qrcode") —— 它只服务于「扫码登录 B 站」这一种方式，
#    而面板已经改成浏览器登录（playwright）为主，扫码入口都从界面上摘掉了。
#    结果是：一个没人用的库被当成必装项，没装就直接拦住启动，装了也用不上。
#    现在降级为可选（见 requirements-optional.txt）。仍想用命令行 --qr 扫码的话自己装。
REQUIRED = (("botpy", "qq-botpy"), ("aiohttp", "aiohttp"),
            ("yaml", "PyYAML"), ("flask", "Flask"))
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def missing_deps():
    miss = []
    for mod, pkg in REQUIRED:
        try:
            __import__(mod)
        except ImportError:
            miss.append(pkg)
    return miss


def ensure_deps():
    # exe 版：依赖已经编进程序里，而且机器上未必有 python/pip，
    # 再去 pip install 只会卡住甚至报错。
    if is_frozen():
        safe_print("[1/4] 依赖已就绪（已随程序打包）")
        return True
    miss = missing_deps()
    if not miss:
        safe_print("[1/4] 依赖已就绪")
        return True
    safe_print(f"[1/4] 正在安装依赖：{'、'.join(miss)}（第一次会比较慢，请等）")
    req = paths.asset("requirements.txt")
    cmd = [sys.executable, "-m", "pip", "install", "-r", req]
    r = subprocess.run(cmd, cwd=BASE)
    if r.returncode != 0:
        safe_print("       官方源失败，换用清华镜像重试…")
        r = subprocess.run(cmd + ["-i", MIRROR], cwd=BASE)
    if r.returncode != 0:
        safe_print("       仍然失败。请手动执行：")
        safe_print(f"       {sys.executable} -m pip install --user -r requirements.txt")
        return False
    if missing_deps():
        safe_print("       装完仍缺依赖，请手动安装后重试。")
        return False
    safe_print("       安装完成")
    return True


def ensure_config():
    cfg = cfg_path()
    example = paths.asset("config.example.yaml")
    if not os.path.exists(cfg) and os.path.exists(example):
        import shutil
        shutil.copy(example, cfg)
        safe_print("       已自动生成 config.yaml")
    if os.path.exists(cfg):
        restrict_perms(cfg)      # 含 AppSecret，收紧为仅本人可读写
    return cfg


def open_browser(url, delay=1.5):
    def _go():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


# 必须在任何 print 之前：输出被重定向到文件时，GBK 编码遇到 emoji 会直接崩溃
safe_stdio()

PANEL_URL_FILE = paths.run_file(".panel_url")
# 面板写这个文件 = 用户点了「重启机器人」。面板拿不到 bot 的 PID，
# 直接杀进程会越权且容易杀错，所以由本进程（bot 的父进程）来执行。
RESTART_FILE = paths.run_file(".restart_bot")


# 必须留住回调对象的引用：被垃圾回收后触发会直接崩
_KEEP_HANDLERS = []


def _install_close_handler(children_ref):
    """Windows 关窗口/注销时尽量清理子进程。

    ⚠️ 这只是**辅助**：点 X 关闭控制台时系统会强杀进程，
    finally 根本跑不到，处理器也未必来得及。真正的保险是
    子进程自己的看门狗（parentwatch）—— 爹没了就自己走。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        HANDLER = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _handler(event):
            for p in list(children_ref()):
                try:
                    kill_process_tree(p)
                except Exception:
                    pass
            return True

        h = HANDLER(_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(h, True)
        _KEEP_HANDLERS.append(h)
        return h
    except Exception:
        return None


def clear_panel_url():
    """退出时删掉端口文件，避免下次启动读到过期的地址。"""
    try:
        if os.path.exists(PANEL_URL_FILE):
            os.remove(PANEL_URL_FILE)
    except OSError:
        pass


def pause_if_interactive(msg: str = "\n按回车键退出…"):
    """失败时暂停一下，让人看清报错。

    为什么不能直接 input()：
        无控制台方式（CREATE_NO_WINDOW）启动本脚本时，
        stdin 是空的 —— input() 会立刻抛 EOFError，进程直接崩掉，
        看起来就是「后端异常退出」却没有任何提示。
    所以只在确实有人在终端里看着的时候才等待。
    """
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return                      # 非交互（桌面版/重定向），直接走
    except Exception:
        return
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt):
        safe_print()


def _kill_leftovers():
    """启动时先清一遍残留进程。

    保险：上次可能没退干净（关窗口/崩溃），旧实例还占着端口、
    还在监听同一个群，会导致一条指令回两次、同一条动态推三次。
    """
    try:
        n = cleanup.kill_ours()
        if n:
            safe_print(f"[0/4] 已清理上次残留的 {n} 个进程")
        cleanup.register_pid()      # 记住自己，方便下次清理
    except Exception:
        pass


def _tail_traceback(max_lines: int = 14) -> list:
    """取 bot.log 末尾最近一段 Traceback（给用户看崩溃原因）。

    为什么需要：机器人崩溃时控制台只有一句"退出码 1"，
    原因写在 bot.log 里 —— 而面板这时候常常也打不开，
    用户根本看不到。这里直接把末尾的错误打在控制台上。
    """
    try:
        p = os.path.join(paths.data_home(), "logs", "bot.log")
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-400:]
    except Exception:
        return []
    # 找最后一段完整堆栈：从 Traceback 起到最后一行非缩进行
    start = -1
    for i in range(len(lines) - 1, -1, -1):
        if "Traceback (most recent call last)" in lines[i]:
            start = i
            break
    if start < 0:
        # 没有堆栈，就把末尾几行错误/警告原文给出来
        tail = [l.rstrip() for l in lines if ("ERROR" in l or "错误" in l)]
        return tail[-max_lines:]
    out = []
    for l in lines[start:]:
        s = l.rstrip()
        if not s:
            continue
        # 堆栈结束后又出现正常日志行（[INFO] / [WARNING] …）→ 停。
        # ⚠️ 不能用"是否缩进"判断：最后那行错误摘要（AttributeError: …）
        #    本身就不缩进，一停就把它丢了 —— 而它恰恰是唯一有用的信息。
        if len(out) >= 2 and s.startswith("["):
            break
        out.append(s)
        if len(out) >= max_lines + 2:
            break
    return out


def _watch_panel(web_ref, port_ref, stop_evt):
    """面板守护：面板自己挂了就再拉起来。

    为什么需要：面板进程崩了以后没人管（以前只在启动 2 秒后查一次），
    于是浏览器一直 ERR_CONNECTION_REFUSED，而机器人崩溃的原因
    又只能去面板日志页看 —— 两头堵死，用户完全无法自查。
    """
    import subprocess as _sp
    tries = 0
    while not stop_evt.is_set():
        stop_evt.wait(5)
        if stop_evt.is_set():
            return
        if tries >= 3:
            return          # 连续拉不起来就别刷屏了，交给下次启动
        try:
            web = web_ref[0]
        except Exception:
            return
        if web is None or web.poll() is None:
            continue
        tries += 1
        try:
            safe_print()
            safe_print("  ⚠️  管理面板已退出，正在重新启动…")
            _port = port_ref[0]
            _kw = {"cwd": BASE}
            if sys.platform == "win32":
                _kw["creationflags"] = 0x08000000
            new = _sp.Popen(
                [python_exe(), os.path.join(BASE, "webui.py"),
                 "--host", "127.0.0.1", "--port", str(_port),
                 "--parent", str(os.getpid())], **_kw)
            web_ref[0] = new
            try:
                cleanup.register_pid(new.pid)
            except Exception:
                pass
            time.sleep(2)
            if new.poll() is None:
                tries = 0
                safe_print(f"  ✅ 面板已恢复：http://127.0.0.1:{_port}")
            else:
                safe_print("  ❌ 面板重启失败，请手动执行：python webui.py 看报错")
        except Exception:
            pass


def main():
    # 先读 .env（环境变量形式的凭据）。放在最前面：后面的数据目录选择、
    # 凭据检测都依赖它，晚一步就会按默认值走，等于没配。
    try:
        import envcfg
        _env_file = envcfg.load_dotenv(force=True)
        if _env_file:
            safe_print(f"  [*] 已从 {os.path.basename(_env_file)} 读取环境变量凭据"
                       f"（优先级高于 config.yaml）")
        _from_env = envcfg.env_sourced_fields()
        if _from_env:
            _names = {"appid": "AppID", "secret": "AppSecret",
                      "bili_cookie": "B站 Cookie"}
            safe_print("  [*] 以下凭据来自环境变量，不会写入 config.yaml："
                       + "、".join(_names.get(f, f) for f in _from_env))
    except Exception:
        pass
    # 目录规范化：旧版本把 config.yaml / data.db / .machine_key 等跟源码
    # 混在同一个目录里，删文件夹升级就全丢了。这里把它们搬到程序目录之外。
    try:
        # v1.96 曾把数据放到用户目录下的隐藏文件夹，这里搬回程序本体目录
        if paths.migrate_from_old_home():
            safe_print(f"  [*] 数据已搬回本体目录：{paths.data_home()}")
        if paths.migrate_legacy():
            safe_print(f"  [*] 已把旧位置的配置与数据搬到：{paths.data_home()}")
        # 图片统一收进 images/ 一个大类（avatars / push / ui）
        if paths.migrate_images():
            safe_print("  [*] 图片已统一归到 images/ 目录")
        # 数据文件被误删时的兜底：从滚动备份回来一份（只在启动时做一次）
        # ⚠️ 必须恢复 Store 真正用的那个文件：用户改过存放位置后，
        #    paths.state_path()（默认路径）跟实际数据文件不是同一个，
        #    恢复错了地方等于没恢复 —— 数据还是空的。
        src = db.auto_recover(db.data_path_from_cfg())
        if src:
            safe_print(f"  [*] 数据文件不见了，已自动从备份恢复：{os.path.basename(src)}")
        # 旧版 data.db（v1.92 及更早的 SQLite 落盘文件）自动合并进来。
        # 老用户升级时把那份 data.db 放进程序目录即可，不用重新加订阅。
        got = db.auto_import_legacy(verbose=False)
        for item in got:
            sm = item.get("summary") or {}
            safe_print("  [*] 已合并旧数据 %s：%d 个群 / %d 条订阅 / %d 个合集"
                       % (os.path.basename(item.get("path") or "data.db"),
                          sm.get("groups", 0), sm.get("subs", 0),
                          sm.get("seasons", 0)))
        # 幽灵群自检：订阅里带 gid、groups 表却没记录的，面板会显示
        # "还没有登记任何群"，而机器人照推不误。每次启动兜一次 ——
        # 导入过的旧库打了标记不会再导，漏这一步就永远补不回来。
        try:
            # ⚠️ 必须跟面板、机器人用同一个路径入口：这里写死
            #    DATA_FILE_DEFAULT 时补的是"机器人那份"，而面板读的是
            #    源码目录那份 —— 补完了面板照样空白（v1.98.3）。
            _dp = db.data_path_from_cfg()
            # 清掉上次写失败留下的 .tmp-*.json —— 写数据被杀软拦下时
            # 临时文件会留下来，越积越多，还会被"目录里有陌生文件"误报。
            _stale = db.cleanup_stale_tmp(_dp)
            if _stale:
                safe_print(f"  [*] 已清掉 {_stale} 个残留临时文件"
                           f"（上次写数据被拦下留下的，不是你的数据）")
            _st = db.Store(_dp)
            # 先清测试残留假群，再补幽灵群 —— 顺序反了会把假群又补回来。
            _junk = _st.purge_test_groups()
            if _junk:
                safe_print(f"  [*] 已清掉 {_junk} 个测试残留假群"
                           f"（打包时混进去的，不是你的订阅）")
            _n = _st.repair_orphan_groups()
            if _n:
                safe_print(f"  [*] 已补回 {_n} 个群记录"
                           f"（此前订阅在跑，面板却看不到这些群）")
        except Exception:
            pass
    except Exception:
        pass
    # 启动先清一次残留：多实例会让一条指令回两次、同一条动态推三次。
    #
    # ⚠️ 只清**一次**。以前这里有三处（两块复制粘贴的重复代码，再加
    #    后面的 _kill_leftovers()），而清理一趟在 Windows 上要起
    #    PowerShell / wmic 扫全机 python 进程、还要 netstat 查端口，
    #    单次就要好几秒；三遍下来光"清理自己"就吃掉半分钟，而且这部分
    #    耗时跟订阅多少、日志多大都无关，纯属浪费。
    _kill_leftovers()
    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--port", type=int, default=0,
                    help="指定面板端口，默认从 8088 开始自动挑一个空闲的")
    ns, _unknown = ap.parse_known_args()
    args_port = ns.port if ns.port and ns.port > 0 else 0

    safe_print("=" * 58)
    safe_print(f" OnOB站通知订阅工具（OnO Bilibili Notifier） · 启动中　v{_version.current()}")
    safe_print(" OnOBN · 仅 Windows 桌面环境实测；服务器环境未测试，遇到问题欢迎反馈")
    safe_print("=" * 58)

    if not ensure_deps():
        pause_if_interactive()
        return 2                        # 桌面版据此判断失败
    ensure_config()
    safe_print("[2/4] 配置文件已就绪")
    # 数据目录打印出来：出问题时用户知道去哪儿找配置、订阅和日志，
    # 也明白"删掉程序文件夹不会删掉这些数据"。
    try:
        safe_print(f"      数据与日志目录：{paths.data_home()}")
    except Exception:
        pass

    # 清掉上次运行残留的心跳，避免面板把已停止的机器人显示成在线
    heartbeat.clear()
    # 上次如果没正常退出，端口文件会残留 —— 先清掉，避免读到过期地址
    clear_panel_url()

    port = args_port or pick_port(8088)
    safe_print(f"[3/4] 启动网页管理面板（端口 {port}）")

    web_kwargs = {"cwd": BASE}
    if sys.platform == "win32":
        web_kwargs["creationflags"] = 0x08000000      # CREATE_NO_WINDOW
    # --parent：把本进程 PID 交给面板，面板自己盯着；
    # 关窗口时本进程是被强杀的，finally 跑不到，
    # 只有面板自己发现"爹没了"才能真正退干净。
    web = subprocess.Popen(
        child_cmd("webui", ["--host", "127.0.0.1", "--port", str(port),
                            "--parent", str(os.getpid())]),
        **web_kwargs,
    )
    try:
        import cleanup as _cu
        _cu.register_pid(web.pid)
    except Exception:
        pass
    # 面板守护：面板自己崩了要能自己起来，否则浏览器一直连不上，
    # 而机器人崩溃的原因又只能去面板日志页看 —— 两头堵死。
    _web_ref = [web]
    _port_ref = [port]
    _panel_stop = threading.Event()
    try:
        threading.Thread(target=_watch_panel,
                         args=(_web_ref, _port_ref, _panel_stop),
                         daemon=True).start()
    except Exception:
        pass
    url = f"http://127.0.0.1:{port}"

    # 把真实端口写进文件，让桌面版能精确读到。
    # 之前桌面版靠猜（扫 8088-8099），一旦猜错就会连到别的程序上，
    # 表现为「浏览器打开了但页面打不开 / 窗口秒关」。
    try:
        with open(PANEL_URL_FILE, "w", encoding="utf-8") as f:
            f.write(url)
    except OSError:
        pass
    time.sleep(2)
    if web.poll() is not None:
        safe_print("       面板启动失败，请手动执行：python webui.py 看报错")
        pause_if_interactive()
        return 3

    safe_print()
    safe_print("  ┌────────────────────────────────────────────────┐")
    safe_print(f"  │  管理面板：{url:<38}│")
    safe_print("  │  已尝试自动打开浏览器，没弹出就手动复制上面的地址 │")
    safe_print("  └────────────────────────────────────────────────┘")
    safe_print("  🛡️  面板只监听本机(127.0.0.1)，局域网和公网访问不到。")
    safe_print("      想远程管理请用 SSH 隧道，不要直接改成 0.0.0.0。")
    open_browser(url)

    bot = None
    _install_close_handler(lambda: [x for x in (bot, web) if x])
    last_fp = None          # 当前正在用的凭据
    failed_fp = None        # 已确认失败的凭据：同一份不再重试，改了才重试
    notified_waiting = False
    _last_hint = None     # 密钥解不开的提示只说一次，否则每 2 秒刷一条
    _told_restored = False  # 备份密钥自动恢复过，也只说一次
    _crashes = 0          # 连续退出次数（用于重启退避）
    _keep_panel = False

    try:
        while True:
            cfg = load_cfg()
            ready = creds_ready(cfg)
            fp = creds_fingerprint(cfg)

            if not ready:
                # 密文解不开 ≠ 没填。不说清楚的话，用户明明填过
                # 却看到「还没填」，根本想不到是密钥文件被换了。
                try:
                    import secretbox
                    hint = secretbox.broken_hint()
                except Exception:
                    hint = ""
                if hint and hint != _last_hint:
                    safe_print()
                    safe_print("  ⚠️ " + hint)
                    safe_print("     这一条只提示一次，不会反复刷屏。")
                    _last_hint = hint
                if not notified_waiting:
                    safe_print()
                    safe_print("  ⏳ 还没填 AppID / AppSecret，机器人先不启动。")
                    safe_print("     请在浏览器面板 →「⚙️ 设置」里填写并保存，填好会自动启动。")
                    safe_print("     （拿到方式见 使用说明.md 第 1.2 步）")
                    notified_waiting = True
                time.sleep(2)
                continue

            notified_waiting = False
            _last_hint = None
            # 靠备份密钥自动救回来了：说一声，免得用户以为还得去重填
            try:
                import secretbox
                if secretbox.RESTORED and not _told_restored:
                    _told_restored = True
                    safe_print()
                    safe_print("  🔑 检测到 .machine_key 被换过，已用备份的上一把密钥")
                    safe_print("     自动恢复 AppSecret / B站 Cookie，不用重填。")
            except Exception:
                pass

            # 面板点了「重启机器人」：杀掉当前 bot，下一轮会重新拉起
            if os.path.exists(RESTART_FILE):
                try:
                    os.remove(RESTART_FILE)
                except OSError:
                    pass
                if bot is not None:
                    try:
                        kill_process_tree(bot)
                    except Exception:
                        pass
                    bot = None
                failed_fp = None          # 清掉失败标记，允许用同一份凭据重试
                safe_print()
                safe_print("  🔄 收到重启请求，正在重新启动机器人…")
                time.sleep(1)
                continue

            if bot is None:
                if fp == failed_fp:
                    # 这份凭据已经失败过，等用户在面板里改
                    time.sleep(3)
                    continue
                last_fp = fp
                safe_print()
                safe_print("  ✅ 检测到凭据，正在启动机器人…")
                bot = subprocess.Popen(
                    child_cmd("bot", ["--parent", str(os.getpid())]),
                    cwd=BASE)
                try:
                    import cleanup as _cu
                    _cu.register_pid(bot.pid)
                except Exception:
                    pass
            elif bot.poll() is not None:
                code = bot.poll()
                bot = None
                # 只有「登录失败（3）」才锁定凭据等用户改；
                # 运行时崩溃（1 等）应该自动重启，否则机器人就再也不起来了。
                if code == 3:
                    failed_fp = last_fp
                    _crashes = 0
                else:
                    failed_fp = None
                    _crashes += 1
                safe_print()
                if code == 2:
                    safe_print("  ⏳ 机器人退出：凭据还没填好，等你在面板里填。")
                elif code == 3:
                    safe_print("  ❌ 机器人登录失败（退出码 3）。上面 [ERROR] 那行是具体原因。")
                    safe_print("     常见：AppID 填错 / AppSecret 填错 / 机器人没上线 / IP 白名单没配。")
                    safe_print("     在面板「⚙️ 设置」里改完保存，这里会自动重试。")
                    safe_print("     也可以另开一个窗口运行 python check.py 逐项自检。")
                else:
                    safe_print("  ⚠️  机器人退出（退出码 %s）——不是凭据问题，稍后自动重启。" % code)
                    # ⚠️ 直接把崩溃原因打在控制台上：面板这时候往往也打不开，
                    #    只说"去看日志页"等于没说。
                    _tb = _tail_traceback()
                    if _tb:
                        safe_print("     崩溃原因（bot.log 末尾）：")
                        for _l in _tb[-14:]:
                            safe_print("       " + _l)
                    else:
                        safe_print("     原因已写入 bot.log，可在面板「日志」页查看末尾。")
                # 崩溃重启要退避，否则一崩溃就会每 2 秒重启一次，刷屏且暴力访问
                if _crashes:
                    _wait = min(60, 5 * _crashes)
                    safe_print(f"     {_wait}s 后重启（第 {_crashes} 次退出）…")
                    time.sleep(_wait)

            time.sleep(2)

    except KeyboardInterrupt:
        safe_print("\n正在停止…")
    except Exception:
        # 启动脚本自身崩溃时不要杀面板：
        # 用户还要靠面板排查，杀掉就只能摸黑改 config.yaml。
        import traceback as _tb
        _keep_panel = True
        safe_print()
        safe_print("  [X] 启动脚本自身异常（不是机器人的问题）：")
        for _line in _tb.format_exc().strip().splitlines():
            safe_print("      " + _line)
        safe_print()
        safe_print("      面板保留，可用它继续排查；下次启动会自动清理残留进程。")
    finally:
        if _keep_panel:
            safe_print()
            safe_print("  面板仍在运行：http://127.0.0.1:%s" % port)
        else:
            # 必须连子进程一起杀：只杀 start.py 的话 webui / bot 会留下来占着端口，
            # 下次启动就只能一直往后跳端口（8092 → 8093 → 8094 …）
            # ⚠️ 取 _web_ref[0]：面板守护可能已经把它换成了重启后的新进程，
            #    只杀旧的那个，新的会留下来继续占端口。
            try:
                _wp = _web_ref[0]
            except Exception:
                _wp = web
            try:
                _panel_stop.set()
            except Exception:
                pass
            for p in (bot, _wp):
                kill_process_tree(p)
            clear_panel_url()
            safe_print("已停止。")


if __name__ == "__main__":
    sys.exit(main() or 0)
