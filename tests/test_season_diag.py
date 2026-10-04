# -*- coding: utf-8 -*-
"""v1.34.0：自检第七项 + 推送测试真实数据/封面图 + 短期图保留 5 分钟。

锁的都是"改完又被悄悄改回去"的坑：
 1. 全面自检第七项「合集检测」，没订阅时自动挑最近更新的合集
 2. 合集里视频顺序不保证 → 必须按发布时间挑最新那条
 3. 推送测试「置顶评论」取的是**置顶动态**下的评论（不是最新动态）
 4. 推送测试会带图（视频封面 / 动态配图），卡片里的 ![](url) 要剥掉
 5. 短期图片保留 5 分钟（可复用），不是发完立刻删
"""
import os
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)


def _os_path(rel):
    """源码在 src/；config.example.yaml 这类随包文件在 docs/（老布局在根目录）—— 都找。"""
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        p = os.path.join(os.path.dirname(ROOT), rel)
    _docs = os.path.join(os.path.dirname(ROOT), "docs", rel)
    if os.path.exists(_docs):
        return _docs
    return p

APP = os.path.join(ROOT, "static", "app.js")
WEBUI = os.path.join(ROOT, "webui.py")
BILI = os.path.join(ROOT, "bilibili.py")
MEDIA = os.path.join(ROOT, "media.py")

FAILS = []


def ck(name, cond, extra=""):
    if cond:
        print("  PASS  " + name)
    else:
        print("  FAIL  " + name + ("  " + extra if extra else ""))
        FAILS.append(name)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


app = read(APP)
web = read(WEBUI)
bili = read(BILI)
media = read(MEDIA)

print("\n[1] 自检第七项：合集检测")
ck("前端 DIAG_ROWS 有 season/合集检测",
   "['season', '合集检测']" in app)
ck("后端 _probe_season 已定义", "async def _probe_season" in web)
ck("后端探测结果带 season 键", '"season": ""' in web)
ck("_probe_bili 会跑合集检测", "res[\"season\"] = await _probe_season" in web)
ck("BiliClient 有 get_up_seasons", "async def get_up_seasons" in bili)
ck("清单接口常量已定义", "SEASON_SERIES_LIST" in bili)

# ⚠️ 必须精确取 _probe_season_diag：webui.py 里还有一个
#    _probe_season（添加合集订阅前的校验），签名不同、位置更靠前，
#    用 find("async def _probe_season") 只会命中前面那个。
i_diag = web.find("async def _probe_season_diag(")
ck("后端 _probe_season_diag 已定义（不能跟校验用的 _probe_season 重名）",
   i_diag > 0 and web.count("async def _probe_season(") == 1)
body = web[i_diag:]
body = body[:body.find("\ndef ", 10)] if "\ndef " in body else body
# ⚠️ 必须按发布时间挑最新那条，不能信接口给的顺序。
#    v1.35.0 起这段逻辑抽到了 BiliClient.newest_season_video（共用方法），
#    推送测试的「用真实数据」也走它 —— 所以这里改成断言"委托给它"，
#    真正的排序逻辑在 bilibili.py 里查（另有 test_pushtest_kinds 真跑）。
i_ns = bili.find("async def newest_season_video(")
ns_body = bili[i_ns:bili.find("async def ", i_ns + 10)] if i_ns > 0 else ""
ck("委托给共用方法 newest_season_video", "newest_season_video(" in body)
ck("按 pubdate 挑最新视频（共用方法里）",
   "pubdate" in ns_body and "max(" in ns_body)
ck("没订阅时自动挑最近更新的合集（共用方法里）", "mtime" in ns_body)
ck("取不到合集时说明原因（不算故障）", "没有公开合集" in body)

print("\n[2] 推送测试：置顶评论取真实数据")
i = web.find("def _real_demo")
seg = web[i:i + 4000]
j = seg.find("top_comment")
seg2 = seg[j:j + 1200]
ck("置顶评论改用 get_pinned_dynamic", "get_pinned_dynamic" in seg2)
# ⚠️ 以前用的是 get_latest_dynamic（那条动态常常没有置顶评论）
#    注意只查**代码行**：注释里提到旧做法是为了说明为什么改，不算数
_code2 = "\n".join(l for l in seg2.splitlines()
                   if not l.lstrip().startswith("#"))
ck("不再用 get_latest_dynamic 取置顶评论",
   "get_latest_dynamic" not in _code2)
ck("带上了 rid（带图动态评论区是 type=11）", "rid=" in seg2)
ck("带上了 comment_type / comment_id",
   "comment_type=" in seg2 and "comment_id=" in seg2)

print("\n[3] 推送测试带图（**内嵌**在卡片里，不单独发）")
k = web.find("def api_test")
# ⚠️ 窗口要够宽：api_test 里说明"为什么内嵌"的注释越来越长，
#    3000 字符已经够不到末尾的 send_markdown，导致误判成 0 次。
seg3 = web[k:k + 5000]
# ⚠️ v1.35.0 改内嵌：卡片里的 ![](url) 原样发出去。
#    以前是 send_image 单独发一条、再把卡片里的图剥掉 —— 用户看到的是
#    「一条孤零零的图 + 一张没图的卡片」，所以这条判据要反过来。
ck("不再单独发图（图随卡片走）", "send_image(" not in seg3)
ck("只发一条 markdown", seg3.count("send_markdown(") == 1)
ck("关图时才剥掉卡片里的图", "strip_images(md)" in seg3)
ck("尊重 send_image 开关", 'cfg.get("send_image"' in seg3)
ck("notify 导出 cover_of 与 strip_images",
   "cover_of, render" in web and "strip_images)" in web)
ck("cover_of 支持 season 封面", 'kind == "season"' in read(
    os.path.join(ROOT, "notify.py")))

print("\n[4] 短期图片保留 5 分钟")
ck("有 TMP_TTL 常量", "TMP_TTL = 300" in media)
ck("有 tmp_ttl()（读配置）", "def tmp_ttl()" in media)
ck("save_tmp 支持按 url 命名（可复用）",
   "def save_tmp(data: bytes, url" in media)
ck("有 load_tmp（命中就省一次下载）", "def load_tmp(" in media)
ck("upload_local 会先查短期缓存", "load_tmp(url)" in media)
ck("upload_local 不再发完立刻删", "drop(tmp)" not in media)
ck("异常时返回二元组（不能返回裸字符串，会解包炸）",
   "return \"\", 0" in media and media.count("return \"\", 0") >= 3)
ck("配置里写了 image_tmp_ttl",
   "image_tmp_ttl" in read(_os_path("config.example.yaml")))

# 真跑一遍：落盘 → 命中 → 过期后失效 → 清扫
import media as _media

_d = _media.tmp_ttl()
ck("tmp_ttl() 返回正数", isinstance(_d, int) and _d >= 0, str(_d))
_u = "https://i0.hdslb.com/bfs/test/cover_%d.jpg" % int(time.time())
_data = b"\xff\xd8\xff" + b"0" * 64      # 假的 jpeg 头，够用来测缓存
ck("save_tmp 落盘成功", bool(_media.save_tmp(_data, _u)))
ck("load_tmp 命中", _media.load_tmp(_u) == _data)
ck("load_tmp 过期即失效", _media.load_tmp(_u, max_age=-1) in (b"", None))
_n = _media.sweep_tmp(0)      # 0 = 全清
ck("sweep_tmp(0) 清空全部", _media.load_tmp(_u) == b"")
ck("清空后 tmp 计数归零", (_media.cache_stats() or {}).get("tmp_count") == 0)

print("\n" + "=" * 46)
if FAILS:
    print("FAILED: %d 项 -> %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("全部通过")
