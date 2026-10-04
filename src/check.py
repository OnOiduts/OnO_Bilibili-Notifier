#!/usr/bin/env python3
# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""命令行自检：一步步告诉你哪里没配好。

用法：python check.py
"""
import asyncio
import os
import sys
from cfgutil import safe_print, safe_stdio
import paths
safe_stdio()


BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

OK, BAD, WARN = "✅", "❌", "⚠️ "


def say(icon, text):
    safe_print(f"{icon} {text}")


def do_unban():
    """解封被自己限流误锁的本机 IP。"""
    import harden
    bans = harden.GUARD.ban_list()
    if not bans:
        print("当前没有被封禁的 IP，无需解封。")
        return 0
    print("当前封禁列表：")
    for b in bans:
        print(f"  {b['ip']}  剩余 {b['left']} 秒")
    harden.GUARD.unban_all()
    print("\n已全部解封。回到面板刷新即可正常使用。")
    return 0


DEFAULT_UID = 259149243      # 没订阅任何 UP 时，用它来测 B 站接口


def load_db():
    """读出已登记的群和 UP 主。失败返回空，不阻断自检。"""
    import os
    from db import Store, data_path_from_cfg
    # ⚠️ 必须跟面板/机器人走同一个入口：用户改过存放位置后，
    #    paths.state_path()（默认路径）不是实际数据文件，自检会读空。
    path = data_path_from_cfg()
    if not os.path.exists(path):
        return None, [], []
    try:
        st = Store(path)
        st.refresh()
        return st, st.all_groups(), st.all_ups()
    except Exception as e:
        say(WARN, f"读取 data.db 失败：{e}")
        return None, [], []


def check_dirty(st):
    """扫描订阅数据里的脏值（本该是 dict 却存成了字符串之类）。

    ⚠️ 为什么要专门扫这一项：
    这类脏数据在轮询里会抛 'str' object has no attribute 'get'，
    而且异常会一路冒到轮询主循环，导致**整轮检测停摆**——
    所有 UP 主的直播/投稿/动态都不再检测，日志里却只有一句话，
    看不出是哪一位 UP 主、哪一步出的问题。

    代码里已经加了类型兜底（脏值当空处理），但那样对应的 UP 主或
    提醒类型会被**静默跳过**，用户看到的是"开了提醒却一直没提示"。
    所以自检里主动把它扫出来，告诉用户去面板重新勾一次。
    """
    if st is None:
        return None
    try:
        st.refresh()
        cache = getattr(st, "_cache", {}) or {}
        from db import KINDS
    except Exception as e:
        return (WARN, f"扫描订阅数据失败：{e}")
    bad_ups, bad_subs = [], []
    for uid, up in (cache.get("ups") or {}).items():
        if not isinstance(up, dict):
            bad_ups.append(str(uid))
    for gid, g in (cache.get("groups") or {}).items():
        if not isinstance(g, dict):
            bad_subs.append(f"{str(gid)[:8]}… 群记录整体异常")
            continue
        subs = g.get("subs")
        if not isinstance(subs, dict):
            bad_subs.append(f"{str(gid)[:8]}… 订阅表异常")
            continue
        for uid, item in subs.items():
            if not isinstance(item, dict):
                bad_subs.append(f"{str(gid)[:8]}…/{uid} 订阅项异常")
                continue
            for k in KINDS:
                if k in item and not isinstance(item[k], dict):
                    bad_subs.append(f"{str(gid)[:8]}…/{uid}/{k}")
    if not bad_ups and not bad_subs:
        return (OK, "订阅数据结构正常")
    lines = []
    if bad_ups:
        lines.append("UP 主缓存异常：" + "、".join(bad_ups[:5]))
    if bad_subs:
        lines.append("订阅项异常：" + "、".join(bad_subs[:8]))
    return (BAD, "订阅数据里有脏值（对应的 UP 主不会被检测）",
            "\n".join(lines)
            + "\n解决：面板「👥 群与订阅」里把这个 UP 主的提醒重新勾一次并保存"
            + "\n（现已兜底不会让整轮停摆，但脏的那几项确实检测不到）")


def check_port():
    """面板端口是否被占（占了会导致面板打不开）。"""
    import socket
    try:
        import webui
        port = int(getattr(webui, "DEFAULT_PORT", 8088))
    except Exception:
        port = 8088
    s = socket.socket()
    s.settimeout(1)
    try:
        busy = s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()
    if busy:
        say(OK, f"面板端口 {port} 已在监听（面板应该开着）")
    else:
        say(WARN, f"面板端口 {port} 没在监听")
        safe_print("    说明：机器人没启动，或面板还没起来")
    return port


def show_recent_errors():
    """把最近的错误从日志里摘出来，省得翻文件。"""
    import os
    log = paths.log_file("backend.log")
    if not os.path.exists(log):
        return
    try:
        with open(log, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return
    errs = [l.rstrip() for l in lines
            if ("[错误]" in l or "[警告]" in l or "ERROR" in l or "WARNING" in l)]
    if not errs:
        say(OK, "近期日志里没有错误/警告")
        return
    say(WARN, f"近期日志里有 {len(errs)} 条错误/警告（最后 5 条）：")
    for l in errs[-5:]:
        safe_print("    " + l[:110])


def do_check_seasons():
    """逐个探测已订阅的合集，找出取不到的（死 ID）。

    合集订阅以前不校验就写库，于是「填错 ID」「填成视频列表(series)的 ID」
    「UP 主把合集删了」都会静默存进去，之后每轮轮询对它报一次 404。
    这个命令一次把所有订阅的合集探测一遍，直接告诉你是哪个、为什么。
    """
    import asyncio
    from bilibili import BiliClient, SeasonGoneError
    from db import Store
    from cfgutil import load_cfg
    from security import redact

    cfg = load_cfg()
    st = Store()
    keys = st.all_season_keys()
    if not keys:
        say(OK, "当前没有任何开启的合集订阅")
        return 0
    pairs = sorted({(int(u), int(sid)) for _, u, sid in keys})
    say(WARN, f"共 {len(pairs)} 个合集订阅，逐个探测…")
    bad = 0

    async def probe(uid, sid):
        c = BiliClient(cookie=cfg.get("bili_cookie", ""))
        try:
            info = await c.get_season_archives(uid, sid, max_pages=1)
            return info
        finally:
            await c.close()

    for uid, sid in pairs:
        gids = [g for g, u, s in keys if int(u) == uid and int(s) == sid]
        try:
            info = asyncio.run(probe(uid, sid))
        except SeasonGoneError as e:
            bad += 1
            say(BAD, f"合集 {sid}（UP {uid}）取不到，订阅它的群：{len(gids)} 个")
            safe_print(f"    原因：{redact(str(e))[:160]}")
            safe_print("    处理：去面板删掉这条订阅，或用正确的合集链接重新添加")
        except Exception as e:
            bad += 1
            say(BAD, f"合集 {sid}（UP {uid}）探测出错：{redact(str(e))[:120]}")
            continue
        else:
            if not info or not info.videos:
                bad += 1
                say(BAD, f"合集 {sid}（UP {uid}）返回空内容")
            else:
                say(OK, f"合集 {sid}「{info.title or '未命名'}」正常，"
                        f"{info.total} 个视频")

    safe_print()
    if bad:
        say(WARN, f"{bad}/{len(pairs)} 个合集有问题，按上面的提示处理即可")
    else:
        say(OK, "所有合集都能正常取到")
    return 0


def main():
    import version as _version
    safe_print("=" * 60)
    safe_print(f" OnOB站通知订阅工具 · 自检　v{_version.current()}")
    safe_print("=" * 60)
    bad_count = [0]

    def bad(icon, text, fix=""):
        if icon == BAD:
            bad_count[0] += 1
        say(icon, text)
        if fix:
            for line in fix.split("\n"):
                safe_print("    " + line)

    # 1. Python
    v = sys.version_info
    bad(OK if v >= (3, 8) else BAD, f"Python {v.major}.{v.minor}.{v.micro}")

    # 2. 依赖
    missing = []
    for mod, pkg in (("botpy", "qq-botpy"), ("aiohttp", "aiohttp"),
                     ("yaml", "PyYAML"), ("flask", "Flask")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        bad(BAD, "缺少依赖：" + "、".join(missing),
            "解决：pip install -r requirements.txt")
    else:
        say(OK, "依赖已安装")

    # 3. 配置文件
    cfg_path = paths.config_path()
    if not os.path.exists(cfg_path):
        bad(BAD, "没有 config.yaml",
            "解决：把 config.example.yaml 复制一份改名 config.yaml，再去网页面板填凭据")
        return
    import yaml
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    say(OK, "找到 config.yaml")

    from cfgutil import creds_ready

    appid, secret = str(cfg.get("appid", "")), str(cfg.get("secret", ""))
    if not creds_ready(cfg):
        bad(BAD, "AppID / AppSecret 还没填好",
            "当前值：" + (f"appid={appid!r}" if appid else "appid 为空")
            + (f" secret={secret[:6]}…" if secret else " secret 为空")
            + "\n解决：打开网页面板 →「⚙️ 设置」→ 填入后保存"
            + "\nAppID 必须是纯数字；两个值都不能是模板里的 xxxxxx")
    else:
        say(OK, f"AppID {appid}（Secret 长度 {len(secret)}）")

    # 4. QQ 凭据
    if creds_ready(cfg):
        asyncio.run(check_qq(appid, secret, bool(cfg.get("is_sandbox"))))

    # 5. 数据库 / 已登记的群
    safe_print()
    safe_print("-" * 60)
    safe_print(" 数据")
    safe_print("-" * 60)
    st, groups, ups = load_db()
    if st is None:
        say(WARN, "还没有 data.db（机器人首次启动后会生成）")
    elif not groups:
        say(WARN, "数据库里还没有任何群",
            "解决：把机器人拉进群，在群里 @ 一下机器人（会自动登记，但不会回复）")
    else:
        say(OK, f"已登记 {len(groups)} 个群、{len(ups)} 位 UP 主")
        for gid in groups:
            name = (st._cache["groups"].get(gid) or {}).get("name") or "未命名"
            n = len(st.ups_of_group(gid))
            safe_print(f"    · {name}  {gid[:20]}…  {n} 个 UP 主")

    # 5.5 订阅数据里的脏值
    if st is not None:
        r = check_dirty(st)
        if r:
            bad(*r)

    # 6. B 站接口（自动测，不交互）
    safe_print()
    safe_print("-" * 60)
    safe_print(" B 站接口")
    safe_print("-" * 60)
    uid_arg = ""
    for a in sys.argv[1:]:
        if a.isdigit():
            uid_arg = a
    if uid_arg:
        targets = [int(uid_arg)]
    else:
        targets = [int(u) for u in ups if str(u).isdigit()][:3] or [DEFAULT_UID]
        if not any(str(u).isdigit() for u in ups):
            safe_print(f"（没订阅任何 UP 主，用默认 UID {DEFAULT_UID} 测试）")
    for uid in targets:
        asyncio.run(check_bili(uid, cfg.get("bili_cookie", "")))

    # 7. B 站 cookie
    try:
        import bili_login
        cookie = str(cfg.get("bili_cookie") or "")
        if not cookie:
            bad(WARN, "B 站 cookie：未登录（投稿/动态接口可能报 -352 风控）",
                "解决：面板「🍮 B站登录」→ 打开浏览器登录")
        else:
            exp = float(cfg.get("bili_cookie_expires") or 0)
            info = bili_login.expired_info(exp) if exp else {"text": "未知", "expired": False, "soon": False}
            if info.get("expired"):
                bad(BAD, "B 站 cookie 已过期，请重新登录或续期",
                    "解决：面板「🍮 B站登录」→ 重新登录")
            elif info.get("soon"):
                say(WARN, f"B 站 cookie 即将过期（{info['text']}），建议续期")
            else:
                say(OK, f"B 站 cookie 有效（{info['text']}）")
    except Exception as e:
        say(WARN, f"B 站 cookie 检查跳过：{e}")

    # 8. 面板端口
    safe_print()
    safe_print("-" * 60)
    safe_print(" 网页面板")
    safe_print("-" * 60)
    check_port()

    # 9. 安全体检
    safe_print()
    safe_print("-" * 60)
    safe_print(" 安全体检")
    safe_print("-" * 60)
    try:
        import security
        for level, name, desc in security.security_report():
            icon = OK if level == "ok" else (WARN if level == "warn" else BAD)
            if icon == BAD:
                bad_count[0] += 1
            say(icon, f"{name}：{desc}")
    except Exception as e:
        say(WARN, f"安全体检跳过：{e}")

    # 10. 近期错误
    safe_print()
    safe_print("-" * 60)
    safe_print(" 近期日志")
    safe_print("-" * 60)
    show_recent_errors()

    safe_print()
    safe_print("=" * 60)
    if bad_count[0]:
        safe_print(f" 自检结束：有 {bad_count[0]} 项需要解决（带 ❌ 的按提示处理）")
    else:
        safe_print(" 自检结束：没有发现必须解决的问题 ✅")
    safe_print("=" * 60)


async def check_qq(appid, secret, sandbox):
    try:
        from qqapi import QQClient
        qq = QQClient(appid, secret, sandbox=sandbox)
        tok = await qq.get_token()
        await qq.close()
        say(OK, f"机器人凭据有效（token {tok[:10]}…）")
    except Exception as e:
        say(BAD, f"机器人凭据无效：{e}")
        safe_print("    常见原因：AppSecret 填错 / 机器人还没上线 / IP 白名单没配")


async def check_bili(uid: int, cookie: str):
    try:
        from bilibili import BiliClient, BiliError, live_status_cn
        c = BiliClient(cookie=cookie)
        up = await c.get_up(uid)
        if not up.uname:
            say(BAD, f"UID {uid} 查不到，确认一下是不是填错了")
            await c.close()
            return
        say(OK, f"UP 主：{up.uname}（UID {uid}）"
                + (f"，直播间 {up.room_id}" if up.room_id else "，没有直播间"))
        if up.room_id:
            try:
                live = await c.get_live(up.room_id)
                say(OK, f"直播接口正常：{live_status_cn(live.live_status)}")
            except BiliError as e:
                say(BAD, f"直播接口失败：{e}")
        for label, coro in (("投稿", c.get_latest_video(uid)),
                            ("动态", c.get_latest_dynamic(uid))):
            try:
                r = await coro
                if r:
                    title = getattr(r, "title", "") or getattr(r, "bvid", "") \
                        or getattr(r, "dyn_id", "")
                    say(OK, f"{label}接口正常：{title[:40]}")
                else:
                    say(WARN, f"{label}接口返回空（可能这个 UP 主没有{label}）")
            except BiliError as e:
                say(BAD, f"{label}接口失败：{e}")
                if label == "动态":
                    safe_print("    常见原因：需要 Cookie。在 config.yaml 的 bili_cookie 里填 SESSDATA")
        await c.close()
    except Exception as e:
        say(BAD, f"B 站接口异常：{e}")


USAGE = """用法：
  python check.py           全面自检
  python check.py 259149243 顺便测指定 UP 主的 B 站接口
  python check.py --seasons 探测所有订阅的合集，找出取不到的（死 ID）
  python check.py --unban   解封被限流误锁的本机 IP
"""

if __name__ == "__main__":
    if "--unban" in sys.argv:
        sys.exit(do_unban())
    if "--seasons" in sys.argv:
        sys.exit(do_check_seasons())
    if "-h" in sys.argv or "--help" in sys.argv:
        safe_print(USAGE)
        sys.exit(0)
    main()
