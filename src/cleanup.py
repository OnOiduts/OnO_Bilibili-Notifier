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
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                               capture_output=True, text=True, timeout=10)
            return str(pid) in (r.stdout or "")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _alive_set(pids) -> set:
    """批量判断一批进程号里哪些还活着 —— **只起一次子进程**。

    ⚠️ 为什么必须批量：以前 _pid_alive() 对**每一个** pid 单独起一次
       tasklist。Windows 上 tasklist 单次就要 0.3~1s，.bot.pids 里攒了
       多少个就起多少次 —— 攒到 50 个就是 15~50 秒。而 all_ours() 每次
       启动都要跑，跑一次就要这么久，"越用越慢"有一半是这么来的。
       现在一次 tasklist 把全机 PID 取回来，剩下的在内存里比对。
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
        try:
            r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                               capture_output=True, text=True, timeout=15)
        except Exception:
            # 批量拿不到就退回逐个（慢，但保证正确性）
            return {p for p in ids if _pid_alive(p)}
        live = set()
        for ln in (r.stdout or "").splitlines():
            # "python.exe","1234","Console","1","12,345 K"
            parts = [x.strip().strip('"') for x in ln.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                live.add(int(parts[1]))
        return {p for p in ids if p in live}
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


def _list_ps_wmic() -> list:
    """Windows 兜底：用 wmic 拿命令行。

    ⚠️ 为什么必须加这条：PowerShell 那条路在不少机器上**根本跑不起来**
       （执行策略受限 / PowerShell 被精简 / 家庭版策略），一旦失败
       `list_ours()` 就返回空 —— 面板据此显示"残留 0"，清理按钮也就
       无从下手。实际进程还活着，于是"杀了但没杀干净"。
       wmic 在新版 Win11 上被移除，所以两条**都要试**，谁成功用谁。
    """
    out = []
    try:
        r = subprocess.run(
            ["wmic", "process", "where",
             "name='python.exe' or name='pythonw.exe'", "get",
             "ProcessId,CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=25)
    except Exception:
        return out
    if not (r.stdout or "").strip():
        return out
    pid = 0
    cmd = ""
    for raw in (r.stdout or "").splitlines():
        line = raw.strip()
        if not line:
            if pid and is_ours(cmd):
                out.append((pid, cmd))
            pid, cmd = 0, ""
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().lower()
        if k == "processid":
            try:
                pid = int(v.strip())
            except (TypeError, ValueError):
                pid = 0
        elif k == "commandline":
            cmd = v.strip()
    if pid and is_ours(cmd):
        out.append((pid, cmd))
    return out


def list_ours() -> list:
    """列出属于本项目的 python 进程（Windows 用 PowerShell/wmic，其它用 ps）。"""
    out = []
    if os.name == "nt":
        try:
            ps = (
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or "
                "Name='pythonw.exe'\" | Select-Object ProcessId,CommandLine "
                "| ConvertTo-Csv -NoTypeInformation"
            )
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=25)
            for line in (r.stdout or "").splitlines()[1:]:
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
                           capture_output=True, text=True, timeout=15)
        for line in (r.stdout or "").splitlines():
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
                               capture_output=True, text=True, timeout=20)
            lines = (r.stdout or "").splitlines()
        else:
            r = subprocess.run(["ss", "-ltnp"], capture_output=True,
                               text=True, timeout=15)
            lines = (r.stdout or "").splitlines()
            if not lines:
                r = subprocess.run(["netstat", "-ltnp"], capture_output=True,
                                   text=True, timeout=15)
                lines = (r.stdout or "").splitlines()
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
                               capture_output=True, text=True, timeout=20)
            rows = (r.stdout or "").splitlines()[1:]
            return rows[0].strip().strip('"') if rows else ""
        r = subprocess.run(["ps", "-p", str(pid), "-o", "args="],
                           capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip()
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
            if cmd and ("python" in cmd.lower()) and is_ours(cmd):
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
                # /T 连子进程一起杀，否则孙进程（bot/webui）会留下来
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=15)
            else:
                os.kill(pid, 9)
        except Exception:
            pass

    # ⚠️ 杀完必须**复查**：taskkill 可能因权限/僵尸状态没真杀掉，
    #    不复查的话返回的永远是"我发过命令了"，而不是"真的没了"。
    time.sleep(0.4)
    still = [p for p in pids if p in _alive_set(pids)]
    if still:
        # 再补一刀：单进程强杀（不带 /T 有时反而能杀掉）
        for pid in still:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=10)
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
