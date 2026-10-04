# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""凭据落盘混淆：config.yaml 里的 AppSecret 不以明文出现。

能防什么：
    别人随手打开你的 config.yaml、或者你不小心把配置文件发出去时，
    看不到真正的 AppSecret。

不能防什么（务必知道）：
    解密所需的密钥就在同目录下的 .machine_key 里。对方如果能读这个文件，
    同样能解出来。所以这是「防顺手看到 / 防误传」，不是真正的加密保护。
    真想万无一失，靠的是操作系统账号权限和不要把文件夹外传。

用法：
    from secretbox import protect_cfg, reveal_cfg
    cfg = reveal_cfg(cfg)     # 读出来：密文 → 明文
    cfg = protect_cfg(cfg)    # 写回去：明文 → 密文
"""
import base64
import hashlib
import os
import secrets

import paths

KEY_FILE = paths.key_file(".machine_key")
# 上一把好密钥的备份。作用：万一 .machine_key 被换掉（历史上因为发布包
# 误带该文件而发生过两次），还能靠它把老密文解出来并自动恢复。
PREV_FILE = paths.key_file(".machine_key.prev")
PREFIX = "enc1:"
# 需要保护的字段
FIELDS = ("secret", "bili_cookie")

_key_cache = None


def _write_file(path: str, data: bytes) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except OSError:
        return
    try:
        from security import restrict_perms
        restrict_perms(path)
    except Exception:
        pass


def _backup_prev(key: bytes) -> None:
    """把当前这把能用的密钥留一份备份（内容相同就不动）。"""
    try:
        if os.path.exists(PREV_FILE):
            with open(PREV_FILE, "rb") as f:
                if f.read().strip() == key:
                    return
        _write_file(PREV_FILE, key)
    except OSError:
        pass


def _load_key() -> bytes:
    """每台机器一把随机密钥，落在 .machine_key（权限收紧）。"""
    global _key_cache
    if _key_cache:
        return _key_cache
    env = os.environ.get("BILI_NOTIFY_KEY")
    if env:
        _key_cache = env.encode("utf-8")
        return _key_cache
    if os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, "rb") as f:
                data = f.read().strip()
            if data:
                _key_cache = data
                return _key_cache
        except OSError:
            pass
    key = secrets.token_bytes(32)
    try:
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    except OSError:
        pass
    from security import restrict_perms
    restrict_perms(KEY_FILE)
    _key_cache = key
    return key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(
            key + nonce + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:length])


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    if plain.startswith(PREFIX):
        return plain                      # 已经是密文
    key = _load_key()
    nonce = secrets.token_bytes(12)
    raw = plain.encode("utf-8")
    ks = _keystream(key, nonce, len(raw))
    body = bytes(a ^ b for a, b in zip(raw, ks))
    # 这把密钥开始保护数据了 → 留备份，将来被换掉才救得回来
    _backup_prev(key)
    return PREFIX + base64.b64encode(nonce + body).decode("ascii")


def _garbled(s: str) -> bool:
    """判断解密结果是不是乱码。

    ⚠️ XOR 流密码**永远不会因为"密钥不对"而抛异常**：
    拿错密钥去异或，照样解出一串字节，只是内容是乱码。
    所以光靠 try/except 抓不到"密钥被换"这种情况——
    程序会拿着乱码去请求，接口回一句「AppSecret 不对」，
    用户根本想不到是密钥文件的锅（v1.31.5 误带 .machine_key 就栽在这）。

    乱码特征：控制字符、无法解码的替换字符。
    """
    if not s:
        return False
    bad = 0
    for ch in s:
        o = ord(ch)
        if o < 0x20 or o == 0x7F or ch == "\ufffd":
            bad += 1
            if bad >= 2:
                return True
    return False


DECRYPT_FAILED = set()          # 本次运行中解不开/解出乱码的字段名


def _decrypt_with(key: bytes, value: str) -> str:
    """用指定的密钥解一次，不做乱码判断。"""
    try:
        blob = base64.b64decode(value[len(PREFIX):])
        nonce, body = blob[:12], blob[12:]
        ks = _keystream(key, nonce, len(body))
        return bytes(a ^ b for a, b in zip(body, ks)).decode("utf-8")
    except Exception:
        return ""


def _prev_key() -> bytes:
    """读上一把密钥的备份；跟当前这把相同就当没有（省一次无谓尝试）。"""
    cur = _load_key()
    try:
        if not os.path.exists(PREV_FILE):
            return b""
        with open(PREV_FILE, "rb") as f:
            data = f.read().strip()
        if not data or data == cur:
            return b""
        return data
    except OSError:
        return b""


def _restore_prev(key: bytes) -> None:
    """确认老密钥能解开老密文 → 把它恢复成当前密钥，并清掉失败标记。"""
    global _key_cache
    _key_cache = key
    _write_file(KEY_FILE, key)
    DECRYPT_FAILED.clear()
    try:
        RESTORED.append(True)
    except Exception:
        pass


RESTORED = []          # 本次运行是否靠备份密钥救回来过


def decrypt(value: str, field: str = "") -> str:
    if not value or not value.startswith(PREFIX):
        return value
    plain = _decrypt_with(_load_key(), value)
    # 解出乱码 = 密钥被换过。先拿备份的上一把密钥再试一次，
    # 能解开就自动恢复，用户不用重填。
    if not plain or _garbled(plain):
        prev = _prev_key()
        if prev:
            alt = _decrypt_with(prev, value)
            if alt and not _garbled(alt):
                _restore_prev(prev)
                plain = alt
    # 解出乱码 = 密钥不对。宁可当"没填"明确提示重填，
    # 也不要拿乱码去请求——那样只会得到一句莫名其妙的「AppSecret 不对」。
    if not plain or _garbled(plain):
        if field:
            DECRYPT_FAILED.add(field)
        return ""
    if field:
        DECRYPT_FAILED.discard(field)
    # 这把密钥确实能解开数据 → 留备份（仅当内容不同才写）
    _backup_prev(_load_key())
    return plain


def protect_cfg(cfg: dict) -> dict:
    """写盘前：把敏感字段变成密文。"""
    for f in FIELDS:
        v = cfg.get(f)
        if isinstance(v, str) and v and not v.startswith(PREFIX):
            cfg[f] = encrypt(v)
    return cfg


def reveal_cfg(cfg: dict) -> dict:
    """读出来后：把敏感字段还原成明文（只在内存里）。"""
    for f in FIELDS:
        v = cfg.get(f)
        if isinstance(v, str) and v.startswith(PREFIX):
            cfg[f] = decrypt(v, field=f)
    return cfg


def broken_fields() -> list:
    """哪些字段存的是密文，但用当前这把密钥解不开。

    典型场景：.machine_key 被换掉了（比如解压包时带进来一份别人的），
    于是 config.yaml 里的密文全都解不出来。
    """
    return sorted(DECRYPT_FAILED)


def broken_hint() -> str:
    """给用户看的一句话说明。"""
    if not DECRYPT_FAILED:
        return ""
    names = {"secret": "AppSecret", "bili_cookie": "B站 Cookie"}
    got = "、".join(names.get(f, f) for f in broken_fields())
    return (f"{got} 存的是密文，但当前这把密钥解不开"
            f"（.machine_key 可能被换过）。请在面板「凭据」里重新填写一次，"
            f"保存后会用现在的密钥重新加密。")


def status() -> dict:
    """给面板展示用：哪些字段是密文、密钥文件在不在。"""
    # 这里直接读文件而不是走 load_cfg：
    # load_cfg 会把密文还原成明文，而这个函数要展示的正是"哪些字段是密文"
    import yaml
    path = paths.config_path()
    raw = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception:
            raw = {}
    protected = {}
    broken = []
    for f in FIELDS:
        v = str(raw.get(f) or "")
        protected[f] = bool(v) and v.startswith(PREFIX)
        # 主动探测：存的是密文，但用当前这把密钥解不开吗？
        # （status() 直接读 yaml 不走 reveal，所以这里要自己试一次）
        if protected[f]:
            out = decrypt(v, field=f)
            if not out or _garbled(out):
                broken.append(f)
    return {
        "ok": True,
        "has_key": os.path.exists(KEY_FILE),
        "fields": protected,
        "broken": broken,
        "hint": broken_hint(),
        "note": "密钥存在同目录 .machine_key，能读该文件的人同样能解出，"
                "这是防误泄而非强加密。",
    }
