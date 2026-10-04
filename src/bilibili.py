# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""
B 站数据层：直播间状态 / 最新投稿视频 / 最新动态。

三个能力对应三个接口：
  直播 → https://api.live.bilibili.com/room/v1/Room/get_info
  投稿 → https://api.bilibili.com/x/space/wbi/arc/search   （需要 WBI 签名）
  动态 → https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space （需要 buvid3）
"""
import asyncio
import hashlib
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# 一条动态最多取几张图（B站上限就是 9）
DYN_IMG_MAX = 9

# 从文本/跳转链接里抠 BV 号。新版投稿动态（major.type=opus）**不给**
# archive 卡片，只把视频地址塞在 jump_url 里 —— 不抠出来，"投稿提醒 +
# 投稿动态"就只能靠时间兜底去重，抠出来就能精确比对。
_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")

# 直播间号。⚠️ 和 BV 号是同一个坑：新版开播动态常常**不给**
# live_rcmd 卡片，只把直播间地址塞在 opus 的 jump_url 里。抠不出房间号，
# 那条动态就会被当成普通图文动态推出去，而且房间号去重也失效 ——
# 「开播提醒 + 开播动态」连着推两条（用户原话："这种动态就是直播中的
# 直播推送"）。
_ROOM_URL_RE = re.compile(
    r"live\.bilibili\.com/(?:h5/|blanc/)?(\d{1,12})")


def _room_from_src(value) -> int:
    """从链接 / 文本里抠直播间号，取不到返回 0。"""
    m = _ROOM_URL_RE.search(str(value or ""))
    if not m:
        return 0
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return 0

import aiohttp

# B 站所有时间字段都是北京时间（UTC+8），不是本地时区也不是 UTC。
_CST = timezone(timedelta(hours=8))


def _to_epoch(v) -> int:
    """把接口给的"时间"统一成 Unix 秒；取不到/不是时间就返回 0。

    ⚠️ 房间接口的 live_time **不是** Unix 秒，而是
       "0000-00-00 00:00:00" / "2026-09-22 00:13:26" 这种字符串。
       没在播时官方给的是前一种 —— 它是**非空字符串**，所以
       `int(d.get("live_time") or 0)` 的 `or 0` 根本兜不住，
       直接 ValueError: invalid literal for int() ... '0000-00-00 00:00:00'。
       这个异常不是 BiliError，bot._check_live 只 catch BiliError，
       于是**每轮直播检测都失败、开播下播一条都不推**，还不报错。
    """
    if v is None or v == "" or isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        n = int(v)
        if n > 10_000_000_000:      # 毫秒时间戳（13 位）收敛到秒
            n //= 1000
        return n if n > 0 else 0
    s = str(v).strip()
    if not s:
        return 0
    # 纯数字：秒 / 毫秒（13 位）都认
    if s.isdigit():
        n = int(s)
        if n > 10_000_000_000:
            n //= 1000
        return n if n > 0 else 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        # 0000-00-00 / 1970 之前的都是"没有开播时间"，不能当真
        if dt.year < 2000:
            return 0
        return int(dt.replace(tzinfo=_CST).timestamp())
    return 0

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

ROOM_INFO = "https://api.live.bilibili.com/room/v1/Room/get_info"
# 用 mid 反查直播间号（get_up 的 live_room 有时不给 roomid，
# 拿不到会导致直播提醒永远不触发且不报错）
ROOM_BY_MID = "https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld"
MASTER_INFO = "https://api.live.bilibili.com/live_user/v1/Master/info"
USER_INFO = "https://api.bilibili.com/x/web-interface/card"
NAV = "https://api.bilibili.com/x/web-interface/nav"
WBI_ARC_SEARCH = "https://api.bilibili.com/x/space/wbi/arc/search"

# ── 视频合集（UGC season）──
# 合集 ≠ 视频列表（series）：合集在创作中心管理，URL 形如
#   https://space.bilibili.com/{mid}/lists/{season_id}?type=season
# 视频列表形如 ...?type=series ，是另一个东西，别混。
SEASON_ARCHIVES = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
# 旧接口无鉴权，作为新接口失败时的降级
SEASON_ARCHIVES_OLD = "https://api.bilibili.com/x/polymer/space/seasons_archives_list"
# 一个 UP 主的合集 / 视频列表**清单**（自检「合集检测」用来挑"最近更新的合集"）
SEASON_SERIES_LIST = "https://api.bilibili.com/x/polymer/web-space/seasons_series_list"
VIEW_API = "https://api.bilibili.com/x/web-interface/view"
DYNAMIC_SPACE = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
DYNAMIC_OLD = "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/space_history"
COMMENT_URL = "https://api.bilibili.com/x/v2/reply"   # 评论区（type=17 是动态）
SPI = "https://api.bilibili.com/x/frontend/finger/spi"

# WBI 签名用的固定重排表（B 站前端常量）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


NEED_LOGIN_HINT = (
    "B 站风控拦截（code=-352）：这个接口现在要求登录态。\n"
    "解决办法：打开管理面板 →「🔑 B 站登录」→ 扫码登录，或运行 python bili_login.py"
)


class BiliError(Exception):
    pass


class BiliTransientError(BiliError):
    """B 站侧**临时**故障：服务超时(-504)、网关错(-502/-503)、网络超时等。

    与"合集没了"这类永久错误必须分开：
      - 临时的 → 退避重试、并让自适应轮询放慢，过一会儿自己就好
      - 永久的 → 重试也没用，要提示用户去面板处理

    ⚠️ 以前 -504 被当成"合集没了"，日志直接甩给用户三条猜测
    （①UP 主删了 ②ID 填错 ③uid 对不上）—— 其实只是 B 站那一下超时了，
    照着提示去删订阅反而把好端端的订阅弄没了。
    """


def is_transient(e) -> bool:
    """判断一个错误是不是**临时**故障（超时 / 网关错 / 网络抖动）。

    优先看类型（BiliTransientError），类型丢了就退回看错误码文本 ——
    有些失败是从别的路径冒上来的（缓存层、旧接口封装），只带消息不带类型，
    这时不能因为"认不出"就把它当成永久错误。
    """
    if isinstance(e, BiliTransientError):
        return True
    s = str(e) or ""
    if any(f"code={c}" in s for c in (-504, -500, -502, -503)):
        return True
    return ("网络请求失败" in s) or ("Timeout" in s) or ("timeout" in s)


class SeasonGoneError(BiliError):
    """合集取不到（已删除 / ID 填错 / uid 对不上）。

    单独一个类型，是为了让上层能把它和"网络抖动"区分开：
    网络抖动应该重试，合集没了再怎么重试也不会有，
    应该在面板上提示用户去重新查询，而不是每轮刷一条日志。

    ⚠️ 只有**确认是永久错误**才能抛它。临时故障（-504 超时等）
    一律抛 BiliTransientError，别让用户白折腾。
    """


@dataclass
class Up:
    """一个 UP 主的基本信息。"""
    uid: int
    uname: str = ""
    face: str = ""
    room_id: int = 0
    # 直播间短号。B 站直播间有两个号：
    #   room_id  = 真实房间号（长号，永远存在）
    #   short_id = 短号（可选，很多主播没有；官方接口里为 0 表示没有）
    # 两个号都能进同一个直播间。
    short_room_id: int = 0


# 直播状态码的中文说法。
# ⚠️ 用户明确要求：不要显示 0/1/2 这种原始数字，直接说人话；
#    且 1 要说「已开播」而不是「直播中」——跟 0「未开播」正好成对。
LIVE_STATUS_CN = {
    0: "未开播",
    1: "已开播",
    2: "轮播中",
}
# 兼容字符串键（日志里的 live_status=1 是文本）
LIVE_STATUS_CN_STR = {str(k): v for k, v in LIVE_STATUS_CN.items()}


def live_status_cn(status) -> str:
    """把直播状态码说成人话：0→未开播 / 1→已开播 / 2→轮播中。

    ⚠️ 还要能识别**各种历史写法**：
      - 数字 1、字符串 "1"
      - 老版本存下来的 "1（直播中）"、"1(已开播)"
      - 已经翻好的 "直播中" / "已开播" / "未开播" / "下播"
    这样数据库里存着什么都能显示成中文，不用等下次刷新。
    认不出来就原样返回，绝不瞎编一个状态。
    """
    s = str(status if status is not None else "").strip()
    if not s:
        return ""
    # 纯数字（含带括号后缀的 "1（直播中）"）
    if s in LIVE_STATUS_CN_STR:
        return LIVE_STATUS_CN_STR[s]
    m = re.match(r"^(-?\d+)", s)
    if m:
        return LIVE_STATUS_CN_STR.get(m.group(1), s)
    # 已经是中文的
    for cn in ("已开播", "直播中", "未开播", "轮播中", "下播", "未直播"):
        if cn in s:
            return "未开播" if cn in ("下播", "未开播", "未直播") else \
                   ("已开播" if cn in ("已开播", "直播中") else "轮播中")
    return s


@dataclass
class LiveInfo:
    room_id: int
    live_status: int          # 0 未开播 / 1 已开播 / 2 轮播中
    title: str = ""
    area: str = ""
    online: int = 0
    cover: str = ""
    short_room_id: int = 0    # 短号，没有则为 0
    # 开播时间戳（Unix 秒），B站房间接口的 live_time 字段。
    # ⚠️ 下播时这个字段会变成 0，所以"本场直播时长"必须在**开播时**
    #    就记下来（见 bot._check_live 的 _live_since），不能等下播再取。
    live_time: int = 0
    # 本场直播持续了多少秒。由 bot 在下播时算好填进来，
    # 渲染层只负责显示（下播卡片不再给"进入直播间"按钮，
    # 改为显示时长 —— 人都下播了，给个进不去的直播间链接没意义）。
    duration: int = 0

    @property
    def is_live(self) -> bool:
        return self.live_status == 1

    @property
    def url(self) -> str:
        return f"https://live.bilibili.com/{self.room_id}"


@dataclass
class VideoInfo:
    bvid: str
    title: str = ""
    pic: str = ""
    desc: str = ""
    pubdate: int = 0

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}"


@dataclass
class SeasonVideo:
    """合集里的一个视频。"""
    bvid: str = ""
    aid: int = 0
    title: str = ""
    pic: str = ""
    pubdate: int = 0


@dataclass
class SeasonInfo:
    """一个合集的当前快照。"""
    season_id: int = 0
    uid: int = 0
    title: str = ""          # 合集名
    cover: str = ""
    total: int = 0           # 合集内视频总数
    videos: list = field(default_factory=list)   # [SeasonVideo]

    @property
    def bvids(self) -> list:
        return [v.bvid for v in self.videos if v.bvid]

    @property
    def url(self) -> str:
        return (f"https://space.bilibili.com/{self.uid}/lists/{self.season_id}"
                f"?type=season")


def parse_season_ref(raw: str) -> tuple:
    """从用户粘贴的内容里解析 (uid, season_id)。

    支持三种写法（用户怎么方便怎么来）：
      · 完整链接 https://space.bilibili.com/123/lists/456?type=season
      · 旧链接   https://space.bilibili.com/123/channel/collectiondetail?sid=456
      · 纯数字   456（只给 season_id，uid 留 0，由调用方补）

    ⚠️ 只认 type=season（合集）。type=series 是"视频列表"，
    是另一个东西，接口不同，不能混用。
    """
    s = str(raw or "").strip()
    if not s:
        return (0, 0)
    import re as _re
    # 新链接：/lists/{sid}  前面是 /{mid}/
    m = _re.search(r"space\.bilibili\.com/(\d+)/lists/(\d+)", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    # 旧链接：collectiondetail?sid=xxx
    m = _re.search(r"space\.bilibili\.com/(\d+)/channel/collectiondetail\?sid=(\d+)", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    # 只有 sid 的旧链接
    m = _re.search(r"collectiondetail\?sid=(\d+)", s)
    if m:
        return (0, int(m.group(1)))
    # 两个数字：mid season_id
    m = _re.search(r"(\d+)[\s,/]+(\d+)", s)
    if m and "type=series" not in s:
        return (int(m.group(1)), int(m.group(2)))
    # 单个数字：当作 season_id
    m = _re.fullmatch(r"(\d+)", s)
    if m:
        return (0, int(m.group(1)))
    return (0, 0)


@dataclass
class SeasonPush:
    """待推送的"合集新增视频"，带好展示需要的全部信息。"""
    bvid: str = ""
    title: str = ""
    pic: str = ""
    season_title: str = ""
    section: str = ""          # 小节名，可能拿不到（那就留空）

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}"


@dataclass
class DynamicInfo:
    dyn_id: str = ""
    title: str = ""
    text: str = ""
    images: List[str] = field(default_factory=list)
    pinned: bool = False        # 是否置顶动态（置顶的不会作为"新动态"推送）
    rid: str = ""               # 评论区的 oid（和 dyn_id 不是一回事）
    # ⚠️ 评论区类型不是固定的 17！官方文档与实测：
    #   纯文字 / 转发 / 站内分享 → type=17，oid=dynamic_id
    #   带图动态（相簿）        → type=11，oid=rid
    # polymer 的 basic 里直接给了 comment_type / comment_id_str，
    # 用它们最准。取不到再按上面的规则猜。
    comment_type: int = 0       # 评论区类型（11 或 17）
    comment_id_str: str = ""    # 评论区 id
    # 动态主体的类型（仅作诊断展示，不用于去重判定）：
    #   live    = 开播卡片动态（B站开播时自动发，下播后删掉）
    #   video   = 投稿动态（发视频时自动带一条）
    #   reserve = 直播预约（UP 主动发的预告）
    #   normal  = 普通图文/文字动态
    major_type: str = "normal"

    # ⚠️ 去重判定的真正依据是**指向的内容**，不是类型：
    #   开播后 B 站自动发的那条动态，里面指向的正是刚开播的直播间；
    #   投稿后自动发的那条，里面指向的正是刚投的视频。
    #   所以只要"动态指向的目标 == 刚推送过的目标"就静默。
    ref_room_id: int = 0   # 动态里指向的直播间号（live_rcmd 卡片）
    ref_bvid: str = ""     # 动态里指向的视频 BV 号（archive 卡片）
    # 发布时间（Unix 秒）。⚠️ 判定"是不是新内容"必须看它，不能只比 ID：
    # UP 主删掉最新那条动态后，接口返回的最新动态会退回成**更早**的一条，
    # ID 变了但时间是旧的——只比 ID 就会把旧动态当成新动态再推一遍。
    # 取不到时留 0，调用方按"无法判定"处理。
    pubdate: int = 0

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/opus/{self.dyn_id}" if self.dyn_id else ""


class BiliClient:
    """带限流、重试、WBI 签名与 buvid3 自动获取的 B 站客户端。"""

    def __init__(self, cookie: str = "", timeout: int = 10, min_interval: float = 2.0):
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        self._min_interval = min_interval
        self._last: Dict[str, float] = {}
        self._risk_hits = 0        # 累计撞风控次数
        self._risk_at = 0.0        # 上次撞风控的时间戳
        self._auth_hits = 0        # 累计"登录态失效/被拦"次数
        self._auth_at = 0.0        # 上次出现认证类失败的时间
        self._auth_bad = False     # 当前是不是处于"取不到"状态
        self.cookie = (cookie or "").strip()
        self._buvid3: str = ""
        self._wbi_key: Optional[tuple] = None   # (img_key, sub_key)
        self._wbi_key_ts: float = 0

    # ---------- 基础设施 ----------
    def _headers(self, referer: str = "https://www.bilibili.com/") -> dict:
        h = {
            "User-Agent": UA,
            "Referer": referer,
            "Accept": "application/json, text/plain, */*",
        }
        cookies = []
        if self._buvid3:
            cookies.append(f"buvid3={self._buvid3}")
        if self.cookie:
            cookies.append(self.cookie)
        if cookies:
            h["Cookie"] = "; ".join(cookies)
        return h

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ---------- 风控计数 ----------
    # 自适应轮询要靠它判断"是不是撞到风控了"：
    # -412 被拦截 / -799 请求过频 / -509 请求过于频繁。
    RISK_CODES = (-412, -799, -509)

    # B 站侧**临时**故障：服务调用超时 / 网关错 / 服务不可用。
    # 这些不是"请求太猛被拦"，而是对面那一下没扛住 —— 重试通常就好，
    # 但也要让自适应轮询放慢（继续猛打只会让对面更超时）。
    TRANSIENT_CODES = (-504, -500, -502, -503)

    # HTTP 状态码：这些先重试再放弃（不含 403，那是明确的拒绝）
    RETRY_STATUS = (404, 429, 500, 502, 503, 504)

    # 登录态 / 权限类：接口明确要求登录，或被 WAF 直接拦下。
    # -352 = 需要登录态；-403 = 访问权限不足（绝大多数也是没登录）。
    AUTH_CODES = (-352, -403)
    # HTTP 层同理：412 是 B 站 WAF 的经典拦截码，403 是明确的拒绝。
    AUTH_STATUS = (403, 412)

    def _note_auth(self, reason: str = ""):
        """记一次"登录态失效/被拦"。

        ⚠️ 与 _note_risk 分开：风控放慢就有救，认证放慢没用（得重新登录）。
        但两者都该让轮询别再加速 —— 否则会一路冲到最快档猛撞墙。
        """
        self._auth_hits += 1
        self._auth_at = time.monotonic()
        self._auth_bad = True
        if reason:
            self._auth_reason = reason

    def _clear_auth(self):
        """任一接口正常返回 → 登录态没问题了，解除"取不到"状态。

        不这样做的话，扫码登录成功后轮询还停在最慢档，用户会以为
        "登录了也没变快"。
        """
        if getattr(self, "_auth_bad", False):
            self._auth_bad = False
            self._auth_at = 0.0
            self._auth_reason = ""

    def auth_bad(self) -> bool:
        return bool(getattr(self, "_auth_bad", False))

    def auth_reason(self) -> str:
        return getattr(self, "_auth_reason", "") or ""

    def since_auth(self) -> float:
        """距上次认证类失败多少秒；从没失败过返回很大的数。"""
        at = getattr(self, "_auth_at", 0.0) or 0.0
        if not at:
            return 1e9
        return time.monotonic() - at

    @property
    def auth_hits(self) -> int:
        return self._auth_hits

    def _note_risk(self, reason: str = ""):
        # reason 只是为了让日志说清楚到底是"被拦了"还是"对面超时了" ——
        # 以前一律显示"撞到 B 站风控"，而 -504 其实是对面没扛住，
        # 提示得不准会让用户以为是自己请求太猛，去调大间隔，反而更慢。
        self._risk_hits += 1
        self._risk_at = time.monotonic()
        if reason:
            self._risk_reason = reason

    def risk_reason(self) -> str:
        return getattr(self, "_risk_reason", "") or ""

    @property
    def risk_hits(self) -> int:
        return self._risk_hits

    def since_risk(self) -> float:
        """距上次风控多少秒；从没风控过返回很大的数。"""
        if not self._risk_at:
            return 1e9
        return time.monotonic() - self._risk_at

    async def _throttle(self, key: str):
        last = self._last.get(key, 0.0)
        wait = self._min_interval - (time.monotonic() - last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last[key] = time.monotonic()

    async def _get(self, url: str, params: dict, referer: str = "https://www.bilibili.com/",
                   retry: int = 2) -> dict:
        session = await self._session_get()
        last_err = None
        for attempt in range(retry + 1):
            try:
                async with session.get(
                    url, params=params, headers=self._headers(referer)
                ) as resp:
                    if resp.status != 200:
                        # 429/5xx 和偶发 404 都先退避重试：B站 WAF 与 CDN
                        # 节点会偶发返回这些码，直接抛会让"一次抖动"变成
                        # 一整轮失败。重试耗尽才真正失败。
                        if resp.status in self.RETRY_STATUS and attempt < retry:
                            self._note_risk(f"HTTP {resp.status}")
                            await asyncio.sleep(1.5 * (attempt + 1))
                            continue
                        # ⚠️ 403/412 是 B站 WAF 的经典拒绝码，基本等同于
                        #    "这段登录态它不认"。以前记都不记，于是面板那边
                        #    只看 config 里有没有 SESSDATA，照样显示「已登录」。
                        if resp.status in self.AUTH_STATUS:
                            self._note_auth(f"HTTP {resp.status}")
                        raise BiliError(f"HTTP {resp.status}")
                    data = await resp.json(content_type=None)
                code = data.get("code")
                if code == 0:
                    # 任一接口正常返回 → 登录态是好的，解除"被拦"状态
                    self._clear_auth()
                    return data
                # -412 被拦截 / -799 请求过频，退避重试
                if code in self.RISK_CODES:
                    self._note_risk(f"code={code}")
                    if attempt < retry:
                        await asyncio.sleep(2 ** attempt * 2)
                        continue
                # ⚠️ -352 明确要求登录态、-403 访问权限不足（绝大多数也是
                #    登录态没被认）—— 都记一次"认证类失败"，让面板能显示
                #    「B站 说未登录」，而不是照着本地 cookie 显示已登录。
                if code in self.AUTH_CODES:
                    self._note_auth(f"code={code}")
                if code == -352:
                    raise BiliError(NEED_LOGIN_HINT)
                # -504 服务调用超时 / -502 -503 网关错：对面那一下没扛住，
                # 退避重试。也记一次软风控，让自适应轮询放慢 ——
                # 继续猛打只会让对面更超时。
                if code in self.TRANSIENT_CODES:
                    self._note_risk(f"code={code}")
                    if attempt < retry:
                        await asyncio.sleep(2 ** attempt * 2)
                        continue
                    raise BiliTransientError(
                        f"code={code} msg={data.get('message')}")
                raise BiliError(f"code={code} msg={data.get('message')}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                if attempt < retry:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
        raise BiliTransientError(f"网络请求失败: {last_err}")

    def has_login(self) -> bool:
        """cookie 里是否有真正的登录态（SESSDATA）。"""
        return "SESSDATA=" in (self.cookie or "")

    async def ensure_buvid3(self):
        """动态接口需要 buvid3。cookie 里带了就直接用，否则自动取一个。"""
        if "buvid3=" in (self.cookie or ""):
            return
        if self._buvid3:
            return
        try:
            data = await self._get(SPI, {})
            self._buvid3 = (data.get("data") or {}).get("b_3") or ""
        except Exception:
            self._buvid3 = ""

    # ---------- WBI 签名 ----------
    async def _get_wbi_key(self) -> tuple:
        """img_key / sub_key 一天变一次，缓存 1 小时即可。"""
        if self._wbi_key and time.time() - self._wbi_key_ts < 3600:
            return self._wbi_key
        data = await self._get(NAV, {})
        wbi = (data.get("data") or {}).get("wbi_img") or {}
        img_key = _basename_noext(wbi.get("img_url", ""))
        sub_key = _basename_noext(wbi.get("sub_url", ""))
        if not img_key or not sub_key:
            raise BiliError("获取 WBI 密钥失败")
        self._wbi_key = (img_key, sub_key)
        self._wbi_key_ts = time.time()
        return self._wbi_key

    @staticmethod
    def _mixin_key(img_key: str, sub_key: str) -> str:
        raw = img_key + sub_key
        return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB if i < len(raw))[:32]

    async def _wbi_sign(self, params: dict) -> dict:
        img_key, sub_key = await self._get_wbi_key()
        mk = self._mixin_key(img_key, sub_key)
        params = dict(params)
        params["wts"] = int(time.time())
        # 参数按 key 排序，value 去掉 !'()* 这几个字符
        items = sorted(params.items())
        query = urllib.parse.urlencode(
            [(k, re.sub(r"[!'()*]", "", str(v))) for k, v in items]
        )
        params["w_rid"] = hashlib.md5((query + mk).encode()).hexdigest()
        return params

    # ---------- 业务接口 ----------
    async def get_up(self, uid: int) -> Up:
        """拿昵称、头像、直播间号。

        ⚠️ 直播间号**不能只信 card 接口**。实测：UP 主当前没在直播时，
        card 返回的 `live_room` 经常是 null 或干脆没有这个字段，
        于是 room_id 被当成 0 → 面板提示"该 UP 主没有直播间"、
        自动不勾「直播」→ 直播提醒永远不触发。
        但人家**有直播间，只是没在播**。

        所以这里三级取：card → 直播接口反查 → 主播信息接口反查。
        """
        data = await self._get(USER_INFO, {"mid": uid, "photo": "false"})
        d = (data.get("data") or {})
        card = d.get("card") or {}
        live = d.get("live_room") or {}
        room_id = int(live.get("roomid") or 0)
        # card 接口的 live_room 里也带短号
        short_id = int(live.get("short_id") or 0)

        if not room_id:
            try:
                got = await self.get_room_id_by_mid(uid)
                room_id = got[0]
                short_id = short_id or got[1]
            except Exception:
                room_id = 0
        if not room_id:
            room_id = await self._room_id_from_master(uid)

        return Up(
            uid=uid,
            uname=card.get("name") or "",
            face=card.get("face") or "",
            room_id=int(room_id or 0),
            short_room_id=int(short_id or 0),
        )

    async def _room_id_from_master(self, uid: int) -> int:
        """最后兜底：从主播信息接口取房间号。"""
        try:
            data = await self._get(MASTER_INFO, {"uid": uid},
                                   referer=f"https://space.bilibili.com/{uid}")
        except Exception:
            return 0
        d = data.get("data") or {}
        info = d.get("info") or d
        return int(info.get("room_id") or info.get("roomid") or 0)

    async def get_room_id_by_mid(self, uid: int) -> tuple:
        """用 UID 反查直播间号，返回 **(长号, 短号)**；查不到是 (0, 0)。

        ⚠️ 返回值从 int 改成了 tuple。老调用方写成
        `room_id = await get_room_id_by_mid(uid)` 的话会拿到一个元组，
        后面拼 URL 就变成 "https://live.bilibili.com/(123, 0)"。
        所以全项目所有调用处都改成了解包。
        """
        try:
            data = await self._get(ROOM_BY_MID, {"mid": uid},
                                   referer=f"https://space.bilibili.com/{uid}")
        except BiliError:
            return (0, 0)
        d = data.get("data") or {}
        return (int(d.get("room_id") or d.get("roomid") or 0),
                int(d.get("short_id") or 0))

    async def get_live(self, room_id: int) -> LiveInfo:
        await self._throttle(f"live{room_id}")
        data = await self._get(ROOM_INFO, {"room_id": room_id},
                               referer="https://live.bilibili.com/")
        d = data.get("data") or {}
        return LiveInfo(
            room_id=int(d.get("room_id") or room_id),
            # 短号没有时官方返回 0，不能拿它当房间号用
            short_room_id=int(d.get("short_id") or 0),
            live_status=int(d.get("live_status") or 0),
            title=d.get("title") or "",
            area=d.get("parent_area_name") or d.get("area_name") or "",
            online=int(d.get("online") or 0),
            cover=d.get("user_cover") or d.get("keyframe") or "",
            # live_time 官方给的是 "YYYY-MM-DD HH:MM:SS" 字符串，
            # 没在播时是 "0000-00-00 00:00:00"（非空字符串，or 0 兜不住）。
            # 一律走 _to_epoch 归一成 Unix 秒 —— 直接 int() 会 ValueError，
            # 而那个异常不是 BiliError，会把直播检测整条链打死。
            live_time=_to_epoch(d.get("live_time")),
        )

    async def get_latest_video(self, uid: int) -> Optional[VideoInfo]:
        await self.ensure_buvid3()
        await self._throttle(f"video{uid}")
        base = {"mid": uid, "pn": 1, "ps": 5, "order": "pubdate", "platform": "web"}
        try:
            params = await self._wbi_sign(base)
        except BiliError:
            params = base  # 签名失败也试一次，可能是接口已放开
        data = await self._get(
            WBI_ARC_SEARCH, params, referer=f"https://space.bilibili.com/{uid}/video"
        )
        vlist = (((data.get("data") or {}).get("list") or {}).get("vlist")) or []
        if not vlist:
            return None
        v = vlist[0]
        return VideoInfo(
            bvid=v.get("bvid") or "",
            title=v.get("title") or "",
            pic=v.get("pic") or "",
            desc=v.get("description") or "",
            pubdate=int(v.get("created") or 0),
        )

    async def get_season_archives(self, uid: int, season_id: int,
                                  max_pages: int = 2) -> Optional[SeasonInfo]:
        """取一个合集里的视频列表。

        接口：GET /x/polymer/web-space/seasons_archives_list
        参数：mid(UP主) + season_id(合集) + 分页
        返回：archives（视频）、meta（合集名/总数/封面）、page（分页）

        ⚠️ 新接口要校验 referer 和 WBI，失败时降级到无鉴权的旧接口。
        ⚠️ 合集里视频可能很多，最多翻 max_pages 页（每页 30）避免刷屏请求。

        ⚠️⚠️ 排序方向：**不信任接口文档，自己判断**。
        接口有个 sort_reverse 参数，不同资料对 true/false 到底是升序还是
        降序说法相反，而且「默认排序」还可能按"加入合集的顺序"排。
        以前一律从第 1 页往后翻 —— 万一拿到的是**旧视频在前**，
        那第 1、2 页永远是合集中最老的那 60 个，UP 主新传的视频排在
        最后一页，永远翻不到。表现就是：**合集更新检测彻底失效**，
        新视频只会被当成普通投稿推（还不进合集基线）。

        现在的取法是**前后比对**（用户指定）：第 1 页 + 最后一页都要，
        合起来按 pubdate 挑最大。因为合集内部顺序可能是混合的 ——
        早期按正序加、后来改成倒序加，只看第 1 页的首尾判断方向，
        无论往哪边翻都可能错过最新那条。前后都取最稳。
        """
        await self.ensure_buvid3()
        page_size = 30
        max_pages = max(1, int(max_pages))
        videos, meta, total = [], {}, 0
        referer = (f"https://space.bilibili.com/{uid}/lists/{season_id}"
                   f"?type=season")

        async def _fetch(pn):
            """取一页，返回 (视频列表, meta, total)。取不到就抛 BiliError。"""
            base = {"mid": uid, "season_id": season_id,
                    "sort_reverse": "false",
                    "page_num": pn, "page_size": page_size,
                    "web_location": "333.999"}
            try:
                params = await self._wbi_sign(base)
            except BiliError:
                params = base
            await self._throttle(f"season{season_id}")
            try:
                data = await self._get(SEASON_ARCHIVES, params, referer=referer)
            except BiliError as e:
                # 新接口失败 → 降级旧接口。旧接口**无鉴权**，所以要用
                # 不带 w_rid/wts 的裸参数去请求（带签名反而可能被拒）。
                try:
                    data = await self._get(SEASON_ARCHIVES_OLD, base,
                                           referer=referer)
                except BiliError as e2:
                    if pn == 1:
                        # ⚠️ 临时故障（超时/网关错/网络抖动）**不能**说成
                        # "合集没了"：照着 ①②③ 去删订阅，会把好端端的
                        # 订阅弄丢。只有确认不是临时故障才这么提示。
                        if is_transient(e2) or is_transient(e):
                            raise BiliTransientError(
                                f"合集 {season_id} 暂时取不到：{e2}"
                                f"（B 站接口超时/繁忙，稍后自动重试）")
                        raise SeasonGoneError(
                            f"合集 {season_id} 取不到：{e}。"
                            f"常见原因：①UP 主删掉了这个合集；②填的是"
                            f"「视频列表(series)」的 ID 而不是合集"
                            f"(season) 的 ID，两者不通用；③uid 与合集"
                            f"对不上。去面板重新查一次这个合集。"
                        )
                    raise
            d = data.get("data") or {}
            pg = d.get("page") or {}
            arr = d.get("archives") or []
            out = [SeasonVideo(
                bvid=a.get("bvid") or "",
                aid=int(a.get("aid") or 0),
                title=a.get("title") or "",
                pic=a.get("pic") or "",
                pubdate=int(a.get("pubdate") or a.get("ctime") or 0),
            ) for a in arr]
            return out, (d.get("meta") or {}), int(pg.get("total") or 0)

        # ---- 第 1 页：拿 meta/total 并判断排序方向 ----
        try:
            first, meta, total = await _fetch(1)
        except SeasonGoneError:
            raise
        except BiliError as e:
            # ⚠️ 临时故障不能悄悄当成"这个合集是空的"：
            #    那样上层既看不到日志、也不会退避，于是每轮照打，
            #    B 站越超时越被打 —— 正是把轮询间隔顶到 24s 的那条链。
            #    必须抛出去，让上层退避重试。
            if is_transient(e):
                raise
            first = []
        videos.extend(first)

        # 还要不要翻页，以及翻哪些页
        #
        # ⚠️⚠️ 只按"第 1 页首尾判断方向再往一边翻"是不够的（用户实测反馈）：
        #   有的合集**早期按正序加、后来改成倒序加**，于是整个合集内部
        #   顺序是混合的 —— 第 1 页的首尾只能反映最老那一批内部的排序，
        #   代表不了整体。这时无论往哪边翻都可能完美错过最新的那条。
        #   正确做法是**前后都取、合起来比**：首页 + 尾页都要，
        #   再按 pubdate 取最大，无论顺序怎么混都能兜住。
        rest = []
        last = (total + page_size - 1) // page_size if total else 0
        if len(first) >= page_size and (not total or len(videos) < total) \
                and max_pages > 1:
            # 1) 尾页必取（混合顺序时最新的很可能落在这）
            if last > 1:
                rest.append(last)
            # 2) 剩余配额按第 1 页判断的方向补，兼顾"新在前"的合集
            if max_pages > 2:
                newest_first = True
                if len(first) >= 2 and first[0].pubdate and first[-1].pubdate:
                    newest_first = first[0].pubdate > first[-1].pubdate
                if newest_first:
                    extra = [p for p in range(2, last)
                             if p not in rest][:max_pages - len(rest) - 1]
                else:
                    extra = [p for p in range(last - 1, 1, -1)
                             if p not in rest][:max_pages - len(rest) - 1]
                rest.extend(extra)

        for pn in rest:
            try:
                more, m2, t2 = await _fetch(pn)
            except (BiliError, SeasonGoneError):
                break
            if m2:
                meta = meta or m2
            if t2:
                total = t2
            if not more:
                break
            videos.extend(more)
            if total and len(videos) >= total:
                break

        # 跨页可能重复，按 bvid 去重（保留先出现的）
        seen, uniq = set(), []
        for v in videos:
            if v.bvid and v.bvid in seen:
                continue
            if v.bvid:
                seen.add(v.bvid)
            uniq.append(v)
        videos = uniq

        if not videos and not meta:
            return None
        return SeasonInfo(
            season_id=int(season_id),
            uid=int(uid),
            title=(meta or {}).get("name") or "",
            cover=(meta or {}).get("cover") or "",
            total=total or len(videos),
            videos=videos,
        )

    async def get_up_seasons(self, uid: int, page_size: int = 20) -> list:
        """取一个 UP 主的合集（season）/ 视频列表（series）清单。

        返回 [{"id","title","total","mtime","type"}]，取不到就是空列表。

        ⚠️ 只用于自检（「合集检测」这一项要挑"最近更新的合集"），
        不参与订阅检测，所以失败抛给调用方去展示原因即可。

        ⚠️ 返回结构各版本不太一致（有的把信息放在 item.meta 里，
        有的直接平铺在 item 上；时间字段有 mtime / ctime / update_time
        三种叫法），所以这里一律**两边都找**，别只认一种。
        """
        await self.ensure_buvid3()
        base = {"mid": uid, "page_num": 1, "page_size": int(page_size),
                "web_location": "333.999"}
        try:
            params = await self._wbi_sign(base)
        except BiliError:
            params = base
        await self._throttle(f"seasons{uid}")
        try:
            data = await self._get(
                SEASON_SERIES_LIST, params,
                referer=f"https://space.bilibili.com/{uid}/lists")
        except BiliError:
            try:
                data = await self._get(
                    SEASON_SERIES_LIST, base,
                    referer=f"https://space.bilibili.com/{uid}/lists")
            except BiliError:
                return []
        d = data.get("data") or {}
        # ⚠️ /lists 页面用的就是 seasons_series_list，它的返回结构是
        #   data.items_lists.seasons_list（合集）+ .series_list（视频列表）
        #   每项形如 {"meta": {...}, "recent_archives": [...]}。
        #   另有版本把内容平铺在 data.items 下 —— 两种都要认。
        #   只认 data.items 会取到空列表，自检「合集检测」永远显示
        #   "该 UP 主没有公开合集"（v1.34.0 及之前就是这个毛病）。
        lists = d.get("items_lists")
        lists = lists if isinstance(lists, dict) else {}
        items, kinds = [], []
        for key in ("seasons_list", "series_list"):
            want = "season" if key == "seasons_list" else "series"
            for it in (lists.get(key) or []):
                if isinstance(it, dict):
                    items.append(it)
                    kinds.append(want)
        for it in (d.get("items") or []):
            if isinstance(it, dict):
                items.append(it)
                kinds.append("")
        out = []
        for idx, it in enumerate(items):
            meta = it.get("meta") if isinstance(it.get("meta"), dict) else {}
            sid = (meta.get("season_id") or meta.get("series_id")
                   or it.get("season_id") or it.get("series_id") or 0)
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                continue
            if not sid:
                continue
            ts = 0
            for k in ("mtime", "update_time", "ctime", "pubdate",
                      "last_update_time"):
                for src in (meta, it):
                    v = src.get(k)
                    if isinstance(v, (int, float)) and v > 0:
                        ts = int(v)
                        break
                if ts:
                    break
            # 来源明确（seasons_list / series_list）时以来源为准；
            # 平铺的旧结构才按字段猜，别把 series 误判成 season
            # —— 两者 ID 不通用，拿 series 的号去查合集必然 404。
            kind = ""
            if idx < len(kinds) and kinds[idx]:
                kind = kinds[idx]
            else:
                kind = ("series" if (it.get("type") == "series"
                                     or (meta.get("series_id")
                                         and not meta.get("season_id")))
                        else "season")
            # ⚠️ recent_archives 是列表接口 **自带** 的"最近几条视频"，
            #    取它里面最大的发布时间，就能知道这个合集最近一次是
            #    什么时候加的视频 —— 这是判断"哪个合集最近更新"最准的
            #    免费依据（零额外请求），比 meta.mtime 靠谱得多。
            recent = 0
            ras = it.get("recent_archives")
            if not isinstance(ras, list):
                ras = meta.get("recent_archives")
            if isinstance(ras, list):
                for a in ras:
                    if not isinstance(a, dict):
                        continue
                    for k in ("pubdate", "ctime", "pub_time", "create_time"):
                        v = a.get(k)
                        if isinstance(v, (int, float)) and v > 0:
                            recent = max(recent, int(v))
                            break
            # 封面：meta.cover 是合集封面；没有就退回最近一条视频的封面
            cv = str(meta.get("cover") or it.get("cover") or "")
            if not cv and isinstance(ras, list):
                for a in ras:
                    if isinstance(a, dict):
                        cv = str(a.get("pic") or a.get("cover") or "")
                        if cv:
                            break
            out.append({
                "id": sid,
                "title": str(meta.get("name") or it.get("name") or ""),
                "total": int(meta.get("total") or it.get("total") or 0),
                "mtime": ts,
                "recent": recent,
                "type": kind,
                "cover": cv,
            })
        return out

    async def _pick_newest_season(self, uid: int, pick: list,
                                  probes: int = 5) -> tuple:
        """从一堆合集里挑出**最近更新**的那个，返回 (season_id, 已取到的合集)。

        ⚠️ 只看 meta.mtime 是不够的 —— 用户实测反馈："合集内挑到的视频
        确实是最新那条，但挑中的合集本身早就不再更新了"。原因有两个：
          ① mtime 在部分版本里**根本没有**（全是 0），排序退化成按 total
             排，于是挑到的是"视频最多"的合集，而不是"最近更新"的；
          ② mtime 反映的是**合集信息**的编辑时间，不等于"最近一次往里
             加视频"的时间 —— 老合集改个名字，mtime 也能很新。
        所以判据按可靠度排：
          ① recent_archives 里最新视频的发布时间（列表接口自带，最准）
          ② 逐个探测候选的最新视频 pubdate（前后比对，慢但准）

        ⚠️ 但 ① **只用来排探测顺序，不当结论**：v1.36.1 之前这里有一条
           "ts 最大且唯一就直接返回"的捷径，实测就是它挑错合集（见下面
           排序处的说明）。现在只要候选多于一个，就一定探测真实视频。
        """
        if not pick:
            return 0, None
        if len(pick) == 1:
            # 只有一个合集，没得挑 —— 直接返回，别浪费请求
            return int(pick[0].get("id") or 0), None

        def _ts(s):
            # recent 优先于 mtime：recent 直接来自"最近加入的视频"
            return int(s.get("recent") or 0) or int(s.get("mtime") or 0)

        # ⚠️ 排序只决定"先探测谁"，**不直接当答案**。
        #    以前在这里有条捷径：时间戳最大且唯一就直接返回、不再探测。
        #    实测就是它挑错的 —— 星瞳的例子里挑中了【合集·整活日记】，
        #    而真正最近更新的是【合集·星瞳のVLOG】：recent / mtime 这些
        #    字段本身就不等于"最近一次往里加视频"，拿它当答案必然出错。
        #    现在一律**逐个探测真实视频**来决胜负，排序只用来省请求。
        #
        #    ts 相同时按**接口原始顺序**（B站 /lists 页面自己就是按
        #    "最近更新"给的），不再按 total 排 —— 视频多不等于更新近。
        ordered = sorted(enumerate(pick),
                         key=lambda p: (_ts(p[1]), -p[0]),
                         reverse=True)
        cands = [s for _, s in ordered]
        # 早停阈值：候选按 ts 降序，若某个未探测候选的 ts 已经
        # 不比当前最好结果新，后面也不用看了。
        ts_list = [_ts(s) for s in cands]

        best_sid, best_pub, best_info = 0, -1, None
        for idx, s in enumerate(cands[:max(1, int(probes))]):
            if idx and ts_list[idx] and ts_list[idx] <= best_pub:
                break                      # 后面的都不可能更新，省请求
            sidc = int(s.get("id") or 0)
            if not sidc:
                continue
            try:
                info = await self.get_season_archives(int(uid), sidc,
                                                      max_pages=2)
            except Exception:
                continue
            vids = list(getattr(info, "videos", None) or [])
            if not vids:
                continue
            pub = max(int(getattr(v, "pubdate", 0) or 0) for v in vids)
            if pub > best_pub:
                best_pub, best_sid, best_info = pub, sidc, info
        if best_sid:
            return best_sid, best_info
        return int(pick[0].get("id") or 0), None

    async def newest_season_video(self, uid: int, season_id: int = 0,
                                  max_pages: int = 2):
        """取「某 UP 主合集里最新一条视频」，返回可直接推送的 SeasonPush。

        ⚠️ 这套"挑合集 + 按发布时间挑最新"的逻辑原本只写在**自检诊断**里，
           而推送测试走的是另一条路：拿"订阅里的第一个合集"，再把
           SeasonInfo 整个交给 render（字段完全对不上：渲染要 bvid/pic/
           season_title/section，SeasonInfo 给的是 videos/cover/season_id）。
           同一份修过的逻辑只有一条路受益，另一条路照旧是坏的 ——
           所以抽成共用方法，两条路都走它。

        ⚠️ 合集里视频顺序不保证（可能正序也可能倒序），一律按 pubdate 挑
           最大的那条，绝不信任接口给的顺序。

        season_id 传 0 时自动挑该 UP **最近更新**的那个合集。
        返回 dict：push / season_id / season_title / src / total。
        """
        sid = int(season_id or 0)
        src = "用的是已订阅的合集"
        cached = None
        if not sid:
            seasons = await self.get_up_seasons(int(uid)) or []
            # 合集(season) 优先于视频列表(series)：两者 ID 不通用
            pick = [s for s in seasons if s.get("type") == "season"] or seasons
            if not pick:
                return None
            # ⚠️ "最近更新"的合集要专门挑：不能用 mtime 直接排序就完事
            #    （mtime 可能全 0，也可能只是"合集改名"的时间）。
            #    详见 _pick_newest_season 的说明。
            sid, cached = await self._pick_newest_season(int(uid), pick)
            src = "未订阅合集，自动挑了最近更新的那个"
        if not sid:
            return None
        if cached is not None:
            info = cached
        else:
            info = await self.get_season_archives(int(uid), sid,
                                                  max_pages=max_pages)
        vids = list(getattr(info, "videos", None) or [])
        if not vids:
            return None
        newest = max(vids, key=lambda v: int(getattr(v, "pubdate", 0) or 0))

        # ⚠️ 小节感知：合集**分了小节**时，扁平列表的顺序会按小节重排
        #   （UP 主往某个小节里加视频，那条不会乖乖待在头或尾），
        #   而且分页可能压根翻不到它。所以用**一次**详情请求拿到整个
        #   合集的小节结构，按小节逐个比 pubdate，取时间线最新的那条。
        section = ""
        try:
            secs = await self.get_season_sections(newest.bvid, sid)
        except Exception:
            secs = []
        if secs:
            ep, _stitle = self.newest_in_sections(secs)
            # 小节里没 pubdate 时挑出来的是第一条（pubdate=0），
            # 这时严格大于才替换，不会把列表挑出的正确结果顶掉。
            if ep and int(ep.get("pubdate") or 0) > \
                    int(getattr(newest, "pubdate", 0) or 0):
                newest = SeasonVideo(bvid=ep.get("bvid") or "",
                                     title=ep.get("title") or "",
                                     pic=ep.get("pic") or "",
                                     pubdate=int(ep.get("pubdate") or 0))
            section = self.section_map(secs).get(newest.bvid, "")
        return {
            "push": SeasonPush(bvid=newest.bvid, title=newest.title,
                               pic=newest.pic,
                               season_title=(getattr(info, "title", "") or ""),
                               section=section),
            "season_id": sid,
            "season_title": getattr(info, "title", "") or "",
            "src": src,
            "total": int(getattr(info, "total", 0) or 0),
        }

    async def _fetch_ugc_season(self, bvid: str) -> dict:
        """取视频详情里的 ugc_season（合集/小节结构）。取不到返回 {}。"""
        if not bvid:
            return {}
        await self._throttle("view")
        try:
            data = await self._get(
                VIEW_API, {"bvid": bvid},
                referer=f"https://www.bilibili.com/video/{bvid}")
        except BiliError:
            return {}
        return ((data.get("data") or {}).get("ugc_season")) or {}

    @staticmethod
    def _norm_episode(ep: dict) -> dict:
        """把小节里的一条 episode 归一化成 {bvid,title,pic,pubdate}。

        ⚠️ 各版本字段位置不一致：有的把视频信息直接摊在 episode 上，
        有的塞在 episode.arc 里，还有的只给 ctime 不给 pubdate。
        所以两个地方都找一遍，找不到就留空（**宁可缺字段，不要报错**）。
        """
        if not isinstance(ep, dict):
            return {}
        arc = ep.get("arc") or {}
        if not isinstance(arc, dict):
            arc = {}
        pub = 0
        for src in (ep, arc):
            for k in ("pubdate", "ctime"):
                try:
                    v = int(src.get(k) or 0)
                except (TypeError, ValueError):
                    v = 0
                if v > 0:
                    pub = max(pub, v)
                    break
            if pub:
                break
        return {
            "bvid": ep.get("bvid") or arc.get("bvid") or "",
            "title": ep.get("title") or arc.get("title") or "",
            "pic": ep.get("pic") or arc.get("pic") or "",
            "pubdate": pub,
        }

    async def get_season_sections(self, bvid: str,
                                  season_id: int = 0) -> list:
        """取某个视频所属合集的**全部分小节**，含每节的视频列表。

        返回 [{"title": 小节名, "episodes": [{"bvid","title","pic","pubdate"}]}]，
        取不到就是空列表。

        原理：小节信息不在合集列表接口里（那里是扁平的视频数组），而在
        **视频详情**接口 /x/web-interface/view 的
        data.ugc_season.sections[] 里。

        ⚠️ 这是"小节"唯一能拿到的公开来源。会员接口需要 UP 主自己的
        登录态，第三方用不了。

        ⚠️⚠️ 一次请求就能拿到**整个合集**的小节结构（不用对每个视频
        查一遍），所以它同时也是**省请求**的做法：拿到之后既能知道
        每个视频属于哪一小节，也能按小节比对哪节更新。
        """
        ugc = await self._fetch_ugc_season(bvid)
        if not ugc:
            return []
        out = []
        for sec in (ugc.get("sections") or []):
            if not isinstance(sec, dict):
                continue
            # ⚠️ 只有**两边都有** season_id 且对不上时才跳过：
            #   有的返回里 section 不带 season_id，那时不能一律过滤掉
            #   （否则整个合集一节都取不到，等于功能白做）。
            sid_sec = int(sec.get("season_id") or 0)
            if season_id and sid_sec and sid_sec != int(season_id):
                continue
            eps = []
            for ep in (sec.get("episodes") or []):
                n = self._norm_episode(ep)
                if n.get("bvid"):
                    eps.append(n)
            if eps:
                out.append({"title": sec.get("title") or "",
                            "episodes": eps})
        return out

    def section_map(self, sections: list) -> dict:
        """小节结构 → {bvid: 小节名}。

        ⚠️ **只有一个小节时返回空字典**：那等于没有分节（B站默认给一个
        "正片"），消息里写"小节：正片"纯属噪音，一律不显示。
        """
        real = [s for s in (sections or []) if s.get("episodes")]
        if len(real) <= 1:
            return {}
        m = {}
        for s in real:
            for ep in s.get("episodes") or []:
                if ep.get("bvid"):
                    m[ep["bvid"]] = s.get("title") or ""
        return m

    @staticmethod
    def newest_in_sections(sections: list) -> tuple:
        """按小节比对，挑**时间线最新**的那条视频。

        返回 ({bvid,title,pic,pubdate}, 小节名)；没有就 (None, "")。

        ⚠️ 为什么需要它：合集列表接口给的是扁平数组，而 UP 主往往是
        **往某个小节里加视频** —— 这时整个列表的顺序会按小节重排，
        "最新那条"不再落在列表的头或尾，只看扁平数组必然错位。
        分小节逐个比 pubdate 才稳。
        """
        best, best_sec = None, ""
        for s in (sections or []):
            for ep in s.get("episodes") or []:
                if not ep.get("bvid"):
                    continue
                if best is None or int(ep.get("pubdate") or 0) > \
                        int(best.get("pubdate") or 0):
                    best, best_sec = ep, (s.get("title") or "")
        return best, best_sec

    async def get_video_section_title(self, bvid: str,
                                      season_id: int = 0) -> str:
        """查某个视频属于合集的哪个**小节**（没分小节时返回空）。

        ⚠️⚠️ **只有一个小节时返回空字符串**：那等于没有分节（B站默认
        给一个"正片"），显示"小节：正片"没有意义 —— 用户在消息里看到
        的应该只有真正分了节的情况。
        """
        if not bvid:
            return ""
        secs = await self.get_season_sections(bvid, season_id)
        m = self.section_map(secs)
        return m.get(bvid, "") if m else ""

    async def get_latest_dynamic(self, uid: int) -> Optional[DynamicInfo]:
        await self.ensure_buvid3()
        await self._throttle(f"dyn{uid}")
        try:
            data = await self._get(
                DYNAMIC_SPACE,
                {"host_mid": uid, "platform": "web", "features": "itemOpusStyle,opusBigCover"},
                referer=f"https://space.bilibili.com/{uid}/dynamic",
            )
            items = (data.get("data") or {}).get("items") or []
            # 取第一条**非置顶**动态（置顶的常驻第一位，不能用来建基线）
            for raw in items:
                d = _parse_polymer(raw)
                if d and d.dyn_id and not d.pinned:
                    return d
            # 全是置顶（或解析不出 id）就走老接口，别直接放弃
        except BiliError:
            pass
        return await self._get_latest_dynamic_old(uid)

    async def _get_latest_dynamic_old(self, uid: int) -> Optional[DynamicInfo]:
        """polymer 接口失败时降级到老接口（对未登录更宽容一些）。"""
        data = await self._get(
            DYNAMIC_OLD, {"host_uid": uid, "need_top": "0"},
            referer=f"https://space.bilibili.com/{uid}/dynamic",
        )
        cards = (data.get("data") or {}).get("cards") or []
        if not cards:
            return None
        return _parse_old_card(cards[0])


    async def get_pinned_dynamic(self, uid: int) -> Optional[DynamicInfo]:
        """取置顶动态。没有置顶返回 None。

        置顶动态和普通动态是**两条独立基线**：
        置顶的那条常驻第一位，不会因为发新动态而移位。

        ⚠️ 真实踩过的坑（用户日志报 code=4101129）：
        这里原来传 `host_uid`，而 polymer feed/space 接口的参数名是
        **`host_mid`**。传错名字等于没传 host_mid，
        B 站直接返回 4101129「请求数据发生错误」。
        同一个文件里 get_latest_dynamic 用的是 host_mid，就它能正常。
        """
        await self.ensure_buvid3()
        await self._throttle(f"dyn_top{uid}")
        ref = f"https://space.bilibili.com/{uid}/dynamic"

        # 1) 正规路子：polymer + host_mid
        try:
            data = await self._get(
                DYNAMIC_SPACE,
                {"host_mid": uid, "platform": "web",
                 "features": "itemOpusStyle,opusBigCover"},
                referer=ref,
            )
            items = (data.get("data") or {}).get("items") or []
            hit, _ = find_pinned(items)
            if hit:
                # ⚠️ 必须显式置 True：调用方（推送 / 自检 / 推送测试）靠
                #    info.pinned 区分「📌 置顶动态」和「📝 新动态」。
                #    以前只有"标记被认出来"时才有值，认不出就退回
                #    pinned=False —— 于是全量跑一遍会发出两张一模一样的
                #    新动态卡片（用户报的"发了两个动态推送"）。
                hit.pinned = True
                return hit
            # 标记没认出来：第一条通常就是置顶（need_top 语义），
            # 既然是 get_pinned_dynamic 的返回值，就按置顶处理
            for raw in items:
                d = _parse_polymer(raw)
                if d and d.dyn_id:
                    d.pinned = True
                    return d
        except BiliError:
            pass

        # 2) 降级：老接口带 need_top=1（对未登录更宽容）
        try:
            data = await self._get(
                DYNAMIC_OLD, {"host_uid": uid, "need_top": "1"},
                referer=ref,
            )
            cards = (data.get("data") or {}).get("cards") or []
            for raw in cards:
                if _is_pinned(raw):
                    d = _parse_old_card(raw)
                    if d and d.dyn_id:
                        d.pinned = True
                        return d
            if cards:
                d = _parse_old_card(cards[0])
                if d and d.dyn_id:
                    d.pinned = True
                    return d
        except BiliError:
            pass
        return None

    async def get_pinned_comment(self, dyn_id: str, uid: int = 0,
                                 rid: str = "", comment_id: str = "",
                                 comment_type: int = 0) -> Optional[dict]:
        """取置顶动态下的置顶评论。

        返回 {"id","user","content","likes"} 或 None。

        ⚠️ 真实踩过的坑（用户日志 code=-404 啥都木有）：
        **评论区类型不是固定的 17**。官方规则是：
            纯文字 / 转发 / 站内分享 → type=17，oid=dynamic_id
            带图动态（相簿）        → type=11，oid=rid
        我们原来硬编码 type=17 + oid=dyn_id，
        遇到带图动态就必然 -404「啥都木有」。

        现在的顺序：
        1. 用 polymer 的 basic.comment_type + comment_id_str（最准）
        2. 否则按官方规则生成候选组合，挨个试：
           (17, dyn_id) (11, rid) (17, rid) (11, dyn_id)
        """
        # 候选 (type, oid)
        cands = []

        def _add(t, o):
            t, o = str(t), str(o or "").strip()
            if o and (t, o) not in cands:
                cands.append((t, o))

        # 1) 接口直接给的
        if comment_type and comment_id:
            _add(comment_type, comment_id)
        if comment_id:
            _add(17, comment_id)
            _add(11, comment_id)
        # 2) 按官方规则猜
        _add(17, dyn_id)      # 纯文字/转发/分享
        _add(11, rid)         # 带图动态（相簿）
        _add(17, rid)
        _add(11, dyn_id)

        if not cands:
            return None

        referer = (f"https://space.bilibili.com/{uid}/dynamic"
                   if uid else "https://www.bilibili.com/")

        last_err = None
        for ctype, oid in cands:
            for sort in (2, 1, 0):     # 2=热度 更容易出置顶
                try:
                    data = await self._get(
                        COMMENT_URL,
                        {"type": ctype, "oid": oid, "sort": sort,
                         "ps": 20, "pn": 1},
                        referer=referer,
                    )
                except BiliError as e:
                    last_err = e
                    continue
                got = _pick_top_reply(data)
                if got:
                    # ⚠️ 跳转链接必须用**动态 id**，不能用评论区 oid。
                    # 评论区类型 type=11 是「相簿(图片动态)」，它的 oid 是
                    # **相簿 id**，跟动态 id 不是一回事。以前拿 oid 去拼
                    # opus 链接 → opus/{相簿id} → 页面不存在。
                    #
                    # 动态评论定位的正确格式（官方行为）：
                    #   https://t.bilibili.com/{动态id}#reply{rpid}
                    jump_id = str(dyn_id or oid)
                    got["url"] = (f"https://t.bilibili.com/{jump_id}"
                                  f"#reply{got.get('id','')}")
                    got["dyn_id"] = jump_id
                    got["oid"] = str(oid)      # 评论区 oid，仅供排查
                    got["ctype"] = int(ctype)
                    return got

        # 全都失败：抛出去让调用方能记录并展示原因
        if last_err:
            raise BiliError(str(last_err))
        return None


def _pick_top_reply(data) -> Optional[dict]:
    """从评论接口返回里挑出置顶评论。"""

    if not isinstance(data, dict):
        return None
    d = data.get("data") or {}
    if not isinstance(d, dict):
        return None

    # ⚠️ 坑点：B站评论接口里 `upper` 是**UP主对象**（含 mid 等字段），
    # 不是评论列表。之前把它当列表遍历，拿到的是字符串 key，
    # 再 .get() 直接 AttributeError → 被外层 except 吞掉，
    # 结果就是"置顶评论永远取不到"。这里必须只认 list。
    cands = []
    tr = d.get("top_replies")
    if isinstance(tr, list):
        cands.append(tr)
    up = d.get("upper")
    if isinstance(up, dict):
        # 有些版本把置顶评论嵌在 upper 里
        nested = up.get("top_replies")
        if isinstance(nested, list):
            cands.append(nested)
    # 最后兜底：普通评论里带 is_top 标记的
    rp = d.get("replies")
    if isinstance(rp, list):
        cands.append([c for c in rp
                      if isinstance(c, dict)
                      and (c.get("is_top") or c.get("top"))])

    for lst in cands:
        for c in lst:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("rpid_str") or c.get("rpid") or "")
            if not cid:
                continue
            member = c.get("member") or {}
            # 表情包：评论正文只有纯文字，表情在 content.emote 里。
            # 不补的话表情多的时候整条评论像是空的。
            emotes = _collect_emotes(c)
            return {
                "id": cid,
                "user": (member.get("uname") or ""),
                # 评论者的 UID —— 用来判断"是不是 UP 主本人发的"
                "mid": str(member.get("mid") or c.get("mid") or ""),
                # 表情套方括号跟正文区分，装扮表情不显示
                "content": _body_text(
                    ((c.get("content") or {}).get("message") or ""),
                    _as_dict(c.get("content")).get("rich_text_nodes"),
                    emotes),
                # ⚠️ 评论也能带图。以前只取 message 纯文字，图全丢了 ——
                #    用户反馈「评论如果有图片也是（要插图）」就是指这个。
                #    结构：content.pictures[]，每项 img_src / img_src_cls。
                "images": _comment_images(c.get("content") or {}),
                "likes": c.get("like") or 0,
                "is_top": True,
            }
    return None


def _comment_images(content) -> list:
    """从评论 content 里取图片地址。

    B站评论的图在 content.pictures[] 里，每项形如
    {"img_src": "...", "img_width": ..., "img_height": ...}。
    ⚠️ content 有时是 dict（正常），有时是 JSON 字符串（老版接口），
    一律先归一化，否则 .get 会 AttributeError。
    """
    c = _as_dict(content)
    if not c:
        return []
    out = []
    for p in (c.get("pictures") or []):
        if not isinstance(p, dict):
            continue
        u = p.get("img_src") or p.get("img_src_cls") or ""
        if u:
            out.append(u)
    return out[:3]


def _basename_noext(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    return name.split(".")[0]


# 表情包：B站的正文/评论文本里**只有纯文字**，
# 表情单独放在 content.emote 这类字典里，key 本身就是 "[doge]" 这种形式。
# 不处理的话用户看到的是一段没有表情的干瘪文字，
# 甚至表情占主要内容时整条消息像是空的。
#
# ⚠️ 官方 emote 对象里 type 字段区分表情种类：
#     1 免费(小黄脸)  2 会员专属  3 购买所得(收藏集)
#     4 颜文字        5 diy       9 UP主大表情(充电表情)
# 收藏集 / 充电表情的名字常常很长（带系列名或 UP 主名前缀），
# 所以**不能靠"名字长短"来猜** —— 只能按结构认。
#
# 表情对象长这样（评论区 content.emote 的某一项）：
#   {"id":26, "package_id":1, "type":1, "text":"[doge]",
#    "url":"https://...", "meta":{"size":1}}
# 动态/专栏则是富文本节点：
#   {"emoji":{"text":"[热词系列_干杯]", "icon_url":"...", "size":2},
#    "text":"[热词系列_干杯]", "type":"RICH_TEXT_NODE_TYPE_EMOJI"}
# 两者都有 text + (url / icon_url / id)，按这个认最稳。
_EMOTE_RE = re.compile(r"^\[[^\]]{1,60}\]$")
# 裸表情名：只有中英文/数字/下划线/连字符（颜文字带符号，不匹配）
_PLAIN_EMOTE_RE = re.compile(r"^[\w\u4e00-\u9fff\-]{1,60}$")
# 表情对象里哪些字段是"图片地址"
_EMOTE_URL_KEYS = ("url", "gif_url", "webp_url", "icon_url", "emote_url")
# 表情对象里哪些字段是"表情的文字代码"
# ⚠️ "emoji" 是**直播间专属表情**的触发关键词字段（不带方括号），
# 必须列进来，否则装扮/直播间表情一个都认不出来。
_EMOTE_TEXT_KEYS = ("text", "emoji_name", "name", "emote_name", "emoji")
# 颜文字的 type（4）。type 是**权威信号** —— 比"有没有图片"更准：
# 收藏集/大表情的名字里带空格、「·」，只能靠"有图"来补方括号；
# 而颜文字本身没有图，但有些返回里会顺带给个 url 字段，
# 只认图片就会把 (￣▽￣) 套成 [(￣▽￣)]。所以 type=4 一律不套。
_YANWEN_TYPES = (4, "4", "RICH_TEXT_NODE_TYPE_EMOJI_CAO")

# 名字里至少要有一个「字」：中英文/数字/下划线。
# 纯符号（unicode emoji 😊、颜文字 (￣▽￣)）不是表情名，套上方括号
# 只会更难读 —— 一律原样保留。
_HAS_WORD_RE = re.compile(r"[\w\u4e00-\u9fff]")


def _emote_is_yanwen(d) -> bool:
    """这个表情对象是不是颜文字（不能套方括号）。"""
    if not isinstance(d, dict):
        return False
    t = d.get("type", d.get("emoji_type"))
    return t in _YANWEN_TYPES


def _emote_code(v: dict, fallback_key: str = "", strict: bool = True) -> str:
    """从表情对象里取出文字代码。取不到返回空串。

    ⚠️ key 优先于 text 的原因：
    评论正文 message 里的 token 是**跟 key 匹配的**（前端拿 key 去
    message 里扫描替换）。如果 key 和 text 不一致，我们判断"正文里
    有没有这个表情"时必须看 key，否则会误判成缺失、重复补一遍。
    """
    if not isinstance(v, dict):
        return ""
    fk = str(fallback_key or "").strip()
    # key 本身就是 "[doge]" 形式 → 直接认（emote 映射表就是这种结构）
    if fk and _EMOTE_RE.match(fk):
        return fk
    # strict：要求同时有"图片"和"文字"，避免把别的对象误当成表情。
    # 非 strict（明确的 emoji_details 等列表上下文）：列表名已经是强信号，
    # 只要有文字代码就认 —— 有些返回里图片字段可能叫别的名字或缺。
    has_img = any(v.get(k) for k in _EMOTE_URL_KEYS)
    has_id = any(v.get(k) for k in ("id", "emote_id", "emoji_id",
                                    "emoticon_id", "emoticon_unique"))
    if strict and not (has_img or has_id):
        return ""
    txt = ""
    for k in _EMOTE_TEXT_KEYS:
        val = v.get(k)
        if isinstance(val, str) and val.strip():
            txt = val.strip()
            break
    if not txt:
        return fk if (fk and _EMOTE_RE.match(fk)) else ""
    if _emote_is_yanwen(v) or not _HAS_WORD_RE.search(txt):
        return txt      # 颜文字 / 纯符号 emoji：原样，套上括号就看不懂了
    # 裸表情名（如 emoji_details 里的 emoji_name="doge"）补成 [doge]。
    # ⚠️ 但颜文字不能补 —— (￣▽￣) 套上方括号就看不懂了。
    # 区分方法：只由 中英文/数字/下划线/连字符 组成的才当表情名；
    # 含括号、空格、全角符号的一律是颜文字，原样保留。
    #
    # ⚠️ 例外：带图片/ID 的一定是**图片表情**（收藏集、充电大表情等），
    # 它们的名字里常常带空格、「·」这类符号（"小星星 收藏集 拍手"），
    # 只靠字符集判断会漏掉，补不上方括号就混在正文里看不出来。
    # 有图就不猜，直接补 —— 颜文字没有 url/id，不会被误伤。
    if not txt.startswith("[") and (has_img or has_id
                                    or _PLAIN_EMOTE_RE.match(txt)):
        return "[" + txt + "]"
    return txt


def _collect_emotes(obj, out: Optional[list] = None, depth: int = 0
                    ) -> list:
    """递归找出接口返回里所有表情的文字代码。

    为什么递归：表情在接口里的位置不固定 ——
      评论：content.emote（映射表，key 是 [doge]）
      动态：module_dynamic.desc / opus.summary / display.emoji_info
      富文本：RICH_TEXT_NODE_TYPE_EMOJI 节点的 emoji 子对象
    挨个路径硬编码漏一个就又没表情了，不如整体找一遍。
    """
    if out is None:
        out = []
    if depth > 8:
        return out

    def _add(code):
        code = str(code or "").strip()
        if not code or len(code) > 60 or "\n" in code:
            return
        if code not in out:
            out.append(code)

    if isinstance(obj, dict):
        # 情况 A：映射表 {"[doge]": {表情对象}}
        for k, v in obj.items():
            if isinstance(v, dict):
                _add(_emote_code(v, k))
        # 情况 B：列表形式（emoji_details / emoji_info 等）
        # "emoticons" 是**直播间专属表情**的列表名（装扮/直播间表情）
        for key in ("emote", "emotes", "emoji_details", "emoji_info",
                    "emojis", "expression", "emoticons", "emoji_list",
                    "recently_used_emoticons"):
            v = obj.get(key)
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict):
                        # 列表名本身就是强信号，不强制要求图片字段
                        _add(_emote_code(it, "", strict=False))
        for v in obj.values():
            _collect_emotes(v, out, depth + 1)
    elif isinstance(obj, list):
        for it in obj:
            _collect_emotes(it, out, depth + 1)
    return out


def _with_emotes(text: str, emotes) -> str:
    """把表情补成 [doge] 这种可读形式。

    ⚠️ 只在文本里**确实没有**这个表情时才追加 ——
    有些接口正文里已经带 [doge] 了，再拼一次会变成两遍。

    颜文字（type=4，如 (￣▽￣)）不带方括号，原样拼接，
    不额外套 [] —— 套上反而看不懂。
    """
    t = str(text or "")
    if not emotes:
        return t
    miss = []
    for e in emotes:
        e = str(e or "").strip()
        if not e or e in t:      # 正文里已经有了，不重复
            continue
        miss.append(e)
    if not miss:
        return t
    joiner = " " if t and not t.endswith(" ") else ""
    return t + joiner + "".join(miss)


# ---------------------------------------------------------------------------
# 装扮表情 / 表情名称规范化（推送正文用）
# ---------------------------------------------------------------------------
# B站「装扮」是直播间装扮套装里的付费装饰表情，名字里带「装扮」字样
# （如 [塔菲_装扮_贴贴] / [装扮_开门啊]）。这类名字又长又杂，
# 混在推送正文里没有信息量 —— 用户明确要求「装扮可以不读」。
#
# ⚠️ 只按**名字里的关键词**认，不按 emote 的 type 认：
#    type=3 是收藏集（购买所得的数字藏品表情），那是正经表情包，
#    跟「装扮」不是一回事；按 type 误杀会让正文平白缺一块。
# ⚠️ 这里**不能**放裸「专属」：UP 主充电大表情的名字也带这两个字
#   （[UP主专属_超级大笑]，type=9），一放就被误杀，正文平白缺一块。
#   装扮（type=2）里名字只写「某某专属」的，靠下面的 etype 兜。
_DRESS_RE = re.compile(r"装扮|_suit_|^suit|粉丝专属|专属装扮|贴纸", re.I)
# 装扮表情的 type（2 = 会员专属 / 装扮套装）。
# 收藏集是 3、充电大表情是 9 —— 都不是装扮，不能按这个数误杀。
_DRESS_TYPES = (2, "2")


def _emote_type(d) -> object:
    """取表情对象的 type（不同接口字段名不同）。取不到返回 None。"""
    if not isinstance(d, dict):
        return None
    return d.get("type", d.get("emoji_type"))


def _is_dress_emote(code: str, etype=None) -> bool:
    """这个表情代码是不是「装扮」类（推送里可以不显示）。

    两条判据，缺一不可：
      1) 名字里的关键词（装扮 / 粉丝专属 / _suit_ / 贴纸）
      2) 给了 type 且是装扮那类（2），名字里又带「专属」
         —— 有些装扮名字只写「星瞳粉丝专属」这种，第 1 条能命中；
            只写「某某专属」的得靠这条补。
    ⚠️ 只看名字里的「专属」会把 UP 主充电大表情（type=9）
       一起杀掉，所以裸「专属」不进关键词表。
    """
    c = str(code or "")
    if _DRESS_RE.search(c):
        return True
    if etype in _DRESS_TYPES and "专属" in c:
        return True
    return False


def _drop_dress(codes) -> list:
    """把装扮表情剔掉，其余按原顺序保留。"""
    return [c for c in (codes or []) if not _is_dress_emote(c)]


# 富文本节点：动态正文 desc.rich_text_nodes 里，
# 表情是 RICH_TEXT_NODE_TYPE_EMOJI 节点，普通文字是 RICH_TEXT_NODE_TYPE_TEXT。
_EMOJI_NODE = "RICH_TEXT_NODE_TYPE_EMOJI"
# 话题（tag）节点。它的 text 通常给的是 "#小星星的家#" 或 "小星星的家"，
# 直接拼进正文就是一串光秃秃的文字，跟正文分不开 —— 统一格式化成
# "#Tag:小星星的家"。
_TOPIC_NODE = "RICH_TEXT_NODE_TYPE_TOPIC"
# 纯文本正文里成对井号包裹的话题：#小星星的家#
_TOPIC_INLINE_RE = re.compile(r"#([^#\n]{1,40})#")
# 正文里已经写成 [xxx] 的片段
_BRACKET_RE = re.compile(r"\[[^\[\]\n]{1,60}\]")


def _fmt_topic(raw: str) -> str:
    """把话题节点格式化成 #Tag:xxx。

    接口给的 text 三种形态都要吃掉：
      "小星星的家"      → #Tag:小星星的家
      "#小星星的家#"    → #Tag:小星星的家
      "#小星星的家"     → #Tag:小星星的家
    已经是 "#Tag:xxx" 的原样返回，不重复加前缀。
    """
    t = str(raw or "").strip()
    if not t:
        return ""
    if t.startswith("#Tag:"):
        return t
    t = t.strip("#").strip()
    if not t:
        return ""
    return "#Tag:" + t


def _fmt_topics_text(t: str) -> str:
    """纯文本正文里成对的 #话题# 统一格式化成 #Tag:xxx。

    为什么还需要这一步：话题格式化本来只在**逐节点重建**那条路上做
    （_fmt_topic）。而新版 opus 动态的节点在 major.opus.summary 里，
    老代码只从 module_dynamic.desc 取 —— 取不到就走纯文本回退，
    话题于是变成光秃秃的「#小星星的家#」混在正文里（用户反馈的正是这个）。

    ⚠️ 只认**成对**的井号，单井号（正文里手写的话题符号）不动，
    否则一句「今天 #1 名」也会被改成 #Tag:1 名。
    """
    s = str(t or "")
    if s.count("#") < 2:
        return s

    def _rep(m):
        name = m.group(1).strip()
        if not name or name.startswith("Tag:"):
            return m.group(0)
        # 话题名里不会有空白 / 方括号，有就不是话题（避免误伤正文）
        if re.search(r"[\s\[\]]", name):
            return m.group(0)
        return "#Tag:" + name

    return _TOPIC_INLINE_RE.sub(_rep, s)


def _strip_dress_brackets(t: str) -> str:
    """删掉正文里已经写成 [xxx] 的装扮表情。

    emote 列表里收集不到装扮时（部分返回里装扮节点既没 icon 也没 id），
    _strip_dress_words 就无从下手，装扮照样显示在推送里。
    这里直接按名字判断正文里的 [xxx]，不依赖接口有没有给 emote 对象。
    """
    s = str(t or "")
    if "[" not in s:
        return s
    return _BRACKET_RE.sub(
        lambda m: "" if _is_dress_emote(m.group(0)) else m.group(0), s)


def _norm_emote(raw: str, has_icon: bool = False) -> str:
    """把表情的裸名规范成 [xxx] 形式。

    已经是 [xxx] 的原样返回。
    ⚠️ 颜文字（(￣▽￣)）不能套方括号 —— 套上反而看不懂。
    只由 中英文/数字/下划线/连字符 组成的才当表情名补括号。
    ⚠️ has_icon：节点带着图片（emoji 子对象 / icon_url）时，
    一定是**图片表情**，名字里带空格、「·」这些符号也照补 ——
    收藏集、充电大表情的名字常常这样，只靠字符集判断会漏。
    """
    t = str(raw or "").strip()
    if not t:
        return ""
    if t.startswith("[") and t.endswith("]"):
        return t
    if not _HAS_WORD_RE.search(t):
        return t      # 纯符号（unicode emoji / 颜文字）：不套
    if has_icon or _PLAIN_EMOTE_RE.match(t):
        return "[" + t + "]"
    return t


def _nodes_text(nodes, drop_dress: bool = True):
    """用 desc.rich_text_nodes 重建正文（表情带方括号）。

    为什么不能直接取 desc.text：
    新版 opus 动态里，表情节点给的常常是**裸名**（OnO_气死我了），
    直接拼进正文就成了「今天真的好倒霉OnO_气死我了」——
    完全看不出哪几个字是表情。逐节点重建能给表情套上方括号：
    「今天真的好倒霉[OnO_气死我了]」。

    节点除了正文 / 表情，还有 @用户、话题 #xx#、抽奖等，
    这些的 text 本身就是能直接显示的内容，原样拼接。

    返回 None 表示这条路走不通（没给节点），调用方要回退到纯文本。
    """
    if not isinstance(nodes, list) or not nodes:
        return None
    out = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        t = str(n.get("text") or "")
        sub = n.get("emoji")
        sub = sub if isinstance(sub, dict) else {}
        if not t:
            # 收藏集 / 充电大表情有时节点自身 text 是空的，
            # 名字只放在 emoji 子对象里 —— 不取回来表情就整块不见了。
            for k in ("text", "emoji_name", "name", "emoji",
                      "emote_name", "emoji_desc"):
                val = sub.get(k)
                if isinstance(val, str) and val.strip():
                    t = val.strip()
                    break
        if not t:
            continue
        ntype = str(n.get("type") or "")
        if ntype == _EMOJI_NODE:
            # 节点类型本身就是权威信号：只要是 EMOJI 节点、又不是颜文字，
            # 就一定是个图片表情（收藏集 / 充电大表情 / 装扮都算），
            # 名字里带空格、「·」这些符号也照补。
            # 只按"有没有 icon 字段"判断会漏 —— 部分返回里节点自身
            # 不带 icon_url、emoji 子对象也是空的，正文就成了裸名
            # （用户反馈的「星回纪·时空轮转是我的翅膀」正是这种）。
            has_icon = True
            if _emote_is_yanwen(sub):
                has_icon = False      # 颜文字不套方括号
            code = _norm_emote(t, has_icon=has_icon)
            if drop_dress and _is_dress_emote(code, _emote_type(sub)):
                continue          # 装扮表情：不显示
            out.append(code)
        elif ntype == _TOPIC_NODE:
            # 话题后面补一个空格：B站把话题单独排在正文上方，
            # 逐节点拼回来时会跟正文贴死（「#Tag:小星星的家互动抽奖…」），
            # 补一个空格才分得清哪几个字是话题。
            out.append(_fmt_topic(t) + " ")
        else:
            out.append(t)
    if not out:
        return None
    s = "".join(out)
    # 话题后补的空格不该留在行尾 / 正文末尾（看着像多打了个空格）
    s = re.sub(r"[ \t]+\n", "\n", s)
    return s.rstrip(" \t") or None


def _mark_emotes(text: str, codes) -> str:
    """把正文里的**裸表情名**补成 [xxx]，正文里没有的才拼到末尾。

    ⚠️ 两个坑，都是实测踩出来的：

      1) 按名字长度**从长到短**处理，并且替换时先落占位符、
         最后统一换回 —— 否则 [doge] 会先把刚替换出来的
         [doge_大笑] 里的 "doge" 再换一次，变成 [[doge]_大笑]。
      2) 单字名（如 [赞]）**不就地替换** —— 中文正文里随便一个字
         都可能撞上，补完满屏方括号。但也不能丢，走末尾追加。
    """
    t = str(text or "")
    todo = []        # [(名字, 代码)] 有机会就地替换的
    tail = []        # 只能拼到末尾的
    for c in codes or []:
        c = str(c or "").strip()
        if not c:
            continue
        name = c[1:-1] if (c.startswith("[") and c.endswith("]")) else c
        if len(name) < 2:
            tail.append(c)
        else:
            todo.append((name, c))
    todo.sort(key=lambda x: len(x[0]), reverse=True)
    slots = []
    for i, (name, c) in enumerate(todo):
        if c in t:                      # 已经是 [xxx]，不用动
            continue
        if name in t:
            ph = "\x00%d\x00" % i       # 占位符，正文里不会出现
            slots.append((ph, c))
            t = t.replace(name, ph, 1)
            continue
        tail.append(c)
    for ph, c in slots:
        t = t.replace(ph, c)
    if not tail:
        return t
    joiner = " " if t and not t.endswith(" ") else ""
    return t + joiner + "".join(tail)


def _strip_dress_words(text: str, codes) -> str:
    """正文里混着的装扮裸名（没走节点重建时）直接删掉。"""
    t = str(text or "")
    names = []
    for c in codes or []:
        c = str(c or "").strip()
        if not _is_dress_emote(c):
            continue
        n = c[1:-1] if (c.startswith("[") and c.endswith("]")) else c
        if len(n) >= 2:
            names.append(n)
    names.sort(key=len, reverse=True)
    for n in names:
        t = t.replace(n, "")
    return t


def _dyn_text_nodes(m_dyn, major=None, item=None):
    """取出动态的富文本节点（rich_text_nodes），多路径 + 递归兜底。

    ⚠️ 节点位置随接口版本变，只认一处就会整段失效：
      · 老版：modules.module_dynamic.desc.rich_text_nodes
      · 新版：…module_dynamic.major.opus.summary.rich_text_nodes
    新版 opus 图文动态（现在最常见）的正文和节点都在 summary 里，
    老代码只从 desc 取 → 取到 None → 退化成纯文本拼接，
    于是话题没有 #Tag: 前缀、收藏集裸名分不清。
    """
    def _ok(v):
        if not isinstance(v, list) or not v:
            return None
        for n in v:
            # 只认真的富文本节点（带 RICH_TEXT_NODE_TYPE_* 的 type）
            if isinstance(n, dict) and str(
                    n.get("type") or "").startswith("RICH_TEXT_NODE_TYPE"):
                return v
        return None

    m_dyn = m_dyn if isinstance(m_dyn, dict) else {}
    major = major if isinstance(major, dict) else {}
    for src in (_as_dict(m_dyn.get("desc")).get("rich_text_nodes"),
                _as_dict(_as_dict(major.get("opus")).get("summary")
                         ).get("rich_text_nodes")):
        got = _ok(src)
        if got:
            return got
    # 兜底：整体递归找第一个 rich_text_nodes（接口再改版也不会整段失效）
    found = []
    _find_nodes(item if item is not None else m_dyn, found)
    for v in found:
        got = _ok(v)
        if got:
            return got
    return None


def _find_nodes(obj, out: list, depth: int = 0) -> list:
    """递归收集所有 rich_text_nodes 列表（顺序：由外到内）。"""
    if depth > 8 or len(out) >= 4:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "rich_text_nodes" and isinstance(v, list) and v:
                out.append(v)
            else:
                _find_nodes(v, out, depth + 1)
    elif isinstance(obj, list):
        for it in obj:
            _find_nodes(it, out, depth + 1)
    return out


def _body_text(plain, nodes=None, emotes=None, drop_dress: bool = True):
    """组装推送正文：表情套方括号区分正文 + 装扮不显示。

    两条路：
      1) 给了富文本节点 → 逐节点重建（位置准确、表情自带括号）
      2) 没给节点     → 纯文本里找裸名补括号，实在没有才拼到末尾
    """
    codes = list(emotes or [])
    if drop_dress:
        codes = _drop_dress(codes)
    built = _nodes_text(nodes, drop_dress=drop_dress)
    if built:
        return built
    t = _strip_dress_words(str(plain or ""), emotes or [])
    # 装扮：emote 列表里没收集到时，正文里 [xxx] 形式的也要删掉
    t = _strip_dress_brackets(t)
    t = _mark_emotes(t, codes)
    # 话题：节点重建那条路已经格式化过，这里是纯文本兜底
    t = _fmt_topics_text(t)
    # 删装扮裸名 / 补话题括号会留下连续空格，压掉（末尾的空格也一并收掉）
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def _clean_text(s: str, limit: int = 300) -> str:
    s = (s or "").replace("\r", "").strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    if len(s) > limit:
        s = s[:limit] + "…"
    return s


def _is_pinned(item: dict) -> bool:
    """判断这条是不是置顶动态。

    为什么必须区分：置顶动态**永远排在列表第一位**，
    如果直接取 items[0] 建基线，那么基线永远是那条置顶动态的 ID，
    之后用户发什么新动态 ID 都不会变 —— 结果就是"动态提醒从来不推"。
    这正是实际使用中动态漏推的根因。

    置顶标记在不同版本接口里位置不同，多取几个来源。
    """
    if not item:
        return False

    # 1) 直接的布尔标记（各版本接口位置不同，多取几个）
    for key in ("is_pinned", "is_top", "top", "pinned", "is_stick",
                "stick", "is_top_dynamic"):
        if item.get(key):
            return True
    basic = _as_dict(item.get("basic"))
    for key in ("is_pinned", "is_top", "top", "pinned"):
        if basic.get(key):
            return True

    # 2) 标签文字：module_tag.text == "置顶"
    modules = item.get("modules") or {}
    tag = modules.get("module_tag") or {}
    txt = str(tag.get("text") or "").strip().lower()
    if txt in ("置顶", "pinned", "top"):
        return True

    # 3) 兜底：整个 modules / item 里任何一层出现"置顶"字样
    #    真实接口字段名经常变（module_top / module_more / 新版嵌套），
    #    只认固定 key 会漏掉，这里做一次宽松的文本扫描。
    try:
        blob = str(item)[:4000].lower()
        if "置顶" in blob or '"pinned"' in blob or "'pinned'" in blob:
            return True
    except Exception:
        pass
    return False


def find_pinned(items: list):
    """从 feed/space 返回里找置顶动态。

    两级策略：
    1. 先按标记找（准）
    2. 找不到且请求带 need_top=1 时，**退而取第一条**并标记 `assumed`
       —— 因为该参数下第一条通常就是置顶。
       这样即使标记字段名变了也不会完全失效；
       返回的 assumed 只用于诊断展示，避免误判时静默出错。
    """
    for raw in items or []:
        d = _parse_polymer(raw)
        if d and d.dyn_id and _is_pinned(raw):
            return d, False
    return None, False


# 合理时间范围：B 站 2009 年才上线，早于 2000 或晚于 2100 一定是取错字段了。
# 取错比取不到更危险 —— 一个离谱的时间会让"是不是新内容"判定整体失灵，
# 所以范围外的直接按"未取到"处理（调用方退回按 ID 判）。
_PUB_TS_MIN = 946_684_800      # 2000-01-01
_PUB_TS_MAX = 4_102_444_800    # 2100-01-01


def _pick_pub_ts(*sources) -> int:
    """从若干容器里挑出发布时间（Unix 秒），取不到返回 0。

    ⚠️ 动态接口在不同版本里把时间放在不同字段：
       polymer  → modules.module_author.pub_ts
       老 card  → desc.timestamp
       兜底     → pub_ts / ctime / pubdate
    统一在这里取，避免各调用点各写一套、有的取到有的取不到。
    """
    for src in sources:
        if not isinstance(src, dict):
            continue
        for key in ("pub_ts", "timestamp", "ctime", "pubdate", "pub_time"):
            v = src.get(key)
            if v in (None, ""):
                continue
            # pub_time 有时是 "2024-01-01 12:00:00" 这种字符串，直接跳过，
            # 交给上面几个整数字段；硬解析容易出错且没必要。
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                iv = int(v)
                # 毫秒时间戳（13 位）收敛到秒
                if iv > 10_000_000_000:
                    iv //= 1000
                if _PUB_TS_MIN < iv < _PUB_TS_MAX:
                    return iv
            elif isinstance(v, str) and v.isdigit():
                iv = int(v)
                if iv > 10_000_000_000:
                    iv //= 1000
                if _PUB_TS_MIN < iv < _PUB_TS_MAX:
                    return iv
    return 0


def _as_dict(v) -> dict:
    """把可能是 JSON 字符串的结构化字段安全转成 dict。

    B站 的 polymer 接口在不同版本/不同动态类型下，同一个字段
    有时给 dict、有时给 JSON 字符串（旧版遗留）。直接 .get() 会在
    字符串上抛 AttributeError，导致整条动态解析失败。
    这里统一：dict 原样返回，str 尝试按 JSON 解析，其余当空。
    """
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("{"):
            import json as _json
            try:
                o = _json.loads(s)
            except Exception:
                return {}
            return o if isinstance(o, dict) else {}
    return {}


def _parse_polymer(item: dict) -> DynamicInfo:
    """解析新版 polymer 动态结构。"""
    # 动态 ID 的字段在不同版本接口里位置不一样，多取几个来源兜底。
    # 取不到 id 会导致"动态永远检测不到"（基线始终是空）。
    basic = _as_dict(item.get("basic"))
    dyn_id = str(item.get("id_str")
                 or item.get("dyn_id_str")
                 or basic.get("id_str")
                 or item.get("dyn_id")
                 or item.get("id")
                 or "")
    modules = _as_dict(item.get("modules"))
    m_dyn = _as_dict(modules.get("module_dynamic"))

    # 正文
    desc = _as_dict(m_dyn.get("desc")).get("text") or ""
    title = ""
    images: List[str] = []

    major = _as_dict(m_dyn.get("major"))
    ref_room_id = 0
    ref_bvid = ""
    # 识别动态主体类型（用于开播/投稿的重复动态去重）
    mtype = str(major.get("type") or "").lower()
    if not mtype:
        # ⚠️ 必须用 `key in major` 而不是 `major.get(key)` 的真值判断：
        # 字段存在但值为空字典 {} 时，真值判断会漏掉，
        # 结果全部退化成 normal，去重逻辑形同虚设。
        for key in ("live_rcmd", "live", "archive", "ugc_season",
                    "reserve", "opus", "draw", "article", "common"):
            if key in major:
                mtype = key
                break
    if mtype in ("live_rcmd", "live"):
        major_type = "live"
    elif mtype in ("archive", "ugc_season"):
        major_type = "video"
    elif mtype == "reserve":
        major_type = "reserve"
    else:
        major_type = "normal"
    if major:
        # 图文动态（新版统一叫 opus）
        opus = _as_dict(major.get("opus"))
        if opus:
            title = opus.get("title") or ""
            if not desc:
                desc = _as_dict(opus.get("summary")).get("text") or ""
            pics = [x for x in (opus.get("pics") or []) if isinstance(x, dict)]
            images = [x.get("url") or "" for x in pics if x.get("url")]
            # 投稿动态：视频地址在 jump_url 里（没有 archive 卡片）
            if not ref_bvid:
                _m = _BV_RE.search(str(opus.get("jump_url") or ""))
                if _m:
                    ref_bvid = _m.group(0)
        # 转发/引用视频 → 记下指向的 BV 号
        archive = _as_dict(major.get("archive"))
        if archive:
            title = title or archive.get("title") or ""
            images = images or ([archive.get("cover")] if archive.get("cover") else [])
            ref_bvid = str(archive.get("bvid")
                           or archive.get("bv_id")
                           or (archive.get("bvid") or "")
                           or "")
        # 老式九宫格图片
        draw = _as_dict(major.get("draw"))
        if draw:
            _items = [x for x in (draw.get("items") or []) if isinstance(x, dict)]
            images = images or [
                x.get("src") or "" for x in _items if x.get("src")
            ]
        # 开播卡片动态 → 记下指向的直播间号
        # ⚠️ 新版开播动态常常**不给** live_rcmd 卡片，直播间地址藏在
        #    opus 的 jump_url 里（和投稿动态把 BV 藏在 jump_url 是同一个
        #    套路）。只认卡片就会漏，漏了这条动态会被当成普通图文动态
        #    推出去，房间号去重也跟着失效。
        live_rcmd = _as_dict(major.get("live_rcmd"))
        if live_rcmd:
            content = _as_dict(live_rcmd.get("content"))
            live_play = _as_dict(content.get("live_play_info"))
            # 直播间号在不同版本里字段名不一样，多取几个来源
            raw_room = (live_play.get("room_id")
                        or content.get("room_id")
                        or live_rcmd.get("room_id")
                        or live_play.get("roomid")
                        or 0)
            try:
                ref_room_id = int(raw_room)
            except (TypeError, ValueError):
                ref_room_id = 0
        # 卡片没给号，就从各种跳转链接里抠（新版开播动态走这条）
        if not ref_room_id:
            _lrc = live_rcmd or {}
            _lp = (_as_dict(_as_dict(_lrc.get("content"))
                            .get("live_play_info")) if _lrc else {})
            for _c in (opus.get("jump_url") if opus else "",
                       _lrc.get("jump_url"), _lrc.get("url"),
                       _lp.get("jump_url"), _lp.get("link"),
                       _lp.get("url")):
                ref_room_id = _room_from_src(_c)
                if ref_room_id:
                    break

    # 兜底：正文里直接贴了视频地址（转发/分享类动态常见）
    if not ref_bvid and desc:
        _m = _BV_RE.search(desc)
        if _m:
            ref_bvid = _m.group(0)

    # 兜底：正文里直接贴了直播间地址（分享直播间的动态常见）
    if not ref_room_id and desc:
        ref_room_id = _room_from_src(desc)

    # ⚠️ 抠到了直播间号、却没认出任何主体卡片 → 它就是开播动态。
    #    只在没认出更具体的类型时才升级：普通图文动态里也可能贴一个
    #    直播间链接，不能整条判成开播（那样会把"分享直播间"也吞掉）。
    if ref_room_id and major_type == "normal":
        major_type = "live"

    # 标题兜底：动态正文第一行当标题
    if not title and desc:
        first = desc.splitlines()[0].strip()
        if len(first) <= 30:
            title = first

    basic_rid = str(basic.get("rid_str") or basic.get("rid")
                    or item.get("rid_str") or item.get("rid") or "")

    # 评论区：polymer 的 basic 直接给了类型和 id，优先用它
    c_type = basic.get("comment_type") or item.get("comment_type") or 0
    try:
        c_type = int(c_type)
    except (TypeError, ValueError):
        c_type = 0
    c_id = str(basic.get("comment_id_str") or item.get("comment_id_str")
               or "")

    return DynamicInfo(
        dyn_id=dyn_id,
        rid=basic_rid,
        comment_type=c_type,
        comment_id_str=c_id,
        major_type=major_type,
        ref_room_id=ref_room_id,
        ref_bvid=ref_bvid,
        title=_clean_text(title, 60),
        # 表情包：动态正文同样只有纯文字，表情单独放在 emote 里。
        # 不补的话表情多的时候整条动态看起来是空的。
        # ⚠️ 表情要**套方括号**跟正文区分开（[OnO_气死我了]，不是裸名），
        #    装扮表情则不显示 —— 逐节点重建最准，没节点时回退纯文本。
        text=_clean_text(_body_text(
            desc,
            _dyn_text_nodes(m_dyn, major, item),
            _collect_emotes(item))),
        # ⚠️ 上限 9：B站一条动态最多 9 张图，以前取 3 张会漏掉后面的图
        images=[i for i in images if i][:DYN_IMG_MAX],
        pinned=_is_pinned(item),
        pubdate=_pick_pub_ts(
            (modules.get("module_author") or {}),
            basic, item,
        ),
    )


def _parse_old_card(card: dict) -> DynamicInfo:
    """解析老版 space_history 动态结构。"""
    import json as _json

    desc = card.get("desc") or {}
    dyn_id = str(desc.get("dynamic_id_str") or desc.get("dynamic_id") or "")
    try:
        c = _json.loads(card.get("card") or "{}")
    except Exception:
        c = {}
    c = c or {}
    title = c.get("title") or ""
    text = c.get("description") or c.get("content") or c.get("text") or ""
    images = []
    if c.get("pic"):
        images.append(c["pic"])
    for p in (c.get("pics") or []):
        if isinstance(p, dict) and p.get("img_src"):
            images.append(p["img_src"])
    item = (c.get("item") or {})
    for p in (item.get("pictures") or []):
        if isinstance(p, dict) and p.get("img_src"):
            images.append(p["img_src"])
    # 转发动态：取原动态内容
    origin = c.get("origin")
    if isinstance(origin, str):
        try:
            o = _json.loads(origin)
            title = title or o.get("title") or ""
            text = text or o.get("description") or ""
            if o.get("pic"):
                images.append(o["pic"])
        except Exception:
            pass
    return DynamicInfo(
        dyn_id=dyn_id, title=_clean_text(title, 60),
        # 老版结构同样要补表情（裸名补方括号、装扮不显示）
        text=_clean_text(_body_text(text, None, _collect_emotes(card))),
        images=images[:DYN_IMG_MAX],
        pubdate=_pick_pub_ts(desc, c, card),
    )
