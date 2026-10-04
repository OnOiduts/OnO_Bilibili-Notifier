#!/usr/bin/env python3
# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""OnOB站通知订阅工具（OnO Bilibili Notifier，缩写 OnOBN）
腾讯 QQ 开放平台官方机器人 + 哔哩哔哩订阅提醒

一个 UP 主可以同时开「直播 / 投稿 / 动态」三类提醒，每个群、每类提醒都能单独开关，
所有提醒都带跳转按钮和封面图。
"""
import asyncio
import logging as stdlib_logging
import os
import re
import subprocess
import sys
import time
import traceback
from typing import Optional

import botpy
import yaml
from botpy import logging

import heartbeat
import version as _version
from cfgutil import safe_print, safe_stdio
from bilibili import (BiliClient, BiliError, BiliTransientError,
                      SeasonGoneError, SeasonPush, SeasonVideo)
from notify import render, short_hint, strip_images, to_plain
from qqapi import QQClient, build_keyboard
from security import redact
from db import DYN_SEEN_KEEP, KIND_LABEL, Store
import db as db          # 数据路径统一入口（data_path_from_cfg）
import paths

# Windows 中文控制台是 GBK，直接 print emoji 会 UnicodeEncodeError 崩溃。
# safe_stdio 把输出流放宽，safe_print 在控制台上把 emoji 换成 ASCII 标记。
safe_stdio()

_log = logging.get_logger()

# 登录态失效期间把轮询固定住的档位（秒）。
# ⚠️ 认证类失败（-352/-403/HTTP 412）不是"我们太猛"，放慢到上限没意义，
#    但也不能照常加速 —— 否则会在持续失败时一路冲到最快档猛撞墙。
#    这里取一个温和的中间档：比基线略慢，又不至于恢复后要爬很久。
AUTH_HOLD_INTERVAL = 30

# 合集"新视频"判定容差（秒）。见 _season_sync_group 里的升级保护：
# 发布时间只要不早于"上次检测"超过这个量，就算新内容。
#
# ⚠️ 不能只按"轮询间隔"给（以前是 300）：B 站合集列表的索引有延迟，
#    视频发布/加入合集后往往要过几分钟才在列表里出现，那段时间我们
#    照常写基线，等它出现时 pubdate 已经早于上次检测好几分钟 ——
#    300 秒不够，整条就被判成旧内容静默掉，表现是"合集更新了不推送"。
#    给到 3600 秒；升级保护照样成立（那时补出来的是几个月前的老视频）。
#    可用 config.yaml 的 season_fresh_grace 覆盖。
SEASON_FRESH_GRACE = 3600

# 日志文件的行首格式（与下面 FileHandler 的 fmt 一致）：
# 2026-09-25 12:00:00 [INFO] …
# 用来判断「这行是不是 logging 已经写过一遍的」，避免再被 Tee 写第二次。
_LOG_LINE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \["
    r"(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\]")
# setup_file_log() 只允许真正生效一次
_FILE_LOG_READY = False


class _Tee:
    """把控制台输出同时抄一份进日志文件（抓 botpy 的 print / traceback）。

    ⚠️ 只抄**非日志行**：长得像
    ``2026-09-25 12:00:00 [INFO] …`` 的说明 FileHandler 已经写过一遍了，
    这里再抄一次就是「同一条消息在日志页出现两次」。
    """

    def __init__(self, orig, f):
        self.orig, self.f = orig, f

    def write(self, data):
        try:
            self.orig.write(data)
        except Exception:
            pass
        try:
            if not _LOG_LINE_RE.match(data.lstrip("\r\n")):
                self.f.write(data)
                self.f.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self.orig.flush()
        except Exception:
            pass

    def isatty(self):
        return False


def _has_file_handler(logger, path: str) -> bool:
    """这个 logger 上是否已经有写同一个日志文件的 FileHandler。"""
    try:
        want = os.path.abspath(path)
    except Exception:
        return False
    for h in getattr(logger, "handlers", None) or []:
        try:
            if os.path.abspath(getattr(h, "baseFilename", "") or "") == want:
                return True
        except Exception:
            continue
    return False


_LOG_MAX_BYTES = 8 * 1024 * 1024      # 超过 8MB 就瘦身
_LOG_KEEP_LINES = 20000               # 只留最近 2 万行


def _trim_log_file(path, max_bytes=_LOG_MAX_BYTES,
                   keep=_LOG_KEEP_LINES) -> int:
    """日志太大就只留最后 N 行，返回删掉的行数。

    ⚠️ bot.log 只追加、从不清理。实测攒到 27 万行 / 9.2MB，而且会一直涨。
       而面板「日志」页每次都要读它 —— 文件越大，面板打开越慢。
       这是"用得越久越慢"的第三处来源，必须在启动时先瘦一次身。
    """
    try:
        if os.path.getsize(path) <= max_bytes:
            return 0
    except OSError:
        return 0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        if len(lines) <= keep:
            return 0
        tail = lines[-keep:]
        tmp = path + ".trim.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(tail)
        os.replace(tmp, path)          # 原子替换，写一半断电也不会丢
        return len(lines) - len(tail)
    except OSError:
        return 0


def setup_file_log():
    """把日志同时写进 bot.log，面板的「日志」页才看得到机器人这边的情况。

    之前 botpy 只往控制台打，面板读 *.log 文件永远是空的。
    这里给根 logger 挂一个 FileHandler，botpy 和本文件走的都是标准 logging，
    所以两边都能落到文件里。
    """
    global _FILE_LOG_READY
    # ⚠️ 幂等：这个函数只要被多跑一次，root 上就多一个写同一个文件的
    #    FileHandler —— 之后**每一条日志都被写两遍**，面板日志页看到的就是
    #    「同一条消息紧挨着出现两次」。加个标记，重复调用直接跳过。
    if _FILE_LOG_READY:
        return
    # 日志放数据目录：删掉程序文件夹时不会把日志一起删了，
    # 而且源码目录可以保持干净。
    path = paths.log_file("bot.log")
    # 日志已经攒得很大时先瘦身：否则面板每读一次都要吞下整个 9MB，
    # 打开「日志」页会明显卡顿，而且文件只会越来越大。
    try:
        dropped = _trim_log_file(path)
        if dropped:
            stdlib_logging.getLogger(__name__).info(
                f"bot.log 过大，已精简：丢掉最早的 {dropped} 行，"
                f"保留最近 {_LOG_KEEP_LINES} 行")
    except Exception:
        pass
    try:
        # 必须带年份：日志要按「年月日」分组显示时间轴，
        # 只有 %m-%d 的话跨年就分不出来了。
        fmt = stdlib_logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        fh = stdlib_logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(stdlib_logging.DEBUG)
        root = stdlib_logging.getLogger()
        # 同文件只挂一个：以前无脑 addHandler，重跑一次就双写。
        if not _has_file_handler(root, path):
            root.addHandler(fh)
        root.setLevel(stdlib_logging.INFO)
        # 同时把 stdout/stderr 也接一份到文件，避免漏掉 botpy 的 print 输出。
        # ⚠️ 但要**躲开已经由 FileHandler 写过一遍的日志行**：
        #    控制台那条 handle 走的是 sys.stderr → 被这里 Tee 进文件，
        #    于是同一条消息在 bot.log 里出现两次（用户看到的"重复消息"）。
        #    凡是长得像日志行的（时间戳 + [级别]），说明 FileHandler 已经
        #    写过，这里只给控制台，不再写文件。print / traceback 照常收。
        _fp = open(path, "a", encoding="utf-8", errors="replace")
        # 已经接过就不再套一层，否则会变成 套娃 → 一条写三遍
        if not isinstance(sys.stdout, _Tee):
            sys.stdout = _Tee(sys.stdout, _fp)
        if not isinstance(sys.stderr, _Tee):
            sys.stderr = _Tee(sys.stderr, _fp)
        _FILE_LOG_READY = True
    except OSError:
        pass


setup_file_log()


def apply_log_level():
    """按 config.log_debug 设置根 logger 级别。

    「输出调试日志」以前是个虚构项 —— 面板上有开关、配置里没有这个键，
    保存后被忽略，看着像坏了。现在把它做成真功能：
    开 → DEBUG（细节写进 bot.log）；关 → INFO。
    面板改完不用重启，轮询每轮对一次（只在配置文件改动时才真的读）。
    """
    try:
        import cfgutil
        want = (stdlib_logging.DEBUG if bool(cfgutil.load_cfg().get(
            "log_debug", False)) else stdlib_logging.INFO)
    except Exception:
        return
    root = stdlib_logging.getLogger()
    if root.level != want:
        root.setLevel(want)


_LL_MTIME = [0.0]
try:
    import cfgutil as _cu
    _LL_MTIME[0] = os.path.getmtime(_cu.cfg_path())
except Exception:
    pass


# 面板保存后无需重启就能生效的字段。
# ⚠️ 只放"每次用的时候从 self.cfg 现取"的项；poll_interval 之类牵扯
#    轮询节奏/_adapt_interval 状态机的不放进来，避免改一半打架。
HOT_CFG_KEYS = (
    "image_send_mode", "send_image", "message_style", "notify_offline",
    "top_comment_only_up", "silence_dyn_after_push", "dedupe_window",
    "request_timeout", "min_request_interval", "season_max_pages",
    "season_push_limit", "image_tmp_ttl", "image_inline_width",
    "image_size_hint", "image_size_hint_w", "image_size_hint_h",
    "image_probe_size",
    "image_size_hint_avatar", "image_size_hint_max_h",
)

_CFG_MTIME = [0.0]


def sync_cfg_from_disk(bot):
    """配置文件改过 → 把可热更新的字段并进 bot.cfg。

    以前面板保存只写 config.yaml，运行中的 bot 还抱着启动时那份 cfg，
    表现就是"改了没生效，得重启"。
    """
    try:
        import cfgutil as _cu
        mt = os.path.getmtime(_cu.cfg_path())
    except Exception:
        return
    if mt == _CFG_MTIME[0]:
        return
    _CFG_MTIME[0] = mt
    try:
        disk = _cu.load_cfg() or {}
    except Exception:
        return
    changed = []
    for k in HOT_CFG_KEYS:
        if k in disk and disk.get(k) != getattr(bot, "cfg", {}).get(k):
            bot.cfg[k] = disk[k]
            changed.append(k)
    if changed:
        _log.info("配置已热更新：%s", "、".join(changed))
        if any(k.startswith("image_size_hint") for k in changed):
            apply_size_hint(bot.cfg)
        if "image_inline_width" in changed:
            apply_inline_pic_size(bot.cfg)


def apply_inline_pic_size(cfg=None) -> None:
    """把 image_inline_width 应用到渲染层（内嵌图片压到多大）。

    只影响 inline 模式卡片里的 ![](url)。
    ⚠️ 默认 0 = 不加 CDN 参数、**用原图不压缩**；想压就设成 672 之类的宽度
    （原图很大，手机端加载可能失败，所以留了这个口子，但默认不压，
    画质优先）。
    """
    try:
        import notify as _n
    except Exception:
        return
    try:
        w = int((cfg or {}).get("image_inline_width", 0) or 0)
    except (TypeError, ValueError):
        w = 0
    w = max(0, min(w, 1920))
    _n.set_inline_pic_size(w, 0 if w <= 0 else int(round(w * 9 / 16)))


def apply_size_hint(cfg=None) -> None:
    """把 image_size_hint* 应用到渲染层（QQ markdown 图片的尺寸提示）。

    ⚠️ 这个开关默认**开**。关掉就退回 !![alt](url)，手机端可能看不到图。
    """
    try:
        import notify as _n
    except Exception:
        return
    c = cfg or {}
    _n.set_size_hint(c.get("image_size_hint", True),
                     c.get("image_size_hint_w", 0),
                     c.get("image_size_hint_h", 0),
                     c.get("image_size_hint_avatar", 0),
                     c.get("image_size_hint_max_h", -1),
                     c.get("image_max_per_msg", -1),
                     c.get("image_size_hint_cell", -1),
                     c.get("image_grid_mode"))
    # 真实尺寸探测：关掉后只靠 URL 里的 @参数，解析不到就 16:9 兜底
    try:
        _n.PROBE_SIZE_ON = c.get("image_probe_size", True) is not False
    except Exception:
        pass


def sync_log_level():
    """每轮轮询调一次：配置文件改过才重新读，平时只是一次 stat。"""
    try:
        import cfgutil as _cu
        mt = os.path.getmtime(_cu.cfg_path())
    except Exception:
        return
    if mt == _LL_MTIME[0]:
        return
    _LL_MTIME[0] = mt
    apply_log_level()
    _log.info("日志级别已更新：%s",
              "DEBUG（输出调试日志）"
              if stdlib_logging.getLogger().level == stdlib_logging.DEBUG
              else "INFO")


apply_log_level()


def _install_excepthook():
    """未捕获异常必须留下痕迹。

    进程被异常顶退时退出码是 1，而控制台可能已经关了，
    用户只能看到一句「退出码 1」，完全不知道哪里炸了。
    这里把 traceback 写进 bot.log，保证事后能在面板「日志」页查到。
    """
    def _hook(exc_type, exc, tb):
        try:
            _log.error("未捕获异常，进程即将退出：%s: %s" % (exc_type.__name__, exc))
            for chunk in traceback.format_exception(exc_type, exc, tb):
                for ln in str(chunk).rstrip().splitlines():
                    _log.error("  " + ln)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


_install_excepthook()


# 输出安全：Windows GBK 环境下 emoji 会导致 UnicodeEncodeError 崩溃
from cfgutil import safe_stdio
safe_stdio()


INVALID_KIND = "类型只能是：直播 / 视频 / 动态"


# 已处理过的消息 ID（QQ 平台可能重复推送同一条事件，
# 不去重就会出现"一条指令回两条"——历史问题：
# 用户发一次「退订」，机器人回了"本群没有订阅"和"已取消订阅"两条矛盾的话）。
_SEEN_MSG: dict = {}
_SEEN_TTL = 300          # 秒


def panel_url(port: int = 0) -> str:
    """网页面板地址。优先读 start.py 写的 .panel_url，避免端口写死。"""
    try:
        with open(paths.run_file(".panel_url"), encoding="utf-8") as f:
            u = (f.read() or "").strip()
        if u:
            return u
    except Exception:
        pass
    return f"http://127.0.0.1:{port or 8088}/"


# 单实例锁：同一台机器只允许一个机器人在跑。
# 多实例会导致一条指令回两次、同一条动态推三次（实测现象）。
_LOCK_FP = None


def acquire_lock() -> bool:
    """拿到锁返回 True；已有实例在跑返回 False。"""
    global _LOCK_FP
    path = paths.run_file(".bot.lock")
    try:
        # 清掉过期锁（进程已死）
        try:
            with open(path, encoding="utf-8") as f:
                old_pid = int((f.read() or "0").strip() or 0)
            if old_pid and old_pid != os.getpid() and not _pid_alive(old_pid):
                os.remove(path)
        except FileNotFoundError:
            pass
        except Exception:
            pass

        if os.path.exists(path):
            return False
        _LOCK_FP = open(path, "w", encoding="utf-8")
        _LOCK_FP.write(str(os.getpid()))
        _LOCK_FP.flush()
        return True
    except Exception:
        return True      # 拿不到锁也让它跑，别因为锁本身把机器人卡住


def release_lock():
    try:
        if _LOCK_FP:
            _LOCK_FP.close()
    except Exception:
        pass
    try:
        os.remove(paths.run_file(".bot.lock"))
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                               capture_output=True, text=True, timeout=10)
            return str(pid) in (r.stdout or "")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _already_seen(msg_id: str, extra: str = "") -> bool:
    """同一条消息只处理一次。返回 True 表示已经处理过了。

    QQ 平台会重复推送同一条事件（实测：发一次 @ 收到两条回复）。
    只靠 msg_id 不够 —— 有些事件根本不带 id，
    所以额外用「群 + 内容」做指纹，短窗口内相同的算同一条。
    """
    key = msg_id or ("fp:" + (extra or ""))
    if not key or key == "fp:":
        return False
    now = time.time()
    for k in [k for k, t in _SEEN_MSG.items() if now - t > _SEEN_TTL]:
        _SEEN_MSG.pop(k, None)
    if key in _SEEN_MSG:
        return True
    _SEEN_MSG[key] = now
    # 内容指纹和 msg_id 互相登记，避免"先来的没 id、后到的有 id"漏判
    if msg_id and extra:
        _SEEN_MSG["fp:" + extra] = now
    return False


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class NotifyBot(botpy.Client):
    def __init__(self, cfg: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg = cfg
        apply_inline_pic_size(cfg)
        # 数据库存储（data.db）。首次运行会自动把旧的 data.json 导入进来，
        # 老用户升级后不用重新配置一遍。
        # ⚠️ 必须走 db.data_path_from_cfg()：跟面板、安全自检、启动自检
        #    共用同一个解析入口。各算各的会算出两个不同的文件，面板就
        #    永远看不到机器人正在推的那些订阅（v1.98.3）。
        df = db.data_path_from_cfg(cfg)
        try:
            from db import migrate_if_needed
            # ⚠️ 迁移目标必须是 Store 真正用的那个文件。以前这里写
            #    paths.state_path()（默认路径），而 Store 走 data_file
            #    自定义路径 —— 用户改过存放位置后，旧 data.json 被迁到
            #    默认文件里，实际用的那份什么都没迁到。
            _d = os.path.dirname(df) or "."
            migrate_if_needed(df, os.path.join(_d, "data.json"))
        except Exception as e:
            _log.warning(f"旧数据迁移跳过：{e}")
        self.store = Store(df)
        # 老用户升级：把旧的「UP 主级」基线复制成「按群」基线。
        # 这样各群继承自己当前的进度，升级后行为连续，
        # 不会因为升级把所有群都当成新订阅重新对齐一遍。
        try:
            n_bl = self.store.migrate_baseline_to_per_group()
            if n_bl:
                _log.info(f"已把 {n_bl} 条旧基线迁移为按群基线")
        except Exception as e:
            _log.warning(f"基线迁移跳过（不影响运行）：{e}")
        self._group_name_denied = set()      # 已确认拿不到群名的群，避免反复请求
        # B站「登录态不被认」的汇总告警节流：每个 UP 每轮都报一次的话，
        # 5 个 UP × 每 24 秒 = 日志被同一件事刷满，真正的推送反而看不见。
        self._auth_warn_at = 0.0
        self.bili = BiliClient(
            cookie=cfg.get("bili_cookie", ""),
            timeout=cfg.get("request_timeout", 10),
            min_interval=cfg.get("min_request_interval", 2.0),
        )
        self.qq = QQClient(
            cfg.get("appid"), cfg.get("secret"),
            sandbox=bool(cfg.get("is_sandbox", False)),
        )
        # 「沙箱环境」以前只在启动时读一次，面板关掉后运行中的进程
        # 还在走 sandbox 域名 —— 表现就是"关不上"：面板显示关了，
        # 发消息照样回 400 沙箱限制。记住当前状态，每轮对一次。
        self._sandbox_on = bool(cfg.get("is_sandbox", False))
        self.tpls = dict(cfg.get("templates") or {})
        # 每个订阅项可以有自己的文案（覆盖全局）。
        # UP 主级文案的短缓存（10 秒），改完立刻生效不用重启
        self._up_tpl_cache = {}
        # 自适应轮询：默认贴着风控边缘跑（12s），
        # 撞到风控就自动放慢，稳定一段时间后再逐步加快回边缘。
        self.base_interval = max(5, int(cfg.get("poll_interval", 12)))
        # 早期版本默认是 60s，配置文件留着旧值就会一直偏慢。
        # 用户多次反馈"检测要等"，这里启动时明确提示一次。
        if self.base_interval > 20:
            _log.warning(
                f"当前轮询基线 {self.base_interval}s 偏慢（早期版本遗留值）；"
                "想更快可在 config.yaml 设 poll_interval: 12 "
                "（或面板「⚙️ 设置」里改），撞风控会自动放慢")
        self.min_interval = max(3, int(cfg.get("poll_interval_min", 8)))
        self.max_interval = max(self.base_interval,
                                int(cfg.get("poll_interval_max", 120)))
        self.interval = self.base_interval
        self._risk_seen = 0
        self._calm_rounds = 0
        # 间隔调整日志的合并状态：见 _adapt_interval / _iv_settled
        self._iv_from = None      # 本次「加快链」的起点间隔
        self._iv_last_log = 0.0   # 上次打间隔日志的时间戳（冷却用）
        self._silent = False   # 启动同步期间为 True → 只记基线不推送
        # 记录各类提醒的推送时间，用于「直播/视频 vs 动态」去重
        self._recent_push = {}
        # 「主动消息无权限」的群先歇一会儿再试：{gid: 到期时间戳}
        # 不歇的话每一轮都要推两次（Markdown 一次、纯文本一次），
        # 每次都是同一个错，日志里成对刷屏，还白白消耗主动消息额度。
        self._noperm_until = {}
        # 冷却时长（秒）。默认 10 分钟：够用户去群里点一下开关，
        # 又不至于长时间真的漏推。填 0 表示不冷却（老行为）。
        self._noperm_cd = max(0, int(cfg.get("push_noperm_cooldown", 600)))
        # 事件里自带的角色（若能拿到就不必查接口）
        self._ev_role = ""
        # 群成员角色缓存：{gid|mid: (role, ts)}，避免每条消息都查接口
        self._role_cache = {}
        self._role_ttl = 600.0
        # ⚠️ 这里原本还有一行旧代码，会把上面算好的自适应间隔
        # 直接覆盖成固定的慢速默认值 —— 自适应等于白做。已删除，
        # 现在间隔统一由 base/min/max 三者决定，只在此赋值一次。
        # 投稿和动态变化慢，每 N 轮才查一次，省请求
        self.slow_every = max(1, int(cfg.get("slow_check_every", 1)))
        self.notify_offline = bool(cfg.get("notify_offline", False))
        self.send_image = bool(cfg.get("send_image", True))
        self._poll_task: Optional[asyncio.Task] = None
        self._hb_task: Optional[asyncio.Task] = None
        self._round = 0
        # 合集连续取不到的次数，(uid, season_id) -> n，用来抑制刷屏
        self._season_fail = {}
        # 合集连续临时失败时还要再歇几轮，(uid, season_id) -> 剩余轮数
        self._season_skip = {}
        # 开播时刻（uid -> Unix 秒），用于下播时算"本场直播时长"。
        # ⚠️ 必须在**还播着**的时候记：B站房间接口的 live_time 一旦下播
        #    就归 0，等下播再去取已经没有了。重启后第一次检测若发现正在
        #    直播，会用接口给的 live_time 补记（见 _check_live）。
        self._live_since = {}

    def _live_since_map(self) -> dict:
        """开播时刻表（uid -> Unix 秒），懒初始化后返回。

        ⚠️ 不能只在 __init__ 里建一次就完事：只要存在不经过 __init__
        构造 NotifyBot 的路径（测试脚本、外部嵌入调用），_check_live
        一进来就 AttributeError，而这个异常会被轮询的兜底 except 吞掉
        —— 表现是"直播提醒一条都不推、日志里还什么都看不到"，极难排查。
        这里兜底保证任何情况下拿到的都是 dict。
        """
        m = getattr(self, "_live_since", None)
        if not isinstance(m, dict):
            m = {}
            self._live_since = m
        return m

    # ============ 生命周期 ============
    async def on_ready(self):
        _log.info(f"程序版本 v{_version.current()}")
        _log.info(f"机器人 {self.robot.name} 已上线，开始监听")
        heartbeat.mark(logged_in=True, bot_name=getattr(self.robot, "name", ""))

        # 启动就向 B站 求证一次登录态：以前只看本地有没有 cookie，
        # 于是 cookie 其实早就失效了，日志里却只有每个 UP 一条 -352，
        # 看不出根因是"没登录"。这里一次性说清楚。
        try:
            acc = await self._bili_verify()
            if acc.get("state") == "logged":
                _log.info("B 站登录态：已登录（%s，UID %s）",
                          acc.get("uname") or "", acc.get("mid") or "")
            elif acc.get("state") == "none":
                _log.warning("B 站登录态：未登录。投稿/动态接口可能返回 -352/412，"
                             "去面板「⚙️ 凭据 → B站登录」扫码登录")
            elif acc.get("state") == "invalid":
                _log.warning("B 站登录态：cookie 已失效/不被认可（%s）。"
                             "请重新扫码或粘贴完整 Cookie", acc.get("msg") or "")
                try:
                    heartbeat.touch(bili_login={"ok": False, "at": time.time(),
                                                "reason": acc.get("msg") or ""})
                except Exception:
                    pass
            else:
                _log.info("B 站登录态：暂时没能验证（%s），稍后会自动重试",
                          acc.get("msg") or "")
        except Exception:
            pass

        # 每次启动先**静默同步**一轮：只把各 UP 主的当前状态记成基线，
        # 绝不推送。这样"重启期间发生的事"不会被当成新内容补推一遍，
        # 之后才开始按周期检测。
        try:
            n = await self._startup_sync()
            _log.info(f"启动同步完成：{n} 位 UP 主的当前状态已记录为基线"
                      f"（静默，不推送）")
        except Exception as e:
            _log.warning(f"启动同步失败（不影响后续轮询）：{e}")

        # 核实一遍还在不在这些群里（离线期间被踢的群，事件收不到）
        try:
            dead = await self._prune_dead_groups()
            if dead:
                _log.info(f"启动清理：{dead} 个群已不在（机器人已被移出），"
                          f"订阅和文案一并清除")
        except Exception as e:
            _log.debug(f"启动核实群失败（不影响使用）：{e}")

        # asyncio 里 task 抛异常时，默认只打一句
        # "Task exception was never retrieved"，不带堆栈，根本没法排查。
        # 挂个 handler 把完整信息写进日志。
        try:
            self.loop.set_exception_handler(self._on_asyncio_error)
        except Exception:
            pass

        if self._poll_task is None or self._poll_task.done():
            self._poll_task = self.loop.create_task(self._poll_loop())
        if self._hb_task is None or self._hb_task.done():
            self._hb_task = self.loop.create_task(self._heartbeat_loop())

    def _on_asyncio_error(self, loop, context):
        """asyncio 异常处理：把 task 崩溃的完整堆栈写进日志。"""
        try:
            exc = context.get("exception")
            _log.error("asyncio 异常：%s" % context.get("message", ""))
            if exc is not None:
                for chunk in traceback.format_exception(
                        type(exc), exc, exc.__traceback__):
                    for ln in str(chunk).rstrip().splitlines():
                        _log.error("  " + ln)
        except Exception:
            pass

    async def _heartbeat_loop(self):
        """定期写心跳，让网页面板能判断机器人是否在线。

        ⚠️ 和 _poll_loop 一样多包一层：这个协程一旦结束，面板就永远
        显示"离线"，而机器人其实还活着 —— 看着像崩了，实际只是心跳停了。
        """
        while True:
            try:
                await self._heartbeat_loop_inner()
                return
            except asyncio.CancelledError:
                raise
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as e:
                _log.error(f"心跳循环异常（已自动重启）：{e}")
                for line in traceback.format_exc().strip().splitlines():
                    _log.error(f"  {line}")
                _d = getattr(self, "_loop_delay", 10)
                try:
                    await asyncio.sleep(_d)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await asyncio.sleep(_d)

    async def _heartbeat_loop_inner(self):
        while True:
            try:
                heartbeat.touch(
                    groups=len(self.store.all_groups()),
                    ups=len(self.store.all_ups()),
                    polls=self._round,
                )
            except Exception:
                pass
            await asyncio.sleep(10)

    async def close(self):
        # 先置 _stopping：轮询/心跳循环靠它区分"真要停"和
        # asyncio 内部泄漏出来的 CancelledError（见 _poll_loop_inner）。
        self._stopping = True
        try:
            for task in (self._poll_task, self._hb_task):
                if task:
                    task.cancel()
        finally:
            await self.bili.close()
            await self.qq.close()
            await super().close()

    # 新群第一次 @ 机器人时的回复。只回这一次，之后这个群完全静默。
    GREETING_NEW_GROUP = "感谢使用OnOiduts产品"

    # ============ 群事件 ============
    async def on_group_add_robot(self, event):
        """被拉进群：**只静默登记，一句话都不说**。

        ⚠️ 之前这里会发一段"已就位 + 用法"的欢迎语。被拉进群就弹一坨
        文字很打扰人，而且主动消息没开时还会失败、在日志里留一条警告。
        现在改成纯登记 —— 群出现在面板里就够了，话留到有人 @ 时再说。
        """
        gid = getattr(event, "group_openid", None)
        if not gid:
            return
        self.store.ensure_group(gid)
        _log.info(f"机器人被加入群聊：{gid}（静默，不发言）")
        # 刚进群往往还没拿到主动发言权限，直接推会撞 40034105。
        # 与其等第一次推送失败才发现，不如进群就提醒一句。
        _log.info(
            "提醒：刚进的群通常还没开通主动消息 —— "
            "在群里 @ 一下机器人或发条消息激活一次，"
            "并在群设置里打开「允许机器人主动发言」，否则提醒发不出去")
        try:
            asyncio.create_task(self._fetch_group_name(gid))
        except Exception:
            pass

    def sync_sandbox(self):
        """「沙箱环境」开关改了不用重启机器人。

        is_sandbox 以前只在启动时读一次，面板上关掉后运行中的进程
        还在走 sandbox.api.sgroup.qq.com —— 表现就是"关不上"：
        面板显示关了，发消息照样回 400 沙箱限制。
        这里每轮询一轮对一次配置，变了就换域名并重新取 token
        （沙箱和正式是两套 token，换域必须重取）。
        """
        try:
            import cfgutil as _cu
            want = bool(_cu.load_cfg().get("is_sandbox", False))
        except Exception:
            return
        if want == self._sandbox_on:
            return
        self._sandbox_on = want
        try:
            import qqapi as _qa
            self.qq.base = _qa.BASE_SANDBOX if want else _qa.BASE
        except Exception:
            return
        # 两套环境的 token 不通用，换域后必须重新获取
        try:
            self.qq._token = ""
            self.qq._token_expire = 0.0
        except Exception:
            pass
        _log.info("沙箱环境已切换为：%s（%s）",
                  "开（沙箱）" if want else "关（正式）", self.qq.base)

    async def _prune_dead_groups(self) -> int:
        """启动时核实一遍：机器人是不是还在这些群里。

        ⚠️ 光靠 group_del_robot 事件不够 —— 机器人**离线期间**被踢，
        事件根本收不到，那个群就会一直留在库里：面板显示一个永远
        收不到消息的群，订阅照旧往那推、每次失败、日志反复刷警告。

        所以启动主动查一次。只删**明确**说"不在群里"的；
        接口未开通（11253）、网络抖动都不删。
        """
        n = 0
        for gid in list(self.store.all_groups() or []):
            try:
                await self.qq.get_group_info(gid)
            except Exception as e:
                if is_gone_group_error(e):
                    if self._drop_group(gid, why="机器人已不是该群成员"):
                        n += 1
                # 其它原因（未开通 / 网络 / 频控）一律不动
            await asyncio.sleep(0.25)
        return n

    def _note_send_error(self, gid: str, err) -> bool:
        """推送失败时判断：是不是机器人已经不在这个群了。

        是就把这个群连同它的数据一起删掉 —— 否则每轮都往那推一次，
        每次都失败，日志里反复刷同一条警告。

        ⚠️ 只认明确的错误码（见 is_gone_group_error），
        网络抖动 / 频控 / 内容违规都不删。
        """
        if not gid:
            return False
        if not is_gone_group_error(err):
            return False
        return self._drop_group(gid, why="推送失败，机器人已不在该群")

    def _mark_no_perm(self, gid: str) -> None:
        """记下"这个群主动消息无权限"，冷却期内不再往它推。

        ⚠️ 只冷却、绝不删群：群还在、订阅还在，
        只是官方暂时不允许主动发言。等用户开了开关还能继续收。
        """
        if not gid or not getattr(self, "_noperm_cd", 0):
            return
        try:
            self._noperm_until[gid] = time.time() + self._noperm_cd
        except Exception:
            pass

    def _noperm_active(self, gid: str) -> bool:
        """该群是否还在「主动消息无权限」的冷却里；冷却到点会自动解除。

        ⚠️ 必须用 getattr 兜底，不能直接 self._noperm_until：
        有些构造路径（测试、热重载后的部分重建）不经过 __init__，
        直接取属性会 AttributeError —— 一抛就把**整条推送**打断，
        表现是"这一轮什么都没推出来"，而日志里只留一行看不懂的报错。
        """
        d = getattr(self, "_noperm_until", None)
        if not isinstance(d, dict):
            d = {}
            try:
                self._noperm_until = d
            except Exception:
                pass
        until = 0
        try:
            until = float(d.get(gid, 0) or 0)
        except Exception:
            until = 0
        if not until:
            return False
        if time.time() < until:
            return True
        d.pop(gid, None)      # 冷却结束，下一轮正常再试一次
        return False

    def _drop_group(self, gid: str, why: str = "") -> bool:
        """删群 + 清内存缓存。on_group_del_robot 和推送失败兜底共用。"""
        name = ""
        try:
            g = self.store.get_group(gid)
            name = (g or {}).get("name") or ""
        except Exception:
            pass
        try:
            n = self.store.remove_group(gid)
        except Exception as e:
            _log.warning(f"清理群 {gid[:8]}… 失败：{e}")
            return False
        for attr in ("_up_tpl_cache", "_greeted_groups"):
            box = getattr(self, attr, None)
            if isinstance(box, dict):
                box.pop(gid, None)
            elif isinstance(box, set):
                box.discard(gid)
        role = getattr(self, "_role_cache", None)
        if isinstance(role, dict):
            for k in [k for k in role if isinstance(k, str)
                      and k.startswith(gid + "|")]:
                role.pop(k, None)
        who = f"{name}（{gid[:8]}…）" if name else f"{gid[:8]}…"
        tag = f"，{why}" if why else ""
        _log.info(
            f"已清理群 {who}{tag}：订阅 {n.get('subs', 0)} 条、"
            f"合集 {n.get('seasons', 0)} 个、群文案 {n.get('tpl', 0)} 条")
        try:
            self.store.refresh()
        except Exception:
            pass
        return True

    async def on_group_del_robot(self, event):
        """被移出群聊：**删掉这个群的所有数据**。

        官方事件名 group_del_robot（botpy 有 parse_group_del_robot），
        事件对象和 on_group_add_robot 一样是 GroupManageEvent，
        所以 group_openid 直接可用。

        ⚠️ 不清理的后果：面板里一直挂着一个永远收不到消息的群，
        订阅照旧往那推、每次都失败，日志里反复刷"发送失败"。
        """
        gid = getattr(event, "group_openid", None)
        if not gid:
            return
        self._drop_group(gid, why="机器人被移出群聊")

    async def on_group_msg_reject(self, event):
        _log.warning(
            "群 {getattr(event, 'group_openid', None)} 关闭了主动消息，提醒将发不出去"
        )

    async def on_group_msg_receive(self, event):
        _log.info("群 {getattr(event, 'group_openid', None)} 已开启主动消息")

    async def on_group_at_message_create(self, message):
        """收到 @ 消息：登记群；**只有第一次见到这个群才回一句话**。

        规则（v1.26.1）：
          - 机器人被拉进群时**不发言**（避免打扰，见 on_group_add_robot）
          - 群里第一次 @ 机器人 → 回复一次感谢语，之后这个群再 @ 一律不理
          - 已经登记过的群 → 完全不交互，只有订阅的推送会发

        ⚠️ 感谢语走**被动回复**（带 msg_id）。被动回复是回复用户消息的
        行为，不需要"允许主动消息"权限 —— 群设置里只开了那个开关也能回。
        万一被动回复失败（比如消息已过期），才退到主动发送。
        """
        try:
            gid = (getattr(message, "group_openid", "") or "").strip()
            if not gid:
                return
            is_new = gid not in self.store.data.get("groups", {})
            self.store.ensure_group(gid)

            # 群名：尽力向官方取，拿不到也不影响（面板可手动改）
            try:
                asyncio.create_task(self._fetch_group_name(gid))
            except Exception:
                pass

            # 平台偶尔会重复推送同一条事件。登记后 gid 已存在于库里，
            # 第二次进来 is_new 就是 False 了 —— 但并发时可能两条都读到
            # 同一个旧快照，所以再用内存记一道，避免回两次感谢语。
            greeted = getattr(self, "_greeted_groups", None)
            if greeted is None:
                greeted = set()
                self._greeted_groups = greeted
            if gid in greeted:
                return
            greeted.add(gid)

            if not is_new:
                _log.debug(f"群 {gid} 已登记过，不回复")
                return

            _log.info(f"新群首次 @：登记并回复感谢语 group_openid={gid}")
            try:
                await self.qq.send_text(gid, self.GREETING_NEW_GROUP,
                                        msg_id=getattr(message, "id", None))
            except Exception as e:
                _log.warning(f"感谢语被动回复失败，改用主动发送：{e}")
                try:
                    await self.qq.send_text(gid, self.GREETING_NEW_GROUP)
                except Exception as e2:
                    # 发送失败不影响登记 —— 群已经在面板里了
                    _log.warning(f"感谢语发送失败（不影响使用）：{e2}")
        except Exception as e:
            _log.debug(f"登记群失败：{e}")

    def _tpls_for(self, gid: str, season_key: tuple = None,
                  uid: int = None) -> dict:
        """取实际使用的文案，四层回落：

            内置默认 → 全局配置 → UP主专属 → 合集专属

        ⚠️ v1.30.1 移除了「群专属」这一层：入口已挪到每个订阅项后面，
        群级没有编辑入口，留着只会变成改不了又删不掉的孤儿数据。
        老库里已存的群级文案在启动时摊到了各 UP 主上，不会丢。

        为什么不在启动时一次性读好：
          面板里改完文案希望能**立刻生效**，不重启机器人。
        所以每次推送时读一次，并做短时缓存避免每轮都查库。

        ⚠️ 每层都是**稀疏**的：只存特地改过的项。
        没改过的项回落到上一层 —— 这样上层改了默认值，
        没特地改过的会跟着变，符合直觉。
        """
        merged = dict(self.tpls)
        # 更细一层：这个群对**这个 UP 主**的文案
        if uid:
            try:
                now = time.time()
                ck = (gid, int(uid))
                hit = self._up_tpl_cache.get(ck)
                if hit and now - hit[0] < 10:
                    uown = hit[1]
                else:
                    uown = self.store.get_up_templates(gid, int(uid)) or {}
                    self._up_tpl_cache[ck] = (now, uown)
            except Exception:
                uown = {}
            if uown:
                merged = {**merged, **uown}
        # 最细一层：某个合集自己的文案（比群级更优先）
        if season_key:
            try:
                sown = self.store.get_season_templates(
                    gid, int(season_key[0]), int(season_key[1])) or {}
            except Exception:
                sown = {}
            if sown:
                merged = {**merged, **sown}
        return merged

    def _remember_pushed(self, uid: int, kind: str, obj) -> None:
        """记下这次推送的具体内容（直播间号 / BV 号）+ 内容自身的发布时间。

        去重靠的是内容比对：开播后 B 站自动发的动态里，
        指向的正是刚开播的那个直播间，房间号对得上就静默。

        ⚠️ 为什么还要记 `pub`（内容自身的发布时间）：
        投稿的那一刻 B 站就自动发了动态，而我们**下一轮轮询**才检测到
        投稿 —— 中间可能隔好几分钟。拿"我们推送的时刻"去和动态发布时间
        比，动态永远显得"更早"，于是被误判成旧动态，结果是
        「投稿提醒 + 投稿动态」连着推两条。所以要比就比**两件事各自的
        发生时间**（投稿时间 vs 动态发布时间），它们几乎同时。
        """
        rec = getattr(self, "_recent_push", None)
        if rec is None:
            rec = self._recent_push = {}
        val = ""
        pub = 0
        if kind == "live":
            val = str(getattr(obj, "room_id", 0) or 0)
            # 开播时刻；接口没给就用"这次推送的时刻"兜底
            pub = int(getattr(obj, "live_time", 0) or 0)
        elif kind in ("video", "season"):
            val = str(getattr(obj, "bvid", "") or "")
            pub = int(getattr(obj, "pubdate", 0) or 0)
        if not val or val in ("0", ""):
            # ⚠️ 动态没有"自己的内容号"，但它可能**指向**某个视频/直播间
            #    （投稿动态 → BV，开播动态 → 房间号）。指向的内容照样要记，
            #    否则「动态先被检测到 → 视频后到」时会再推一遍。
            self._mark_content_pushed(uid, kind, obj)
            if kind == "dynamic":
                # ⚠️ 光记指向的 BV 还不够：新版投稿动态把视频地址藏在
                #    跳转链接里，取不到 BV 时上面那份索引是空的，
                #    视频随后被发现就会再推一遍（「新动态」后又来
                #    「新视频投稿」，内容其实是同一个）。
                #    所以额外记一份"动态推过"的时间，供
                #    `_should_silence_video` 按发布时间兜底比对。
                rec.setdefault(uid, {})["dynamic"] = {
                    "val": str(getattr(obj, "dyn_id", "") or ""),
                    "ts": time.time(),
                    "pub": int(getattr(obj, "pubdate", 0) or 0),
                }
            return
        # 内容时间取不到就退回推送时刻（老行为），不因此放弃去重
        rec.setdefault(uid, {})[kind] = {
            "val": val, "ts": time.time(),
            "pub": pub or int(time.time()),
        }
        self._mark_content_pushed(uid, kind, obj)

    # ---- 「同一件事」双向去重 ----------------------------------------
    #
    # 投稿 / 开播时 B 站会**自动**发一条动态，指向同一个视频 / 同一个
    # 直播间。这两条其实是一件事，只该提醒一次。
    #
    # 谁先被检测到是不确定的：动态接口通常比投稿接口快（视频要等索引），
    # 于是常常是「动态先推、视频随后」。而旧逻辑只在"动态被视频静默"
    # 这一个方向上生效 —— 动态先到时它压根没有记录可查，视频就又推一遍。
    #
    # 所以这里按**内容**建一份索引：不管由哪一类提醒先推出去，记的都是
    # 那个视频 / 那个直播间，后到的那一类查得见就不再推。
    # 谁先发现就推谁，另一方向静默。

    @staticmethod
    def _content_key(kind: str, obj):
        """这次推送涉及的"具体内容"：(内容类型, 标识)。

        live → 直播间号；video/season → BV 号；
        dynamic → 它指向的那个视频 / 直播间（没指向就是空）。
        """
        if kind == "live":
            return ("room", str(getattr(obj, "room_id", 0) or 0))
        if kind in ("video", "season"):
            return ("video", str(getattr(obj, "bvid", "") or ""))
        if kind == "dynamic":
            bv = str(getattr(obj, "ref_bvid", "") or "")
            if bv:
                return ("video", bv)
            rm = str(getattr(obj, "ref_room_id", 0) or 0)
            if rm and rm != "0":
                return ("room", rm)
        return ("", "")

    def _mark_content_pushed(self, uid: int, kind: str, obj) -> None:
        """记下"这个内容已经由某一类提醒推出去过"。"""
        ctype, val = self._content_key(kind, obj)
        if not ctype or not val or val in ("0", ""):
            return
        idx = getattr(self, "_pushed_content", None)
        if idx is None:
            idx = self._pushed_content = {}
        rec = {"by": kind, "ts": time.time(),
               "pub": int(getattr(obj, "pubdate", 0) or 0)}
        idx.setdefault(uid, {})[(ctype, val)] = dict(rec)
        # 直播间有长号/短号两种写法：开播提醒记的是接口给的 room_id，
        # 开播动态里抠出来的是链接里的号。两种都记，比对时才对得上。
        if ctype == "room":
            alt = str(getattr(obj, "short_room_id", 0) or 0)
            if alt and alt not in ("0", str(val)):
                idx.setdefault(uid, {})[(ctype, alt)] = dict(rec)

    def _content_pushed_by(self, uid: int, ctype: str, val: str,
                           by_kinds) -> bool:
        """这个内容是不是已经被 by_kinds 里的某一类提醒推过了。"""
        if not ctype or not val or val in ("0", ""):
            return False
        idx = getattr(self, "_pushed_content", None) or {}
        hit = (idx.get(uid) or {}).get((ctype, str(val)))
        if not hit or hit.get("by") not in by_kinds:
            return False
        window = float(self.cfg.get("dedupe_window", 900))
        if time.time() - float(hit.get("ts", 0) or 0) >= window:
            return False
        return True

    def _gid_also_gets_dynamic(self, uid: int, gid: str) -> bool:
        """这个群有没有开「动态」提醒。

        ⚠️ 反向去重只在**该群也会收到那条动态**时才成立：
        只订了投稿的群压根收不到动态，若一并静默，它就什么都收不到了。
        """
        try:
            return gid in set(self.store.targets(uid, "dynamic") or [])
        except Exception:
            return False

    def _pushed_recently(self, uid: int, kind: str, val: str) -> bool:
        """某个具体内容是否在时间窗内推送过。"""
        if not val:
            return False
        rec = getattr(self, "_recent_push", None) or {}
        hit = (rec.get(uid) or {}).get(kind)
        if not hit:
            return False
        window = float(self.cfg.get("dedupe_window", 900))
        if time.time() - hit.get("ts", 0) >= window:
            return False
        return str(hit.get("val")) == str(val)

    def _should_silence_dynamic(self, uid: int, d) -> bool:
        """这条动态要不要静默 —— **按指向的内容**判定，不按类型。

        场景：
        - 开播 → B 站自动发一条动态，那条动态指向**刚开播的直播间**
          → 房间号对得上 → 静默
        - 投稿 → B 站自动发一条动态，指向**刚投的那个视频**
          → BV 号对得上 → 静默

        ⚠️ 反过来不成立：普通动态（图文）不指向任何直播间/视频，
        不会误伤。
        ⚠️ 直播预约（reserve）是 UP 主动发的预告，
        即便带房间号也**不静默**（它通知的是"未来要播"，不是"正在播"）。
        """
        mt = getattr(d, "major_type", "") or "normal"
        if mt == "reserve":
            return False

        room = int(getattr(d, "ref_room_id", 0) or 0)
        # ⚠️ 房间号对得上就算，长号/短号两种写法都认：
        # 开播提醒记的是接口给的 room_id，而动态里抠出来的是链接里的号，
        # 两者可能一个是长号一个是短号 —— 只比一种就会漏，
        # 漏了就是「开播提醒 + 开播动态」连着推两条。
        if room and (self._pushed_recently(uid, "live", str(room))
                     or self._content_pushed_by(uid, "room", str(room),
                                                ("live",))):
            return True

        bvid = str(getattr(d, "ref_bvid", "") or "")
        # 投稿提醒 / 合集更新都可能先推过同一个 BV
        if bvid and (self._pushed_recently(uid, "video", bvid)
                     or self._pushed_recently(uid, "season", bvid)):
            return True

        # 兜底：指向不是每次都取得到。开播动态在某些版本里 major 只有
        #   opus（纯文字 / 图文），压根不给 live_rcmd 卡片 → ref_room_id=0，
        #   上面两条都判不出来，于是「开播提醒 + 开播动态」连着推两条。
        #   这时按时间窗兜底：窗内刚推过开播/投稿提醒，而这条动态又是在
        #   那之后发的 → 认作同一件事，静默掉。
        if self._silence_after_push(uid, d):
            return True

        return False

    def _silence_after_push(self, uid: int, d) -> bool:
        """刚推过开播/投稿提醒，这条动态多半就是 B 站自动发的那条。

        ⚠️ 副作用：UP 主直播期间手发的普通动态也会被一并吞掉。
           不想要就在 config.yaml 里设 silence_dyn_after_push: false。
        """
        if not self.cfg.get("silence_dyn_after_push", True):
            return False
        rec = (getattr(self, "_recent_push", None) or {}).get(uid) or {}
        if not rec:
            return False
        window = float(self.cfg.get("dedupe_window", 900) or 900)
        # 两件事"算同一件"的时间容差（秒）。
        # 投稿与它自带的动态是同一秒发生的；开播与开播动态同理。
        # 真正需要容差的是轮询延迟 + 两端时钟/取整误差，300 秒足够宽。
        same_win = float(self.cfg.get("dup_same_event_window", 300) or 0)
        now = time.time()
        pub = int(getattr(d, "pubdate", 0) or 0)
        if not pub:
            # ⚠️ 取不到发布时间就没法判断"是不是开播之后发的"。
            #    宁可重复推一条，也不能把真动态吞掉 —— 与 _is_newer_content
            #    一个原则：判定不了就按"推"处理。
            return False
        for kind in ("live", "video"):
            hit = rec.get(kind)
            if not isinstance(hit, dict):
                continue
            ts = float(hit.get("ts", 0) or 0)
            if not ts or now - ts >= window:
                continue      # 超出窗口 → 不再吞动态
            cpub = int(hit.get("pub", 0) or 0)
            if cpub:
                # ⚠️ 比的是**两件事各自的发生时间**，不是"我们推送的时刻"。
                #    投稿那一刻 B 站就自动发了动态，而我们要下一轮轮询才
                #    发现投稿 —— 中间隔几分钟。用推送时刻比，动态永远显得
                #    "更早"，于是被当成旧动态放行，结果投稿提醒之后又来
                #    一条投稿动态。两条时间挨在一起才是同一件事。
                if abs(pub - cpub) <= same_win:
                    return True
                continue      # 时间对不上 → 多半不是同一件事
            # 内容时间取不到 → 退回老的"推送时刻"比较
            if pub < ts - 60:
                continue      # 动态明显早于那次推送 → 旧动态
            return True
        return False

    def _should_silence_season(self, gid: str, uid: int, v) -> bool:
        """这条合集更新要不要静默 —— 它刚作为「新投稿」推过了。

        与 `_video_covered_by_season` 对称：那边管"合集先检测到 →
        投稿跳过"，这边管"投稿先推了 → 合集别再推一遍"。

        ⚠️ 为什么需要这一边：B 站合集列表的索引比投稿接口慢，
        视频常常先以「新投稿」推掉，过几分钟才出现在合集列表里。
        那时基线里还没有它，_video_covered_by_season 当时也判不出来，
        于是又来一条「合集更新」—— 同一条视频推两次。

        ⚠️ 必须**按群**判断：只在"这个群也订了投稿"时才静默。
        只订合集的群没收到过那条投稿，一并静默它就什么都收不到了。

        ⚠️ 只认 `video` 这一种记录，不认 `season`：同一个合集在**别的群**
        推过，不该让这个群也跟着安静。
        """
        bvid = str(getattr(v, "bvid", "") or "")
        if not bvid:
            return False
        try:
            if gid not in set(self.store.targets(uid, "video") or []):
                return False
        except Exception:
            return False     # 读不出来就按"要推"处理
        lookback = max(float(self.cfg.get("dedupe_window", 900) or 0),
                       float(self.cfg.get("season_dedupe_lookback", 3600)
                             or 0))
        rec = (getattr(self, "_recent_push", None) or {}).get(uid) or {}
        hit = rec.get("video")
        if not isinstance(hit, dict):
            return False
        ts = float(hit.get("ts", 0) or 0)
        if not ts or time.time() - ts >= lookback:
            return False
        return str(hit.get("val", "")) == bvid

    def _should_silence_video(self, uid: int, v) -> bool:
        """这条投稿要不要静默 —— 该 UP 的动态**先**被检测到并推过了。

        与 `_silence_after_push` 对称：那边管"视频先推 → 别再推动态"，
        这边管"动态先推 → 别再推视频"。

        ⚠️ 反向去重原本只靠 BV 号比对：动态里取得到 BV 才拦得住。
        而新版投稿动态把视频地址藏在跳转链接里，取不到 BV 时那份索引
        是空的，于是「新动态」推完、「新视频投稿」又来一条 ——
        其实是同一件事。这里按**发布时间**兜底：窗内刚推过这个 UP 的
        动态，而这条投稿的发布时间与那条动态挨在一起 → 认作同一件事。

        谁先被发现就保留谁：动态接口通常更快，先到的多半是动态。
        """
        if not self.cfg.get("silence_dyn_after_push", True):
            return False
        rec = (getattr(self, "_recent_push", None) or {}).get(uid) or {}
        hit = rec.get("dynamic")
        if not isinstance(hit, dict):
            return False
        window = float(self.cfg.get("dedupe_window", 900) or 900)
        # 投稿接口通常比动态接口慢（视频要等索引），"动态先推、视频十几
        # 分钟后才出现"是常态。默认窗口只有 15 分钟，一过就会在「新动态」
        # 之后再补一条「新视频投稿」。所以这里给一个不低于 1 小时的回溯：
        # 只要这条投稿的发布时间与那条动态挨在一起，就是同一件事。
        lookback = max(window,
                       float(self.cfg.get("dup_same_event_lookback", 3600)
                             or 0))
        ts = float(hit.get("ts", 0) or 0)
        if not ts or time.time() - ts >= lookback:
            return False          # 超出回溯 → 不再吞投稿
        same_win = float(self.cfg.get("dup_same_event_window", 300) or 0)
        vpub = int(getattr(v, "pubdate", 0) or 0)
        dpub = int(hit.get("pub", 0) or 0)
        if vpub and dpub:
            # 两边都拿到发布时间 → 挨在一起才算同一件事
            return abs(vpub - dpub) <= same_win
        if not vpub:
            # 判定不了就按"推"处理，绝不因为接口没给时间就吞掉真投稿
            return False
        # 动态发布时间取不到 → 退回"动态推送的时刻"比对
        return abs(vpub - ts) <= same_win

    @staticmethod
    def _is_newer_content(cur_pub: int, base_pub: int) -> bool:
        """ID 变了，但内容是不是真的"新"？—— 用发布时间判定。

        ⚠️ 只比 ID 会踩这个坑：
        最新一条被 UP 主删掉（或撤稿/转私密）后，接口返回的"最新"
        就退回了**更早**的那条。ID 与基线不同 → 判定成新内容 →
        把几天前的旧稿/旧动态再推一遍。

        判定规则：
        · 两边都拿到时间 → 只有当新条目的时间**更晚**才算新内容
        · 时间相同或更早   → 是删除/撤稿暴露出来的旧内容，静默
        · 任一边取不到时间 → 无法判定，退回原来的"按 ID 判"（推），
          避免因为接口没给时间就彻底不提醒

        ⚠️ 用 `>` 而不是 `>=`：同一秒发布的重复条目不该刷两遍。
        """
        try:
            cur = int(cur_pub or 0)
            base = int(base_pub or 0)
        except (TypeError, ValueError):
            return True
        if cur <= 0 or base <= 0:
            return True          # 取不到 → 无法判定 → 保持原行为
        return cur > base

    @staticmethod
    def _wm_pub(prev_pub: int, cur_pub: int) -> int:
        """基线时间的水位线：**只许涨，不许跌**。

        ⚠️ 为什么必须有这个函数 —— 与上一个函数配套的第二个坑：
        「回退为更早的」判定出来之后，原来是无条件把基线改写成当前
        这条（更早的）动态，等于把水位线**往下拉**了。

            UP 删掉最新那条 A（T_A）→ 最新退回 B（T_B < T_A）
            → 判为回退、静默，但基线 pubdate 被写成 T_B
            → A 恢复 / 接口又返回 A：T_A > T_B，判成"新内容"
            → 把订阅前就存在的旧动态 A 又推一遍

        用户日志里那两行矛盾的输出就是这么来的：
        先「回退为更早的，静默不推送」，紧接着又「已推送」。

        所以判定出"不是新内容"时，ID 照更新，但时间必须保留原来
        更高的那一档 —— 已经见过的内容不该因为水位下降而重新变新。

        取不到时间的情形（任一边为 0）维持原行为：写当前值，
        因为那时 _is_newer_content 也走了"无法判定 → 按新内容推"。
        """
        try:
            prev = int(prev_pub or 0)
            cur = int(cur_pub or 0)
        except (TypeError, ValueError):
            return 0
        if prev <= 0:
            return cur
        if cur <= 0:
            return prev
        return max(prev, cur)

        return "已取消对本 UP 主的全部订阅。"

    # ============ 基线 ============
    # 订阅开关的 kind → 按群基线的 kind（两套名字，不能混用）
    _BL_KIND = {"live": "live", "video": "video",
                "dynamic": "dyn", "top_comment": "dyn_top_cmt"}

    def _missing_baselines(self, uid: int, kinds) -> list:
        """哪些 (群, 基线类型) 还没有基线 —— 需要静默对齐。

        新订阅、或给已有 UP 主新开了某个提醒类型，都属于这一类：
        先静默记下当前状态，之后的检测才是"正常工作"。
        没对齐就参与比对，会把订阅之前就存在的内容当成新内容推一遍。
        """
        out = []
        for k in (kinds or []):
            bl_kind = self._BL_KIND.get(k)
            if not bl_kind:
                continue
            try:
                gids = self.store.targets(uid, k) or []
            except Exception:
                gids = []
            for gid in gids:
                if not (self.store.baseline_of(gid, uid, bl_kind) or {}):
                    out.append((gid, bl_kind))
        return out

    async def _init_baseline(self, uid: int, only_missing: bool = True):
        """订阅/新增类型时立刻记一次当前状态，避免把历史内容当新内容推送。

        ⚠️ 只给**没有基线**的群写（only_missing）。
        不加这个保护会出严重问题：给 B 群做初始化时，会把 A 群的基线
        也一并改成当前最新 —— A 群本来正等着推的那条就被吞掉了。
        不同群的进度必须互不影响。
        """
        kinds = self.store.enabled_kinds_of_up(uid)
        if not kinds:
            return
        missing = self._missing_baselines(uid, kinds)
        if only_missing and not missing:
            return
        need = {bk for _, bk in missing} if only_missing else None
        up = self.store.get_up(uid)
        room_id = up.get("room_id") or 0

        def _g(kind, bl_kind):
            """订阅了该类型的群，且（必要时）确实缺基线。"""
            if kind not in kinds:
                return []
            try:
                gids = self.store.targets(uid, kind) or []
            except Exception:
                return []
            if need is None:
                return gids
            if bl_kind not in need:
                return []
            return [g for g in gids if (g, bl_kind) in missing]

        g_live = _g("live", "live")
        g_video = _g("video", "video")
        g_dyn = _g("dynamic", "dyn")

        if room_id and g_live:
            try:
                live = await self.bili.get_live(room_id)
                for gid in g_live:
                    self.store.set_baseline_of(
                        gid, uid, "live", status=live.live_status,
                        title=live.title)
            except BiliError as e:
                _log.warning(f"[UID {uid}] 初始化直播基线失败：{e}")
        if g_video:
            try:
                v = await self.bili.get_latest_video(uid)
                if v:
                    for gid in g_video:
                        self.store.set_baseline_of(
                            gid, uid, "video", bvid=v.bvid,
                            pubdate=getattr(v, "pubdate", 0) or 0)
            except BiliError as e:
                _log.warning(f"[UID {uid}] 初始化投稿基线失败：{e}")
        if g_dyn:
            try:
                d = await self.bili.get_latest_dynamic(uid)
                if d:
                    for gid in g_dyn:
                        self.store.set_baseline_of(
                            gid, uid, "dyn", id=d.dyn_id,
                            pubdate=getattr(d, "pubdate", 0) or 0)
            except BiliError as e:
                _log.warning(f"[UID {uid}] 初始化动态基线失败：{e}")
        if g_live or g_video or g_dyn:
            _log.info(f"[UID {uid}] 新订阅/新增类型：已静默对齐 "
                      f"{len(set(g_live) | set(g_video) | set(g_dyn))} 个群"
                      f"（不推送历史内容）")

    # ============ 轮询 ============
    async def _fetch_group_name(self, gid: str):
        """向官方查询群名并保存。

        官方接口 GET /v2/groups/{group_openid}/info 能返回 group_name，
        但它是「白名单接口」——未开通会返回 11253。
        拿不到就静默跳过，群名留空由用户手动备注，不刷错误日志。
        """
        try:
            info = await self.qq.get_group_info(gid)
        except Exception as e:
            _log.debug(f"查询群名失败（可忽略）：{redact(str(e))[:120]}")
            return ""
        if not info.get("ok"):
            code = info.get("code")
            # 11253 = 应用无接口访问权限（白名单），只在第一次提示一次
            if code == 11253 and gid not in self._group_name_denied:
                self._group_name_denied.add(gid)
                _log.info("官方「获取群信息」接口未开通（11253），"
                          "群名需手动备注；可在面板给群起个名字")
            elif not code:
                _log.debug("查询群名失败（可忽略）：{info.get('msg','')[:120]}")
            return ""
        name = (info.get("name") or "").strip()
        if name:
            self.store.set_group_name(gid, name)
            _log.info(f"已获取群名：{name}（{gid[:12]}…）")
        return name

    async def _reload_cookie(self):
        """面板扫码登录后 cookie 会变，这里让机器人不用重启就能用上新的。"""
        try:
            cfg = load_config(os.environ.get("BOT_CONFIG", "").strip()
                              or paths.config_path())
            new = str(cfg.get("bili_cookie") or "").strip()
        except Exception:
            return
        if new and new != self.bili.cookie:
            self.bili.cookie = new
            self.bili._buvid3 = ""      # 重新按新 cookie 取设备指纹
            self.bili._wbi_key = None
            _log.info("B 站 cookie 已更新，重新加载")
            heartbeat.touch(last_event="cookie 已更新")
            # ⚠️ 「存下来了」不等于「B站 认」。扫完码/粘完 cookie 立刻求证一次，
            #    否则面板显示已登录、接口却一直 -352/412，两边说法完全相反。
            acc = await self._bili_verify(new)
            if acc.get("ok"):
                _log.info("B 站登录态校验通过：%s（UID %s）",
                          acc.get("uname") or "", acc.get("mid") or "")
            elif acc.get("state") == "invalid":
                _log.warning("B 站 cookie 已保存，但 B站 说它无效（%s）。"
                             "接口仍会返回 -352/412，请重新扫码或粘贴完整 Cookie",
                             acc.get("msg") or "未知")
            # state=unknown（连不上 / 查询失败）不吓唬人，下一轮还会再验

    async def _bili_verify(self, cookie: str = "") -> dict:
        """向 B站 求证这段 cookie 到底算不算登录。

        面板以前只看「config 里有没有 SESSDATA」——那是本地判断，
        cookie 过期、被风控踢下线、粘了半串，它照样显示「已登录」。
        这里以 B站 自己的回答为准。
        """
        cookie = (cookie or getattr(self.bili, "cookie", "") or "").strip()
        if "SESSDATA=" not in cookie:
            return {"ok": False, "state": "none", "msg": "没有 cookie"}
        try:
            import bili_login
            acc = await bili_login.account_info(cookie)
        except Exception as e:
            return {"ok": False, "state": "unknown", "msg": f"查询异常：{e}"}
        if acc.get("ok"):
            return {"ok": True, "state": "logged", "msg": "",
                    "uname": acc.get("uname", ""), "mid": acc.get("mid", 0)}
        msg = str(acc.get("msg") or "")
        # 「cookie 无效或未登录」「未登录」这类是 B站 明确给了否定回答；
        # 网络错误 / 查询失败只是我们这边没问到，不能当成未登录。
        state = "invalid" if ("无效" in msg or "未登录" in msg
                              or "code=-101" in msg) else "unknown"
        return {"ok": False, "state": state, "msg": msg}

    def _log_check_fail(self, name: str, kind: str, err):
        """单个检测项失败怎么记：认证类降级，别每个 UP 每轮刷一条。

        B站 不认登录态时是**所有** UP 一起失败 —— 5 个 UP × 每 24 秒，
        日志被同一句话刷满，真正的推送记录反而被埋掉，
        用户还以为"每个 UP 各有各的问题"。根因由 _sync_bili_auth
        汇总成一条说清楚，这里就不再重复喊了。
        """
        try:
            bad = bool(self.bili.auth_bad())
        except Exception:
            bad = False
        msg = f"[{name}] {kind}获取失败：{err}"
        if bad:
            _log.debug(msg)
        else:
            _log.warning(msg)

    def _sync_bili_auth(self):
        """把「B站 认不认」写进心跳，并把刷屏告警汇总成一条。

        bilibili 里早就有 auth_bad / auth_reason，但**从来没人调用** ——
        于是机器人其实一直知道登录态失效，却从不告诉面板，
        面板照着 config 显示「已登录」。这就是"前后端说法相反"的根。
        """
        try:
            bad = bool(self.bili.auth_bad())
        except Exception:
            bad = False
        info = {"ok": (not bad), "at": time.time()}
        if bad:
            try:
                info["reason"] = self.bili.auth_reason() or ""
            except Exception:
                info["reason"] = ""
        try:
            heartbeat.touch(bili_login=info)
        except Exception:
            pass
        if not bad:
            self._auth_warn_at = 0.0
            return
        now = time.time()
        if now - (self._auth_warn_at or 0.0) < 600:
            return                       # 10 分钟内只报一次，别刷屏
        self._auth_warn_at = now
        _log.warning("B 站登录态没生效，投稿/动态接口都被拦（%s）。"
                     "面板显示「已登录」只代表本地存了 cookie，不代表 B站 认 —— "
                     "去「⚙️ 凭据 → B站登录」重新扫码，或重新粘贴完整 Cookie。",
                     info.get("reason") or "被风控/已过期")

    def _note(self, uid: int, kind: str, text: str):
        """记录某个 UP 某类检测的最近结果（成功值 / 失败原因）。

        以前这些失败只写日志，面板上看不到，
        用户只能看到"没提示"，完全不知道是接口挂了还是没开播。
        """
        try:
            st = heartbeat.read() or {}
            notes = st.get("notes") or {}
            notes[f"{uid}:{kind}"] = {
                "text": str(text)[:120],
                "ts": time.time(),
            }
            # 只保留最近 40 条，避免心跳文件无限增长
            if len(notes) > 40:
                keep = sorted(notes.items(),
                              key=lambda kv: kv[1].get("ts", 0))[-40:]
                notes = dict(keep)
            try:
                poll = self.interval_info()
            except Exception:
                poll = None           # 取不到间隔也要把 note 写下去
            heartbeat.touch(notes=notes, poll=poll)
        except Exception:
            pass

    async def _startup_sync(self) -> int:
        """启动时把每个已订阅 UP 主的当前状态全部记成基线。

        关键点：
        1. **只写基线，不推送** —— 重启期间的更新不该补推
        2. **强制刷新所有类型** —— 不看 slow_every，一次把
           直播 / 视频 / 动态 / 置顶动态 / 置顶评论 全部对齐
        3. 已有基线也重新拉一次，防止停机期间错过变化而被误判为"新内容"
        """
        self._silent = True
        try:
            return await self._do_startup_sync()
        finally:
            self._silent = False

    async def _fix_missing_rooms(self) -> int:
        """补查历史订阅里缺失的直播间号。

        早期版本只信 card 接口的 live_room，UP 没在播时那个字段是 null
        → 存成了 room_id=0 → 面板判定"没有直播间"→ 自动不勾「直播」
        → 直播提醒彻底失效。
        这里启动时把 room_id=0 的全部重新反查一遍并写回，
        老订阅不用手动重加。
        """
        fixed = 0
        try:
            uids = sorted(self.store.all_ups())
        except Exception:
            return 0
        for uid in uids:
            try:
                up = self.store.get_up(uid) or {}
                if int(up.get("room_id") or 0):
                    continue
                room, short_room = await self.bili.get_room_id_by_mid(uid)
                if not room:
                    got = await self.bili.get_up(uid)
                    room, short_room = got.room_id, got.short_room_id
                if room:
                    self.store.set_up_info(
                        uid, up.get("uname") or "", int(room),
                        up.get("face") or "", int(short_room or 0))
                    fixed += 1
                    _log.info(f"已补查到直播间号：{up.get('uname') or uid}"
                              f"（{room}），直播提醒可以正常触发了")
                    await asyncio.sleep(0.4)
            except Exception as e:
                _log.debug(f"补查直播间号 [{uid}] 跳过：{e}")
        if fixed:
            self.store.refresh()
        return fixed

    async def _do_startup_sync(self) -> int:
        # 先修历史数据：缺直播间号的补查回来（老订阅不用重加）
        try:
            n = await self._fix_missing_rooms()
            if n:
                _log.info(f"补查到 {n} 个缺失的直播间号，已写回数据库")
        except Exception as e:
            _log.debug(f"补查直播间号整体跳过：{e}")

        self.store.refresh()
        uids = sorted(self.store.all_ups())

        # ⚠️ 启动同步以前是**完全串行**的：每个 UP 依次跑 直播/投稿/动态
        #    三个请求，跑完再固定 sleep 0.4s。5 个 UP + 3 个合集就是 18 次
        #    串行请求，B站 一风控每次都要等满 10s 超时 —— 光这一步就要三
        #    分钟，日志停在"机器人已上线"不动，看着像卡死；而且**订阅越多
        #    越慢**，这正是"启动越来越慢"的另一半原因。
        #
        #    现在两步走：
        #    1) 并发（默认 3 个）—— 并发本身就是节流，不再逐个 sleep
        #    2) 总时限（默认 25s）—— 超预算就停，剩下的交给正常轮询补。
        #       轮询里本来就有静默对齐（_poll_once → _init_baseline），
        #       没对齐的 UP 第一轮会自己对齐，不会把历史内容当新内容推。
        try:
            conc = max(1, min(8, int(
                self.cfg.get("startup_sync_concurrency", 3) or 3)))
        except (TypeError, ValueError):
            conc = 3
        try:
            budget = max(5.0, float(
                self.cfg.get("startup_sync_budget", 25.0) or 25.0))
        except (TypeError, ValueError):
            budget = 25.0
        deadline = time.time() + budget
        sem = asyncio.Semaphore(conc)

        async def _sync_one(uid):
            async with sem:
                if time.time() > deadline:
                    return None              # 超预算，交给轮询补做
                try:
                    name = self.store.get_up(uid).get("uname") or str(uid)
                    kinds = self.store.enabled_kinds_of_up(uid)
                    if not kinds:
                        return 0
                    # 走正常检测流程，但因为基线为空/已对齐，不会产生推送
                    await self._check_live(uid, name)
                    await self._check_video(uid, name)
                    await self._check_dynamic(uid, name)
                    return 1
                except Exception as e:
                    _log.debug(f"启动同步 [{uid}] 跳过：{e}")
                    return 0

        done = 0
        skipped = 0
        if uids:
            res = await asyncio.gather(*[_sync_one(u) for u in uids])
            done = sum(1 for r in res if r == 1)
            skipped = sum(1 for r in res if r is None)

        # 合集也静默同步一遍基线，否则重启会把已有视频当成新增推一遍
        try:
            n_season = await self._startup_sync_seasons(deadline=deadline)
            if n_season:
                _log.info(f"启动同步：{n_season} 个合集的当前内容已记为基线（静默）")
        except Exception as e:
            _log.debug(f"合集启动同步跳过：{e}")

        if skipped:
            _log.info(f"启动同步：{skipped} 位 UP 主留到第一轮轮询再对齐"
                      f"（已用满 {budget:.0f}s 预算，不拖慢启动；轮询里会"
                      f"自动静默对齐，不会补推历史内容）")
        return done

    async def _startup_sync_seasons(self, deadline=None) -> int:
        """开机把每个订阅合集的现有视频记为基线。

        ⚠️ 必须在 _silent 期间做。不做的话重启一次就会把合集里
        已有的几十个视频当成"新增"全部推一遍 —— 那才是真骚扰。

        deadline：启动同步的总时限。超了就停，剩下的交给轮询补
        （轮询里 _poll_once 会先做静默对齐，不会误推历史内容）。
        """
        keys = self.store.all_season_keys()
        if not keys:
            return 0
        pairs = sorted({(int(u), int(sid)) for _, u, sid in keys})
        done = 0
        for uid, sid in pairs:
            if deadline is not None and time.time() > deadline:
                _log.info(f"启动同步：剩余 {len(pairs) - done} 个合集留到"
                          f"轮询再对齐（已用满启动预算，不拖慢启动）")
                break
            try:
                await self._check_one_season(uid, sid)
                done += 1
            except Exception as e:
                _log.debug(f"启动同步合集 [{sid}] 跳过：{e}")
            await asyncio.sleep(0.4)
        return done

    async def _poll_loop(self):
        """轮询主循环的外壳：只负责"循环不能死"。

        为什么要多包一层
        ----------------
        以前 while True 直接写在 _poll_loop 里，循环体内部虽然到处
        try/except，但只要漏掉一处（尤其是 BaseException 家族 ——
        asyncio.CancelledError 在 3.8 起**不再**继承 Exception，
        `except Exception` 根本抓不到它），整个协程就结束了：
        机器人还显示在线，却再也不检测，最后进程退出码 1。

        外壳负责兜住一切：真要取消（CancelledError）照常抛出，
        其它任何异常都记下完整堆栈、歇一会儿继续下一轮。
        """
        while True:
            try:
                await self._poll_loop_inner()
                return
            except asyncio.CancelledError:
                # 能到这儿的是"真要停"（close() 已置 _stopping），
                # 或者确实是取消本任务。记一笔再抛：以前一句话都没有，
                # 排查时看不出循环是正常停的还是被打断的。
                _log.error("轮询主循环被取消，正在退出")
                raise
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as e:
                heartbeat.bump("errors")
                try:
                    heartbeat.touch(last_error=redact(str(e))[:200])
                except Exception:
                    pass
                _log.error(f"轮询主循环异常（已自动重启循环）：{e}")
                for line in traceback.format_exc().strip().splitlines():
                    _log.error(f"  {line}")
                # _loop_delay 只为测试能快跑，默认 5s
                _d = getattr(self, "_loop_delay", 5)
                try:
                    await asyncio.sleep(_d)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await asyncio.sleep(_d)

    async def _poll_loop_inner(self):
        await asyncio.sleep(3)
        _log.info(
            f"监听已启动：当前每 {self.interval}s 一轮"
            f"（最快 {self.min_interval}s / 基线 {self.base_interval}s，"
            f"撞风控自动放慢，稳定后自动加快）")
        while True:
            try:
                await self._reload_cookie()
                # 面板改了「输出调试日志」后不用重启，这里每轮对一次
                try:
                    sync_log_level()
                except Exception:
                    pass
                # 面板改了「沙箱环境」后不用重启：换域名 + 重取 token
                try:
                    self.sync_sandbox()
                except Exception:
                    pass
                # 面板保存的其它配置（图片发送方式、提醒开关等）也热更新，
                # 不用重启机器人才生效
                try:
                    sync_cfg_from_disk(self)
                except Exception:
                    pass
                self._round += 1
                await self._poll_once()
                # 每轮把「B站 认不认这个登录态」写给面板（见 _sync_bili_auth）
                try:
                    self._sync_bili_auth()
                except Exception:
                    pass
            except asyncio.CancelledError:
                # ⚠️ CancelledError 有两种来源，必须分开：
                #   ① 真要停止（close() 里 cancel，会先置 _stopping）→ 照常抛出
                #   ② asyncio 内部泄漏出来的（超时/取消竞态；3.8 起它继承
                #      BaseException，`except Exception` 抓不到）
                #      → 以前一律 raise，整轮巡查就再也不跑了。
                #        这里记一笔，歇一下继续下一轮。
                if getattr(self, "_stopping", False):
                    raise
                _log.error("本轮收到取消信号（不是停止指令），跳过本轮继续")
                try:
                    await asyncio.sleep(getattr(self, "_loop_delay", 5))
                except asyncio.CancelledError:
                    raise
                continue
            except Exception as e:
                heartbeat.bump("errors")
                heartbeat.touch(last_error=redact(str(e))[:200])
                # ⚠️ 必须打完整堆栈。以前只打一句 `轮询异常：{e}`，
                # 像 'str' object has no attribute 'get' 这种错法根本
                # 定位不到是哪一位 UP 主、哪一步出的问题，
                # 只能靠猜。堆栈里带文件名和行号，一眼就能看到。
                _log.error(f"轮询异常：{e}")
                for line in traceback.format_exc().strip().splitlines():
                    _log.error(f"  {line}")

            # ⚠️ 以前这两句在 try 外面：一旦它们抛异常，
            # 整个 _poll_loop 协程就结束了——机器人还在线，
            # 但再也不检测，表现就是「不提醒了」。
            # _adapt_interval 调的是 bili 的方法，必须保护。
            try:
                self._adapt_interval()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _log.error(f"速率调整异常（已跳过本轮调整）：{e}")
                for line in traceback.format_exc().strip().splitlines():
                    _log.error(f"  {line}")

            # ⚠️ 每轮上报一次**实际**间隔。以前只在 _note() 里顺带写，
            #    而 _note 只在有检测结果时才调 —— 顶栏拿到的是上一次的值，
            #    撞风控放慢时顶栏还显示 8s，看着像速率调整没生效。
            try:
                # 每轮一次：既上报实际间隔，也把轮次累进去
                # （面板「运行状态」要显示真实跑了多少轮）
                heartbeat.bump("polls")
                heartbeat.touch(poll=self.interval_info())
            except Exception:
                pass

            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # sleep 出错不能让循环死掉，也不能变成空转
                _log.error(f"轮询休眠异常：{e}")
                await asyncio.sleep(5)

    def _adapt_interval(self):
        """贴着风控边缘跑：撞风控就退避，稳定之后逐步加快。

        目标是在不被风控的前提下尽可能快（准秒级）。
        所以不是固定间隔，而是：
          - 撞到风控 → 立刻翻倍退避（上限 max_interval）
          - 连续 N 轮平安 → 每轮回一点点，逐步逼近 min_interval
          - 长时间没风控（>5 分钟）→ 直接回到最快档
        """
        hits = getattr(self.bili, "risk_hits", 0) or 0
        since = getattr(self.bili, "since_risk", lambda: 1e9)() or 1e9

        # ⚠️ 下面几个状态都用 getattr 兜底：对象若不是走 __init__ 建出来的
        #    （测试桩、反序列化等）就没有这些属性，直接取会 AttributeError，
        #    把整轮巡查打断 —— 表现为"这一轮什么都没检查"。
        risk_seen = getattr(self, "_risk_seen", 0) or 0
        calm = getattr(self, "_calm_rounds", 0) or 0
        iv_last = getattr(self, "_iv_last_log", 0.0) or 0.0

        # ⚠️ 登录态失效期间**必须冻结加速**。
        #    -352/-403/HTTP 412 走的是 _note_auth，不计入 risk_hits，
        #    于是 since_risk 会一直涨：下面"超过 5 分钟没风控 → 回最快"
        #    就在这时候误判成"风控已解除"，一路把间隔压到最快档 ——
        #    每 8 秒撞一次墙，B站 风控更重、更难恢复，日志里还谎报
        #    「风控风险已解除」。这里先拦掉：不加速，也不无限退避
        #    （认证问题靠放慢救不了，得重新登录）。
        auth_bad = False
        try:
            auth_bad = bool(self.bili.auth_bad())
        except Exception:
            auth_bad = False
        if auth_bad:
            self._calm_rounds = 0
            # 退避到认证档，但不超过配置上限、也不快于基线
            cap = min(self.max_interval,
                      max(self.base_interval, AUTH_HOLD_INTERVAL))
            if self.interval < cap:
                old = self.interval
                self.interval = cap
                self._iv_from = None
                # 冷却：这段期间每轮都会走到这，只说一次
                if time.time() - iv_last >= 60:
                    self._iv_last_log = time.time()
                    _log.warning(
                        f"B 站登录态没生效（{self._auth_why()}），"
                        f"已暂停自动加速：{old}s → {self.interval}s。"
                        f"重新登录后会自动恢复，不用改配置")
            return

        if hits > risk_seen:
            # 新风控：退避
            self._risk_seen = hits
            self._calm_rounds = 0
            old = self.interval
            self.interval = min(self.max_interval, max(self.base_interval,
                                                        self.interval * 2))
            if self.interval != old:
                self._iv_from = None   # 退避是新一轮的起点
                # ⚠️ 说清楚是"被拦了"还是"对面超时了"：以前一律写
                #    "撞到 B 站风控"，但 -504 是对面没扛住、不是我们太猛，
                #    提示不准会让人白调配置。
                why = self._risk_why()
                # ⚠️ 冷却：连续撞风控时别每轮都刷一条 WARNING。
                #    60 秒内只留第一条，其余进 debug（默认不显示）。
                if time.time() - iv_last < 60:
                    _log.debug(
                        f"{why}，轮询间隔 {old}s → {self.interval}s")
                else:
                    self._iv_last_log = time.time()
                    _log.warning(
                        f"{why}，轮询间隔 {old}s → {self.interval}s"
                        f"（稳定后会自动加快）")
            return

        if since < 60:
            # 刚撞过，先别急着加速
            self._calm_rounds = 0
            return

        self._calm_rounds = calm + 1
        if since > 300 and self.interval > self.min_interval:
            # 超过 5 分钟没风控 → 直接回最快
            old = self.interval
            self.interval = self.min_interval
            self._iv_settled(old)
            return

        # 每 3 轮平安就回一档（-20%），逐步逼近最快
        if self._calm_rounds >= 3 and self.interval > self.min_interval:
            self._calm_rounds = 0
            old = self.interval
            self.interval = max(self.min_interval,
                                int(self.interval * 0.8))
            if self.interval != old:
                if self.interval > self.min_interval:
                    # ⚠️ 中间档只进 debug：从 24s 回到 8s 要连着回 4 档，
                    #    以前每档一条 INFO，翻译后还都是「检查频率自动调整
                    #    了」，日志页看着就是同一条消息连着出现好几次。
                    #    这里攒着起点，等真正回到最快档一起说。
                    if getattr(self, "_iv_from", None) is None:
                        self._iv_from = old
                    _log.debug(f"检查频率加快中：{old}s → {self.interval}s")
                else:
                    self._iv_settled(old)

    def _auth_why(self) -> str:
        """登录态为什么没生效，说成人话（给日志/面板看）。"""
        r = ""
        try:
            r = self.bili.auth_reason() or ""
        except Exception:
            r = ""
        if "-352" in r:
            return "接口要求登录态"
        if "-403" in r:
            return "访问权限不足"
        if "412" in r:
            return "请求被 WAF 拦下"
        return r or "接口要求登录态"

    def _risk_why(self) -> str:
        # 把最近一次风控的原因说成人话
        r = ""
        try:
            r = self.bili.risk_reason() or ""
        except Exception:
            r = ""
        if any(c in r for c in ("-504", "-500", "-502", "-503")):
            return "B 站接口超时/繁忙"
        if "-412" in r:
            return "请求被 B 站拦截（风控）"
        if r.startswith("HTTP"):
            return f"B 站返回 {r}（对面繁忙）"
        return "撞到 B 站风控（请求过频）"

    def _iv_settled(self, old):
        """回到最快档：把整段「逐步加快」合并成一条日志。

        只说起点和终点（24s → 8s），中间那些档位不再逐条刷。
        """
        f = getattr(self, "_iv_from", None)
        f = f if f is not None else old
        self._iv_from = None
        if f == self.interval:
            return
        now = time.time()
        # 冷却：刚打过就别再刷（比如反复在最快档边缘抖动）
        if now - (getattr(self, "_iv_last_log", 0.0) or 0.0) < 60:
            return
        self._iv_last_log = now
        _log.info(
            f"风控风险已解除，检查频率已回到最快：{f}s → {self.interval}s"
            f"（最快档）")

    def interval_info(self) -> dict:
        """给面板 / 状态指令看的轮询状态。"""
        try:
            auth_hold = bool(self.bili.auth_bad())
        except Exception:
            auth_hold = False
        return {
            "current": self.interval,
            "base": self.base_interval,
            "min": self.min_interval,
            "max": self.max_interval,
            "risk_hits": getattr(self.bili, "risk_hits", 0) or 0,
            "backing_off": self.interval > self.base_interval,
            # 登录态没生效 → 已冻结自动加速（面板要说清楚，别让人以为卡住）
            "auth_hold": auth_hold,
        }

    async def _poll_once(self):
        self.store.refresh()
        # 注意：slow_every<=1 表示"每轮都查"。
        # 不能只写 (round % slow_every == 1) —— 当 slow_every=1 时
        # 任何数 % 1 都等于 0，永远 != 1，投稿/动态就再也不会被检查了。
        slow_round = self.slow_every <= 1 or (self._round % self.slow_every == 1)
        # 合集检测**放在 UP 主循环之前**：合集里新增的视频往往同时也是
        # 最新投稿，如果先跑 video 就会同一条视频推两次（投稿提醒 +
        # 合集提醒）。先跑合集、并把结果记进基线，video 那边就能跳过。
        await self._check_seasons()

        for uid in sorted(self.store.all_ups()):
            kinds = self.store.enabled_kinds_of_up(uid)
            if not kinds:
                continue
            name = self.store.get_up(uid).get("uname") or str(uid)

            # 新订阅、或给已有 UP 主新开了某个类型：先静默对齐。
            # 放在检测之前，这样"对齐"和"正常工作"是分开的两步，
            # 不会把订阅之前就存在的内容当成新内容。
            try:
                if self._missing_baselines(uid, kinds):
                    await self._init_baseline(uid)
            except Exception as e:
                _log.warning(f"[{name}] 静默对齐失败：{redact(str(e))[:80]}")

            # ⚠️ 每一类检测都单独兜住异常。
            # 以前任何一个 UP 主的任何一类抛异常，都会一路冒到
            # _poll_loop，导致这一轮**剩下所有 UP 主全部不检测**——
            # 一个人出问题，所有人都收不到提醒，而且日志里看不出来。
            async def _safe(coro, label):
                try:
                    await coro
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    heartbeat.bump("errors")
                    self._note(uid, label, f"检测异常：{redact(str(e))[:60]}")
                    _log.error(f"[{name}] {label} 检测异常：{e}")
                    for ln in traceback.format_exc().strip().splitlines()[-5:]:
                        _log.error(f"  {ln}")

            if "live" in kinds:
                await _safe(self._check_live(uid, name), "live")
            if slow_round:
                if "video" in kinds:
                    await _safe(self._check_video(uid, name), "video")
                if "dynamic" in kinds:
                    await _safe(self._check_dynamic(uid, name), "dyn")
                # 置顶评论是独立开关，不能挂在 dynamic 下面
                if "top_comment" in kinds:
                    await _safe(self._check_top_comment(uid, name), "top_cmt")

    async def _check_seasons(self):
        """检查所有订阅的合集有没有新增视频。

        合集是 (群, UP主, 合集) 三元订阅，和 UP 主的直播/投稿/动态
        是**两套独立的东西**：同一个 UP 主可以订阅多个合集
        （录播一个、切片一个），每个合集的文案也可以不同。

        ⚠️ 多个群订阅同一个合集时只查**一次**接口（按键去重），
        再逐个群判断要不要推 —— 否则每多一个群就多一倍请求。

        ⚠️ 合集检测比直播慢、比投稿也不是很急，所以只在慢速轮次跑，
        并且间隔再翻倍，省请求。
        """
        if self.slow_every > 1 and (self._round % (self.slow_every * 2) not in (1,)):
            return
        try:
            keys = self.store.all_season_keys()
        except Exception as e:
            _log.debug(f"读取合集订阅失败：{e}")
            return
        if not keys:
            return
        # 按 (uid, season_id) 去重，共用一个查询结果
        pairs = sorted({(int(u), int(sid)) for _, u, sid in keys})
        for uid, sid in pairs:
            # ⚠️ 连续临时失败的合集，先歇几轮再查。
            #    一个合集一轮要打 3~7 次请求（新接口 + 旧接口降级 + 翻页
            #    + 小节），B 站一超时就连着失败，继续每轮猛打只会让它更
            #    超时，还会顺带把整轮拖成风控（轮询间隔被顶到 24s）。
            # ⚠️ 这几个状态都用 getattr 兜底：对象若不是走 __init__ 建出来的
            #    （测试桩等）就没有这些属性，直接取会 AttributeError 把整轮
            #    巡查打断 —— 表现为"这一轮什么都没检查"。
            skipmap = getattr(self, "_season_skip", None)
            if skipmap is None:
                skipmap = {}
                self._season_skip = skipmap
            skip = skipmap.get((uid, sid), 0)
            if skip > 0:
                skipmap[(uid, sid)] = skip - 1
                continue
            try:
                await self._check_one_season(uid, sid)
                self._season_fail.pop((uid, sid), None)
                skipmap.pop((uid, sid), None)
            except BiliTransientError as e:
                # 临时故障：不是合集没了，别劝用户去删订阅。
                # 退避：连续失败 1、2、4、8… 轮后再试（上限 8 轮）。
                n = self._season_fail.get((uid, sid), 0) + 1
                self._season_fail[(uid, sid)] = n
                skipmap[(uid, sid)] = min(8, 2 ** (n - 1))
                # 日志也别每轮刷：第 1 次说清楚，之后攒着每隔一段说一次
                if n <= 2 or n % 10 == 0:
                    _log.warning(
                        f"合集 {sid}（UP {uid}）暂时取不到（第 {n} 次）："
                        f"{redact(str(e))[:120]} —— B 站接口超时/繁忙，"
                        f"已自动放慢重试，不是订阅出问题，不用去删它")
            except SeasonGoneError as e:
                # 合集没了（被删 / ID 填错），重试也不会有。
                # 连续几次都这样就别每轮刷日志了，提示去面板处理。
                n = self._season_fail.get((uid, sid), 0) + 1
                self._season_fail[(uid, sid)] = n
                if n <= 3 or n % 20 == 0:
                    _log.warning(f"合集 {sid}（UP {uid}）取不到："
                                 f"{redact(str(e))[:160]}")
            except Exception as e:
                _log.warning(f"合集 {sid} 检测失败：{redact(str(e))[:120]}")

    async def _check_one_season(self, uid: int, season_id: int):
        max_pages = max(1, int(self.cfg.get("season_max_pages", 2)))
        # 拿不到就让它抛出去（上层会记日志），**不写基线** ——
        # 否则一次网络抖动就会把"当前有哪些视频"记歪，下次真新增也推不出来。
        info = await self.bili.get_season_archives(uid, season_id,
                                                   max_pages=max_pages)
        if not info or not info.videos:
            return
        # 这个合集被哪些群订阅了（且开着）
        gids = [g for g, u, sid in self.store.all_season_keys()
                if int(u) == int(uid) and int(sid) == int(season_id)]
        if not gids:
            return
        if info.title:
            for g in gids:
                try:
                    self.store.set_season_title(g, uid, season_id, info.title)
                except Exception:
                    pass
        # 封面：面板里拿它当合集头像，裁成 1:1 显示。只在还没记过时写。
        if getattr(info, "cover", ""):
            for g in gids:
                try:
                    self.store.set_season_cover(g, uid, season_id, info.cover)
                except Exception:
                    pass

        name = self.store.get_up(uid).get("uname") or str(uid)
        # ⚠️ 小节感知（每个合集只做一次，不按群重复请求）
        await self._season_apply_sections(info, season_id)
        for gid in gids:
            try:
                await self._season_sync_group(
                    gid, uid, season_id, name, info)
            except Exception as e:
                _log.warning(f"群 {gid[:8]}… 合集 {season_id} 处理失败："
                             f"{redact(str(e))[:120]}")

    async def _season_apply_sections(self, info, season_id: int):
        """把合集的小节结构并进 info：补齐漏掉的视频 + 记下每条属于哪节。

        ⚠️ 为什么要它：合集**分了小节**时，列表接口给的是扁平数组，UP 主
        往某个小节里加视频会让整个顺序按小节重排，"最新那条"不再落在
        列表的头或尾，分页也很可能翻不到它 —— 只看扁平数组必然错位。
        而详情接口能**一次**给出整个合集的完整小节结构（不分页），
        用它既补齐遗漏，又顺带知道了每条视频的小节名。

        ⚠️ 只在**真的分了小节**时才生效：只有一个小节（B站默认给个
        "正片"）等于没分节，那时什么都不补，也不显示"小节"这一行。

        结果挂在 info.sec_map（{bvid: 小节名}），取不到或没分节就是 {}。
        """
        info.sec_map = {}
        vids = list(getattr(info, "videos", None) or [])
        if not vids:
            return
        hint = max(vids, key=lambda v: int(getattr(v, "pubdate", 0) or 0))
        if not getattr(hint, "bvid", ""):
            return
        # ⚠️ 整段都兜住：小节只是"锦上添花"，取不到就退回不分节的老行为，
        #    绝不能因为它把整个合集检测搞挂（那样会连普通更新都推不出来）。
        try:
            secs = await self.bili.get_season_sections(hint.bvid,
                                                       int(season_id))
            m = self.bili.section_map(secs) if secs else {}
        except Exception as e:
            _log.debug(f"合集 {season_id} 小节结构取不到："
                       f"{redact(str(e))[:80]}")
            return
        if not m:
            return                       # 只有一个小节 = 没分节
        info.sec_map = m
        # 小节里出现了、但扁平列表没给到的视频 —— 补进去参与比对，
        # 否则往小节里加的新视频会被漏掉（这正是顺序错位的后果）。
        have = {v.bvid for v in vids if getattr(v, "bvid", "")}
        for s in secs:
            for ep in s.get("episodes") or []:
                b = ep.get("bvid") or ""
                if not b or b in have:
                    continue
                have.add(b)
                vids.append(SeasonVideo(
                    bvid=b, title=ep.get("title") or "",
                    pic=ep.get("pic") or "",
                    pubdate=int(ep.get("pubdate") or 0)))
        info.videos = vids

    async def _season_sync_group(self, gid: str, uid: int, season_id: int,
                                 name: str, info):
        """对**单个群**比对合集基线并推送。

        基线是**按群**存的：A 群早就订阅、B 群刚订阅，
        两者进度不同，不该互相影响。
        """
        known = self.store.get_season_known(gid, uid, season_id)
        known_set = {str(b) for b in known}
        fresh = [v for v in info.videos if v.bvid and v.bvid not in known_set]

        # ⚠️ 必须在**写基线之前**取：写完 updated_at 就变成现在了，
        # 那时再读就永远等于"刚刚"，这道判断会直接失效。
        try:
            last_seen = self.store.get_season_updated_at(gid, uid, season_id)
        except Exception:
            last_seen = 0

        # ⚠️ 静默期/首次订阅时**才**一次性把整批写进基线（下面那种
        #    "只写推送过的"策略不适合这里：那样会把几十个历史视频
        #    分批推出来）。正常工作时基线在**推送之后**按需写，
        #    见下面"只把这次真的推掉的写进基线"。
        if getattr(self, "_silent", False) or not known:
            if fresh:
                try:
                    self.store.set_season_known(
                        gid, uid, season_id,
                        list(known) + [v.bvid for v in fresh if v.bvid])
                except Exception:
                    pass
                if not known:
                    _log.info(f"合集 {info.title or season_id} 已记基线 "
                              f"{len(info.videos)} 个视频（静默，不推送）")
            return
        if not fresh:
            return

        # ⚠️ 升级保护：分页修好之前，合集基线可能只记了最老那几十个视频，
        # 修好后会突然扫出一大堆"没见过但其实早就存在"的视频。
        # 不挡一下的话，升级后会一口气把它们当新视频推十几轮。
        # 判据：发布时间早于"上次写基线" → 说明我们上次看的时候它就在了，
        # 只是以前翻不到，不算新。pubdate 取不到时不拦（宁可多推，别漏）。
        #
        # ⚠️ 必须留容差：last_seen 是我们"写基线"的时刻，而视频可能在
        #    那之前就已经发布了。没有容差的话，刚发布就被这一轮抓到的
        #    视频反而会被判成旧内容 → **漏推**。
        #
        # ⚠️⚠️ 容差不能只吸收"轮询间隔"：B 站合集列表的索引有延迟，
        #    视频发布/加入合集后，列表接口往往要过几分钟甚至更久才看得到。
        #    那段时间里我们照常跑、照常写基线（updated_at 一直往前走），
        #    等视频真正出现时 pubdate 已经早于 last_seen 好几分钟 ——
        #    容差只有 300 秒的话，整条都被判成"旧内容"静默掉，
        #    表现就是**合集更新了但永远不推送**。默认给 3600 秒。
        #    升级保护照样成立：那时补出来的是几个月前的老视频，
        #    3600 秒也远远拦得住。
        grace = float(self.cfg.get("season_fresh_grace",
                                   SEASON_FRESH_GRACE) or 0)
        dropped = []
        if last_seen:
            kept = [v for v in fresh
                    if not v.pubdate or (v.pubdate + grace) > last_seen]
            dropped = [v for v in fresh if v not in kept]
            if dropped:
                _log.info(f"合集 {info.title or season_id}：{len(dropped)} 个视频"
                          f"发布时间早于上次检测，只补基线不推送")
            fresh = kept
            if not fresh:
                return

        # 一次最多推 3 个，避免 UP 主批量传视频时刷屏
        limit = max(1, int(self.cfg.get("season_push_limit", 3)))
        # ⚠️ 推**最新**的那几条：批量补录时用户想先看到的是新内容，
        #    升序会把最老的先推、最新的反而排到后面。
        todo = sorted(fresh, key=lambda x: x.pubdate, reverse=True)[:limit]
        # 本群也收投稿、且这条刚作为「新投稿」推过 → 同一件事，别推两遍。
        # （合集先被检测到时，投稿那边由 _video_covered_by_season 跳过）
        todo = [v for v in todo
                if not self._should_silence_season(gid, uid, v)]
        todo = [v for v in todo if v.bvid]
        if not todo:
            # 全被静默了：写基线（下一轮不再算新）+ 结束
            try:
                merged = list(known) + [v.bvid for v in fresh if v.bvid]
                self.store.set_season_known(gid, uid, season_id, merged)
            except Exception:
                pass
            return

        # ⚠️ 只把**这次真的推掉/判定掉**的写进基线。
        #    以前把整批 fresh 一次写全，于是超出 limit 的那几条
        #    稀里糊涂进了基线、下一轮不再算新 —— UP 主一次加 5 个视频、
        #    limit 是 3，那 2 个就**永远推不出来**。
        #    现在留着，下一轮接着推。
        try:
            merged = (list(known)
                      + [v.bvid for v in dropped if v.bvid]
                      + [v.bvid for v in todo])
            self.store.set_season_known(gid, uid, season_id, merged)
        except Exception:
            pass

        # ⚠️ 小节名在 _season_apply_sections 里一次查全了，
        #    别对每条视频各查一次详情接口（请求量会翻好几倍）。
        sec_map = getattr(info, "sec_map", None) or {}
        for v in todo:
            section = (sec_map or {}).get(v.bvid, "")
            push = SeasonPush(bvid=v.bvid, title=v.title, pic=v.pic,
                              season_title=info.title, section=section)
            await self._push_season(gid, uid, season_id, name, push)

    async def _push_season(self, gid: str, uid: int, season_id: int,
                           name: str, push):
        """推送一条合集更新。文案走四层回落：默认→全局→群→合集。"""
        if getattr(self, "_silent", False):
            return
        if self._noperm_active(gid):
            _log.debug(f"群 {gid[:8]}… 主动消息无权限，冷却中，本轮跳过合集")
            return
        try:
            tpls = self._tpls_for(gid, season_key=(uid, season_id))
            md, buttons = render("season", push, name, tpls)
        except Exception as e:
            _log.error(f"合集消息渲染失败：{e}")
            return
        plain = to_plain(md) or f"合集更新：{push.title}"
        style = str(self.cfg.get("message_style", "markdown")).lower()
        try:
            if style == "text":
                await self._push_plain(gid, plain, plain)
            else:
                try:
                    await self.qq.send_markdown(
                        gid, md, keyboard=build_keyboard(buttons))
                except Exception as e_md:
                    # 同上：无权限时退纯文本也是同一个错，别白试一次
                    if is_no_perm_error(e_md):
                        self._mark_no_perm(gid)
                        _log.warning(no_perm_hint(gid))
                        return
                    _log.warning(f"合集 Markdown 推送失败，改用纯文本："
                                 f"{redact(str(e_md))[:80]}")
                    await self._push_plain(gid, plain, plain)
            heartbeat.bump("pushes")
            # 合集走的是独立的推送函数（不走 _push），
            # 不记的话概况页「最近动态」里永远没有合集更新。
            try:
                heartbeat.push_event("season", uid, name, 1)
            except Exception:
                pass
            # 合集推过的视频，投稿动态别再推一遍（同一件事）
            # ⚠️ 记成 "season" 而不是 "video"：投稿那边的去重要能分清
            #    "这条是作为投稿推的"还是"只是某个群推了合集"，
            #    混在 video 里会让只订投稿的群被别的群的合集推送静默掉。
            try:
                self._remember_pushed(uid, "season", push)
            except Exception:
                pass
            heartbeat.touch(last_push_at=time.time(),
                            last_push=f"合集 · {push.title}")
            _log.info(f"已推送【合集】{push.title} → 群 {gid[:8]}…")
        except Exception as e:
            heartbeat.bump("errors")
            _log.warning(f"合集推送到群 {gid[:8]}… 失败：{redact(str(e))[:120]}")

    async def _check_live(self, uid: int, name: str):
        up = self.store.get_up(uid)
        room_id = up.get("room_id") or 0
        if not room_id:
            # get_up 的 live_room 有时不给 roomid。这种情况以前是
            # 直接 return —— 直播提醒永远不触发，而且日志里什么都没有，
            # 用户只会觉得"开了直播却没提示"。这里补一次反查并落库。
            room_id, short_room = await self.bili.get_room_id_by_mid(uid)
            if room_id:
                self.store.set_up_info(uid, up.get("uname") or "", room_id,
                                       "", int(short_room or 0))
                _log.info(f"[{name}] 已补到直播间号 {room_id}"
                          + (f"（短号 {short_room}）" if short_room else ""))
            else:
                self._note(uid, "live", "没查到直播间号，直播提醒不会触发")
                return
        try:
            live = await self.bili.get_live(room_id)
        except BiliError as e:
            # 记下来，面板能看到（以前只打日志，用户根本不知道失败了）
            self._note(uid, "live", f"获取失败：{e}")
            self._log_check_fail(name, "直播状态", e)
            return
        except Exception as e:
            # ⚠️ 非 BiliError 也不能让它冒出去：以前只 catch BiliError，
            #    于是解析类异常（如 live_time 是 "0000-00-00 00:00:00"
            #    这种字符串、int() 直接 ValueError）会一路冒到轮询兜底，
            #    表现为"直播检测每轮都失败、开播下播一条都不推，还不报错"。
            #    这里单独接住并写进面板「最近检测结果」，看得见才好修。
            self._note(uid, "live", f"检测异常：{type(e).__name__}: {e}")
            _log.warning(f"[{name}] 直播状态检测异常："
                         f"{type(e).__name__}: {e}")
            return
        self._note(uid, "live", f"live_status={live.live_status}")
        # 记开播时刻：下播时算时长用。
        # ⚠️ 重启后补记也走这里 —— 接口给的 live_time 是真实开播时间，
        #    比"进程启动时间"准得多（机器人中途重启也能算出正确时长）。
        _since = self._live_since_map()
        if live.is_live and int(uid) not in _since:
            _since[int(uid)] = int(
                getattr(live, "live_time", 0) or 0) or int(time.time())
        # 基线**按群隔离**：新订阅的群只静默对齐，不补推订阅前的状态。
        # 以前是全站共用一份基线，B 群刚订阅时会继承 A 群的进度，
        # 结果 B 群一订阅就收到"下播提醒"（它从没收到过开播）。
        try:
            gids = self.store.targets(uid, "live") or []
        except Exception:
            gids = []
        to_open, to_close, skip = [], [], set()
        for gid in gids:
            base = self.store.baseline_of(gid, uid, "live")
            prev = base.get("status")
            # 不管推不推都先把基线对齐，避免下次重复判定
            self.store.set_baseline_of(gid, uid, "live",
                                       status=live.live_status,
                                       title=live.title)
            if prev is None:
                skip.add(gid)          # 首次：静默对齐，不推送
                _log.debug(f"[{name}] 群 {gid[:8]}… 直播基线已建立"
                           f"（静默，不推送）")
                continue
            if prev != 1 and live.live_status == 1:
                # 反向去重：开播动态**先**被检测到并推过了，
                # 这是同一件事，别再推一条开播提醒。
                try:
                    if (self._gid_also_gets_dynamic(uid, gid)
                            and self._content_pushed_by(uid, "room",
                                                        str(room_id),
                                                        ("dynamic",))):
                        skip.add(gid)
                        _log.info(f"[{name}] 群 {gid[:8]}… 开播已由开播动态"
                                  f"提醒过，静默不重复推送")
                        continue
                except Exception:
                    pass
                to_open.append(gid)
            elif prev == 1 and live.live_status != 1:
                if self.notify_offline:
                    to_close.append(gid)
                else:
                    skip.add(gid)
            else:
                skip.add(gid)
        # exclude 表达"只有这些群要推"：把不需要推的群全部排除掉。
        # （_push 只有 exclude 参数，这里不新增 only，避免改签名影响别处）
        if to_open:
            await self._push("live", uid, name, live,
                             exclude=set(gids) - set(to_open))
        if to_close:
            # 本场直播时长 = 现在 - 开播时刻。算不出来（比如重启前就在播
            # 且接口没给 live_time）就留 0，卡片不显示这一行，不瞎编。
            started = int(_since.get(int(uid), 0) or 0)
            if started:
                try:
                    live.duration = max(0, int(time.time()) - started)
                except Exception:
                    live.duration = 0
            _since.pop(int(uid), None)
            await self._push("offline", uid, name, live,
                             exclude=set(gids) - set(to_close))

    def _video_covered_by_season(self, gid: str, uid: int, bvid: str) -> bool:
        """这个视频是不是已经由「合集更新」负责推送了。

        ⚠️ 合集和投稿是两套独立订阅，但**同一条视频可能同时命中两者**：
        UP 主传了个新视频、同时它属于某个已订阅的合集。
        那样会收到两条提醒（投稿 + 合集），属于叠加消息。

        规则：该视频在本群某个已开启的合集里 → 只发合集提醒，
        投稿提醒在这个群里跳过。
        """
        if not bvid:
            return False
        try:
            seasons = self.store.seasons_of_group(gid) or []
        except Exception:
            return False
        for s in seasons or []:
            try:
                if int(s.get("uid") or 0) != int(uid):
                    continue
                if not s.get("on", True):
                    continue
                known = self.store.get_season_known(
                    gid, uid, int(s.get("season_id") or 0)) or []
            except Exception:
                continue
            if str(bvid) in {str(x) for x in known}:
                return True
        return False

    async def _check_video(self, uid: int, name: str):
        try:
            v = await self.bili.get_latest_video(uid)
        except BiliError as e:
            self._note(uid, "video", f"获取失败：{e}")
            self._log_check_fail(name, "投稿", e)
            return
        if not v or not v.bvid:
            self._note(uid, "video", "没取到投稿")
            return
        self._note(uid, "video", v.bvid)
        # 基线**按群隔离**：刚订阅的群只静默对齐，不补推订阅前就存在的投稿。
        # 以前全站共用一份基线，旧群取消订阅后基线停更，新群再订阅时
        # 会拿过期基线判定出"新投稿"，于是新群一订阅就收到历史投稿。
        try:
            gids = self.store.targets(uid, "video") or []
        except Exception:
            gids = []
        skip = set()
        changed = []
        for gid in gids:
            base = self.store.baseline_of(gid, uid, "video")
            prev = base.get("bvid")
            prev_pub = base.get("pubdate") or 0
            self.store.set_baseline_of(
                gid, uid, "video", bvid=v.bvid,
                pubdate=self._wm_pub(prev_pub,
                                     getattr(v, "pubdate", 0) or 0))
            if not prev:
                skip.add(gid)          # 首次：静默对齐，不推送
                _log.debug(f"[{name}] 群 {gid[:8]}… 投稿基线已建立"
                           f"（静默，不推送）")
            elif prev == v.bvid:
                skip.add(gid)          # 无变化
            elif not self._is_newer_content(getattr(v, "pubdate", 0),
                                            prev_pub):
                # 删稿/撤稿后"最新投稿"退回成更早的一条：
                # ID 变了但不是新内容，只更新基线，不推送。
                skip.add(gid)
                _log.info(f"[{name}] 群 {gid[:8]}… 最新投稿回退为更早的 "
                          f"{v.bvid}（疑似删稿），静默不推送")
            # 这个视频如果已经在某个合集里推过，就别再当"新投稿"推一遍。
            # 按群判断：没订阅那个合集的群照样要收到投稿提醒。
            try:
                if self._video_covered_by_season(gid, uid, v.bvid):
                    skip.add(gid)
            except Exception:
                pass
            # 反向去重：投稿动态**先**被检测到并推过了（动态接口通常比
            # 投稿接口快），这条视频就是同一件事，别再推一遍。
            # ⚠️ 只在这个群也收动态时才成立 —— 只订投稿的群收不到那条
            # 动态，一并静默它就什么都收不到了。
            try:
                if self._gid_also_gets_dynamic(uid, gid):
                    if self._content_pushed_by(uid, "video", v.bvid,
                                               ("dynamic",)):
                        skip.add(gid)
                        _log.info(f"[{name}] 群 {gid[:8]}… 新投稿 {v.bvid} 已由"
                                  f"投稿动态提醒过，静默不重复推送")
                    elif self._should_silence_video(uid, v):
                        # 动态里取不到 BV（新版投稿动态把地址藏在跳转链接
                        # 里）→ 按发布时间兜底，同样是刚推过的那条动态
                        skip.add(gid)
                        _log.info(f"[{name}] 群 {gid[:8]}… 新投稿 {v.bvid} 与"
                                  f"刚推过的动态是同一件事，静默不重复推送")
            except Exception:
                pass
        changed = [g for g in gids if g not in skip]
        if changed:
            _n_skip = len(gids) - len(changed)
            if _n_skip:
                _log.info(f"[{name}] 新投稿 {v.bvid}：{len(changed)} 个群推送，"
                          f"{_n_skip} 个群跳过（未对齐 / 无变化 / 已由合集推送）")
            await self._push("video", uid, name, v, exclude=skip)

    def _dyn_notified(self, gid: str, uid: int, dyn_id: str) -> bool:
        """这个群是不是已经为**这条动态 ID** 提醒过了。

        ⚠️ 为什么不能拿 `dyn` / `dyn_top` 基线当"已提醒"用：
        基线在**首次静默对齐**时也会写成当前那条动态，而那一刻群里
        什么都没收到。拿基线比对会把订阅前就存在的内容永久吞掉 ——
        订阅后一条动态都收不到。所以必须单独记"真的推过"的那一条。

        ⚠️ 必须**按群**记：A 群收过不代表 B 群收过，
        后订阅的群照样要推一遍。
        """
        if not gid or not dyn_id:
            return False
        try:
            seen = self.store.baseline_of(gid, uid, "dyn_seen") or {}
        except Exception:
            return False        # 读不出来就按"没提醒过"处理，宁可重复推
        if str(seen.get("id") or "") == str(dyn_id):
            return True
        # ⚠️ 必须查**一串**而不只是最后一条：
        # 推 A → 推 D（只记一条的话 A 就被顶掉了）→ UP 把 A 置顶 →
        # 查不到 A 又推一遍。所以这里整串都认。
        return str(dyn_id) in {str(x) for x in (seen.get("ids") or [])}

    def _mark_dyn_notified(self, gid: str, uid: int, dyn_id: str,
                           pubdate: int = 0):
        """记下"这个群已经为这条动态提醒过了"。"""
        if not gid or not dyn_id:
            return
        try:
            seen = self.store.baseline_of(gid, uid, "dyn_seen") or {}
        except Exception:
            seen = {}
        # 累积成一串（最新的在最后），老的在前；超出上限就丢最早的
        ids = [str(x) for x in (seen.get("ids") or []) if x]
        old = str(seen.get("id") or "")
        if old and old not in ids:
            ids.append(old)
        cur = str(dyn_id)
        if cur in ids:
            ids.remove(cur)
        ids.append(cur)
        try:
            keep = int(DYN_SEEN_KEEP or 20)
        except Exception:
            keep = 20
        try:
            self.store.set_baseline_of(gid, uid, "dyn_seen", id=dyn_id,
                                       pubdate=int(pubdate or 0),
                                       ids=ids[-keep:])
        except Exception:
            pass

    async def _check_dynamic(self, uid: int, name: str):
        """检查动态：普通动态 + 置顶动态 + 置顶动态的置顶评论。

        三者是独立的基线。同一条 ID 如果既是置顶又是最新，
        只提醒一次（按优先级：置顶 > 普通），不重复刷屏。
        """
        pushed_ids = set()          # 本轮已推过的 dyn_id，避免同一条推两次

        # ---- 0) 先取「最新非置顶动态」----
        # 为什么必须先取它：置顶接口在**认不出置顶标记**时会退而返回列表
        # 第一条（也就是最新动态）。于是同一条动态会同时落进两个分支 ——
        # 一次「📌 置顶动态」、一次「📝 新动态」，群里看着就是同一条来了
        # 两次，第二次还换了个「置顶」的帽子。先拿到它才能和置顶比对
        # id：同一条就只走一次普通推送。
        try:
            d_latest = await self.bili.get_latest_dynamic(uid)
        except BiliError as e:
            d_latest = None
            self._note(uid, "dyn", f"获取失败：{e}")
            self._log_check_fail(name, "动态", e)
        latest_id = str(getattr(d_latest, "dyn_id", "") or "")

        # ---- 1) 置顶动态（优先级更高，先处理）----
        try:
            top = await self.bili.get_pinned_dynamic(uid)
        except BiliError as e:
            top = None
            self._note(uid, "dyn_top", f"获取失败：{e}")
        if top and top.dyn_id and top.dyn_id != latest_id:
            self._note(uid, "dyn_top", top.dyn_id)
            # 按群隔离基线：刚订阅的群只静默对齐
            try:
                g_top = self.store.targets(uid, "dynamic") or []
            except Exception:
                g_top = []
            skip_top = set()
            for gid in g_top:
                base = self.store.baseline_of(gid, uid, "dyn_top")
                prev = base.get("id")
                prev_pub = base.get("pubdate") or 0
                self.store.set_baseline_of(
                    gid, uid, "dyn_top", id=top.dyn_id,
                    pubdate=self._wm_pub(prev_pub,
                                         getattr(top, "pubdate", 0) or 0))
                if not prev:
                    skip_top.add(gid)      # 首次：静默对齐，不推送
                    _log.debug(f"[{name}] 群 {gid[:8]}… 置顶动态基线已建立"
                               f"（静默，不推送）")
                elif prev == top.dyn_id:
                    skip_top.add(gid)
                elif self._dyn_notified(gid, uid, top.dyn_id):
                    # UP 把刚发不久的动态置顶了：这条已经作为「📝 新动态」
                    # 提醒过，不能因为换了个「置顶」的帽子就再推一遍。
                    # ⚠️ 必须排在「时间是否更新」**之前**：部分接口不给置顶
                    # 那条的时间（pubdate=0），按时间比会先命中"回退为更早的"
                    # 分支，日志写成"回退为更早的"，把真实原因（早就提醒过）
                    # 盖掉，排查时会被误导。
                    skip_top.add(gid)
                    _log.info(f"[{name}] 群 {gid[:8]}… 置顶动态 {top.dyn_id} "
                              f"已作为新动态提醒过，静默不推送")
                elif self._should_silence_dynamic(uid, top):
                    skip_top.add(gid)
                    _log.debug(f"[{name}] 置顶动态是开播/投稿的重复动态，已静默")
                elif not self._is_newer_content(getattr(top, "pubdate", 0),
                                                prev_pub):
                    # 置顶被取消/删除后指向了更早的一条，不是新内容
                    skip_top.add(gid)
                    # 记进「已提醒」：取消置顶/恢复置顶来回切时，
                    # 不该因为 ID 重新出现就再推一遍。
                    self._mark_dyn_notified(
                        gid, uid, top.dyn_id, getattr(top, "pubdate", 0) or 0)
                    _log.info(f"[{name}] 群 {gid[:8]}… 置顶动态回退为更早的 "
                              f"{top.dyn_id}，静默不推送（基线时间不回退）")
            todo = [g for g in g_top if g not in skip_top]
            if todo:
                pushed_ids.add(top.dyn_id)
                for gid in todo:
                    self._mark_dyn_notified(
                        gid, uid, top.dyn_id, getattr(top, "pubdate", 0) or 0)
                await self._push("dynamic", uid, name, top, exclude=skip_top)

            # 置顶评论**不在这里**调 —— 以前嵌套在这里，
            # 置顶动态没识别出来就一次都不会查。现由 _poll_once 独立调度。
        elif top and top.dyn_id:
            self._note(uid, "dyn_top", "与最新动态同一条，按新动态推送")

        # ---- 3) 普通（最新非置顶）动态 ----
        d = d_latest        # 上面已经取过，不重复请求
        if not d or not d.dyn_id:
            self._note(uid, "dyn", "没取到动态")
            return
        # 与置顶同一条时不重复提醒（置顶优先级更高，已在上面推过）
        if d.dyn_id in pushed_ids:
            self._note(uid, "dyn", "与置顶同一条，已提醒，跳过")
            return
        self._note(uid, "dyn", d.dyn_id)
        # 按群隔离基线：刚订阅的群只静默对齐，
        # 不补推它订阅之前就存在的动态。
        try:
            gids = self.store.targets(uid, "dynamic") or []
        except Exception:
            gids = []
        skip = set()
        for gid in gids:
            base = self.store.baseline_of(gid, uid, "dyn")
            prev = base.get("id")
            prev_pub = base.get("pubdate") or 0
            self.store.set_baseline_of(
                gid, uid, "dyn", id=d.dyn_id,
                pubdate=self._wm_pub(prev_pub, getattr(d, "pubdate", 0) or 0))
            if not prev:
                skip.add(gid)          # 首次：静默对齐，不推送
                _log.debug(f"[{name}] 群 {gid[:8]}… 动态基线已建立"
                           f"（静默，不推送）")
            elif prev == d.dyn_id:
                skip.add(gid)
            elif self._dyn_notified(gid, uid, d.dyn_id):
                # 这条之前是置顶、已经按「📌 置顶动态」提醒过；
                # UP 取消置顶后它变成"最新非置顶"，不能再补推一遍。
                skip.add(gid)
                _log.info(f"[{name}] 群 {gid[:8]}… 动态 {d.dyn_id} "
                          f"已作为置顶动态提醒过，静默不推送")
            elif not self._is_newer_content(getattr(d, "pubdate", 0),
                                            prev_pub):
                # UP 删掉最新那条后，"最新动态"退回成更早的一条。
                # ID 变了但时间是旧的 → 旧动态，只更新基线不推送。
                skip.add(gid)
                # ⚠️ 必须记进「已提醒」：删掉的那条如果又恢复（或接口
                # 在两个 ID 之间来回跳），水位线已经保住了不回退，这里
                # 再按 ID 兜一道，双重保险。
                self._mark_dyn_notified(
                    gid, uid, d.dyn_id, getattr(d, "pubdate", 0) or 0)
                _log.info(f"[{name}] 群 {gid[:8]}… 最新动态回退为更早的 "
                          f"{d.dyn_id}（疑似删动态），静默不推送"
                          f"（基线时间不回退）")
            elif self._should_silence_dynamic(uid, d):
                skip.add(gid)
                _log.debug(f"[{name}] 这条是开播/投稿的重复动态，已静默")
        todo = [g for g in gids if g not in skip]
        if todo:
            for gid in todo:
                self._mark_dyn_notified(
                    gid, uid, d.dyn_id, getattr(d, "pubdate", 0) or 0)
            await self._push("dynamic", uid, name, d, exclude=skip)

    async def _check_top_comment(self, uid: int, name: str,
                                 dyn_id: str = "", rid: str = ""):
        """检测「置顶动态内的置顶评论」。

        ⚠️ 之前这里是**完全静默**的：所有异常都被 `except Exception: return`
        吞掉，取不到也直接 return。用户看到的就是"没检测、没读取、没反馈"。

        另外它原本**嵌套在 _check_dynamic 里**，只有 `if top and top.dyn_id`
        成立时才被调用 —— 一旦置顶动态没被识别出来，置顶评论**一次都不会查**。

        现在改成：
        1. 自给自足：自己取置顶动态（调用方也可传入避免重复请求）
        2. 每一步都记 note + 日志，面板和 /检测 都能看到失败原因
        """
        # 1) 拿到置顶动态（没传就自己取）
        if not dyn_id:
            try:
                top = await self.bili.get_pinned_dynamic(uid)
            except BiliError as e:
                self._note(uid, "top_cmt", f"置顶动态获取失败：{e}")
                self._log_check_fail(name, "置顶动态", e)
                return
            except Exception as e:
                self._note(uid, "top_cmt", f"置顶动态异常：{redact(str(e))[:50]}")
                return
            if not top or not top.dyn_id:
                self._note(uid, "top_cmt", "没找到置顶动态")
                return
            dyn_id = top.dyn_id
            rid = getattr(top, "rid", "") or ""
            top_dyn = top
        else:
            top_dyn = None

        # 2) 取置顶动态下的置顶评论
        try:
            c = await self.bili.get_pinned_comment(
                dyn_id, uid, rid,
                comment_id=getattr(top_dyn, "comment_id_str", "") or "",
                comment_type=getattr(top_dyn, "comment_type", 0) or 0)
        except BiliError as e:
            self._note(uid, "top_cmt", f"评论获取失败：{e}")
            self._log_check_fail(name, "置顶评论", e)
            return
        except Exception as e:
            self._note(uid, "top_cmt", f"评论异常：{redact(str(e))[:50]}")
            _log.warning(f"[{name}] 置顶评论异常：{e}")
            return

        if not c or not c.get("id"):
            # 区分"接口没返回"和"确实没有置顶评论"，两者都让用户看到
            self._note(uid, "top_cmt", f"未返回置顶评论（oid={rid or dyn_id}）")
            return

        # 只认 UP 主本人的置顶评论：
        # 置顶评论常常是粉丝刷上去的，那种对订阅者没意义。
        # 开关 top_comment_only_up 可关掉这个限制。
        if self.cfg.get("top_comment_only_up", True):
            mid = str((c or {}).get("mid") or "").strip()
            if mid and mid != str(uid):
                self._note(uid, "top_cmt", "评论者不是 UP 主本人，已跳过")
                return

        # 3) 与基线比对
        # 按群隔离基线：刚开启「置顶评论」的群只静默对齐，
        # 不补推它开启之前就存在的那条评论。
        cur = c["id"]
        try:
            gids = self.store.targets(uid, "top_comment") or []
        except Exception:
            gids = []
        skip = set()
        for gid in gids:
            base = self.store.baseline_of(gid, uid, "dyn_top_cmt")
            prev = base.get("id")
            self.store.set_baseline_of(gid, uid, "dyn_top_cmt", id=cur)
            if not prev:
                skip.add(gid)          # 首次：静默对齐，不推送
                _log.debug(f"[{name}] 群 {gid[:8]}… 置顶评论基线已建立"
                           f"（静默，不推送）")
            elif prev == cur:
                skip.add(gid)
        if [g for g in gids if g not in skip]:
            self._note(uid, "top_cmt", f"更新：{c.get('content','')[:24]}")
            await self._push("top_comment", uid, name, c, exclude=skip)
        else:
            self._note(uid, "top_cmt", c.get("content", "")[:24])

    # ============ 推送 ============
    async def _push_plain(self, gid: str, md: str, plain: str,
                          msg_id=None) -> bool:
        """纯文本推送（Markdown 没申请权限时用）。

        直接把 markdown 原文当文本发会看到一堆星号和大于号，
        所以走 notify.to_plain 转成用 Unicode 符号排版的文本。
        """
        try:
            from notify import to_plain
            text = to_plain(md) or plain
        except Exception:
            text = plain
        if not text:
            return False
        try:
            await self.qq.send_text(gid, text, msg_id=msg_id)
            return True
        except Exception as e:
            # 无权限时给能照做的提示，别只留一句"推送失败"
            if is_no_perm_error(e):
                self._mark_no_perm(gid)
                _log.warning(no_perm_hint(gid))
                return False
            _log.warning(f"纯文本推送失败：{e}")
            # 如果是"机器人已不在该群"，顺手把这个群清掉
            try:
                self._note_send_error(gid, e)
            except Exception:
                pass
            return False

    async def _push(self, kind: str, uid: int, name: str, info,
                    exclude=None):
        # 启动同步期间：只更新基线，一律不推送。
        # 这样重启期间发生的变化不会被当成"新内容"补推一遍。
        if getattr(self, "_silent", False):
            _log.debug(f"启动同步中，{name} 的【{kind}】只记基线，不推送")
            return
        # 置顶评论是独立开关，只有订阅了它的群才收
        lookup = {"offline": "live"}.get(kind, kind)
        targets = self.store.targets(uid, lookup)
        if not targets:
            return
        # ⚠️ 渲染必须**放在下面的按群循环里**：
        # 每个群可以有自己的文案模板，同一条推送在不同群里文案可能不同。
        # 在循环外只渲染一次的话，所有群都会用到同一个文案。

        # 图片改走富媒体（msg_type=7）：平台下载或本地 base64 直传，
        # 不走客户端渲染，因此不受「消息 URL 配置」报备限制。
        import media as _media
        img_mode = str(self.cfg.get("image_send_mode", "inline")).lower()
        if not self.send_image:
            img_mode = "off"
        if img_mode == "inline":
            # ⚠️ 内嵌模式：![](url) 留在卡片里一起发出去，不单独发一条图。
            #    把 cover 置空即可复用下面两条既有逻辑：
            #    md_g_no_img = md_g（不剥图）、if cover:（不单独发）。
            cover = ""
        else:
            cover = _media_safe_cover(kind, info) if img_mode != "off" else ""

        # exclude：这些群不用推（比如该视频已由「合集更新」负责推送）
        skip = set(exclude or ())
        for gid in targets:
            if gid in skip:
                continue
            # 该群刚被官方回过"主动消息无权限" → 冷却期内先不推。
            # 不然每一轮都要撞两次墙，日志里成对刷同一条警告。
            if self._noperm_active(gid):
                _log.debug(f"群 {gid[:8]}… 主动消息无权限，冷却中，本轮跳过")
                continue
            # ⚠️ 每个群用自己的文案模板渲染 —— 不同群可以有不同的说法
            try:
                # uid 只有下播卡片用得到：按钮指向 UP 主空间，
                # 而不是一个已经关掉的直播间。
                md_g, buttons = render(kind, info, name,
                                       self._tpls_for(gid, uid=uid), uid=uid)
            except Exception as e:
                _log.error(f"消息渲染失败：{e}")
                continue
            # 纯文本降级文案：用该群的模板渲染后转纯文本排版
            plain_g = to_plain(md_g) or short_hint(kind, name)
            md_g_no_img = strip_images(md_g) if cover else md_g
            # 记下这条推送用到过哪些图。
            # ⚠️ 内嵌模式图片不落盘，光数缓存文件永远是 0 —— 面板
            #    「媒体缓存」就是靠这份清单才能列出动态/视频/直播的封面。
            try:
                from notify import IMG_LINE_RE as _img_re
                for _ln in (md_g or "").splitlines():
                    _m = _img_re.match((_ln or "").strip())
                    if _m:
                        _media.remember_recent(_m.group(1), kind, name)
                if cover:
                    _media.remember_recent(cover, kind, name)
            except Exception:
                pass
            try:
                if cover:
                    ok_img = await _media.send_image(
                        self.qq, gid, cover, mode=img_mode)
                    if ok_img:
                        await asyncio.sleep(0.4)
                    else:
                        _log.debug(f"图片发送失败，改用卡片内图片：{cover[:48]}")
                        md_g_no_img = md_g    # 退回到卡片里带图
                style = str(self.cfg.get("message_style", "markdown")).lower()
                if style == "text":
                    # 没申请 Markdown 权限：直接走纯文本
                    ok = await self._push_plain(gid, md_g_no_img, plain_g)
                else:
                    try:
                        await self.qq.send_markdown(gid, md_g_no_img,
                                                    keyboard=build_keyboard(buttons))
                        ok = True
                    except Exception as e_md:
                        # ⚠️ 「主动消息无权限」时**不要**退纯文本：
                        # Markdown 和纯文本走的是同一个权限，
                        # 退过去必然也是同一个错 —— 只会多一次请求、
                        # 多一条一模一样的警告。直接说清楚并冷却。
                        if is_no_perm_error(e_md):
                            self._mark_no_perm(gid)
                            _log.warning(no_perm_hint(gid))
                            ok = False
                            continue
                        # 发不出去多半是没申请 Markdown 权限
                        # （或没报备域名）→ 自动退回纯文本，
                        # 否则用户会完全收不到提醒。
                        _log.warning(
                            f"Markdown 推送失败，自动改用纯文本："
                            f"{redact(str(e_md))[:80]}")
                        ok = await self._push_plain(gid, md_g_no_img, plain_g)
                if not ok:
                    continue
                heartbeat.bump("pushes")
                # 记一条「最近动态」（概况页卡片）。一个群调一次，
                # 同一次更新在窗口内会自动累加成"推给了几个群"。
                try:
                    heartbeat.push_event(kind, uid, name, 1)
                except Exception:
                    pass
                # 记下"这次推送的是什么内容"，用于动态去重。
                # B 站开播/投稿会自动发一条动态，且那条动态**指向同一个
                # 直播间 / 同一个视频**。所以比对的是内容，不是类型。
                self._remember_pushed(uid, lookup, info)
                heartbeat.touch(
                    last_push_at=time.time(),
                    last_push=f"{KIND_LABEL.get(kind, kind)} · {name}",
                )
                _log.info(f"已推送【{KIND_LABEL.get(kind, kind)}】{name} → 群 {gid[:8]}…")
            except Exception as e:
                heartbeat.bump("errors")
                heartbeat.touch(last_error=redact(str(e))[:200])
                if is_sandbox_error(e):
                    # 群还在，只是开着沙箱往正式群发被官方拒了。
                    # 说清楚，否则用户会以为机器人被踢出去、白折腾。
                    _log.warning(
                        f"推送到群 {gid[:8]}… 失败：沙箱环境不能访问此资源 —— "
                        "到面板「设置 → 通用 → 沙盒群」关掉「沙箱环境」"
                        "（已支持热切换，不用重启机器人）")
                else:
                    _log.warning(f"推送到群 {gid[:8]}… 失败：{redact(str(e))}")
                self._note_send_error(gid, e)
            await asyncio.sleep(0.6)      # 主动消息频控保护


# ⚠️ 11292 是个「一号多义」的危险码，必须先判沙箱再看群：
#   官方既用它表示"机器人已不在该群"，也用它表示
#   {"code":11292,"err_code":40012004,"message":"沙箱环境不能访问此资源"}。
#   以前只按码判断 → 开着沙箱往正式群发消息被回 11292
#   → 误判成"机器人不在群里" → 群连同订阅数据一起被删掉。
#   沙箱限制是环境问题、群还在，永远不能按群失效处理。
SANDBOX_ERR_WORDS = ("沙箱", "沙盒", "sandbox")
SANDBOX_ERR_CODES = ("40012004",)


def is_sandbox_error(text) -> bool:
    """这条报错是不是「沙箱环境不能访问此资源」。

    开着「沙箱环境」却往**正式群**发消息 / 查群信息，官方就回这个。
    它和"机器人不在群里"完全两回事，绝不能当成群失效去删数据。
    """
    t = str(text or "")
    if not t:
        return False
    low = t.lower()
    for w in SANDBOX_ERR_WORDS:
        if w in low:
            return True
    for c in SANDBOX_ERR_CODES:
        if c in t:
            return True
    return False


# 官方错误码：机器人已经不在那个群里了 / 群没了
#   11242 = 机器人不在这个群里（或已被移出）
#   11252 = 群不存在或已解散
#   11292 = 机器人已不在该群，无法获取（也可能是沙箱限制，见上）
# ⚠️ 11293 是最常见的那个：群主把机器人踢了，官方直接回
#   {"code":11293, "message":"鉴权失败，机器人非群成员"}
# 上一轮我漏了它 —— 结果就是"检测到了但没删"。
GONE_GROUP_CODES = (11242, 11252, 11292, 11293)

# 官方 message 里出现这些词也算。万一以后换错误码，中文提示能兜住。
GONE_GROUP_WORDS = ("非群成员", "不在该群", "已退出", "不是群成员")


def is_gone_group_error(text) -> bool:
    """推送失败的错误里，是不是明确说"机器人已不在这个群"。

    ⚠️ 只在**明确**命中错误码或关键词时才认。网络抖动、频控、内容违规
    都可能让推送失败，那些情况下删群会误伤。
    """
    t = str(text or "")
    if not t:
        return False
    # 沙箱限制优先排除：它和"不在群里"共用 11292，误判会删掉整个群
    if is_sandbox_error(t):
        return False
    for c in GONE_GROUP_CODES:
        if str(c) in t:
            return True
    for w in GONE_GROUP_WORDS:
        if w in t:
            return True
    return False


# 官方错误码 40034105 = {"message":"主动消息失败, 无权限"}
#   机器人**在群里**，但官方不允许它主动发言。常见原因：
#     ① 群设置里没开「允许机器人主动发言」
#     ② 机器人刚被拉进群，还没激活（群里没人 @ 过它、也没发过消息）
#     ③ 机器人被群管理禁言
#     ④ 机器人没上架，而这个群不在「沙箱群 / 开发体验」名单里
# ⚠️ 它**不等于**机器人不在群里：群还在、订阅还在，绝不能删群。
#    且 Markdown 和纯文本走的是同一个权限，
#    所以命中后不要再退纯文本重试一次（必然也是同一个错）。
NO_PERM_CODES = ("40034105",)
NO_PERM_WORDS = ("主动消息失败", "主动消息", "无权限")


def is_no_perm_error(text) -> bool:
    """推送失败是不是「主动消息无权限」。

    命中后：说清原因和怎么办 + 该群冷却一段时间，
    而不是笼统一句"推送失败"还每轮重试两次。
    """
    t = str(text or "")
    if not t:
        return False
    for c in NO_PERM_CODES:
        if c in t:
            return True
    low = t.lower()
    if "主动消息" in t and "无权限" in t:
        return True
    return "主动消息失败" in low


def no_perm_hint(gid: str = "") -> str:
    """无权限时给一句能照做的话，不只是一句"失败了"。"""
    who = f"群 {gid[:8]}… " if gid else ""
    return (
        f"{who}主动消息无权限（官方 40034105），这条提醒发不出去 —— "
        "机器人需要在该群获得主动发言权限："
        "① 群设置 → 机器人 → 打开「允许机器人主动发言」；"
        "② 刚拉进群的话，先在群里 @ 一下机器人或发条消息激活一次；"
        "③ 检查机器人是不是被禁言、或这个群不在沙箱群名单里。"
        "处理好之前会自动跳过这个群，不再反复重试"
    )


def _media_safe_cover(kind: str, info) -> str:
    """取封面，失败返回空（不能因为取封面把整条推送搞挂）。"""
    try:
        import notify as _n
        return _n.cover_of(kind, info) or ""
    except Exception:
        return ""


def patch_botpy_group_message_parser():
    """补上 botpy 缺失的 GROUP_MESSAGE_CREATE 解析器。

    官方文档「群消息(全量模式)」：机器人开启"接收所有消息"后，
    群里每条消息（不限于 @）都会推送 GROUP_MESSAGE_CREATE 事件。

    但 botpy 1.2.1 的 connection.py 里**没有** parse_group_message_create，
    官方仓库 issue #8842 记录了后果：
        `_parser unknown event group_message_create`
        且最坏情况下连 @ 消息一起被丢弃。

    这里在缺失时补上，让"群里直接打 /列表"也能工作。
    只在确实缺失时补，不覆盖已有实现。
    """
    try:
        import botpy.connection as conn

        cls = getattr(conn, "ConnectionState", None)
        if cls is None or hasattr(cls, "parse_group_message_create"):
            return True      # 已有，不用管
        from botpy.message import GroupMessage

        def parse_group_message_create(self, payload):
            _m = GroupMessage(self.api, payload.get("id", None),
                              payload.get("d", {}))
            self._dispatch("group_message_create", _m)

        cls.parse_group_message_create = parse_group_message_create
        return True
    except Exception:
        return False         # 补不了也不影响现有 @ 消息功能


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    # 密文字段还原（config.yaml 里的 secret / cookie 是加密保存的）
    try:
        import secretbox
        cfg = secretbox.reveal_cfg(cfg)
    except Exception:
        pass
    return cfg


def _fatal_dump(e: BaseException) -> None:
    """致命异常：把完整堆栈打到控制台，并单独存一份崩溃日志。

    为什么不只写 bot.log：机器人一崩，面板往往也跟着打不开（或用户
    根本不知道日志在哪），写进日志等于没说。这里两边都写，
    控制台这份才是用户第一眼看到的。
    """
    import traceback as _tb
    lines = _tb.format_exception(type(e), e, e.__traceback__)
    txt = "".join(lines).strip()
    safe_print()
    safe_print("=" * 58)
    safe_print(" 机器人异常退出（不是凭据问题），下面是完整原因：")
    safe_print("=" * 58)
    for _l in txt.splitlines():
        safe_print("   " + _l)
    safe_print("=" * 58)
    try:
        import paths
        _d = os.path.join(paths.data_home(), "logs")
        os.makedirs(_d, exist_ok=True)
        with open(os.path.join(_d, "crash.log"), "a", encoding="utf-8") as f:
            f.write("\n[%s]\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write(txt + "\n")
        safe_print(" 已同时写入：%s" % os.path.join(_d, "crash.log"))
    except Exception:
        pass


def main():
    # 登记自己的 PID：cleanup 靠 .bot.pids 精确清理，
    # 不依赖命令行解析（相对路径启动时命令行里没有项目目录）
    try:
        import cleanup
        cleanup.register_pid()
    except Exception:
        pass

    # 看门狗：关窗口时 start.py 被系统强杀，finally 跑不到，
    # 本进程会留在后台继续监听同一个群（一条指令回两次的元凶）。
    # 所以自己盯着父进程，爹没了就走。
    try:
        import argparse as _ap
        _pp = _ap.ArgumentParser(add_help=False)
        _pp.add_argument("--parent", type=int, default=0)
        _ns, _ = _pp.parse_known_args()
        from parentwatch import start_watch
        start_watch(_ns.parent, name="bot")
    except Exception:
        pass
    from cfgutil import creds_ready

    # ⚠️ 必须走 paths.config_path()：v1.96 起 config.yaml 不再跟源码放在
    #    同一个目录（数据目录独立），而本进程是以 cwd=src/ 启动的，
    #    写死相对路径 "config.yaml" 时 os.path.exists 永远是 False
    #    → 直接 sys.exit(1)。表现就是「填完凭据，机器人立刻退出码 1，
    #    且提示不是凭据问题」——其实是压根没找到配置文件。
    cfg_path = os.environ.get("BOT_CONFIG", "").strip() or paths.config_path()
    if not os.path.exists(cfg_path):
        safe_print(f"缺少配置文件 {cfg_path}，请先复制 config.example.yaml 并填写。")
        sys.exit(1)
    cfg = load_config(cfg_path)
    cfg.setdefault("appid", os.environ.get("QQ_BOT_APPID", ""))
    cfg.setdefault("secret", os.environ.get("QQ_BOT_SECRET", ""))

    if not creds_ready(cfg):
        safe_print()
        safe_print("=" * 58)
        safe_print(" 还没填好 AppID / AppSecret，机器人无法登录")
        safe_print("=" * 58)
        safe_print(" AppID 和 AppSecret 在 QQ 开放平台 → 你的机器人 →「开发设置」里。")
        safe_print(" 填两个地方任意一个：")
        safe_print("   1) 网页面板「⚙️ 设置」里填（推荐，不会写错格式）")
        safe_print(f"   2) 直接编辑 {cfg_path}")
        safe_print()
        safe_print(" 注意：AppID 是纯数字；AppSecret 点「生成」后只显示一次。")
        safe_print("=" * 58)
        sys.exit(2)

    # 单实例保护：已经有一个在跑就别再来一个
    if not acquire_lock():
        safe_print()
        safe_print("=" * 58)
        safe_print(" 已经有一个机器人在运行了，本次不再启动第二个。")
        safe_print("=" * 58)
        safe_print(" 多开会导致：一条指令回两次、同一条动态推三次。")
        safe_print(" 想重启请先关掉原来的窗口，或运行：python cleanup.py")
        safe_print("=" * 58)
        sys.exit(0)

    # 补 botpy 缺失的群全量消息解析器（不影响现有 @ 消息）
    try:
        if patch_botpy_group_message_parser():
            safe_print("  [OK] 群全量消息解析器已就绪")
    except Exception:
        pass

    # public_messages = 群/C2C 消息（含全量模式）；interaction = 指令面板
    intents = botpy.Intents(public_messages=True)
    client = NotifyBot(cfg, intents=intents, is_sandbox=bool(cfg.get("is_sandbox", False)))
    try:
        try:
            client.run(appid=str(cfg["appid"]), secret=str(cfg["secret"]))
        finally:
            release_lock()
    except RuntimeError as e:
        msg = str(e)
        safe_print()
        safe_print("=" * 58)
        safe_print(" 机器人登录失败")
        safe_print("=" * 58)
        if "appid invalid" in msg or "100007" in msg:
            safe_print(" 原因：AppID 不对（平台返回 appid invalid）")
            safe_print()
            safe_print(" 排查：")
            safe_print("   1. AppID 是不是抄错了？它是纯数字，不含空格和引号")
            safe_print("   2. 确认填的是「机器人」的 AppID，不是其他应用的")
            safe_print("   3. 如果开了「沙箱环境」，先在面板设置里关掉试试")
        elif "secret" in msg.lower() or "100008" in msg:
            safe_print(" 原因：AppSecret 不对")
            safe_print(" AppSecret 只显示一次，忘了的话去开放平台重新生成。")
        else:
            safe_print(f" 平台返回：{msg}")
        safe_print()
        safe_print(" 去面板「⚙️ 设置」改完保存，这里会自动重试。")
        safe_print("=" * 58)
        sys.exit(3)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:
        # ⚠️ 兜底：以前任何漏网的异常都直接变成"退出码 1"，
        #    控制台一行原因都没有，只剩日志里有 —— 而面板常常也打不开，
        #    用户只能干瞪眼。这里把完整堆栈打在控制台上。
        _fatal_dump(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
