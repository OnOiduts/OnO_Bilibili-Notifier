# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""B 站登录态：拿到并维护 cookie，解决接口 -352 风控。

为什么需要它：
    B 站的投稿 / 动态接口现在基本强制要求登录态。没带有效 cookie 时，
    接口会返回 code=-352（风控校验失败），表现为「查询 UP 主失败」。
    手动去浏览器复制 SESSDATA 对小白不友好，所以这里做了浏览器登录。

流程（唯一方式：浏览器登录，playwright）：
    1. 打开一个真实浏览器窗口，用户在里面扫码或账号密码登录
    2. 登录成功后自动读取 cookie 并保存
    3. GET x/web-interface/nav  校验登录态 + 取用户信息

⚠️ 曾经有过「扫码登录」（二维码）方式，已彻底移除：
    B 站的 x/passport-login/web/qrcode/poll 接口经常被风控，
    返回的是一串残缺 cookie —— 存进去后面板显示「已登录」，
    实际接口一直 -352，极难排查。浏览器登录成功率最高，统一走它。
    相关的 qrcode / Pillow 依赖也一并从依赖清单里去掉了。

cookie 时效：
    SESSDATA 默认有效期约 1 个月，这里会记录获取时间和过期时间，
    面板上能看到「还剩多少天」，过期前还能一键续期。

命令行直接用：python bili_login.py
"""
import asyncio
import os
import re
import sys
from cfgutil import safe_print
import time
from typing import Dict, Optional, Tuple

import aiohttp

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
PASSPORT = "https://passport.bilibili.com"
NAV = "https://api.bilibili.com/x/web-interface/nav"
REFRESH = PASSPORT + "/x/passport-login/web/cookie/refresh"

DEFAULT_TTL_DAYS = 30      # 拿不到明确过期时间时的保守估计


def _headers(referer: str = "https://www.bilibili.com/") -> dict:
    return {
        "User-Agent": UA,
        "Referer": referer,
        "Origin": PASSPORT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }


def cookie_str(cookies: Dict[str, str]) -> str:
    """拼成能塞进 Cookie 头的字符串，敏感字段按重要性排序。"""
    order = ["DedeUserID", "DedeUserID__ckMd5", "SESSDATA", "bili_jct",
             "buvid3", "buvid4", "sid"]
    parts = []
    for k in order:
        if cookies.get(k):
            parts.append(f"{k}={cookies[k]}")
    for k, v in cookies.items():
        if k not in order and v:
            parts.append(f"{k}={v}")
    return "; ".join(parts)


def parse_cookie(text: str) -> Dict[str, str]:
    out = {}
    for seg in (text or "").split(";"):
        seg = seg.strip()
        if "=" in seg:
            k, v = seg.split("=", 1)
            k, v = k.strip(), v.strip()
            if k:
                out[k] = v
    return out


def expired_info(expires: float, now: float = None) -> dict:
    """把过期时间戳变成人话。"""
    now = now or time.time()
    left = expires - now
    days = int(left // 86400)
    return {
        "expires_at": expires,
        "expires_str": time.strftime("%Y-%m-%d %H:%M", time.localtime(expires)),
        "days_left": days,
        "expired": left <= 0,
        "soon": 0 < left < 3 * 86400,     # 3 天内算快过期
        "text": ("已过期" if left <= 0 else
                 f"还剩 {days} 天" if days > 0 else
                 f"还剩 {int(left // 3600)} 小时"),
    }


async def account_info(cookie: str) -> dict:
    """拿登录账号的详细信息 —— 相当于「打开账号主页看一眼」。

    用户扫完码最想知道的就是"到底登上的是不是我这个号"，
    所以这里把昵称、UID、等级、大会员状态都取回来显示。
    """
    if not (cookie or "").strip():
        return {"ok": False, "msg": "没有 cookie"}
    headers = _headers()
    headers["Cookie"] = cookie.strip()
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.get(NAV, timeout=10) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        return {"ok": False, "msg": f"网络错误：{e}"}
    if data.get("code") != 0:
        return {"ok": False, "msg": f"查询失败（code={data.get('code')}）"}
    d = data.get("data") or {}
    if not d.get("isLogin"):
        return {"ok": False, "msg": "cookie 无效或未登录"}
    vip = d.get("vip") or {}
    level = d.get("level_info") or {}
    pendant = d.get("pendant") or {}
    # 头像框：B 站官方返回的图片，装扮用户才有。用 <img onerror> 兜底，
    # 没有也不会显示破图。
    return {
        "ok": True,
        "uname": d.get("uname", ""),
        "mid": d.get("mid", 0),
        "face": d.get("face", ""),
        "pendant": pendant.get("image") or "",
        "level": int(level.get("current_level") or 0),
        "level_exp": int(level.get("current_exp") or 0),
        "vip": int(vip.get("status") or 0) == 1,
        "vip_type": int(vip.get("type") or 0),      # 1=月度 2=年度
        "vip_label": (vip.get("label") or {}).get("text", "") if vip else "",
        "vip_due": vip.get("due_date") or 0,        # 会员到期（毫秒）
        "coins": d.get("money", 0),
        "is_login": True,
    }


async def refresh_cookie(cookie: str) -> dict:
    """续期：拿旧 cookie 换新 cookie，有效期重新算。"""
    c = parse_cookie(cookie)
    jct = c.get("bili_jct", "")
    if not c.get("SESSDATA") or not jct:
        return {"ok": False, "msg": "cookie 里缺少 SESSDATA / bili_jct，无法续期"}
    headers = _headers(PASSPORT + "/")
    headers["Cookie"] = cookie.strip()
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.post(REFRESH, params={"csrf": jct},
                              data={"csrf": jct}, timeout=15) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        return {"ok": False, "msg": f"网络错误：{e}"}
    if data.get("code") != 0:
        return {"ok": False, "msg": f"续期失败：{data.get('message')}"}
    new = {}
    for ck in s.cookie_jar:
        if ck.key:
            new[ck.key] = ck.value
    merged = dict(c)
    merged.update(new)
    if not merged.get("SESSDATA"):
        return {"ok": False, "msg": "续期后没拿到新的 SESSDATA"}
    return {"ok": True, "cookie": cookie_str(merged),
            "expires": time.time() + DEFAULT_TTL_DAYS * 86400}


def save_cookie(cookie: str, expires: float):
    """把 cookie 写进 config.yaml。"""
    import yaml
    base = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base, "config.yaml")
    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    cfg["bili_cookie"] = cookie
    cfg["bili_cookie_ts"] = time.time()
    cfg["bili_cookie_expires"] = expires
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    return cfg_path


def show_result(cookie: str, expires: float, acc: dict = None):
    """统一的成功展示：账号名 + 过期时间。"""
    info = expired_info(expires)
    acc = acc or {}
    safe_print("\n" + "=" * 58)
    if acc.get("ok"):
        safe_print(" ✅ 登录成功")
        safe_print("=" * 58)
        safe_print(f"   账号昵称：{acc.get('uname','')}")
        safe_print(f"   UID     ：{acc.get('mid','')}")
        safe_print(f"   等级    ：LV{acc.get('level',0)}")
        if acc.get("vip"):
            safe_print(f"   会员    ：{acc.get('vip_label') or '大会员'}")
        safe_print(f"   硬币    ：{acc.get('coins', 0)}")
    else:
        safe_print(" ✅ 已获取 cookie")
        safe_print("=" * 58)
        safe_print("   （账号信息查询失败，cookie 仍会保存，接口应该可用）")
    safe_print(f"\n   过期时间：{info['expires_str']}（{info['text']}）")


async def cmd_browser_login():
    """方式二（推荐）：打开浏览器登录，自动读 cookie。"""
    safe_print("=" * 58)
    safe_print(" B 站 Cookie 获取（浏览器方式）")
    safe_print("=" * 58)
    safe_print()
    safe_print(" 会打开一个浏览器窗口，请在里面扫码或账号密码登录 B 站。")
    safe_print(" 登录成功后会自动读取 cookie 并保存，全程不用你手动复制。")
    safe_print()

    try:
        import bili_browser_login as BBL
    except ImportError:
        safe_print("[失败] bili_browser_login.py 缺失")
        return 1

    try:
        res = await BBL.browser_login(timeout=300,
                                      on_stage=lambda m: print(f"   {m}"))
    except BBL.BrowserLoginError as e:
        safe_print(f"\n[失败] {e}")
        safe_print()
        safe_print(" 备用方案：")
        safe_print("   1. 在浏览器登录 bilibili.com，按 F12 → Network →")
        safe_print("      随便点一个请求，复制 Request Headers 里的 Cookie 整行")
        safe_print("   2. 粘进面板「🔑 B 站登录」的手动粘贴框")
        return 1
    except Exception as e:
        safe_print(f"\n[失败] {type(e).__name__}: {e}")
        return 1

    cs = res["cookie_str"]
    exp = res["expires"]
    acc = res.get("account") or {}
    if not acc.get("ok"):
        acc = await account_info(cs) or acc

    show_result(cs, exp, acc)
    path = save_cookie(cs, exp)
    safe_print(f"\n   已写入 {path}")
    safe_print("\n机器人会自动读取，不需要重启。")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    # 只有浏览器登录一条路（--browser / -b 与默认行为一致，保留兼容旧习惯）
    sys.exit(asyncio.run(cmd_browser_login()))
