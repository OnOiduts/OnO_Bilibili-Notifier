"""验证：日志不全（多文件归并）+ 群代码翻译成群名 + 翻译不再指错方向。"""
import os, sys, time, shutil, tempfile
sys.path.insert(0, "/data/workspace/bili-notify/src")

TMP = tempfile.mkdtemp()
os.chdir(TMP)
for f in ("bot.py", "webui.py", "db.py", "logtranslate.py", "security.py"):
    shutil.copy(os.path.join("/data/workspace/bili-notify/src", f), TMP)

import logtranslate as lt

ok = 0
def chk(name, cond, extra=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + name + (("  " + extra) if extra else ""))
    ok += 1 if cond else 0

print("== 1. 翻译不再指错方向 ==")
e = lt.explain("[ERROR] (bot.py:1023)_safe  [伊万] dyn 检测异常："
               "'str' object has no attribute 'get'")
chk("str 脏数据不判成版本不一致", "版本不一致" not in (e["meaning"] or ""),
    "meaning=" + (e["meaning"] or "")[:40])
chk("给出可操作的办法", "重新勾选" in (e["howto"] or ""))

e2 = lt.explain("[ERROR] (bot.py:1)_poll_loop 轮询异常："
                "'NotifyBot' object has no attribute '_reload_cookie'")
chk("其它类型仍按版本不一致提示", "版本不一致" in (e2["meaning"] or ""))

print("== 2. 标识符不再被翻坏 ==")
cn = lt.translate_symbols("[INFO]  (bot.py:876)_do_startup_sync    启动同步：3 个合集")
chk("_do_startup_sync 整体翻译", "启动静默同步" in cn and "_do_startup" not in cn, cn[:70])
cn2 = lt.translate_symbols("[INFO]  (bot.py:1)_reload_cookie  x")
chk("_reload_cookie 不再半中半英", "_reload_登录凭据" not in cn2, cn2[:70])

print("== 3. 群代码 → 群名 ==")
import webui
webui.get_store = lambda: type("S", (), {"data": {"groups": {
    "6FBBD9B5ABCDEF": {"name": "OnOiduts工业区"},
    "DDD81F36AAAAAA": {"name": "测试群"},
}}})()
gmap = webui._group_name_map()
chk("映射到前8位", gmap.get("6FBBD9B5") == "OnOiduts工业区", str(gmap))
s = webui._humanize_groups("已推送【置顶评论】星瞳 → 群 6FBBD9B5…", gmap)
chk("替换为群名", "群「OnOiduts工业区」" in s and "6FBBD9B5" not in s, s)
s2 = webui._humanize_groups("已推送 → 群 ZZZZZZZZ…", gmap)
chk("未登记的群原样保留", "ZZZZZZZZ" in s2, s2)

print("== 4. 多文件归并：最新的 bot.log 不再被挤掉 ==")
# bot.log 很长且最新；两个小文件排在后面（旧写法会被它们占满尾部）
with open(os.path.join(TMP, "bot.log"), "w", encoding="utf-8") as f:
    for i in range(500):
        f.write("2026-09-21 10:%02d:%02d [INFO]  (bot.py:1)_push 老记录 %d\n"
                % (i // 60, i % 60, i))
    for i in range(150):
        f.write("2026-09-21 23:%02d:%02d [INFO]  (bot.py:1)_push 最新记录 %d\n"
                % (i // 60, i % 60, i))
now = time.time()
for name, n in (("desktop.log", 80), ("security.log", 80)):
    with open(os.path.join(TMP, name), "w", encoding="utf-8") as f:
        for i in range(n):
            f.write("2026-09-20 01:%02d:%02d [INFO] %s 陈旧 %d\n"
                    % (i // 60, i % 60, name, i))
    os.utime(os.path.join(TMP, name), (now - 86400, now - 86400))

# ⚠️ 日志接口读的是 paths.log_dir()（数据目录下的 logs/），不是 BASE_DIR。
#    只改 BASE_DIR 的话，它会去读真实数据目录里那个 4 万多行的大日志，
#    测试造的小文件一条也读不到 —— 断言"包含最新记录"必然失败。
#    所以改 paths.log_dir 让它指到本次的临时目录。
webui.paths.log_dir = lambda: TMP
app = webui.app
app.config["TESTING"] = True
c = app.test_client()
r = c.get("/api/log?limit=200")
d = r.get_json()
chk("接口 200", r.status_code == 200 and d.get("ok"), str(r.status_code))
items = d.get("items") or []
txt = "\n".join((i.get("raw") or "") for i in items)
chk("包含 bot.log 最新记录", "最新记录 149" in txt, "共 %d 条" % len(items))
chk("不再被陈旧文件占满", txt.count("陈旧") <= 40,
    "陈旧 %d 条 / 最新 %d 条" % (txt.count("陈旧"), txt.count("最新记录")))
print("  总条数 total =", d.get("total"), "返回 items =", len(items))
print("\n通过 %d 项" % ok)
