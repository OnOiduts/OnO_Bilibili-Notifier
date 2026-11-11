# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
# -*- coding: utf-8 -*-
"""清理本项目残留的 Python 进程。

为什么必须有这个：
  如果上一次的机器人没退干净（关窗口、崩溃、端口占用），
  新的机器人起来后就会和旧的**同时监听同一个群**，
  于是你发一条指令收到两条回复、同一条动态被推三次 ——
  这正是实际使用中出现的现象，不是代码逻辑问题，是多实例。

用法：
  python cleanup.py            # 清理并打印结果
  python cleanup.py --quiet    # 只清理不打印
  python cleanup.py --dry      # 只看看会清理谁
  python cleanup.py --stop     # 启动前用：检测到残留就关掉，没有就说明可以启动
"""
import os
import re
import subprocess
import sys
import threading
import time
import paths


def _decode_out(buf):
    """子进程输出统一解码。

    Windows 中文版的 locale 是 GBK，但 `chcp 65001` / PowerShell 的
    ConvertTo-Csv 会吐 UTF-8；subprocess 的文本模式按 locale 解码，
    遇到 UTF-8 续字节 0x80 就抛 UnicodeDecodeError，读线程整个崩掉。
    这里优先 UTF-8、再退回 GBK/mbcs，最后 replace 兜底：解析只认 PID
    和数字，个别中文字符变成替换符也不影响结果。
    """
    if buf is None:
        return ""
    if isinstance(buf, str):
        return buf
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            return buf.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return buf.decode("utf-8", errors="replace")

# ------------------------------------------------------------ Win32 进程工具
#
# ⚠️ 为什么不能再用 tasklist.exe / taskkill.exe / wmic：
#    这三者都依赖 WMI 的 framedyn.dll。本机 framedyn 损坏或缺失时，
#    它们启动即 0xc0000142 (STATUS_DLL_INIT_FAILED)，Windows 弹一个
#    "tasklist.exe - Application Error" 模态框挡在启动流程前面；
#    即便点掉「确定」，stdout 也是空的 → 残留检查误判成「没有残留」。
#    而「进程还活着吗 / 关掉它」本来就是 kernel32 的一次 API 调用：
#    OpenProcess + GetExitCodeProcess / TerminateProcess，微秒级、零子进程、
#    零弹窗。PowerShell（Get-CimInstance）走的是另一条 COM 路径，实测
#    在本机正常，故保留；它失败时也不再退回 wmic，改用纯 Python 兜底。
# ------------------------------------------------------------
_NT = (os.name == "nt")
_k32 = None
_PE32 = None
_INVALID_HANDLE = (1 << 64) - 1

if _NT:
    try:
        import ctypes
        from ctypes import wintypes

        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD]
        _k32.OpenProcess.restype = wintypes.HANDLE
        _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(wintypes.DWORD)]
        _k32.GetExitCodeProcess.restype = wintypes.BOOL
        _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        _k32.TerminateProcess.restype = wintypes.BOOL
        _k32.CloseHandle.argtypes = [wintypes.HANDLE]
        _k32.CloseHandle.restype = wintypes.BOOL
        _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        _k32.Process32FirstW.restype = wintypes.BOOL
        _k32.Process32NextW.restype = wintypes.BOOL

        class _PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        _PE32 = _PROCESSENTRY32W
        _k32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                         ctypes.POINTER(_PE32)]
        _k32.Process32NextW.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(_PE32)]
    except Exception:
        _k32 = None
        _PE32 = None

_STILL_ACTIVE = 259
_PROC_QUERY_LIMITED = 0x1000
_PROC_TERMINATE = 0x0001
_TH32CS_SNAPPROCESS = 0x00000002


def _win_pid_alive(pid: int) -> bool:
    """进程还活着吗 —— 纯 kernel32，不启动任何外部程序。"""
    if not _NT or _k32 is None:
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False
    h = None
    try:
        h = _k32.OpenProcess(_PROC_QUERY_LIMITED, False, int(pid))
        if not h:
            return False
        code = wintypes.DWORD()
        if not _k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    except Exception:
        return False
    finally:
        if h:
            try:
                _k32.CloseHandle(h)
            except Exception:
                pass


def _win_enum_procs():
    """{pid: ppid} —— kernel32 进程快照，纯 API，零外部进程。"""
    if not _NT or _k32 is None or _PE32 is None:
        return {}
    snap = None
    try:
        snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snap is None or int(snap or 0) in (0, _INVALID_HANDLE):
            return {}
        e = _PE32()
        e.dwSize = ctypes.sizeof(_PE32)
        out = {}
        ok = _k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID),
                                         str(e.szExeFile).lower())
            ok = _k32.Process32NextW(snap, ctypes.byref(e))
        return out
    except Exception:
        return {}
    finally:
        if snap:
            try:
                _k32.CloseHandle(snap)
            except Exception:
                pass


def _win_terminate(pid: int) -> bool:
    """终止单个进程 —— 替代 taskkill /F /PID。"""
    if not _NT or _k32 is None:
        try:
            os.kill(int(pid), 9)
            return True
        except Exception:
            return False
    h = None
    try:
        h = _k32.OpenProcess(_PROC_TERMINATE, False, int(pid))
        if not h:
            return False
        return bool(_k32.TerminateProcess(h, 1))
    except Exception:
        return False
    finally:
        if h:
            try:
                _k32.CloseHandle(h)
            except Exception:
                pass


def _win_terminate_tree(pid: int) -> bool:
    """连子进程一起关 —— 替代 taskkill /F /T。

    先子后父：父进程先死的话，子进程的 ppid 会变，树就断了。
    """
    procs = _win_enum_procs()
    if not procs:
        return _win_terminate(pid)
    seen = set()

    def _rec(p):
        if p in seen:
            return
        seen.add(p)
        for child, info in procs.items():
            parent = info[0] if isinstance(info, tuple) else info
            if parent == p:
                _rec(child)
        _win_terminate(p)

    _rec(int(pid))
    return True


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 只认这些脚本，避免误杀别的 Python 程序
TARGETS = ("start.py", "bot.py", "webui.py",
           "bili_login.py", "bili_browser_login.py", "security.py",
           "check.py")


PIDS_FILE = ".bot.pids"


def _pid_alive(pid: int) -> bool:
    """进程还活着吗（跨平台）。"""
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        if os.name == "nt":
            return _win_pid_alive(pid)
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _alive_set(pids) -> set:
    """批量判断一批进程号里哪些还活着 —— Windows 上零外部进程。

    ⚠️ 历史：最早 _pid_alive() 对每一个 pid 单独起一次 tasklist，
       50 个 pid 就是 15~50 秒；后来改成"一次 tasklist 取全机再比对"。
       两种都依赖 tasklist.exe，而它在本机会 0xc0000142 弹窗。
       现在 Windows 直接逐个 OpenProcess（微秒级），不再起任何子进程；
       非 Windows 保持 os.kill(pid, 0)，测试桩也仍替得住这一路。
    """
    ids = set()
    for p in pids or ():
        try:
            p = int(p)
        except (TypeError, ValueError):
            continue
        if p > 0:
            ids.add(p)
    if not ids:
        return set()
    if os.name == "nt":
        # OpenProcess 是微秒级 API 调用，一次都不用起子进程。
        # 以前为了少起几次 tasklist 才做"全机快照 + 内存比对"，
        # 现在批量反而多余，且 tasklist 损坏时会弹 0xc0000142。
        return {p for p in ids if _win_pid_alive(p)}
    # 非 Windows：os.kill(pid, 0) 本身是微秒级，批量化没有意义，
    # 这里统一走 _pid_alive —— 保持"只有一处真正判定存活"，
    # 测试替身（替 _pid_alive）也才替得住。
    return {p for p in ids if _pid_alive(p)}


def _norm(p):
    return (p or "").replace("/", "\\").lower()


def register_pid(pid=None):
    """把进程号记进 .bot.pids + 实例登记表，供下次清理使用。

    ⚠️ 不能只靠命令行匹配：bat 里若用相对路径启动
    （如 `python start.py`），命令行里就没有项目目录，
    判定函数永远匹配不上 —— 结果就是每次启动都多一个，
    越攒越多（实测攒到 15 个）。PID 文件是最可靠的兜底。
    """
    try:
        pid = int(pid if pid is not None else os.getpid())
        path = paths.run_file(PIDS_FILE)
        pids = set()
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.isdigit():
                        pids.add(int(line))
        except FileNotFoundError:
            pass
        pids.add(pid)
        # 只保留最近 50 个，避免文件无限增长
        with open(path, "w", encoding="utf-8") as f:
            for x in sorted(pids)[-50:]:
                f.write(str(x) + "\n")
    except Exception:
        pass
    # 顺带写实例登记表：命令行那条路在部分机器上不可用，这张表能兜住
    try:
        register_instance(pid if pid is not None else os.getpid())
    except Exception:
        pass


# ------------------------------------------------------------ 实例登记表
#
# ⚠️ 为什么必须有这张表：all_ours() 原来靠三条路 —— PID 文件、命令行
#    （Windows 上走 PowerShell / wmic）、端口（netstat）。后两条都要起
#    子进程，而 PowerShell 在不少机器上根本跑不起来（执行策略受限、
#    PowerShell 被精简、家庭版策略），wmic 在新版 Win11 上又被移除。
#    两条同时失败 → 返回空 → 面板显示「残留 0」、启动脚本打印
#    「没有检测到残留进程」，可旧实例还活着还在监听同一个群。
#    表现就是"机器人又不清除残留了"。
#    登记表不依赖任何外部命令：每个实例启动时自己写一张小纸条，
#    清理时只看这张纸条 + 进程还活着，稳。

REG_KEEP = 24 * 3600      # 登记表项最多认这么久（防止 PID 被别的程序复用）


def _reg_dir() -> str:
    d = os.path.join(paths.run_dir(), "instances")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _reg_file(pid: int) -> str:
    return os.path.join(_reg_dir(), "%d.json" % int(pid))


def _reg_write(pid: int, role: str = "") -> bool:
    """写一张实例纸条（重复写 = 刷新 mtime，等于"我还活着"）。"""
    try:
        import json as _json
        with open(_reg_file(pid), "w", encoding="utf-8") as f:
            _json.dump({"pid": int(pid), "role": role or "",
                        "argv": " ".join(sys.argv[:3]),
                        "ts": time.time()}, f)
        return True
    except Exception:
        return False


_refresher_started = False


def _start_refresher(interval: float = 15.0):
    """每 15 秒刷一次自己的纸条 mtime —— 证明"这不是一个死掉的残留登记"。"""
    global _refresher_started
    if _refresher_started:
        return
    _refresher_started = True

    def _loop():
        me = os.getpid()
        while True:
            try:
                p = _reg_file(me)
                if os.path.exists(p):
                    os.utime(p, None)
                else:
                    _reg_write(me)
            except Exception:
                pass
            time.sleep(interval)

    try:
        threading.Thread(target=_loop, daemon=True).start()
    except Exception:
        pass


def register_instance(pid=None, role: str = "") -> bool:
    """登记一个实例（自己的进程默认顺带起刷新线程）。"""
    try:
        pid = int(pid if pid is not None else os.getpid())
    except (TypeError, ValueError):
        return False
    ok = _reg_write(pid, role)
    if pid == os.getpid():
        _start_refresher()
    return ok


def registry_pids() -> list:
    """登记表里**还活着**的实例进程号（不依赖任何外部命令）。"""
    d = _reg_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return []
    cand = []
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            cand.append(int(n[:-5]))
        except ValueError:
            continue
    if not cand:
        return []
    alive = _alive_set(cand)
    now = time.time()
    out = []
    for pid in cand:
        p = _reg_file(pid)
        try:
            age = now - os.path.getmtime(p)
        except OSError:
            continue
        if pid not in alive or age > REG_KEEP:
            # 进程没了 / 纸条太旧（PID 可能已被别的程序复用）→ 清掉
            try:
                os.remove(p)
            except OSError:
                pass
            continue
        out.append(pid)
    return sorted(out)


def unregister_pid(pid=None):
    try:
        pid = int(pid if pid is not None else os.getpid())
        path = paths.run_file(PIDS_FILE)
        try:
            with open(path, encoding="utf-8") as f:
                pids = {int(x) for x in f if x.strip().isdigit()}
        except FileNotFoundError:
            return
        pids.discard(pid)
        with open(path, "w", encoding="utf-8") as f:
            for x in sorted(pids):
                f.write(str(x) + "\n")
    except Exception:
        pass
    try:
        os.remove(_reg_file(pid))
    except OSError:
        pass


def _pids_from_file():
    """从 .bot.pids 读出还活着的进程号（一次子进程批量判定）。"""
    path = paths.run_file(PIDS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            candidates = [int(x) for x in f if x.strip().isdigit()]
    except FileNotFoundError:
        return []
    except Exception:
        return []
    if not candidates:
        return []
    alive = _alive_set(candidates)
    # 保持文件里的原有顺序，避免写回时顺序乱跳（便于排查）
    seen, out = set(), []
    for p in candidates:
        if p in alive and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _proc_names() -> list:
    """要扫描的进程名 —— 源码版与 exe 版不一样。

    源码版：python.exe / pythonw.exe（脚本由解释器跑）
    exe 版：主程序自己的名字（OnOBN.exe）。

    ⚠️ 为什么必须分开：exe 里没有 python.exe，照旧只扫 python.*
       的话，残留实例**一个都扫不到**，用户反复双击就会开出一堆
       互相抢端口的进程，而"检测到残留"永远显示 0。
    """
    if getattr(sys, "frozen", False):
        n = os.path.basename(sys.executable or "")
        return [n] if n else []
    return ["python.exe", "pythonw.exe"]


def is_ours(cmdline: str) -> bool:
    """判断这条命令行是不是本项目的进程。

    两档判定：
    1. 命令行含**完整路径**（bat 用绝对路径启动时）→ 直接认
    2. 只有脚本名、没有路径（bat 用相对路径 `python start.py` 时）
       → 只要该脚本确实存在于本项目目录，也认

    ⚠️ 第 2 档是必须的：早期 bat 用相对路径启动，命令行里没有
    项目目录，只按第 1 档判会**永远匹配不上**，残留进程越攒越多。
    """
    c = _norm(cmdline)
    if not c:
        return False
    # exe 版：命令行形如 `C:\...\OnOBN.exe --child webui --host ...`，
    # 里面既没有 start.py 也没有 bot.py，按下面的脚本名判会全部漏掉。
    if getattr(sys, "frozen", False):
        exe = _norm(os.path.basename(sys.executable or ""))
        return bool(exe) and exe in c
    base = _norm(BASE_DIR)
    for t in TARGETS:
        if t not in c:
            continue
        if base in c:
            return True
        # 没有路径信息 → 看这个脚本是不是本项目的
        if os.path.exists(os.path.join(BASE_DIR, t)):
            return True
    return False


def _reg_argv(pid: int) -> str:
    """从实例登记表读回命令行（登记表本来就写了 argv 字段）。"""
    try:
        import json
        with open(_reg_file(int(pid)), encoding="utf-8") as f:
            return str(json.load(f).get("argv", "") or "")
    except Exception:
        return ""


def _list_ps_wmic() -> list:
    """PowerShell 失败时的兜底 —— 纯 Python，不再起 wmic。

    ⚠️ wmic 和 tasklist 同样依赖 WMI/framedyn.dll，本机一样会
       0xc0000142 弹窗；新版 Win11 还直接移除了 wmic。
       改为 kernel32 进程快照 + 三个「已知属于本项目」的来源
       （登记表 / PID 文件 / 面板端口）取交集，命令行从登记表读，
       全程零外部进程。宁可漏掉没登记的旧实例，也绝不误杀别的程序。
    """
    out = []
    if not _NT:
        return out
    procs = _win_enum_procs()
    if not procs:
        return out
    names = {n.lower() for n in _proc_names()}
    known = set()
    for src in (registry_pids, _pids_from_file, pids_on_ports):
        try:
            for p in src():
                known.add(int(p))
        except Exception:
            continue
    for pid, info in procs.items():
        exe = info[1] if isinstance(info, tuple) else ""
        if exe not in names or pid not in known:
            continue
        cmd = _reg_argv(pid)
        if cmd and is_ours(cmd):
            out.append((pid, cmd))
    return sorted(out)


def list_ours() -> list:
    """列出属于本项目的 python 进程（Windows 用 PowerShell/wmic，其它用 ps）。"""
    out = []
    if os.name == "nt":
        try:
            ps = (
                "Get-CimInstance Win32_Process -Filter \"" + " or ".join(
                    "Name='%s'" % n for n in _proc_names()
                ) + "\" | Select-Object ProcessId,CommandLine "
                "| ConvertTo-Csv -NoTypeInformation"
            )
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, timeout=25)
            for line in _decode_out(r.stdout).splitlines()[1:]:
                parts = [x.strip().strip('"') for x in line.split('","')]
                if len(parts) >= 2 and parts[0].isdigit():
                    pid, cmd = int(parts[0]), parts[1] if len(parts) > 1 else ""
                    if is_ours(cmd):
                        out.append((pid, cmd))
        except Exception:
            pass
        # PowerShell 拿不到（被禁/超时/精简版）时换 wmic，别直接返回空
        if not out:
            out = _list_ps_wmic()
        return out

    try:
        r = subprocess.run(["ps", "-eo", "pid=,args="],
                           capture_output=True, timeout=15)
        for line in _decode_out(r.stdout).splitlines():
            m = re.match(r"\s*(\d+)\s+(.*)", line)
            if m and is_ours(m.group(2)):
                out.append((int(m.group(1)), m.group(2)))
    except Exception:
        pass
    return out


def pids_on_ports(ports=(8088, 8089, 8090, 8091, 8092, 8093, 8094,
                         8095, 8096, 8097, 8098, 8099)) -> list:
    """找出占着面板端口的进程号。

    为什么需要这条兜底：
      之前只靠"命令行里有没有本项目脚本名"来判定。但面板进程可能
      是以各种方式起来的（相对路径 / 被别的脚本拉起 / 命令行拿不到），
      一旦匹配不上，面板就一直活着占着端口 —— 表现就是
      「程序关了，面板还能打开」。
      端口是硬证据：面板只要活着就一定占着一个端口。
    """
    out = []
    try:
        if os.name == "nt":
            r = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                               capture_output=True, timeout=20)
            lines = _decode_out(r.stdout).splitlines()
        else:
            r = subprocess.run(["ss", "-ltnp"], capture_output=True, timeout=15)
            lines = _decode_out(r.stdout).splitlines()
            if not lines:
                r = subprocess.run(["netstat", "-ltnp"], capture_output=True, timeout=15)
                lines = _decode_out(r.stdout).splitlines()
    except Exception:
        return out

    for ln in lines:
        if os.name == "nt":
            # TCP  127.0.0.1:8088  0.0.0.0:0  LISTENING  12345
            parts = ln.split()
            if len(parts) < 4 or "LISTENING" not in ln.upper():
                continue
            local, pid_s = parts[1], parts[-1]
            if ":" not in local:
                continue
            try:
                port = int(local.rsplit(":", 1)[1])
            except ValueError:
                continue
            if port not in ports or not pid_s.isdigit():
                continue
            out.append(int(pid_s))
        else:
            # LISTEN 0 128 127.0.0.1:8088 0.0.0.0:* users:(("python3",pid=123,fd=3))
            if "pid=" not in ln:
                continue
            for seg in ln.split():
                if ":" in seg and seg.rsplit(":", 1)[1].isdigit():
                    try:
                        port = int(seg.rsplit(":", 1)[1])
                    except ValueError:
                        continue
                    if port not in ports:
                        continue
                    for chunk in ln.split("pid=")[1:]:
                        digits = ""
                        for ch in chunk:
                            if ch.isdigit():
                                digits += ch
                            else:
                                break
                        if digits:
                            out.append(int(digits))
    # 去重
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _cmdline_of(pid: int) -> str:
    """拿到某个进程的命令行（用来确认它确实是我们的 python）。"""
    try:
        if os.name == "nt":
            ps = (
                "Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" "
                "| Select-Object CommandLine | ConvertTo-Csv -NoTypeInformation"
                % pid
            )
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, timeout=20)
            rows = _decode_out(r.stdout).splitlines()[1:]
            return rows[0].strip().strip('"') if rows else ""
        r = subprocess.run(["ps", "-p", str(pid), "-o", "args="],
                           capture_output=True, timeout=10)
        return _decode_out(r.stdout).strip()
    except Exception:
        return ""


def all_ours() -> list:
    """汇总**所有**属于本项目的进程号（三条路的并集），排除自己。

    ⚠️ 面板以前只调 `list_ours()` 来算"残留数"，而那条路在 Windows 上
       只有 PowerShell 一条路 —— 一旦它失败（执行策略受限/被精简），
       返回值就是空，面板于是显示「残留 0」，清理按钮也没有目标。
       进程明明还在跑（端口还占着），用户看到的就是"杀了没杀干净"。
       所以显示和清理**必须共用**这个三条路汇总的结果。
    """
    me = os.getpid()
    pids = set()
    # 登记表：不依赖 PowerShell / wmic / netstat，最稳的一路
    try:
        for p in registry_pids():
            if int(p) != me:
                pids.add(int(p))
    except Exception:
        pass
    try:
        for p in _pids_from_file():
            if p != me:
                pids.add(int(p))
    except Exception:
        pass
    try:
        for pid, _cmd in list_ours():
            if int(pid) != me:
                pids.add(int(pid))
    except Exception:
        pass
    # 端口兜底：只认确认是本项目 python 的，绝不误杀别的程序
    try:
        for pid in pids_on_ports():
            pid = int(pid)
            if pid == me or pid in pids:
                continue
            cmd = _cmdline_of(pid)
            if cmd and is_ours(cmd):
                pids.add(pid)
    except Exception:
        pass
    return sorted(pids)


def supervisor_pids() -> list:
    """监督进程（start.py）的进程号。

    start.py 是拉起面板和机器人的"管家"，它自己当然也是本项目进程。
    但它**不是残留**：把它杀掉等于把管家干掉，面板和机器人一起没，
    用户只是想清掉重复的实例，结果整个程序当场退出。

    ⚠️ 面板以前只把「面板自己 + 心跳里的 bot」算正常，start.py 漏了
       —— 于是运行状态页永远显示「进程 1 · 1 个残留，建议清理」，
        而那个"残留"正是正在好好干活的管家。
    """
    out = []
    try:
        for pid, cmd in list_ours():
            c = str(cmd or "").lower().replace("\\", "/")
            if "start.py" in c and "webui.py" not in c and "bot.py" not in c:
                try:
                    out.append(int(pid))
                except (TypeError, ValueError):
                    continue
    except Exception:
        pass
    return sorted(set(out))


def kill_ours(dry: bool = False, keep=None) -> int:
    """杀掉本项目残留进程。返回**实际确认已关闭**的数量。

    keep：要**保留**的进程号（面板自己 + 正在正常运行的机器人）。
    面板上的「清理残留进程」只该清掉重复的实例，
    把正在跑的机器人一起杀了反而变成"清理一次就掉线"。
    """
    skip = {int(x) for x in (keep or ()) if str(x).isdigit()}
    pids = [p for p in all_ours() if int(p) not in skip]
    if dry:
        return len(pids)
    if not pids:
        try:
            open(paths.run_file(PIDS_FILE), "w",
                 encoding="utf-8").close()
        except Exception:
            pass
        return 0

    for pid in pids:
        try:
            if os.name == "nt":
                # 连子进程一起关，否则孙进程（bot/webui）会留下来
                _win_terminate_tree(pid)
            else:
                os.kill(pid, 9)
        except Exception:
            pass

    # ⚠️ 杀完必须**复查**：终止可能因权限/僵尸状态没真生效，
    #    不复查的话返回的永远是"我发过命令了"，而不是"真的没了"。
    time.sleep(0.4)
    still = [p for p in pids if p in _alive_set(pids)]
    if still:
        # 再补一刀：单进程强杀（不带 /T 有时反而能杀掉）
        for pid in still:
            try:
                if os.name == "nt":
                    _win_terminate(pid)
                else:
                    os.kill(pid, 9)
            except Exception:
                pass
        time.sleep(0.3)
        still = [p for p in still if p in _alive_set(still)]

    # ⚠️ PID 文件必须在**杀完之后**重写。
    #    以前是在杀之前算 alive、然后写文件，于是文件里留下了一堆
    #    马上就要被杀掉的号；下次清理时这些号已死，看着像"清过了"。
    try:
        # ⚠️ 重写时**不要**再跑一遍 all_ours()：那等于把"扫全机 python
        #    进程 + netstat 查端口"整套再来一次，而 kill_ours() 开头刚跑过。
        #    一次 kill_ours 就要扫两遍、启动又调三次 kill_ours —— 六趟全机
        #    扫描，这是启动慢的主要来源。这里只写"确认还活着、且是我们
        #    尝试杀过的"，语义等价但零额外开销。
        alive = [p for p in still if p != os.getpid()]
        with open(paths.run_file(PIDS_FILE), "w",
                  encoding="utf-8") as f:
            for x in sorted(alive):
                f.write(str(x) + "\n")
    except Exception:
        pass
    # 被杀掉的实例要把纸条撤了，否则下次还会被当残留再杀一遍
    for pid in pids:
        if pid in still:
            continue
        try:
            os.remove(_reg_file(pid))
        except OSError:
            pass
    return len(pids) - len(still)


def stop_others(quiet: bool = False) -> int:
    """启动前先确认没有旧实例在跑，有就全部关掉。

    这是「每次打开启动机器人.bat 都从干净状态开始」的关键：
    残留实例会同时监听同一个群 → 一条动态推好几次。
    """
    n = kill_ours(dry=False)
    if not quiet:
        if n:
            print(f"检测到 {n} 个残留进程，已全部关闭")
        else:
            print("没有检测到残留进程，可以放心启动")
    return n


def main():
    dry = "--dry" in sys.argv
    quiet = "--quiet" in sys.argv
    if "--stop" in sys.argv:
        stop_others(quiet=quiet)
        return 0
    n = kill_ours(dry=dry)
    if not quiet:
        if dry:
            print(f"发现 {n} 个残留进程（未清理，dry 模式）")
        else:
            print(f"已清理残留进程：{n} 个" if n else "没有残留进程")
    return 0


if __name__ == "__main__":
    sys.exit(main())
