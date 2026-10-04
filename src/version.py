# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""版本信息：读取 VERSION 与 CHANGELOG.md。

为什么要单独一个模块：
    面板、启动器、自检都要显示版本号，统一在这里读，避免各处硬编码不一致。
    另外用户反馈过「文件版本混用」的问题，启动器打印版本号能一眼看出是不是旧文件。

版本规则（用户自定义）：
    小更新 +0.0.1，中等更新 +0.1.0，大更新 +1.0.0
"""
import os
import re
import paths

BASE = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = paths.asset("VERSION")
CHANGELOG_FILE = os.path.join(paths.docs_dir(), "CHANGELOG.md")

FALLBACK = "0.0.0"


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
        elif "中等更新" in head:
            level = "minor"

        # 一句话摘要：标题里「小更新：xxx」冒号后面那段。
        # ⚠️ 以前面板只显示「v1.98.6 小更新」，光看标题不知道这次改了啥，
        #    得展开才能看见内容。摘要单独拎出来，一眼就知道值不值得展开。
        sm = re.search(r"(?:大更新|中等更新|小更新)\s*[：:]\s*(.+)$", head)
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
