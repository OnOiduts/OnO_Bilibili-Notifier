# -*- coding: utf-8 -*-
"""验证：运行日志不再重复 + 概况页健康度的「轮询」显示实际值。

两件事都是"看着像坏了"但其实一直没接上：
1. 日志重复 —— setup_file_log 被多跑一次就多挂一个写同一个文件的
   FileHandler，之后每条日志写两遍；控制台那条还会被 Tee 再写一遍。
2. 健康度轮询 —— 描述写死「正在按设定检查」，而机器人每轮上报的
   真实间隔（hb.poll.current）早就有了，压根没用。
"""
import os
import re
import sys
import shutil
import subprocess
import tempfile

BASE = "/data/workspace/bili-notify/src"
sys.path.insert(0, BASE)

ok = 0
bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    print(("  PASS " if cond else "  FAIL ") + name +
          (("  " + extra) if extra else ""))
    if cond:
        ok += 1
    else:
        bad += 1


print("== 1. 日志：同文件只挂一个 FileHandler ==")
TMP = tempfile.mkdtemp()
for f in ("bot.py", "bilibili.py", "notify.py", "qqapi.py", "db.py",
          "heartbeat.py", "security.py", "cfgutil.py", "secretbox.py",
          "version.py", "logtranslate.py"):
    p = os.path.join(BASE, f)
    if os.path.exists(p):
        shutil.copy(p, TMP)
sys.path.insert(0, "/data/workspace/_stub")
sys.path.insert(0, TMP)
# ⚠️ 日志已移到数据目录（不再跟源码放一起），必须先把数据目录指到 TMP，
#    否则 setup_file_log 写到 ~/.bili-notify/logs/ 去，这里永远数不到 handler。
os.environ["BILI_NOTIFY_HOME"] = TMP
os.chdir(TMP)

import logging as stdlib_logging  # noqa: E402
import bot as B  # noqa: E402
import bot as B2  # noqa: E402  （同一模块，验证重复调用）

root = stdlib_logging.getLogger()
log_path = os.path.join(TMP, "logs", "bot.log")

n_before = len([h for h in root.handlers
                if os.path.abspath(getattr(h, "baseFilename", "") or "")
                == os.path.abspath(log_path)])
B.setup_file_log()
n_after = len([h for h in root.handlers
               if os.path.abspath(getattr(h, "baseFilename", "") or "")
               == os.path.abspath(log_path)])
chk("再调一次 setup_file_log 不新增 handler",
    n_after == n_before and n_after == 1, "before=%d after=%d" % (n_before, n_after))

print("== 2. Tee 不重复抄写日志行 ==")
# 造一个 Tee，喂它一条「日志格式」的行和一条普通的 print 行
fp = os.path.join(TMP, "_tee.txt")
f = open(fp, "w", encoding="utf-8")
orig_out = os.path.join(TMP, "_orig.txt")
fo = open(orig_out, "w", encoding="utf-8")
tee = B._Tee(fo, f)
tee.write("2026-09-25 12:00:00 [INFO] (bot.py:1)_x 这是一条日志\n")
tee.write("这是一句 print\n")
f.close()
fo.close()
file_txt = open(fp, encoding="utf-8").read()
orig_txt = open(orig_out, encoding="utf-8").read()
chk("日志行不再被 Tee 写第二遍", "这是一条日志" not in file_txt, repr(file_txt[:60]))
chk("print 仍然进文件", "这是一句 print" in file_txt)
chk("控制台两行都在（不影响窗口输出）",
    "这是一条日志" in orig_txt and "这是一句 print" in orig_txt)

print("== 3. /api/log 相邻完全重复只留一条 ==")
# ⚠️ 必须另起一个进程在干净目录里跑：webui 的 BASE_DIR 在**导入时**就定死了
#    （= webui.py 所在目录），在同一个进程里 chdir + reload 改不掉 __file__，
#    结果读的仍是源码目录里那份被测试污染的 bot.log。
_SUB = r'''
import os, sys, json, shutil, tempfile
BASE = "/data/workspace/bili-notify/src"
W = tempfile.mkdtemp()
for f in os.listdir(BASE):
    if f.endswith(".py"):
        shutil.copy(os.path.join(BASE, f), W)
if os.path.exists(os.path.join(BASE, "VERSION")):
    shutil.copy(os.path.join(BASE, "VERSION"), W)
os.environ["BILI_NOTIFY_HOME"] = W
os.makedirs(os.path.join(W, "logs"), exist_ok=True)
os.chdir(W)
with open(os.path.join(W, "logs", "bot.log"), "w", encoding="utf-8") as fh:
    fh.write("2026-09-25 12:00:00 [INFO] (bot.py:1)_a 第一条\n")
    fh.write("2026-09-25 12:00:00 [INFO] (bot.py:1)_a 第一条\n")
    fh.write("2026-09-25 12:00:01 [INFO] (bot.py:1)_b 第二条\n")
    fh.write("2026-09-25 12:00:02 [INFO] (bot.py:1)_a 同名但不同秒\n")
sys.path.insert(0, "/data/workspace/_stub")
sys.path.insert(0, W)
import webui
c = webui.app.test_client()
d = c.get("/api/log?limit=50").get_json()
print("__JSON__" + json.dumps({"ok": d.get("ok"),
                               "lines": d.get("lines") or []},
                              ensure_ascii=False))
'''
_p = subprocess.run([sys.executable, "-c", _SUB], capture_output=True,
                    text=True, timeout=180)
if "__JSON__" in (_p.stdout or ""):
    import json
    _d = json.loads((_p.stdout.split("__JSON__", 1)[1]).splitlines()[0])
    _lines = _d.get("lines") or []
    chk("接口返回成功", _d.get("ok") is True)
    n1 = sum(1 for l in _lines if "第一条" in l)
    n2 = sum(1 for l in _lines if "同名但不同秒" in l)
    chk("紧邻重复行只留一条", n1 == 1, "出现 %d 次" % n1)
    chk("不同时间戳的同名行不被误删", n2 == 1, "出现 %d 次" % n2)
else:
    chk("/api/log 子进程跑通", False,
        ((_p.stderr or "")[-200:] or (_p.stdout or "")[-120:]))

print("== 4. 健康度轮询：不再写死「正在按设定检查」 ==")
os.chdir(BASE)
src = open(os.path.join(BASE, "static", "app.js"), encoding="utf-8").read()
chk("写死的旧文案已移除", "正在按设定检查" not in src)
chk("有取实际间隔的函数", "function pollActual" in src)
chk("有判断是否放慢的函数", "function pollBackingOff" in src)
chk("轮询项用上了实际值", "pollText(hb, alive)" in src)

# 把这几个纯函数抽出来在 node 里真跑一遍
seg = src[src.find("function pollActual"):src.find("function renderHealth")]
open("/data/workspace/_poll_fn.js", "w", encoding="utf-8").write(
    "var STATE={config:{poll_interval:8}};\n" + seg + """
console.log(JSON.stringify({
  actual_only: pollText({poll:{current:24,base:8}}, true),
  same: pollText({poll:{current:8,base:8}}, true),
  faster: pollText({poll:{current:8,base:24}}, true),
  no_poll: pollText({}, true),
  dead: pollText({}, false),
  bo_flag: pollBackingOff({poll:{current:24,base:8,backing_off:true}}),
  bo_num: pollBackingOff({poll:{current:24,base:8}}),
  bo_no: pollBackingOff({poll:{current:8,base:8}}),
  fallback_cfg: pollText({}, true)
}));
""")

print("== 5. 轮询文案（node 实跑） ==")
import subprocess  # noqa: E402
p = subprocess.run(["node", "/data/workspace/_poll_fn.js"],
                   capture_output=True, text=True)
if p.returncode == 0:
    import json
    r = json.loads(p.stdout.strip().splitlines()[-1])
    chk("实际 24s / 设定 8s → 说明已放慢",
        "24" in r["actual_only"] and "8" in r["actual_only"]
        and "放慢" in r["actual_only"], r["actual_only"])
    chk("与设定一致时明说一致", "一致" in r["same"], r["same"])
    chk("加快时说已加快", "加快" in r["faster"], r["faster"])
    chk("没心跳时不编造", "心跳" in r["dead"] or "还没" in r["dead"], r["dead"])
    chk("backing_off 标记生效", r["bo_flag"] is True)
    chk("没标记时按数值判断", r["bo_num"] is True)
    chk("没放慢时不误报", r["bo_no"] is False)
    chk("拿不到实际值时退回设定值", "8" in r["fallback_cfg"], r["fallback_cfg"])
else:
    chk("node 跑通", False, p.stderr[:120])

print("\n结果: %d 项通过, %d 项失败" % (ok, bad))
sys.exit(1 if bad else 0)
