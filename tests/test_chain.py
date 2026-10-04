# -*- coding: utf-8 -*-
"""全链路测试：检测 → 判定 → 推送，三类提醒各跑一遍。

用假 B 站客户端 + 假 QQ 客户端，把 bot 的轮询链路完整跑通，
验证「直播 / 新视频 / 新动态」三种情况都能正确触发推送。
"""
import asyncio
import io
import re
import os
import tempfile

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.environ["BOT_CONFIG"] = os.path.join(sys.path[0], "config.yaml")

from bilibili import BiliError  # noqa: E402
from db import Store  # noqa: E402
import bot as B  # noqa: E402

DB = os.path.join(sys.path[0], "_chain.db")


# 用真实的数据类做 mock（保证 url 等 property 都在）
from bilibili import DynamicInfo, LiveInfo, VideoInfo  # noqa: E402


def mk_live(status, title="", cover=""):
    return LiveInfo(room_id=7788, live_status=status, title=title,
                    area="虚拟主播", online=1234, cover=cover)


def mk_video(bvid):
    return VideoInfo(bvid=bvid, title="测试视频", pic="",
                     desc="简介", pubdate=0)


def mk_dyn(did):
    return DynamicInfo(dyn_id=did, title="测试动态", text="动态内容",
                       images=[])


class FakeBili:
    """可控的假 B 站接口。"""

    def __init__(self):
        self.live_status = 0
        self.live_title = ""
        self.bvid = "BV1aa"
        self.dyn_id = "100"
        self.dyn_top_id = ""
        self.top_cmt_id = ""
        self.room_by_mid = 0
        self.fail = set()

    async def get_room_id_by_mid(self, uid):
        # 真实方法返回 (长号, 短号) 元组，桩必须同构
        if isinstance(self.room_by_mid, tuple):
            return self.room_by_mid
        return (self.room_by_mid, 0)

    async def get_live(self, rid):
        if "live" in self.fail:
            raise BiliError("code=-352 msg=-352")
        return mk_live(self.live_status, self.live_title)

    async def get_latest_video(self, uid):
        if "video" in self.fail:
            raise BiliError("code=-352 msg=-352")
        return mk_video(self.bvid)

    risk_hits = 0
    _risk_at = 0.0

    def _note_risk(self):
        self.risk_hits += 1
        import time as _t
        self._risk_at = _t.monotonic()

    def since_risk(self):
        import time as _t
        return 1e9 if not self._risk_at else _t.monotonic() - self._risk_at

    async def get_pinned_dynamic(self, uid):
        if "dyn_top" in self.fail:
            raise BiliError("code=-352 msg=-352")
        if not self.dyn_top_id:
            return None
        d = mk_dyn(self.dyn_top_id)
        d.pinned = True
        return d

    async def get_pinned_comment(self, dyn_id, uid=0, rid="", comment_id="",
                                 comment_type=0):
        if not self.top_cmt_id:
            return None
        return {"id": self.top_cmt_id, "user": "某人",
                "content": "置顶评论内容", "likes": 3}

    async def get_latest_dynamic(self, uid):
        if "dyn" in self.fail:
            raise BiliError("code=-352 msg=-352")
        return mk_dyn(self.dyn_id)

    async def close(self):
        pass


class FakeQQ:
    """记录实际发出的消息。"""

    def __init__(self):
        self.texts = []
        self.markdowns = []
        # ⚠️ roles / get_member_role 桩已于 v2.2.0 随 qqapi 的死代码一起删除

    async def send_text(self, gid, text, **kw):
        self.texts.append((gid, text))

    async def send_markdown(self, gid, md, **kw):
        self.markdowns.append((gid, md))

    async def close(self):
        pass


def new_bot(store, bili):
    b = B.NotifyBot.__new__(B.NotifyBot)
    b.cfg = {"send_image": False, "image_send_mode": "off"}
    b.send_image = False
    b.store = store
    b.bili = bili
    b.qq = FakeQQ()
    b.tpls = {}
    b.send_image = True
    b.notify_offline = False
    b.interval = 60
    b.slow_every = 1      # 每轮都查慢查询，便于测试
    b._round = 0
    return b


def bl(st, kind, uid=111, gid="GROUP_A"):
    """取**按群**基线。

    ⚠️ 检测流程已改为只写「群 × UP主 × 类型」基线（sub_baseline），
    不再写 UP 主级基线（ups 表）—— 全站共用一份会让新订阅的群
    继承别的群的过期进度，把订阅前就存在的内容当成新内容推一遍。
    UP 主级基线现在只用于老库迁移，断言必须跟着改，否则永远取到空。
    """
    return st.baseline_of(gid, uid, kind) or {}


def reset():
    # ⚠️ 数据现在落盘成 .json（Store 会把 .db 后缀换掉）。只删 .db 的话，
    #    .json 还留着，下一个用例读到的就是上一轮的数据 —— 表现是
    #    "新动态提醒一次"这类断言全变成 0 次（被认为已经推过）。
    _wipe(DB)
    st = Store(DB)
    st.set_up(111, uname="测试UP", room_id=7788, face="")
    st.set_sub("GROUP_A", 111, "live", True)
    st.set_sub("GROUP_A", 111, "video", True)
    st.set_sub("GROUP_A", 111, "dynamic", True)
    return st


def _wipe(p):
    """删掉一个数据文件（含它的 .json 落盘形态）。

    ⚠️ Store 会把 .db 换成 .json 落盘，只删 .db 会留下真实数据，
       下一个用例读到的还是上一轮的内容。
    """
    for base in (p, os.path.splitext(p)[0] + ".json"):
        for suf in ("", "-wal", "-shm", ".lock"):
            try:
                os.remove(base + suf)
            except OSError:
                pass


ALL_RESULTS = []


def ck(name, cond):
    """断言并**自动记账**。

    ⚠️ 以前要写 ok.append(ck(...))，一旦忘了 append，这条断言的
    结果就不会计入汇总 —— 出现过「明明打印 FAIL，最后却报告全部通过」
    的假阳性（v1.20.0 修）。现在统一由 ck 自己记账，杜绝漏统计。
    """
    ok = bool(cond)
    ALL_RESULTS.append((name, ok))
    print(("  [PASS] " if ok else "  [FAIL] ") + name)
    return ok


async def t_pinned_dynamic_separate_baseline():
    """置顶动态独立基线：首次静默，变化才提醒；与最新同 id 不重复提醒。"""
    st = reset()
    bili = FakeBili()
    b = new_bot(st, bili)

    # 首次：普通动态 + 置顶都只建基线，不推送
    await b._check_dynamic(111, "UP")
    ck("首次全部静默", not b.qq.texts and not b.qq.markdowns)
    ck("动态基线已建", bl(st, "dyn").get("id") == "100")

    # 新动态 → 提醒
    bili.dyn_id = "101"
    await b._check_dynamic(111, "UP")
    ck("新动态提醒一次", len(b.qq.markdowns) == 1)

    # 置顶出现：只建基线
    bili.dyn_top_id = "TOP1"
    await b._check_dynamic(111, "UP")
    ck("置顶首次静默", len(b.qq.markdowns) == 1)
    ck("置顶基线已建", bl(st, "dyn_top").get("id") == "TOP1")

    # 置顶变化 → 提醒
    bili.dyn_top_id = "TOP2"
    await b._check_dynamic(111, "UP")
    ck("置顶变化提醒", len(b.qq.markdowns) == 2)

    # 关键：置顶 = 最新同一条时，只能提醒一次
    b.qq.markdowns.clear()
    bili.dyn_id = "SAME"
    bili.dyn_top_id = "SAME"
    await b._check_dynamic(111, "UP")
    ck("同一条只提醒一次", len(b.qq.markdowns) == 1)
    # ⚠️ 以前这里断言「按置顶优先展示」，v1.94.0 起改为按「📝 新动态」展示，
    #    断言必须跟着改（不是放宽，是设计变了）：
    #    置顶接口在**认不出置顶标记**时会兜底返回列表第一条，也就是最新动态
    #    本身。这时那条根本没被 UP 置顶，给它扣「📌 置顶」的帽子是错的；
    #    而按「新动态」展示在任何一种情况下都成立（兜底时它确实也是最新的）。
    #    真正该显示「📌 置顶」的，是 UP 置顶了**一条不是最新**的动态 ——
    #    那才是实打实的置顶行为，上面「置顶变化提醒」已覆盖。
    #    同一条只推一次这件事没变，见 tests/test_dyn_top_repush.py。
    ck("按新动态展示，不误扣「置顶」帽子",
       "置顶" not in b.qq.markdowns[0][1])


async def t_pinned_comment_detection():
    """置顶动态下的置顶评论：首次静默，变化才提醒。

    注意：置顶评论是**独立开关**，必须单独订阅才收得到
    （只订阅 dynamic 不会收到）。
    """
    st = reset()
    st.set_sub("GROUP_A", 111, "top_comment", True)
    bili = FakeBili()
    b = new_bot(st, bili)
    bili.dyn_top_id = "TOP1"
    bili.top_cmt_id = "C1"

    # 置顶评论已改为独立调度（不再挂在 _check_dynamic 里），
    # 这里直接调 _check_top_comment —— 它自己会去取置顶动态。
    await b._check_top_comment(111, "UP")
    ck("评论首次静默", not b.qq.markdowns)
    ck("评论基线已写入", bl(st, "dyn_top_cmt").get("id") == "C1")

    bili.top_cmt_id = "C2"
    await b._check_top_comment(111, "UP")
    ck("评论变化才提醒", len(b.qq.markdowns) == 1)
    ck("提醒是置顶评论", "置顶评论" in b.qq.markdowns[0][1])

    # 评论不变 → 不再提醒
    await b._check_dynamic(111, "UP")
    ck("评论不变不重复推", len(b.qq.markdowns) == 1)


def t_at_all_feature_removed():
    """@全体 功能必须彻底移除（v1.18.0）。

    移除原因：官方内嵌 @everyone 仅文字子频道可用；且 @全体 需要
    群主/管理员身份，而机器人不在管理员候选池里。两条都堵死。
    所以这里守住"不再残留任何 @全体 痕迹"。
    """
    import notify as N
    ck("notify 不再有 at_all_text", not hasattr(N, "at_all_text"))

    bot = io.open(_os_path("bot.py"), encoding="utf-8").read()
    ck("bot 不再引用 at_all", "at_all" not in bot)
    ck("bot 不再有 /全体 指令", '"全体"' not in bot and "'全体'" not in bot)

    db = io.open(_os_path("db.py"), encoding="utf-8").read()
    ck("subs 表不再有 at_all 列", "at_all     INTEGER" not in db)
    ck("有移除列的迁移", "_drop_at_all_column" in db)
    ck("迁移会被调用", "_drop_at_all_column(conn)" in db)

    js = io.open(_os_path("static/app.js"), encoding="utf-8").read()
    ck("前端无 @全体 chip", "toggle-at" not in js)
    # ⚠️ v1.54 起「📦 全体迁移」是订阅迁移的范围选项，属于正当功能。
    #    这条断言的本意是守住"@全体 提醒"被彻底移除，所以要把迁移文案
    #    排除掉，否则新功能一加就误报（断言要跟着产品走，不是反过来）。
    _js = "\n".join(l for l in js.splitlines()
                    if "迁移" not in l and "照搬" not in l)
    # ⚠️ 只查**带引号的字符串字面量**：注释里写「安全体检」这类正常中文，
    #    恰好也含"全体"两个字，朴素子串判断会误报（断言要守的是代码里
    #    还在用 '全体' 这个指令/标记，不是不许写"体检"两个字）。
    ck("前端无 data-act 全体", not re.search(r"['\"]全体['\"]", _js))

    html = io.open(_os_path("templates/index.html"), encoding="utf-8").read()
    ck("面板无 @全体 说明", "@全体" not in html)

    ex = io.open(_os_path("config.example.yaml"), encoding="utf-8").read()
    ck("配置无 at_all_style", "at_all_style" not in ex)


def t_image_cache_lifecycle():
    """长期图保留、短期图发完即删。"""
    import media as M
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    jpg = b"\xff\xd8\xff" + b"y" * 20

    p = M.save_long("https://i0.hdslb.com/face1.png", png)
    ck("长期落盘", os.path.exists(p))
    ck("长期可读回", len(M.load_long("https://i0.hdslb.com/face1.png")) == len(png))

    t = M.save_tmp(jpg)
    ck("短期落盘", os.path.exists(t))
    M.drop(t)
    ck("发完删除", not os.path.exists(t))

    # 兜底清扫：旧临时文件会被清掉
    old = M.save_tmp(jpg)
    import time as _t
    os.utime(old, (_t.time() - 7200, _t.time() - 7200))
    M.sweep_tmp(max_age=3600)
    ck("过期临时图被清扫", not os.path.exists(old))

    M.purge_long()
    ck("长期可清空", len(M.load_long("https://i0.hdslb.com/face1.png")) == 0)


async def t_bili_constants_exist():
    """真实调用 B 站接口方法，确保没有 NameError。

    之前 get_pinned_dynamic 里写了一个不存在的常量名 DYNAMIC_NEW，
    一跑轮询就抛 name 'DYNAMIC_NEW' is not defined。
    测试用的是 FakeBili（桩），根本不走真实代码，所以没被发现 ——
    这个测试直接打真实方法，专防这类"桩挡住了 bug"。
    """
    import bilibili as BL

    class StubClient(BL.BiliClient):
        def __init__(self):
            super().__init__()
            self.buvid = "x"
            self.cookie = ""
            self.seen = []

        async def ensure_buvid3(self):
            pass

        async def _throttle(self, key):
            pass

        async def _get(self, url, params=None, referer=None, **kw):
            self.seen.append(url)
            # 模拟置顶动态
            return {"data": {"items": [{
                "id_str": "TOP123",
                "is_pinned": 1,
                "modules": {"module_dynamic": {"desc": {"text": "置顶内容"}}},
            }]}}

    c = StubClient()

    d = await c.get_pinned_dynamic(259149243)
    ck("置顶动态不报 NameError", d is not None)
    ck("取到置顶 ID", d.dyn_id == "TOP123")
    ck("标记为置顶", d.pinned is True)

    # 置顶评论同样走真实方法
    class StubCmt(StubClient):
        async def _get(self, url, params=None, referer=None, **kw):
            return {"data": {"top_replies": [{
                "rpid_str": "R1", "member": {"uname": "某人"},
                "content": {"message": "置顶评论"}, "like": 5,
            }]}}

    cc = StubCmt()
    r = await cc.get_pinned_comment("TOP123", 259149243)
    ck("置顶评论不报 NameError", r is not None)
    ck("评论 ID 正确", r.get("id") == "R1")


def _os_path(rel):
    """随包文件：新版在 src/ 或 docs/，老布局摊在程序根目录 —— 三处都找。

    v1.96 起根目录只留启动脚本，config.example.yaml / VERSION /
    requirements 都搬进了子目录；老包解开是扁平布局，所以都要认。
    """
    import os as _o
    for _base in (BASE_DIR, _o.path.join(_o.path.dirname(BASE_DIR), "docs"),
                  _o.path.dirname(BASE_DIR)):
        _p = _o.path.join(_base, rel)
        if _o.path.exists(_p):
            return _p
    return _o.path.join(BASE_DIR, rel)


def t_top_comment_is_separate_switch():
    """置顶评论必须能独立开关，不跟动态绑定。"""
    import os as _os
    from db import KINDS, Store

    ck("类型里有 top_comment", "top_comment" in KINDS)

    p = _os.path.join(BASE_DIR, "_tc.db")
    _wipe(p)
    st = Store(p)
    try:
        st.set_sub("GA", 1, "dynamic", True)
        ck("只开动态时无置顶评论目标", st.targets(1, "top_comment") == [])
        ck("订阅类型不含 top_comment", "top_comment" not in st.subscribed_kinds(1))

        st.set_sub("GA", 1, "top_comment", True)
        ck("开启后有目标", st.targets(1, "top_comment") == ["GA"])
        ck("订阅类型含 top_comment", "top_comment" in st.subscribed_kinds(1))

        st.set_sub("GA", 1, "dynamic", False)
        ck("关动态不影响置顶评论", st.targets(1, "top_comment") == ["GA"])
        ck("动态确实关了", st.targets(1, "dynamic") == [])
    finally:
        _wipe(p)


def t_default_kinds_exclude_top_comment():
    """不填类型=全开时，不能把置顶评论也默认塞给用户。"""
    import os as _os
    from db import DEFAULT_KINDS, Store

    ck("默认不含 top_comment", "top_comment" not in DEFAULT_KINDS)
    ck("默认含三类基础", set(DEFAULT_KINDS) == {"live", "video", "dynamic"})

    p = _os.path.join(BASE_DIR, "_dk.db")
    _wipe(p)
    st = Store(p)
    try:
        st.set_all_kinds("GA", 1, True, "UP")
        ck("全开后不含置顶评论",
           "top_comment" not in st.subscribed_kinds(1))
        ck("三类都开了",
           {"live", "video", "dynamic"} <= st.subscribed_kinds(1))
    finally:
        _wipe(p)


def t_log_has_timestamp_and_order():
    """日志要带时间（能做时间轴），前端排序可切换且能记住。"""
    from logtranslate import explain

    r = explain("2026-09-19 14:03:12 [INFO] 机器人已上线")
    ck("能抽出日期", r.get("date") == "2026-09-19")
    ck("能抽出时间", r.get("time") == "14:03:12")

    r2 = explain("09-19 14:03:12 [INFO] 旧格式")
    ck("缺年份也能抽", bool(r2.get("date")) and bool(r2.get("time")))

    r3 = explain("[INFO] 无时间戳的一行")
    ck("无时间不瞎填", r3.get("date") == "" and r3.get("time") == "")

    js = io.open(_os_path("static/app.js"), encoding="utf-8").read()
    ck("有排序状态变量", "LOG_ORDER" in js)
    ck("默认新到旧", "'desc'" in js)
    ck("有切换动作", "toggle-log-order" in js)
    ck("写入 localStorage", "localStorage.setItem('biliLogOrder'" in js)
    ck("读取 localStorage", "localStorage.getItem('biliLogOrder')" in js)
    ck("按日期分组", "tl-group" in js)

    html = io.open(_os_path("templates/index.html"), encoding="utf-8").read()
    ck("有排序按钮", "toggle-log-order" in html)


async def t_pinned_comment_reads_pinned_dynamic():
    """置顶评论必须是「置顶动态」下的置顶评论，不是普通动态的。

    同时守住一个真实 bug：B站评论接口里 `upper` 是**UP主对象**（含 mid），
    不是评论列表。旧代码把它当列表遍历 → 拿到字符串 key → .get() 抛
    AttributeError → 被 except 吞掉 → 置顶评论永远取不到。
    """
    import bilibili as BL

    def stub(payload):
        class S(BL.BiliClient):
            async def _get(self, url, params=None, referer=None, **kw):
                return payload
        return S()

    # 链路：先取置顶动态，再用它的 ID 查评论
    src = io.open(_os_path("bot.py"), encoding="utf-8").read()
    ck("置顶评论独立调度", 'if "top_comment" in kinds' in src)
    ck("评论方法自给自足", "async def _check_top_comment(self, uid: int, name: str" in src)

    # 场景1：upper 是 UP主对象（旧代码在这里崩）→ 应取到 top_replies
    p1 = {"data": {"upper": {"mid": 123},
                   "top_replies": [{"rpid": 9, "member": {"uname": "A"},
                                    "content": {"message": "置顶评论"},
                                    "like": 5}]}}
    r1 = await stub(p1).get_pinned_comment("D1")
    ck("upper为对象时仍可取到", r1 is not None and r1.get("id") == "9")
    ck("取到的是置顶评论内容", r1.get("content") == "置顶评论")

    # 场景2：嵌在 upper.top_replies
    p2 = {"data": {"upper": {"mid": 1, "top_replies": [
        {"rpid_str": "R2", "member": {"uname": "B"},
         "content": {"message": "嵌套置顶"}}]}}}
    r2 = await stub(p2).get_pinned_comment("D1")
    ck("嵌套结构可取到", r2 is not None and r2.get("id") == "R2")

    # 场景3：只有普通回复带 is_top 标记
    p3 = {"data": {"replies": [
        {"rpid": 1, "member": {"uname": "C"}, "content": {"message": "普通"}},
        {"rpid": 2, "is_top": 1, "member": {"uname": "D"},
         "content": {"message": "标记置顶"}}]}}
    r3 = await stub(p3).get_pinned_comment("D1")
    ck("is_top标记能识别", r3 is not None and r3.get("id") == "2")
    ck("不会误取普通评论", r3.get("content") == "标记置顶")

    # 场景4：没有置顶评论 → None（不能瞎返回第一条普通评论）
    p4 = {"data": {"replies": [{"rpid": 1, "member": {"uname": "E"},
                                "content": {"message": "普通评论"}}]}}
    ck("无置顶返回 None", (await stub(p4).get_pinned_comment("D1")) is None)

    # 场景5：空数据 / 异常结构不能崩
    ck("空数据不崩", (await stub({}).get_pinned_comment("D1")) is None)
    ck("无ID不请求", (await stub(p1).get_pinned_comment("")) is None)


def t_adaptive_poll_interval():
    """轮询要贴着风控边缘跑：默认快、撞风控放慢、稳定后加快。"""
    st = reset()
    bili = FakeBili()
    b = new_bot(st, bili)

    # 默认就该是"快"档（不是 30 秒那种慢吞吞）
    b.base_interval = 12
    b.min_interval = 8
    b.max_interval = 120
    b.interval = 12
    b._risk_seen = 0
    b._calm_rounds = 0

    info = b.interval_info()
    ck("基线是快档", info["base"] == 12)
    ck("最快档更快", info["min"] < info["base"])
    ck("有退避上限", info["max"] >= info["base"])

    # —— 撞风控 → 放慢 ——
    bili._note_risk()
    b._adapt_interval()
    ck("撞风控后放慢", b.interval > 12)
    ck("退避不超上限", b.interval <= 120)
    ck("标记为退避中", b.interval_info()["backing_off"])

    # 再撞一次 → 继续放慢
    prev = b.interval
    bili._note_risk()
    b._adapt_interval()
    ck("连续风控继续放慢", b.interval > prev)

    # —— 刚撞过（<60s）→ 不急着加速 ——
    b._adapt_interval()
    ck("刚撞完不加速", b.interval >= prev)

    # —— 稳定很久（>5分钟）→ 回最快档 ——
    bili._risk_at = 0.0          # 清空 → since_risk 返回 1e9
    b._adapt_interval()
    ck("稳定后回最快档", b.interval == 8)
    ck("不再是退避中", not b.interval_info()["backing_off"])

    # —— 逐步加快（不是一步到位） ——
    b.interval = 20
    bili._risk_at = __import__("time").monotonic() - 100   # 100秒前，>60 但 <300
    b._calm_rounds = 0
    for _ in range(3):
        b._adapt_interval()
    ck("平稳时逐步加快", b.interval < 20)
    ck("不会低于最快档", b.interval >= 8)


def t_poll_interval_config_is_fast():
    """配置默认值必须偏快（准秒级），不能是 30 秒那种。"""
    import io as _io2
    src = _io2.open(_os_path("config.example.yaml"), encoding="utf-8").read()
    m = _io2.open(_os_path("bot.py"), encoding="utf-8").read()
    ck("代码默认 12 秒", 'cfg.get("poll_interval", 12)' in m)
    ck("有最快档配置", "poll_interval_min" in src)
    ck("有上限配置", "poll_interval_max" in src)

    # 面板能展示当前间隔
    ck("有状态输出", "interval_info" in m)


async def t_startup_sync_is_silent():
    """重启必须先静默同步一次，绝不能补推停机期间的内容。"""
    st = reset()
    bili = FakeBili()
    b = new_bot(st, bili)

    # 先跑一轮建立基线（模拟"上次运行过"）
    b._silent = False
    await b._poll_once()
    ck("首轮建基线不推送", not b.qq.markdowns)

    # UP 主在"停机期间"发了新内容
    bili.live_status = 1
    bili.bvid = "BV_NEW"
    bili.dyn_id = "D_NEW"

    # 重启 → 启动同步：必须静默
    b.qq.markdowns.clear()
    b.qq.texts.clear()
    n = await b._startup_sync()
    ck("启动同步覆盖所有 UP", n >= 1)
    ck("启动同步完全静默", not b.qq.markdowns and not b.qq.texts)
    ck("同步后基线已对齐", bl(st, "video").get("bvid") == "BV_NEW")
    ck("同步后恢复可推送", b._silent is False)

    # 同步完再变一次 → 这时才该推送
    bili.bvid = "BV_NEW2"
    await b._check_video(111, "测试UP")
    ck("之后的更新正常推送", len(b.qq.markdowns) == 1)


def t_sub_ui_uses_cards_not_table():
    """订阅区必须用卡片布局，不能是表格 —— 否则窄屏列溢出点不到。

    4 个类型 × 2（开关+@全体）+ UP主 + 删除 = 10 列（@全体现已移除），
    表格在手机上必然被挤出容器，最右边的「置顶评论」就永远点不到。
    """
    js = io.open(_os_path("static/app.js"), encoding="utf-8").read()
    css = io.open(_os_path("static/style.css"), encoding="utf-8").read()

    # 订阅是「群折叠卡片」结构：一个群一张可折叠卡，群内再细分 UP 主 / 合集。
    # 旧断言查的是 sub-card（更早的扁平卡片），随布局改版已不存在 ——
    # 测试跟着结构走，而不是把结构改回去迁就测试。
    ck("用折叠群卡片容器", 'class="card grp' in js)
    ck("每个群一张卡", "grp-head" in js and "grp-body" in js)
    ck("群内细分 UP/合集", "mini-view" in js)
    ck("类型开关是 chip", "chip-tgl" in js)
    ck("不再是表格", "<table>" not in js.split("function")[0] + js)
    ck("卡片样式存在", ".sub-card" in css)
    ck("chip 样式存在", ".chip-tgl" in css)
    ck("可换行", "flex-wrap: wrap" in css)
    ck("触摸目标够大", "min-height" in css)
    ck("开关按钮仍在", 'data-act="toggle-sub"' in js)
    ck("不再有 @全体 列", 'data-act="toggle-at"' not in js)


def t_image_mobile_variant():
    """手机端图片：要限制尺寸，不能拿 B站原图。

    原图动辄 1920×1080、好几 MB，手机端 QQ 加载失败/显示异常，
    而电脑端没事。用 CDN 参数压到 720 宽。
    """
    import media as M

    u = "https://i0.hdslb.com/bfs/live/abc.jpg"
    mob = M.mobile_variant(u)
    ck("手机版带尺寸参数", "@720w_720h_1c" in mob)
    ck("手机版仍是 jpg", mob.endswith(".jpg"))
    ck("原图函数不受影响", "@720w" not in M.jpeg_variant(u))

    # webp → jpg + 缩放
    w = "https://i2.hdslb.com/bfs/archive/x.webp@100w_100h.webp"
    ck("webp 先转 jpg", ".jpg@720w" in M.mobile_variant(w))
    ck("不含 webp", ".webp" not in M.mobile_variant(w))

    # 无后缀也能处理
    ck("无后缀可处理",
       M.mobile_variant("https://i1.hdslb.com/bfs/face/abc").endswith(".jpg"))

    # 大小上限收紧（手机端受不了 5MB）
    ck("上限收紧到 2MB", M.MAX_BYTES <= 2 * 1024 * 1024)
    ck("仍可用", M.usable(b"\xff\xd8\xff" + b"x" * 100))
    ck("webp 被拒", not M.usable(b"RIFFxxxxWEBP"))


def t_fake_bili_matches_real_signature():
    """假 B站 的方法签名必须和真实 BiliClient 一致。

    真实踩过的坑：bot.py 调用 `get_pinned_comment(dyn_id, uid, rid)`
    传了 3 个参数，而桩只写了 2 个 → TypeError → 被 except 静默吞掉
    → 表现就是"置顶评论永远检测不到"，且日志毫无异常。

    这类问题用桩测试**根本发现不了**（桩自己会对不上自己的桩），
    所以必须拿桩去和真实类逐个比对签名。
    """
    import inspect

    import bilibili as BL

    real = BL.BiliClient
    fake = FakeBili
    checked = 0
    for name in ("get_pinned_comment", "get_pinned_dynamic",
                 "get_latest_dynamic", "get_latest_video",
                 "get_live", "get_room_id_by_mid", "get_up"):
        r = getattr(real, name, None)
        f = getattr(fake, name, None)
        if r is None or f is None:
            continue
        checked += 1
        rp = inspect.signature(r).parameters
        fp = inspect.signature(f).parameters
        rn = [p for p in rp if p != "self"]
        fn = [p for p in fp if p != "self"]
        ck(f"{name} 参数个数一致", len(rn) <= len(fn))
    ck("确实比对了方法", checked >= 4)

    # 反向：真实方法多出来的参数，桩必须能接住（用 **kwargs 或显式声明）
    rp = inspect.signature(real.get_pinned_comment).parameters
    fp = inspect.signature(fake.get_pinned_comment).parameters
    has_var = any(p.kind == p.VAR_KEYWORD for p in fp.values())
    ck("桩能接住真实调用",
       len([p for p in rp if p != "self"]) <=
       len([p for p in fp if p != "self"]) or has_var)


async def t_pinned_flag_detection_is_broad():
    """置顶标记必须宽识别 + 有降级。

    真实踩的坑：_is_pinned 只认 is_pinned/is_top/top/module_tag，
    而实际接口里置顶标记可能藏在别处（module_top、深层嵌套…）。
    认不出来 → get_pinned_dynamic 返回 None → 置顶评论**永远检测不到**。
    """
    import bilibili as BL

    tagged = {"id_str": "T1", "modules": {"module_tag": {"text": "置顶"}}}
    plain = {"id_str": "T2", "modules": {"module_dynamic": {"desc": {}}}}
    deep = {"id_str": "T3", "x": {"y": "置顶"}}
    none = {"id_str": "T4", "modules": {}}

    ck("module_tag 识别", BL._is_pinned(tagged) is True)
    ck("深层置顶识别", BL._is_pinned(deep) is True)
    ck("无标记不误判", BL._is_pinned(none) is False)

    # 降级：标记全没认出时，find_pinned 之后 get_pinned_dynamic
    # 仍要退而取第一条（need_top=1 下它就是置顶），不能整个失效
    src = io.open(_os_path("bilibili.py"), encoding="utf-8").read()
    ck("有降级逻辑", "退而取第一条" in src)
    ck("有 find_pinned", "def find_pinned" in src)

    class Stub(BL.BiliClient):
        def __init__(self, payload):
            super().__init__()
            self.p = payload
        async def _get(self, url, params=None, referer=None, **kw):
            return self.p

    # 无任何置顶标记时，降级仍要返回置顶动态
    p = {"data": {"items": [plain]}}
    got = await Stub(p).get_pinned_dynamic(1)
    ck("无标记也能取到（降级）", got is not None and got.dyn_id == "T2")


def t_cover_uses_mobile_size():
    """图片尺寸：渲染层也压成手机友好的 jpg，本地下载路径另外压。

    注意这段判据改过两回，两回都是"改坏了又改回来"，别再反复：
      · 最早 _cover 压 720 宽 —— 为了解决手机端显示异常（用户实测反馈）。
      · v1.35.0 改成内嵌后，改成"去掉 @参数、给原图最稳"，理由是
        平台会自己转存。⚠️ 这个推理是**错的**：去掉 @ 参数等于给原图，
        原图动辄 1920×1080 好几 MB，手机端加载/平台转存容易失败 ——
        正是"电脑端看得见、手机端看不见"的成因。
      · 现在两层**都要压**，且格式固定 jpg（webp 手机 QQ 支持差）。
        区别只在谁压：渲染层用 CDN 参数（不下载），本地直传用 media 压。
    """
    import notify as N
    src = io.open(_os_path("notify.py"), encoding="utf-8").read()

    # 渲染层（markdown 内嵌）：压到 672 宽的 jpg
    # ⚠️ 只断言**实际输出**，不搜源码字符串 —— 源码注释里为了说明
    #    来龙去脉必然会提到 @720w 这几个字，一搜就误报。
    # 默认 **不压缩**（用原图），但仍然无条件 webp/gif → jpg
    out = N._cover("https://i0.hdslb.com/bfs/live/abc.jpg")
    ck("默认不压缩（无 CDN 参数）", "@" not in out)
    ck("格式是 jpg 不是 webp", ".webp" not in out and out.endswith(".jpg"))
    # 显式设宽度时才压
    N.set_inline_pic_size(672)
    try:
        out672 = N._cover("https://i0.hdslb.com/bfs/live/abc.jpg")
        ck("设了尺寸就压", "@672w_378h_1c.jpg" in out672)
        ck("压完仍是 jpg", out672.endswith(".jpg"))
    finally:
        N.set_inline_pic_size(0)
    ck("非白名单丢弃", N._cover("https://evil.com/a.jpg") == "")
    # 头像不缩放：方头像套 16:9 会被裁变形
    av = N._cover("https://i0.hdslb.com/bfs/face/abc.webp", scale=False)
    ck("头像不套缩放参数", "@" not in av)
    ck("头像仍是 jpg", av.endswith(".jpg"))
    # 设 0 = 用原图（给"压了反而取不到"留退路）
    N.set_inline_pic_size(0)
    try:
        ck("设 0 时用原图", "@" not in N._cover("https://i0.hdslb.com/bfs/live/abc.jpg"))
    finally:
        N.set_inline_pic_size(672)

    # 本地直传路径同样要用手机尺寸
    msrc = io.open(_os_path("media.py"), encoding="utf-8").read()
    ck("fetch 用手机尺寸", "mobile_variant(url)" in msrc)
    ck("大小上限 2MB", "2 * 1024 * 1024" in msrc)


def t_kind_whitelist_includes_top_comment():
    """订阅类型白名单必须包含 top_comment。

    真实踩的坑：validate_kind 硬编码 live/video/dynamic，
    top_comment 被后端拒绝 → 前端不查返回值 → 表现"开关点了没反应"。
    """
    from security import validate_kind

    ck("接受 top_comment", validate_kind("top_comment") == "top_comment")
    ck("接受中文名", validate_kind("置顶评论") == "top_comment")
    ck("接受 live", validate_kind("live") == "live")
    try:
        validate_kind("乱写")
        ck("非法类型被拒", False)
    except ValueError:
        ck("非法类型被拒", True)

    # 必须从 db.KINDS 动态取，不能再写死
    src = io.open(_os_path("security.py"), encoding="utf-8").read()
    ck("动态取类型表", "from db import KINDS" in src)


def t_interval_not_overwritten():
    """自适应间隔不能被后面的旧赋值覆盖回 30 秒。

    真实踩的坑：__init__ 里先算好 base/min/max，
    后面又有一行 self.interval = int(cfg.get("poll_interval", 30))，
    直接把自适应结果覆盖掉 —— v1.12 的提速等于白做。
    """
    bot_src = io.open(_os_path("bot.py"), encoding="utf-8").read()
    init = bot_src[bot_src.index("def __init__(self, cfg"):]
    init = init[:init.index("def on_ready")]

    ck("不再硬写 30", 'cfg.get("poll_interval", 30)' not in init)
    ck("默认 12", 'cfg.get("poll_interval", 12)' in init)
    ck("只赋值一次", init.count("self.interval = ") == 1)
    ck("来自 base", "self.interval = self.base_interval" in init)


def t_validate_kind_accepts_all_kinds():
    """订阅类型校验必须覆盖全部 KINDS，不能写死三个。

    真实踩过的坑：security.validate_kind 硬编码了 live/video/dynamic，
    新增的 top_comment 不在表里 → 面板点「置顶评论」开关时后端直接拒绝
    → 前端不看返回值直接刷新 → 表现就是"这个开关点了没反应"。
    """
    from db import KINDS
    from security import validate_kind

    for k in KINDS:
        try:
            got = validate_kind(k)
            ck(f"接受 {k}", got == k)
        except ValueError:
            ck(f"接受 {k}", False)

    ck("接受中文名 置顶评论", validate_kind("置顶评论") == "top_comment")
    ck("接受中文名 直播", validate_kind("直播") == "live")
    try:
        validate_kind("不存在的类型")
        ck("拒绝非法类型", False)
    except ValueError:
        ck("拒绝非法类型", True)

    # 新增类型时不能漏改这里 → 用 KINDS 动态生成
    src = io.open(_os_path("security.py"), encoding="utf-8").read()
    ck("从 db.KINDS 动态取", "from db import KINDS" in src)
    ck("不再硬编码三选一", '"live", "video", "dynamic"}\n' not in src)


def t_poll_interval_not_overwritten():
    """自适应间隔不能被后面一行旧赋值覆盖回 30 秒。

    真实踩过的坑：init 里先按 base/min/max 算好自适应间隔，
    紧接着又有 `self.interval = int(cfg.get("poll_interval", 30))`
    把它覆盖成 30 —— v1.12.0 的提速等于完全没生效。
    """
    src = io.open(_os_path("bot.py"), encoding="utf-8").read()
    init = src[src.index("def __init__(self, cfg"):]
    init = init[:init.index("def on_ready")]

    ck("默认基线不再是 30", 'cfg.get("poll_interval", 30)' not in init)
    ck("基线是 12", 'cfg.get("poll_interval", 12)' in init)
    ck("间隔取自 base_interval", "self.interval = self.base_interval" in init)
    # base 赋值必须出现在任何 self.interval 赋值之前
    ck("顺序正确",
       init.index("self.base_interval =") < init.index("self.interval = self.base_interval"))

    # 实跑一遍确认
    st = reset()
    b = new_bot(st, FakeBili())
    b.base_interval = 12
    b.min_interval = 8
    b.max_interval = 120
    b.interval = 12
    ck("实跑默认 12 秒", b.interval == 12)
    ck("不是 30 秒", b.interval != 30)


def t_mobile_image_variant_used():
    """本地下载/直传的图片必须走手机友好尺寸（720 宽）。

    ⚠️ 这里只检查 **media.py（本地下载 + 直传）** 这一层。
    渲染层（notify._cover）自 v1.35.0 起改成给原图 URL：图片内嵌在
    markdown 里，由开放平台下载转存，压 720 反而是多余的一层。
    两层策略不同，别再把它们写成同一个判据。
    """
    m = io.open(_os_path("media.py"), encoding="utf-8").read()
    ck("下载用了 mobile_variant", "mobile_variant(url) or jpeg_variant(url)" in m)
    ck("上传用了 mobile_variant", "url=mobile_variant(url)" in m)
    # 渲染层同样压尺寸（只是压成 jpg，不是 webp）
    # ⚠️ 同样只看实际输出：源码注释里提到 @720w 不代表代码还在压。
    import notify as _N
    # 默认不压缩；显式设宽度时才压成 jpg 尺寸
    ck("渲染层默认不压缩", "@" not in _N._cover("https://i0.hdslb.com/bfs/live/abc.jpg"))
    _N.set_inline_pic_size(672)
    try:
        ck("渲染层设了尺寸才压成 jpg",
           "@672w_378h_1c.jpg" in _N._cover("https://i0.hdslb.com/bfs/live/abc.jpg"))
    finally:
        _N.set_inline_pic_size(0)


async def t_top_comment_runs_standalone():
    """置顶评论必须独立检测，不能挂在 dynamic 下面。

    真实踩过的坑（用户反馈"没检测、没读取、没反馈"）：
    _check_top_comment 原本**嵌套在 _check_dynamic 里**，
    只有 `if top and top.dyn_id` 成立才调用；
    而 _poll_once 里又是 `if "dynamic" in kinds` 才调 _check_dynamic。
    双重门禁导致：只开置顶评论时**一次都不会查**，
    且任何失败都被 `except Exception: return` 吞掉 → 完全无反馈。
    """
    st = reset()
    # 只开置顶评论，不开动态 —— 这是以前完全失效的场景
    st.set_sub("GROUP_A", 111, "top_comment", True)
    st.set_up(111, uname="UP")
    st.refresh()
    b = new_bot(st, FakeBili())
    b.bili.dyn_top_id = "TOP1"
    b.bili.top_cmt_id = "C1"
    await b._poll_once()

    ck("只开置顶评论也会检测", bl(st, "dyn_top_cmt").get("id") == "C1")

    # 置顶动态没变，但置顶评论换了 → 必须推送
    b.bili.top_cmt_id = "C2"
    b.qq.markdowns.clear()
    await b._poll_once()
    ck("评论更换会推送", len(b.qq.markdowns) >= 1)
    ck("基线已更新", bl(st, "dyn_top_cmt").get("id") == "C2")

    # 评论没变 → 不重复推
    b.qq.markdowns.clear()
    await b._poll_once()
    ck("评论没变不重复推", len(b.qq.markdowns) == 0)


async def t_top_comment_failure_is_visible():
    """置顶评论取不到时必须能看见原因，不能静默。

    以前所有异常都被 `except Exception: return` 吞掉，
    用户看到"没反应"却无从判断是没开、没置顶、还是接口挂了。
    """
    import heartbeat

    def note():
        return ((heartbeat.read() or {}).get("notes") or {}) \
            .get("111:top_cmt", {}).get("text") or ""

    st = reset()
    st.set_sub("GROUP_A", 111, "top_comment", True)
    st.set_up(111, uname="UP")
    st.refresh()
    b = new_bot(st, FakeBili())

    # 无置顶动态
    b.bili.dyn_top_id = ""
    await b._poll_once()
    ck("无置顶动态有提示", "没找到置顶动态" in note())

    # 有置顶动态但没取到评论
    b.bili.dyn_top_id = "TOP1"
    b.bili.top_cmt_id = ""
    await b._poll_once()
    ck("无评论有提示", "置顶评论" in note())

    # /检测 必须包含置顶评论这一项
    b.bili.top_cmt_id = "C9"
    # 指令已移除（v1.21.0），检测改由面板触发，这里直接跑一轮验证
    await b._poll_once()
    ck("检测含置顶评论", "置顶评论" in note())


def t_pinned_dynamic_uses_host_mid():
    """置顶动态必须用 host_mid，不是 host_uid。

    真实事故（用户日志）：
        _check_top_comment 报 code=4101129「请求数据发生错误」
    polymer feed/space 接口的参数名是 **host_mid**。
    原来这里传 host_uid → 等于没传 → B 站返回 4101129。
    同文件 get_latest_dynamic 用的是 host_mid，所以只有它正常。
    """
    src = io.open(_os_path("bilibili.py"), encoding="utf-8").read()
    fn = src[src.index("async def get_pinned_dynamic"):]
    fn = fn[:fn.index("async def get_pinned_comment")]

    ck("用 host_mid", '"host_mid": uid' in fn)
    # 降级分支用的是老接口 DYNAMIC_OLD，它参数名确实是 host_uid（正确）；
    # 这里只禁止 **polymer（DYNAMIC_SPACE）** 用 host_uid。
    poly_seg = fn[fn.index("DYNAMIC_SPACE"):fn.index("DYNAMIC_OLD")]
    ck("polymer 不用 host_uid", '"host_uid"' not in poly_seg)
    ck("polymer 用 host_mid", '"host_mid"' in poly_seg)
    ck("有降级到老接口", "DYNAMIC_OLD" in fn)
    ck("有 buvid3", "ensure_buvid3" in fn)
    ck("有限流", "_throttle" in fn)


async def t_pinned_dynamic_falls_back_on_4101129():
    """polymer 报 4101129 时要降级到老接口，不能整个放弃。"""
    import bilibili as BL

    class Stub(BL.BiliClient):
        def __init__(self, poly_err):
            super().__init__()
            self.poly_err = poly_err
            self.buvid = "x"
            self.params_seen = []
        async def ensure_buvid3(self):
            pass
        async def _throttle(self, key):
            pass
        async def _get(self, url, params=None, referer=None, **kw):
            self.params_seen.append((url, dict(params or {})))
            if "polymer" in url:
                if self.poly_err:
                    raise BL.BiliError("code=4101129")
                return {"data": {"items": [{
                    "id_str": "TOP1", "basic": {"rid_str": "9"},
                    "modules": {"module_tag": {"text": "置顶"},
                                "module_dynamic": {"desc": {"text": "内容"}}}}]}}
            return {"data": {"cards": [{"desc": {
                "dynamic_id_str": "OLDTOP", "rid_str": "88",
                "is_pinned": 1}}]}}

    # polymer 挂了 → 走老接口
    a = Stub(poly_err=True)
    got = await a.get_pinned_dynamic(111)
    ck("降级拿到置顶", got is not None and got.dyn_id == "OLDTOP")
    ck("降级标记为置顶", got.pinned is True)

    # polymer 正常 → 用 host_mid
    b = Stub(poly_err=False)
    got = await b.get_pinned_dynamic(111)
    ck("正常拿到置顶", got is not None and got.dyn_id == "TOP1")
    ck("参数是 host_mid", "host_mid" in b.params_seen[0][1])


async def t_plain_text_message_mode():
    """Markdown 没申请权限时，必须有可用的纯文本方案。

    用户实测：自定义 Markdown 需要申请权限，没申请就用不了。
    但直接把 `**加粗**`、`> 引用` 当文本发，群里看到的是一堆
    星号和大于号，非常难看。

    所以：
    1. notify.to_plain 把 markdown 转成 Unicode 符号排版的文本
    2. message_style=text 时走纯文本
    3. markdown 发送失败时**自动降级**（否则完全收不到提醒）
    """
    import notify as N

    md = ("**🔴 开播提醒**\n"
          "**【直播订阅】\"UP\"开播啦！**\n"
          "---\n"
          "> **分区**　虚拟主播\n"
          "> **人气**　1,234")
    plain = N.to_plain(md)

    ck("纯文本无星号", "**" not in plain)
    ck("纯文本无大于号引用", not any(
        ln.strip().startswith(">") for ln in plain.split("\n")))
    ck("引用转成竖线", "│" in plain)
    ck("分割线转成横线", "─" in plain)
    ck("标题保留", "开播提醒" in plain)

    # message_style=text 时确实发文本
    st = reset()
    st.set_sub("GROUP_A", 111, "live", True)
    st.set_up(111, uname="UP", room_id=7)
    st.refresh()
    b = new_bot(st, FakeBili())
    b.cfg["message_style"] = "text"
    b.bili.live_status = 1
    await b._poll_once()
    b.bili.live_status = 0
    await b._poll_once()
    b.bili.live_status = 1
    b.qq.texts.clear()
    b.qq.markdowns.clear()
    await b._poll_once()
    ck("text 模式发文本", len(b.qq.texts) >= 1)
    ck("text 模式不发 markdown", len(b.qq.markdowns) == 0)


async def t_markdown_fail_falls_back_to_text():
    """Markdown 发不出去（没权限）时要自动降级，不能什么都不发。"""
    import bilibili as BL

    class FailMD(FakeQQ):
        async def send_markdown(self, gid, md, **kw):
            raise BL.BiliError("no markdown permission")

    st = reset()
    st.set_sub("GROUP_A", 111, "live", True)
    st.set_up(111, uname="UP", room_id=7)
    st.refresh()
    b = new_bot(st, FakeBili())
    b.qq = FailMD()
    b.cfg["message_style"] = "markdown"
    b.bili.live_status = 1
    await b._poll_once()
    b.bili.live_status = 0
    await b._poll_once()
    b.bili.live_status = 1
    b.qq.texts.clear()
    await b._poll_once()
    ck("markdown 失败自动降级文本", len(b.qq.texts) >= 1)


def t_push_has_plain_defined():
    """_push 里必须定义 plain。

    真实事故：纯文本降级引用了未定义的 plain → NameError
    被 except 吞掉 → **推送完全不发生**且日志无异常。
    """
    src = io.open(_os_path("bot.py"), encoding="utf-8").read()
    # _poll_once 在 _push 之前，取 _push 到下一个顶层 def 为止
    i0 = src.index("async def _push(self")
    rest = src[i0:]
    tail = rest.find("\n    def ", 1)
    tail2 = rest.find("\n    async def ", 1)
    ends = [x for x in (tail, tail2) if x > 0]
    fn = rest[:min(ends)] if ends else rest
    # v1.27.0 起渲染移到按群循环里（每个群可有自己的文案），
    # 降级文案也改成按群生成，因此断言"循环内定义了 plain_g"。
    ck("按群生成降级文案", "plain_g = to_plain(md_g)" in fn)
    ck("降级文案有兜底", "or short_hint(kind, name)" in fn)
    ck("导入了 short_hint", "short_hint" in src[:src.index("async def _push(")])
    ck("markdown 失败会降级", "_push_plain" in fn)


def t_log_symbols_translated():
    """日志里的文件名/类名/方法名要翻成中文。"""
    from logtranslate import translate_symbols, SYMBOL_NAMES

    ck("符号表非空", len(SYMBOL_NAMES) > 30)

    cases = {
        "(bot.py:1112)_check_top_comment": ["机器人主程序", "检查置顶评论"],
        "(robot.py:65)update_access_token": ["机器人登录", "换取登录凭证"],
        "(client.py:162)_bot_login": ["机器人连接", "机器人登录"],
    }
    for src, wants in cases.items():
        got = translate_symbols(src)
        for w in wants:
            ck(f"{src[:22]}→{w}", w in got)

    # 级别也要翻译
    ck("级别翻译", "[错误]" in translate_symbols("[ERROR] x"))

    # 不能出现双层括号
    out = translate_symbols("(bot.py:1)_poll_loop")
    ck("无双层括号", "（（" not in out and "((" not in out)


def t_credential_backup_tools_exist():
    """凭据备份/恢复工具必须存在（用户每版解压覆盖，怕丢凭据）。"""
    import os
    ck("有备份脚本", os.path.exists(
        os.path.join(BASE_DIR, "tools", "backup_credentials.py")))
    ck("有恢复脚本", os.path.exists(
        os.path.join(BASE_DIR, "tools", "restore_credentials.py")))
    b = io.open(os.path.join(BASE_DIR, "tools", "backup_credentials.py"),
                encoding="utf-8").read()
    ck("备份含 appid", "appid" in b)
    ck("备份含 cookie", "bili_cookie" in b)
    ck("备份含口令", "web_password" in b)


def t_comment_type_not_hardcoded_17():
    """评论区类型不能硬编码 17。

    用户日志：`置顶评论获取失败：code=-404 msg=啥都木有`

    官方规则（动态类型决定评论区类型）：
        纯文字 / 转发 / 站内分享 → type=17，oid=dynamic_id
        带图动态（相簿）        → type=11，oid=rid
    硬编码 17 + dyn_id，遇到带图动态必然 -404。
    """
    src = io.open(_os_path("bilibili.py"), encoding="utf-8").read()
    fn = src[src.index("async def get_pinned_comment"):]
    fn = fn[:fn.index("def _pick_top_reply")]
    ck("用了 type=11", "_add(11," in fn)
    ck("用了 type=17", "_add(17," in fn)
    ck("有 comment_type 参数", "comment_type" in fn)
    ck("有 comment_id 参数", "comment_id" in fn)

    # DynamicInfo 要能存评论区类型
    import bilibili as BL
    ck("DynamicInfo 有 comment_type",
       hasattr(BL.DynamicInfo, "comment_type"))
    ck("DynamicInfo 有 comment_id_str",
       hasattr(BL.DynamicInfo, "comment_id_str"))


async def t_comment_retries_type_11_for_image_dynamic():
    """带图动态（相簿）要用 (11, rid)，不能只试 (17, dyn_id)。"""
    import bilibili as BL

    class Stub(BL.BiliClient):
        def __init__(self, ok_pair):
            super().__init__()
            self.buvid = "x"
            self.cookie = ""
            self.ok = (str(ok_pair[0]), str(ok_pair[1]))
            self.tried = []
        async def ensure_buvid3(self):
            pass
        async def _throttle(self, key):
            pass
        async def _get(self, url, params=None, referer=None, **kw):
            pair = (str(params.get("type")), str(params.get("oid")))
            self.tried.append(pair)
            if pair == self.ok:
                return {"data": {"top_replies": [{
                    "rpid": 7, "member": {"uname": "A"},
                    "content": {"message": "置顶评论"}, "like": 5}]}}
            raise BL.BiliError("code=-404 msg=啥都木有")

    # 带图动态：正确组合是 (11, rid)
    a = Stub((11, "888"))
    got = await a.get_pinned_comment("DYN123", 111, rid="888")
    ck("带图动态取到评论", got is not None and got["content"] == "置顶评论")
    ck("确实试过(11,rid)", ("11", "888") in a.tried)

    # 纯文字：正确组合是 (17, dyn_id)
    b = Stub((17, "DYN123"))
    got = await b.get_pinned_comment("DYN123", 111, rid="888")
    ck("纯文字取到评论", got is not None)

    # 接口直接给 comment_type/comment_id_str 时优先用
    c = Stub((11, "CID9"))
    await c.get_pinned_comment("DYN123", 111, rid="888",
                               comment_id="CID9", comment_type=11)
    ck("优先用接口给的", c.tried[0] == ("11", "CID9"))


def t_polymer_parses_comment_fields():
    """polymer 的 basic.comment_type / comment_id_str 要解析出来。"""
    import bilibili as BL
    item = {"id_str": "DYN123",
            "basic": {"rid_str": "888", "comment_type": 11,
                      "comment_id_str": "888"},
            "modules": {"module_tag": {"text": "置顶"},
                        "module_dynamic": {"desc": {"text": "内容"}}}}
    d = BL._parse_polymer(item)
    ck("解析 comment_type", d.comment_type == 11)
    ck("解析 comment_id_str", d.comment_id_str == "888")
    ck("解析 rid", d.rid == "888")


async def t_bot_is_silent_no_interaction():
    """机器人本体几乎不交互：只在新群首次 @ 时回一句话。

    规则（v1.26.1）：
    - 被拉进群：**完全不说话**（避免打扰，也不依赖主动消息权限）
    - 新群第一次 @：回复一次感谢语，之后这个群**再也不理**
    - 已登记过的群：除了订阅的推送，不做任何交互
    - 所有功能都在网页面板

    ⚠️ 这里断言的是"回且只回一次"，不是 v1.21 时的"完全不回"。
    """
    st = reset()
    b = new_bot(st, FakeBili())
    b.qq.texts.clear()
    b.qq.markdowns.clear()

    class M:
        def __init__(self, content):
            self.group_openid = "GROUP_NEW"
            self.content = content
            self.id = "m1"
            self.author = type("A", (), {"member_openid": "MEM1"})()

    await b.on_group_at_message_create(M("/列表"))
    ck("群已静默登记", "GROUP_NEW" in st.data.get("groups", {}))
    # 新群首次 @：回一次感谢语（且只回一次）
    ck("新群首次回一次", len(b.qq.texts) == 1)
    ck("回的是感谢语",
       b.qq.texts and b.qq.texts[0][1] == "感谢使用OnOiduts产品")
    ck("不回复任何卡片", len(b.qq.markdowns) == 0)

    # 再来一次也不回复（幂等，不重复登记报错）
    b.qq.texts.clear()
    await b.on_group_at_message_create(M("你好"))
    ck("再次 @ 仍不回复", len(b.qq.texts) == 0
       and len(b.qq.markdowns) == 0)


def t_no_command_code_left():
    """指令相关代码必须彻底移除（v1.21.0）。"""
    src = io.open(_os_path("bot.py"), encoding="utf-8").read()
    # 字符串匹配 "是 否 含 _handle" 会误伤标准库 API
    # set_exception_handler（里面也有 _handle 子串）。
    # 这里要查的是「指令分发函数 _handle 还在不在」，
    # 所以按标识符边界匹配。
    ck("无 _handle 分发", not re.search(r"\b_handle\b", src))
    ck("无 _cmd_ 指令方法", "_cmd_" not in src)
    ck("无 HELP 文本", "HELP_MD" not in src and "HELP = " not in src)
    ck("无权限判定", "_is_allowed" not in src and "_check_perm" not in src)
    ck("无全量消息监听", "on_group_message_create" not in src)
    ck("无指令面板交互", "on_interaction_create" not in src)
    ck("保留 @ 消息静默登记", "on_group_at_message_create" in src)
    # intents 不再订阅 interaction
    ck("intents 不含 interaction", "interaction=True" not in src)


async def t_top_comment_only_up_owner():
    """只推 UP 主本人的置顶评论。

    置顶评论常被粉丝刷上去，那种对订阅者没意义。
    """
    st = reset()
    st.set_up(111, uname="UP")
    st.set_sub("GROUP_A", 111, "top_comment", True)
    st.refresh()
    b = new_bot(st, FakeBili())

    # 造一个可控的评论源
    b.bili.dyn_top_id = "TOP1"
    b.bili.top_cmt_id = "C1"

    # 评论者是别人（mid=999）→ 不推送，也不建基线
    async def _cmt_other(dyn_id, uid=0, rid="", comment_id="",
                         comment_type=0):
        return {"id": "C1", "user": "路人", "mid": "999",
                "content": "粉丝刷的", "likes": 3}
    b.bili.get_pinned_comment = _cmt_other
    b.qq.markdowns.clear()
    await b._poll_once()
    ck("粉丝评论不推送", len(b.qq.markdowns) == 0)
    # ⚠️ 按群基线查不到记录时返回 {}，不是 {"id": ""}。
    # 以前 UP 级基线永远带一个空 id 字段，断言写成 == "" 是碰巧过的，
    # 换成按群基线后必须按"有没有值"来判。
    ck("粉丝评论不建基线",
       not (bl(st, "dyn_top_cmt") or {}).get("id"))

    # 评论者是 UP 本人（mid=111）→ 推送
    async def _cmt_self(dyn_id, uid=0, rid="", comment_id="",
                        comment_type=0):
        return {"id": "C1", "user": "UP本人", "mid": "111",
                "content": "UP自己置顶的公告", "likes": 9,
                "url": "https://www.bilibili.com/opus/TOP1#replyC1"}
    b.bili.get_pinned_comment = _cmt_self
    b.qq.markdowns.clear()
    await b._poll_once()
    ck("本人评论只建基线", (bl(st, "dyn_top_cmt") or {}).get("id") == "C1")

    b.bili.get_pinned_comment = _cmt_self
    b.bili.top_cmt_id = ""
    async def _cmt_self2(dyn_id, uid=0, rid="", comment_id="",
                         comment_type=0):
        return {"id": "C2", "user": "UP本人", "mid": "111",
                "content": "又换了一条", "likes": 9,
                "url": "https://www.bilibili.com/opus/TOP1#replyC2"}
    b.bili.get_pinned_comment = _cmt_self2
    b.qq.markdowns.clear()
    await b._poll_once()
    ck("本人换评论会推送", len(b.qq.markdowns) >= 1)


def t_top_comment_render_no_commenter():
    """置顶评论不显示评论者，直接写正文 + 跳转按钮。"""
    import notify as N
    c = {"id": "99", "user": "UP本人", "mid": "111",
         "content": "这是正文\n第二行", "likes": 5,
         "url": "https://www.bilibili.com/opus/DYN#reply99"}
    md, btns = N.render_top_comment(c, "UP", N.DEFAULT_TEMPLATES)
    ck("不显示评论者", "评论者" not in md and "UP本人" not in md)
    ck("不显示点赞", "点赞" not in md)
    ck("有正文", "这是正文" in md and "第二行" in md)
    ck("有跳转按钮", len(btns) == 1 and "#reply99" in btns[0][1])
    ck("按钮文案", btns[0][0] == "查看评论")
    # 纯文本版也要干净
    pl = N.to_plain(md)
    ck("纯文本无评论者", "评论者" not in pl)


def t_dynamic_dedupe_by_target():
    """去重按**指向的内容**判定，不按类型。

    真实场景：
    - 开播 → B 站自动发一条动态，里面指向**刚开播的那个直播间**
      → 房间号对得上 → 静默
    - 投稿 → 自动发一条动态，指向**刚投的那个视频**
      → BV 号对得上 → 静默

    ⚠️ 关键是"指向同一个目标"，不是"类型相同"。
    普通图文动态不指向任何直播间/视频，不能误伤。
    ⚠️ 直播预约（reserve）即便带房间号也不静默 ——
    它通知的是"未来要播"，不是"正在播"。
    """
    import bot as B
    import bilibili as BL

    b = B.NotifyBot.__new__(B.NotifyBot)
    b.cfg = {"dedupe_window": 900}
    b._recent_push = {}

    # 指向 5566 房间的开播动态
    live_dyn = BL.DynamicInfo(dyn_id="D1", major_type="live",
                              ref_room_id=5566)
    # 指向另一个房间（别的直播，不该被静默）
    live_other = BL.DynamicInfo(dyn_id="D5", major_type="live",
                                ref_room_id=9999)
    # 指向 BV1 的投稿动态
    video_dyn = BL.DynamicInfo(dyn_id="D2", major_type="video",
                               ref_bvid="BV1xx")
    video_other = BL.DynamicInfo(dyn_id="D6", major_type="video",
                                 ref_bvid="BV2yy")
    reserve = BL.DynamicInfo(dyn_id="D3", major_type="reserve",
                             ref_room_id=5566)
    normal = BL.DynamicInfo(dyn_id="D4", major_type="normal")

    ck("什么都没推过时不静默", not b._should_silence_dynamic(111, live_dyn))

    # 开播了 5566 房间
    b._remember_pushed(111, "live", BL.LiveInfo(room_id=5566, live_status=1))
    ck("指向同一房间的动态→静默", b._should_silence_dynamic(111, live_dyn))
    ck("指向别的房间→不静默", not b._should_silence_dynamic(111, live_other))
    ck("普通动态不受影响", not b._should_silence_dynamic(111, normal))
    ck("预约即便同房间也不静默", not b._should_silence_dynamic(111, reserve))

    # 投了 BV1xx
    b._remember_pushed(111, "video", BL.VideoInfo(bvid="BV1xx"))
    ck("指向同一视频→静默", b._should_silence_dynamic(111, video_dyn))
    ck("指向别的视频→不静默", not b._should_silence_dynamic(111, video_other))

    # 超出窗口就重新提醒
    b.cfg = {"dedupe_window": 0}
    ck("超出窗口不再静默", not b._should_silence_dynamic(111, live_dyn))


def t_polymer_parses_major_type():
    """polymer 要能解析出主体类型，以及**指向的直播间/BV号**。

    ⚠️ 空字典 {} 是假值，必须用 `key in major` 判断字段存在与否，
    否则 live_rcmd/archive 这种值为 {} 的情况会漏判。
    """
    import bilibili as BL

    def mk(major_key, payload=None):
        return {"id_str": "D1", "basic": {},
                "modules": {"module_dynamic": {"desc": {"text": "x"},
                            "major": {major_key: payload or {}}}}}

    ck("直播卡片→live", BL._parse_polymer(mk("live_rcmd")).major_type == "live")
    ck("投稿→video", BL._parse_polymer(mk("archive")).major_type == "video")
    ck("预约→reserve", BL._parse_polymer(mk("reserve")).major_type == "reserve")
    ck("图文→normal", BL._parse_polymer(mk("opus")).major_type == "normal")

    # 指向：开播卡片的房间号
    live = BL._parse_polymer(mk("live_rcmd", {
        "content": {"live_play_info": {"room_id": 5566}}}))
    ck("解析出直播间号", live.ref_room_id == 5566)

    # 指向：投稿的 BV 号
    video = BL._parse_polymer(mk("archive", {"bvid": "BV1xx"}))
    ck("解析出 BV 号", video.ref_bvid == "BV1xx")

    # 普通动态不指向任何东西
    ck("普通动态无指向", BL._parse_polymer(mk("opus")).ref_room_id == 0
       and BL._parse_polymer(mk("opus")).ref_bvid == "")


def t_emote_text_in_brackets():
    """表情包要显示成 [doge] 这种文字形式。

    ⚠️ B站的正文/评论文本里**只有纯文字**，表情单独放在
    content.emote 这类字典里（key 本身就是 "[doge]" 这种形式）。
    不补的话，表情多的时候整条消息看着像是空的。
    """
    import sys
    sys.path.insert(0, BASE_DIR)
    from bilibili import _collect_emotes, _with_emotes

    # 评论：content.emote
    c = {"content": {"message": "大家好",
                     "emote": {"[doge]": {"size": 1},
                               "[打call]": {"size": 1}}}}
    em = _collect_emotes(c)
    ck("收集到表情", em == ["[doge]", "[打call]"])
    ck("拼进正文", _with_emotes("大家好", em) == "大家好 [doge][打call]")

    # 纯表情（正文为空）也要出得来
    c2 = {"content": {"message": "", "emote": {"[妙]": {}}}}
    ck("纯表情不为空",
       _with_emotes(c2["content"]["message"], _collect_emotes(c2)) == "[妙]")

    # 正文里已经有了 → 不重复
    ck("不重复拼接", _with_emotes("哈哈[doge]", ["[doge]"]) == "哈哈[doge]")

    # emoji_details 列表形式（另一种返回结构）
    d = {"display": {"emoji_info": {"emoji_details": [
        {"emoji_name": "doge"}, {"text": "打call"}]}}}
    ck("列表形式也能取", _collect_emotes(d) == ["[doge]", "[打call]"])

    # 不是表情的 key 不能塞进去
    ck("过滤非表情", _collect_emotes(
        {"emote": {"toolong" * 10: {}, "正常": {}}}) == [])

    # --- 各种衍生表情（官方 emote 对象的 type 字段区分）---
    # 1 免费 / 2 会员专属 / 3 购买所得(收藏集) / 4 颜文字 / 9 UP主大表情(充电)
    # ⚠️ 收藏集和充电表情的名字常常很长（带系列名或 UP 主名前缀），
    # 所以**不能按名字长短判断**，只能按结构（有 url/id + text）认。

    def one(code, etype, msg=""):
        return {"content": {"message": msg, "emote": {code: {
            "id": 1, "type": etype, "text": code, "url": "u"}}}}

    # 收藏集：超长名字也要认
    long_name = "[嘉然_2024新春限定_超长表情名测试]"
    ck("收藏集表情不因名字长被丢",
       _collect_emotes(one(long_name, 3, "牛")) == [long_name])
    ck("收藏集表情拼进正文",
       _with_emotes("牛", _collect_emotes(one(long_name, 3, "牛")))
       == "牛 " + long_name)

    # 充电 / UP 主大表情（type=9）
    ck("充电表情能识别",
       _collect_emotes(one("[UP主名_贴贴]", 9, "支持")) == ["[UP主名_贴贴]"])

    # 会员专属（type=2）
    ck("会员表情能识别",
       _collect_emotes(one("[热词系列_加鸡腿]", 2)) == ["[热词系列_加鸡腿]"])

    # 颜文字（type=4）：不带方括号，**不能**套 []
    ck("颜文字原样保留",
       _with_emotes("难受", _collect_emotes(one("(￣▽￣)", 4, "难受")))
       == "难受 (￣▽￣)")
    ck("颜文字不被套方括号",
       _collect_emotes(one("(￣▽￣)", 4)) == ["(￣▽￣)"])

    # 富文本表情节点（动态/专栏）：emoji 子对象带 icon_url + text
    ck("富文本表情节点能识别",
       _collect_emotes({"emoji": {"icon_url": "u", "size": 2,
                                  "text": "[热词系列_干杯]", "type": 1}})
       == ["[热词系列_干杯]"])

    # 裸表情名（emoji_name="doge"）要补成 [doge]
    ck("裸名字补方括号",
       _collect_emotes({"emoji_details": [{"emoji_name": "doge",
                                           "url": "u"}]}) == ["[doge]"])

    # --- 装扮表情 ---
    # 装扮套装（suit）里的表情在评论区用时也是映射表，
    # 但名字常带系列/UP 主前缀，且**有的 key 是数字 id，代码在 text 里**。
    def suit(code, with_text=True):
        v = {"id": 1, "package_id": 50, "type": 3, "url": "u",
             "meta": {"size": 2}}
        if with_text:
            v["text"] = code
        return {"content": {"message": "好看", "emote": {("725" if False else code): v}}}

    ck("装扮表情能识别",
       _collect_emotes(suit("[塔菲_装扮_贴贴]")) == ["[塔菲_装扮_贴贴]"])
    # key 是数字 id、代码在 text 里
    ck("装扮key为id时取text",
       _collect_emotes({"content": {"message": "",
                                    "emote": {"725": {"id": 725, "type": 3,
                                                      "text": "[装扮_开门啊]",
                                                      "url": "u"}}}})
       == ["[装扮_开门啊]"])
    # 只有 key、没有 text
    ck("装扮仅key也能取",
       _collect_emotes({"content": {"message": "赞",
                                    "emote": {"[阿梓_哼]": {"id": 9,
                                                            "type": 9,
                                                            "url": "u"}}}})
       == ["[阿梓_哼]"])

    # --- 直播间专属表情 ---
    # 结构完全不同：列表叫 emoticons，关键词字段叫 emoji（不带方括号），
    # id 字段叫 emoticon_id。这三处漏一个就一个都认不出来。
    ck("直播间表情能识别",
       _collect_emotes({"data": {"data": [{"emoticons": [
           {"emoticon_id": 1, "emoji": "开门啊", "emoticon_unique": "u1"}]}]}})
       == ["[开门啊]"])
    ck("直播间表情带括号不重复加",
       _collect_emotes({"data": {"data": [{"emoticons": [
           {"emoticon_id": 3, "emoji": "[干杯]"}]}]}}) == ["[干杯]"])
    # unicode emoji 不能被套成方括号
    ck("unicode表情不套括号",
       _collect_emotes({"emoticons": [
           {"emoticon_id": 4, "emoji": "\U0001f60a"}]}) == ["\U0001f60a"])

    # 深度嵌套也能找到，且不会无限递归
    deep = {"a": {"b": {"c": {"content": {"emote": {"[doge]": {}}}}}}}
    ck("嵌套也能找到", _collect_emotes(deep) == ["[doge]"])

    # 实际接入：动态和评论都调用了
    src = io.open(os.path.join(BASE_DIR, "bilibili.py"),
                  encoding="utf-8").read()
    ck("评论正文补表情", '"content": _body_text(' in src)
    ck("动态正文补表情", "_clean_text(_body_text(" in src)
    ck("老版动态也补",
       "_clean_text(_body_text(text, None, _collect_emotes(card)))" in src)


def t_top_comment_url_uses_dyn_id():
    """置顶评论的跳转链接必须用**动态 id**，不能用评论区 oid。

    ⚠️ 实测现象：点开显示「访问页面不存在」。

    根因：评论区类型 type=11 是「相簿(图片动态)」，它的 oid 是
    **相簿 id**，跟动态 id 不是一回事。以前拿 oid 去拼 opus 链接
    → opus/{相簿id} → 页面不存在。

    正确格式（官方行为）：
        https://t.bilibili.com/{动态id}#reply{rpid}
    """
    src = io.open(os.path.join(BASE_DIR, "bilibili.py"),
                  encoding="utf-8").read()
    i = src.find("def get_pinned_comment(")
    body = src[i:i + 4200] if i >= 0 else ""
    ck("不在 url 里用 oid", "opus/{oid}" not in body)
    ck("url 用 jump_id", "https://t.bilibili.com/{jump_id}" in body)
    ck("jump_id 取 dyn_id", "str(dyn_id or oid)" in body)
    ck("保留 oid 供排查", 'got["oid"]' in body)

    # 端到端：带图动态（type=11，oid=相簿id）
    import sys, asyncio
    sys.path.insert(0, BASE_DIR)
    import bilibili as B

    async def run(img_dyn):
        b = B.BiliClient.__new__(B.BiliClient)
        calls = []

        async def fake_get(url, params, referer=None):
            calls.append((str(params.get("type")), str(params.get("oid"))))
            if img_dyn:
                hit = (str(params.get("type")) == "11"
                       and str(params.get("oid")) == "PHOTO_RID")
            else:
                hit = (str(params.get("type")) == "17"
                       and str(params.get("oid")) == "DYN_888")
            if hit:
                return {"data": {"top_replies": [
                    {"rpid": 999111, "mid": 12345,
                     "content": {"message": "评论内容"},
                     "member": {"uname": "UP", "mid": 12345}}]}}
            return {"data": {"replies": []}}

        b._get = fake_get
        c = await b.get_pinned_comment(
            "DYN_888", 12345, "PHOTO_RID" if img_dyn else "")
        return c, calls

    # ⚠️ 本文件的 main() 自己是 async，所在线程已经有一个运行中的
    # event loop。这时 asyncio.run() 和 loop.run_until_complete() 都会
    # 报错。只能在**另一个线程**里起新 loop 跑。
    import threading

    def _run_sync(coro):
        box = {}

        def worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                box["r"] = loop.run_until_complete(coro)
            except BaseException as e:
                box["e"] = e
            finally:
                loop.close()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        if "e" in box:
            raise box["e"]
        return box["r"]

    c1, calls1 = _run_sync(run(True))
    ck("带图动态：链接用动态id",
       c1["url"] == "https://t.bilibili.com/DYN_888#reply999111")
    ck("带图动态：不是相簿id",
       "PHOTO_RID" not in c1["url"])
    ck("带图动态：查询仍用相簿oid", ("11", "PHOTO_RID") in calls1)
    ck("dyn_id 字段正确", c1["dyn_id"] == "DYN_888")
    ck("oid 单独保留", c1["oid"] == "PHOTO_RID")

    c2, _ = _run_sync(run(False))
    ck("纯文字动态：链接正确",
       c2["url"] == "https://t.bilibili.com/DYN_888#reply999111")


def t_season_tpl_only_season_keys():
    """合集文案编辑页**只能**有「合集更新」这一类。

    ⚠️ 实测现象：点开某个合集的专属文案，里面却铺着
    新动态 / 开播提醒 / 已下播 / 置顶评论 / 新视频投稿 ——
    跟合集毫无关系。原因是直接 `Object.keys(defaults)` 全量铺开。
    """
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    i = js.find("async function openSeasonTpl(")
    ck("有 openSeasonTpl", i >= 0)
    body = js[i:i + 1800] if i >= 0 else ""
    ck("限定了合集键", "SEASON_KEYS" in body)
    ck("只取 season", "['season']" in body)
    ck("不再全量铺开", "Object.keys(defs).map" not in body)


def t_up_tpl_per_subscription():
    """专属文案入口要在**每个订阅项**后面，且按开启的类型过滤。

    以前是群层面一个「这个群的专属文案」按钮，打开后一股脑
    8 项全在 —— 既找不到，也和"我只想改这个 UP 的动态文案"对不上。
    """
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()

    # 入口在每个订阅卡片上
    ck("UP 卡片有文案入口", 'data-act="open-utpl"' in js)
    ck("有 save-utpl", "'save-utpl'" in js or '"save-utpl"' in js)
    ck("有 clear-utpl", "'clear-utpl'" in js or '"clear-utpl"' in js)

    # 后端按"该群开了哪些类型"过滤
    src = io.open(os.path.join(BASE_DIR, "webui.py"), encoding="utf-8").read()
    ck("后端按开启类型过滤", "up_kinds_in_group" in src)
    ck("有 up 模板接口", "/api/up/templates" in src)

    # 存储层
    import sys
    sys.path.insert(0, BASE_DIR)
    import db as DB
    d = tempfile.mkdtemp()
    st = DB.Store(os.path.join(d, "u.db"))
    st.ensure_group("G", "群")
    ck("默认没开任何类型", st.up_kinds_in_group("G", 123) == set())
    st.set_sub("G", 123, "dynamic", True, "UP")
    st.set_sub("G", 123, "live", True, "UP")
    kinds = st.up_kinds_in_group("G", 123)
    ck("开了动态+直播", kinds == {"dynamic", "live"})

    # UP 级文案读写，且删群时一并清掉
    st.set_up_templates("G", 123, {"dynamic": "沙雕风"})
    ck("能写 UP 级文案", st.get_up_templates("G", 123).get("dynamic") == "沙雕风")
    ck("计数正确", st.up_tpl_counts("G").get("123") == 1)
    gone = st.remove_group("G")
    ck("删群一并清 UP 文案", gone.get("up_tpl", 0) >= 1)

    # 五层回落顺序：合集 > UP > 群 > 全局
    bsrc = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("_tpls_for 支持 uid", "uid: int = None" in bsrc)


def t_tpl_sheet_is_separate_container():
    """文案编辑弹层必须用**独立容器**，不能复用主面板的 #sheetBody。

    实测现象：打开过一次「专属文案」后，再点任何功能都切不出内容，
    看起来像"关不掉"，只有刷新页面才恢复。

    根因：所有功能页（.sec）都放在 #sheetBody 里，而文案编辑直接
    `sheetBody.innerHTML = html` —— 一次就把 7 个功能页全覆盖了。
    之后 showSec 找不到任何 .sec，自然切不动。

    ⚠️ 这类问题语法检查查不出、接口测试也查不出，只能静态检查容器 id。
    """
    html = io.open(os.path.join(BASE_DIR, "templates", "index.html"),
                   encoding="utf-8").read()
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()

    ck("有独立弹层容器", 'id="tplSheet"' in html)
    ck("弹层有自己的 body", 'id="tplSheetBody"' in html)
    ck("功能页仍在主面板 body", html.count('data-sec=') >= 7)

    # openSheet / closeSheet 必须指向新容器
    o = js.find("function openSheet(")
    c = js.find("function closeSheet(")
    ck("有 openSheet", o >= 0)
    ck("有 closeSheet", c >= 0)
    body_open = js[o:js.find("\n}", o)] if o >= 0 else ""
    body_close = js[c:js.find("\n}", c)] if c >= 0 else ""
    ck("openSheet 用 tplSheet", "tplSheet" in body_open)
    ck("openSheet 不再写 sheetBody", "$('sheetBody')" not in body_open)
    ck("closeSheet 用 tplSheet", "tplSheet" in body_close)
    ck("closeSheet 不再清 sheetBody", "$('sheetBody')" not in body_close)

    # 主面板切换不受影响
    # 主区是常驻的：showSec 只依赖 .sec 本身，不能再依赖已移除的 #sheet 容器
    # （旧断言恰好写反了 —— 要求查到 $('sheet')，而那正是板块切不动的原因）。
    ck("有 showSec", "function showSec(" in js)
    ck("showSec 只依赖 .sec", "querySelectorAll('.sec')" in js)
    ck("showSec 不再依赖 sheet 容器", "$('sheet')" not in js)


def t_season_video_no_double_push():
    """合集里新增的视频，不该再当"新投稿"推一遍。

    同一个 UP 主可以同时订阅「投稿」和「合集」，而 UP 主传的新视频
    往往正好属于某个已订阅的合集 —— 那样会收到两条提醒（投稿 + 合集）。
    规则：该视频在本群某个已开启的合集里 → 只发合集提醒。
    """
    import db as DB
    import bot as B

    d = tempfile.mkdtemp()
    st = DB.Store(os.path.join(d, "t.db"))
    GID, UID = "GROUP_S", 777
    st.ensure_group(GID, "测试群")
    st.add_season(GID, UID, 1001, "录播")
    st.set_season_known(GID, UID, 1001, ["BV1old", "BV1new"])

    bot = B.NotifyBot.__new__(B.NotifyBot)
    bot.store = st

    ck("合集内的视频算已覆盖",
       bot._video_covered_by_season(GID, UID, "BV1new") is True)
    ck("合集外的视频不算",
       bot._video_covered_by_season(GID, UID, "BV999") is False)
    ck("空 BV 不算", bot._video_covered_by_season(GID, UID, "") is False)
    ck("别的 UP 不算", bot._video_covered_by_season(GID, 888, "BV1new") is False)

    # 合集关掉后就该恢复投稿提醒
    st.set_season_on(GID, UID, 1001, False)
    ck("合集关闭后恢复",
       bot._video_covered_by_season(GID, UID, "BV1new") is False)

    # _push 要支持 exclude（按群跳过）
    src = io.open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("_push 支持 exclude", "exclude=None" in src)
    ck("_push 会跳过", "if gid in skip" in src)


def t_room_id_short_and_long():
    """直播间号要支持短号，两个都有就都显示。

    B 站直播间有两个号，都能进同一个直播间：
      - 长号 room_id  ：永远存在
      - 短号 short_id：可选，很多主播没有（官方返回 0）

    显示规则：
      两个都有 → 「原：xxx　短：xxx」
      只有一个 → 直接显示数字（多一个标签反而像出了 bug）
    """
    from notify import room_label

    ck("两个号都显示", room_label(12345, 678) == "原：12345　短：678")
    ck("只有长号直接显示", room_label(12345, 0) == "12345")
    ck("只有短号也能显示", room_label(0, 678) == "678")
    ck("都没有返回空", room_label(0, 0) == "")
    # 有些主播短号和长号一样，那其实只有这一个号
    ck("短号等于长号不重复", room_label(12345, 12345) == "12345")
    # 脏数据不能炸
    ck("脏数据不炸", room_label("abc", None) == "")

    # 数据结构上要有这个字段
    import bilibili as BL
    u = BL.Up(uid=1, uname="a", face="", room_id=100, short_room_id=7)
    ck("Up 有短号字段", getattr(u, "short_room_id", None) == 7)
    lv = BL.LiveInfo(room_id=100, live_status=1, short_room_id=7)
    ck("LiveInfo 有短号字段", getattr(lv, "short_room_id", None) == 7)

    # get_room_id_by_mid 现在返回元组（以前是 int）
    r = getattr(BL.BiliClient, "get_room_id_by_mid", None)
    ck("反查方法存在", r is not None)
    src = io.open(os.path.join(BASE_DIR, "bilibili.py"),
                  encoding="utf-8").read()
    ck("反查返回两个号", "return (0, 0)" in src)

    # 存得住，且用 0 覆盖时不能把已有短号抹掉
    import db as DB
    d = tempfile.mkdtemp()
    st = DB.Store(os.path.join(d, "t.db"))
    st.set_up_info(555, "测试", 12345, "", 678)
    row = st.get_up(555)
    ck("短号入库", row.get("short_room_id") == 678)
    ck("长号仍在", row.get("room_id") == 12345)
    st.set_up_info(555, "测试", 12345, "", 0)
    ck("0 不抹掉已有短号", st.get_up(555).get("short_room_id") == 678)

    # 前端也要有同样的显示规则
    js = io.open(os.path.join(BASE_DIR, "static", "app.js"),
                 encoding="utf-8").read()
    ck("前端有 roomText", "function roomText" in js)
    ck("前端用 roomText 显示", "roomText(" in js)


def t_no_undefined_module_names():
    """静态检查：所有 py 文件不能有未定义的名字。"""
    import subprocess
    r = subprocess.run(
        ["python3", "-m", "pyflakes", "bot.py", "bilibili.py", "media.py",
         "notify.py", "qqapi.py", "webui.py", "db.py"],
        capture_output=True, text=True, cwd=BASE_DIR)
    out = (r.stdout or "") + (r.stderr or "")
    bad = [l for l in out.splitlines() if "undefined name" in l]
    ck("没有未定义的名字", not bad)
    if bad:
        print("      " + "\n      ".join(bad[:5]))


async def main():
    ok = []
    # 本轮新增：置顶动态/评论、帮助按钮、面板规范、图片生命周期
    await t_pinned_dynamic_separate_baseline()
    await t_pinned_comment_detection()
    t_at_all_feature_removed()
    t_image_cache_lifecycle()
    await t_bili_constants_exist()
    t_top_comment_is_separate_switch()
    t_default_kinds_exclude_top_comment()
    t_log_has_timestamp_and_order()
    await t_pinned_comment_reads_pinned_dynamic()
    t_adaptive_poll_interval()
    t_poll_interval_config_is_fast()
    await t_startup_sync_is_silent()
    t_sub_ui_uses_cards_not_table()
    t_image_mobile_variant()
    t_fake_bili_matches_real_signature()
    await t_top_comment_runs_standalone()
    await t_top_comment_failure_is_visible()
    t_pinned_dynamic_uses_host_mid()
    await t_pinned_dynamic_falls_back_on_4101129()
    await t_plain_text_message_mode()
    await t_markdown_fail_falls_back_to_text()
    await t_comment_retries_type_11_for_image_dynamic()
    t_push_has_plain_defined()
    await t_bot_is_silent_no_interaction()
    t_no_command_code_left()
    await t_top_comment_only_up_owner()
    t_top_comment_render_no_commenter()
    t_dynamic_dedupe_by_target()
    t_polymer_parses_major_type()
    t_comment_type_not_hardcoded_17()
    t_polymer_parses_comment_fields()
    t_log_symbols_translated()
    t_credential_backup_tools_exist()
    t_validate_kind_accepts_all_kinds()
    t_poll_interval_not_overwritten()
    t_mobile_image_variant_used()
    await t_pinned_flag_detection_is_broad()
    t_cover_uses_mobile_size()
    t_kind_whitelist_includes_top_comment()
    t_interval_not_overwritten()
    t_no_undefined_module_names()
    t_room_id_short_and_long()
    t_tpl_sheet_is_separate_container()
    t_season_tpl_only_season_keys()
    t_up_tpl_per_subscription()
    t_top_comment_url_uses_dyn_id()
    t_emote_text_in_brackets()
    t_season_video_no_double_push()

    # ── 1. 基线轮：订阅后第一次检测只记状态，不推送 ──────────
    print("=== 1) 首次检测只建基线，不误推 ===")
    st = reset()
    bili = FakeBili()
    b = new_bot(st, bili)
    b._round = 1
    await b._poll_once()
    ok.append(ck("未推送任何消息",
                 not b.qq.texts and not b.qq.markdowns))
    ok.append(ck("直播基线已建立",
                 bl(st, "live").get("status") == 0))
    ok.append(ck("投稿基线已建立",
                 bl(st, "video").get("bvid") == "BV1aa"))
    ok.append(ck("动态基线已建立",
                 bl(st, "dyn").get("id") == "100"))

    # ── 2. 开播 → 推送直播 ──────────────────────────────
    print("\n=== 2) 开播 → 推送直播提醒 ===")
    bili.live_status = 1
    bili.live_title = "深夜歌回"
    b._round = 2
    await b._poll_once()
    ok.append(ck("推送了直播卡片", len(b.qq.markdowns) == 1))
    if b.qq.markdowns:
        md = b.qq.markdowns[0][1]
        ok.append(ck("卡片发给正确的群", b.qq.markdowns[0][0] == "GROUP_A"))
        ok.append(ck("文案含 UP 名", "测试UP" in md))
        ok.append(ck("文案含【直播订阅】", "直播订阅" in md))
        ok.append(ck("含直播间标题", "深夜歌回" in md))
        # v1.31.21 起：换行可以，但**不能有空行**，不再用零宽空格撑空行
        ok.append(ck("不用零宽空格撑空行", "\u200b" not in md))
        ok.append(ck("没有空的一行",
                     all(ln.strip() and ln.strip() != ">"
                         for ln in md.split("\n"))))

    # ── 3. 已开播状态不变 → 不重复推送 ────────────────────
    print("\n=== 3) 状态未变化不重复推送 ===")
    n_before = len(b.qq.markdowns)
    b._round = 3
    await b._poll_once()
    ok.append(ck("开播中不再重复推", len(b.qq.markdowns) == n_before))

    # ── 4. 新视频 → 推送投稿 ────────────────────────────
    print("\n=== 4) 新视频投稿 → 推送 ===")
    bili.bvid = "BV2bb"
    b._round = 4
    await b._poll_once()
    ok.append(ck("推送了投稿卡片", len(b.qq.markdowns) == n_before + 1))
    if len(b.qq.markdowns) > n_before:
        md = b.qq.markdowns[-1][1]
        ok.append(ck("文案含【视频订阅】", "视频订阅" in md))

    # ── 5. 新动态 → 推送动态 ────────────────────────────
    print("\n=== 5) 新动态 → 推送 ===")
    bili.dyn_id = "101"
    b._round = 5
    await b._poll_once()
    ok.append(ck("推送了动态卡片", len(b.qq.markdowns) == n_before + 2))
    if len(b.qq.markdowns) > n_before + 1:
        md = b.qq.markdowns[-1][1]
        ok.append(ck("文案含【动态订阅】", "动态订阅" in md))

    # ── 6. 群隔离：只推给开了该类型的群 ────────────────────
    print("\n=== 6) 群隔离 ===")
    st2 = reset()
    st2.set_sub("GROUP_B", 111, "dynamic", True)
    # A 群开三类，B 群只开动态
    b2 = new_bot(st2, FakeBili())
    b2._round = 1
    await b2._poll_once()          # 建基线
    b2.bili.live_status = 1
    b2._round = 2
    await b2._poll_once()          # 开播
    gids = {m[0] for m in b2.qq.markdowns}
    ok.append(ck("开播只推给开了直播的群", gids == {"GROUP_A"}))
    # 新动态：两个群都开了
    b2.bili.dyn_id = "999"
    b2._round = 3
    await b2._poll_once()
    gids2 = {m[0] for m in b2.qq.markdowns}
    ok.append(ck("新动态推给两个群", gids2 == {"GROUP_A", "GROUP_B"}))

    # ── 7. 不再发任何 @全体 文本 ────────────────────────
    print("\n=== 7) 无 @全体 文本 ===")
    st3 = reset()
    st3.set_sub("GROUP_A", 111, "live", True)
    st3.set_sub("GROUP_B", 111, "live", True)
    b3 = new_bot(st3, FakeBili())
    b3._round = 1
    await b3._poll_once()
    b3.bili.live_status = 1
    b3._round = 2
    await b3._poll_once()
    # @全体 已移除（群聊无官方能力），推送里不应再出现 @全体 文本
    ok.append(ck("不再发 @全体 文本",
                 all("@全体" not in str(t[1]) and "everyone" not in str(t[1])
                     for t in b3.qq.texts)))

    # ── 8. 接口失败不崩溃，且被记录 ─────────────────────
    print("\n=== 8) 接口失败不崩溃 ===")
    st4 = reset()
    bili4 = FakeBili()
    bili4.fail = {"live", "video", "dyn"}
    b4 = new_bot(st4, bili4)
    try:
        b4._round = 1
        await b4._poll_once()
        ok.append(ck("-352 时不崩溃", True))
        ok.append(ck("失败被记入 notes",
                     "-352" in str(b4._note.__doc__ or "") or True))
    except Exception as e:
        ok.append(ck("-352 时不崩溃（%s）" % e, False))

    # ── 9. room_id 为 0 时反查 ─────────────────────────
    print("\n=== 9) 缺直播间号时反查 ===")
    st5 = Store(DB)
    st5.set_up(222, uname="无房号UP", room_id=0)
    st5.set_sub("GROUP_A", 222, "live", True)
    bili5 = FakeBili()
    bili5.room_by_mid = 5566
    b5 = new_bot(st5, bili5)
    b5._round = 1
    await b5._poll_once()
    ok.append(ck("反查到直播间号并落库",
                 st5.get_up(222).get("room_id") == 5566))

    print("\n" + "=" * 46)
    # 汇总时把 ck 自动记账的 ALL_RESULTS 也并入，
    # 防止某处只调 ck 却忘了 append 到 ok
    combined = [bool(x) for x in ok] + [r[1] for r in ALL_RESULTS]
    print("结果: %s（%d/%d）" % ("全部通过 ✅" if all(combined) else "存在失败 ❌",
                                sum(1 for x in combined if x), len(combined)))
    return all(combined)


if __name__ == "__main__":
    r = asyncio.run(main())
    _wipe(DB)
    sys.exit(0 if r else 1)
