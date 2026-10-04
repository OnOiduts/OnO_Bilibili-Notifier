# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""加固层：HTTP 安全头、全局限流、自动封禁、请求体限制、文件完整性自检。

这些是「纵深防御」——即使前面某一层被绕过，后面还有一层兜底。
用法：在 webui.py 里调用 init_app(app) 即可全部生效。
"""
import hashlib
import json
import os
import time
from typing import Dict

import paths as _paths
BASE = os.path.dirname(os.path.abspath(__file__))
BAN_FILE = _paths.run_file(".banned_ips")
INTEGRITY_FILE = _paths.run_file(".integrity.json")

# 全局限流：单 IP 在窗口内允许的写请求数
WRITE_WINDOW = 60          # 秒
WRITE_MAX = 120            # 写请求
READ_WINDOW = 60
READ_MAX = 300
MAX_CONTENT_LENGTH = 256 * 1024   # 请求体上限 256KB

# ── 封禁策略（v1.5.2 放宽）────────────────────────────────────
# 之前的 3600 秒对"自己点自己"来说惩罚过重：
# 面板默认只监听 127.0.0.1，能访问的只有本机的你，
# 多点几下就被锁 1 小时，而且解封接口本身也被拦（死锁）。
BAN_SECONDS = 90           # 首次封禁时长（原来 3600）
BAN_MAX_SECONDS = 600      # 封禁时长上限（原来会翻倍到无穷）
BAN_AFTER = 6              # 触发多少次限流才封（原来 3）
LOOPBACK_NO_BAN = True     # 本机地址（127.0.0.1 / ::1）永不自动封禁

# 面板的自动轮询接口：只读、频率固定，不该消耗限流额度，
# 否则页面多开几个标签页就容易误触发。
POLL_PATHS = ("/api/state", "/api/log", "/api/security",
              "/api/harden", "/api/bili/cookie",
              "/api/bili/browser-status", "/api/version")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",                 # 禁止被嵌进 iframe（防点击劫持）
    "Referrer-Policy": "no-referrer",
    "X-XSS-Protection": "0",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cache-Control": "no-store",               # 面板内容不进浏览器缓存
    "Content-Security-Policy": (
        "default-src 'self'; "
        "img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}


class Guard:
    """请求级防护：滑动窗口限流 + 自动封禁。"""

    def __init__(self, ban_after: int = BAN_AFTER,
                 ban_seconds: int = BAN_SECONDS):
        self._hits: Dict[str, list] = {}
        self._bans: Dict[str, float] = {}
        self.ban_after = ban_after          # 触发多少次限流后封禁
        self.ban_seconds = ban_seconds
        self._strikes: Dict[str, int] = {}
        self._load()

    def is_loopback(self, ip: str) -> bool:
        """本机地址。面板默认只监听 127.0.0.1，能连进来的只有你自己。"""
        return ip in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1")

    def should_ban(self, ip: str) -> bool:
        """本机访问永不自动封禁（只限流，不锁死）。"""
        if LOOPBACK_NO_BAN and self.is_loopback(ip):
            return False
        return True

    # ---------- 封禁名单持久化 ----------
    def _load(self):
        try:
            with open(BAN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            self._bans = {k: v for k, v in data.items() if v > now}
        except Exception:
            self._bans = {}

    def _save(self):
        try:
            with open(BAN_FILE, "w", encoding="utf-8") as f:
                json.dump(self._bans, f)
        except OSError:
            pass

    def banned(self, ip: str) -> int:
        """返回还需封禁多少秒；0 表示未被封。"""
        until = self._bans.get(ip, 0)
        return max(0, int(until - time.time()))

    def ban(self, ip: str, seconds: int = None):
        if seconds is None:
            # 渐进：首次 ban_seconds，每次翻倍，但有上限。
            # 之前是固定 3600 且无上限，对误触太狠。
            n = self._strikes.get(ip, 0)
            seconds = min(self.ban_seconds * (2 ** max(0, n - 1)),
                          BAN_MAX_SECONDS)
        self._bans[ip] = time.time() + seconds
        self._save()

    def unban_all(self):
        self._bans.clear()
        self._hits.clear()
        self._strikes.clear()
        self._save()

    def ban_list(self) -> list:
        now = time.time()
        return [{"ip": k, "left": int(v - now)}
                for k, v in self._bans.items() if v > now]

    # ---------- 限流 ----------
    def allow(self, ip: str, is_write: bool, path: str = "") -> bool:
        # 面板的自动轮询不消耗额度：它是页面自己发的、频率固定，
        # 多开几个标签页就会成倍增长，容易误触发限流。
        if path in POLL_PATHS:
            return True

        window = WRITE_WINDOW if is_write else READ_WINDOW
        limit = WRITE_MAX if is_write else READ_MAX
        now = time.time()
        hits = [t for t in self._hits.get(ip, []) if now - t < window]
        hits.append(now)
        self._hits[ip] = hits
        if len(hits) > limit:
            if not self.should_ban(ip):
                # 本机：只限流，绝不封禁
                return False
            self._strikes[ip] = self._strikes.get(ip, 0) + 1
            if self._strikes[ip] >= self.ban_after:
                self.ban(ip)
                self._strikes[ip] = 0
            return False
        return True


GUARD = Guard()


# ────────────────────────── 文件完整性自检 ──────────────────────────
def _core_files() -> list:
    return [f for f in (
        # ⚠️ store.py 已在 v1.2.0 删除（被 db.py 取代），不要再加回清单
        "bot.py", "webui.py", "bilibili.py", "notify.py", "qqapi.py",
        "db.py", "security.py", "heartbeat.py", "cfgutil.py", "bili_login.py",
    ) if os.path.exists(os.path.join(BASE, f))]


def _digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def save_baseline() -> dict:
    """把当前代码哈希存为基准，之后可检测是否被改动。"""
    data = {"ts": time.time(),
            "files": {f: _digest(os.path.join(BASE, f)) for f in _core_files()}}
    with open(INTEGRITY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    return data


def check_integrity() -> dict:
    """对比基准，返回被改动/新增的文件。"""
    if not os.path.exists(INTEGRITY_FILE):
        return {"ok": True, "baseline": False, "changed": [],
                "msg": "还没建立基准快照"}
    try:
        with open(INTEGRITY_FILE, "r", encoding="utf-8") as f:
            base = json.load(f)
    except Exception:
        return {"ok": True, "baseline": False, "changed": [], "msg": "基准文件损坏"}
    changed = []
    for name, old in (base.get("files") or {}).items():
        p = os.path.join(BASE, name)
        if not os.path.exists(p):
            changed.append({"file": name, "status": "已丢失"})
        elif _digest(p) != old:
            changed.append({"file": name, "status": "被修改"})
    return {"ok": not changed, "baseline": True, "changed": changed,
            "saved_at": time.strftime("%Y-%m-%d %H:%M",
                                      time.localtime(base.get("ts", 0)))}


def init_app(app):
    """把加固层挂到 Flask app 上。"""
    import threading

    from flask import jsonify, request

    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    lock = threading.Lock()

    # 这些路径必须绕过封禁检查：
    # 否则一旦被封，连"解封"和"登录"都调不通 —— 死锁，只能手动删文件。
    BAN_EXEMPT = ("/api/harden/unban", "/login", "/logout",
                  "/api/login", "/api/harden")

    @app.before_request
    def _guard_request():
        # 静态资源和登录页不参与限流（登录页自身有限流）
        if request.path.startswith("/static"):
            return None

        ip = (request.remote_addr or "unknown")

        left = GUARD.banned(ip)
        if left > 0 and request.path not in BAN_EXEMPT:
            return jsonify({
                "ok": False,
                "msg": f"操作太快被临时限制了，还剩 {left} 秒",
                "banned": True,
                "left": left,
                "how": ("等一会儿自动解除；或删掉数据目录里的 .banned_ips "
                        "文件后重启；或在命令行执行 python check.py --unban"),
            }), 429

        is_write = request.method != "GET"
        with lock:
            if not GUARD.allow(ip, is_write, path=request.path):
                msg = "请求有点快，歇一下再试"
                if GUARD.is_loopback(ip):
                    # 本机访问说清楚：不会封你，只是暂时挡一下
                    msg = "操作太快了，歇几秒再试（本机不会被封禁）"
                return jsonify({"ok": False, "msg": msg,
                                "banned": False}), 429
        return None

    @app.after_request
    def _add_headers(resp):
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        # 面板只允许本机访问时，顺手挡掉一切外链嵌入
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        return resp

    @app.errorhandler(413)
    def _too_large(_e):
        return jsonify({"ok": False, "msg": "请求内容过大"}), 413

    return app
