# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
# -*- coding: utf-8 -*-
"""子进程看门狗：父进程没了，自己就退出。

为什么必须有这个
----------------
Windows 上直接**关掉控制台窗口**（点右上角 X）时，进程是被系统
强杀的 —— try/finally、atexit 全都不会执行。

start.py 里那句 `kill_process_tree(web)` 写在 finally 里，看起来很
稳妥，但关窗口时它**根本跑不到**。于是 webui.py 子进程留在后台继续
占着端口，表现就是：

    「程序已经关了，面板还能打开」

父进程自己都被强杀了，指望它清理子进程是不现实的。只能反过来 ——
让子进程盯着父进程：爹没了，我也跟着走。

用法
----
父进程启动时把自己的 PID 传过来：

    python webui.py --parent 12345

子进程入口调用一次：

    from parentwatch import start_watch
    start_watch(parent_pid)

没传 --parent 时不启用（独立运行 webui.py 不受影响）。
"""
import os
import sys
import threading
import time

# Windows: GetExitCodeProcess 返回这个值表示进程还在跑
_STILL_ACTIVE = 259


def _alive(pid: int) -> bool:
    """这个进程还活着吗（跨平台）。

    Windows 上不能用 os.kill(pid, 0) —— 那会真的发信号。
    这里用 OpenProcess + GetExitCodeProcess 查询。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.windll.kernel32
            # PROCESS_QUERY_LIMITED_INFORMATION：Vista 起可用，权限要求最低
            handle = k32.OpenProcess(0x1000, False, wintypes.DWORD(pid))
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return int(code.value) == _STILL_ACTIVE
            finally:
                k32.CloseHandle(handle)
        except Exception:
            # 查不到就当活着，宁可漏杀也别自杀错
            return True

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return True


def start_watch(parent_pid, interval: float = 3.0, name: str = "") -> bool:
    """启动守护线程：父进程消失就立刻退出本进程。

    返回是否真的启用了（没传父 PID 时返回 False，不影响独立运行）。
    """
    try:
        ppid = int(parent_pid or 0)
    except (TypeError, ValueError):
        ppid = 0
    if ppid <= 0:
        return False
    # 自己当自己的爹没有意义
    if ppid == os.getpid():
        return False

    def _loop():
        while True:
            time.sleep(interval)
            if not _alive(ppid):
                _quit(name)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return True


def _quit(name: str = ""):
    """父进程没了，自己也退出。

    用 os._exit 而不是 sys.exit：
      抛异常会被外层的 try/except 吞掉，退出不了；
      os._exit 是直接走人，不留余地。
    """
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)
