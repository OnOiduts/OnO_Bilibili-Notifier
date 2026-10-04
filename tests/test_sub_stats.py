# -*- coding: utf-8 -*-
"""锁定「订阅数去重口径」的修复（v1.98.5）。

症状（用户实况）：
    面板「订阅」卡片：10  →「UP 主 7 · 合集 3」
    机器人日志：启动同步完成：6 位 UP 主的当前状态已记录为基线
    同一个合集，用户只订了 1 个，卡片却数出 3 个。

根因（两处口径不一致，都在前端现算）：

  1) 合集按 "uid:season_id" 拼字符串去重。
     同一个合集只要在**某个群**里没存 uid（旧数据常见，uid=0），键就变成
     ':3175959'，跟别的群的 '123:3175959' 不是一个键 —— **一个合集数成两个**。
     「按合集筛群」的下拉里同一个合集也列两遍，选哪个都只能筛出一半的群。
     B 站 season_id 是全局唯一的，按它去重才对。

  2) UP 主按 uid 字符串收集，非数字的坏值也算一个；
     机器人侧 all_ups() 只认能 int() 的 —— 面板 7 位、日志 6 位，对不上。

修法：后端 Store.sub_stats() 统一算好，/api/state 回给前端；前端直接用，
     本地兜底也改成与后端同口径（数字 uid、合集按 season_id）。
前端筛选的 season 键同步改成只用 season_id。

顺带锁住：通用设置页「🔔 提醒开关」改成折叠卡片（图片 / 备份早已折叠）。
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
    d = tempfile.mkdtemp(prefix="sst-%s-" % tag)
    os.environ["BILI_NOTIFY_HOME"] = d
    return d


SRC = os.path.join(_ROOT, "src")
JS = open(os.path.join(SRC, "static", "app.js"), encoding="utf-8").read()
HTML = open(os.path.join(SRC, "templates", "index.html"), encoding="utf-8").read()
WEBUI = open(os.path.join(SRC, "webui.py"), encoding="utf-8").read()


def _store(tag):
    _home(tag)
    p = os.path.join(tempfile.mkdtemp(prefix="sstdb-"), "state.json")
    return db.Store(p)


print("\n== 1. 同一个合集被多个群订阅，只算一个（其中一个群没存 uid）==")
st = _store("season")
# 三个群订同一个合集 3175959，其中 G3 没存 uid（旧数据常见）
st.add_season("GAAAAAAAAAAA1", 123, 3175959, "合集A")
st.add_season("GAAAAAAAAAAA2", 123, 3175959, "合集A")
st.add_season("GAAAAAAAAAAA3", 0, 3175959, "合集A")
s = st.sub_stats()
ck(s["seasons"] == 1, "一个合集数成 1 个（旧算法会数成 2 个）：得到 %r" % s["seasons"])

print("\n== 2. 不同合集分别计数 ==")
st.add_season("GAAAAAAAAAAA1", 123, 5550001, "合集B")
s = st.sub_stats()
ck(s["seasons"] == 2, "两个不同合集数成 2 个：得到 %r" % s["seasons"])

print("\n== 3. UP 主跨群去重 + 坏 uid 不算 ==")
st2 = _store("up")
for gid in ("GAAAAAAAAAAA1", "GAAAAAAAAAAA2", "GAAAAAAAAAAA3"):
    st2.set_sub(gid, 123, "live", True)
    st2.set_sub(gid, 123, "dyn", True)
st2.set_sub("GAAAAAAAAAAA1", 456, "video", True)
s2 = st2.sub_stats()
ck(s2["ups"] == 2, "3 个群订同 2 位 UP，只数 2 位：得到 %r" % s2["ups"])
ck(s2["total"] == s2["ups"] + s2["seasons"], "总数 = UP + 合集：%r" % s2)

print("\n== 4. 与机器人侧口径一致（all_ups 能 int 的才算）==")
st3 = _store("baduid")
st3.set_sub("GAAAAAAAAAAA1", 123, "live", True)
# 直接往缓存里塞一个非数字 uid 的坏订阅项（历史数据里出现过）
try:
    st3.data["groups"]["GAAAAAAAAAAA2"] = {"name": "", "subs": {"abc": {"uname": "坏值"}}}
    st3.save()
except Exception:
    pass
s3 = st3.sub_stats()
ck(s3["ups"] == 1, "非数字 uid 不算 UP 主：得到 %r" % s3["ups"])
ck(len(st3.all_ups()) == 1, "与 all_ups() 一致：%r" % len(st3.all_ups()))

print("\n== 5. 关掉的合集不计数 ==")
st4 = _store("off")
st4.add_season("GAAAAAAAAAAA1", 123, 7770001, "合集C")
ck(st4.sub_stats()["seasons"] == 1, "开着时数 1 个")
st4.set_season_on("GAAAAAAAAAAA1", 123, 7770001, False)
ck(st4.sub_stats()["seasons"] == 0, "关掉后数 0 个：%r" % st4.sub_stats()["seasons"])

print("\n== 6. 后端把统计回给前端 ==")
ck('"sub_stats"' in WEBUI or "'sub_stats'" in WEBUI, "/api/state 返回 sub_stats 字段")
ck("st.sub_stats()" in WEBUI, "/api/state 调的是 Store.sub_stats()")

print("\n== 7. 前端统计卡片优先用后端口径 ==")
ck("STATE.sub_stats" in JS, "统计卡片读 STATE.sub_stats")
seg = JS[JS.find("const st = STATE.sub_stats"):JS.find("const errN = ERR_RECENT")]
ck("seaSet.add(sid)" in seg, "兜底算法里合集按 season_id 去重（不再拼 uid）")
ck("/^\\d+$/.test" not in seg and "parseInt" in seg,
   "兜底算法里 UP 只认数字 uid")
ck("String(sv.uid || '') + ':'" not in JS,
   "全站不再用 uid:season_id 拼键")

print("\n== 8. 「按合集筛群」下拉不再把同一合集列两遍 ==")
ck("const sid = String(sv.season_id || '');\n        if (!sid) return;\n        const k = sid;"
   in JS or "const k = sid;" in JS, "下拉条目的键只用 season_id")
mseg = JS[JS.find("function subFilterMatch"):JS.find("function subMatch")]
ck("String(x.season_id || '') === pick" in mseg,
   "筛选匹配也只用 season_id（选哪个都能筛出全部群）")

print("\n== 9. 通用设置页「提醒开关」是折叠卡片 ==")
i = HTML.find('id="st-gen"')
j = HTML.find('id="st-tpl"')
gen = HTML[i:j]
ck('<details class="card fold" id="swCard">' in gen, "提醒开关改成了 details.fold")
ck('id="swCardSum"' in gen, "折叠时有摘要位")
ck("function syncSwCardSum" in JS and "syncSwCardSum();" in JS, "摘要有代码填充")
ck(len(re.findall(r"<details\b", gen)) == len(re.findall(r"</details>", gen)),
   "通用页 details 标签配平")
ck(len(re.findall(r"<div\b", gen)) == len(re.findall(r"</div>", gen)),
   "通用页 div 标签配平")

print("\n== 10. 反向验证：改回旧算法就会多算（证明断言咬得住）==")
st5 = _store("reverse")
st5.add_season("GAAAAAAAAAAA1", 123, 3175959, "合集A")
st5.add_season("GAAAAAAAAAAA2", 0, 3175959, "合集A")
old = set()
for gid, gg in st5.data.get("groups", {}).items():
    for sv in (st5.seasons_of_group(gid) or []):
        old.add(str(sv.get("uid") or "") + ":" + str(sv.get("season_id") or ""))
ck(len(old) == 2, "旧算法把同一合集数成 2 个（%r）" % sorted(old))
ck(st5.sub_stats()["seasons"] == 1, "新算法数成 1 个 —— 差别真实存在")

print("\n%d 项通过，%d 项失败" % (PASS, FAIL))
if FAILED:
    print("失败：")
    for n in FAILED:
        print("  -", n)
    sys.exit(1)
