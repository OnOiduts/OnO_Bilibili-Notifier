# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""版本信息：读取 VERSION 与 CHANGELOG.md。

为什么要单独一个模块：
    面板、启动器、自检都要显示版本号，统一在这里读，避免各处硬编码不一致。
    另外用户反馈过「文件版本混用」的问题，启动器打印版本号能一眼看出是不是旧文件。

版本规则（用户自定义；历史版本号在定版时按此规则统一重排过，
    日志只保留重排后的编号，不标注重排前的版本号）：

    - 大更新   +1.0.0，单独发版
    - 中等更新 +0.1.0，单独发版
    - 小更新   攒够 5 项才 +0.0.1，这 5 项合并成一个版本
    - 严重修复 +0.1.0，版本号后面加 fix（如 v2.3.4 → v2.4.0fix），单独发版
    - 一般修复 / 删除  不单独发版，攒到下一个版本里一起写入
    - 没做完 / 还在修的功能  不涨号；只有实测确认没问题后才计入版本。
      版本号只代表「已经验过的东西」，避免出现「版本号涨了但功能还是坏的」。

为什么要这么定：改动太碎会让版本号一天涨好几次，用户根本记不住自己装的是
哪一版。把「小改动」攒起来，一个版本对应一次值得说清的变化。
"""
import os
import re
import paths

BASE = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = paths.asset("VERSION")
CHANGELOG_FILE = os.path.join(paths.docs_dir(), "CHANGELOG.md")

FALLBACK = "0.0.0"

# 解压后的顶层目录名 / 压缩包名格式。
# ⚠️ 顶层目录**固定**不跟源码目录名走：以前跟着叫 bili-notify，
#    用户覆盖解压时还得自己改名；固定成 OnO_Bilibili-Notifier 之后，
#    每次解压出的目录名都一样，整目录覆盖即可。
PACK_TOPDIR = "OnO_Bilibili-Notifier"
# 压缩包名：OnO_Bilibili-Notifier[v2.3.4].zip（方括号，用户指定）
PACK_NAME_FMT = "%s[v%%s]%%s.zip" % PACK_TOPDIR


def current() -> str:
    """当前版本号，如 0.6.0。"""
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            v = f.read().strip().splitlines()
        return (v[0].strip() if v else FALLBACK) or FALLBACK
    except OSError:
        return FALLBACK


def bump(version: str, level: str) -> str:
    """按级别递增版本号。level: patch(0.0.1) / minor(0.1.0) / major(1.0.0)。"""
    parts = re.findall(r"\d+", version or "")
    nums = [int(p) for p in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    major, minor, patch = nums
    if level == "major":
        major, minor, patch = major + 1, 0, 0
    elif level == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


def changelog() -> list:
    """解析 CHANGELOG.md，返回 [{version, date, level, items}, ...]，最新在前。"""
    try:
        with open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []

    entries = []
    # 按 "## " 分节
    chunks = re.split(r"^## ", text, flags=re.M)[1:]
    for c in chunks:
        lines = c.strip().splitlines()
        if not lines:
            continue
        head = lines[0].strip()
        # 形如：v0.6.0（2026-09-19）中等更新
        m = re.match(r"^(v?[\d.]+)", head)
        if not m:
            continue
        ver = m.group(1).lstrip("v")
        # ⚠️ 日期要带时分秒：标题写作「## v1.31.16（2026-09-21 20:30:15）修：…」。
        # 时分秒是可选的 —— 早期条目只写了年月日，没有就不倒填假时间，
        # 前端有 time 才显示，没有只显示日期。
        dm = re.search(
            r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}(?::\d{2})?))?", head)
        date = dm.group(1) if dm else ""
        time = dm.group(2) if (dm and dm.group(2)) else ""
        level = "patch"
        if "大更新" in head:
            level = "major"
        elif "中等更新" in head or "中更新" in head:
            level = "minor"

        # 一句话摘要：标题里「小更新：xxx」冒号后面那段。
        # ⚠️ 以前面板只显示「v1.98.6 小更新」，光看标题不知道这次改了啥，
        #    得展开才能看见内容。摘要单独拎出来，一眼就知道值不值得展开。
        #
        # ⚠️ 等级词要带上「严重修复」：立了维护政策之后，改动只剩
        #    「功能更新」和「严重 bug 修复」两类，后者正是用这个词起头的。
        #    漏了它，这类条目的摘要会解析成空 —— 面板上又变成光秃秃
        #    一个标题，看不出这次改了什么（数据落盘那版踩到）。
        # ⚠️ 等级词要覆盖重排后的全部写法：重排后小更新会合并成
        #    「小更新：合并 N 项小更新 —— …」，修复/清理则并入「同期修复与清理」。
        #    漏一个词，这类条目的摘要就解析成空。
        sm = re.search(
            r"(?:大更新|中等更新|中更新|小更新|严重修复|紧急修复"
            r"|补丁更新|修复|清理|首个版本)\s*[：:]\s*(.+)$",
            head)
        summary = sm.group(1).strip() if sm else ""

        # 条目带 group： Markdown 里的 **小标题** 作为分组名
        items, cur, group = [], None, ""
        for ln in lines[1:]:
            st = ln.rstrip()
            stripped = st.strip()
            # 支持三种写法：- / * / 1. 2. 3.
            if re.match(r"^[-*] ", stripped) or re.match(r"^\d+[.)]\s", stripped):
                if cur:
                    items.append({"text": cur, "group": group})
                cur = re.sub(r"^(?:[-*]|\d+[.)])\s+", "", stripped).strip()
            elif stripped.startswith("**") and stripped.endswith("**"):
                # 小标题，如 **界面重做** → 之后条目的分组名
                if cur:
                    items.append({"text": cur, "group": group})
                cur = None
                group = stripped.strip("*").strip()
            elif cur and st.startswith(("  ", "\t")):
                cur += " " + stripped
        if cur:
            items.append({"text": cur, "group": group})

        # 整节没有任何列表（全是段落）时，用段落本身当条目，
        # 否则面板里会显示成一个空版本。
        if not items:
            buf = []
            for ln in lines[1:]:
                t = ln.strip()
                if not t or t.startswith(("|", "```", "---")):
                    continue
                if t.startswith("###"):
                    if buf:
                        items.append({"text": " ".join(buf), "group": group})
                        buf = []
                    group = t.lstrip("#").strip()
                    continue
                buf.append(t)
            if buf:
                items.append({"text": " ".join(buf), "group": group})

        # 顺带给出分组顺序，前端好按顺序渲染
        groups = []
        for it in items:
            if it["group"] and it["group"] not in groups:
                groups.append(it["group"])

        entries.append({"version": ver, "date": date, "time": time,
                        "level": level, "summary": summary,
                        "title": head, "items": items, "groups": groups})
    return entries


def latest(n: int = 3) -> list:
    return changelog()[:n]


if __name__ == "__main__":
    print("当前版本:", current())
    print()
    for e in changelog()[:3]:
        print(f"## v{e['version']}（{e['date']}）")
        for it in e["items"][:5]:
            print(f"  - {it}")
        print()
