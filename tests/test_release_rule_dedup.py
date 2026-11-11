# -*- coding: utf-8 -*-
"""锁定发布规则：三类计数器各自独立累加、先到先发；更新日志必须去重。

用户最终澄清的规则（之前被我误读成「统一攒够 10 项」）：

    - 小更新        攒够 5 项  才 +0.0.1，这 5 项合并成一个版本
    - bug 修复/删除 攒够 10 项 才 +0.0.1，这 10 项合并成一个版本
                    （修复与功能删除共用这一个计数器）
    - 两类合并      加在一起超过 12 项，也 +0.0.1
                    （两类各自都没攒够，但加起来够多，同样发一版）
    - 日志去重      同一个 bug 反复修了好几轮，日志只保留首次那条，后续修复
                    并入它（正文里补一句现状或最终结论），不新增条目

为什么要锁：同一个毛病修十次就写十条，日志会被一串相似条目淹没，看的人
分不清哪一条才是最终结果。这次专门把「日志去重」也做成可检查的 —— 同一
版本内如果出现重复或高度相似的条目标题，就是又犯了老毛病。

这份测试守两件事：

    1. 三档阈值都写进了三处文档（根 README.md / docs/README.md /
       src/version.py），且没有互相矛盾的旧阈值残留
    2. CHANGELOG 里同一版本内没有重复 / 高度相似的条目

⚠️ 反向验证做过（不是看代码，是撤掉改动重跑）：
    小更新阈值改回 10 项      → 变红
    修复阈值改成 5 项         → 变红
    删掉「超过 12 项」        → 变红
    删掉「同一个问题只记一条」 → 三处文档检查变红
    往日志里插一条重复标题     → 「同版本内无重复条目」变红
    把严重修复举例改回 v2.4.0fix → 「举例与当前版本一致」变红
"""
import io
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DOCS = ("README.md", "docs/README.md")
VERDOC = "src/version.py"
CL_PATH = os.path.join(BASE, "docs", "CHANGELOG.md")

FAILS = []


def ok(name, cond, extra=""):
    if cond:
        print("  [OK] " + name)
    else:
        print("  [FAIL] " + name + (" → " + str(extra) if extra else ""))
        FAILS.append(name)


def read(p):
    try:
        return io.open(os.path.join(BASE, p), encoding="utf-8").read()
    except OSError:
        return ""


print("== 发布规则：小更新 5 项 / 修复删除 10 项 / 合并超 12 项 + 日志去重 ==")

# ---------- 1. 三处文档都写全了三档阈值 ----------
# ⚠️ 不能只判「字符串出现过」：旧阈值若没删干净，文档里会同时存在两条互相
#    矛盾的阈值。所以按「表格行/条目行」定位，确认每个类型挂的是正确的数字。


def row_of(s, key):
    """取出表格里含 key 的那一行。"""
    for l in s.splitlines():
        if key in l and l.strip().startswith("|"):
            return l
    return ""


def line_of(s, key):
    """取出注释/列表里含 key 的那一行。"""
    for l in s.splitlines():
        if key in l:
            return l
    return ""


for p in DOCS:
    s = read(p)
    r_minor = row_of(s, "小更新")
    r_fix = row_of(s, "bug 修复")
    r_both = row_of(s, "上面两类合并")
    ok("%s 小更新挂的是 5 项" % p, "**5 项**" in r_minor, r_minor[:60])
    ok("%s 修复/删除挂的是 10 项" % p, "**10 项**" in r_fix, r_fix[:60])
    ok("%s 合并档写的是超过 12 项" % p, "超过 12 项" in r_both, r_both[:60])
    ok("%s 三档阈值都出现了" % p,
       ("5" in r_minor) and ("10" in r_fix) and ("12" in r_both))

v = read(VERDOC)
ok("version.py 小更新 5 项", "攒够 5 项" in line_of(v, "小更新"))
ok("version.py 修复/删除 10 项", "攒够 10 项" in line_of(v, "修复/删除"))
ok("version.py 合并超过 12 项", "超过 12 项" in v)

# ---------- 2. 三处文档都写了日志去重 ----------
for p in DOCS:
    s = read(p)
    ok("%s 写了「同一个问题只记一条」" % p, "同一个问题只记一条" in s)
    ok("%s 写了不去重" % p, "不新增条目" in s)

ok("version.py 写了日志去重", "日志去重" in v and "不新增条目" in v)

# ---------- 3. 严重修复「只加 fix、不涨号」，且举例跟当前版本一致 ----------
# ⚠️ 这条之前改过一次却没落进文档（文档里仍写着 +0.1.0 → v2.4.0fix），
#    和「严重修复不该让人以为出了新功能」的初衷相反，一并订正并锁住。
for p in DOCS:
    s = read(p)
    ok("%s 严重修复不再 +0.1.0" % p,
       "+0.1.0`，版本号后面加 `fix`" not in s and "版本号**不涨**" in s)

cur = read("src/VERSION").strip()
for p in DOCS:
    s = read(p)
    # 举例形如：当前 v2.3.4 → 下一个严重修复 v2.3.4fix（三位数字不动）
    ok("%s 严重修复举例与当前版本一致" % p,
       ("`v%sfix`" % cur) in s and ("v%sfix2" % cur) in s,
       "当前版本 v%s" % cur)

# ---------- 4. 日志去重：同一版本内不得有重复 / 高度相似条目 ----------
CL = read("docs/CHANGELOG.md")
_LV = (r"(?:大更新|中等更新|中更新|小更新|严重修复|紧急修复|"
       r"紧急更新|清理|首个版本|补丁更新|补丁|"
       r"大修|中修|小修|修复|修)")
SUB = re.compile(r"^\*\*" + _LV + r"[：:](.+?)\*\*\s*$")


def norm(t):
    # 去掉括号里的补充说明与所有标点，只留中英文数字再比
    t = re.sub(r"[（(].*?[)）]", "", t)
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", t)


dup, sim = [], []
for c in re.split(r"^## ", CL, flags=re.M)[1:]:
    lines = c.strip().splitlines()
    if not lines:
        continue
    head = lines[0]
    ts = []
    for l in lines[1:]:
        m = SUB.match(l.strip())
        if m:
            ts.append(m.group(1).strip())
    ns = [norm(t) for t in ts]
    seen = {}
    for i, t in enumerate(ns):
        if t in seen:
            dup.append((head[:30], ts[seen[t]], ts[i]))
        seen.setdefault(t, i)
    for i in range(len(ns)):
        for j in range(i + 1, len(ns)):
            a, b = ns[i], ns[j]
            if not a or not b:
                continue
            r = len(set(a) & set(b)) / len(set(a) | set(b))
            # ⚠️ 0.75 是比过一遍历史日志定下来的：71 条里最高相似度也没到 0.75，
            #    定这个阈值不会误伤正常条目，又能抓住「换个说法再写一遍」。
            if r >= 0.75:
                sim.append((round(r, 2), head[:24], ts[i][:26], ts[j][:26]))

ok("同一版本内没有重复条目", not dup, dup[:3])
ok("同一版本内没有高度相似条目（≥0.75）", not sim, sim[:3])

print()
if FAILS:
    print("失败 %d 项：%s" % (len(FAILS), "、".join(FAILS)))
else:
    print("ALL PASS")
