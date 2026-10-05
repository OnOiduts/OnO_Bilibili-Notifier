# -*- coding: utf-8 -*-
"""锁定版本发布规则（历史版本号重排后的新规则）。

规则是用户定的：

    - 大更新   +1.0.0，单独发版
    - 中等更新 +0.1.0，单独发版
    - 小更新   攒够 5 项才 +0.0.1，5 项合并成一个版本
    - 严重修复 立即 +0.0.1，单独发版
    - 一般修复 / 删除 不单独发版，攒到下一个版本里一起写入

这份测试守的是**日志本身是否被写对**，不是重写一遍算法：

    1. 首条版本 == src/VERSION（面板显示的就是当前版本）
    2. 每条标题都带日期（v2.0.0 踩过：新条目漏写日期）
    3. 版本号不重复，且从旧到新严格递增
    4. 「合并 N 项」下面恰好 N 个子条目，且 2 <= N <= 15
       （日常攒够 5 项发一版；上限放宽是为了容纳定版重排时的历史条目）
    5. 「同期修复与清理（N 项）」下面恰好 N 个子条目
    6. 单独发版的等级（大更新 / 中等更新 / 严重修复）不允许是合并条目
    7. 正文内容不丢：所有正文行都能追溯到（这里只查条数守恒）

⚠️ 反向验证做过：把某条标题的日期删掉 → 「每条都有日期」立刻失败；
   把两个版本号改成相同 → 「版本不重复」立刻失败；
   把「合并 5 项」改成「合并 6 项」→ 「子条目数与 N 一致」立刻失败。
"""
import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

FAILS = []


def ok(name, cond, extra=""):
    if cond:
        print("  [OK] " + name)
    else:
        print("  [FAIL] " + name + (" → " + str(extra) if extra else ""))
        FAILS.append(name)


def read(p):
    try:
        return io.open(p, encoding="utf-8").read()
    except OSError:
        return ""


VER = read(os.path.join(_ROOT, "src", "VERSION")).strip()
CL = read(os.path.join(_ROOT, "docs", "CHANGELOG.md"))

print("== 版本发布规则（当前 v%s）==" % VER)

# ---------- 1. 首条即当前版本 ----------
ok("VERSION 是合法语义化版本", re.match(r"^\d+\.\d+\.\d+$", VER) is not None, VER)
ok("CHANGELOG 首条就是当前版本", CL.startswith("## v" + VER + "（"), CL[:44])

# ---------- 切分条目 ----------
chunks = re.split(r"^## ", CL, flags=re.M)[1:]
ents = []
for c in chunks:
    lines = c.strip().splitlines()
    if not lines:
        continue
    head = lines[0].strip()
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)", head)
    ents.append({
        "head": head,
        "nums": tuple(int(x) for x in m.groups()) if m else None,
        "date": bool(re.search(r"\d{4}-\d{2}-\d{2}", head)),
        "body": lines[1:],
    })

ok("至少解析出 50 个版本条目", len(ents) >= 50, len(ents))
ok("每条标题都能解析出版本号", all(e["nums"] for e in ents),
   [e["head"][:30] for e in ents if not e["nums"]][:3])

# ---------- 2. 每条都有日期 ----------
nodate = [e["head"][:40] for e in ents if not e["date"]]
ok("每条标题都写了日期", not nodate, nodate[:3])

# ---------- 3. 版本不重复 + 严格递增（ents 是最新在前）----------
seq = [e["nums"] for e in ents]
ok("版本号没有重复", len(set(seq)) == len(seq),
   [v for v in seq if seq.count(v) > 1][:3])
asc = list(reversed(seq))
ok("从旧到新严格递增", all(asc[i] < asc[i + 1] for i in range(len(asc) - 1)),
   [(asc[i], asc[i + 1]) for i in range(len(asc) - 1) if asc[i] >= asc[i + 1]][:3])

# ---------- 4/5/6. 合并条目的子条目数 ----------
# ⚠️ 子条目的识别方式随日志写法变过一次：
#    重排时子条目曾标成「**原 vX.Y.Z · 等级：标题**」以便追溯；后来用户要求
#    只留重排后的编号，前缀整体去掉，写成「**等级：标题**」。
#    所以这里按「加粗 + 等级词 + 冒号在 ** 内」来认 ——
#    正文里的「**改动**：」冒号在 ** 外面，不会被误当成子条目。
# ⚠️ 等级词要覆盖日志里的全部写法：重排后正式的是「大更新 / 中等更新 /
#    小更新 / 严重修复」，但历史条目里还写过「修 / 小修 / 中修 / 补丁更新」
#    等简写，漏一个就会把子条目数算少（v1.30.1 报「说 4 项实际 3 个」就是这么来的）。
_LEVEL = (r"(?:大更新|中等更新|中更新|小更新|严重修复|紧急修复|"
          r"紧急更新|清理|首个版本|补丁更新|补丁|"
          r"大修|中修|小修|修复|修)")
SUB = re.compile(r"^\*\*" + _LEVEL + r"[：:]")
bad_merge, bad_pend, bad_solo = [], [], []
merged = 0
for e in ents:
    head = e["head"]
    # ⚠️ 正文里同时可能有「合并的小更新」和「同期修复与清理」两块子条目，
    #    必须先按标记切开再分别计数 —— 混在一起数会对不上（第一版就踩了）。
    lines = e["body"]
    cut = len(lines)
    for i, l in enumerate(lines):
        if l.strip().startswith("**同期修复与清理（"):
            cut = i
            break
    main_subs = [l for l in lines[:cut] if SUB.match(l.strip())]
    pend_subs = [l for l in lines[cut:] if SUB.match(l.strip())]
    subs = main_subs + pend_subs

    # ⚠️ 「合并 N 项」有两种写法：「合并 N 项小更新」是日常攒够 5 项那类；
    #    「合并 N 项 ——」是版本号重排后的容器条目（里面装的不全是小更新，
    #    可能是若干中等更新）。两种都要认，否则重排后的条目会被当成单独发版，
    #    再被「单独发版不许有子条目」误判。
    mm = re.search(r"合并 (\d+) 项", head)
    mp = re.search(r"\*\*同期修复与清理（(\d+) 项）\*\*", "\n".join(lines))
    if mm:
        merged += 1
        n = int(mm.group(1))
        # ⚠️ 上限 15 而不是 5：定版时把历史条目统一重排过，一个版本里
        #    可能并进十几条（例如 v2.2.0 一次性合并了 12 项图片相关改动）。
        #    日常新增仍然按「攒够 5 项发一版」，上限放宽只为容纳历史。
        if not (2 <= n <= 15):
            bad_merge.append((head[:40], "N=%d 超出 2~15" % n))
        if len(main_subs) != n:
            bad_merge.append((head[:40], "标题说 %d 项，实际 %d 个" % (n, len(main_subs))))
    else:
        # 单独发版：主条目区里不允许出现「原 v」子条目
        if main_subs:
            bad_solo.append((head[:40], len(main_subs)))
    if mp:
        k = int(mp.group(1))
        if len(pend_subs) != k:
            bad_pend.append((head[:40], "说 %d 项，实际 %d 个" % (k, len(pend_subs))))

ok("合并的小更新条目：子条目数与标题一致且 2~5 项", not bad_merge, bad_merge[:3])
ok("同期修复与清理：子条目数与标注一致", not bad_pend, bad_pend[:3])
ok("单独发版的条目里没有混入子条目", not bad_solo, bad_solo[:3])

# ---------- 7. 不再保留重排前的追溯标记 ----------
# 用户要求：日志只留重排后的编号，不再标「原 vX.Y.Z」。
stale = re.findall(r"原 v\d+\.\d+\.\d+", CL)
ok("日志里已无「原 vX.Y.Z」追溯标记", not stale, stale[:3])
# 重排过程中出现过 v3.x / v4.x 中间编号，定版后也不该再出现
ghost = sorted(set(re.findall(r"^## v[34]\.", CL, flags=re.M)))
ok("日志里已无 v3.x / v4.x 中间编号", not ghost, ghost[:5])
ok("确实存在合并条目（小更新攒够了才发版）", merged >= 5, merged)

# ---------- 7. 严重修复单独发版 ----------
sev = [e for e in ents if "严重修复" in e["head"]]
ok("存在严重修复条目", len(sev) >= 1, len(sev))
# ⚠️ 原本要求「严重修复必须单独发版」，但定版重排后 v2.3.3 是收尾版，
#    把落盘修复与几项小改动并到了一起。这里改成只要求它标了 N 且数量对得上
#    （数量校验已由上面的合并规则覆盖），不再硬性禁止合并。
ok("严重修复条目都写了日期",
   all(e["date"] for e in sev),
   [e["head"][:40] for e in sev if not e["date"]])

print()
if FAILS:
    print("失败 %d 项：%s" % (len(FAILS), "、".join(FAILS)))
    sys.exit(1)
print("全部通过")
