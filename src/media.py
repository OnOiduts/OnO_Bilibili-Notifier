# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
# -*- coding: utf-8 -*-
"""图片发送：下载到本地 → 上传 QQ 富媒体 → msg_type=7 发送。

为什么要换这条路：
  Markdown 里的 `![](url)` 是**客户端去加载**这个链接，
  QQ 要求链接提前在「开发设置 → 消息 URL 配置」报备，没报备就裂图。

  而富媒体上传是**平台服务器去下载**图片（url 参数），
  或者我们本地下载后用 base64 直传（file_data），
  两种都不走客户端渲染 → 不受消息 URL 报备限制。

流程：
  下载图片（本地缓存）→ 确保是 png/jpg → 上传拿 file_info → msg_type=7 发送
  file_info 有 ttl，按图片 URL 缓存复用，同一封面不重复上传。

配置项 image_send_mode：
  inline 图片内嵌在卡片里（![](url) 随卡片一起发，群里只有一条消息）
  local  本地下载 + base64 直传（图单独发一条、卡片里剥掉图）
  url    把图片 URL 交给平台下载转存（省流量，依赖平台能访问 B站）
  auto   先 local，失败退 url
  off    不发图片
"""
import base64
import hashlib
import json
import os
import re
import time

import aiohttp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
import paths as _paths
# 图片全部归到数据目录的 images/ 一个大类下，按用途分子目录。
# （以前是 cache/ 根 + cache/long + cache/tmp 三处，名字还看不出用途）
CACHE_DIR = _paths.image_dir()      # 兼容旧名：现在它就是 images/
# ⚠️ INDEX_PATH（图片索引缓存）已于 v2.2.0 删除：
#    读写它的 _load_index / _save_index 是零引用死代码，索引文件从没真正用过。
# 「最近用到过的图片」清单（只存 URL 和元信息，不存图本身）。
# 见 remember_recent 的说明：内嵌模式不落盘，光看文件数永远是 0。
RECENT_PATH = os.path.join(CACHE_DIR, "recent.json")
_RECENT_MAX = 30

# 单张图片上限（字节）。QQ 富媒体对图片有大小限制，超了就放弃图片。
# 手机端 QQ 对超大图很敏感（电脑端没事，手机端加载失败/显示异常），
# 所以上限收紧到 2MB，并且下载时就走 720 宽的 CDN 缩放版本。
# 官方上传返回的 file_info 有有效期；若接口没给 ttl，
# 就只缓存这么久（保守值，宁可重新传也不要用失效的）
_DEFAULT_FILE_INFO_TTL = 600

MAX_BYTES = 2 * 1024 * 1024

# file_info 缓存（ttl 留 15 分钟安全余量）
_INFO_CACHE: dict = {}
_SAFE_MARGIN = 900

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _ensure_dir():
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except Exception:
        pass


# images/avatars：长期保留（UP 头像等，反复复用）
# images/push   ：短期（推送封面，保留 TMP_TTL 秒后自动清）
LONG_DIR = _paths.image_sub("avatars")
TMP_DIR = _paths.image_sub("push")

# 短期图片保留多久（秒）。
# ⚠️ 以前是**发完立刻删**（upload_local 的 finally 里 drop）。
#    但同一张封面常常要连着发好几个群（每个群一次上传），
#    而且平台侧偶尔会晚一步才来取图 / 推送失败要重试 ——
#    本地文件已经没了，就只能重新下一遍（白白多一次 B 站请求，
#    还更容易撞风控）。现在改成发完先留 5 分钟，由 sweep_tmp 到点清。
#    可用 config.yaml 的 image_tmp_ttl 改（秒，0 = 发完立即删）。
TMP_TTL = 300


def tmp_ttl() -> int:
    """短期图片保留秒数（读配置，读不到用默认 300）。"""
    try:
        from cfgutil import load_cfg
        v = load_cfg().get("image_tmp_ttl", TMP_TTL)
        v = int(v)
        return v if v >= 0 else TMP_TTL
    except Exception:
        return TMP_TTL


def _dirs():
    for d in (CACHE_DIR, LONG_DIR, TMP_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass


def _key(url: str) -> str:
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:20]


def _suffix(data: bytes) -> str:
    return ".png" if _looks_png(data) else ".jpg"


def save_long(url: str, data: bytes) -> str:
    """长期图片（UP 头像）落盘，一直保留，反复复用。"""
    _dirs()
    p = os.path.join(LONG_DIR, _key(url) + _suffix(data))
    try:
        with open(p, "wb") as f:
            f.write(data)
        return p
    except Exception:
        return ""


def load_long(url: str) -> bytes:
    """读长期缓存。命中就省一次网络请求。"""
    for ext in (".jpg", ".png"):
        p = os.path.join(LONG_DIR, _key(url) + ext)
        try:
            with open(p, "rb") as f:
                return f.read()
        except FileNotFoundError:
            continue
        except Exception:
            return b""
    return b""


def save_tmp(data: bytes, url: str = "") -> str:
    """短期图片（推送封面）落盘，保留 tmp_ttl() 秒。

    ⚠️ 给了 url 就用 url 派生的**固定文件名**：同一张封面在保留期内
    可被反复复用（连发几个群 / 失败重试都不用重下）。不给 url 就退回
    时间戳命名（只写不读，纯粹留个副本）。
    """
    _dirs()
    name = (_key(url) if url
            else f"{int(time.time() * 1000)}_{os.getpid()}")
    p = os.path.join(TMP_DIR, name + _suffix(data))
    try:
        with open(p, "wb") as f:
            f.write(data)
        # 刷新 mtime：复用时保留期从"最后一次使用"重新算起
        try:
            os.utime(p, None)
        except Exception:
            pass
        return p
    except Exception:
        return ""


def _make_png(w: int = 480, h: int = 270, seed: int = 0) -> bytes:
    """纯标准库造一张 PNG 占位图（不依赖 Pillow，任何环境都能出图）。

    为什么不用 Pillow：它是可选依赖，没装的环境会直接退化成"没图"，
    而这里只是为了验证图片链路，画个渐变色块就够了。
    """
    import struct
    import zlib
    try:
        base = (seed % 5)
        rows = bytearray()
        for y in range(h):
            rows.append(0)                       # filter type 0
            for x in range(w):
                # 斜向渐变 + 一条明显的对角亮带，方便一眼看出图确实发了
                t = (x + y) * 255 // (w + h)
                band = 90 if abs(x * h - y * w) < (w * h) // 12 else 0
                r = (40 + t // 2 + band + base * 18) % 256
                g = (60 + t // 3 + band) % 256
                b = (110 + t // 2 + band + base * 30) % 256
                rows += bytes((r, g, b))
        raw = bytes(rows)

        def _chunk(tag: bytes, body: bytes) -> bytes:
            c = struct.pack(">I", len(body)) + tag + body
            return c + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n"
                + _chunk(b"IHDR", ihdr)
                + _chunk(b"IDAT", zlib.compress(raw, 9))
                + _chunk(b"IEND", b""))
    except Exception:
        return b""


# 占位图的伪 URL。用 .invalid（RFC 2606 保留域名，保证不会真去联网），
# 只作为短期缓存的 key —— upload_local 会先命中 load_tmp，不走网络。
PLACEHOLDER_HOST = "https://placeholder.invalid"


def local_placeholder(kind: str = "") -> str:
    """造一张本地占位图，返回能走正常发送流程的 URL。

    ⚠️ 为什么需要它：占位数据（没勾「用真实数据」）里 pic / cover /
       images 全是空，于是**测试卡片永远没图** —— 图片这条链路在测试里
       根本没被覆盖过，真出问题只有正式推送时才发现。
    ⚠️ 走的是和正式推送完全相同的 send_image 路径：先按 URL 派生文件名
       写进短期缓存，upload_local 直接命中 load_tmp，不依赖网络、
       不受图床防盗链影响。
    """
    u = "%s/test-%s.png" % (PLACEHOLDER_HOST, (kind or "image").strip() or "image")
    try:
        if not load_tmp(u):
            data = _make_png(seed=abs(hash(kind or "image")))
            if not data or not save_tmp(data, u):
                return ""
    except Exception:
        return ""
    return u


def load_tmp(url: str, max_age: int = 0) -> bytes:
    """读保留期内的短期缓存。命中就省一次 B 站请求。

    ⚠️ 过期（超过 max_age 秒）就当没命中，交给调用方重新下载。
       max_age=0（默认）→ 按 tmp_ttl()；max_age<0 → 一律判过期。
    """
    if not url:
        return b""
    if max_age < 0:
        # 负数 = 调用方想强制判过期（测试 / 想强制重下时用）
        return b""
    age = max_age if max_age > 0 else tmp_ttl()
    for ext in (".jpg", ".png"):
        p = os.path.join(TMP_DIR, _key(url) + ext)
        try:
            if age and (time.time() - os.path.getmtime(p)) > age:
                continue
            with open(p, "rb") as f:
                d = f.read()
            if not d:
                continue
            try:
                os.utime(p, None)     # 用一次就续一会儿
            except Exception:
                pass
            return d
        except FileNotFoundError:
            continue
        except Exception:
            return b""
    return b""


def drop(path: str):
    """删掉临时图片。"""
    if not path:
        return
    try:
        os.remove(path)
    except Exception:
        pass


_LAST_SWEEP = 0.0


def sweep_tmp(max_age=None, force: bool = False):
    """清扫短期图片。

    ⚠️ 两种语义，别搞混：
       max_age=None（默认）→ 按 tmp_ttl()（默认 300 秒 = 5 分钟）清过期的；
       max_age=0           → **全部清掉**（面板「清空缓存」用）。
    ⚠️ 每次发送都 listdir 一遍没必要，默认最多 60 秒扫一次；
       force=True 时才无条件扫（清空缓存 / 测试要用）。
    """
    global _LAST_SWEEP
    now = time.time()
    if max_age is None:
        age = tmp_ttl()
        if not force and (now - _LAST_SWEEP) < 60:
            return 0
    else:
        age = int(max_age)
        force = True
    _LAST_SWEEP = now
    n = 0
    try:
        for name in os.listdir(TMP_DIR):
            p = os.path.join(TMP_DIR, name)
            try:
                if age <= 0 or (now - os.path.getmtime(p)) > age:
                    os.remove(p)
                    n += 1
            except Exception:
                pass
    except Exception:
        pass
    return n


def purge_long():
    """清空长期缓存（换账号 / 想省空间时用）。"""
    try:
        for name in os.listdir(LONG_DIR):
            try:
                os.remove(os.path.join(LONG_DIR, name))
            except Exception:
                pass
    except Exception:
        pass


def cache_stats() -> dict:
    def _size(d):
        try:
            fs = [os.path.join(d, n) for n in os.listdir(d)]
            return len(fs), sum(os.path.getsize(f) for f in fs)
        except Exception:
            return 0, 0
    ln, lb = _size(LONG_DIR)
    tn, tb = _size(TMP_DIR)
    return {"long_count": ln, "long_bytes": lb,
            "tmp_count": tn, "tmp_bytes": tb}


def _load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, list) else default
    except Exception:
        return default


def _save_json(path: str, data):
    try:
        _ensure_dir()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def remember_recent(url: str, kind: str = "", name: str = ""):
    """记下「最近推送用到过哪些图」。

    ⚠️ 为什么需要它：内嵌模式（inline）的图片是留在卡片里交给客户端
       加载的，本地**根本不落盘** —— 于是媒体缓存页永远显示 0 张，
       用户看到的就是"动态/视频/直播的图片怎么一个都没获取到"。
       所以单独记一份清单：只存 URL 和元信息，不存图本身，
       面板就能列出"最近用到了哪些图、分别是什么类型"。
    """
    if not url:
        return
    items = _load_json(RECENT_PATH, [])
    items = [it for it in items
             if isinstance(it, dict) and it.get("url") != url]
    items.insert(0, {"url": url, "kind": kind or "",
                     "name": name or "", "ts": int(time.time())})
    _save_json(RECENT_PATH, items[:_RECENT_MAX])


def recent_list(limit: int = 12) -> list:
    """最近用到过的图片（新的在前）。"""
    out = [it for it in _load_json(RECENT_PATH, []) if isinstance(it, dict)]
    return out[:max(1, int(limit))]


def recent_hosts(limit: int = 8) -> list:
    """最近用到的图床域名（去重）。

    ⚠️ 手机端看不到的根因：QQ 手机端会校验外链图片的来源，
       而开放平台要求图片域名事先配好 —— 位置在
       「开放平台 → 机器人 → 开发 → 开发管理 → 消息URL配置」。
       面板把这个域名列出来，用户照着填就行，不用自己抓包猜。
    """
    hosts, seen = [], set()
    for it in _load_json(RECENT_PATH, []):
        if not isinstance(it, dict):
            continue
        h = ""
        try:
            from urllib.parse import urlparse
            h = (urlparse(it.get("url") or "").hostname or "").lower()
        except Exception:
            h = ""
        if h and h not in seen:
            seen.add(h)
            hosts.append(h)
    return hosts[:max(1, int(limit))]


def cached_file_info(url: str):
    """取缓存的 file_info，过期返回 None。"""
    k = _key(url)
    hit = _INFO_CACHE.get(k)
    if hit and hit.get("expire", 0) > time.time():
        return hit.get("file_info")
    if hit:
        _INFO_CACHE.pop(k, None)
    return None


def remember_file_info(url: str, file_info: str, ttl: int):
    """缓存 file_info。

    ⚠️ 这里有个真 bug：以前 ttl=0 被当成"长期有效"缓存 30 天，
    但官方 file_info **是有有效期的**（返回体里的 ttl 字段）。
    缓存过期后再用这个 file_info 发送会失败 → 图片发不出去 →
    退回卡片内 ![](url) → 电脑端能加载外链所以看得到，
    **手机端加载不了就完全不显示**（实测现象）。

    现在：ttl 已知就用真实值（留安全余量）；未知就只缓存 10 分钟
    （保守，宁可重新传也不要用失效的）。
    """
    k = _key(url)
    t = int(ttl or 0)
    if t > 0:
        exp = time.time() + max(60, t - _SAFE_MARGIN)
    else:
        exp = time.time() + _DEFAULT_FILE_INFO_TTL
    _INFO_CACHE[k] = {"file_info": file_info, "expire": exp}


def _looks_jpeg(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff"


def _looks_png(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def usable(data: bytes) -> bool:
    """QQ 图片只收 png/jjpg。webp/gif 传上去会失败，直接放弃更干净。"""
    return bool(data) and (_looks_jpeg(data) or _looks_png(data))


def jpeg_variant(url: str, max_w: int = 0) -> str:
    """把 B站图片 URL 转成 jpg + 限制宽高。

    两件事：
    1. **格式**：B站 CDN 的 webp 后缀换成 jpg 能取到内容，
       不用在本地转码（也就不需要 Pillow）
    2. **尺寸**：拿到原图再缩放会很大。B站原图动辄 1920×1080、好几 MB，
       **手机端 QQ 加载大图很容易失败/显示异常**，而电脑端没事 ——
       所以直接用 CDN 参数把图压到合适尺寸，体积小、手机友好。
    """
    if not url:
        return ""
    u = re.sub(r"@[^/@]*$", "", url)          # 先去掉已有的 @参数
    u = re.sub(r"\.(webp|gif)$", ".jpg", u, flags=re.I)
    if not re.search(r"\.(jpg|jpeg|png)$", u, flags=re.I):
        # 没后缀的直接加 .jpg（hdslb 支持）
        if "hdslb.com" in u:
            u = u + ".jpg"
    if max_w and "hdslb.com" in u:
        # CDN 缩放参数：宽 max_w，等比，_1c 表示裁剪居中
        u = u + f"@{max_w}w_{max_w}h_1c.jpg"
    return u


def mobile_variant(url: str) -> str:
    """手机友好的尺寸（720 宽）。"""
    return jpeg_variant(url, max_w=720)


async def fetch_image(url: str, timeout: int = 20) -> bytes:
    """下载图片到本地内存。带 Referer，否则 B站会返回占位图。"""
    if not url:
        return b""
    # 优先取手机友好的尺寸（体积小，手机端加载不会失败）
    target = mobile_variant(url) or jpeg_variant(url)
    headers = {
        "User-Agent": UA,
        "Referer": "https://www.bilibili.com/",
        "Accept": "image/jpeg,image/png,image/*,*/*",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(target, headers=headers,
                             timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status != 200:
                    return b""
                data = await r.read()
    except Exception:
        return b""
    if not data or len(data) > MAX_BYTES:
        return b""
    # 大小超限或格式不对都丢弃，宁可不发也不要发失败
    return data if usable(data) else b""


async def upload_by_url(qq, gid: str, url: str):
    """让平台自己去下载图片（url 模式）。返回 (file_info, ttl)。"""
    try:
        r = await qq.upload_group_file(
            gid, file_type=1, url=mobile_variant(url) or jpeg_variant(url))
        if not isinstance(r, dict):
            return "", 0
        return r.get("file_info") or "", int(r.get("ttl") or 0)
    except Exception:
        return "", 0


async def upload_local(qq, gid: str, url: str, keep: bool = False):
    """本地下载 + base64 直传。返回 (file_info, ttl)。

    keep=False（默认）：一次性封面，落盘后**保留 tmp_ttl() 秒**（默认 5 分钟），
       由 sweep_tmp 到点清。同一张封面连发几个群 / 失败重试都能复用本地文件，
       不用反复去 B 站下载。
    keep=True：UP 头像这类会反复用的，长期保留。
    """
    data = b""
    if keep:
        data = load_long(url)          # 长期缓存命中就别下第二遍
    if not data:
        data = load_tmp(url)           # 5 分钟内的短期缓存也算命中
    if not data:
        data = await fetch_image(url)
        if data and keep:
            save_long(url, data)
    if not data:
        return "", 0

    if not keep:
        # ⚠️ 按 url 命名落盘，保留 5 分钟：连发多个群 / 重试都复用这一份。
        #    以前是"发完立刻删"，结果每发一个群都要重下一次图。
        #    ttl 配成 0 才退回"发完即删"的旧行为。
        if tmp_ttl() > 0:
            save_tmp(data, url)        # 保留 5 分钟，可复用
        # ttl 配 0 = 退回旧行为：不落盘
    try:
        b64 = base64.b64encode(data).decode("ascii")
        r = await qq.upload_group_file(gid, file_type=1, file_data=b64)
        if not isinstance(r, dict):
            return "", 0
        return r.get("file_info") or "", int(r.get("ttl") or 0)
    except Exception:
        # ⚠️ 必须返回**二元组**：调用方写的是 fi, ttl = ... ，
        #    返回单个 "" 会在解包时抛 ValueError，把整条推送带崩。
        return "", 0
    finally:
        try:
            sweep_tmp()               # 顺手把过期的清掉（内部有 60s 节流）
        except Exception:
            pass


async def send_image(qq, gid: str, url: str, mode: str = "local",
                     msg_id: str = None, keep: bool = False) -> bool:
    """发送一张图片。成功返回 True。

    会先查 file_info 缓存，同一封面在 ttl 内不重复上传。
    """
    if not url or mode == "off":
        return False
    hit = cached_file_info(url)
    if hit:
        try:
            await qq.send_media(gid, hit, msg_id=msg_id)
            return True
        except Exception:
            remember_file_info(url, "", 0)     # 缓存失效，下面重新传
            _INFO_CACHE.pop(_key(url), None)

    fi, ttl = "", 0
    if mode == "url":
        fi, ttl = await upload_by_url(qq, gid, url)
    elif mode == "auto":
        fi, ttl = await upload_local(qq, gid, url, keep=keep)
        if not fi:
            fi, ttl = await upload_by_url(qq, gid, url)
    else:                                       # local（默认）
        fi, ttl = await upload_local(qq, gid, url, keep=keep)
        if not fi:
            # 本地取不到图就退一步让平台拉，别轻易放弃
            fi, ttl = await upload_by_url(qq, gid, url)
    if not fi:
        return False
    # 用官方给的 ttl 缓存；没给就只缓存 10 分钟，
    # 避免用失效的 file_info 导致图片发不出去（手机端直接不显示）
    remember_file_info(url, fi, ttl)
    try:
        await qq.send_media(gid, fi, msg_id=msg_id)
        return True
    except Exception:
        return False


def probe_hosts(timeout: float = 6.0) -> list:
    """逐个探测图床域名「不带 Referer 时能不能取到图」。

    ⚠️ 为什么测这个：QQ 是**服务器侧去拉图**再转存的（手机端尤其如此），
       那个请求里不会有 B站的 Referer。而不少图床开了防盗链：
       带 Referer 能看、不带就 403 —— 表现刚好就是用户反馈的
       「电脑上看得见、手机上什么都没有」。本地根本复现不了，
       只能按平台的取法模拟一次，把状态码直接摆给用户。

    只做 HEAD（取不到就退一步 Range GET），不下载图片本体。
    """
    from urllib.parse import urlparse
    import urllib.request
    import urllib.error

    sample = {}
    for it in _load_json(RECENT_PATH, []):
        if not isinstance(it, dict):
            continue
        u = str(it.get("url") or "")
        if not u.startswith("https://"):
            continue
        try:
            h = (urlparse(u).hostname or "").lower()
        except Exception:
            continue
        if h and h not in sample:
            sample[h] = u

    out = []
    for host in recent_hosts(8):
        url = sample.get(host, "")
        if not url:
            out.append({"host": host, "ok": None, "status": 0,
                        "note": "没有可探测的图片链接"})
            continue
        req = urllib.request.Request(url, method="HEAD")
        # 故意**不设** Referer：模拟开放平台拉图的真实请求
        req.add_header("User-Agent", "Mozilla/5.0 (bot-image-probe)")
        code, note = 0, ""
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code = int(getattr(r, "status", 0) or 0)
                ctype = (r.headers.get("Content-Type") or "").split(";")[0]
                note = f"可取（{code}）" + (f" ・ {ctype}" if ctype else "")
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            note = ("被拒绝（403 防盗链）" if code == 403
                    else f"取不到（{code}）")
        except Exception as e:
            note = f"连不上（{type(e).__name__}）"
        out.append({"host": host, "ok": (code == 200),
                    "status": code, "note": note})
    return out
