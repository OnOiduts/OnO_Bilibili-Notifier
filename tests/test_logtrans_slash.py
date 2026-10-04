# -*- coding: utf-8 -*-
"""日志翻译 / 进程登记的锁定测试。

对应用户实测反馈：
  · 「运行日志翻译功能怎么没用啊」——看截图里的行全是半中半英：
      测试推送 dynamic/video/offline/live
      (机器人主程序)__init__  日志级别已更新：DEBUG
    ⚠️ 真因有两个，都是**替换前界把斜杠排除了**：
      ① 推送类型是斜杠连写的（dynamic/video/...），而 KIND_WORDS 沿用了
         通用词表的前界 `(?<![A-Za-z0-9._/-])` —— 斜杠也在排除集里，
         于是 video / live / dynamic 前面是斜杠，**一个都匹配不上**；
         后界 `(?![\w./-])` 同样排除了斜杠，每个词后面跟着的也是斜杠。
         前后都被卡死，整行自然翻不出来。
      ② __init__ / api_xxx 这类最常见的名字根本没登记在 SYMBOL_NAMES 里，
         所以文件名翻了、方法名还是英文。
    ⚠️ 另外：那几条日志本来就没有 meaning（INFO_RULES 里没有对应规则），
       而前端只在 i.meaning 有值时才显示解释行 —— 用户看到"翻译没生效"。

  · 「进程关闭又杀不干净」——面板自己不登记进程号，见 test_webui_registers_pid。

这些用例把上面每一条都钉死，改回去就红。
"""
import io
import os
import sys

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE)

FAIL = []


def ck(name, cond, extra=""):
    if cond:
        print("  [OK] " + name)
    else:
        print("  [FAIL] " + name + ((" -> " + str(extra)) if extra else ""))
        FAIL.append(name)
    return cond


def _src(name):
    with io.open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()


def t_slash_kinds():
    """斜杠连写的推送类型必须**每个词**都翻成中文。"""
    from logtranslate import translate_symbols
    out = translate_symbols("测试推送 dynamic/video/offline/live")
    print("[1] 斜杠连写的推送类型全翻")
    for cn in ("动态", "投稿", "下播", "开播"):
        ck("翻出「%s」" % cn, cn in out, out)
    for en in ("dynamic", "video", "offline", "live"):
        ck("不再残留 %s" % en, en not in out.split("测试推送")[-1], out)

    out2 = translate_symbols("测试推送 season/top_comment/dynamic_pinned")
    print("[2] 长词优先（dynamic_pinned 不被 dynamic 拆坏）")
    ck("置顶动态", "置顶动态" in out2, out2)
    ck("置顶评论", "置顶评论" in out2, out2)
    ck("合集", "合集" in out2, out2)
    ck("没有拆坏的 动态_pinned", "动态_pinned" not in out2, out2)


def t_domain_not_hurt():
    """放行斜杠后仍不能误伤域名 / 路径。"""
    from logtranslate import translate_symbols
    print("[3] 域名与路径不被误翻")
    for u in ("https://live.bilibili.com/12345",
              "live.bilibili.com/video/av1",
              "https://i0.hdslb.com/bfs/live/abc.jpg"):
        ck("保持原样：" + u, translate_symbols(u) == u, translate_symbols(u))


def t_symbol_names():
    """最常见的 __init__ / api_xxx 必须登记，否则半中半英。"""
    from logtranslate import translate_symbols
    print("[4] 方法名登记")
    o1 = translate_symbols("[INFO]  (bot.py:200)__init__    日志级别已更新：DEBUG")
    ck("__init__ → 初始化", "初始化" in o1, o1)
    ck("不再残留 __init__", "__init__" not in o1, o1)
    ck("级别 DEBUG → 调试", "调试" in o1, o1)

    o2 = translate_symbols("[INFO] (webui.py:1200)api_save 127.0.0.1 修改配置")
    ck("api_save → 保存配置", "保存配置" in o2, o2)
    ck("不再残留 api_save", "api_save" not in o2, o2)


def t_info_meaning():
    """面板上最常见的几条日志必须有 meaning，否则条目下没有解释行。"""
    from logtranslate import explain
    print("[5] 常见日志有中文解释")
    cases = [
        ("日志级别已更新：DEBUG", "日志级别"),
        ("127.0.0.1 修改配置 凭据/口令/模板等", "修改配置"),
        ("测试推送 dynamic/video/offline/live", "测试推送"),
        ("[*] Checking for leftover processes...", "清理残留进程"),
    ]
    for line, kw in cases:
        m = explain(line).get("meaning") or ""
        ck("%s 有解释" % kw, bool(m.strip()), repr(m))


def t_webui_registers_pid():
    """面板必须登记自己的进程号，否则独立启动时清理不到它。"""
    print("[6] 面板登记进程号")
    src = _src("webui.py")
    ck("webui.py 调用 register_pid", "register_pid()" in src)
    ck("webui.py 导入 cleanup", "import cleanup" in src)
    ck("有 atexit 注销", "atexit.register" in src and "unregister_pid" in src)
    ck("atexit 已导入", "import atexit" in src)


def t_pids_file_only_alive():
    """PID 文件重写时只保留**确认活着**的进程。"""
    print("[7] PID 文件只留活着的")
    src = _src("cleanup.py")
    # ⚠️ 重写处原来是 `alive = [p for p in all_ours() ...]` —— 等于把
    #    "扫全机 python 进程 + netstat 查端口"整套再跑一遍，而 kill_ours
    #    开头刚跑过；启动又调三次 kill_ours，六趟全机扫描就是这么来的。
    #    现在改成基于 still（已经过存活判定的），语义不变但零额外开销。
    i = src.find("alive = [p for p in still")
    ck("重写处存在", i > 0)
    if i > 0:
        seg = src[i:i + 200]
        ck("不再全量重扫", "all_ours()" not in seg, seg)
    j = src.find("still = [p for p in pids")
    ck("只留确认活着的",
       j > 0 and "_alive_set" in src[j:j + 80], src[j:j + 80] if j > 0 else "")


def main():
    print("=" * 60)
    print("日志翻译 / 进程登记 锁定测试")
    print("=" * 60)
    t_slash_kinds()
    t_domain_not_hurt()
    t_symbol_names()
    t_info_meaning()
    t_webui_registers_pid()
    t_pids_file_only_alive()
    print("-" * 60)
    if FAIL:
        print("结果: 存在失败 ❌（%d 项）" % len(FAIL))
        for x in FAIL:
            print("   - " + x)
        return 1
    print("结果: 全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
