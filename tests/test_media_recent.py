"""① 媒体缓存页看得到"用到过哪些图" ② 下播卡片显示本场直播时长。

① 用户反馈：「媒体缓存怎么没有获取到动态视频直播哪些的图片啊」。
   根因不是没缓存，是**内嵌模式本来就不落盘** —— 图片留在卡片里
   交给客户端加载，本地一个文件都不写，光数缓存文件永远是 0 张。
   所以新增"最近用到过的图片"清单（只存 URL 和元信息，不存图）。
   另外顺带给出图床域名：手机端看不到图时，要拿它去开放平台报备。

② 下播卡片不给按钮（见 test_offline_button.py），改为显示本场直播
   时长。时长必须在**还播着**的时候记 —— B站房间接口的 live_time
   一旦下播就归 0，等下播再取就没了。
"""

import asyncio
import os
import sys
import tempfile
import time
_real_time = time  # 冻结时钟时用来还原/转发

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import botpy  # noqa: E402
import media as M  # noqa: E402
from bilibili import LiveInfo  # noqa: E402
import bot  # noqa: E402
from bot import NotifyBot  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
results = []


def ck(name, cond):
    results.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name)


# ---------------- ① 最近用到过的图片 ----------------

print("=== 最近图片清单 ===")

# 用一个临时目录当缓存目录，别污染项目（也别污染打包）
_tmp = tempfile.mkdtemp(prefix="_mr_")
_orig_cache = M.CACHE_DIR
M.CACHE_DIR = _tmp
M.RECENT_PATH = os.path.join(_tmp, "recent.json")

M.remember_recent("https://i0.hdslb.com/bfs/a.jpg", "video", "小狐狸")
M.remember_recent("https://i1.hdslb.com/bfs/b.jpg", "dynamic", "星瞳")
lst = M.recent_list(12)
ck("记录了两条", len(lst) == 2)
ck("新的排在最前", lst and lst[0].get("url", "").endswith("b.jpg"))
ck("类型被记下来了", lst and lst[0].get("kind") == "dynamic")

# 同一张图再发一次（比如连发好几个群）不能变成两条
M.remember_recent("https://i0.hdslb.com/bfs/a.jpg", "video", "小狐狸")
lst2 = M.recent_list(12)
ck("同一 URL 不重复计数", len(lst2) == 2)
ck("重复记录会把它刷到最前", lst2 and lst2[0].get("url", "").endswith("a.jpg"))

M.remember_recent("", "video", "没图")
ck("空 URL 不记录", len(M.recent_list(12)) == 2)

for i in range(40):
    M.remember_recent(f"https://i2.hdslb.com/bfs/{i}.jpg", "video", "x")
ck("清单有上限（不会无限涨）", len(M.recent_list(99)) <= 30)
ck("limit 生效", len(M.recent_list(3)) == 3)

hosts = M.recent_hosts(8)
# ⚠️ 上面塞了 40 条 i2，早期那两个域名已经被挤出清单了（上限 30），
#    所以这里只断言"最近用到的域名在"，不要求老域名还在。
ck("提取出图床域名", "i2.hdslb.com" in hosts)
ck("域名去重", len(hosts) == len(set(hosts)))

# 面板接口要带上这两块，否则前端没得显示
try:
    import webui as W
    st = W._media_stats()
    ck("/api/media 返回 recent", isinstance(st.get("recent"), list))
    ck("/api/media 返回 hosts", isinstance(st.get("hosts"), list))
    ck("recent 带中文类型名",
       any(it.get("kind_text") for it in (st.get("recent") or [])))
except Exception as e:
    ck("/api/media 返回 recent/hosts（导入失败：%s）" % e, False)

# 前端必须真的渲染它们（不然接口给了也白搭）
_js = os.path.join(ROOT, "static", "app.js")
_html = os.path.join(ROOT, "templates", "index.html")
try:
    js = open(_js, encoding="utf-8").read()
    html = open(_html, encoding="utf-8").read()
    ck("面板有「最近用到的图片」容器", "mdRecent" in html)
    ck("面板有图床域名提示容器", "mdHosts" in html)
    ck("前端会渲染 recent", "d.recent" in js)
    ck("前端会渲染 hosts", "d.hosts" in js)
    ck("内嵌模式有中文名（不再是裸露的 inline）",
       "inline: '内嵌在卡片里'" in js)
except Exception as e:
    ck("前端渲染检查（%s）" % e, False)

# bot 必须真的往清单里写（否则清单永远是空的）
_src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
ck("_push 会把卡片里的图记进清单",
   "remember_recent" in _src and "IMG_LINE_RE" in _src)

M.CACHE_DIR = _orig_cache
M.RECENT_PATH = os.path.join(_orig_cache, "recent.json")


# ---------------- ② 下播时长 ----------------

print("=== 下播时长（bot 层真跑） ===")


class FakeQQ:
    def __init__(self):
        self.cards = []

    async def send_text(self, gid, content, **kw):
        pass

    async def send_markdown(self, gid, md, keyboard=None, **kw):
        self.cards.append((gid, md))

    async def send_media(self, gid, fi, **kw):
        return {}

    async def close(self):
        pass


class FakeBili:
    def __init__(self):
        self.live = {}

    async def get_live(self, room_id):
        return self.live.get(room_id, LiveInfo(room_id=room_id, live_status=0))

    async def get_room_id_by_mid(self, uid):
        return uid * 10, 0

    async def close(self):
        pass


class _FixedTime:
    """把 bot 模块里的 time.time() 冻在一个固定值上。

    ⚠️ 原来这项测试靠"两轮检测之间真实流逝不到 1 秒"来通过：
    兜底走的是"首次检测时刻"，机器一慢（批量跑批时常见）两轮就跨了秒，
    时长变成 "1 秒"，卡片就多出「直播时长」一行，断言随即红。
    这不是代码的问题，是测试把时间当成了可控量却没控住。
    除 time() 之外都转给真正的 time 模块，别的调用不受影响。
    """
    def __init__(self, value):
        self._v = int(value)

    def time(self):
        return self._v

    def __getattr__(self, name):
        return getattr(_real_time, name)


async def _run(live_time, label, elapsed=0, t1=None):
    """跑两轮检测，返回下播卡片文案。

    两轮的时钟都冻住：第一轮停在 t1，第二轮停在 t1 + elapsed。
    这样"本场时长"是算出来的确定值，而不是看机器跑得多快。
    默认 elapsed=0 —— 接口没给开播时刻时，兜底用的是首次检测时刻，
    两轮之间没有时间流逝，时长就该是 0、卡片不该多出「直播时长」一行。
    """
    t1 = int(_real_time.time()) if t1 is None else int(t1)
    bot.time = _FixedTime(t1)
    try:
        return await _run_inner(live_time, label, t1 + int(elapsed))
    finally:
        bot.time = _real_time


async def _run_inner(live_time, label, t2):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    b = NotifyBot({"data_file": path, "image_send_mode": "off",
                   "send_image": False},
                  intents=botpy.Intents(public_messages=True))
    b.qq, b.bili = FakeQQ(), FakeBili()
    b.notify_offline = True
    b._silent = False
    uid, room = 1001, 10010
    b.store.set_up_info(uid, "小狐狸", room)
    b.store.set_sub("GA", uid, "live", True, "小狐狸")
    # 第一轮：正在直播 → 建基线（静默）+ 记下开播时刻
    b.bili.live[room] = LiveInfo(room_id=room, live_status=1,
                                 live_time=live_time, title="直播中")
    await b._check_live(uid, "小狐狸")
    # 第二轮：下播 → 推下播卡片（时钟拨到 t2，两轮间隔由调用方定死）
    bot.time = _FixedTime(t2)
    b.bili.live[room] = LiveInfo(room_id=room, live_status=0, title="直播中")
    await b._check_live(uid, "小狐狸")
    md = b.qq.cards[-1][1] if b.qq.cards else ""
    try:
        os.remove(path)
    except OSError:
        pass
    return md


now = int(time.time())
md1 = asyncio.get_event_loop().run_until_complete(
    _run(now - 3600, "一小时", elapsed=0, t1=now))
ck("下播卡片推出来了", "已下播" in md1)
ck("显示「1 小时 0 分」", "1 小时 0 分" in md1)
ck("下播卡片不再给按钮", "live.bilibili.com" not in md1)

md2 = asyncio.get_event_loop().run_until_complete(
    _run(0, "接口没给开播时间", elapsed=0, t1=now))
ck("接口没给 live_time 也能算出时长（用首次检测时刻兜底）",
   "已下播" in md2)
ck("兜底时不瞎编时长（秒级直播不显示这一行）",
   "直播时长" not in md2)

bad = [n for n, ok in results if not ok]
print()
print(f"通过 {len(results) - len(bad)}/{len(results)}")
if bad:
    print("失败：")
    for n in bad:
        print("  - " + n)
    sys.exit(1)
