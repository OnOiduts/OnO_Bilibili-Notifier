# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""配置读取与有效性校验（start.py / webui.py / check.py 共用）。"""
import os
import shutil
import subprocess
import sys
import re

import yaml

import paths

BASE = os.path.dirname(os.path.abspath(__file__))
PLACEHOLDER_RE = re.compile(r"x{3,}|你的|your_|请填写|example", re.I)


def cfg_path() -> str:
    """config.yaml 的位置：默认在数据目录（程序目录之外）。

    ⚠️ 为什么不跟源码放一起：升级要删掉整个文件夹，配置跟着一起没了就
       得重新填一遍。放在数据目录后，删文件夹再也删不到它。
    """
    return paths.config_path()


def load_cfg() -> dict:
    path = cfg_path()
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            cfg = {}
    # 落盘时敏感字段是密文，这里还原成明文供程序使用（只在内存里）
    try:
        import secretbox
        cfg = secretbox.reveal_cfg(cfg)
    except Exception:
        pass
    # 环境变量优先：配了 ONOBN_* 就以它为准，config.yaml 里那份被忽略。
    # 意义在于密钥可以完全不落盘 —— 公开仓库场景下不会误提交。
    try:
        import envcfg
        cfg = envcfg.apply_env(cfg)
    except Exception:
        pass
    # 面板访问口令也支持环境变量（ONOBN_PANEL_PASSWORD）。
    # ⚠️ 之前 .env.example 写了这一项、envcfg 也有读取函数，
    #    但没人调用它 —— 照着文档填了根本不生效。这里补上。
    # 环境变量里是明文，转成哈希塞进 cfg，verify_password() 就能正常比对。
    try:
        import envcfg
        _pw = envcfg.panel_password_from_env()
        if _pw:
            from security import hash_password
            cfg["web_password"] = hash_password(_pw)
            cfg["_web_password_from_env"] = True
    except Exception:
        pass
    return cfg


def save_cfg(cfg: dict):
    """写回 config.yaml：敏感字段先加密，文件权限只留本人。

    ⚠️ 为什么要放在这里（而不是只留在 webui 里）：
    备份/恢复工具也要写配置。两处各写一遍就会出现"一处加密、一处明文"，
    恢复完发现凭据是明文躺着的。
    """
    path = cfg_path()
    out = dict(cfg)
    # ⚠️ 关键：来自环境变量的凭据绝不写回 config.yaml。
    # 否则面板一保存，环境变量里的真密钥就以明文（经 secretbox 加密但仍落盘）
    # 落到配置文件里，等于把"不落盘"这个前提破坏掉了。
    try:
        import envcfg
        for field in envcfg.env_sourced_fields():
            out.pop(field, None)
    except Exception:
        pass
    # 口令若来自环境变量，同样不写回文件（理由同上）
    if out.pop("_web_password_from_env", None):
        out.pop("web_password", None)
    try:
        import secretbox
        out = secretbox.protect_cfg(out)
    except Exception:
        pass
    try:
        import yaml
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(out, f, allow_unicode=True, sort_keys=False)
    except Exception:
        return False
    try:
        from security import restrict_perms
        restrict_perms(path)
    except Exception:
        pass
    return True


def creds_ready(cfg: dict = None) -> bool:
    """AppID / AppSecret 是不是真的填了（排除模板占位符）。"""
    cfg = cfg if cfg is not None else load_cfg()
    appid = str(cfg.get("appid", "")).strip()
    secret = str(cfg.get("secret", "")).strip()
    if not appid or not secret:
        return False
    if PLACEHOLDER_RE.search(appid) or PLACEHOLDER_RE.search(secret):
        return False
    if not appid.isdigit():
        return False
    return len(secret) >= 8


def creds_fingerprint(cfg: dict = None) -> str:
    cfg = cfg if cfg is not None else load_cfg()
    return f"{str(cfg.get('appid', '')).strip()}|{str(cfg.get('secret', '')).strip()}"


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def safe_stdio():
    """把 stdout/stderr 换成 UTF-8，且遇到不能编码的字符就替换而不是崩溃。

    为什么必须做：
        Windows 中文环境默认编码是 GBK。当输出被重定向到文件（桌面版就是
        这样干的：stdout → backend.log），Python 会用 GBK 编码写，
        而日志里有 🛡️ ✅ ⏳ 这类 emoji —— GBK 编不了，直接抛
        UnicodeEncodeError，程序当场崩掉。

        你在桌面版里看到的 "锟斤拷" 乱码 + UnicodeEncodeError 就是这么来的。

    处理：优先用 UTF-8；拿不到就退到「替换不可编码字符」，绝不让它崩。
    """
    # 关键区分：
    #   输出到控制台  → 跟随控制台编码（Windows 中文是 GBK），
    #                   只把 errors 改成 replace，这样 emoji 变 ? 而不是崩溃，
    #                   中文则能正常显示（不会变成乱码）。
    #   输出到文件    → 用 UTF-8，日志文件里中文和 emoji 都完整。
    #
    # 之前无脑强制 UTF-8，结果控制台是 GBK，中文全变乱码。
    #
    # 另外：Windows 中文控制台是 GBK，表示不了 emoji。
    # 装了 safe_print() 统一处理 —— 控制台自动把 emoji 换成 ASCII 标记，
    # 重定向到文件时保留原样。这样既不崩，也不显示成问号。
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue

        is_tty = False
        try:
            is_tty = bool(stream.isatty())
        except Exception:
            is_tty = False

        # 控制台：保留原编码，只放宽 errors；文件：用 UTF-8
        enc = None if is_tty else "utf-8"
        try:
            if hasattr(stream, "reconfigure"):     # Python 3.7+
                if enc:
                    stream.reconfigure(encoding=enc, errors="replace")
                else:
                    stream.reconfigure(errors="replace")
                continue
        except Exception:
            pass
        try:
            import io as _io
            setattr(sys, name, _io.TextIOWrapper(
                stream.buffer,
                encoding=enc or (getattr(stream, "encoding", None) or "utf-8"),
                errors="replace", line_buffering=True))
        except Exception:
            pass


def kill_process_tree(proc, grace: float = 2.0):
    """终止进程及其所有子进程。

    为什么不能只 terminate 父进程：
        start.py 会再起 webui.py 和 bot.py。桌面版只杀了 start.py 的话，
        这两个"孙进程"还活着，端口就一直被占着 ——
        表现就是「关掉窗口了，下次启动端口还是被占用，只能一直往后跳」。

    Windows 上用 taskkill /T（/T = 连子进程一起杀）。
    """
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return

    pid = proc.pid
    try:
        proc.terminate()
        proc.wait(timeout=grace)
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=8)
            return
        except Exception:
            pass

    try:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=grace)
    except Exception:
        pass


def python_exe() -> str:
    """返回适合起子进程的解释器路径。

    为什么不能直接用 sys.executable：
        桌面版常被 pythonw.exe 拉起（为了不弹黑窗口），但 pythonw 没有控制台，
        sys.stdout / sys.stderr 为 None —— 日志库、第三方库写 stderr 时可能炸掉，
        而且报错完全看不见。子进程的窗口我们已经用 CREATE_NO_WINDOW 单独隐藏，
        不需要靠 pythonw 来藏，所以这里统一换回 python.exe。
    """
    exe = sys.executable or "python"
    low = exe.lower()
    if low.endswith("pythonw.exe"):
        cand = exe[: -len("pythonw.exe")] + "python.exe"
        if os.path.exists(cand):
            return cand
        # pythonw 旁边没找到 python.exe（便携版少见），退回去用 python
        which = shutil.which("python") or shutil.which("python3")
        if which:
            return which
    return exe


def pick_port(start: int = 8088, tries: int = 12) -> int:
    """从 start 开始找一个空闲端口。"""
    for p in range(start, start + tries):
        if not port_in_use(p):
            return p
    return start


_EMOJI_MAP = {
    "\u2705": "[OK] ", "\u274c": "[X] ", "\u26a0": "[!] ",
    "\U0001f6e1": "[safe]", "\u2699": "[cfg]",
    "\U0001f680": ">>", "\U0001f4a1": "*", "\U0001f517": "->",
    "\u23f3": "...", "\U0001f552": "[wait]", "\U0001f511": "[key]",
}


def safe_print(*args, **kwargs):
    """打印，但在 GBK 控制台上自动把 emoji 换成 ASCII 标记。

    控制台（Windows 中文 = GBK）表示不了 emoji：
      - 严格模式 → UnicodeEncodeError 崩溃（v1.1.1 之前就是这样）
      - replace   → 显示成 ?（能看，但不美观）
    这里主动替换成 [OK] / [X] 这类标记，可读性更好。
    输出到文件时保留 emoji。
    """
    try:
        stream = kwargs.get("file", sys.stdout) or sys.stdout
        is_tty = bool(getattr(stream, "isatty", lambda: False)())
    except Exception:
        is_tty = False

    if not is_tty:
        return print(*args, **kwargs)          # 文件/管道：原样

    enc = (getattr(stream, "encoding", None) or "utf-8").lower()
    utf8_ok = enc.replace("-", "") in ("utf8", "utf-8")
    if utf8_ok:
        return print(*args, **kwargs)

    out = []
    for a in args:
        t = a if isinstance(a, str) else str(a)
        for k, v in _EMOJI_MAP.items():
            t = t.replace(k, v)
        # 兜底：其余非 GBK 字符换成 ?
        try:
            t.encode("gbk")
        except UnicodeEncodeError:
            t = "".join(ch if _gbk_ok(ch) else "?" for ch in t)
        out.append(t)
    return print(*args[:0], *out, **kwargs)


_gbk_cache = {}


def _gbk_ok(ch):
    if ch not in _gbk_cache:
        try:
            ch.encode("gbk")
            _gbk_cache[ch] = True
        except Exception:
            _gbk_cache[ch] = False
    return _gbk_cache[ch]
