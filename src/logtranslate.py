# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""日志翻译器：把英文报错翻成中文白话，并指出是哪部分出的问题。

后台日志里混杂了 Python traceback、aiohttp 警告、B 站错误码、QQ 平台错误码，
对不懂代码的人来说完全是天书。这里做两件事：
  1. 归类：判断这条日志属于「机器人 / B站接口 / 面板 / 网络 / 依赖」哪个部分
  2. 翻译：给出中文说明 + 该怎么做

用法：
    from logtranslate import explain
    info = explain("[ERROR] (bot.py:300)_poll_loop 轮询异常：...")
    # → {"part": "机器人", "level": "error", "meaning": "...", "howto": "..."}
"""
import re
import time

# ---------- 模块归属 ----------
# 先按「源文件」判断（bot.py:300 这种最准），再按关键词兜底
FILE_PART = [
    (r"\bbot\.py\b", "机器人", "🤖"),
    (r"\bwebui\.py\b", "管理面板", "🖥️"),
    (r"\bbilibili\.py\b", "B站接口", "📺"),
    (r"\bbili_login\.py\b", "扫码登录", "🔑"),
    (r"\bqqapi\.py\b", "机器人", "🤖"),
    (r"\bnotify\.py\b", "消息推送", "📢"),
    (r"\bstore\.py\b", "数据存储", "💾"),
    (r"\bsecurity\.py\b|\bharden\.py\b|\bsecretbox\.py\b", "安全加固", "🛡️"),
    (r"\bheartbeat\.py\b", "运行状态", "📡"),
    (r"\bcfgutil\.py\b", "配置文件", "⚙️"),
    (r"\bstart\.py\b", "启动器", "🚀"),
    (r"\bcheck\.py\b", "自检", "🩺"),
    (r"\bclient\.py\b|\bgateway\.py\b|\brobot\.py\b|\bconnection\.py\b",
     "机器人连接", "🤖"),
    # ⚠️ http.py 以前没登记，导致网络相关的日志全掉进"其他"
    (r"\bhttp\.py\b", "网络请求", "🌐"),
]

# 错误堆栈：Traceback 后面跟着一堆 `File "xxx", line N, in foo`，
# 这些行没有任何业务特征，以前全掉进"其他"里，把真正的说明淹没了。
# 单独归一类，概览里一眼能看出"有堆栈"。
STACK_PART = ("错误详情", "📕")
_STACK_RE = re.compile(
    r"^\s*(Traceback \(most recent call last\)|File \"|\w*Error:|"
    r"\w*Exception:|\s+at |\s+raise )"
)

PART_RULES = [
    (r"botpy|gateway|ws_connect|ws_identify|access_token|bot_login"
     r"|监听|轮询|推送|订阅|开播|提醒|机器人|@全体|群消息|心跳维持|会话",
     "机器人", "🤖"),
    (r"bilibili|bili_login|wbi|buvid|space\.bilibili|api\.live|polymer"
     r"|BiliError|code=-|UP ?主|投稿|动态|直播间|封面",
     "B站接口", "📺"),
    (r"webui|flask|werkzeug|127\.0\.0\.1 - -|GET /|POST /|/api/",
     "管理面板", "🖥️"),
    (r"heartbeat|hbBox|_hb_task", "运行状态", "📡"),
    (r"security|harden|secretbox|封禁|限流", "安全加固", "🛡️"),
    (r"qrcode|qrcode_key|passport", "扫码登录", "🔑"),
    (r"aiohttp|ClientSession|Unclosed|connector|ClientConnector|TimeoutError",
     "网络请求", "🌐"),
    (r"ModuleNotFoundError|ImportError|pip install|No module",
     "依赖安装", "📦"),
    (r"yaml|config\.yaml|cfgutil|secretbox", "配置文件", "⚙️"),
    (r"store|data\.json|json\.dump", "数据存储", "💾"),
]

# ---------- 错误模式：匹配到就给出中文解释和处理办法 ----------
ERROR_RULES = [
    # --- 机器人 ---
    (r"appid invalid|100007",
     "AppID 填错了，QQ 平台不认这个 ID",
     "去开放平台核对 AppID（纯数字）。改完在面板保存，机器人会自动重连。", "error"),
    (r"secret invalid|100008|获取token失败",
     "AppSecret 不对或已失效",
     "去开放平台重新生成 AppSecret（旧的会立刻失效），填回面板保存。", "error"),
    (r"Unclosed client session|Unclosed connector",
     "有网络连接没正常关闭（程序的小疏漏，不影响功能）",
     "可以忽略。频繁出现说明某次请求失败了，看同一时间有没有其他报错。", "warn"),
    # ⚠️ 这条必须排在通用 "has no attribute" 前面。
    #    以前 'str' object has no attribute 'get' 会被通用规则判成
    #    「代码版本不一致，请用最新版完整替换」——用户照着重装好几遍也没用，
    #    因为真实原因是历史脏数据（旧版本写进去的字符串混在订阅缓存里），
    #    跟版本号毫无关系。翻译指错方向比不翻译更坑。
    (r"'(str|int|float|bool|list|dict|tuple|NoneType)' object has no attribute",
     "读到一条格式不对的旧数据（不是版本问题，也不是你配置错了）",
     "这条会被自动跳过，不影响其它订阅。若反复出现，去面板把该 UP 主的提醒类型重新勾选保存一次。",
     "error"),
    (r"has no attribute",
     "代码版本不一致：新代码调用了旧版本里还没有的功能",
     "你是用旧版本在跑。请完整替换成最新的程序文件后重新启动。", "error"),
    (r"ModuleNotFoundError: No module named '(\w+)'",
     None,  # 动态生成
     "缺这个依赖包。执行：python -m pip install 包名（或双击「安装依赖.bat」）。", "error"),
    (r"websocket|ws_|连接断开|reconnect",
     "机器人的长连接断了，正在自动重连",
     "偶尔出现是正常的，网络波动会自动恢复；持续断连请检查网络。", "warn"),
    (r"获取token失败，请检查appid和secret",
     "机器人登录失败，凭据不对",
     "核对面板「⚙️ 设置」里的 AppID 和 AppSecret。", "error"),

    # --- B 站 ---
    (r"code=-352|msg=-352|-352",
     "B 站风控拦截：这个接口需要登录态",
     "去面板「🔑 B 站登录」扫码登录。登录成功后不用重启，下一轮自动生效。", "error"),
    (r"code=-412|-412",
     "请求太频繁，被 B 站临时限流",
     "程序会自动退避重试。经常出现可以把检查间隔调大（面板设置里）。", "warn"),
    (r"code=-799|-799",
     "请求过频，B 站要求降速",
     "同上，把「检查间隔」调到 90 秒以上。", "warn"),
    (r"code=-404|-404|啥都木有",
     "找不到这个内容（UP 主不存在或已注销）",
     "核对 UID 是否正确。", "warn"),
    (r"code=-403|-403|权限不足",
     "没有权限访问（通常需要登录）",
     "去面板「🔑 B 站登录」扫码登录。", "error"),
    (r"WBI|获取 WBI 密钥失败|wbi",
     "B 站签名参数获取失败（通常是风控）",
     "先扫码登录；仍不行就等一会儿再试。", "error"),
    (r"buvid3|b_3",
     "设备指纹获取失败",
     "一般会自动重试。持续失败时扫码登录即可解决。", "warn"),
    (r"HTTP 403|Request denied|policy_default_denied",
     "被服务器拒绝（IP 或网络环境被限制）",
     "换网络环境，或确认没有走异常代理。", "error"),
    (r"HTTP 412",
     "请求被拦截（B 站风控）",
     "扫码登录后重试。", "error"),

    # --- 网络 ---
    (r"ClientConnectorError|Cannot connect to host|Connection refused",
     "连不上服务器（网络不通或对方没响应）",
     "检查网络；确认目标站点能正常访问。", "error"),
    (r"TimeoutError|timed out|Timeout",
     "请求超时（网络慢或对方没及时响应）",
     "偶尔出现可忽略；频繁出现要检查网络稳定性。", "warn"),
    (r"DNS|Name or service not known|getaddrinfo",
     "域名解析失败（DNS 问题）",
     "检查网络；换个 DNS（如 114.114.114.114）试试。", "error"),
    (r"SSL|Certificate|证书",
     "HTTPS 证书校验失败",
     "检查系统时间是否正确；确认没有中间人代理。", "error"),

    # --- 面板 ---
    (r"Address already in use|端口.*占用|in use",
     "端口被别的程序占了",
     "关掉占用该端口的程序，或换一个端口启动。", "warn"),
    (r"Broken pipe|ConnectionResetError",
     "浏览器提前断开了连接（刷新或关页面时会这样）",
     "正常现象，不用管。", "info"),

    # --- 配置 / 依赖 ---
    (r"缺少配置文件|config\.yaml.*不存在",
     "还没生成配置文件",
     "面板「⚙️ 设置」里填一次保存就会自动生成。", "warn"),
    (r"PermissionError|Permission denied|拒绝访问",
     "没有权限读写文件",
     "确认程序目录没放在系统保护目录里；用管理员身份运行试试。", "error"),
    (r"FileNotFoundError",
     "找不到某个文件",
     "确认完整解压了程序包，没有缺文件。", "error"),
    (r"yaml.*error|YAMLError|解析失败",
     "配置文件格式写错了",
     "检查 config.yaml 的缩进（必须用空格，不能用 Tab）。", "error"),

    # --- 通用 Python ---
    (r"KeyError: '(\w+)'",
     None,
     "程序内部读取了不存在的字段。把这条日志反馈给开发者。", "error"),
    (r"AttributeError",
     "代码版本不一致：调用了不存在的功能",
     "请用最新版本的完整文件替换后重启。", "error"),
    (r"TypeError",
     "传参类型不对（通常是版本混用导致）",
     "确认所有程序文件都是同一版本，别只替换一部分。", "error"),
    (r"UnicodeDecodeError|UnicodeEncodeError|codec can't",
     "编码问题（Windows 中文环境常见）",
     "把程序文件夹移到不带中文的路径，例如 C:\\bili-notify。", "error"),
    (r"MemoryError",
     "内存不足",
     "关掉其他占用内存的程序。", "error"),
]

# ---------- 正常信息也要解释 ----------
# 很多 INFO 行看着像"没问题"，但里面全是术语（基线、静默、轮询），
# 小白根本不知道程序在干嘛。这里给常见的信息行配一句大白话。
INFO_RULES = [
    (r"启动同步完成|已记录为基线|记录为基线",
     "开机自检完成：已把各位 UP 主『当前的状态』记下来当参照。"
     "参照点之后的变化才会提醒，所以开机时不会把你订阅前的内容翻出来推一遍。"),
    (r"监听已启动|开始监听|轮询已启动",
     "开始定时巡查了：按设定的间隔反复检查有没有开播/投稿/新动态。"),
    # ⚠️ 下面三条必须排在「轮询间隔 →」那条**泛化规则之前**：
    #    泛化规则会先把「撞到风控 12s→24s」「风控解除 48s→8s」
    #    「运行平稳 20s→16s」全部匹配掉，于是三种不同的调整在日志页里
    #    显示成**同一句**「检查频率自动调整了…」—— 连着看就是同一条
    #    消息被重复推送了好几遍。改法：先按具体动作给不同的说法，
    #    泛化规则只兜底其它间隔调整。
    (r"撞到\s*B\s*站风控|撞到B站风控",
     "撞到 B 站限流，检查频率已临时放慢（稳定后会自动加快）。"),
    (r"风控风险已解除",
     "B 站限流已过去，检查频率已恢复到最快。"),
    (r"运行平稳",
     "运行平稳，检查频率自动加快了一档。"),
    (r"检查频率加快中",
     "运行平稳，检查频率正在逐步加快（回到最快档后会汇总成一条）。"),
    (r"轮询间隔\s*(\d+)s?\s*→\s*(\d+)s?|间隔.*→",
     "检查频率有调整（撞到 B 站限流就放慢，平稳了再加快）。"),
    (r"机器人.*启动成功|机器人已上线",
     "机器人成功连上 QQ，现在正式开始工作。"),
    (r"程序版本\s*v?([\d.]+)",
     "当前运行的程序版本。"),
    (r"群全量消息解析器已就绪|解析器已就绪",
     "群里直接发斜杠指令的功能已准备好（需要在开放平台订阅对应事件才生效）。"),
    (r"access_token expires_in (\d+)",
     "QQ 登录凭证已拿到，还有一段时间才过期。"),
    (r"登录机器人账号中|bot_login",
     "正在用 AppID 和密钥登录 QQ 机器人。"),
    (r"鉴权中|ws_identify",
     "正在向 QQ 服务器验证身份。"),
    (r"心跳维持启动",
     "长连接保活已开启，用来维持和 QQ 服务器的连接。"),
    (r"会话启动中|程序启动",
     "机器人正在建立连接。"),
    (r"已登记|自动登记",
     "这个群已经被记录到面板里，可以在面板上为它设置订阅了。"),
    (r"已推送|推送成功",
     "提醒消息已成功发到群里。"),
    (r"检测完成|立即检测",
     "本轮检查跑完了。"),
    (r"没有新|无更新|未变化",
     "这一轮没发现新内容，属于正常情况。"),
    (r"已订阅|订阅成功",
     "订阅已生效。"),
    (r"已取消订阅|退订",
     "订阅已取消，之后不会再收到这位 UP 主的提醒。"),
    (r"未开播|live_status=0",
     "当前没有在直播。"),
    (r"正在直播|live_status=1",
     "当前正在直播中。"),
    # ⚠️ 下面这几条以前**没有对应规则**，而它们恰恰是面板上最常见的
    #    几条日志（用户截图里四条里有三条是它们）。没有 meaning 就等于
    #    条目下面不显示任何解释行 —— 用户看到的就是"翻译功能没用"。
    (r"日志级别已更新|日志级别",
     "日志详细程度调整了：级别越详细，记录的内容越多，排查问题时更有用，"
     "但日志文件也会长得更快。"),
    (r"修改配置|保存配置|配置已保存",
     "面板上的设置已保存并生效（凭据、口令、模板这类改动都记在这里）。"),
    (r"测试推送|推送测试",
     "这是一条测试消息，用来确认排版和图片是否正常，不是真的有人开播/投稿。"),
    (r"清理残留进程|已清理残留进程|没有检测到残留进程"
     r"|Checking for leftover|leftover processes",
     "启动前检查有没有上一次没退干净的程序。多份程序同时跑会导致"
     "同一条提醒重复发好几次，所以必须先清干净。"),
    (r"重启机器人|正在重启",
     "机器人正在重新启动，几秒后会自动连回来。"),
]

# 日志级别 → 中文。
# ⚠️ 前端以前直接把 info / warn / debug 原样显示出来，
# 不懂的人看到"机器人 info"根本不知道是什么意思。
LEVEL_CN = {
    "error": "错误",
    "warn": "警告",
    "info": "信息",
    "debug": "调试",
}


def level_cn(level) -> str:
    return LEVEL_CN.get(str(level or "").strip().lower(), str(level or ""))


# 日志级别识别
LEVEL_RULES = [
    (r"\[ERROR\]|\[CRITICAL\]|Traceback|Error:|Exception", "error"),
    (r"\[WARNING\]|WARNING|警告|Warning", "warn"),
    (r"\[INFO\]|INFO", "info"),
    (r"\[DEBUG\]", "debug"),
]

PART_UNKNOWN = ("其他", "📝")


def classify_part(line: str) -> tuple:
    """判断这条日志属于哪个部分，返回 (名称, 图标)。

    优先级：
      1) 错误堆栈（Traceback / File "..." / XxxError:）—— 最优先，
         否则 `File "bot.py"` 会被下面的文件名规则抢走，
         同一段堆栈被拆到好几个类别里，看起来莫名其妙
      2) 「文件名:行号」—— 最可靠的归属依据
      3) 关键词兜底
    """
    # 1) 错误堆栈
    if _STACK_RE.search(line or ""):
        return STACK_PART
    # 2) 文件名
    for pat, name, icon in FILE_PART:
        if re.search(pat, line, re.I):
            return name, icon
    for pat, name, icon in PART_RULES:
        if re.search(pat, line, re.I):
            return name, icon
    return PART_UNKNOWN


def detect_level(line: str) -> str:
    for pat, lv in LEVEL_RULES:
        if re.search(pat, line):
            return lv
    return "info"


# 日志行里可能的时间戳写法，按常见程度排列
# ⚠️ 关键：秒后面可能有毫秒（,088），必须一起吃掉。
# Python logging 默认 asctime 就是 `2026-09-20 06:22:53,088`（逗号+毫秒），
# botpy 库用的正是默认格式。之前正则只匹配到秒，剥掉时间戳后
# 行首会留下一个孤零零的 `,088` —— 用户截图里那个红框里的碎片就是它。
_MS = r"(?:[.,]\d{1,6})?"          # 可选的毫秒部分
_TS_PATTERNS = [
    # 2026-09-19 14:03:12[,088]   （本项目 bot.log / botpy 的格式）
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})" + _MS),
     "%Y-%m-%d"),
    # 2026/09/19 14:03:12[,088]
    (re.compile(r"\b(\d{4}/\d{2}/\d{2})[ T](\d{2}:\d{2}:\d{2})" + _MS),
     "%Y/%m/%d"),
    # 09-19 14:03:12[,088]   （旧格式，缺年份 → 用文件修改年份兜底）
    (re.compile(r"\b(\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})" + _MS), None),
    # 19/Sep/2026:04:03:12[,088]   （Flask / access log）
    (re.compile(r"\b(\d{2})/([A-Za-z]{3})/(\d{4}):(\d{2}:\d{2}:\d{2})" + _MS),
     "apache"),
]
_MON = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def extract_time(line: str) -> dict:
    """从日志行里抽时间。返回 {"date": "YYYY-MM-DD", "time": "HH:MM:SS"}。

    抽不到就返回空 —— 前端会把这些归到「未标注时间」一组，
    而不是乱塞进某个日期里。
    """
    out = {"date": "", "time": ""}
    if not line:
        return out

    for pat, kind in _TS_PATTERNS:
        m = pat.search(line)
        if not m:
            continue
        try:
            if kind in ("%Y-%m-%d", "%Y/%m/%d"):
                d = m.group(1).replace("/", "-")
                out["date"], out["time"] = d, m.group(2)
                return out
            if kind is None:
                # 缺年份：用当前年份兜底
                mm, dd = m.group(1).split("-")
                out["date"] = f"{time.localtime().tm_year}-{mm}-{dd}"
                out["time"] = m.group(2)
                return out
            if kind == "apache":
                mon = _MON.get(m.group(2).lower(), 1)
                out["date"] = f"{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}"
                out["time"] = m.group(4)
                return out
        except Exception:
            continue
    return out


# ---------- 代码符号翻译 ----------
# 日志里到处是 `bot.py:1112` `_check_top_comment` `update_access_token`
# 这种名字，不懂代码的人完全不知道在说什么。这里把常见符号译成中文。
# ---------- 推送类型 / 日志级别 ----------
# 审计日志以前直接写英文 kind，翻译器又只认得 offline（→"离线"），
# 于是同一行出现 "dynamic/video/离线/live" 这种中英混杂 ——
# 用户看到就是"这日志根本没翻译"。
# ⚠️ 这两张表**不进 PLAIN_WORDS**：live/video/dynamic 太通用，
#    全局替换会误伤 live.bilibili.com、video/ 这类，只在特定上下文用。
KIND_WORDS = [
    ("dynamic_pinned", "置顶动态"),
    ("top_comment", "置顶评论"),
    ("season", "合集"),
    ("offline", "下播"),
    ("live", "开播"),
    ("video", "投稿"),
    ("dynamic", "动态"),
]
LEVEL_WORDS = [
    ("DEBUG", "调试"),
    ("INFO", "信息"),
    ("WARNING", "警告"),
    ("ERROR", "错误"),
    ("CRITICAL", "严重"),
]

# ---------- 大白话词汇表 ----------
# 给不懂代码的人看：这些词在日志里到处都是，但不翻译根本不知道在说什么。
# 顺序有意义：长的先替换，避免短的先匹配把长的拆坏。
PLAIN_WORDS = [
    # 长词必须排在短词前面，否则短的先匹配会把长的拆坏
    ("bili_cookie", "B站登录状态"),
    ("cookie_expires", "登录状态有效期"),
    ("access_token", "登录凭证"),
    ("appid", "AppID"),
    ("clientSecret", "密钥"),
    ("secret", "密钥"),
    ("cookie", "登录状态"),
    ("SESSDATA", "登录票据"),
    ("buvid3", "设备标识"),
    ("wbi", "接口签名"),
    ("timeout", "超时"),
    ("timed out", "超时"),
    ("retry", "重试"),
    ("retrying", "正在重试"),
    ("reconnect", "重新连接"),
    ("connected", "已连接"),
    ("disconnected", "连接已断开"),
    ("success", "成功"),
    ("successfully", "成功"),
    ("failed", "失败"),
    ("failure", "失败"),
    ("invalid", "无效"),
    ("expired", "已过期"),
    ("expire", "过期"),
    ("refused", "被拒绝"),
    ("denied", "被拒绝"),
    ("unauthorized", "未授权"),
    ("forbidden", "没有权限"),
    ("not found", "没找到"),
    ("missing", "缺少"),
    ("skipped", "已跳过"),
    ("ignored", "已忽略"),
    ("cancelled", "已取消"),
    ("canceled", "已取消"),
    ("pending", "等待中"),
    ("ready", "就绪"),
    ("alive", "在线"),
    ("offline", "离线"),
    ("online", "在线"),
    ("heartbeat", "心跳"),
    ("polling", "轮询"),
    ("interval", "间隔"),
    ("baseline", "基线"),
    ("cache", "缓存"),
    ("cached", "已缓存"),
    ("dedupe", "去重"),
    ("duplicate", "重复"),
    ("throttle", "限流"),
    ("ratelimit", "限流"),
    ("rate limit", "限流"),
    ("banned", "已封禁"),
    ("unban", "解封"),
    ("verify", "校验"),
    ("verified", "已校验"),
    ("register", "登记"),
    ("registered", "已登记"),
    ("sync", "同步"),
    ("synchronized", "已同步"),
    ("migrate", "迁移"),
    ("migration", "迁移"),
    ("deprecated", "已废弃"),
    ("removed", "已移除"),
    ("added", "已添加"),
    ("updated", "已更新"),
    ("deleted", "已删除"),
    ("saved", "已保存"),
    ("loaded", "已读取"),
    ("started", "已启动"),
    ("stopped", "已停止"),
    ("running", "运行中"),
    ("finished", "已完成"),
    ("completed", "已完成"),
    ("enabled", "已开启"),
    ("disabled", "已关闭"),
    ("skipping", "跳过"),
    ("refresh", "刷新"),
    ("refreshed", "已刷新"),
]

# Python 异常名 → 大白话
EXCEPTION_NAMES = [
    ("ModuleNotFoundError", "缺少模块【找不到这个组件】"),
    ("ImportError", "导入失败【组件装不上或版本不对】"),
    ("AttributeError", "属性错误【新旧版本对不上】"),
    ("KeyError", "键不存在【读了个没有的字段】"),
    ("TypeError", "类型错误【参数格式不对】"),
    ("ValueError", "取值错误【填的内容不合法】"),
    ("NameError", "名字未定义【代码版本不一致】"),
    ("IndexError", "下标越界【取了个不存在的位置】"),
    ("FileNotFoundError", "找不到文件"),
    ("PermissionError", "没有权限【读写被拒绝】"),
    ("ConnectionError", "连接失败"),
    ("ConnectionRefusedError", "连接被拒绝【对方没开或端口不对】"),
    ("ConnectionResetError", "连接被断开【对方主动关了】"),
    ("TimeoutError", "超时【等太久没响应】"),
    ("UnicodeDecodeError", "解码失败【文件编码不对】"),
    ("UnicodeEncodeError", "编码失败【中文输出不了】"),
    ("ZeroDivisionError", "除零错误"),
    ("MemoryError", "内存不足"),
    ("RecursionError", "递归太深【程序卡在自己的循环里】"),
    ("OSError", "系统错误"),
    ("RuntimeError", "运行时错误"),
    ("StopIteration", "遍历结束"),
    ("AssertionError", "断言失败【程序内部检查没通过】"),
]

# 第三方库 → 它的作用（说名字没人知道是干嘛的）
LIB_NAMES = [
    ("botpy", "QQ机器人官方库"),
    ("aiohttp", "网络请求库"),
    ("flask", "面板服务"),
    ("werkzeug", "面板服务"),
    ("yaml", "配置文件"),
    ("qrcode", "二维码"),
    ("playwright", "浏览器登录工具"),
    ("PIL", "图片处理"),
    ("Pillow", "图片处理"),
    ("asyncio", "异步调度"),
    ("sqlite3", "数据库"),
    ("requests", "网络请求库"),
]

# 直播状态码：0=未开播，1=已开播，2=轮播中
# ⚠️ 1 说「已开播」而不是「直播中」——跟 0「未开播」成对，
#    也跟 bilibili.live_status_cn() 保持一致（两处必须同口径，
#    否则会出现"面板说已开播、日志说直播中"的割裂）。
LIVE_STATUS = {
    "0": "未开播",
    "1": "已开播",
    "2": "轮播中",
}

SYMBOL_NAMES = {
    # 文件
    "bot.py": "机器人主程序",
    "webui.py": "管理面板",
    "bilibili.py": "B站接口",
    "bili_login.py": "B站扫码登录",
    "bili_browser_login.py": "B站浏览器登录",
    "qqapi.py": "QQ接口",
    "notify.py": "消息排版",
    "media.py": "图片处理",
    "db.py": "数据库",
    "store.py": "旧版存储(已废弃)",
    "security.py": "安全",
    "harden.py": "加固",
    "secretbox.py": "凭据加密",
    "heartbeat.py": "运行状态",
    "cfgutil.py": "配置文件",
    "start.py": "启动器",
    "check.py": "自检",
    "cleanup.py": "进程清理",
    "version.py": "版本号",
    "logtranslate.py": "日志翻译",
    "client.py": "机器人连接",
    "gateway.py": "机器人连接",
    "robot.py": "机器人登录",
    "connection.py": "机器人连接",
    "http.py": "网络请求",
    # 方法 / 类名
    # ⚠️ __init__ / api_xxx 这类最常见的名字以前**没登记**，于是日志里
    #    翻出来是 `(机器人主程序)__init__`、`(管理面板)api_save` —— 文件名
    #    翻了、方法名还是英文，半中半英看着就像"翻译没生效"。
    "__init__": "初始化",
    "api_save": "保存配置",
    "api_config": "保存配置",
    "api_log": "运行日志",
    "api_log_clear": "清空日志",
    "api_state": "面板数据",
    "api_sub": "订阅设置",
    "api_sub_set": "订阅设置",
    "api_test": "测试推送",
    "api_selftest": "全面自检",
    "api_version": "更新日志",
    "api_login": "登录校验",
    "api_restart": "重启机器人",
    "api_cleanup": "清理进程",
    "api_media": "图片缓存",
    "_check_live": "检查直播",
    "_check_video": "检查投稿",
    "_check_dynamic": "检查动态",
    "_check_top_comment": "检查置顶评论",
    "_poll_loop": "轮询主循环",
    "_poll_once": "单轮检测",
    "_startup_sync": "启动静默同步",
    "_do_startup_sync": "启动静默同步",
    "_check_seasons": "检查合集",
    "_safe": "单项检测",
    "_adapt_interval": "调整检测间隔",
    "_push": "发送提醒",
    "_handle": "处理指令",
    "on_ready": "机器人上线",
    "on_group_at_message_create": "收到群@消息",
    "on_group_message_create": "收到群消息",
    "on_interaction_create": "指令面板交互",
    "on_group_msg_receive": "群开启主动消息",
    "on_group_msg_reject": "群关闭主动消息",
    "update_access_token": "换取登录凭证",
    "_bot_login": "机器人登录",
    "_bot_init": "机器人初始化",
    "bot_connect": "建立连接",
    "ws_connect": "连接服务器",
    "ws_identify": "身份验证",
    "on_message": "收到消息",
    "_send_heart": "心跳维持",
    "multi_run": "启动会话",
    "check_token": "检查凭证",
    "check_session": "检查会话",
    "get_pinned_dynamic": "取置顶动态",
    "get_pinned_comment": "取置顶评论",
    "get_latest_dynamic": "取最新动态",
    "get_latest_video": "取最新投稿",
    "get_live": "取直播状态",
    "get_room_id_by_mid": "查直播间号",
    "get_up": "查UP主信息",
    "ensure_buvid3": "获取设备标识",
    "_throttle": "请求限流",
    "send_text": "发文本消息",
    "send_markdown": "发Markdown消息",
    "send_image": "发图片",
    "get_token": "换取凭证",
    "_cmd_check_now": "立即检测",
    "_cmd_list": "订阅列表",
    "_cmd_subscribe": "订阅",
    "_cmd_unsubscribe": "退订",
    "_cmd_status": "运行状态",
    "_cmd_panel": "面板地址",
    "_fetch_group_name": "获取群名",
    "_reload_cookie": "重载cookie",
    "sync_cfg_from_disk": "热更新配置",
    "sync_log_level": "同步日志级别",
    "apply_size_hint": "应用图片尺寸提示",
    "apply_inline_pic_size": "应用卡片图片尺寸",
    "_note": "记录检测结果",
    "patch_botpy_group_message_parser": "修复群消息解析",
    "strip_mention": "去掉@提及",
    "_normalize_cmd": "整理指令",
    "validate_kind": "校验订阅类型",
    "explain": "日志翻译",
}


# 配置项名 → 中文。
# 「配置已热更新：image_size_hint_h」这种行，键名原样显示等于没翻译。
# ⚠️ 这些键名只在**含「配置」二字**的行里替换（见 translate_symbols），
#    否则 poll_interval 之类会误伤普通文本。
CFG_KEY_NAMES = {
    "image_size_hint": "图片尺寸提示开关",
    "image_size_hint_w": "图片尺寸提示-宽",
    "image_size_hint_h": "图片尺寸提示-高",
    "image_size_hint_avatar": "头像尺寸提示",
    "image_send_mode": "图片发送方式",
    "image_inline_width": "卡片内图片宽度",
    "image_tmp_ttl": "临时图片保留秒数",
    "send_image": "提醒带图片",
    "notify_offline": "下播提醒",
    "message_style": "纯文本消息",
    "log_translate": "日志中文翻译",
    "log_debug": "输出调试日志",
    "is_sandbox": "沙箱环境",
    "sandbox_gid": "沙盒群",
    "poll_interval": "检查间隔",
    "poll_interval_min": "检查间隔下限",
    "poll_interval_max": "检查间隔上限",
    "min_request_interval": "最小请求间隔",
    "slow_check_every": "投稿动态每N轮",
    "dedupe_window": "去重时间窗",
    "request_timeout": "请求超时",
    "season_max_pages": "合集最多翻页数",
    "season_push_limit": "合集单次最多推送",
    "silence_dyn_after_push": "开播后动态静默",
    "top_comment_only_up": "只推UP主本人评论",
    "selfcheck_uid": "测试UID",
    "data_file": "数据文件",
}


def translate_symbols(text: str) -> str:
    """把日志里的文件名、类名、方法名换成中文。

    `(bot.py:1112)_check_top_comment`
      → `(机器人主程序) 检查置顶评论`
    """
    if not text:
        return ""

    def _file(m):
        name = m.group(1)
        cn = SYMBOL_NAMES.get(name) or name
        # 原文若是 (xxx.py:123) 已有括号，这里不再套一层
        pre = text[max(0, m.start() - 1):m.start()]
        return cn if pre == "(" else f"「{cn}」"

    def _sym(m):
        name = m.group(0)
        return SYMBOL_NAMES.get(name, name)

    # 文件名（带行号）
    text = re.sub(r"\b([a-z_]+\.py):\d+", _file, text)
    # 文件名（不带行号）
    text = re.sub(r"\b([a-z_]+\.py)\b", _file, text)
    # 方法名 / 类名
    for en, cn in SYMBOL_NAMES.items():
        if en.endswith(".py"):
            continue
        text = re.sub(r"\b" + re.escape(en) + r"\b", cn, text)

    # 级别
    # ⚠️ 必须忽略大小写：审计日志写的是**小写** `[info]` / `[warn]`
    #    （webui 的 AUDIT.log 直接把 level 原样写进方括号），
    #    以前只认大写 `[INFO]`，于是最常见的那几条永远是半中半英 ——
    #    用户看到 `[info] 127.0.0.1 测试推送 …` 就认定"翻译没生效"。
    for en, cn in (("CRITICAL", "严重"), ("WARNING", "警告"), ("WARN", "警告"),
                   ("ERROR", "错误"), ("DEBUG", "调试"), ("INFO", "信息")):
        text = re.sub(r"\[\s*" + en + r"\s*\]", f"[{cn}]", text, flags=re.I)

    # 配置项名 → 中文
    # ⚠️ 只在明确写到了「配置」的行里替换：poll_interval 这类词太通用，
    #    全局替换会把普通句子里的同名词也改掉。
    if "配置" in text:
        for k, cn in CFG_KEY_NAMES.items():
            text = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(k)
                          + r"(?![A-Za-z0-9_])", cn, text)

    # 常见英文词换成大白话（只在"独立单词"位置替换，
    # 不会动到变量名、UID、域名这些）
    for en, cn in PLAIN_WORDS:
        # ⚠️ 前界必须排除下划线。以前写成 (?<![A-Za-z0-9./-])，下划线不在
        #    排除集里，于是 sync / cookie 这类词会被塞进标识符内部替换：
        #      _do_startup_sync → _do_startup_同步
        #      _reload_cookie   → _reload_登录凭据
        #    好好的方法名被翻得半中半英，比不翻还难看。
        #    代价是 bili_cookie 这种"下划线开头的独立词"不再被翻，
        #    但标识符完整比多翻一个词重要得多。
        text = re.sub(r"(?<![A-Za-z0-9._/-])" + re.escape(en) + r"(?![\w./-])",
                      cn, text, flags=re.I)

    # Python 异常名：ModuleNotFoundError 这种，不懂代码根本看不出是"缺东西"
    for en, cn in EXCEPTION_NAMES:
        text = re.sub(r"\b" + re.escape(en) + r"\b", cn, text)

    # ⚠️ 关键：No module named 'xxx' / pip install xxx 里的**包名必须保留原文**。
    # 翻成中文用户就不知道该装什么了（"缺少模块：网络请求库"没法执行）。
    # 所以先把这些包名占位保护起来，翻完再放回去。
    _keep = {}

    def _hold(m):
        key = "\x00K%d\x00" % len(_keep)
        _keep[key] = m.group(0)
        return key

    text = re.sub(r"No module named ['\"]?([A-Za-z0-9_\-\.]+)['\"]?",
                  lambda m: "No module named " + _hold(re.match(r"([A-Za-z0-9_\-\.]+)", m.group(1))),
                  text)
    text = re.sub(r"(?i)pip install\s+([A-Za-z0-9_\-\.=<>\[\]]+)",
                  lambda m: "pip install " + _hold(re.match(r"([A-Za-z0-9_\-\.=<>\[\]]+)", m.group(1))),
                  text)
    # 「缺少依赖：a、b」这类提示里的包名同样要保留
    text = re.sub(r"(缺少依赖[:：]\s*)([^\n]*)",
                  lambda m: m.group(1) + _hold(re.match(r"([^\n]*)", m.group(2))),
                  text)

    # 第三方库名：说出来没人知道是什么，换成它的作用
    for en, cn in LIB_NAMES:
        text = re.sub(r"\b" + re.escape(en) + r"\b", cn, text)

    for k, v in _keep.items():
        text = text.replace(k, v)

    # 状态值：live_status=1 这种，光看数字不知道是啥
    text = re.sub(r"live_status=(\d)",
                  lambda m: "直播状态=" + LIVE_STATUS.get(m.group(1), m.group(1)),
                  text)
    text = re.sub(r"\bcode=(-?\d+)",
                  lambda m: "错误码=" + m.group(1), text)

    # 日志里残留的英文 kind / 级别（历史行）。
    # ⚠️ 只在这几类**明确的上下文**里替换 —— live/video/dynamic 这类词
    #    太通用（live.bilibili.com、video/x 之类），全局替换必误伤。
    #    所以：推送类型只在"测试推送"这行里翻；级别只在"日志级别"这行翻。
    # ⚠️⚠️ 这两处的**前界必须放行斜杠**。
    #    日志里推送类型是斜杠连写的：`测试推送 dynamic/video/offline/live`。
    #    以前沿用了通用词表的前界 (?<![A-Za-z0-9._/-])，把 `/` 也排除了 ——
    #    于是 video / live / dynamic 前面是斜杠，**一个都匹配不上**，
    #    整行翻出来还是半中半英（用户看到的就是"翻译没生效"）。
    #    因为只在上面两个明确上下文里替换，不存在误伤域名/路径的风险。
    if "测试推送" in text:
        for en, cn in KIND_WORDS:
            # 后界同样要放行 `/`（斜杠连写时每个词后面都跟着斜杠），
            # 但仍排除 `.` 和 `-`，避免误伤 live.bilibili.com 这类域名。
            text = re.sub(r"(?<![A-Za-z0-9._])" + re.escape(en)
                          + r"(?![\w.-])", cn, text)
    if "日志级别" in text:
        for en, cn in LEVEL_WORDS:
            text = re.sub(r"(?<![A-Za-z0-9._])" + re.escape(en)
                          + r"(?![\w./-])", cn, text)
    return text


def strip_timestamp(line: str) -> str:
    """去掉行首的时间戳。

    时间轴（大日期分组 + 每条左边的时间）已经把时间显示出来了，
    条目正文里再带一份 `2026-09-19 14:03:12` 就是重复，看着很乱。
    """
    if not line:
        return ""
    for pat, kind in _TS_PATTERNS:
        m = pat.search(line)
        if not m:
            continue
        # 只剥行首（允许少量前导空白），行中间的时间保留
        if m.start() <= len(line) - len(line.lstrip()) + 2:
            return line[m.end():].lstrip()
    return line


def explain(line: str) -> dict:
    """把一行日志翻译成人话。"""
    line = line or ""
    part, icon = classify_part(line)
    level = detect_level(line)

    meaning, howto, lv = None, None, level
    for pat, mean, how, lv_hint in ERROR_RULES:
        m = re.search(pat, line, re.I)
        if not m:
            continue
        # 动态生成解释（把正则捕获组填进去）
        if mean is None:
            if "ModuleNotFoundError" in pat:
                mean = f"缺少依赖包：{m.group(1)}"
                how = how.replace("包名", m.group(1))
            elif "KeyError" in pat:
                mean = f"程序读取了不存在的字段「{m.group(1)}」"
        meaning, howto = mean, how
        lv = lv_hint
        break

    # 没匹配到错误规则时，再看看有没有对应的"信息说明"
    if meaning is None:
        for pat, mean in INFO_RULES:
            if re.search(pat, line, re.I):
                meaning = mean
                break

    ts = extract_time(line)
    return {
        "part": part,
        "icon": icon,
        "level": lv,
        "level_cn": level_cn(lv),
        "date": ts.get("date", ""),
        "time": ts.get("time", ""),
        "known": meaning is not None,
        "meaning": meaning or "",
        "howto": howto or "",
        # 时间已由时间轴显示，正文里不再重复写
        "raw": strip_timestamp(line),
    }


def summarize(lines) -> dict:
    """对一批日志做汇总：各部分的错误数、最需要关注的问题。"""
    counts = {}
    issues = []
    for ln in lines:
        if not ln.strip():
            continue
        info = explain(ln)
        part = info["part"]
        c = counts.setdefault(part, {"part": part, "icon": info["icon"],
                                     "total": 0, "error": 0, "warn": 0})
        c["total"] += 1
        if info["level"] == "error":
            c["error"] += 1
        elif info["level"] == "warn":
            c["warn"] += 1
        if info["known"] and info["level"] in ("error", "warn"):
            issues.append(info)

    # 去重（同一问题只保留一条），错误优先
    seen = set()
    uniq = []
    for it in sorted(issues, key=lambda x: 0 if x["level"] == "error" else 1):
        key = it["meaning"]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    total_err = sum(c["error"] for c in counts.values())
    total_warn = sum(c["warn"] for c in counts.values())
    if total_err:
        health, health_txt = "bad", f"发现 {total_err} 处错误"
    elif total_warn:
        health, health_txt = "warn", f"{total_warn} 处警告，暂无错误"
    else:
        health, health_txt = "ok", "运行正常"

    return {
        "parts": sorted(counts.values(), key=lambda x: -x["error"]),
        "issues": uniq[:10],
        "total_error": total_err,
        "total_warn": total_warn,
        "health": health,
        "health_text": health_txt,
    }


if __name__ == "__main__":
    # 自测：拿用户真实日志里的几条试一下
    samples = [
        "[ERROR] (bot.py:300)_poll_loop  轮询异常：'NotifyBot' object has no attribute '_reload_cookie'",
        "[ERROR] (robot.py:63)update_access_token [botpy] 获取token失败，请检查appid和secret填写是否正确！",
        "Unclosed client session",
        "127.0.0.1 - - [19/Sep/2026 04:03:12] \"GET /api/state HTTP/1.1\" 200 -",
        "[INFO]  (gateway.py:85)on_message [botpy] 机器人「OnO_开播提醒工具人」启动成功！",
        "ModuleNotFoundError: No module named 'botpy'",
        "BiliError: code=-352 msg=-352",
        "aiohttp.client_exceptions.ClientConnectorError: Cannot connect to host",
        "监听已启动：每 60s 检查直播",
    ]
    for s in samples:
        r = explain(s)
        tag = f"{r['icon']}{r['part']}"
        print(f"[{r['level']:5s}] {tag:12s} {r['meaning'] or '(无匹配)'}")
        if r["howto"]:
            print(f"          → {r['howto']}")
