# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""安全防护模块。

覆盖这几类风险：
  1. 面板被公网扫到 → 默认只监听 127.0.0.1；显式对外暴露时强制要求登录口令
  2. 口令被爆破      → 登录失败限流 + 指数锁定 + 审计日志
  3. 口令明文泄露    → 保存时自动转成 pbkdf2 哈希；配置文件收紧为 600
  4. 会话被劫持      → HMAC 签名令牌（httpOnly + SameSite），重启后仍有效但会过期
  5. CSRF            → 写接口要求自定义请求头 + SameSite=Strict
  6. 路径穿越        → 所有文件读写限制在项目目录内
  7. SSRF / 恶意图床 → 图片 URL 只放行 B 站域名白名单
  8. 日志泄密        → 输出的 secret / token / cookie 一律打码

命令行工具：
  python security.py --hash 你的口令     # 生成可写进 config.yaml 的哈希
  python security.py --check             # 输出当前安全体检报告

⚠️ 从未实际测试过：
    本模块的每一条防护都只做过「能不能跑通」的功能性验证 ——
    设了口令确实会要求登录、连错 5 次确实会锁定、哈希确实写成了 pbkdf2 串。
    但它**从未经过真正的安全测试、渗透测试或代码审计**，
    也没有请任何安全从业者复核过。
    能跑通 ≠ 挡得住攻击。不要把它当作可靠的安全屏障，
    尤其不要把面板直接暴露到公网。发现可疑之处请务必反馈。
"""
import hashlib
import hmac
import os
import re
import secrets
import sys
import time
from typing import List, Tuple
from urllib.parse import urlparse
import paths

BASE = os.path.dirname(os.path.abspath(__file__))

PBKDF2_ITER = 120_000
TOKEN_TTL = 7 * 86400
# 不勾「记住登录状态」时的令牌寿命（滑动续期，见 webui._issue_login_cookie）
SESSION_TOKEN_TTL = 30 * 60

# 允许出现在提醒卡片里的图片域名（防上游返回恶意地址）
IMG_HOST_SUFFIXES = (
    ".hdslb.com", ".bilivideo.com", ".biliimg.com",
    ".bilibili.com", ".biliapi.com", ".akamaized.net",
)

_SENSITIVE_RE = re.compile(
    r"((?:secret|token|password|passwd|cookie|sessdata|bili_jct)"
    r"[\"'\s:=]{1,4})([^\s,;\"'\}\]\)]{4,})", re.I)


# ────────────────────────── 口令哈希 ──────────────────────────
def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"),
                             salt.encode("utf-8"), PBKDF2_ITER)
    return f"pbkdf2${PBKDF2_ITER}${salt}${dk.hex()}"


def is_hashed(stored: str) -> bool:
    return bool(stored) and stored.startswith("pbkdf2$")


def verify_password(pw: str, stored: str) -> bool:
    """同时支持哈希与明文（明文用于兼容手填，恒定时间比较）。"""
    if not stored:
        return False
    if is_hashed(stored):
        try:
            _, it, salt, hexdk = stored.split("$", 3)
            dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"),
                                     salt.encode("utf-8"), int(it))
            return hmac.compare_digest(dk.hex(), hexdk)
        except Exception:
            return False
    return hmac.compare_digest(str(stored), str(pw))


# ────────────────────────── 会话令牌 ──────────────────────────
def _secret_key_path() -> str:
    return paths.key_file(".webui_secret")


def get_secret_key() -> str:
    """签名密钥持久化到文件（600），避免每次重启都被登出。"""
    path = _secret_key_path()
    env = os.environ.get("WEBUI_SECRET")
    if env:
        return env
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            pass
    v = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(v)
    except OSError:
        pass
    return v


def _sign(key: str, payload: str) -> str:
    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"),
                    hashlib.sha256).hexdigest()


# ─────────────────── 进程世代号（关掉程序后旧令牌作废） ───────────────────
# 每次进程启动随机生成，**只活在内存里、绝不落盘**。关掉程序再开，这个值
# 就变了 —— 令牌里编了它，于是上一轮的令牌全部失效、必须重新登录。
#
# 为什么需要它：签名密钥必须持久化（否则重启一次就被登出一次），而密钥一
# 持久化，已签发的令牌服务端就作废不掉。用户要的是「我把程序关了，再开就
# 得重新输口令」—— 这是最朴素的预期，之前却做不到：关掉机器人再启动，
# 浏览器里那枚令牌照样验得过。
#
# 进程世代号正好补上这一环：它的生命周期就是进程的生命周期，进程一退，
# 它带走的那一批令牌一起作废。
_BOOT_ID = secrets.token_hex(8)


def boot_id() -> str:
    """当前进程的世代号。进程重启即变，不落盘。"""
    return _BOOT_ID


def issue_token(key: str, ttl: int = TOKEN_TTL, gen: int = 0,
                boot: str = "") -> str:
    """签发登录令牌。

    ⚠️ gen（令牌世代号）是这里的关键，别当成可选装饰。

    签名密钥是**持久化**的（get_secret_key 写进文件，否则每次重启都被登出），
    而令牌有效期 7 天。两者叠加的后果是：一旦签发，服务端就**再也作废不掉**
    —— 只要密钥文件还在，那个 7 天令牌就一直验得过。

    这曾造成一个真实漏洞：设置口令的接口以前会无条件下发令牌，用户设完口令
    白拿一个 7 天会话。后来改成了"首次设置不下发令牌"，但**已经发出去的
    旧令牌依然有效** —— 于是表现成「设完口令再打开还是不弹登录页，
    换什么地址都能进」，而服务端对此毫无办法。

    所以把世代号编进令牌：设置/清除口令时 +1，此后签发的令牌带新世代号，
    旧的因对不上而全部失效。

    ⚠️ boot（进程世代号）同理关键：它让「关掉程序就得重新登录」成立。
    不传则用当前进程的 boot_id() —— 也就是说默认行为就是绑定本进程。

    格式 admin:{exp}:{gen}:{boot}:{nonce}:{sig}
    """
    exp = int(time.time()) + ttl
    nonce = secrets.token_hex(8)
    bid = boot or boot_id()
    sig = _sign(key, f"admin:{exp}:{gen}:{bid}:{nonce}")
    return f"admin:{exp}:{gen}:{bid}:{nonce}:{sig}"


def verify_token(key: str, token: str, gen: int = 0, boot: str = "") -> bool:
    if not token:
        return False
    try:
        # 6 段：旧格式（4 段 / 5 段）在这里会 ValueError —— 正好，升级后
        # 旧令牌一律失效、必须重新登录一次，这正是我们想要的。
        user, exp, tgen, tboot, nonce, sig = token.split(":", 5)
    except ValueError:
        return False
    if tgen != str(gen):
        return False
    if tboot != (boot or boot_id()):
        return False
    if not hmac.compare_digest(
            sig, _sign(key, f"{user}:{exp}:{tgen}:{tboot}:{nonce}")):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


# ────────────────────── 令牌世代号（让旧会话失效） ──────────────────────
def _auth_gen_path() -> str:
    return paths.key_file(".webui_authgen")


def get_auth_gen() -> int:
    """读当前令牌世代号。文件不存在/损坏都当作 0。"""
    try:
        with open(_auth_gen_path(), "r", encoding="utf-8") as f:
            return int((f.read() or "0").strip())
    except (OSError, ValueError):
        return 0


def bump_auth_gen() -> int:
    """世代号 +1 并落盘，返回新值。此后此前签发的所有令牌立即失效。

    调用时机：设置口令、修改口令、清除口令、登出。
    这些动作的语义都是"此前的会话不再作数"。
    """
    gen = get_auth_gen() + 1
    try:
        fd = os.open(_auth_gen_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(gen))
    except OSError:
        pass
    return gen


# ────────────────────────── 登录限流 ──────────────────────────
class LoginLimiter:
    """同一来源连续失败后指数锁定，防暴力破解。"""

    def __init__(self, max_fail: int = 5, window: int = 900,
                 lock_base: int = 60, lock_max: int = 3600):
        self.max_fail = max_fail
        self.window = window
        self.lock_base = lock_base
        self.lock_max = lock_max
        self._fails = {}      # ip -> [时间戳]
        self._until = {}      # ip -> 解锁时间戳
        self._strikes = {}    # ip -> 被锁次数

    def _prune(self, ip: str):
        now = time.time()
        self._fails[ip] = [t for t in self._fails.get(ip, [])
                           if now - t < self.window]

    def remaining(self, ip: str) -> int:
        """还需等待多少秒；0 表示未被锁。"""
        until = self._until.get(ip, 0)
        left = int(until - time.time())
        return max(0, left)

    def note_fail(self, ip: str) -> int:
        now = time.time()
        self._prune(ip)
        self._fails.setdefault(ip, []).append(now)
        if len(self._fails[ip]) >= self.max_fail:
            strikes = self._strikes.get(ip, 0) + 1
            self._strikes[ip] = strikes
            self._fails[ip] = []
            lock = min(self.lock_max, self.lock_base * (2 ** (strikes - 1)))
            self._until[ip] = now + lock
            return lock
        return 0

    def note_ok(self, ip: str):
        self._fails.pop(ip, None)
        self._until.pop(ip, None)
        self._strikes.pop(ip, None)

    def fails(self, ip: str) -> int:
        self._prune(ip)
        return len(self._fails.get(ip, []))


# ────────────────────────── 输入校验 ──────────────────────────
def validate_uid(raw) -> int:
    """B 站 UID：3~12 位纯数字。"""
    s = str(raw or "").strip()
    m = re.fullmatch(r"(\d{3,12})", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:space\.bilibili\.com/|mid=)(\d{3,12})", s)
    if m:
        return int(m.group(1))
    raise ValueError("UID 必须是 3~12 位数字")


def validate_gid(raw) -> str:
    """QQ 群 openid：只放行字母数字下划线短横线，防注入与路径穿越。"""
    s = str(raw or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,128}", s):
        raise ValueError("群标识格式不合法")
    return s


def validate_kind(raw) -> str:
    """校验订阅类型。

    ⚠️ 之前这里**硬编码**了 live/video/dynamic 三个，
    后来新增的 top_comment 不在表里 → 面板点「置顶评论」开关时
    后端直接拒绝 → 前端不看返回值直接刷新 → 表现就是
    "这个开关点了没反应"。类型表必须从 db.KINDS 动态取。
    """
    from db import KINDS, KIND_LABEL

    s = str(raw or "").strip().lower()
    alias = {}
    for k in KINDS:
        alias[k] = k
        lb = KIND_LABEL.get(k) or ""
        if lb:
            alias[lb] = k
    alias.update({"投稿": "video", "动态更新": "dynamic",
                  "置顶评论": "top_comment"})
    if s not in alias:
        raise ValueError("类型只能是 " + " / ".join(KINDS))
    return alias[s]


def resolve_in_base(path: str, base: str = None) -> str:
    """把路径限制在项目目录内，阻断 ../ 穿越。"""
    base = os.path.abspath(base or BASE)
    p = os.path.abspath(os.path.join(base, str(path)))
    if p != base and not p.startswith(base + os.sep):
        raise ValueError(f"路径越界：{path}")
    return p


def allowed_image_url(url: str) -> bool:
    """只放行 B 站自己的图床，避免把任意地址塞进卡片。"""
    if not url:
        return False
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    try:
        p = urlparse(u)
    except Exception:
        return False
    if p.scheme != "https":
        return False
    host = (p.hostname or "").lower()
    return any(host.endswith(s) for s in IMG_HOST_SUFFIXES)


# ────────────────────────── 脱敏与文件权限 ──────────────────────────
def redact(text: str) -> str:
    """把日志/输出里的密钥打码。"""
    s = str(text)
    return _SENSITIVE_RE.sub(lambda m: m.group(1) + "***", s)


def restrict_perms(path: str):
    """POSIX 下收紧为仅本人可读写。Windows 无 chmod 语义，静默跳过。"""
    if os.name != "posix":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ────────────────────────── 审计日志 ──────────────────────────
class Audit:
    """最近的安全事件，供面板展示；同时落盘 security.log。"""

    def __init__(self, path: str = None, keep: int = 200):
        self.path = path or paths.log_file("security.log")
        self.keep = keep
        self.items: List[dict] = []

    def log(self, event: str, ip: str = "", detail: str = "", level: str = "info"):
        item = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event, "ip": ip,
            "detail": redact(detail)[:200], "level": level,
        }
        self.items.append(item)
        self.items = self.items[-self.keep:]
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"{item['ts']} [{level}] {item['ip']} {event}"
                        f" {item['detail']}\n")
            restrict_perms(self.path)
        except OSError:
            pass

    def recent(self, n: int = 30) -> List[dict]:
        return list(reversed(self.items[-n:]))


# ────────────────────────── 绑定安全检查 ──────────────────────────
def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1", "")


def bind_risk(host: str, has_password: bool) -> Tuple[str, str]:
    """返回 (级别, 说明)。级别：ok / warn / fatal。"""
    if is_loopback(host):
        return "ok", "面板只监听本机，外部访问不到。"
    if not has_password:
        return "fatal", (
            f"面板要绑定到 {host}（对外可见），但没有设置访问口令，"
            "任何人都能改你的订阅和机器人凭据。"
        )
    return "warn", (
        f"面板绑定在 {host}，可对外访问。已设置口令，但仍建议改用 SSH 隧道，"
        "或至少用防火墙限制来源 IP。"
    )


def client_ip() -> str:
    """只信任直连地址；X-Forwarded-For 可被伪造，不采信。"""
    from flask import request
    try:
        return request.remote_addr or "unknown"
    except RuntimeError:      # 无请求上下文
        return "-"


# ────────────────────────── 命令行 ──────────────────────────
def security_report() -> List[Tuple[str, str, str]]:
    """返回 [(级别, 项目, 说明)]，级别：ok / warn / fatal。"""
    from cfgutil import creds_ready, load_cfg

    out = []
    cfg = load_cfg()

    host = str(cfg.get("web_host", "127.0.0.1"))
    pw = str(cfg.get("web_password", "") or "")
    lvl, msg = bind_risk(host, bool(pw))
    out.append((lvl, "面板暴露面", f"web_host={host}｜{msg}"))

    if pw:
        kind = "哈希存储（安全）" if is_hashed(pw) else "明文存储（建议改成哈希）"
        out.append(("ok" if is_hashed(pw) else "warn", "访问口令", kind))
    else:
        out.append(("warn", "访问口令", "未设置，任何人能打开面板（本机运行可接受）"))

    cfgp = paths.config_path()
    if os.path.exists(cfgp):
        restrict_perms(cfgp)          # 先收紧，再报告，避免误报
        mode = oct(os.stat(cfgp).st_mode & 0o777)
        ok = os.name != "posix" or (os.stat(cfgp).st_mode & 0o077) == 0
        if ok:
            out.append(("ok", "配置文件权限", f"config.yaml {mode}（仅本人可读写）"))
        elif os.name != "posix":
            out.append(("warn", "配置文件权限",
                        f"config.yaml {mode}（Windows 无 chmod 语义，"
                        "请自己确认文件没放在共享目录）"))
        else:
            out.append(("warn", "配置文件权限",
                        f"config.yaml {mode}（已尝试收紧但没生效，"
                        "当前文件系统可能不支持；手动执行 chmod 600 config.yaml）"))

    # ⚠️ 走 db.data_path_from_cfg()：以前这里自己拼 paths.state_path()，
    #    跟机器人算出来的不是同一个文件，自检说"正常"而面板其实是空的。
    try:
        from db import data_path_from_cfg as _dp
        df = _dp(cfg)
    except Exception:
        df = paths.state_path(str(cfg.get("data_file", "state.json")))
    home = ""
    try:
        import paths as _p
        home = os.path.abspath(_p.data_home())
    except Exception:
        pass
    out.append(("ok", "数据文件位置",
                ("在数据目录内" if home and df.startswith(home)
                 else "不在数据目录，请检查 config.yaml 的 data_file")))

    if creds_ready(cfg):
        out.append(("ok", "机器人凭据", "已填写（内容不会出现在日志里）"))
    else:
        out.append(("warn", "机器人凭据", "还没填，机器人无法登录"))

    skp = _secret_key_path()
    out.append(("ok" if os.path.exists(skp) else "warn", "会话密钥",
                "已生成 .webui_secret（600）" if os.path.exists(skp) else "尚未生成"))
    return out


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--hash":
        if len(args) < 2:
            print("用法：python security.py --hash 你的口令")
            return
        print("\n把下面这行写进 config.yaml：\n")
        print(f"web_password: \"{hash_password(args[1])}\"\n")
        return
    if args[0] == "--check":
        print("=" * 60)
        print(" 安全体检")
        print("=" * 60)
        icons = {"ok": "✅", "warn": "⚠️ ", "fatal": "❌"}
        worst = "ok"
        for level, name, desc in security_report():
            print(f"{icons.get(level, '·')} {name}：{desc}")
            if level == "fatal" or (level == "warn" and worst == "ok"):
                worst = level
        print("=" * 60)
        print(" 结论：" + {"ok": "没发现明显风险", "warn": "有可改进项",
                          "fatal": "存在高风险，请立即处理"}[worst])
        return
    print(f"未知参数：{args[0]}；用 --help 看用法")


if __name__ == "__main__":
    main()
