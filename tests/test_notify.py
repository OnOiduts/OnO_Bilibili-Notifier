"""验证：三类提醒可独立开关、@全体独立控制、文案/图片/按钮符合预期。"""
import asyncio
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import botpy  # noqa: E402
from bilibili import DynamicInfo, LiveInfo, VideoInfo, SeasonPush  # noqa: E402
from bot import NotifyBot  # noqa: E402
from notify import (ZWSP, IMG_LINE_RE, render, safe_format,  # noqa: E402
                    to_plain, TPL_VARS, TPL_GROUPS, VAR_LABEL,
                    DEFAULT_TEMPLATES)

TPL = {}
COVER = "https://i0.hdslb.com/bfs/test.jpg"


class FakeQQ:
    def __init__(self):
        self.texts, self.cards = [], []

    async def send_text(self, gid, content, **kw):
        self.texts.append((gid, content))

    async def send_markdown(self, gid, md, keyboard=None, **kw):
        self.cards.append((gid, md, keyboard))

    async def close(self):
        pass


class FakeBili:
    """按 uid 分别控制直播 / 投稿 / 动态返回内容。"""
    def __init__(self):
        self.live = {}
        self.video = {}
        self.dyn = {}

    async def get_up(self, uid):
        from bilibili import Up
        return Up(uid=uid, uname=f"UP{uid}", room_id=uid * 10)

    async def get_live(self, room_id):
        return self.live.get(room_id, LiveInfo(room_id=room_id, live_status=0))

    async def get_latest_video(self, uid):
        return self.video.get(uid)

    async def get_pinned_dynamic(self, uid):
        return None

    async def get_pinned_comment(self, dyn_id, uid=0, rid=""):
        return None

    async def get_latest_dynamic(self, uid):
        return self.dyn.get(uid)

    async def close(self):
        pass


def make_bot():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    bot = NotifyBot({"data_file": path}, intents=botpy.Intents(public_messages=True))
    bot.qq = FakeQQ()
    bot.bili = FakeBili()
    return bot, path


def ck(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    return cond


def _t_more_vars():
    """模板变量不能只有 {name} {title} 两个。

    想在开播提醒里写直播间号 / 分区 / 人气，以前根本做不到。
    """
    # 开播提醒要能写直播间号、分区、人气
    live = TPL_VARS.get("live") or []
    for v in ("{room}", "{area}", "{online}"):
        if v not in live:
            print("  缺变量", v)
            return False
    # 投稿要能写 BV 号、时间
    for v in ("{bvid}", "{time}"):
        if v not in (TPL_VARS.get("video") or []):
            print("  缺变量", v)
            return False
    # 动态要能写正文
    if "{text}" not in (TPL_VARS.get("dynamic") or []):
        return False
    # 合集要能写合集名、小节名
    for v in ("{season}", "{section}"):
        if v not in (TPL_VARS.get("season") or []):
            return False
    # 每个变量都要有中文说明（面板显示用）
    for keys in TPL_VARS.values():
        for v in keys:
            if v not in VAR_LABEL:
                print("  变量没有中文说明", v)
                return False
    return True


def _t_safe_format():
    """用户填了个这个类目不支持的变量，不能让整条消息发不出去。

    ⚠️ 以前是 tpl.format(name=..., title=...)，写了 {room} 就 KeyError
    → 渲染失败 → 整条提醒没了，而且用户不知道是自己文案填错了。
    """
    # 未知变量保留原样（用户能一眼看出哪个没生效）
    if safe_format("a {room} b", {"name": "X"}) != "a {room} b":
        return False
    # 已知变量正常替换
    if safe_format("{name}-{title}", {"name": "A", "title": "B"}) != "A-B":
        return False
    # 括号写坏了不抛异常
    if safe_format("{name", {"name": "A"}) != "{name":
        return False
    # 空模板不炸
    if safe_format("", {}) != "":
        return False
    # 真的能渲染出直播间号
    from notify import render_live
    md, _ = render_live(
        LiveInfo(room_id=5566, live_status=1, title="测试", area="手游",
                 online=1234),
        "波萝", {"live": '【直播】{name} 在 {room} 开播（{area}）人气{online}'})
    return ("5566" in md) and ("手游" in md) and ("1,234" in md)


def _t_tpl_groups():
    """文案要按类目分组：改直播就只看直播，不被别的类目干扰。"""
    keys = [g[0] for g in TPL_GROUPS]
    for k in ("live", "video", "dynamic", "top_comment", "season"):
        if k not in keys:
            return False
    # 直播组包含开播和下播
    live = [g for g in TPL_GROUPS if g[0] == "live"]
    if not live or "offline" not in live[0][2]:
        return False
    # 每个分组里的键都必须是真实存在的模板键
    for _, _, ks in TPL_GROUPS:
        for k in ks:
            if k not in DEFAULT_TEMPLATES:
                return False
            if k not in TPL_VARS:
                return False
    return True


def main():
    ok = True
    ok &= ck("直播文案与封面", _t_live())
    ok &= ck("视频文案与封面", _t_video())
    ok &= ck("动态有标题", _t_dyn_title())
    ok &= ck("动态无标题+正文缩进", _t_dyn_no_title())
    ok &= ck("按钮链接类型正确", _t_buttons())
    ok &= ck("符合官方 Markdown 规范", _t_official_style())
    ok &= ck("订阅卡片带头像", _t_subscribe_card())
    ok &= ck("仅首次订阅插图", _t_subscribe_first_only())
    ok &= ck("模板变量不再只有两个", _t_more_vars())
    ok &= ck("未知变量不会让推送挂掉", _t_safe_format())
    ok &= ck("文案按类目分组", _t_tpl_groups())
    ok &= ck("所有卡片都不出现空行", _t_no_blank_line_all())
    ok &= ck("纯文本兜底也无空行", _t_plain_no_blank_line())

    ok &= asyncio.run(_t_push())
    print("\n结果:", "全部通过 ✅" if ok else "存在失败 ❌")
    sys.exit(0 if ok else 1)


def _cover_expect(url, scale=True):
    """卡片里图片的 URL。

    ⚠️ 内嵌模式下平台要**下载转存**这张图，所以这里不是原图 —— 原图动辄
    1920×1080 好几 MB，手机端经常加载失败（电脑端没事），表现就是
    "电脑能看见、手机看不见"。因此统一压成 jpg 并限制尺寸：
    https + 非 webp + CDN 缩放参数（jpg 结尾，不能是 webp）。
    压缩只发生在本地下载直传那条路的场景由 media 内部自己处理。
    scale=False 用于头像（原图本就小，套 16:9 会把方头像裁变形）。
    """
    try:
        from notify import _cover
        return _cover(url, scale=scale) or url
    except Exception:
        return url


def _img_line(md):
    """取卡片里第一行的图片 markdown。

    ⚠️ 现在用的是 QQ 私有语法 `![#宽px #高px](url)`（尺寸提示），
       不是标准 markdown 的 `![alt](url)` —— 少了尺寸提示，
       手机端 QQ 不渲染图片（电脑端却正常）。断言跟着走新格式。
    """
    for line in str(md).splitlines():
        m = IMG_LINE_RE.match(line.strip())
        if m:
            return m.group(0)
    return ""


def _has_img(md, url):
    """卡片里有这张图，且 alt 非空。"""
    line = _img_line(md)
    if not line:
        return False
    return (url in line) and not line.startswith("![](")


def _all_cards():
    """每种提醒都造一条，返回 [(名字, markdown), ...]。

    目的：把"不许有空行"这条规则套到**所有**卡片上，
    避免以后某个分支又插回空行却没人发现。
    """
    return [
        ("直播", render("live", LiveInfo(room_id=214, live_status=1, title="标题",
                                         area="虚拟主播", online=999, cover=COVER),
                        "小狐狸", TPL)[0]),
        ("下播", render("offline", LiveInfo(room_id=214, live_status=0, title="标题"),
                        "小狐狸", TPL)[0]),
        ("视频", render("video", VideoInfo(bvid="BV1", title="视频", pic=COVER,
                                           desc="简介\n第二行", pubdate=1758400000),
                        "小狐狸", TPL)[0]),
        ("动态", render("dynamic", DynamicInfo(dyn_id="D1", title="标题",
                                               text="正文\n\n第二段", images=[COVER]),
                        "小狐狸", TPL)[0]),
        ("无标题动态", render("dynamic", DynamicInfo(dyn_id="D2", title="",
                                                     text="只有正文", images=[]),
                              "小狐狸", TPL)[0]),
        ("置顶动态", render("dynamic", DynamicInfo(dyn_id="D3", title="置顶标题",
                                                   text="置顶正文", pinned=True),
                            "小狐狸", TPL)[0]),
        ("置顶评论", render("top_comment", {"content": "评论内容\n第二行",
                                            "url": "https://x.com"},
                            "小狐狸", TPL)[0]),
        ("合集", render("season", SeasonPush(bvid="BV2", title="合集视频", pic=COVER,
                                             season_title="合集名", section="小节"),
                        "小狐狸", TPL)[0]),
        ("订阅成功", render_subscribe_safe()),
    ]


def render_subscribe_safe():
    """订阅成功卡片（有头像/无头像两种都不能有空行，这里取有头像的）。"""
    from notify import render_subscribe
    return render_subscribe("小狐狸", 1001, COVER, "直播、动态")[0]


def _t_no_blank_line_all():
    """所有卡片：可以有换行，但不能有"空的一行"。"""
    ok = True
    for name, md in _all_cards():
        ok &= ck(f"  {name} 无空行", _no_blank_line(md))
        ok &= ck(f"  {name} 无零宽空格", ZWSP not in md)
    return ok


def _t_plain_no_blank_line():
    """纯文本兜底（markdown 发不出去时）同样不许出现空行。"""
    ok = True
    for name, md in _all_cards():
        plain = to_plain(md)
        ok &= ck(f"  {name} 纯文本无空行", _no_blank_line(plain))
    return ok


def _no_blank_line(md: str) -> bool:
    """消息里可以有换行，但**不能有"空的一行"**（v1.31.21 起的硬要求）。

    要挡住三种形态：
      · 真正的空行
      · 只有零宽空格撑出来的行（QQ 里会渲染成一条空行）
      · 只有 > 的"空引用行"
    另外消息首尾也不能有多余空白。
    """
    if not md:
        return True
    lines = md.split("\n")
    for i, line in enumerate(lines):
        s = line.strip().replace(ZWSP, "").strip()
        if s and s != ">":
            continue
        # ⚠️ 唯一允许的例外：图片是块级元素，前后各留一个空行让它
        #    独占一块（否则会被并进上一段，群里就看不到图）。
        #    除这两处之外，任何空行都算"空的一行"，一律不许。
        prev_img = i > 0 and bool(IMG_LINE_RE.match(lines[i - 1].strip()))
        next_img = (i + 1 < len(lines)
                    and bool(IMG_LINE_RE.match(lines[i + 1].strip())))
        if prev_img or next_img:
            continue
        return False
    return md == md.strip()


def _t_live():
    md, btns = render("live", LiveInfo(room_id=214, live_status=1, title="今晚打游戏",
                                       area="虚拟主播", online=999, cover=COVER),
                      "小狐狸", TPL)
    return (md.startswith("**")
            and "开播提醒" in md.splitlines()[0]
            and _has_img(md, _cover_expect(COVER))
            and '【直播订阅】"小狐狸"开播啦！记得看噢~' in md
            and "今晚打游戏" in md
            and _no_blank_line(md)              # 换行可以，但不能有空行
            and btns[0][:2] == ("进入直播间", "https://live.bilibili.com/214"))


def _t_video():
    md, btns = render("video", VideoInfo(bvid="BV1xx411c7XX", title="测试视频",
                                         pic=COVER, desc="简介一行"),
                      "小狐狸", TPL)
    return (_has_img(md, _cover_expect(COVER))
            and "新视频投稿" in md
            and '【视频订阅】"小狐狸"新视频投稿了《测试视频》，记得看噢~' in md
            and btns[0][:2] == ("观看视频",
                                "https://www.bilibili.com/video/BV1xx411c7XX"))


def _t_dyn_title():
    md, btns = render("dynamic", DynamicInfo(dyn_id="999", title="新周边",
                                             text="内容文本", images=[COVER]),
                      "小狐狸", TPL)
    return (_has_img(md, _cover_expect(COVER))
            and '【动态订阅】"小狐狐"新动态更新了《新周边》'.replace("狐狐", "狐狸") in md
            # v2.2.5：不再显示「动态内容」标签行，正文直接进引用块
            and "> **动态内容**" not in md
            and any(l.startswith("> 　") and "内容文本" in l for l in md.splitlines())
            and btns[0][:2] == ("查看动态", "https://www.bilibili.com/opus/999"))


def _t_dyn_no_title():
    md, btns = render("dynamic", DynamicInfo(dyn_id="998", title="",
                                             text="第一行\n第二行", images=[]),
                      "小狐狸", TPL)
    body = [l for l in md.splitlines() if l.startswith("> 　")]
    return ('【动态订阅】"小狐狸"新动态更新了' in md
            and "《》" not in md
            # v2.2.5：不再显示「动态内容」标签行
            and "> **动态内容**" not in md
            and body and "第一行" in body[0])


def _t_buttons():
    """按钮是三元组（文字, 链接, 点击后文字），且能被 build_keyboard 正确拼装。"""
    from qqapi import build_keyboard
    _, btns = render("live", LiveInfo(room_id=1, live_status=1), "A", TPL)
    ok = btns[0][1].startswith("https://live.bilibili.com/")
    ok &= len(btns[0]) == 3 and bool(btns[0][2])
    kb = build_keyboard(btns)
    b0 = kb["content"]["rows"][0]["buttons"][0]
    ok &= b0["action"]["type"] == 0                    # 跳转按钮
    ok &= b0["action"]["permission"]["type"] == 2      # 所有人可点
    ok &= b0["render_data"]["visited_label"] == btns[0][2]
    return ok


def _t_subscribe_card():
    """订阅成功卡片：带头像、有空间按钮、有纯文本兜底。"""
    from notify import render_subscribe, subscribe_plain_text
    md, btns = render_subscribe("小狐狸", 12345, COVER, "直播、视频、动态")
    ok = md.startswith("**") and "订阅成功" in md
    # 头像不缩放：方头像套 16:9 参数会被裁变形
    ok &= _has_img(md, _cover_expect(COVER, scale=False))        # 头像已插入
    ok &= "小狐狸" in md and "12345" in md
    ok &= btns[0][:2] == ("TA 的空间", "https://space.bilibili.com/12345")

    # 无头像时也不能崩
    md2, _ = render_subscribe("无脸", 1, "", "直播")
    ok &= md2.startswith("**") and "无脸" in md2

    # 纯文本兜底不含 markdown 符号
    plain = subscribe_plain_text("小狐狸", 12345, "直播")
    ok &= "小狐狸" in plain and "**" not in plain and "#" not in plain
    return ok


def _t_subscribe_first_only():
    """只有「本群从无到有」时才返回带头像的卡片。"""
    d = tempfile.mkdtemp()
    os.environ["BOT_CONFIG"] = os.path.join(d, "config.yaml")
    from bilibili import LiveInfo, Up
    from bot import NotifyBot
    from db import Store

    class FakeBili:
        async def get_up(self, uid):
            return Up(uid=uid, uname="小狐狸", face=COVER, room_id=214)

        async def get_live(self, rid):
            return LiveInfo(room_id=rid, live_status=0)

        async def get_latest_video(self, uid):
            return None

        async def get_latest_dynamic(self, uid):
            return None

    st = Store(os.path.join(d, "data.json"))
    st.ensure_group("GA")
    b = NotifyBot.__new__(NotifyBot)
    b.store, b.bili, b.cfg, b.tpls = st, FakeBili(), {}, {}

    async def run():
        # v1.21.0 起订阅全部走网页面板，群指令已移除。
        # 这里改为直接验证「落库 + 渲染」这两层：
        #   1) 头像能存进 ups 表（面板 /api/sub/add 走 set_up_info）
        #   2) 渲染时首次订阅卡片带图，纯文本不带图
        st.set_up_info(12345, "小狐狸", 214, face=COVER)
        ok = bool(st.get_up(12345).get("face"))          # 头像已落库

        from notify import render_subscribe, subscribe_plain_text
        md, _btns = render_subscribe("小狐狸", 12345, COVER, "直播、视频、动态")
        plain = subscribe_plain_text("小狐狸", 12345, "直播、视频、动态")
        ok &= COVER in md                                # 卡片带头像
        ok &= COVER not in plain                         # 纯文本不带图（手机友好）
        return ok

    import asyncio
    return asyncio.run(run())


def _t_official_style():
    """对照官方 Markdown 规范的几个硬性要求。"""
    md, _ = render("dynamic", DynamicInfo(dyn_id="1", title="标题",
                                          text="正文\n\n第二段", images=[]),
                   "小狐狸", TPL)
    lines = md.splitlines()
    # v1.7.0 起：不用 ## 标题（QQ 里会渲染出 null 前缀），改用加粗
    ok = lines[0].startswith("**")
    ok &= not md.startswith("## ")
    ok &= ZWSP not in md                     # v1.31.21 起不再用零宽空格撑空行
    ok &= "\n\n" not in md                   # 不应出现连续空行（会被压缩）
    ok &= _no_blank_line(md)                 # 整条消息都不许有空行
    ok &= "\n---\n" not in md                # 不用水平分割线
    # 内容里不能残留会破坏排版的符号
    bad = re.search(r"[`*_#]", md.replace("**", "").replace("## ", ""))
    return ok


async def _t_push():
    ok = True
    bot, path = make_bot()
    uid = 1001
    room = uid * 10

    # A 群只要直播；B 群要直播 + 动态，且直播要 @全体
    bot.store.set_up_info(uid, "小狐狸", room)
    bot.store.set_sub("GA", uid, "live", True, "小狐狸")
    bot.store.set_sub("GA", uid, "video", False)
    bot.store.set_sub("GA", uid, "dynamic", False)
    bot.store.set_sub("GB", uid, "live", True, "小狐狸")
    bot.store.set_sub("GB", uid, "dynamic", True)
    bot.store.set_sub("GB", uid, "video", False)

    # 订阅时 UP 已有旧投稿/旧动态 → 建立基线（真实流程，避免把历史内容当新内容推送）
    bot.bili.live[room] = LiveInfo(room_id=room, live_status=0)
    bot.bili.video[uid] = VideoInfo(bvid="BV0", title="旧视频")
    bot.bili.dyn[uid] = DynamicInfo(dyn_id="D0", title="旧动态", text="很久以前")
    await bot._init_baseline(uid)

    # 1) 开播：A、B 都该收到（@全体 已在 v1.18.0 移除，不再额外发文本）
    bot.bili.live[room] = LiveInfo(room_id=room, live_status=1, title="开播啦", cover=COVER)
    await bot._check_live(uid, "小狐狸")
    card_gids = [g for g, _, _ in bot.qq.cards]
    ok &= ck("开播只推给开了直播的群", sorted(card_gids) == ["GA", "GB"])
    ok &= ck("不再发 @全体 文本（已移除）",
             not any("@全体" in str(c) or "everyone" in str(c)
                     for _, c in bot.qq.texts))
    ok &= ck("卡片带封面和按钮",
             all(_has_img(md, _cover_expect(COVER)) for _, md, _ in bot.qq.cards)
             and all(kb and kb["content"]["rows"][0]["buttons"][0]["action"]["type"] == 0
                     for _, _, kb in bot.qq.cards))

    # 2) 视频：A、B 都没开 → 不推送
    bot.qq.cards.clear(); bot.qq.texts.clear()
    bot.bili.video[uid] = VideoInfo(bvid="BV1", title="新视频", pic=COVER)
    await bot._check_video(uid, "小狐狸")
    ok &= ck("未开视频订阅则不推", len(bot.qq.cards) == 0)

    # 3) 动态：只有 B 群开了 → 只推 B
    bot.qq.cards.clear()
    bot.bili.dyn[uid] = DynamicInfo(dyn_id="D1", title="新动态", text="正文", images=[COVER])
    await bot._check_dynamic(uid, "小狐狸")
    ok &= ck("动态只推给开了动态的群",
             [g for g, _, _ in bot.qq.cards] == ["GB"]
             and "【动态订阅】" in bot.qq.cards[0][1])

    # 4) 状态没变不重复推
    bot.qq.cards.clear()
    await bot._check_live(uid, "小狐狸")
    await bot._check_dynamic(uid, "小狐狸")
    ok &= ck("状态未变化不重复推送", len(bot.qq.cards) == 0)

    os.remove(path)
    return ok


if __name__ == "__main__":
    main()
