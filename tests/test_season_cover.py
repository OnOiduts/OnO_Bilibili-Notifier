# -*- coding: utf-8 -*-
"""锁定三件事（v1.98.6）：

  1) 合集前面那个图标换成**合集封面**，且是裁剪后的 1:1。
     以前用的是 UP 主头像（sv["face"]）—— 同一个 UP 主的几个合集
     长得一模一样，根本分不出哪个是哪个。B 站合集封面是 16:9，
     直接当头像用会：① 为 34px 的头像下整张几百 KB 的原图；
     ② 只靠 CSS cover 裁，比例差得多时裁掉哪块不可控。
     所以让**图床**先裁（hdslb 支持 @宽w_高h_1c，1c=裁剪填充不是拉伸）。

  2) 「立即检测」跑完还停在「正在检测所有订阅…」。
     根因：pushCheckNowCard 只在**失败**分支清进度文字，成功时不管，
     于是结果块插进去了、那行带转圈的进度文字还杵在上面。

  3) 检测结果改成**折叠卡片**：5 个 UP 主各有三行，全铺开把下面顶没。
     折着只看一行结论，默认折叠（v2.0.1 起，作者嫌全铺开占满整屏）。

顺带锁住：目录迁移（复制合集订阅）必须连 uname / cover 一起搬 ——
         以前只选了前五列，搬过去后合集又变成一串数字 uid、封面也没了。
"""
import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import db  # noqa: E402

PASS = FAIL = 0
FAILED = []


def ck(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  ", name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  FAIL", name)


def _home(tag):
    d = tempfile.mkdtemp(prefix="scv-%s-" % tag)
    os.environ["BILI_NOTIFY_HOME"] = d
    return d


SRC = os.path.join(_ROOT, "src")
JS = open(os.path.join(SRC, "static", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(SRC, "static", "style.css"), encoding="utf-8").read()
WEBUI = open(os.path.join(SRC, "webui.py"), encoding="utf-8").read()
BOTPY = open(os.path.join(SRC, "bot.py"), encoding="utf-8").read()
BILI = open(os.path.join(SRC, "bilibili.py"), encoding="utf-8").read()


def _store(tag):
    _home(tag)
    p = os.path.join(tempfile.mkdtemp(prefix="scvdb-"), "state.json")
    return db.Store(p)


print("\n== 1. 数据库：seasons 表要有 cover 列 ==")
st = _store("col")
st.add_season("GAAAAAAAAAAA1", 123, 3175959, "合集A", uname="星瞳",
              cover="https://i0.hdslb.com/bfs/x.jpg")
row = st.seasons_of_group("GAAAAAAAAAAA1")
ck(len(row) == 1 and row[0].get("cover") == "https://i0.hdslb.com/bfs/x.jpg",
   "订阅时存下的封面能读回来：%r" % (row[0].get("cover") if row else None))
ck("cover" in db._SEASONS_EXTRA_COLS, "旧库升级会补 cover 列")

print("\n== 2. 只写一次：已有封面不被空值/旧值覆盖 ==")
st.set_season_cover("GAAAAAAAAAAA1", 123, 3175959, "https://i0.hdslb.com/bfs/y.jpg")
row = st.seasons_of_group("GAAAAAAAAAAA1")[0]
ck(row["cover"] == "https://i0.hdslb.com/bfs/x.jpg",
   "已记过就不再覆盖（UP 换封面才改，检测每轮写会白写）：%r" % row["cover"])
st.set_season_cover("GAAAAAAAAAAA1", 123, 3175959, "")
ck(st.seasons_of_group("GAAAAAAAAAAA1")[0]["cover"] != "", "空封面不会把已有封面洗掉")

print("\n== 3. 没记过封面时，检测拿到就补上 ==")
st.add_season("GAAAAAAAAAAA1", 123, 5550001, "合集B", uname="星瞳")
ck(st.seasons_of_group("GAAAAAAAAAAA1")[1]["cover"] == "", "新订阅的合集默认没封面")
st.set_season_cover("GAAAAAAAAAAA1", 123, 5550001,
                    "https://i0.hdslb.com/bfs/z.jpg")
ck(st.seasons_of_group("GAAAAAAAAAAA1")[1]["cover"]
   == "https://i0.hdslb.com/bfs/z.jpg", "第一次拿到就落盘")
ck(st.season_cover(123, 5550001) == "https://i0.hdslb.com/bfs/z.jpg",
   "season_cover() 能按 (uid, season_id) 查")

print("\n== 4. 目录迁移要连 uname / cover 一起搬 ==")
st2 = _store("copy")
st2.add_season("GSRCAAAAAAAAA1", 123, 3175959, "合集A", uname="星瞳",
               cover="https://i0.hdslb.com/bfs/c.jpg")
n = st2.copy_seasons("GSRCAAAAAAAAA1", "GDSTAAAAAAAAA1")
got = st2.seasons_of_group("GDSTAAAAAAAAA1")
ck(n == 1, "搬过去了 1 个合集：%r" % n)
ck(got and got[0].get("cover") == "https://i0.hdslb.com/bfs/c.jpg",
   "封面跟着搬走（旧代码只选前五列，封面会丢）：%r"
   % (got[0].get("cover") if got else None))
ck(got and got[0].get("uname") == "星瞳",
   "UP 主名字也跟着搬（旧代码会变成数字 uid）：%r"
   % (got[0].get("uname") if got else None))

print("\n== 5. 前端：封面裁成 1:1 ==")
ck("function crop1x1(" in JS, "有 crop1x1() 这个工具函数")
m = re.search(r"function crop1x1\(u\)\s*\{(.*?)\n\}", JS, re.S)
body = m.group(1) if m else ""
ck("96w_96h_1c" in body, "B站图床补 @96w_96h_1c（1c=裁剪填充，不拉伸）")
ck("hdslb" in body, "只认 hdslb 图床（别的域名套 B站 @参数取不到）")
ck("function seasonFace(" in JS, "有 seasonFace()：优先合集封面")

print("\n== 6. 前端：群卡片里的合集用封面（不是 UP 主头像）==")
sea = re.search(r"const seaCards = .*?\.join\(''\)", JS, re.S)
seg = sea.group(0) if sea else ""
ck("seasonFace(sv)" in seg, "群卡片seaCards 用 seasonFace(sv)")
ck("data-face=\"' + esc(proxyImg(seasonFace(sv)))" in seg,
   "走 /api/img 代理（B站图床有防盗链，直连拿到的是占位图）")

print("\n== 7. 后端：检测时把封面记下来 / 订阅时存封面 / state 回传 ==")
ck("set_season_cover(g, uid, season_id, info.cover)" in BOTPY,
   "bot 每轮检测到合集封面就记一次")
ck('cover=str(probed.get("cover") or "")' in WEBUI, "订阅合集时一并存封面")
ck('sv["cover"] = str(sv.get("cover") or "")' in WEBUI,
   "/api/state 把 cover 回给前端")
ck('"cover": getattr(info, "cover", "") or ""' in WEBUI,
   "订阅前的探测能拿到封面")
ck('"cover": cv,' in BILI, "合集清单接口也带封面（添加订阅页能预览）")

print("\n== 8. 立即检测：跑完要清掉「正在检测所有订阅…」==")
fn = re.search(r"function pushCheckNowCard\(box, d, body\)\s*\{(.*?)\n\}",
               JS, re.S)
fb = fn.group(1) if fn else ""
ck("progress-text" in fb, "结果块插进去之前先清进度文字")
ck(".remove()" in fb, "是 remove 掉那一行，不是整块 innerHTML=''（会丢历史结果）")
# 失败分支也不能整块清空
seg2 = JS[JS.index("async 'check-now'(btn)"):JS.index("async 'refresh-names'()")]
ck(seg2.count("innerHTML = ''") == 0,
   "失败分支不再整块清空（以前试一次失败就把上一次结果抹了）")

print("\n== 9. 检测结果是可折叠卡片 ==")
ck("createElement('details')" in fb, "结果块用 <details>")
ck("diag-card fold" in fb, "带 fold 类（复用设置页那套折叠样式）")
ck("summary class=\"fold-hd dcard-head\"" in fb, "标题进 summary，整行可点")
ck("fold-ic" in fb, "有展开箭头")
# ⚠️ v2.0.1 起作者要求改成**默认折叠**（CHECK_NOW_CARD_OPEN = false）：
#    5 个 UP × 3 行全铺开会占满整屏，而折着那行已经写了结论
#    「状态没变，不推送」，不展开也知道结果。所以断言的是 false，不是 true。
ck("CHECK_NOW_CARD_OPEN = false" in JS, "最新那块默认折叠（不占满整屏）")
ck("el.open = CHECK_NOW_CARD_OPEN" in fb, "新建的块按开关决定开合")
ck("olds[oi].open = CHECK_NOW_CARD_OPEN" in fb,
   "新块进来时把上一块收起（一次只看一份）")
ck("details.diag-card.fold > summary.fold-hd" in CSS,
   "补了折卡的样式（详情块标题不是 h3，渐变竖条那条规则够不着）")

print("\n== 10. 反向验证：改回旧的写法，这些断言应当变红 ==")
ck(("seasonFace(sv)" in seg) and ("sv.face" not in seg.split("seasonFace")[0][-80:]),
   "群卡片不再直接拿 sv.face 当合集头像")
ck("box.innerHTML = ''" not in fb, "pushCheckNowCard 不整块清空")

print("\n" + "=" * 60)
print("通过 %d / 失败 %d" % (PASS, FAIL))
if FAILED:
    print("失败项：")
    for n in FAILED:
        print("  - " + n)
    sys.exit(1)
