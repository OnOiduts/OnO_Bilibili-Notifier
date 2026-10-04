# -*- coding: utf-8 -*-
"""自检页 / 推送测试页的 5 项细节（v1.31.22）。

锁的都是"改完又被悄悄改回去"的坑：
 1. 「已记住」不能出现重复前缀，且必须是「用户 | uid：xxx」
 2. 全面自检固定七行（含「合集检测」），点之前一律「未检测」
 3. 推送测试的沙盒群提示要有边框（.note），且不含群号
 4. 「用真实数据」用测试 UID 取真实内容（方法名必须真实存在）
 5. 「模拟未设置沙盒群」已移除，不留残骸
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

APP = os.path.join(ROOT, "static", "app.js")
HTML = os.path.join(ROOT, "templates", "index.html")
WEBUI = os.path.join(ROOT, "webui.py")

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
html = read(HTML)
web = read(WEBUI)

print("\n[1] 已记住：格式与重复前缀")
# JS 里不能再拼「已记住：」——HTML 已有前缀
ck("JS 不再重复拼『已记住：』",
   "已记住：'" not in app.replace("'已记住测试 UID：'", ""),
   "app.js 里仍有 '已记住：' 拼接")
m = re.search(r"function showChkUid\s*\([^)]*\)\s*\{(.*?)\n\}", app, re.S)
body = m.group(1) if m else ""
ck("showChkUid 输出『用户 | uid：』", "uid：" in body and "|" in body)
ck("showChkUid 有『用户』兜底", "'用户'" in body)
ck("HTML 保留唯一前缀", "已记住：<b id=\"chkUidNow\">" in html)
ck("后端返回 selfcheck_uname", "selfcheck_uname" in web)
ck("_selfcheck_uname 已定义", "def _selfcheck_uname" in web)

print("\n[2] 全面自检：固定七行 + 未检测")
ck("DIAG_ROWS 七项齐全",
   all(k in app for k in
       ("'直播间'", "'直播状态'", "'最新投稿'", "'最新动态'",
        "'置顶动态'", "'置顶评论'", "'合集检测'")))
ck("DIAG_ROWS 第七项是 season", "'season'" in app and "'合集检测'" in app)
ck("renderDiag 已定义", "function renderDiag" in app)
ck("未跑时显示『未检测』", "'未检测'" in app)
ck("diagnose 先刷未检测再填值", "renderDiag(null)" in app)
ck("启动时铺七行", "renderDiag(null)" in app)
ck(".diag-row 有样式（否则挤成一坨）", ".diag-row {" in html)
ck("自检行有状态色 ok/bad/idle",
   all(x in html for x in (".dv.idle", ".dv.ok", ".dv.bad")))

print("\n[3] 推送测试沙盒群提示")
i = html.find("sbName")
seg = html[max(0, i - 400):i + 200]
ck("提示用 .note（有边框）", 'class="note"' in seg, seg[:160])
ck("提示不再用裸 .hint", 'class="hint"' not in seg)
ck("syncSandbox 不拿群号顶群名", "未命名群" in app)

print("\n[4] 用真实数据：取测试 UID 的真实内容")
ck("_real_demo 用 selfcheck_uid", "selfcheck_uid" in web)
i2 = web.find("def _real_demo")
body2 = web[i2:i2 + 3000]
# ⚠️ 只看**实际调用** c.get_videos( ，不能只搜方法名：
#    注释里说明了"这些方法不存在"，会把名字带进去造成误报。
ck("不再调不存在的 get_videos", "c.get_videos(" not in body2)
ck("不再调不存在的 get_dynamics", "c.get_dynamics(" not in body2)
ck("不再调不存在的 get_season(", "c.get_season(" not in body2)
ck("直播取真实 room_id", "get_live(up.room_id)" in body2)
ck("投稿用 get_latest_video", "get_latest_video" in body2)
ck("动态用 get_latest_dynamic", "get_latest_dynamic" in body2)
ck("置顶动态用 get_pinned_dynamic", "get_pinned_dynamic" in body2)
ck("置顶评论用 get_pinned_comment", "get_pinned_comment" in body2)

print("\n[5] 模拟未设置沙盒群已移除")
ck("HTML 无该按钮", "sim-nosandbox" not in html)
ck("JS 无该处理", "sim-nosandbox" not in app)

print("\n" + "=" * 46)
if FAILS:
    print("FAILED: %d 项 -> %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("全部通过")
