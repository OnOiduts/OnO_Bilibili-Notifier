#!/usr/bin/env python3
# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""网页管理面板：可视化配置订阅、开关、文案模板，支持日间/暗夜主题。

启动：python webui.py           默认 http://127.0.0.1:8088
      python webui.py --host 0.0.0.0 --port 8088
"""
import argparse
import asyncio
import atexit
import glob
import os
import re
import sys
import time

import yaml
from flask import (Flask, jsonify, make_response, redirect,  # noqa: E401
                   render_template, request, send_file)

from bilibili import (BiliClient, parse_season_ref, BiliError,
                      live_status_cn)
import base64
import threading

import bili_login
import harden
import heartbeat
import secretbox
import version as _version
from cfgutil import (cfg_path as _cfg_path, creds_ready,
                     load_cfg as _load_cfg)
from cfgutil import safe_stdio
safe_stdio()
from notify import (DEFAULT_TEMPLATES, MARK_LINE, TPL_GROUPS, TPL_LABEL,
                    TPL_VARS, VAR_LABEL, apply_test_mark, cover_of, render,
                    strip_images)
from qqapi import QQClient, build_keyboard
from security import (Audit, LoginLimiter, bind_risk, client_ip, get_secret_key,
                      hash_password, is_hashed, is_loopback, issue_token, redact,
                      resolve_in_base, restrict_perms, validate_gid,
                      validate_kind, validate_uid, verify_password, verify_token,
                      bump_auth_gen, get_auth_gen)
from db import KIND_LABEL, KINDS, Store, DEFAULT_KINDS
import paths
import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = paths.config_path()

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=os.path.join(BASE_DIR, "static"))
# 会话签名密钥持久化到 .webui_secret（600），重启后已登录状态不失效
app.secret_key = get_secret_key()

# 让 jsonify 直接输出中文，而不是 \uXXXX 转义。
# 两者都是合法 JSON，但前端报错时若直接显示响应原文，用户看到的就是一串天书。
try:
    app.json.ensure_ascii = False
except Exception:      # 老版本 Flask 没有这个属性，忽略即可
    pass

# 加固层：安全响应头 + 全局限流 + 自动封禁 + 请求体大小限制
harden.init_app(app)

_state = {}
AUDIT = Audit()
LIMITER = LoginLimiter()
COOKIE_NAME = "bn_auth"
# 父进程（start.py）的 PID：由 --parent 传入，算残留时要排除它
PARENT_PID = 0


def cfg_path() -> str:
    """配置路径。直接用 cfgutil 的实现，避免两处定义不一致。"""
    return _cfg_path()


def load_cfg() -> dict:
    return _load_cfg()


def save_cfg(cfg: dict):
    """配置落盘。统一走 cfgutil 的那一份实现。

    ⚠️ 备份/恢复工具也要写 config.yaml。以前 webui 自己写一遍，
       工具里再写一遍，就会出现"一处加密、一处明文"的不一致。
    """
    from cfgutil import save_cfg as _save
    _save(dict(cfg))


def _migrate_cfg_once():
    """一次性修正历史默认值（只做一次，做完打标记）。

    v1.40.0 的面板把「图片尺寸提示-高」的默认值写成了 **378**，只要那时
    保存过任何配置，这个 378 就会留在 config.yaml 里。而 0 才代表
    「按原图比例自动」——378 会把所有预览锁死成 16:9，竖图/方图被拉变形。

    用户后来装了 v1.41.0（等比逻辑）也没用，因为配置里那个 378 一直在，
    而且从界面上完全看不出它仍然生效 —— 这正是"改了也没区别"的由来。

    这里把**这个特定的历史默认值**改回 0。只认 378 这一个值、只做一次，
    之后再手动填 378 不会被覆盖。
    """
    try:
        cfg = load_cfg()
        if not isinstance(cfg, dict) or not cfg:
            return
        if cfg.get("_cfg_migrated_v141"):
            return
        cfg["_cfg_migrated_v141"] = True
        try:
            if int(cfg.get("image_size_hint_h") or 0) == 378:
                cfg["image_size_hint_h"] = 0
                print("已把「图片尺寸提示-高」从旧默认值 378 改回 0（按原图比例自动）")
        except (TypeError, ValueError):
            pass
        # 「宽」被钳成 1 的历史脏值：保存时最小值钳到 1，而面板回填显示 1，
        # 用户以为宽度就是 1px、改了也没变化。1 不可能有人真想用，重置。
        try:
            if int(cfg.get("image_size_hint_w") or 0) == 1:
                cfg["image_size_hint_w"] = 672
                print("已把「图片尺寸提示-宽」从脏值 1 重置为 672")
        except (TypeError, ValueError):
            pass
        save_cfg(cfg)
    except Exception:
        pass


def _db_path() -> str:
    """数据文件路径：默认 state.json，落在数据目录（程序目录之外）。

    ⚠️ 不放程序目录：升级要删掉整个文件夹，数据跟着没了就得重新加一遍订阅。

    ⚠️ 必须走 db.data_path_from_cfg()：以前这里用 resolve_in_base()，
       相对路径被拼到**源码目录**，而机器人那边拼到**数据目录** —— 两边
       不是同一个文件，于是机器人照推、面板显示"还没有登记任何群"。
    """
    return db.data_path_from_cfg(load_cfg())


def get_store() -> Store:
    path = _db_path()
    # 面板先起来的情况（start.py 的兜底没跑到）：这里补一次
    try:
        db.auto_recover(path)
    except Exception:
        pass
    st = _state.get("store")
    if st is None or st.path != path:
        st = Store(path)
        _state["store"] = st
    st.refresh()
    return st


def bili_client() -> BiliClient:
    cfg = load_cfg()
    return BiliClient(cookie=cfg.get("bili_cookie", ""))


def run_async(coro):
    return asyncio.run(coro)


def need_auth() -> bool:
    return bool(str(load_cfg().get("web_password") or ""))


def authed() -> bool:
    """校验签名令牌，而不是拿 cookie 和口令做明文比对。

    ⚠️ 必须带上当前令牌世代号。签名密钥是持久化的、令牌有效期 7 天，
       少了世代号，服务端一旦签发就无法作废 —— 旧会话会一直有效。
    """
    return verify_token(app.secret_key, request.cookies.get(COOKIE_NAME, ""),
                        get_auth_gen())


def _auth_ok() -> bool:
    """写操作统一用这个判断，端点里**不要**直接调 authed()。

    ⚠️ authed() 只看 cookie 里的令牌。没设面板口令时 need_auth() 为假，
    压根不会走登录流程，cookie 里自然没有令牌 —— 于是
    `if not authed()` 一律拒绝，表现成「清空缓存 / 重启机器人一点就弹
    『请先登录』」，而用户根本没设口令、无从登录。
    所以：不需要登录时直接放行，需要登录时才校验令牌。
    """
    if not need_auth():
        return True
    return authed()


@app.before_request
def guard():
    # 登录入口和静态资源永远可访问（登录接口内部自己做限流）
    # ⚠️ /api/brand/ 也必须放行：登录页自己要显示 logo，标签页图标更是
    #    不带 cookie 也不带自定义头。设了口令之后如果把它们挡在门外，
    #    登录页会显示成无图、标签页图标也永远取不到。
    if request.path.startswith(("/login", "/static", "/api/login", "/api/brand")):
        return None

    # 写操作统一要求自定义头，阻断跨站表单提交（CSRF）
    if (request.path.startswith("/api/") and request.method != "GET"
            and request.headers.get("X-Requested-With") != "bili-notify"):
        AUDIT.log("CSRF拦截", client_ip(), request.path, "warn")
        return jsonify({"ok": False, "msg": "请求来源不被允许"}), 403

    if not need_auth():
        return None
    if authed():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "msg": "未登录"}), 401
    return redirect("/login")


def _check_login(ip, pwd):
    """校验一次登录尝试，返回 (ok, msg)。

    JSON 接口和**原生表单提交**共用这一套判定 —— 否则会出现
    "接口被限流了、但直接提交表单还能继续试"这种绕过。
    """
    left = LIMITER.remaining(ip)
    if left > 0:
        return False, f"尝试太多次，请 {left} 秒后再试"

    stored = str(load_cfg().get("web_password") or "")
    if not verify_password(pwd, stored):
        lock = LIMITER.note_fail(ip)
        AUDIT.log("登录失败", ip, f"已失败 {LIMITER.fails(ip)} 次", "warn")
        msg = f"口令不对。{'已锁定，请 ' + str(lock) + ' 秒后再试。' if lock else ''}"
        return False, msg

    LIMITER.note_ok(ip)
    AUDIT.log("登录成功", ip, "", "info")
    return True, ""


SESSION_COOKIE = "onobn_sess"   # 标记"这次是临时登录"，用于滑动续期


def _issue_login_cookie(resp, remember=True):
    """签发登录 cookie。

    ⚠️ 这里有个真实的坑，改的时候别再踩回去：
    remember 必须同时决定 **cookie 寿命** 和 **令牌本身的寿命（ttl）**。
    以前只用它设了 cookie 的 max_age，令牌却始终按默认的 7 天签发 ——
    于是「不勾记住」只是让 cookie 变成会话级，而 Chrome 的「继续上次的
    会话」会把会话 cookie 也恢复出来，令牌又是 7 天有效，结果勾不勾一个样。

    现在：
      - 勾了 → cookie max_age=7 天，令牌 ttl=7 天，持久登录；
      - 没勾 → cookie 会话级 + 令牌 ttl=SESSION_TOKEN_TTL（默认 30 分钟），
        并额外放一枚会话级标记 cookie，供 after_request 滑动续期。
    """
    import security as _sec
    ttl = _sec.TOKEN_TTL if remember else _sec.SESSION_TOKEN_TTL
    resp.set_cookie(
        COOKIE_NAME,
        issue_token(app.secret_key, ttl=ttl, gen=get_auth_gen()),
        max_age=(7 * 86400 if remember else None),
        httponly=True, samesite="Strict",
        secure=request.is_secure,
    )
    if remember:
        resp.set_cookie(SESSION_COOKIE, "", max_age=0, httponly=True,
                        samesite="Strict", secure=request.is_secure)
    else:
        resp.set_cookie(SESSION_COOKIE, "1", max_age=None, httponly=True,
                        samesite="Strict", secure=request.is_secure)
    return resp


@app.after_request
def _slide_session(resp):
    """临时登录（没勾记住）滑动续期：还在操作就续，不动了到点自己失效。

    为什么需要这个：单靠 cookie 寿命判断"浏览器关没关"是不可靠的，
    浏览器会恢复会话 cookie。所以改成服务端空闲计时 —— 30 分钟没有任何
    请求，令牌自然过期，下一次访问就得重新登录。
    """
    try:
        if request.cookies.get(SESSION_COOKIE) != "1":
            return resp
        if not need_auth() or not authed():
            return resp
        resp.set_cookie(
            COOKIE_NAME,
            issue_token(app.secret_key, ttl=_sec_ttl(), gen=get_auth_gen()),
            max_age=None, httponly=True, samesite="Strict",
            secure=request.is_secure,
        )
    except Exception:
        pass
    return resp


def _sec_ttl():
    import security as _sec
    return _sec.SESSION_TOKEN_TTL


@app.route("/api/login", methods=["POST"])
def api_login():
    ip = client_ip()
    d = request.json or {}
    pwd = d.get("password", "")
    ok, msg = _check_login(ip, pwd)
    if not ok:
        code = 401 if "口令不对" in msg else 429
        return jsonify({"ok": False, "msg": msg}), code

    resp = make_response(jsonify({"ok": True}))
    return _issue_login_cookie(resp, remember=bool(d.get("remember")))


@app.route("/logout")
def logout():
    # 世代号 +1：登出要让所有已签发的会话作废，而不只是清掉自己
    # 这一枚 cookie —— 否则别人手里的旧令牌照样能进。
    bump_auth_gen()
    resp = make_response(redirect("/login"))
    resp.set_cookie(COOKIE_NAME, "", max_age=0)
    return resp


@app.route("/api/lock", methods=["POST"])
def api_lock():
    """手动锁：立刻锁定面板，所有会话作废（不限当前这一枚）。

    跟 logout() 的区别只在返回形态 —— 这个是给面板按钮调的，返回 JSON，
    前端自己跳登录页。世代号 +1 是同一个动作，所以别人手里的旧令牌也失效。
    """
    if not need_auth():
        return jsonify({"ok": False, "msg": "还没设置口令，无需锁定"})
    bump_auth_gen()
    resp = make_response(jsonify({"ok": True, "msg": "已锁定"}))
    resp.set_cookie(COOKIE_NAME, "", max_age=0, httponly=True,
                    samesite="Strict", secure=request.is_secure)
    resp.set_cookie(SESSION_COOKIE, "", max_age=0, httponly=True,
                    samesite="Strict", secure=request.is_secure)
    return resp


def _theme_from_cookie() -> str:
    """登录页初始主题：读面板写下的 bili-theme cookie。

    为什么需要服务端参与：登录页此前把主题完全交给 body 末尾的一段 JS
    —— 脚本没跑到（被拦、被缓存、老内核报错）就是硬编码的 dark，用户看到
    "锁死在夜间模式"；跑到也是先把深色画出来再切成浅色，闪一下。

    现在两端都参与：服务端先按 cookie 定一次（JS 完全不执行也对），
    前端 <head> 里的内联脚本再用 localStorage 校正一次（面板主要存那里）。

    ⚠️ cookie 只是"尽力而为"：读不到就返回 dark，由前端兜底，不在这里猜。
    """
    try:
        from flask import request
        v = (request.cookies.get("bili-theme") or "").strip().lower()
    except Exception:
        v = ""
    return v if v in ("light", "dark") else "dark"


def _login_ctx(error="", authed_now=None):
    """登录页模板变量，两条渲染路径（GET / POST 失败）共用，避免漏传。"""
    if authed_now is None:
        authed_now = authed()
    return {
        "error": error,
        "authed": authed_now,
        # ⚠️ 必须传：登录页用它拼 style.css?v= 和 ico?v= 的缓存串。
        #    不传就渲染成空，换版本后浏览器继续用旧的 CSS，看着像"改了没生效"。
        "app_version": _version.current(),
        # 背景飘动用的**同目录同名字的 svg**；svg 取不到时前端会自动退回同名 png。
        "bg_logo_svg": svg_variant(_logo_remote_url()),
        "bg_ico_svg": svg_variant(_ico_remote_url()),
        # 背景额外飘的两张装饰图（svg 取不到前端会退回同名 png）
        # ⚠️ png 地址也必须传：装饰图此前只挂了 onerror="display:none"，
        #    svg 一旦取不到就直接消失，于是"左下/右下两个角是空的"。
        #    现在跟 logo/ico 一样走 svg → 同名 png → 隐藏 的降级链。
        "bg1_svg": svg_variant(_bg1_remote_url()),
        "bg2_svg": svg_variant(_bg2_remote_url()),
        "bg1_png": _bg1_remote_url(),
        "bg2_png": _bg2_remote_url(),
        # 初始主题：后端读 cookie 先定一次，前端再用 localStorage 校正。
        "theme": _theme_from_cookie(),
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    if not need_auth():
        return redirect("/")

    # ⚠️ POST 这条路径是**保底登录**：登录页的提交是一个真正的
    # <form method="post">，不走 JavaScript。以前只支持 JSON 接口，
    # 脚本一旦没执行（老浏览器不认 ES6 语法、被插件或缓存拦掉），
    # 点「进入」就毫无反应，连错误都显示不出来 —— 因为显示提示的代码也是 JS。
    # 现在没有 JS 也能登录：浏览器原生提交 → 这里校验 → 成功直接跳转。
    if request.method == "POST":
        pwd = request.form.get("password", "")
        # 原生表单提交：remember 是真正的 <input name="remember">，
        # 浏览器会一起序列化进来，不用像 fetch 那条路那样手动塞。
        remember = bool(request.form.get("remember"))
        ok, msg = _check_login(client_ip(), pwd)
        if ok:
            return _issue_login_cookie(make_response(redirect("/")),
                                       remember=remember)
        # 失败就重新渲染登录页，把原因写进页面（服务端渲染，不依赖 JS）
        return render_template("login.html", **_login_ctx(error=msg,
                                                          authed_now=False)), 401

    return render_template("login.html", **_login_ctx())


# ---------------- 页面 ----------------
@app.route("/")
def index():
    # ⚠️ app_version 必须传：页面用它显示版本号，并且拼在
    # /static/style.css?v=... 后面当缓存串。不传就渲染成空，
    # 更新后浏览器会继续用旧的 CSS/JS，看着像"改了没生效"。
    # ⚠️ theme 必须传：面板首页此前硬编码 data-theme="dark"，
    #    日间模式的用户登录成功后进面板会先闪一下夜间，等 app.js
    #    加载执行才变回日间。服务端先按 cookie 定一次，
    #    前端 <head> 里的内联脚本再用 localStorage 校正。
    return render_template("index.html", app_version=_version.current(),
                           theme=_theme_from_cookie())


# ---------------- API ----------------
@app.route("/api/state")
def api_state():
    cfg = load_cfg()
    st = get_store()
    # 机器人每轮检测的结果（含失败原因）。挂在订阅项上，
    # 这样"开了直播却没提示"时能直接在面板看到是不是接口失败了。
    notes = (heartbeat.read() or {}).get("notes") or {}
    groups = []
    for gid, g in st.data["groups"].items():
        if not isinstance(g, dict):
            continue
        subs = []
        for uid, item in (g.get("subs") or {}).items():
            if not isinstance(item, dict):
                continue
            # ups 缓存里偶尔混进非 dict 的坏值（历史数据），
            # 直接 .get() 会抛 'str' object has no attribute 'get'，
            # 整个 /api/state 挂掉 → 面板显示不出任何订阅。
            _up = st.get_up(int(uid)) if uid.isdigit() else {}
            if not isinstance(_up, dict):
                _up = {}
            subs.append({
                "uid": uid,
                "uname": item.get("uname") or uid,
                "face": (_up.get("face") or "") if uid.isdigit() else "",
                "room_id": (_up.get("room_id") or 0)
                           if uid.isdigit() else 0,
                "short_room_id": (_up.get("short_room_id")
                                  or 0) if uid.isdigit() else 0,
                "kinds": {k: {
                    "on": bool(((item.get(k) or {})
                                if isinstance(item.get(k), dict) else {}
                                ).get("on")),
                } for k in KINDS},
                "last": {
                    k: (notes.get(f"{uid}:{k}") or {}).get("text", "")
                    for k in ("live", "video", "dyn")
                },
            })
        # 该群订阅的视频合集（与 UP 主订阅是两套独立的东西）
        try:
            seasons = st.seasons_of_group(gid)
        except Exception:
            seasons = []
        for sv in seasons:
            if not isinstance(sv, dict):
                continue
            try:
                _su = st.get_up(int(sv["uid"]))
            except Exception:
                _su = {}
            if not isinstance(_su, dict):
                _su = {}
            # ⚠️ 三级兜底，别再显示成 uid：
            #   ① 订阅时记下的名字（只订合集、没订 UP 主时只有它有）
            #   ② ups 缓存（订过 UP 主的会有）
            #   ③ 同一个群里正好也订了这个 UP 主 → 用订阅项上的名字
            #   都没有才退回 uid，而不是一上来就显示数字。
            _nm = str(sv.get("uname") or "") or str(_su.get("uname") or "")
            if not _nm:
                for _s in subs:
                    if str(_s.get("uid")) == str(sv.get("uid")):
                        _nm = str(_s.get("uname") or "")
                        break
            sv["uname"] = _nm or str(sv["uid"])
            sv["face"] = str(_su.get("face") or "")
            # 合集封面：面板拿它当合集头像（裁 1:1）。老的订阅没存过封面，
            # 退回 UP 主头像 —— 至少不是空白方块。
            sv["cover"] = str(sv.get("cover") or "")
            sv["tpl_custom"] = len(st.get_season_templates(
                gid, sv["uid"], sv["season_id"]))
            sv["url"] = (f"https://space.bilibili.com/{sv['uid']}/lists/"
                         f"{sv['season_id']}?type=season")
        groups.append({"gid": gid, "name": g.get("name") or "",
                       "subs": subs, "seasons": seasons})

    # ⚠️ 订阅数由后端统一算好再给前端（去重口径见 Store.sub_stats）。
    #    以前前端按 groups 现数：合集拿 "uid:season_id" 拼键，某个群没存
    #    uid 就多算一个；非数字 uid 也算一个 UP 主 —— 面板写着 7 位，
    #    机器人日志说 6 位，两边对不上。
    try:
        _sst = st.sub_stats()
    except Exception:
        _sst = {"ups": 0, "seasons": 0, "total": 0}

    return jsonify({
        "ok": True,
        "groups": groups,
        "sub_stats": _sst,
        "config": {
            "appid": cfg.get("appid", ""),
            "has_secret": bool(cfg.get("secret")),
            "is_sandbox": bool(cfg.get("is_sandbox", False)),
            "sandbox_group": str(cfg.get("sandbox_group") or ""),
            "poll_interval": cfg.get("poll_interval", 30),
            "slow_check_every": cfg.get("slow_check_every", 1),
            "notify_offline": bool(cfg.get("notify_offline", False)),
            "send_image": bool(cfg.get("send_image", True)),
            "image_send_mode": str(cfg.get("image_send_mode", "inline")),
            "image_inline_width": cfg.get("image_inline_width", 0),
            "image_size_hint": bool(cfg.get("image_size_hint", True)),
            "image_size_hint_w": cfg.get("image_size_hint_w", 672),
            "image_size_hint_h": cfg.get("image_size_hint_h", 0),
            "image_size_hint_avatar": cfg.get("image_size_hint_avatar", 160),
            "image_size_hint_max_h": cfg.get("image_size_hint_max_h", 1200),
            "image_size_hint_cell": cfg.get("image_size_hint_cell", 320),
            "image_grid_mode": _norm_grid_mode(cfg.get("image_grid_mode", "box")),
            "image_max_per_msg": cfg.get("image_max_per_msg", 0),
            "message_style": str(cfg.get("message_style", "markdown")),

            "top_comment_only_up": bool(cfg.get("top_comment_only_up", True)),
            "log_translate": bool(cfg.get("log_translate", True)),
            "log_debug": bool(cfg.get("log_debug", False)),
            "request_timeout": cfg.get("request_timeout", 10),
            "dedupe_window": cfg.get("dedupe_window", 900),

            # ⚠️ 这几个字段以前**只在 /api/config 里收，没在 /api/state 里回**。
            #    结果：保存真的落盘了，但前端 loadState() 刷回来的 config 里
            #    根本没有这些键 → 一律回退前端默认值 → 输入框表现成
            #    "保存成功（toast 对）但内容又跳回旧值"。
            #    凡是面板能改的字段，state 必须回，否则就是假保存。
            "selfcheck_uid": str(cfg.get("selfcheck_uid") or SELFCHECK_UID),
            # 自检 UID 对应的 UP 主名，只从本地订阅/缓存里查，不联网。
            # 查不到就是空，前端显示「用户」，不瞎编名字。
            "selfcheck_uname": _selfcheck_uname(
                str(cfg.get("selfcheck_uid") or SELFCHECK_UID)),
            "min_request_interval": cfg.get("min_request_interval", 2.0),
            "poll_interval_min": cfg.get("poll_interval_min", 8),
            "poll_interval_max": cfg.get("poll_interval_max", 120),

            "bili_cookie": cfg.get("bili_cookie", ""),
            "web_password": bool(cfg.get("web_password")),
            # 机器人写的「登录态到期提醒」：level=ok/ui/qq，面板顶部横幅会读。
            # 机器人不在跑时读不到，前端当作"不提醒"，不显示横幅。
            "bili_expiry": ((heartbeat.read() or {}).get("bili_expiry")
                            or {"level": "ok"}),
        },
        "templates": {**DEFAULT_TEMPLATES, **(cfg.get("templates") or {})},
        # 自定义值单独给，前端才能区分「用户改过」和「用的默认」
        "tpl_custom": cfg.get("templates") or {},
        "tpl_defaults": DEFAULT_TEMPLATES,
        "tpl_titles": TPL_LABEL,
        "kinds": [{"key": k, "label": KIND_LABEL[k]} for k in KINDS],
        # 每个群对每个 UP 改了几条专属文案 → {gid: {uid: n}}
        "up_tpl_counts": {
            g["gid"]: st.up_tpl_counts(g["gid"])
            for g in st.groups_with_meta()},
        "bot_running": bot_alive(),
        "creds_ready": creds_ready(cfg),
        "heartbeat": heartbeat.status(),
        "protection": secretbox.status(),
        "version": _version.current(),
    })


# 面板头像能直接引用的图片域名。
# ⚠️ 只放行这几个，别的域名一律拒绝 —— 否则这个接口就成了
#    「任意 URL 代理」，能把内网地址也拉出来（SSRF）。
_IMG_HOST_OK = (".hdslb.com", ".bilibili.com", ".biliimg.com", ".akamaized.net")


def _img_host_ok(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = (urlparse(str(url)).hostname or "").lower()
    except Exception:
        return False
    return bool(host) and host.endswith(_IMG_HOST_OK)


@app.route("/api/img")
def api_img():
    """代理面板里的 B站 图片（主要是 UP 主头像）。

    ⚠️ 为什么要绕一圈：B站图床带防盗链，浏览器以 127.0.0.1 为 Referer
    直接请求 i0.hdslb.com，拿到的往往不是头像而是占位图/403 ——
    表现就是「头像获取异常」：明明有 face 地址，界面上却是空的。
    服务端带上 B站的 Referer 去取就能拿到真图，再原样吐给浏览器。

    结果落 media 的长期缓存，同一张头像只取一次。
    """
    import media as _media
    url = str(request.args.get("u") or "").strip()
    if not url:
        return jsonify({"ok": False, "msg": "缺少 u 参数"}), 400
    if not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"ok": False, "msg": "只支持 http/https"}), 400
    if not _img_host_ok(url):
        return jsonify({"ok": False, "msg": "这个域名不允许"}), 403
    try:
        data = _media.load_long(url)
    except Exception:
        data = None
    if not data:
        try:
            data = run_async(_media.fetch_image(url))
        except Exception:
            data = None
    if not data:
        return jsonify({"ok": False, "msg": "取不到这张图"}), 404
    try:
        _media.save_long(url, data)
    except Exception:
        pass
    ct = "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ct = "image/png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ct = "image/webp"
    elif data[:3] == b"GIF":
        ct = "image/gif"
    resp = make_response(data)
    resp.headers["Content-Type"] = ct
    # 头像不会变，缓存一天
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


async def _fetch_up_seasons(uid: int) -> list:
    """取一个 UP 主的合集清单，失败就抛给调用方展示原因。"""
    c = bili_client()
    try:
        return await c.get_up_seasons(uid)
    finally:
        await c.close()


@app.route("/api/up/search")
def api_up_search():
    """查一个 UP 主，顺带把他名下的合集列出来。

    ⚠️ 为什么要顺带返回合集：以前「订阅合集」只能手动粘合集链接
    （space.bilibili.com/UID/lists/ID?type=season），UID 和合集 ID
    都得自己找。现在查询完直接在结果里点就行 —— UP 主和合集
    分两栏，点哪个加哪个。
    """
    try:
        uid = validate_uid(request.args.get("uid", ""))
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})
    try:
        up = run_async(_fetch_up(uid))
    except Exception as e:
        return jsonify({"ok": False, "msg": redact(str(e))})
    # 合集列表取不到不该连 UP 主一起查不出来 —— 单独兜住，
    # 只是那一栏显示"没取到"，UP 主订阅照常能加。
    seasons, series_n, sea_err = [], 0, ""
    try:
        lst = run_async(_fetch_up_seasons(uid)) or []
        for it in lst:
            # series（视频列表）是另一个东西，ID 不通用，
            # 拿它去查合集必然 404 —— 只统计个数，不给订阅。
            if str(it.get("type") or "") == "series":
                series_n += 1
                continue
            seasons.append({
                "id": int(it.get("id") or 0),
                "title": str(it.get("title") or ""),
                "total": int(it.get("total") or 0),
                # 前端按"最近一次加视频"倒序展示，最新更新的排最上面
                "recent": int(it.get("recent") or it.get("mtime") or 0),
            })
    except Exception as e:
        sea_err = redact(str(e))[:160]
    seasons = [s for s in seasons if s["id"]]
    seasons.sort(key=lambda x: x["recent"], reverse=True)
    return jsonify({"ok": True, "up": up, "seasons": seasons,
                    "series_n": series_n, "season_err": sea_err})


async def _probe_season(uid: int, season_id: int) -> dict:
    """添加合集订阅前先查一次，确认这个合集真的存在。

    以前添加时不校验，只解析链接就写库 —— 于是填错 ID、填成
    「视频列表(series)」的 ID、或合集早被 UP 主删掉，都会静默写进去，
    之后每一轮轮询都对这个死 ID 报错（表现为每轮一条 404 警告）。
    """
    c = bili_client()
    try:
        info = await c.get_season_archives(uid, season_id, max_pages=1)
    finally:
        await c.close()
    if not info or not info.videos:
        raise BiliError(f"合集 {season_id} 里没有视频，或这个 ID 取不到内容")
    return {"title": info.title, "total": info.total,
            "cover": getattr(info, "cover", "") or ""}


async def _fetch_up(uid: int) -> dict:
    c = bili_client()
    try:
        up = await c.get_up(uid)
        if not up.uname:
            raise BiliError("查不到这个 UP 主")
        return {"uid": up.uid, "uname": up.uname, "face": up.face,
                "room_id": up.room_id,
                "short_room_id": getattr(up, "short_room_id", 0)}
    finally:
        await c.close()


@app.route("/api/sub/add", methods=["POST"])
def api_sub_add():
    d = request.json or {}
    try:
        gid = validate_gid(d.get("gid"))
        uid = validate_uid(d.get("uid"))
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})
    uname = str(d.get("uname") or "").strip()[:64]
    room_id = int(d.get("room_id") or 0)
    short_room_id = int(d.get("short_room_id") or 0)
    face = str(d.get("face") or "").strip()[:512]
    st = get_store()
    if not uname or not room_id or not face:
        try:
            info = run_async(_fetch_up(int(uid)))
            uname = uname or info["uname"]
            room_id = room_id or info["room_id"]
            short_room_id = short_room_id or info.get("short_room_id") or 0
            face = face or (info.get("face") or "")
        except Exception as e:
            return jsonify({"ok": False, "msg": f"查询 UP 主失败：{e}"})
    st.set_up_info(uid, uname, room_id, face, short_room_id)
    for k in KINDS:
        # 置顶评论默认关（前端不传就是关），其余三类默认开
        default_on = k in DEFAULT_KINDS
        st.set_sub(gid, uid, k, bool(d.get("kinds", {}).get(k, default_on)),
                   uname)
    AUDIT.log("添加订阅", client_ip(), f"{gid} uid={uid} {uname}", "info")
    return jsonify({"ok": True, "msg": f"已添加 {uname}"})


@app.route("/api/sub/set", methods=["POST"])
def api_sub_set():
    d = request.json or {}
    try:
        gid = validate_gid(d.get("gid"))
        uid = validate_uid(d.get("uid"))
        kind = validate_kind(d.get("kind"))
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})
    st = get_store()
    st.set_sub(gid, uid, kind, bool(d.get("on")))
    return jsonify({"ok": True})


@app.route("/api/season/add", methods=["POST"])
def api_season_add():
    """订阅一个视频合集。

    支持粘贴合集链接自动解析：
      https://space.bilibili.com/{mid}/lists/{sid}?type=season
    也可以直接填 season_id（uid 不填时用当前指定的 UP 主）。
    """
    d = request.json or {}
    try:
        gid = validate_gid(d.get("gid"))
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})

    uid_raw = str(d.get("uid") or "").strip()
    ref = str(d.get("ref") or "").strip()     # 链接或 season_id
    try:
        uid = validate_uid(uid_raw) if uid_raw else 0
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})

    p_uid, p_sid = parse_season_ref(ref)
    uid = int(uid or p_uid or 0)
    season_id = int(p_sid or 0)
    if not uid:
        return jsonify({"ok": False, "msg": "还需要填 UP 主的 UID"})
    if not season_id:
        return jsonify({"ok": False, "msg": "没认出合集 ID，请粘贴合集链接或填数字"})

    # 先查一次，确认合集真的能取到 —— 避免把死 ID 写进库，
    # 之后每轮轮询都对它报错。校验失败就不写库。
    try:
        probed = run_async(_probe_season(uid, season_id))
    except Exception as e:
        return jsonify({"ok": False, "msg":
            f"查不到这个合集（{redact(str(e))[:120]}）。"
            f"确认一下：链接要是 ...?type=season 的合集，"
            f"不是「视频列表」；UID 是这个合集作者的。"})

    st = get_store()
    # 订阅时就把 UP 主名字记下来：只订合集、没订 UP 主的情况下，
    # ups 缓存里不会有这一条，界面上就会显示成 uid。
    _owner = str(d.get("uname") or "").strip()[:64]
    if not _owner:
        try:
            _owner = str((st.get_up(uid) or {}).get("uname") or "")
        except Exception:
            _owner = ""
    if not _owner:
        try:
            _owner = str(run_async(_fetch_up(uid)).get("uname") or "")
        except Exception:
            _owner = ""
    try:
        st.add_season(gid, uid, season_id,
                      str(d.get("title") or probed.get("title") or ""),
                      uname=_owner,
                      cover=str(probed.get("cover") or ""))
    except Exception as e:
        return jsonify({"ok": False, "msg": f"添加失败：{redact(str(e))}"})
    try:
        AUDIT.log("订阅合集", client_ip(), f"{gid[:8]} {season_id}", "info")
    except Exception:
        pass
    return jsonify({"ok": True, "msg": f"已订阅合集 {season_id}（静默记录基线，不补推历史）"})


@app.route("/api/season/remove", methods=["POST"])
def api_season_remove():
    d = request.json or {}
    try:
        gid = validate_gid(d.get("gid"))
        uid = validate_uid(d.get("uid"))
        season_id = int(d.get("season_id"))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "参数不对"})
    st = get_store()
    try:
        st.remove_season(gid, uid, season_id)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"删除失败：{redact(str(e))}"})
    return jsonify({"ok": True})


@app.route("/api/season/set", methods=["POST"])
def api_season_set():
    """开关某群的某个合集。"""
    d = request.json or {}
    try:
        gid = validate_gid(d.get("gid"))
        uid = validate_uid(d.get("uid"))
        season_id = int(d.get("season_id"))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "参数不对"})
    st = get_store()
    try:
        st.set_season_on(gid, uid, season_id, bool(d.get("on")))
    except Exception as e:
        return jsonify({"ok": False, "msg": f"设置失败：{redact(str(e))}"})
    return jsonify({"ok": True})


@app.route("/api/season/templates", methods=["GET", "POST"])
def api_season_templates():
    """某个合集的专属文案（最细一层，比群级更优先）。

    GET  ?gid=&uid=&season_id=
    POST {gid, uid, season_id, templates:{kind: content}}
    某项传空 = 取消覆盖，回落到群级 / 全局 / 内置默认。
    """
    from notify import DEFAULT_TEMPLATES, TPL_LABEL
    if request.method == "GET":
        try:
            gid = validate_gid(request.args.get("gid"))
            uid = validate_uid(request.args.get("uid"))
            season_id = int(request.args.get("season_id"))
        except (ValueError, TypeError):
            return jsonify({"ok": False, "msg": "参数不对"})
        st = get_store()
        cfg = load_cfg()
        return jsonify({
            "ok": True,
            "defaults": DEFAULT_TEMPLATES,
            "global": cfg.get("templates") or {},
            "custom": st.get_season_templates(gid, uid, season_id),
            "titles": TPL_LABEL,
            # 合集只显示「合集更新」这一类，变量也只给合集能用的
            "groups": [{"key": "season", "label": "📦 合集更新",
                        "keys": ["season"]}],
            "vars": TPL_VARS,
            "var_label": VAR_LABEL,
        })

    d = request.json or {}
    try:
        gid = validate_gid(d.get("gid"))
        uid = validate_uid(d.get("uid"))
        season_id = int(d.get("season_id"))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "参数不对"})
    incoming = d.get("templates")
    if not isinstance(incoming, dict):
        return jsonify({"ok": False, "msg": "格式不对"})
    cleaned = {k: str(v or "")[:500] for k, v in incoming.items()
               if k in DEFAULT_TEMPLATES}
    st = get_store()
    try:
        n = st.set_season_templates(gid, uid, season_id, cleaned)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"保存失败：{redact(str(e))}"})
    return jsonify({"ok": True,
                    "msg": (f"已保存 {n} 条专属文案" if n else "已清空，改用上一层文案")})


@app.route("/api/up/templates", methods=["GET", "POST"])
def api_up_templates():
    """某个群对**某个 UP 主**的专属文案。

    GET   ?gid=&uid=          → 只返回这个群对该 UP **开了哪些类型**对应
                                的类目（开了直播+动态就只给这两块）
    POST  {gid, uid, templates:{kind: content}}

    ⚠️ 只给该类目相关的输入框：合集的编辑页里不该出现"开播提醒"。
    """
    if request.method == "GET":
        try:
            gid = validate_gid(request.args.get("gid"))
            uid = validate_uid(request.args.get("uid"))
        except (ValueError, TypeError):
            return jsonify({"ok": False, "msg": "参数不对"})
        st = get_store()
        cfg = load_cfg()
        opened = st.up_kinds_in_group(gid, uid)
        # 该 UP 开了哪些类型 → 只显示这些类目
        want = []
        for gkey, _label, keys in TPL_GROUPS:
            if gkey == "season":
                continue
            if gkey in opened or (gkey == "live" and
                                  ("live" in opened or "offline" in opened)):
                want.append({"key": gkey, "label": _label, "keys": keys})
        if not want:
            want = [{"key": g[0], "label": g[1], "keys": g[2]}
                    for g in TPL_GROUPS if g[0] != "season"]
        return jsonify({
            "ok": True,
            "defaults": DEFAULT_TEMPLATES,
            "global": cfg.get("templates") or {},
            "custom": st.get_up_templates(gid, uid),
            "titles": TPL_LABEL,
            "groups": want,
            "opened": sorted(opened),
            "vars": TPL_VARS,
            "var_label": VAR_LABEL,
        })

    d = request.json or {}
    try:
        gid = validate_gid(d.get("gid"))
        uid = validate_uid(d.get("uid"))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "参数不对"})
    incoming = d.get("templates")
    if not isinstance(incoming, dict):
        return jsonify({"ok": False, "msg": "格式不对"})
    cleaned = {k: str(v or "")[:500] for k, v in incoming.items()
               if k in DEFAULT_TEMPLATES}
    st = get_store()
    try:
        n = st.set_up_templates(gid, uid, cleaned)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"保存失败：{redact(str(e))}"})
    return jsonify({"ok": True,
                    "msg": (f"已保存 {n} 条专属文案" if n else "已清空，改用上一层文案")})


@app.route("/api/sub/copy", methods=["POST"])
def api_sub_copy():
    """把一个群的订阅复制/迁移到另一个群。

    mode=copy（默认）：源群保留，目标群得到一份相同的订阅
    mode=move       ：源群清空，订阅全部搬过去

    ⚠️ move 会**清空源群**，这是不可逆的。所以前端必须二次确认，
    后端也要求显式传 move=true（不接受"没传就当迁移"）。
    """
    d = request.json or {}
    try:
        src = validate_gid(d.get("src"))
        dst = validate_gid(d.get("dst"))
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})
    try:
        move = bool(d.get("move"))
    except (TypeError, ValueError):
        move = False

    # 迁移范围：全体（默认，兼容老调用）/ 选择（只搬勾中的 UP × 提醒类型）
    scope = str(d.get("scope") or "all").strip().lower()
    picks = None
    season_picks = None
    if scope == "part":
        picks = {}
        for p in (d.get("picks") or []):
            if not isinstance(p, dict):
                continue
            try:
                uid = int(p.get("uid"))
            except (TypeError, ValueError):
                continue
            ks = [str(k) for k in (p.get("kinds") or []) if str(k) in KINDS]
            if ks:
                picks.setdefault(uid, set()).update(ks)
        # 合集也能单独挑：传了 season_picks 就不再按 UP 过滤
        # （空列表 = 明确一个合集都不搬）。
        # ⚠️ 没传这个字段时保持老行为（按 UP 勾选带走），兼容旧前端。
        _sp = d.get("season_picks")
        if isinstance(_sp, list):
            sp = set()
            for x in _sp:
                try:
                    sp.add(int(x))
                except (TypeError, ValueError):
                    continue
            season_picks = sp
        # 允许"只搬合集"：picks 为空不再单独拒绝，
        # 只要合集也勾了就算有效（真正的判空交给 db，避免两处口径不一致）
        if not picks and not season_picks:
            return jsonify({"ok": False, "msg": "还没勾选任何要搬的内容"})
    try:
        with_seasons = bool(d.get("seasons", True))
    except (TypeError, ValueError):
        with_seasons = True

    st = get_store()
    try:
        r = st.copy_subs(src, dst, move=move, picks=picks,
                         with_seasons=with_seasons,
                         season_picks=season_picks)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"迁移失败：{redact(str(e))}"})
    if not r.get("ok"):
        return jsonify({"ok": False, "msg": r.get("msg") or "迁移失败"})
    try:
        AUDIT.log("订阅迁移" if move else "订阅复制", client_ip(),
                  f"{src[:8]}→{dst[:8]} {r.get('ups')}位"
                  + ("（选择迁移）" if picks else ""), "info")
    except Exception:
        pass
    return jsonify({"ok": True, "msg": r.get("msg") or "已完成",
                    "ups": r.get("ups", 0), "kinds": r.get("kinds", 0),
                    "seasons": r.get("seasons", 0)})


@app.route("/api/sub/remove", methods=["POST"])
def api_sub_remove():
    d = request.json or {}
    try:
        gid = validate_gid(d.get("gid"))
        uid = validate_uid(d.get("uid"))
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})
    st = get_store()
    st.remove_up(gid, uid)
    AUDIT.log("删除订阅", client_ip(), f"{gid} uid={uid}", "info")
    return jsonify({"ok": True})


@app.route("/api/group/name", methods=["POST"])
def api_group_name():
    d = request.json or {}
    try:
        gid = validate_gid(d.get("gid"))
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})
    st = get_store()
    st.set_group_name(gid, str(d.get("name") or "").strip()[:40])
    return jsonify({"ok": True})


def _is_gone(code, msg=None) -> bool:
    """这个失败是不是明确说「机器人已不在这个群」。

    和 bot.is_gone_group_error 同一套判定（错误码 + 中文兜底），
    只是这里拿到的已经是解析好的 code / msg。
    """
    try:
        from bot import is_gone_group_error
    except Exception:
        is_gone_group_error = None
    if is_gone_group_error is not None:
        # 拼一个和原始报错差不多的串，复用同一套判定
        return is_gone_group_error(
            f"code={code} message={msg or ''} msg={msg or ''}")
    return str(code) in ("11242", "11252", "11292", "11293")


def _group_name_fail_reason(code, msg) -> str:
    """把群名获取失败的原因说成人话。

    官方常见错误码：
      11253 = 接口未开通（白名单）
      其它   = 原样带上 code 和 message，方便对照官方文档排查

    ⚠️ 11292 一号多义：既可能是"机器人不在群里"，也可能是
    "沙箱环境不能访问此资源"（err_code 40012004）。沙箱那个
    必须说清楚，否则用户会以为群没了、白折腾。
    """
    try:
        from bot import is_sandbox_error
        if is_sandbox_error(f"code={code} message={msg or ''}"):
            return ("沙箱环境不能访问此资源：到「设置 → 通用 → 沙盒群」"
                    "关掉「沙箱环境」（已支持热切换，不用重启机器人）")
    except Exception:
        pass
    if code is None:
        return f"接口没返回群名{('：' + str(msg)) if msg else ''}"
    table = {
        11253: "官方「获取群信息」接口未开通（白名单接口，需向平台申请）",
        11242: "机器人不在这个群里（或已被移出）",
        11252: "群不存在或已解散",
        11292: "机器人已不在该群，无法获取",
        11293: "机器人非群成员（已被移出）",
    }
    try:
        c = int(code)
    except (TypeError, ValueError):
        return str(msg or "未知原因")[:120]
    base = table.get(c) or f"错误码 {c}"
    if msg and c not in table:
        base += f"（{str(msg)[:80]}）"
    return base


def _backfill_season_names(st, limit: int = 8) -> int:
    """给「还显示成 uid」的旧合集补回 UP 主名字。

    以前订阅合集时不记名字，ups 缓存里也没有，界面就显示数字。
    这里按 uid 去重后逐个查一次 B站，查到就写回 seasons.uname。
    ⚠️ 最多补 limit 个：刷新是顺带做的，不能因为查名字把面板卡住。
    """
    try:
        rows = st._conn().execute(
            "SELECT DISTINCT uid FROM seasons WHERE uname='' "
            "LIMIT ?", (int(limit),)).fetchall()
    except Exception:
        return 0
    fixed = 0
    for r in rows:
        try:
            uid = int(r["uid"])
        except Exception:
            continue
        if uid <= 0:
            continue
        try:
            nm = str(run_async(_fetch_up(uid)).get("uname") or "")
        except Exception:
            nm = ""
        if not nm:
            continue
        try:
            st.set_season_uname(uid, nm)
            st.set_up_info(uid, nm, 0)
        except Exception:
            pass
        fixed += 1
    return fixed


@app.route("/api/group/refresh-names", methods=["POST"])
def api_group_refresh_names():
    """向官方批量查询群名并保存。

    官方接口 GET /v2/groups/{group_openid}/info 能返回 group_name，
    但它是白名单接口，未开通会返回 11253。这里把每个群的结果如实返回，
    让前端能告诉用户「哪些成功了、为什么失败的」。
    """
    cfg = load_cfg()
    if not creds_ready(cfg):
        return jsonify({"ok": False, "msg": "请先填好 AppID / AppSecret"})

    st = get_store()
    gids = list(st.data["groups"].keys())
    if not gids:
        return jsonify({"ok": True, "updated": 0, "denied": False,
                        "msg": "还没有群"})

    # ⚠️ 整个循环必须在**同一个事件循环**里跑完。
    # 之前是 `for gid: run_async(qq.get_group_info(gid))`，
    # 而 run_async = asyncio.run() —— 它每次都会新建一个事件循环，
    # 跑完立刻关闭。但 QQClient 里的 aiohttp 会话是**缓存在实例上**的，
    # 第一次调用时创建、绑定到第 1 个循环；第 1 个循环关闭后，
    # 第 2 次调用在新循环里复用这个会话 → "Event loop is closed"。
    # 表现就是：只有第一个群成功，后面全部失败。
    async def _run():
        _qq = QQClient(cfg["appid"], cfg["secret"],
                       sandbox=bool(cfg.get("is_sandbox")))
        _updated, _denied, _failed, _removed = 0, False, 0, 0
        _detail = []
        try:
            for gid in gids:
                try:
                    info = await _qq.get_group_info(gid)
                except Exception as e:
                    _failed += 1
                    _detail.append({"gid": gid,
                                    "reason": f"请求失败：{redact(str(e))}"})
                    continue
                if info.get("ok") and info.get("name"):
                    st.set_group_name(gid, info["name"])
                    _updated += 1
                    continue
                code = info.get("code")
                if code == 11253:
                    _denied = True
                    _detail.append({"gid": gid, "code": 11253,
                                    "reason": "官方接口未开通（11253）"})
                    continue
                # 确认「机器人已不在这个群」→ 直接删掉，不留垃圾。
                # 用户要的就是：刷新群名顺便把失效群清掉。
                if _is_gone(code, info.get("msg")):
                    try:
                        gone = st.remove_group(gid) or {}
                        _removed += 1
                        _detail.append({
                            "gid": gid, "code": code, "removed": True,
                            "reason": _group_name_fail_reason(code,
                                                              info.get("msg"))
                            + f"，已自动移除（订阅 {gone.get('subs', 0)} 条"
                            + f"、文案 {gone.get('tpl', 0)} 条）"})
                    except Exception as e:
                        _detail.append({"gid": gid, "code": code,
                                        "reason": f"移除失败：{e}"})
                    continue
                _failed += 1
                _detail.append({"gid": gid, "code": code,
                                "reason": _group_name_fail_reason(
                                    code, info.get("msg"))})
        finally:
            try:
                await _qq.close()
            except Exception:
                pass
        return _updated, _denied, _failed, _removed, _detail

    updated, denied, failed, removed, detail = run_async(_run())

    # 顺带把「合集显示成 uid」的旧数据补上 UP 主名字。
    # 以前订阅合集时没记名字，ups 缓存里也没有，界面就显示数字。
    try:
        fixed = _backfill_season_names(st)
        if fixed:
            detail.append({"gid": "-", "reason":
                           f"已补回 {fixed} 个合集的 UP 主名字"})
    except Exception:
        pass

    msg = f"已更新 {updated} 个群名"
    if denied:
        msg += "；官方「获取群信息」接口未开通（11253）"
        if bool(cfg.get("is_sandbox")):
            msg += "（也可能是沙箱环境不匹配：试试关闭「沙箱环境」再刷新）"
    if removed:
        msg += f"；{removed} 个已不在群里，自动移除"
    if failed:
        msg += f"；{failed} 个未能获取（见下方详情）"
    return jsonify({"ok": True, "updated": updated, "denied": denied,
                    "failed": failed, "removed": removed,
                    "msg": msg, "detail": detail})


@app.route("/api/group/add", methods=["POST"])
def api_group_add():
    d = request.json or {}
    try:
        gid = validate_gid(d.get("gid"))
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})
    st = get_store()
    st.ensure_group(gid, str(d.get("name") or "").strip()[:40])
    AUDIT.log("登记新群", client_ip(), gid[:20], "info")
    return jsonify({"ok": True})


@app.route("/api/group/remove", methods=["POST"])
def api_group_remove():
    """移除一个群（连同它的订阅和专属文案）。"""
    if not _auth_ok():
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    d = request.json or {}
    gid = str(d.get("gid") or "").strip()
    if not gid:
        return jsonify({"ok": False, "msg": "没给群 ID"})
    st = get_store()
    try:
        gone = st.remove_group(gid) or {}
    except Exception as e:
        return jsonify({"ok": False, "msg": f"移除失败：{redact(str(e))}"})
    AUDIT.log("移除群", client_ip(), gid[:20], "warn")
    return jsonify({"ok": True, "msg": f"已移除（订阅 {gone.get('subs', 0)} 条、"
                                       f"文案 {gone.get('tpl', 0)} 条）"})


@app.route("/api/config", methods=["POST"])
def api_config():
    """只接受白名单字段，避免被塞入任意配置（比如改掉 data_file 指向别的路径）。"""
    d = request.json or {}
    cfg = load_cfg()
    was_ready = creds_ready(cfg)

    # 文本类字段：限长，防超长 payload
    for key, maxlen in (("appid", 32), ("bili_cookie", 4096)):
        if key in d:
            cfg[key] = str(d[key] or "").strip()[:maxlen]
    if d.get("secret"):
        cfg["secret"] = str(d["secret"]).strip()[:128]


    # 消息样式：markdown（需申请权限）/ text（纯文本，一定可用）
    if isinstance(d.get("message_style"), str):
        m = str(d["message_style"]).lower().strip()
        if m in ("markdown", "text"):
            cfg["message_style"] = m

    if isinstance(d.get("image_send_mode"), str):
        m = str(d["image_send_mode"]).lower()
        if m in ("local", "url", "auto", "off", "inline"):
            cfg["image_send_mode"] = m
    # 内嵌图片压到多宽。
    # ⚠️ 默认 0 = **不压缩、用原图**（画质优先）；设成 672 才压
    #    （原图动辄 1920x1080 好几 MB，手机端加载可能失败）。
    #    不管压不压，格式都统一成 jpg —— webp 手机 QQ 支持差。
    if d.get("image_inline_width") is not None:
        try:
            _w = int(d.get("image_inline_width"))
        except (TypeError, ValueError):
            _w = None
        if _w is not None:
            cfg["image_inline_width"] = max(0, min(_w, 1920))
    # QQ markdown 图片的尺寸提示（![#宽px #高px](url)）。
    # ⚠️ 默认开：少了它手机端 QQ 不渲染图片（电脑端却正常），
    #    这个现象极易被误判成防盗链，其实平台转存是成功的。
    if d.get("image_size_hint") is not None:
        cfg["image_size_hint"] = bool(d.get("image_size_hint"))
    for _k, _dflt, _mx in (("image_size_hint_w", 672, 1920),
                           ("image_size_hint_h", 0, 1920),
                           ("image_size_hint_avatar", 160, 1024),
                           ("image_size_hint_max_h", 1200, 4096),
                           ("image_size_hint_cell", 320, 1024),
                           ("image_max_per_msg", 0, 9)):
        if d.get(_k) is not None:
            try:
                _v = int(d.get(_k))
            except (TypeError, ValueError):
                continue
            # ⚠️ 高度允许 0 =「按原图比例自动」；被钳成 1 的话就永远固定
            #    1px 高、预览彻底变形。其它字段最小值仍是 1。
            cfg[_k] = max(0 if _k in ("image_size_hint_h",
                                      "image_size_hint_max_h",
                                      "image_size_hint_cell",
                                      "image_max_per_msg") else 1,
                          min(_v, _mx))
    # 多图排版方式：只认 ratio / square / contain，其它写法一律忽略
    if d.get("image_grid_mode") is not None:
        cfg["image_grid_mode"] = _norm_grid_mode(d.get("image_grid_mode"))
    # 「沙箱环境」改动提示：以前改了必须重启机器人才生效（表现成"关不上"），
    # 现在 bot 每轮轮询会对一次配置，热切换，这里如实告诉用户。
    _sandbox_before = bool(cfg.get("is_sandbox", False))
    for key in ("is_sandbox", "notify_offline", "send_image"):
        cfg[key] = bool(d.get(key, cfg.get(key, False)))

    # 中文翻译 / 输出调试日志：
    # ⚠️ 面板上以前**没有入口**（"缺少日志翻译功能"），现在补成真实配置项。
    #   log_translate → /api/log 决定要不要把英文术语翻成中文
    #   log_debug     → bot.py 根 logger 级别 DEBUG / INFO
    if "log_translate" in d:
        cfg["log_translate"] = bool(d.get("log_translate"))
    if "log_debug" in d:
        cfg["log_debug"] = bool(d.get("log_debug"))

    # 请求超时 / 去重窗口：面板「设置→高级」里能改。
    # ⚠️ 这两个是真实配置项（config.example.yaml 里有），
    #    但以前面板上有输入框、后端白名单里没有 —— 改了不保存，
    #    看着能改其实改不动。现在补上并做范围钳制：
    #    超时太短会大面积假失败，去重窗口太小同一条内容会重复推。
    try:
        cfg["request_timeout"] = min(60, max(3, int(d.get(
            "request_timeout", cfg.get("request_timeout", 10)))))
    except (TypeError, ValueError):
        pass
    try:
        cfg["dedupe_window"] = min(86400, max(0, int(d.get(
            "dedupe_window", cfg.get("dedupe_window", 900)))))
    except (TypeError, ValueError):
        pass

    # 自检用的测试 UID。
    # ⚠️ 以前面板上有输入框、后端白名单里没有 —— 点了保存根本不落盘，
    #    前端 loadState() 一刷又用默认值覆盖输入框，表现就是"一直回弹"。
    #    只取数字、限长，防止塞进脏值。
    if "selfcheck_uid" in d:
        _m = re.search(r"\d{3,}", str(d.get("selfcheck_uid") or ""))
        if _m:
            cfg["selfcheck_uid"] = _m.group(0)[:20]

    # 最小请求间隔 / 轮询上限：真实生效（bot.py 在读），
    # ⚠️ 但以前面板没有输入框、白名单也不收 —— 只能手改 yaml。现在补上。
    try:
        cfg["min_request_interval"] = min(30.0, max(0.0, float(d.get(
            "min_request_interval", cfg.get("min_request_interval", 2.0)))))
    except (TypeError, ValueError):
        pass
    try:
        _pmin = int(cfg.get("poll_interval_min", 8) or 8)
        cfg["poll_interval_max"] = min(3600, max(_pmin, int(d.get(
            "poll_interval_max", cfg.get("poll_interval_max", 120)))))
    except (TypeError, ValueError):
        pass

    # 沙盒群：指定一个群专门用来测试（发条测试 / 立即检测只发它）
    if "sandbox_group" in d:
        raw = str(d.get("sandbox_group") or "").strip()
        try:
            cfg["sandbox_group"] = validate_gid(raw) if raw else ""
        except ValueError:
            # 校验不过就保留原值，别把配置写成垃圾
            pass

    # 文案模板：只接受已知键，值限长，空串表示"恢复默认"
    if isinstance(d.get("templates"), dict):
        tpls = dict(cfg.get("templates") or {})
        for k, v in (d.get("templates") or {}).items():
            if k in DEFAULT_TEMPLATES:
                val = str(v or "").strip()[:200]
                if val:
                    tpls[k] = val
                else:
                    tpls.pop(k, None)
        cfg["templates"] = tpls

    try:
        # ⚠️ 下限对齐配置里的 poll_interval_min（默认 8）。
        # 之前写死 15，导致 8~14 秒这个代码本就支持的区间在面板上永远设不了。
        _pmin = int(cfg.get("poll_interval_min", 8) or 8)
        cfg["poll_interval"] = min(3600, max(_pmin, int(d.get("poll_interval",
                                                              cfg.get("poll_interval", 12)))))
        cfg["slow_check_every"] = min(60, max(1, int(d.get("slow_check_every",
                                                            cfg.get("slow_check_every", 1)))))
    except (TypeError, ValueError):
        pass

    # 口令：明文输入 → 存成哈希，配置文件里不留明文
    # 约定：留空 = 不修改（避免每次保存设置把口令清掉）
    pw_changed = False
    if "web_password" in d:
        pw = str(d.get("web_password") or "").strip()
        if pw:
            cfg["web_password"] = pw if is_hashed(pw) else hash_password(pw)
            pw_changed = True

    # ⚠️ 模板只在上面处理一次（空串 = 恢复默认、未知键忽略）。
    # 这里原本还有一段重复处理，且从空表开始，会把本次没提交的自定义文案悄悄清空。

    save_cfg(cfg)
    AUDIT.log("修改配置", client_ip(),
              "凭据/口令/模板等" + ("（口令已变更）" if pw_changed else ""), "info")

    msg = "已保存。"
    if creds_ready(cfg):
        msg += "凭据有效，机器人会自动连接（看命令行窗口有没有「已上线」）。"
    else:
        msg += "但 AppID/AppSecret 还没填好，机器人不会启动。"
    if not was_ready and creds_ready(cfg):
        msg = "已保存，正在尝试启动机器人…"
    if bool(cfg.get("is_sandbox", False)) != _sandbox_before:
        msg += ("沙箱环境已切换，下一轮轮询自动生效（不用重启机器人）。"
                if not cfg.get("is_sandbox")
                else "已切到沙箱环境，下一轮轮询自动生效。")
    return jsonify({"ok": True, "msg": msg, "creds_ready": creds_ready(cfg)})


def _real_demo(kind: str, render_kind: str):
    """用真实订阅内容生成测试卡片。

    取不到就抛异常，由调用处退回 _demo —— 绝不因为开了「用真实数据」
    就让测试发不出去。
    """
    from bilibili import BiliClient
    st = get_store()
    cfg = load_cfg()
    cookie = str(cfg.get("bili_cookie") or "")
    # ⚠️ 「用真实数据」用的是**测试 UID**（自检那个），不是"第一个订阅的 UP"：
    #    用户改了自检 UID 就该看到那个 UP 的真实内容，否则开关等于没用。
    uid = str(cfg.get("selfcheck_uid") or SELFCHECK_UID)
    uname = _selfcheck_uname(uid) or "测试UP主"

    async def _pull():
        c = BiliClient(cookie)
        nm = uname
        try:
            up = await c.get_up(int(uid))
            if getattr(up, "uname", ""):
                nm = up.uname
            # ⚠️ 方法名必须是 BiliClient 真实存在的：
            #    get_videos / get_dynamics / get_season 都不存在，
            #    以前这么调必然 AttributeError → 被 except 吞掉 →
            #    永远退回占位内容，所以"用真实数据"看着像没生效。
            # ⚠️ 一律按 **kind**（原始类型）取数，不要用 render_kind：
            #    dynamic_pinned 的 render_kind 是 dynamic，
            #    拿它分支就会去取"最新动态"——于是置顶动态测试发出来的
            #    其实是一条普通新动态，全量跑一遍就是两张一样的卡片。
            if kind in ("live", "offline"):
                if not getattr(up, "room_id", 0):
                    raise ValueError("该 UP 主没有直播间")
                return await c.get_live(up.room_id), nm
            if kind == "video":
                return await c.get_latest_video(int(uid)), nm
            if kind == "dynamic":
                return await c.get_latest_dynamic(int(uid)), nm
            if kind == "dynamic_pinned":
                p = await c.get_pinned_dynamic(int(uid))
                if not p:
                    raise ValueError("没有置顶动态")
                # 双保险：bilibili 那边已置 True，这里再确认一次
                try:
                    setattr(p, "pinned", True)
                except Exception:
                    pass
                return p, nm
            if kind == "top_comment":
                # ⚠️ 置顶评论挂在**置顶动态**下面，不是"最新动态"：
                #    以前取 get_latest_dynamic，那条动态往往没有置顶评论
                #    → 必然取不到 → 静默退回占位内容，
                #    表现就是"用了真实数据但置顶评论那栏一直是假数据"。
                # ⚠️ rid / comment_type / comment_id 也要一起传：
                #    带图动态（相簿）的评论区是 type=11、oid=rid，
                #    不传就只能碰运气，命中不了就 code=-404「啥都木有」。
                d = await c.get_pinned_dynamic(int(uid))
                if not d or not getattr(d, "dyn_id", ""):
                    raise ValueError("没有置顶动态")
                cm = await c.get_pinned_comment(
                    getattr(d, "dyn_id", ""), int(uid),
                    rid=getattr(d, "rid", "") or "",
                    comment_type=int(getattr(d, "comment_type", 0) or 0),
                    comment_id=getattr(d, "comment_id_str", "") or "")
                if not cm:
                    raise ValueError("没有置顶评论")
                return cm, nm
            if kind == "season":
                # ⚠️ 走和「自检第七项」同一个共用方法：
                #   以前这里取"订阅里的第一个合集"，还把 SeasonInfo 整个
                #   交给 render —— 渲染要的是 bvid/pic/season_title/section，
                #   SeasonInfo 一个都没有，卡片字段全是空的。
                #   优先已订阅的合集，没有就自动挑测试 UID 最近更新的那个。
                sid = 0
                try:
                    for gid in (st.data.get("groups") or {}):
                        for sv in (st.seasons_of_group(gid) or []):
                            if isinstance(sv, dict) \
                                    and str(sv.get("uid") or "") == str(uid):
                                sid = int(sv.get("season_id") or 0)
                                break
                        if sid:
                            break
                except Exception:
                    sid = 0
                got = await c.newest_season_video(int(uid), sid)
                if not got or not got.get("push"):
                    raise ValueError("没有可用合集")
                return got["push"], nm
            raise ValueError("未知类型")
        finally:
            try:
                await c.close()
            except Exception:
                pass

    got = run_async(_pull())
    # _pull 一定是 (内容, 名字) 二元组
    r, nm = got if isinstance(got, tuple) else (got, uname)
    if r is None:
        raise ValueError("没取到内容")
    if isinstance(r, (list, tuple)):
        if not r:
            raise ValueError("没取到内容")
        r = r[0]
    return r, (nm or uname)


def _norm_grid_mode(v) -> str:
    """多图排版方式归一化：只认 box / ratio / square / contain，其余按默认 box。

    ⚠️ 不能直接把用户填的字符串拼进图床 URL 参数 —— 写错（比如拼进奇怪
       字符）会弄出取不到图的怪链接，表现成"多图全没了"。
    """
    _v = str(v or "").strip().lower()
    return _v if _v in ("box", "ratio", "square", "contain") else "box"


def _apply_render_cfg(cfg=None):
    """把图片相关的渲染设置应用到**本进程**的渲染层。

    ⚠️ 这是"面板改了没反应"的根因修复：notify 的尺寸提示是模块级全局
       变量，而热更新（bot.sync_cfg_from_disk）只在 **bot 进程**里跑。
       测试推送是在 **面板进程**里渲染的，从来没应用过这些设置 ——
       于是无论面板上关没关开关、把宽改成多少，测试推送永远用模块
       默认值（开 / 672），表现就是"没有功能联动"。
       现在每次渲染测试卡片前都按磁盘最新配置刷一次。
    """
    try:
        import notify as _n
    except Exception:
        return
    c = cfg or {}
    try:
        _n.set_size_hint(c.get("image_size_hint", True),
                         c.get("image_size_hint_w", 0),
                         c.get("image_size_hint_h", 0),
                         c.get("image_size_hint_avatar", 0),
                         c.get("image_size_hint_max_h", -1),
                         c.get("image_max_per_msg", -1),
                         c.get("image_size_hint_cell", -1),
                         c.get("image_grid_mode"))
    except Exception:
        pass
    try:
        _w = int(c.get("image_inline_width", 0) or 0)
    except (TypeError, ValueError):
        _w = 0
    try:
        _n.set_inline_pic_size(max(0, min(_w, 1920)), 0)
    except Exception:
        pass


@app.route("/api/test", methods=["POST"])
def api_test():
    """给某个群发一条测试卡片，确认推送链路是通的。"""
    d = request.json or {}
    try:
        gid = validate_gid(d.get("gid"))
        kind = _push_kind(d.get("kind", "live"))
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})
    cfg = load_cfg()
    if not creds_ready(cfg):
        return jsonify({"ok": False, "msg": "还没填好 AppID / AppSecret"})
    _apply_render_cfg(cfg)
    # 测试卡片不带头图，避免图床波动影响「链路是否打通」的判断
    # ⚠️ dynamic_pinned 走 render_dynamic（靠 info.pinned 区分），
    #    render() 没有这个分支，所以在这里先映射回 dynamic。
    render_kind = "dynamic" if kind == "dynamic_pinned" else kind
    # 「带 🧪 测试标记」默认开（面板上那个开关默认亮着）
    mark = d.get("mark") is not False
    # ⚠️ 「用真实数据」默认**开**：占位数据里 pic / images 全是空，
    #    卡片内嵌不到图，测试就看不出图片链路通不通。默认取测试 UID 的
    #    真实内容（封面 / 配图 / 合集视频都是真的），取不到才退回占位。
    use_real = d.get("real") is not False
    info = _demo(kind)
    uname = "测试UP主"
    real_ok = False          # 真实数据到底取到了没
    real_err = ""            # 没取到时的原因
    if use_real:
        # 用真实订阅内容：取第一个有订阅的群里的第一个 UP 主的最新内容，
        # 取不到就退回占位，绝不因为"用真实数据"而让测试发不出去。
        try:
            info, uname = _real_demo(kind, render_kind)
            real_ok = True
        except Exception as e:
            # ⚠️ 以前静默退回占位，用户勾了「用真实数据」却发出来没图的
            #    占位卡片，还以为开关坏了。现在把结果报回去，面板上写明
            #    「真实数据 / 占位内容」和失败原因。
            real_err = redact(str(e))[:120]
            info, uname = _demo(kind), "测试UP主"
    # ⚠️ 置顶动态必须带 pinned 标记：render_dynamic 靠 info.pinned 区分
    #    「📌 置顶动态」和「📝 新动态」。取不到标记就会渲染成普通新动态，
    #    于是「全部跑一遍」里会出现两张一样的动态卡片。
    if kind == "dynamic_pinned":
        try:
            setattr(info, "pinned", True)
        except Exception:
            pass
    # 下播卡片需要 uid：按钮指向 UP 主空间，不是关掉的直播间。
    try:
        _duid = int(str(cfg.get("selfcheck_uid") or SELFCHECK_UID) or 0)
    except (TypeError, ValueError):
        _duid = 0
    md, buttons = render(render_kind, info, uname,
                         cfg.get("templates") or {}, uid=_duid)
    # 图片：视频推封面、动态推动态自带的图，走富媒体单独发一条
    # （和正式推送同一条路），卡片里那条 ![](url) 要剥掉，否则群里两条图。
    # ⚠️ 以前测试卡片完全不带图，于是"视频封面 / 动态配图"这条链路
    #    根本没被测试覆盖过 —— 真出问题只有正式推送时才发现。
    # ⚠️ 图片**内嵌在卡片里**（![](url) 留在 md 中），绝不单独发一条图。
    #    以前的写法是 send_image 单独发 + strip_images 把卡片里的图剥掉，
    #    群里就成了「一条孤零零的图 + 一张没图的卡片」——这正是用户反馈的
    #    「该放图的怎么没有」和「图是插在消息内的，不是单独发出来」。
    #    现在只做一件事：卡片里该有图就有图，剩下的交给端上渲染。
    # ⚠️ send_image=false（全局关图）时仍然要剥掉，尊重配置。
    if not bool(cfg.get("send_image", True)):
        md = strip_images(md)
    # ⚠️ 占位数据（没勾「用真实数据」）里 pic / cover / images 全是空，
    #    而内嵌必须是一个真 URL（端上要能拉到），本地生成的图没有 URL、
    #    内嵌只会是个裂图，所以占位模式就是没图 —— 因此「用真实数据」
    #    默认打开，保证测试卡片拿得到真实封面/配图。
    # ⚠️ 标记加在**渲染之后**：单独一行放在卡片最顶上，然后才是卡片标题。
    #    以前加在 info.title 里，标记会跑进块引用的「标题」行，对不上。
    md = _apply_mark_md(md, mark)
    # ⚠️ 统计卡片里到底有没有图。
    #    "该放图的怎么没有"有两种完全不同的原因，以前分不清：
    #      ① 压根没取到封面/配图（占位数据本来就没图）；
    #      ② 取到了但内嵌在 markdown 里端上没渲染出来。
    #    把图片数回报给面板，用户一眼能看出是哪种，不用靠猜。
    n_img = 0
    _ilines = []
    try:
        from notify import IMG_LINE_RE
        for ln in str(md).split("\n"):
            if IMG_LINE_RE.match(ln.strip()):
                n_img += 1
                # ⚠️ 把**实际发出的**图片行原文回报给面板（最多 3 条）。
                #    「比例不对」「图没显示」这类问题，光看"有几张图"是
                #    查不出来的；把 ![#672px #1195px](...) 摆出来，用户
                #    一眼就能确认提示尺寸是不是跟着原图比例走的。
                if len(_ilines) < 3:
                    _ilines.append(ln.strip()[:120])
    except Exception:
        n_img = 0
    keyboard = build_keyboard(buttons)
    qq = QQClient(cfg["appid"], cfg["secret"], sandbox=bool(cfg.get("is_sandbox")))
    try:
        # ⚠️ 图片随卡片一起走（内嵌），这里只发这一条 markdown。
        #    不要再加 send_image 单独发图 —— 那样卡片会被 strip_images
        #    剥成没图的样子，群里多出一条孤零零的图。
        run_async(qq.send_markdown(gid, md, keyboard=keyboard))
    except Exception as e:
        return jsonify({"ok": False, "msg": f"发送失败：{redact(str(e))}"})
    finally:
        run_async(qq.close())
    AUDIT.log("测试推送", client_ip(),
              f"{gid} {PUSH_KIND_LABEL.get(kind, kind)}", "info")
    # ⚠️ 图片行也写进运行日志：面板上的「上次发送详情」有可能被忽略，
    #    而"后台看不到"是用户的原话。写进日志后，运行日志页同样查得到，
    #    且会走日志翻译（群代码→群名等）。
    for _ln in _ilines:
        AUDIT.log("图片行", client_ip(), _ln, "info")
    if use_real and not real_ok:
        return jsonify({
            "ok": True,
            "msg": (f"已发送，但没取到真实数据（{real_err}），"
                    f"发的是占位内容——占位内容本身没有封面和配图。"
                    f"需要看真实图的话，确认测试 UID 和 B站 cookie 有效"
                    f"（自检页能取到东西即可）。"),
            "src": "demo", "real": False, "imgs": n_img,
            "img_lines": _ilines})
    src = "real" if (use_real and real_ok) else "demo"
    if src == "real":
        hint = "（真实内容）"
    else:
        hint = "（占位内容，没图是正常的）"
    if n_img:
        hint += f"，卡片里有 {n_img} 张图（内嵌在卡片里）"
    elif src == "real":
        # 真取到了内容却没图：不是渲染的问题，是这条内容本身没带图，
        # 或者封面被安全白名单挡了 —— 说清楚，别让用户以为功能坏了。
        hint += "，但这条内容本身没带封面/配图"
    return jsonify({"ok": True,
                    "msg": f"测试消息已发送{hint}，去看群里有没有",
                    "src": src, "real": bool(use_real and real_ok),
                    "imgs": n_img, "img_lines": _ilines})


# 测试标记的样式：全角「」包住，不加冒号（用户定的样子："「🧪 测试」"）。
# ⚠️ 必须是**真字符** U+300C / U+300D（全角），别写成半角的 ｢｣ ——
#    用户反馈过"符号大小不一"，半角引号在中文里会明显偏窄。
# ⚠️ 单一来源：定义在 notify 里，这里只是别名，别再抄一份（抄了就会不同步）。
MARK_PREFIX = MARK_LINE


def _apply_mark_md(md: str, mark: bool) -> str:
    """给**整张卡片**加 / 去「🧪 测试」标记行。

    ⚠️ 三段坑，别再踩回去：
      1. 以前只在 mark=False 时**加**前缀，逻辑整个写反了
         （占位数据关不掉、真实数据勾了又不显示）。
      2. 以前加在 info.title 里 —— render 会把 title 拼进块引用的
         「标题」行，于是标记跑到"标题"那一行、跟 🔴 开播提醒 对不上。
         用户要的是：**单独一行放在卡片最顶上**，然后换行才是卡片标题。
      3. 占位 / 真实 / 置顶评论(dict) 三条路都必须生效，不能只走一条。
         现在统一在渲染**之后**处理，三条路自然都覆盖到。
    """
    return apply_test_mark(md, mark)


# 推送测试支持的全部类型。
# ⚠️ 比"订阅开关类型"(db.KINDS) 多：下播 / 置顶动态 / 合集是**推送**类型，
#    不是可开关的订阅项，所以不能拿 validate_kind 去校验它们
#    （之前就是这么错的：这几个按钮一律被拒）。
PUSH_KINDS = ("live", "offline", "video", "dynamic",
              "dynamic_pinned", "top_comment", "season")

# ⚠️ 日志里必须写中文。以前审计日志直接写 `kind`（live/video/dynamic…），
#    翻译器只认得 offline → "离线"，于是同一行出现
#    "测试推送 … dynamic/video/离线/live" 这种**中英混杂**，
#    用户看到就是"这日志没翻译"。db.KIND_LABEL 只覆盖 4 种订阅类型，
#    推送测试有 7 种，所以这里单独列一份全的。
PUSH_KIND_LABEL = {"live": "开播", "offline": "下播", "video": "投稿",
                   "dynamic": "动态", "dynamic_pinned": "置顶动态",
                   "top_comment": "置顶评论", "season": "合集"}

# 这几类推送本来就该带图：直播/下播带封面、投稿/合集带封面、动态带配图。
# 置顶评论（top_comment）没有图的概念，不在里面。
# ⚠️ 图片现在是**内嵌**在卡片里的（![](url) 留在 md 中），因此这里只是
#    「哪些类型该有图」的说明性清单，不再用来补占位图 —— 内嵌必须是
#    端上能拉到的真 URL，本地生成的图没有 URL，补上去只会是个裂图。


def _push_kind(raw) -> str:
    """校验推送测试的类型。"""
    s = str(raw or "").strip().lower()
    if s not in PUSH_KINDS:
        raise ValueError("类型只能是 " + " / ".join(PUSH_KINDS))
    return s


def _demo(kind: str):
    """生成一份假数据，用来发测试卡片。

    ⚠️ 必须覆盖 PUSH_KINDS 全部 7 种 —— 之前只有 live/video/dynamic
       三种，于是面板上「下播 / 置顶动态 / 置顶评论 / 合集」这几个
       测试按钮后端一律拒绝，看起来就是"点了没反应"。
    ⚠️ 这里**不要**往 title 里塞「🧪 测试」：title 会被 render 拼进
       块引用的「标题」行。标记统一由 _apply_mark_md 在渲染后加在最顶上。
    """
    from bilibili import DynamicInfo, LiveInfo, SeasonPush, VideoInfo
    title = "这是一条测试提醒"
    if kind == "video":
        return VideoInfo(bvid="BV1GJ411x7h7", title=title,
                         pic="", desc="看到这条说明推送链路是通的")
    if kind == "season":
        # 合集：视频本体 + 所属合集名 + 小节名
        return SeasonPush(bvid="BV1GJ411x7h7", title=title, pic="",
                          season_title="测试合集", section="测试小节")
    if kind == "dynamic":
        return DynamicInfo(dyn_id="123456789012345678", title=title,
                           text="这是动态正文第一行\n这是第二行", images=[])
    if kind == "dynamic_pinned":
        # 置顶动态：靠 pinned=True 走 dynamic_pinned 文案
        return DynamicInfo(dyn_id="123456789012345678", title=title,
                           text="这是置顶动态正文", images=[], pinned=True)
    if kind == "top_comment":
        # 置顶评论：render_top_comment 收的是 dict
        return {"content": "这是置顶评论的正文，看到这条说明链路是通的",
                "url": "https://www.bilibili.com/opus/123456789012345678"}
    if kind == "offline":
        return LiveInfo(room_id=1, live_status=0, title=title,
                        area="测试分区", online=0, cover="")
    return LiveInfo(room_id=1, live_status=1, title=title,
                    area="测试分区", online=12345, cover="")


@app.route("/api/audit")
def api_audit():
    """最近安全事件：登录、改配置、删订阅等敏感操作。"""
    if not _auth_ok():
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    import time as _t
    out = []
    for e in AUDIT.recent(20):
        ts = e.get("ts") or 0
        try:
            t = _t.strftime("%H:%M", _t.localtime(float(ts)))
            ago = _ago(ts)
        except Exception:
            t, ago = "", ""
        out.append({
            "event": e.get("event", ""),
            "ip": e.get("ip", ""),
            "detail": e.get("detail", ""),
            "level": e.get("level", "info"),
            "time": (ago or t),
        })
    return jsonify({"ok": True, "items": out})


def _ago(ts):
    """把时间戳说成「3 分钟前」这种人话。"""
    try:
        import time as _t
        d = _t.time() - float(ts)
    except Exception:
        return ""
    if d < 60:
        return "刚刚"
    if d < 3600:
        return "%d 分钟前" % int(d / 60)
    if d < 86400:
        return "%d 小时前" % int(d / 3600)
    return "%d 天前" % int(d / 86400)


def _media_stats() -> dict:
    """图片缓存的真实情况。

    分「长期」和「临时」两块统计：长期是 UP 头像这类会复用的，
    临时是推送封面（发完即删）。以前只给一个总数，
    所以面板上永远是 0 —— 看不出到底有没有在缓存。
    """
    import os as _os
    import media as _media
    try:
        st = _media.cache_stats() or {}
    except Exception:
        st = {}
    ln = int(st.get("long_count") or 0)
    tn = int(st.get("tmp_count") or 0)
    lb = int(st.get("long_bytes") or 0)
    tb = int(st.get("tmp_bytes") or 0)

    def _sz(b):
        mb = b / 1024 / 1024
        return ("%.1f MB" % mb) if mb >= 0.1 else ("%d KB" % (b / 1024))

    # 最近 24 小时的图片相关失败：从心跳 notes 里数，取不到就是 0
    fail = 0
    try:
        notes = (heartbeat.read() or {}).get("notes") or {}
        for _k, v in notes.items():
            t = (v or {}).get("text", "") if isinstance(v, dict) else ""
            if t and ("图片" in t or "封面" in t or "媒体" in t):
                fail += 1
    except Exception:
        fail = 0

    # 最近用到过的图片（内嵌模式不落盘，只有这份清单能反映"用了哪些图"）
    try:
        from notify import IMG_ALT as _alt
    except Exception:
        _alt = {}
    recent = []
    for it in (_media.recent_list(12) or []):
        if not isinstance(it, dict):
            continue
        k = str(it.get("kind") or "")
        recent.append({
            "url": str(it.get("url") or ""),
            "kind": k,
            "kind_text": _alt.get(k) or "图片",
            "name": str(it.get("name") or ""),
            "ts": int(it.get("ts") or 0),
        })

    cfg = load_cfg()
    return {
        "ok": True,
        "count": ln + tn,
        "recent": recent,
        # 图床域名：手机端看不到的图，多半要靠它去开放平台报备
        "hosts": _media.recent_hosts(8),
        "size_text": _sz(lb + tb),
        "fail_24h": fail,
        # 缓存到底存在哪 —— 不给路径的话，用户没法自己去确认
        "dir": _media.CACHE_DIR,
        "exists": _os.path.isdir(_media.CACHE_DIR),
        "long_count": ln, "long_size": _sz(lb),
        "tmp_count": tn, "tmp_size": _sz(tb),
        "send_image": bool(cfg.get("send_image", True)),
        "mode": str(cfg.get("image_send_mode", "inline")),
        "ttl_text": str(_media._DEFAULT_FILE_INFO_TTL // 60) + " 分钟",
        # 短期图（推送封面）保留多久 —— 现在留 5 分钟方便连发多群复用，
        # 面板要写清楚，否则用户会以为缓存没被清理
        "tmp_ttl_text": (str(_media.tmp_ttl()) + " 秒"
                         if _media.tmp_ttl() > 0 else "发完即删"),
    }


@app.route("/api/media")
def api_media():
    """图片缓存：几张、多大、存在哪、最近失败几次。"""
    try:
        return jsonify(_media_stats())
    except Exception as e:
        return jsonify({"ok": False, "msg": "读取失败：%s" % redact(str(e))})


@app.route("/api/media/clear", methods=["POST"])
def api_media_clear():
    """清空图片缓存。下次推送会重新下载，不影响订阅数据。"""
    if not _auth_ok():
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    try:
        import media as _media
        _media.purge_long()
        _media.sweep_tmp(0)
        AUDIT.log("清空缓存", client_ip(), "图片缓存", "info")
        return jsonify({"ok": True, "msg": "已清空图片缓存", **_media_stats()})
    except Exception as e:
        return jsonify({"ok": False, "msg": "清空失败：%s" % redact(str(e))})


def _test_png(color=(0x2E, 0x9F, 0x6E)) -> bytes:
    """生成一张纯色 PNG，用来测富媒体上传链路。

    为什么不用现成图片：打包里不带二进制素材，而且测试要的是
    「上传链路通不通」，不是图片好不好看。纯标准库（zlib+struct）就能画。
    """
    import struct
    import zlib
    w = h = 240
    rows = b""
    for y in range(h):
        # 中间画一条更亮的横带，方便肉眼确认图没传歪
        band = abs(y - h // 2) < 12
        px = bytes(color) if not band else bytes(
            (min(255, color[0] + 60), min(255, color[1] + 60),
             min(255, color[2] + 60)))
        rows += b"\x00" + px * w
    def _chunk(tag, data):
        c = tag + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(rows, 9)) + _chunk(b"IEND", b""))


@app.route("/api/media/probe")
def api_media_probe():
    """探测图床域名：不带 Referer 时能不能取到图。

    用于定位「电脑上看得见图、手机上没有」——多数是图床防盗链，
    平台侧拉图不带 Referer 就被 403，本地根本复现不了。
    """
    if not _auth_ok():
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    try:
        import media as _media
        return jsonify({"ok": True, "items": _media.probe_hosts()})
    except Exception as e:
        return jsonify({"ok": False, "msg": "探测失败：%s" % redact(str(e))})


@app.route("/api/media/test-upload", methods=["POST"])
def api_media_test_upload():
    """测试上传：造一张图走完「上传 → 发送」全流程，验证富媒体链路。

    这是真发到沙盒群的，不是干跑 —— 只有群里真看到图，才算链路通。
    """
    if not _auth_ok():
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    cfg = load_cfg()
    if not creds_ready(cfg):
        return jsonify({"ok": False, "msg": "还没填好 AppID / AppSecret"})
    gid = str(cfg.get("sandbox_group") or "").strip() or str(
        (request.json or {}).get("gid") or "").strip()
    if not gid:
        return jsonify({"ok": False, "msg": "还没设置沙盒群，不知道发到哪"})
    try:
        import base64 as _b64
        import media as _media
        data = _test_png()
        b64 = _b64.b64encode(data).decode()
        qq = QQClient(cfg["appid"], cfg["secret"],
                      sandbox=bool(cfg.get("is_sandbox")))
        try:
            r = run_async(qq.upload_group_file(gid, file_type=1,
                                               file_data=b64,
                                               srv_send_msg=False))
            fi = (r or {}).get("file_info") or ""
            ttl = (r or {}).get("ttl") or 0
            if not fi:
                return jsonify({"ok": False,
                                "msg": "上传没拿到 file_info：" +
                                       redact(str(r)[:200])})
            run_async(qq.send_media(gid, fi))
            # 顺手留一份到本地缓存目录，让「缓存图片」计数真的动起来，
            # 否则测完还是 0，看不出缓存到底有没有在工作
            try:
                _media.save_tmp(data)
            except Exception:
                pass
        finally:
            run_async(qq.close())
        AUDIT.log("测试上传", client_ip(), f"{gid} 图片 {len(data)}B", "info")
        return jsonify({"ok": True,
                        "msg": "已向沙盒群发送一张测试图，去看群里有没有",
                        "file_info": fi[:40] + ("…" if len(fi) > 40 else ""),
                        "ttl": ttl, "bytes": len(data), **_media_stats()})
    except Exception as e:
        return jsonify({"ok": False, "msg": "测试上传失败：" + redact(str(e))})


def _keep_pids() -> set:
    """「不该被当成残留」的进程号。

    残留 = 本项目进程里，去掉下面这些之后剩下的：
      1. 面板自己
      2. 心跳里那个 bot —— 正在正常跑的机器人
      3. 监督进程 start.py —— 拉起面板和机器人的管家
      4. 本进程的父进程（--parent 传进来的，通常就是 3）

    ⚠️ 3 和 4 以前漏了：start.py 明明在正常干活，却被算进残留数，
       于是运行状态页长期显示「进程 1 · 1 个残留，建议清理」；
       更糟的是点一下「清理」，杀掉的正是管家 —— 整个程序当场退出。
    """
    import os as _os
    keep = {int(_os.getpid())}
    hb = heartbeat.read() or {}
    try:
        bpid = int(hb.get("pid") or 0)
        if bpid:
            keep.add(bpid)
    except Exception:
        pass
    try:
        ppid = int(globals().get("PARENT_PID") or 0)
        if ppid:
            keep.add(ppid)
    except Exception:
        pass
    try:
        import cleanup as _cl
        for p in (_cl.supervisor_pids() or []):
            keep.add(int(p))
    except Exception:
        pass
    return keep


def _proc_info() -> dict:
    """本项目进程数与残留数。

    残留 = 在跑、但不在 keep（面板自己 / 正常 bot / 监督进程）里的进程。
    多开一个实例就会同时监听同一个群 → 一条动态推好几次，所以必须能看见。
    """
    import os as _os
    try:
        import cleanup as _cl
        # ⚠️ 必须用 all_ours()（PID 文件 + 命令行 + 端口三条路汇总），
        #    不能只用 list_ours()。后者在 Windows 上只有 PowerShell 一条
        #    路，失败就返回空 —— 于是"进程还在跑"却显示残留 0，
        #    清理按钮也没有目标，表现就是"杀了没杀干净"。
        ours = _cl.all_ours() or []
    except Exception:
        return {"total": 1, "leftover": 0, "leftover_pids": [],
                "unknown": True}
    keep = _keep_pids()
    pids = []
    for row in ours:
        try:
            pid = int(row[0] if isinstance(row, (list, tuple)) else row)
        except Exception:
            continue
        pids.append(pid)
    left = [x for x in pids if x and x not in keep]
    return {"total": len(pids) or 1, "leftover": len(left),
            "leftover_pids": left[:10], "unknown": False}


@app.route("/api/runstate")
def api_runstate():
    """运行状态页要用的真实参数（进程 / 轮询 / 推送 / 心跳 / 面板）。"""
    cfg = load_cfg()
    hb = heartbeat.read() or {}
    stt = heartbeat.status()
    poll = hb.get("poll") if isinstance(hb.get("poll"), dict) else {}
    # 实际间隔优先用机器人每轮上报的值，拿不到才回配置里的基线值
    cur = poll.get("current") or cfg.get("poll_interval", 30)
    base = poll.get("base") or cfg.get("poll_interval", 30)
    mn = poll.get("min") or cfg.get("poll_interval_min", 8)
    mx = poll.get("max") or cfg.get("poll_interval_max", 120)
    try:
        cur, base, mn, mx = int(cur), int(base), int(mn), int(mx)
    except Exception:
        cur, base, mn, mx = 30, 30, 8, 120
    started = 0.0
    try:
        started = float(hb.get("started_at") or 0)
    except Exception:
        started = 0.0
    return jsonify({
        "ok": True,
        "proc": _proc_info(),
        "poll": {"current": cur, "base": base, "min": mn, "max": mx,
                 "backing_off": bool(poll.get("backing_off")),
                 "auth_hold": bool(poll.get("auth_hold")),
                 "risk_hits": int(poll.get("risk_hits") or 0)},
        "pushes": int(hb.get("pushes") or 0),
        "pushes_today": int(hb.get("pushes_today") or 0),
        "errors": int(hb.get("errors") or 0),
        "errors_today": int(hb.get("errors_today") or 0),
        "polls": int(hb.get("polls") or 0),
        "started_at": started,
        "uptime_text": _ago(started) if started else "",
        "last_push_at": float(hb.get("last_push_at") or 0),
        "last_push": str(hb.get("last_push") or ""),
        "last_error": redact(str(hb.get("last_error") or "")),
        "heartbeat": {"age": stt.get("age"), "alive": stt.get("alive"),
                      "detail": stt.get("detail", "")},
        # 用请求里的真实地址，别写死 8088 —— 换端口启动时不至于显示错
        "panel": {"addr": str(request.host or "127.0.0.1:8088"),
                  "running": True},
        "bot_running": bool(stt.get("alive")),
        "bot_name": str(hb.get("bot_name") or ""),
        "creds_ready": creds_ready(cfg),
    })


@app.route("/api/clean-procs", methods=["POST"])
def api_clean_procs():
    """清理残留进程（保留面板自己 + 正在正常运行的机器人）。

    ⚠️ 运行状态页以前只提示「N 个残留，建议清理」却**没有清理按钮**，
       用户只能关掉整个程序重开 —— 体感就是"杀了没杀干净"。
    ⚠️ 只清"残留"：把正在跑的机器人也杀了，就变成"清理一次就掉线"。
    """
    if not _auth_ok():
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    try:
        import cleanup as _cl
        # ⚠️ keep 必须包含监督进程 start.py，否则点一次「清理」就把管家
        #    杀了，面板和机器人一起没 —— 表现为"清理一次就掉线"。
        keep = _keep_pids()
        n = _cl.kill_ours(keep=keep)
        left = [p for p in (_cl.all_ours() or []) if int(p) not in keep]
        AUDIT.log("清理残留进程", client_ip(),
                  f"关掉 {n} 个，还剩 {len(left)} 个", "info")
        return jsonify({"ok": True, "n": int(n), "left": len(left),
                        "msg": (f"已关掉 {n} 个残留进程"
                                + (f"，还有 {len(left)} 个没关掉"
                                   if left else ""))
                        if n else "没有残留进程，不用清理"})
    except Exception as e:
        return jsonify({"ok": False,
                        "msg": "清理失败：" + redact(str(e))})


@app.route("/api/system/restart", methods=["POST"])
def api_system_restart():
    """让机器人重新登录一次。

    面板拿不到 bot 子进程的 PID（那是 start.py 起的），所以这里
    不直接杀进程 —— 只在 start.py 看得见的地方放一个标记文件，
    由它下一轮循环去处理，避免面板越过父进程乱杀。
    """
    if not _auth_ok():
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    try:
        import os as _os
        with open(paths.run_file(".restart_bot"), "w",
                  encoding="utf-8") as f:
            f.write(str(int(time.time())))
        AUDIT.log("重启机器人", client_ip(), "已请求", "info")
        return jsonify({"ok": True, "msg": "已请求重启，稍等几秒机器人会重新连接"})
    except Exception as e:
        return jsonify({"ok": False, "msg": "请求失败：%s" % redact(str(e))})


@app.route("/api/security")
def api_security():
    """面板安全状态：暴露面、口令强度、失败次数、最近安全事件。"""
    cfg = load_cfg()
    host = str(cfg.get("web_host", "127.0.0.1"))
    pw = str(cfg.get("web_password") or "")
    level, msg = bind_risk(host, bool(pw))
    ip = client_ip()
    return jsonify({
        "ok": True,
        "host": host,
        "level": level,
        "msg": msg,
        "has_password": bool(pw),
        "password_hashed": is_hashed(pw),
        "login_fails": LIMITER.fails(ip),
        "login_locked": LIMITER.remaining(ip),
        "my_ip": ip,
        "authed": authed(),
        "events": AUDIT.recent(20),
        "checks": [
            {"name": n, "level": lv, "detail": d}
            for lv, n, d in _sec_report()
        ],
    })


def _sec_report():
    try:
        from security import security_report
        return [(lv, n, d) for lv, n, d in security_report()]
    except Exception:
        return []


@app.route("/api/security/password", methods=["POST"])
def api_set_password():
    """设置 / 修改 / 清除访问口令。"""
    d = request.json or {}
    # ⚠️ 两套字段名都要认 —— 这是「设置口令 / 清除口令两个按钮都是死的」的根因。
    # 前端（app.js）历史上发的是 {password, clear:true}，而这里读的是
    # {new, action}：字段名对不上，new 恒为空，于是任何提交都撞上
    # 「口令至少 6 位」，设置不了也清不掉。现在两套都接受。
    action = str(d.get("action") or "").lower()
    if d.get("clear"):
        action = "clear"
    if not action:
        action = "set"
    cfg = load_cfg()
    ip = client_ip()

    if action == "clear":
        # 清除口令 = 拆掉门锁，比改口令后果更严重，门槛只能更高不能更低。
        # 以前这里只查「有没有登录态」，意思是"已登录就免验原口令"——谁手里
        # 有一枚有效会话就能把口令拆掉。现在与修改口令同一标准：只要已经设过
        # 口令，清除一律先验证原口令，不再看请求来源、也不再看是否登录。
        if need_auth():
            old_c = str(d.get("old") or d.get("password") or "")
            if not verify_password(old_c, str(cfg.get("web_password") or "")):
                LIMITER.note_fail(ip)
                AUDIT.log("清除口令失败", ip, "原口令不正确", "warn")
                return jsonify({
                    "ok": False,
                    "msg": "原口令不对（清除口令必须先验证原口令）"}), 401
        cfg.pop("web_password", None)
        save_cfg(cfg)
        # 世代号 +1：既然改成免登录，此前凭口令进来的会话也该作废，
        # 免得清完口令旧令牌还挂着。
        bump_auth_gen()
        AUDIT.log("清除口令", ip, "面板改为免登录", "warn")
        return jsonify({"ok": True, "msg": "已清除口令，面板现在无需登录即可访问。"})

    new = str(d.get("new") or d.get("password") or "")
    old = str(d.get("old") or "")
    if len(new) < 6:
        return jsonify({"ok": False, "msg": "口令至少 6 位"})

    # 还没设口令时，只允许本机来设，防止别人抢先设一个把你挡在门外
    if not need_auth() and not is_loopback(client_ip()):
        AUDIT.log("设置口令被拒", client_ip(), "非本机首次设置", "warn")
        return jsonify({"ok": False, "msg": "首次设置口令只允许在本机操作"}), 403

    # ⚠️ 只要已经设过口令，**就必须校验旧口令** —— 不管当前是不是登录状态。
    # 以前写成 `if need_auth() and not authed()`，意思是"已登录就不用验旧口令"：
    # 谁手里有一枚有效会话（比如别人用过的电脑、借去的令牌、清口令前的旧 cookie），
    # 就能直接把口令改掉，把真正的主人锁在门外，而主人这边连个提示都没有。
    # 改口令属于"改门锁"，必须证明自己知道原来的锁芯。
    if need_auth():
        if not verify_password(old, str(cfg.get("web_password") or "")):
            LIMITER.note_fail(ip)
            AUDIT.log("改口令失败", ip, "旧口令不正确", "warn")
            return jsonify({
                "ok": False,
                "msg": "原口令不对（修改口令必须先验证原口令，即使当前已登录）"}), 401

    # 记下设置前的状态：True 表示这次是「首次设置」（此前面板免登录）
    _was_open = not need_auth()

    cfg["web_password"] = hash_password(new)
    save_cfg(cfg)
    LIMITER.note_ok(ip)
    AUDIT.log("设置口令", ip, "已加密保存", "info")

    # ⚠️ 首次设置**绝不能**下发登录令牌。
    # 以前这里无条件 set_cookie(issue_token(...))，后果是：设完口令当场白拿一个
    # 7 天有效的免登录会话 —— 登录页永远不会出现，口令形同虚设；连带把上面
    # 「首次设置只允许本机操作」这道防护也抵消掉了（本机设完即刻拿到令牌）。
    # 正确做法是让他用刚设的口令登录一次，证明确实是本人在设。
    if _was_open:
        # 世代号 +1：把此前面板免登录期间可能残留的任何令牌全部作废。
        # 这一步对老用户尤其关键 —— 以前设置口令会白送一枚 7 天令牌，
        # 光靠"这次不下发"治不了已经发出去的那些。
        bump_auth_gen()
        AUDIT.log("设置口令", ip, "首次设置，要求重新登录", "info")
        return jsonify({
            "ok": True,
            "need_login": True,
            "msg": "口令已设置并加密保存（配置文件里只会看到 pbkdf2 开头的哈希）。"
                   "请刷新页面，用刚设的口令登录。请自己记住，忘了可以用 "
                   "python security.py --hash 新口令 手动替换。"})

    # 修改口令：前面已校验过旧口令，确认是本人，保持登录并续期。
    resp = make_response(jsonify({
        "ok": True,
        "msg": "口令已更新并加密保存（配置文件里只会看到 pbkdf2 开头的哈希）。"
               "请自己记住，忘了可以用 python security.py --hash 新口令 手动替换。"}))
    # 世代号 +1：改口令之后，此前所有会话（含其他设备）立即失效，
    # 再按新世代号给当前这个已验证身份的会话补发一枚。
    resp.set_cookie(COOKIE_NAME,
                    issue_token(app.secret_key, gen=bump_auth_gen()),
                    max_age=7 * 86400, httponly=True, samesite="Strict",
                    secure=request.is_secure)
    return resp


# ---------------- B 站登录 ----------------
# 扫码要跨多次请求（生成 → 轮询），而 aiohttp session 绑在事件循环上，
# 所以整段流程放在独立线程里跑，HTTP 接口只读写状态字典。
#
# epoch（世代号）：每次点「重新登录」就 +1。旧线程发现自己的 epoch
# 不是最新就立刻退出，且它的任何结果都会被丢弃 ——
# 这样上一轮失败的报错绝不会"穿越"到新一轮，
# 表现为「还没开始扫就弹出上一次的错误」。
_login = {"running": False, "qr": "", "status": "", "done": False,
          "error": "", "result": None, "epoch": 0, "qr_at": 0.0}

# 浏览器登录（推荐方式）：开一个真的浏览器窗口让用户登录，
# 绕开 B 站扫码接口的风控（那个接口经常拿不到 SESSDATA）。
# 面板跑在本机，所以这里起的浏览器窗口就出现在用户桌面上。
_blogin = {"running": False, "done": False, "ok": False, "stage": "",
           "msg": "", "epoch": 0, "started_at": 0.0, "cancel": False,
           "timeout": 180}


def _epoch_ok(state: dict, mine: int) -> bool:
    """旧线程用：自己的世代号还是最新的吗。"""
    return state.get("epoch") == mine


# ⚠️ 扫码登录（原 /api/bili/qrcode、/api/bili/poll）已于 v2.2.0 整条删除：
#   B 站扫码接口经常被风控，返回残缺 cookie（面板显示已登录、接口一直 -352），
#   前端按钮早就摘掉了。现在只有「🌐 浏览器登录」和「手动粘贴 Cookie」两条路。


def _run_browser_login(epoch: int):
    """后台线程：开一个真浏览器让用户登录，登录完自动保存 cookie。"""
    def stage(m):
        if _blogin.get("epoch") == epoch:
            _blogin["stage"] = m

    try:
        import bili_browser_login as BBL
    except ImportError:
        _blogin.update(done=True, ok=False, running=False,
                       msg="缺少 bili_browser_login 模块，请更新到最新版")
        return

    try:
        # 必须走同步入口 run()：
        # browser_login 是 async 函数，直接调用只返回协程、不会执行，
        # 浏览器永远打不开，进度一直停在"准备中…"。
        res = BBL.run(
            timeout=int(_blogin.get("timeout") or 180),
            on_stage=stage,
            should_cancel=lambda: bool(_blogin.get("cancel")))
    except BBL.BrowserLoginError as e:
        if _blogin.get("epoch") == epoch:
            _blogin.update(done=True, ok=False, running=False, msg=str(e))
        return
    except Exception as e:
        if _blogin.get("epoch") == epoch:
            _blogin.update(done=True, ok=False, running=False,
                           msg=f"{type(e).__name__}: {e}")
        return

    if _blogin.get("epoch") != epoch:
        return

    cs = res.get("cookie_str") or ""
    if not cs:
        _blogin.update(done=True, ok=False, running=False,
                       msg="没读到 cookie")
        return

    # 浏览器给的过期时间只作备选：SESSDATA 里那个才是 B 站自己写的准信儿
    exp, _est = bili_login.effective_expires(
        cs, float(res.get("expires") or 0))
    cfg = load_cfg()
    cfg["bili_cookie"] = cs
    cfg["bili_cookie_ts"] = time.time()
    cfg["bili_cookie_expires"] = exp
    save_cfg(cfg)
    AUDIT.log("B站浏览器登录", client_ip(), "成功获取 cookie", "info")

    # 顺便确认登的是哪个号
    acc = {}
    try:
        acc = run_async(bili_login.account_info(cs)) or {}
    except Exception:
        acc = {}

    _blogin.update(done=True, ok=True, running=False,
                   msg="登录成功，cookie 已保存",
                   account={"ok": bool(acc.get("ok")),
                            "uname": acc.get("uname", ""),
                            "mid": acc.get("mid", 0),
                            "level": acc.get("level", 0),
                            "vip": bool(acc.get("vip")),
                            "vip_label": acc.get("vip_label", "")},
                   expires_str=bili_login.expired_info(exp)["expires_str"]
                   if exp else "")


@app.route("/api/bili/browser-login", methods=["POST"])
def api_bili_browser_login():
    """打开一个浏览器窗口让用户登录 B 站（推荐方式）。

    扫码接口（qrcode/poll）经常被风控拦掉拿不到 SESSDATA，
    浏览器方式绕开了那一步，成功率最高。
    面板跑在本机，所以窗口就开在用户桌面上。
    """
    if _blogin["running"]:
        return jsonify({"ok": True, "already": True,
                        "msg": "已经在登录中，请看打开的浏览器窗口"})

    epoch = _blogin.get("epoch", 0) + 1
    _blogin.update(running=True, done=False, ok=False, stage="准备中…",
                   msg="", epoch=epoch, started_at=time.time(), cancel=False)

    threading.Thread(target=_run_browser_login, args=(epoch,),
                     daemon=True).start()
    # 看门狗：万一后台线程卡死（比如浏览器内核正在下载），
    # 也要在超时后把状态置为失败，否则前端会永远转圈。
    def watchdog(ep):
        total = int(_blogin.get("timeout") or 180) + 90
        for _ in range(total):
            time.sleep(1)
            if _blogin.get("epoch") != ep or not _blogin.get("running"):
                return
        if _blogin.get("epoch") == ep and _blogin.get("running"):
            _blogin.update(done=True, ok=False, running=False,
                           msg="登录超时（浏览器窗口可能没弹出）。\n"
                               "请用下方的「手动粘贴 cookie」。")
    threading.Thread(target=watchdog, args=(epoch,), daemon=True).start()
    return jsonify({"ok": True, "msg": "已打开浏览器，请在里面登录 B 站"})


@app.route("/api/bili/browser-cancel", methods=["POST"])
def api_bili_browser_cancel():
    """取消正在进行的浏览器登录。"""
    if not _blogin.get("running"):
        return jsonify({"ok": True, "msg": "当前没有进行中的登录"})
    _blogin["cancel"] = True
    AUDIT.log("取消B站浏览器登录", client_ip(), "", "info")
    return jsonify({"ok": True, "msg": "已请求取消"})


@app.route("/api/bili/browser-status")
def api_bili_browser_status():
    """浏览器登录进度。前端每 2 秒查一次。"""
    return jsonify({
        "ok": True,
        "running": bool(_blogin.get("running")),
        "done": bool(_blogin.get("done")),
        "success": bool(_blogin.get("ok")),
        "stage": _blogin.get("stage", ""),
        "msg": _blogin.get("msg", ""),
        "account": _blogin.get("account", {}),
        "expires_str": _blogin.get("expires_str", ""),
        "waited": int(time.time() - float(_blogin.get("started_at") or time.time())),
        "timeout": int(_blogin.get("timeout") or 180),
        "remaining": max(0, int(float(_blogin.get("timeout") or 180) -
                               (time.time() - float(_blogin.get("started_at")
                                                   or time.time())))),
    })


def friendly_login_error(msg: str) -> str:
    """把扫码登录的报错翻成人话 + 给出可执行的办法。"""
    m = (msg or "").lower()
    if "no module named" in m:
        if "pil" in m or "pillow" in m:
            return ("缺 Pillow（图片库）。已内置降级方案，更新到最新版即可；"
                    "想装也可以：python -m pip install pillow")
        import re as _re
        mm = _re.search(r"named '([^']+)'", msg or "")
        mod = mm.group(1) if mm else "对应依赖"
        return (f"缺少依赖 {mod}。执行：python -m pip install {mod}"
                "，或双击「安装依赖.bat」")
    if "403" in m or "policy" in m or "denied" in m:
        return "B 站拒绝了请求（网络环境被限制）。换个网络，或用下方手动粘贴 cookie。"
    if "timed out" in m or "timeout" in m:
        return "连接超时。检查网络后重试；仍不行就用下方手动粘贴 cookie。"
    if "cannot connect" in m or "dns" in m:
        return "连不上 B 站。检查网络，或用下方手动粘贴 cookie。"
    if "-352" in m or "风控" in m:
        return "触发 B 站风控。稍等再试，或用下方手动粘贴 cookie。"
    if not msg:
        return ""
    return "如果重试仍失败，可用下方「手动粘贴 cookie」。"


def _bili_login_state(cookie: str, acc: dict) -> tuple:
    """把「B站 到底认不认」归成三种状态。

    ⚠️ 以前 has_login 只看本地字符串里有没有 "SESSDATA=" —— 那是本地判断。
    cookie 过期、被风控踢下线、粘了半串，它照样是 True，
    于是面板显示「已登录」而接口一直 -352/412，前后端说法完全相反。
    这里以 B站 自己的回答为准：
        logged  = B站 说已登录
        invalid = B站 明确说无效/未登录
        unknown = 我们这边没问到（网络错误等），不能当成已登录也不能当成失效
    """
    if not cookie or "SESSDATA=" not in cookie:
        return "none", ""
    if acc.get("ok"):
        return "logged", ""
    msg = str(acc.get("msg") or "")
    if not msg:
        return "unknown", "没能向 B站 求证"
    if ("无效" in msg or "未登录" in msg or "code=-101" in msg):
        return "invalid", msg
    return "unknown", msg


def _sessdata_suspicious(cookie: str) -> bool:
    """这段 SESSDATA 看着像不像占位符/半串（长得对但根本用不了）。"""
    import re as _re
    m = _re.search(r"SESSDATA\s*=\s*([^;]*)", cookie or "")
    if not m:
        return False
    v = (m.group(1) or "").strip()
    if len(v) < 10:
        return True
    return bool(_re.search(r"x{3,}|你的|your_|请填写|example|xxx", v, _re.I))


@app.route("/api/bili/cookie")
def api_bili_cookie():
    """当前 cookie 状态：是否有效、是谁、还剩多久。"""
    cfg = load_cfg()
    cookie = str(cfg.get("bili_cookie") or "")
    # 真实过期时间优先从 cookie 自带的 SESSDATA 解 —— 配置里那个可能是
    # 旧版留下的 30 天估算，那样面板会一直显示"还剩 29 天"却永远不动。
    # 解到的真值回写一份，之后离线也能用。
    exp, estimated = bili_login.effective_expires(
        cookie, float(cfg.get("bili_cookie_expires") or 0))
    if exp > 0 and abs(exp - float(cfg.get("bili_cookie_expires") or 0)) > 60:
        try:
            cfg["bili_cookie_expires"] = exp
            save_cfg(cfg)
        except Exception:
            pass
    info = bili_login.expired_info(exp) if exp else None
    has_login = "SESSDATA=" in cookie

    # 已登录时顺便查一下"登的是哪个号"，用户最关心这个
    acc = {}
    if has_login:
        try:
            acc = run_async(bili_login.account_info(cookie)) or {}
        except Exception:
            acc = {}
    state, reason = _bili_login_state(cookie, acc)

    return jsonify({
        "ok": True,
        "has_cookie": bool(cookie),
        "has_login": has_login,
        "state": state,             # logged / invalid / unknown / none
        "reason": reason,
        "suspicious": _sessdata_suspicious(cookie),
        "expires_str": info["expires_str"] if info else "",
        # 估算值要标明，否则"还剩 29 天"会被当成准信儿
        "expires_estimated": bool(estimated),
        "expires_text": (info["text"] + "（估算）") if (info and estimated)
                        else (info["text"] if info else "未知"),
        "expired": info["expired"] if info else False,
        "soon": info["soon"] if info else False,
        "days_left": info["days_left"] if info else None,
        "got_at": time.strftime("%Y-%m-%d %H:%M",
                                time.localtime(float(cfg.get("bili_cookie_ts") or 0)))
                  if cfg.get("bili_cookie_ts") else "",
        "account": {
            "ok": bool(acc.get("ok")),
            "uname": acc.get("uname", ""),
            "mid": acc.get("mid", 0),
            "face": acc.get("face", ""),          # B 站头像（官方返回）
            "pendant": acc.get("pendant", ""),    # 头像框（有装扮才有）
            "level": acc.get("level", 0),
            "vip": bool(acc.get("vip")),
            "vip_type": acc.get("vip_type", 0),
            "vip_label": acc.get("vip_label", ""),
            "vip_due": acc.get("vip_due", 0),
            "coins": acc.get("coins", 0),
            "msg": acc.get("msg", "") if not acc.get("ok") else "",
        } if has_login else {},
    })


@app.route("/api/bili/refresh", methods=["POST"])
def api_bili_refresh():
    """续期：拿旧 cookie 换新的，有效期重新计算。"""
    cfg = load_cfg()
    cookie = str(cfg.get("bili_cookie") or "")
    if not cookie:
        return jsonify({"ok": False, "msg": "还没有 cookie"})
    res = run_async(bili_login.refresh_cookie(cookie))
    if not res.get("ok"):
        return jsonify(res)
    cfg["bili_cookie"] = res["cookie"]
    cfg["bili_cookie_ts"] = time.time()
    cfg["bili_cookie_expires"] = res.get("expires", 0)
    save_cfg(cfg)
    AUDIT.log("B站cookie续期", client_ip(), "", "info")
    return jsonify({"ok": True, "msg": "已续期，有效期重新计算"})


@app.route("/api/bili/cookie", methods=["POST"])
def api_bili_cookie_save():
    """手动粘贴 cookie（扫码登录不可用时的备用办法）。"""
    d = request.json or {}
    raw = str(d.get("cookie") or "").strip()
    if not raw:
        return jsonify({"ok": False, "msg": "cookie 不能为空"})
    c = bili_login.parse_cookie(raw)
    if not c.get("SESSDATA"):
        return jsonify({"ok": False,
                        "msg": "这段 cookie 里没有 SESSDATA，登录态不完整。"
                               "请确认复制的是 B 站域名下的完整 Cookie。"})
    cs = bili_login.cookie_str(c)
    # ⚠️ 占位符 / 半串也能通过上面的检查（只要有 "SESSDATA="），
    #    保存后面板照样显示「已登录」，接口却一直 -352 —— 这就是
    #    "前端显示登录、后端显示未登录"最常见的来源。这里先挡掉。
    if _sessdata_suspicious(cs):
        return jsonify({"ok": False,
                        "msg": "这段 SESSDATA 看着是占位符或者不完整（真实的长得多）。"
                               "请复制浏览器里完整的一串 Cookie，没保存。"})
    # 保存前先向 B站 求证：它说不认，就别存，免得又造出一个假的「已登录」
    try:
        acc = run_async(bili_login.account_info(cs)) or {}
    except Exception:
        acc = {}
    st, why = _bili_login_state(cs, acc)
    if st == "invalid":
        return jsonify({"ok": False,
                        "msg": f"B 站说这段 cookie 无效（{why}），没有保存。"
                               "请重新扫码登录，或从浏览器复制完整 Cookie。"})
    cfg = load_cfg()
    cfg["bili_cookie"] = cs
    cfg["bili_cookie_ts"] = time.time()
    # 过期时间先从 SESSDATA 里解真值（大多数情况下解得到），
    # 解不到才退回 30 天估算 —— 以前这里一律写 30 天，于是面板永远
    # 显示"还剩 29 天"，到期提醒也就永远进不了 2~5 天那个区间。
    _exp, _est = bili_login.effective_expires(cs, 0.0)
    cfg["bili_cookie_expires"] = _exp
    save_cfg(cfg)
    AUDIT.log("手动填写B站cookie", client_ip(), "", "info")
    if st == "unknown":
        return jsonify({"ok": True,
                        "msg": "已保存，但这会儿没能向 B站 求证成功（" + (why or "未知") +
                               "）。如果之后接口仍报 -352，说明这段 cookie 其实没生效。"})
    return jsonify({"ok": True,
                    "msg": ("已保存（这段 cookie 里读不到明确到期时间，"
                            "按 30 天估算，以 B 站实际为准）" if _est
                            else "已保存（到期时间已从 SESSDATA 读出："
                                 + bili_login.expired_info(_exp)["expires_str"] + "）")})


@app.route("/api/bili/logout", methods=["POST"])
def api_bili_logout():
    cfg = load_cfg()
    cfg.pop("bili_cookie", None)
    cfg.pop("bili_cookie_ts", None)
    cfg.pop("bili_cookie_expires", None)
    save_cfg(cfg)
    AUDIT.log("清除B站cookie", client_ip(), "", "info")
    return jsonify({"ok": True, "msg": "已清除 B 站 cookie"})


@app.route("/api/version")
def api_version():
    """当前版本号 + 更新日志。"""
    # CHANGELOG 已经累积到 150KB+，全量塞给前端会拖慢面板打开速度。
    # 面板只展示最近若干个版本，够用就行。
    # ⚠️ 上限放到 999：面板「更新日志」有三档（最新3/最新5/全部），
    # 选「全部」时要能一次取完（当前 94 条、约 61KB）。
    # 以前硬编码 50，选「全部」会被悄悄截断，看着像日志不全。
    try:
        n = int(request.args.get("limit") or 12)
    except ValueError:
        n = 12
    n = max(1, min(n, 999))
    return jsonify({
        "ok": True,
        "version": _version.current(),
        "changelog": _version.changelog()[:n],
    })


@app.route("/api/harden")
def api_harden():
    """加固状态：被封 IP 列表、文件完整性、是否建议建立基准。"""
    return jsonify({
        "ok": True,
        "banned": harden.GUARD.ban_list(),
        "integrity": harden.check_integrity(),
        "max_content_length": harden.MAX_CONTENT_LENGTH,
        "limits": {"write": f"{harden.WRITE_MAX}/{harden.WRITE_WINDOW}s",
                   "read": f"{harden.READ_MAX}/{harden.READ_WINDOW}s"},
    })


@app.route("/api/harden/unban", methods=["POST"])
def api_harden_unban():
    """解封所有 IP（自己被误封时用）。"""
    d = request.json or {}
    ip = str(d.get("ip") or "").strip()
    if ip:
        harden.GUARD._bans.pop(ip, None)
        harden.GUARD._strikes.pop(ip, None)
        harden.GUARD._save()
        AUDIT.log("解封IP", client_ip(), ip, "info")
        return jsonify({"ok": True, "msg": f"已解封 {ip}"})
    harden.GUARD.unban_all()
    AUDIT.log("解封全部IP", client_ip(), "", "info")
    return jsonify({"ok": True, "msg": "已解封全部 IP"})


@app.route("/api/harden/baseline", methods=["POST"])
def api_harden_baseline():
    """建立代码完整性基准快照。"""
    d = harden.save_baseline()
    AUDIT.log("建立完整性基准", client_ip(), f"{len(d['files'])} 个文件", "info")
    return jsonify({"ok": True, "msg": f"已记录 {len(d['files'])} 个文件的校验值"})


@app.route("/api/templates", methods=["POST"])
def api_templates():
    """单独保存文案模板（模板区有自己的保存按钮）。

    校验：只认 DEFAULT_TEMPLATES 里的键；值里的 {变量} 必须是
    {name} / {title}，其它占位符会被渲染时原样输出，容易让人困惑，
    所以这里直接提示而不是静默存下。
    """
    d = request.json or {}
    cfg = load_cfg()
    tpls = dict(cfg.get("templates") or {})
    bad = []
    for k, v in (d or {}).items():
        if k not in DEFAULT_TEMPLATES:
            continue
        val = str(v or "").strip()[:200]
        if not val:
            tpls.pop(k, None)
            continue
        import re as _re
        for ph in _re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", val):
            if ph not in ("name", "title"):
                bad.append(f"{k} 里的 {{{ph}}}")
        tpls[k] = val
    cfg["templates"] = tpls
    save_cfg(cfg)
    if bad:
        return jsonify({"ok": True, "warn": True,
                        "msg": "已保存，但不认识的变量会原样显示：" + "、".join(bad[:5])})
    return jsonify({"ok": True, "msg": "模板已保存"})


def _group_name_map():
    """gid 前缀 → 面板里保存的群名。

    日志写的是 `→ 群 6FBBD9B5…`（只取前 8 位，方便排版），
    对用户就是一串认不出来的十六进制。这里换成自己登记的群名。
    """
    out = {}
    try:
        st = get_store()
        for gid, info in (st.data.get("groups") or {}).items():
            info = info if isinstance(info, dict) else {}
            name = (info.get("name") or "").strip()
            if gid and name:
                out[str(gid)[:8]] = name
    except Exception:
        pass
    return out


def _humanize_groups(text, gmap):
    """把日志里的群代码换成群名；没登记过的群原样保留。

    ⚠️ 以前只认「群 xxxxx」这一种写法（前面必须有「群」字）。
       而审计日志写的是**光秃秃的群代码**：
         [info] 127.0.0.1 测试推送 6FBBD9B594F83BBCF92C29B284239945 动态
       那种行一个字都翻不出来，用户看到就是"群代码没翻译"。
    """
    if not text or not gmap:
        return text

    def _re(m):
        code = m.group(1)
        for pre, name in gmap.items():
            if code.startswith(pre):
                return "群「%s」" % name
        return m.group(0)

    # ① `群 6FBBD9B5…` / `群 6FBBD9B5` / `群 6FBBD9B5ABCDEF`
    text = re.sub(r"群\s+([A-Za-z0-9]{6,})…?", _re, text)
    # ② 光秃秃的群代码：16 位以上的字母数字串（群 openid 是 32 位十六进制）
    #    只有**前缀能对上已登记群**才替换，trace_id / md5 之类不受影响。
    text = re.sub(r"\b([A-Za-z0-9]{16,})\b", _re, text)
    return text


def collapse_log_items(items):
    """把「连着出现的同一句」合并成一条。

    一次突发里每一步都会各打一条日志（撞风控放慢 → 解除回最快 →
    平稳加快），翻译后说法完全相同、时间也是同一秒 —— 日志页看着就是
    同一条消息连着刷了好几次。这里把**相邻、同秒、说法相同**的条目
    并成一条并标上次数，具体数值原文叠在一起（页面可展开看）。

    第二道闸：同一秒内**都属于检查频率调整**的（先放慢、再回最快、
    再平稳加快）也并成一条 —— 对看日志的人来说这是"一件事"，连着
    三条只会觉得在重复。合并后以**最后一条**（最终状态）的说法为准，
    前面几条的原文照旧叠进去，不丢具体数值。
    """
    def _same_moment(a, b):
        return ((a.get("date") or "") == (b.get("date") or "")
                and (a.get("time") or "") == (b.get("time") or ""))

    def _freq(it):
        txt = (it.get("meaning") or "") + (it.get("cn") or "")
        return any(h in txt for h in ("检查频率", "限流", "轮询间隔"))

    out = []
    for it in items or []:
        meaning = (it.get("meaning") or "").strip()
        if not meaning:
            out.append(it)
            continue
        prev = out[-1] if out else None
        # ① 说法完全相同 → 并进上一条
        if (prev is not None and _same_moment(prev, it)
                and (prev.get("meaning") or "").strip() == meaning):
            prev["repeat"] = (prev.get("repeat") or 1) + 1
            prev["more"] = True
            for k in ("raw", "cn"):
                add = (it.get(k) or "").strip()
                if add:
                    prev[k] = (prev.get(k) or "").rstrip() + "\n" + add
            continue
        # ② 同一次突发里的不同步骤 → 并为一条，说法取最终状态
        if prev is not None and _same_moment(prev, it) \
                and _freq(prev) and _freq(it):
            rep = (prev.get("repeat") or 1) + 1
            for k in ("raw", "cn"):
                old = (prev.get(k) or "").strip()
                new = (it.get(k) or "").strip()
                if old:
                    it[k] = old + "\n" + new if new else old
            it["repeat"] = rep
            it["more"] = True
            out[-1] = it
            continue
        out.append(it)
    return out


def _count_lines_fast(path) -> int:
    """只数行数，不构造字符串列表（比 readlines 快一个数量级）。"""
    n = 0
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                n += chunk.count(b"\n")
    except OSError:
        return 0
    return n


def _tail_lines(path, want):
    """只读文件末尾若干行 —— **不把整个日志吞进内存**。

    ⚠️ 以前是 f.readlines() 全量读入。bot.log 攒到 27 万行 / 9.2MB 时，
       光读入就要几百毫秒、还会造出 27 万个字符串对象，而真正用到的
       只是末尾 200 行。文件越大面板打开越慢，这就是"越来越慢"的第三处。
    """
    try:
        want = max(1, int(want))
    except (TypeError, ValueError):
        want = 200
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size <= 0:
        return []
    # 按"每行约 400 字节"估一块，最少 256KB，保证够取到 want 行
    take = min(size, max(1 << 18, want * 400))
    try:
        with open(path, "rb") as f:
            f.seek(size - take)
            blob = f.read(take)
    except OSError:
        return []
    lines = blob.decode("utf-8", "ignore").splitlines()
    if len(lines) < want and take < size:
        # 估少了（遇到超长行），整份读一次兜底
        try:
            with open(path, "rb") as f:
                lines = f.read().decode("utf-8", "ignore").splitlines()
        except OSError:
            pass
    return [l.rstrip("\r\n") for l in lines[-want:]]


@app.route("/api/log")
def api_log():
    """运行日志。默认返回「翻译后」的结构化结果，?raw=1 则返回原始文本。"""
    limit = 200
    try:
        limit = min(1000, max(20, int(request.args.get("limit", 200))))
    except ValueError:
        pass

    # 只读一遍：既统计每个文件的条数，也汇总内容
    # ⚠️ 以前是把所有文件首尾相接拼成一串，再取最后 N 行。
    #    文件按名字排序（bot.log, security.log），末尾那 N 行
    #    必然被排在后面的小文件占满 —— bot.log 真正最新的记录被挤出去，
    #    面板显示的反倒是一堆陈旧的桌面版/安全日志。
    #    这就是用户说的「运行日志并不是完全都有」：不是没写，是没被读到。
    #    改法：**每个文件各取自己的末尾**，再按时间归并排序后统一取尾部。
    files = []
    _dated = []          # (date, time, seq, line)
    _total_all = 0
    _seq = 0
    _extract_ts = None
    try:
        from logtranslate import extract_time as _extract_ts
    except Exception:
        _extract_ts = None

    def _ts_of(line):
        if _extract_ts is None:
            return "", ""
        try:
            t = _extract_ts(line) or {}
            return t.get("date", "") or "", t.get("time", "") or ""
        except Exception:
            return "", ""

    for path in sorted(glob.glob(os.path.join(paths.log_dir(), "*.log"))):
        # ⚠️ 只读末尾：以前是 f.readlines() 把整个文件读进内存
        #    （bot.log 实测 27 万行 / 9.2MB），而真正用到的只有末尾
        #    limit 行。文件越大面板打开越慢 —— 这就是"越来越慢"的第三处。
        #    总条数另用字节扫描来数，同样不必构造几十万个字符串。
        if not os.path.isfile(path):
            continue
        raw = _tail_lines(path, limit)
        total = _count_lines_fast(path)
        files.append({"name": os.path.basename(path), "count": total})
        _total_all += total
        # 没有日期的日志（desktop.log 只有 HH:MM:SS）用文件修改日期补齐，
        # 否则归并时会被当成"远古记录"永远排在前面、永远显示不出来。
        try:
            _mdate = time.strftime("%Y-%m-%d",
                                   time.localtime(os.path.getmtime(path)))
        except OSError:
            _mdate = ""
        for l in raw[-limit:]:
            d, t = _ts_of(l)
            if not d and t:
                d = _mdate
            _dated.append((d, t, _seq, l))
            _seq += 1

    _dated.sort(key=lambda x: (x[0], x[1], x[2]))
    all_lines = [x[3] for x in _dated]

    # 第二道防线：真被写了两遍的行，时间戳和内容**完全一样**且紧挨着，
    # 这里只留一条。日志页就不会再「同一条消息出现两次」。
    # 只去紧邻重复、且跳过空行（空行本来就保留了分段作用）。
    _uniq, _prev = [], None
    for l in all_lines:
        if l.strip() and l == _prev:
            continue
        _uniq.append(l)
        _prev = l
    all_lines = _uniq

    # 过滤噪音：Flask 的 HTTP 访问记录（GET /api/log 200 之类）
    # 面板自己每几秒轮询一次，这类行会淹没真正有用的信息。
    def _noise(l: str) -> bool:
        if re.match(r'^\s*\d+\.\d+\.\d+\.\d+ - - \[', l):
            return True
        if re.search(r'"(GET|POST|PUT|DELETE) /', l) and 'HTTP/' in l:
            return True
        # aiohttp 的连接未关闭警告，属于无害噪音
        if 'Unclosed client session' in l or 'Unclosed connector' in l:
            return True
        if re.match(r'^\s*(client_session|connector|connections)\s*:', l):
            return True
        return False

    all_lines = [l for l in all_lines if not _noise(l)]

    # 群代码 → 群名（放在过滤之后、翻译之前，保证两种模式都生效）
    _gmap = _group_name_map()

    if request.args.get("raw") == "1":
        return jsonify({"ok": True,
                        "lines": [_humanize_groups(redact(l), _gmap)
                                  for l in all_lines[-limit:]],
                        "files": files})

    # 翻译：逐行归类 + 汇总
    from logtranslate import explain, summarize, translate_symbols
    tail = all_lines[-limit:]

    # 「中文翻译」关掉时只看原文（设置→通用→提醒开关，日志页不再放开关）
    if not bool(load_cfg().get("log_translate", True)):
        # ⚠️ 以前这里直接返回 items: []，而前端只渲染 items →
        #    「中文翻译」一关，日志页整片空白，看着像日志凭空丢了。
        #    关翻译只是不翻，不是不显示：照样逐条解析出时间/级别/归属，
        #    只是正文用原文（同样把没有时间戳的行并入上一条）。
        items = []
        for _l in tail:
            if not _l.strip():
                continue
            _it = explain(_l)
            # ⚠️ explain() 返回的是顶层字符串 "date"/"time"，没有 time 子字典。
            #    以前写成 _it.get("time") or {} 再 .get("date") —— 只要日志里有
            #    时间（几乎每条都有）就 AttributeError，/api/log 直接 500，
            #    前端拿到空响应 → 日志页整片空白，看着像「没有日志」。
            if not (_it.get("date") or _it.get("time")) and items:
                _prev = items[-1]
                _prev["raw"] = (_prev.get("raw") or "").rstrip() + "\n" + _l
                _prev["cn"] = _prev["raw"]
                _prev["more"] = True
                continue
            _it["raw"] = _humanize_groups(redact(_l), _gmap)
            _it["cn"] = _it["raw"]
            items.append(_it)
        summ = summarize(tail)
        for it in summ.get("issues", []):
            it["raw"] = _humanize_groups(redact(it["raw"]), _gmap)
        return jsonify({"ok": True,
                        "lines": [_humanize_groups(redact(l), _gmap)
                                  for l in tail],
                        "items": items, "summary": summ, "files": files,
                        "total": _total_all, "translated": False})
    # ⚠️ 逐行独立成条会让日志很乱：多行堆栈、print 的输出、空行
    #    全都没有时间戳，各占一条，时间列显示 --:--:--，看不出从属关系。
    #    改法：**没有时间戳的行并入上一条**（当多行正文显示），
    #    空行直接丢掉。这样一条日志 = 一个时间点，一眼能数清有多少件事。
    items = []
    for _l in tail:
        if not _l.strip():
            continue
        _it = explain(_l)
        # ⚠️ explain() 返回的是顶层字符串 "date"/"time"，没有 time 子字典。
        #    以前写成 _it.get("time") or {} 再 .get("date") —— 只要日志里有
        #    时间（几乎每条都有）就 AttributeError，/api/log 直接 500，
        #    前端拿到空响应 → 日志页整片空白，看着像「没有日志」。
        if not (_it.get("date") or _it.get("time")) and items:
            _prev = items[-1]
            _prev["raw"] = (_prev.get("raw") or "").rstrip() + "\n" + _l
            _prev["cn"] = (_prev.get("cn") or _prev.get("raw") or "").rstrip() + "\n" + _l
            _prev["more"] = True
            continue
        items.append(_it)
    summ = summarize(tail)
    # 安全起见，日志里可能含密钥片段
    for it in items:
        it["raw"] = _humanize_groups(redact(it["raw"]), _gmap)
        # 把文件名/类名/方法名也翻成中文，不懂代码也能看明白
        try:
            it["cn"] = _humanize_groups(
                redact(translate_symbols(it["raw"])), _gmap)
        except Exception:
            it["cn"] = it["raw"]
    for it in summ.get("issues", []):
        it["raw"] = _humanize_groups(redact(it["raw"]), _gmap)

    # ⚠️ 连发的同一句要合并：一次突发里每一步各打一条日志（撞风控放慢 →
    #    解除回最快 → 平稳加快），翻译后说法完全相同、时间也同一秒，
    #    日志页看着就是同一条消息连着出现好几次（用户反馈"重复推送"）。
    #    这里把相邻、同秒、说法相同的条目并成一条并标上次数。
    items = collapse_log_items(items)

    return jsonify({"ok": True, "lines": all_lines[-limit:], "items": items,
                    "summary": summ, "files": files,
                    "total": _total_all, "translated": True})


@app.route("/api/debug/dump", methods=["POST"])
def api_debug_dump():
    """往 bot.log 打一份诊断快照。

    「输出调试日志」开关打开后才能用 —— 否则 DEBUG 级内容不会落盘，
    点了也是白点。写进去的内容在面板「测试与诊断 → 运行日志」里能看到。
    """
    cfg = load_cfg()
    if not bool(cfg.get("log_debug", False)):
        return jsonify({"ok": False, "msg": "先打开「输出调试日志」开关"})
    try:
        import logging as _lg
        _log = _lg.getLogger("bili.debug")
        st = get_store()
        n_grp = len(st.data.get("groups") or {})
        n_sub = sum(len((g.get("subs") or {})) for g in
                    (st.data.get("groups") or {}).values()
                    if isinstance(g, dict))
        _log.debug("==== 调试快照 ====")
        _log.debug("版本=%s 群=%d 订阅UP主=%d", _version.current(), n_grp, n_sub)
        _log.debug("配置：轮询=%s 投稿动态每N轮=%s 去重窗口=%s 超时=%s",
                   cfg.get("poll_interval"), cfg.get("slow_check_every"),
                   cfg.get("dedupe_window"), cfg.get("request_timeout"))
        _log.debug("开关：图片=%s 下播=%s 纯文本=%s 沙箱=%s 中文翻译=%s",
                   cfg.get("send_image"), cfg.get("notify_offline"),
                   cfg.get("message_style"), cfg.get("is_sandbox"),
                   cfg.get("log_translate"))
        _log.debug("机器人在线=%s 凭据就绪=%s", bot_alive(), creds_ready(cfg))
        _log.debug("==== 快照结束 ====")
        AUDIT.log("输出调试日志", client_ip(), "写入诊断快照", "info")
        return jsonify({"ok": True, "msg": "已写入 bot.log"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route("/api/log/clear", methods=["POST"])
def api_log_clear():
    """清空运行日志（保留文件，只清空内容）。

    为什么保留文件：日志文件是面板持续读取的对象，
    删掉再建容易在 Windows 上撞上"文件被占用"，清空内容更安全。
    """
    n = 0
    for path in glob.glob(os.path.join(paths.log_dir(), "*.log")):
        try:
            with open(path, "w", encoding="utf-8"):
                pass
            n += 1
        except OSError:
            continue
    AUDIT.log("清空日志", client_ip(), f"{n} 个文件", "info")
    return jsonify({"ok": True, "msg": f"已清空 {n} 个日志文件"})


def _bl_list(st, uid, kind, field):
    """自检用：列出**各群**对某 UP 主某类型的基线。

    基线是按群存的（新订阅的群不该继承别的群进度），
    所以自检不能只给一个值——不同群进度本就不同。
    """
    lookup = {"dyn": "dynamic"}.get(kind, kind)
    out = []
    try:
        gids = st.targets(uid, lookup) or []
    except Exception:
        return out
    for gid in gids:
        b = st.baseline_of(gid, uid, kind) or {}
        val = b.get(field)
        g = st.get_group(gid) or {}
        out.append({
            "gid": gid,
            "name": g.get("name") or "",
            "value": "" if val is None else str(val),
        })
    return out


def _first_bl(st, uid, kind, field):
    """兼容旧前端：只有一个订阅群时直接给值，多群时给第一个。"""
    lst = _bl_list(st, uid, kind, field)
    return lst[0]["value"] if lst else ""


@app.route("/api/check-now", methods=["POST"])
def api_check_now():
    """立刻跑一轮检测（不等轮询间隔），并返回每个订阅项的结果。

    排查"开了直播/发了动态却没提示"时最有用：不用等 60 秒，
    点一下就能看到到底查到了什么、是接口失败还是状态没变。
    """
    st = get_store()

    async def _run():
        c = bili_client()
        out = []
        try:
            for uid in sorted(st.all_ups()):
                kinds = st.enabled_kinds_of_up(uid)
                if not kinds:
                    continue
                up = st.get_up(uid)
                name = up.get("uname") or str(uid)
                room_id = up.get("room_id") or 0
                row = {"uid": uid, "name": name, "kinds": sorted(kinds),
                       "room_id": room_id, "results": {}}

                if "live" in kinds:
                    if not room_id:
                        room_id, short_room = await c.get_room_id_by_mid(uid)
                        row["room_id_fixed"] = room_id
                        if room_id:
                            st.set_up_info(uid, name, room_id, "",
                                           int(short_room or 0))
                    if room_id:
                        try:
                            lv = await c.get_live(room_id)
                            row["results"]["live"] = {
                                "ok": True, "status": lv.live_status,
                                # 不显示 0/1/2，直接说人话
                                "text": live_status_cn(lv.live_status),
                                "baseline": _first_bl(st, uid, "live",
                                                      "status"),
                                "baselines": _bl_list(st, uid, "live",
                                                      "status")}
                        except BiliError as e:
                            row["results"]["live"] = {"ok": False,
                                                      "text": str(e)}
                    else:
                        row["results"]["live"] = {
                            "ok": False, "text": "没查到直播间号"}

                if "video" in kinds:
                    try:
                        v = await c.get_latest_video(uid)
                        row["results"]["video"] = {
                            "ok": True, "bvid": (v.bvid if v else ""),
                            "baseline": _first_bl(st, uid, "video", "bvid"),
                            "baselines": _bl_list(st, uid, "video", "bvid")}
                    except BiliError as e:
                        row["results"]["video"] = {"ok": False,
                                                   "text": str(e)}

                if "dynamic" in kinds:
                    try:
                        d = await c.get_latest_dynamic(uid)
                        row["results"]["dynamic"] = {
                            "ok": True, "id": (d.dyn_id if d else ""),
                            "baseline": _first_bl(st, uid, "dyn", "id"),
                            "baselines": _bl_list(st, uid, "dyn", "id")}
                    except BiliError as e:
                        row["results"]["dynamic"] = {"ok": False,
                                                     "text": str(e)}
                out.append(row)
        finally:
            await c.close()
        return out

    t0 = time.time()
    try:
        rows = run_async(_run())
    except Exception as e:
        return jsonify({"ok": False, "msg": redact(str(e))})
    # 给前端开一块独立结果区用：检测时刻 + 耗时 + 一句话结论。
    # 以前只有 rows，前端只能把结果塞进 hint 区域，点了跟没点一样。
    n_up = len(rows)
    n_push, n_fail = 0, 0
    for _r in rows:
        for _v in ((_r.get("results") or {}).values()):
            if not isinstance(_v, dict):
                continue
            if not _v.get("ok"):
                n_fail += 1
                continue
            _cur = _v.get("status", _v.get("bvid", _v.get("id")))
            if str(_cur) != str(_v.get("baseline")):
                n_push += 1
    if n_fail:
        summary = f"{n_up} 个订阅，{n_push} 项会推送，{n_fail} 项接口失败"
    elif n_push:
        summary = f"{n_up} 个订阅，{n_push} 项与已记状态不同，会推送"
    else:
        summary = f"{n_up} 个订阅，状态都没变，不推送"
    return jsonify({"ok": True, "rows": rows,
                    "note": "baseline=已记住的状态；与当前值不同才会推送",
                    "at": time.strftime("%H:%M:%S"),
                    "elapsed": round(time.time() - t0, 1),
                    "summary": summary})


# 自检用的固定测试 UID。
# ⚠️ 自检是用来查「接口通不通、凭据对不对」的，跟你要订阅谁**没关系**，
# 所以必须和「添加订阅」那个输入框剥离开 —— 否则每次自检都要先填一遍
# UID，而且填错了还会误以为接口坏了。
# 想换可以在 config.yaml 里设 selfcheck_uid。
SELFCHECK_UID = 259149243


def _selfcheck_uname(uid) -> str:
    """自检 UID 对应的 UP 主名（本地查，不联网）。

    先看订阅里有没有这个人，再看 UP 缓存。查不到就返回空 ——
    前端显示「用户」，不要拿 UID 当名字显示（之前就是这么错的：
    「已记住：259149243」看着像记住了一个群号）。
    """
    s = str(uid or "").strip()
    if not s:
        return ""
    try:
        st = get_store()
    except Exception:
        return ""
    try:
        for _gid, g in (st.data.get("groups") or {}).items():
            sub = (g.get("subs") or {}).get(s)
            if isinstance(sub, dict) and sub.get("uname"):
                return str(sub["uname"])
    except Exception:
        pass
    try:
        up = st.get_up(s) if hasattr(st, "get_up") else None
        if isinstance(up, dict) and up.get("uname"):
            return str(up["uname"])
    except Exception:
        pass
    return ""


# ==================== 数据与备份 ====================
# ⚠️ 为什么要有这一组：
#   数据（订阅/文案/推送基线）跟程序目录是分开的，用户不知道在哪，
#   也就没法主动保护；一旦"删文件夹换新版"，很容易以为数据没了。
#   这里把数据位置摆到明面上，并给一个「设定+凭据+订阅」一起打包的入口。


@app.route("/api/data/info")
def api_data_info():
    """数据文件在哪、多大、最近什么时候写的、有多少备份。"""
    try:
        p = get_store().path
    except Exception:
        p = ""
    info = {"ok": True, "path": p, "exists": False, "size": 0,
            "mtime": "", "stats": {}, "full": [], "snapshots": []}
    try:
        if p and os.path.exists(p):
            info["exists"] = True
            info["size"] = os.path.getsize(p)
            info["mtime"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p)))
    except Exception:
        pass
    try:
        from tools import backup_all
        info["stats"] = backup_all._stats(p)
        info["full"] = [{"name": b["name"],
                         "mtime": time.strftime(
                             "%Y-%m-%d %H:%M:%S", time.localtime(b["mtime"])),
                         "size": b["size"]}
                        for b in backup_all.list_full_backups()[:10]]
    except Exception:
        pass
    try:
        from db import list_backups
        info["snapshots"] = [
            {"name": b["name"], "mtime": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(b["mtime"]))}
            for b in (list_backups(p) if p else [])[:10]]
    except Exception:
        pass
    return jsonify(info)


# 品牌 logo 的远程地址。项目不再随包带图片（src/static/webuiimg 已移除），
# 统一从仓库读取：仓库里不留二进制文件，换图也不用改代码。
#
# ⚠️ 这里用 jsDelivr 而不是 raw.githubusercontent.com：
#    指向的是同一个仓库、同一个分支、同一张图，但 raw 域名在国内经常被拦，
#    实测 20 秒超时取不到；jsDelivr 是 GitHub 的公共 CDN 镜像，实测 1 秒就返回。
#    想换回官方 raw（或其它镜像）设环境变量 ONOBN_LOGO_URL 即可。
#
# 图床仓库：https://github.com/OnOiduts/OnO-ImageHost-  （注意名字末尾有连字符）
# 图片路径：Logo/logo.png（大写 L 的目录名，GitHub 路径大小写敏感）
LOGO_URL_DEFAULT = ("https://cdn.jsdelivr.net/gh/OnOiduts/"
                    "OnO-ImageHost-@main/Logo/logo.png")

# 设成这几个值 = 明确不要 logo（离线部署时用，省掉一次外部请求）
LOGO_OFF_VALUES = ("off", "none", "disable", "disabled", "0")

# 标签页小图标。同样走 jsDelivr，理由同上（raw 域名国内经常取不到）。
# 图床仓库：https://github.com/OnOiduts/OnO-ImageHost-  （名字末尾有连字符）
# 图片路径：Web/OnOBN/ico.png（Web、OnOBN 两处都是大写开头，大小写敏感）
ICO_URL_DEFAULT = ("https://cdn.jsdelivr.net/gh/OnOiduts/"
                   "OnO-ImageHost-@main/Web/OnOBN/ico.png")

# 登录页背景装饰图：除 logo / ico 之外再飘两张，让背景不那么单调。
# 图床路径：Web/OnOBN/bg1.svg、Web/OnOBN/bg2.svg（目录名大小写敏感）。
# 同样可用环境变量 ONOBN_BG1_URL / ONOBN_BG2_URL 换，设 off 则不显示。
#
# ⚠️ 实测过这两张图的可达性：**两个 CDN 各有一张取不到**
#    bg1.svg → raw 200(1.6KB/1.0s)，jsDelivr 502
#    bg2.svg → raw 超时，jsDelivr 200(293KB/7.8s)
#   所以两张图**分别走不同的源**，各自取最快的那条路。
#   即便如此仍可能取不到（CDN 边缘节点抖动，之前 logo 就反复出现过），
#   这没关系：登录页每张图都有降级链，取不到就隐藏，背景还有纯 CSS 光斑兜底。
#
# ⚠️⚠️ 这里的默认地址**必须是 .png**，不能是 .svg。
#    背景 <img> 的 src 用 svg_variant(它) 换成 .svg，onerror 的回退地址用它本身。
#    以前这里直接写成 .svg，结果 src 和回退地址**是同一个字符串** ——
#    svg 取不到 → 用同一个取不到的地址再试一次 → 再失败 → 隐藏。
#    降级链等于没做，表现就是"左下角和右下角是空的"（bg1/bg2 正是那两个角）。
BG1_URL_DEFAULT = ("https://raw.githubusercontent.com/OnOiduts/"
                   "OnO-ImageHost-/main/Web/OnOBN/bg1.png")
BG2_URL_DEFAULT = ("https://cdn.jsdelivr.net/gh/OnOiduts/"
                   "OnO-ImageHost-@main/Web/OnOBN/bg2.png")


def svg_variant(url: str) -> str:
    """同目录、同文件名，只把扩展名换成 .svg。

    登录页背景飘的就是这两张图（放得很大），用矢量图放大不糊、体积也小得多。

    ⚠️ 换完的地址**可能取不到**（图床里还没传 svg）。这没关系：登录页的
       <img> 挂了降级链 —— svg 404 → 自动退回同名 png → 再失败才隐藏，
       而且背景本身还有纯 CSS 光斑兜底，任何时候都"有东西在飘"。
    """
    if not url or "." not in url.rsplit("/", 1)[-1]:
        return ""
    base, _dot, _ext = url.rpartition(".")
    return (base + ".svg") if base else ""


def _logo_remote_url() -> str:
    """远程 logo 地址；被显式关掉时返回空串。"""
    raw = ""
    try:
        import envcfg
        raw = envcfg.logo_url_from_env()
    except Exception:
        pass
    if raw and raw.lower() in LOGO_OFF_VALUES:
        return ""
    return raw or LOGO_URL_DEFAULT


def _ico_remote_url() -> str:
    """远程 favicon 地址；被显式关掉时返回空串。"""
    raw = ""
    try:
        import envcfg
        raw = envcfg.ico_url_from_env()
    except Exception:
        pass
    if raw and raw.lower() in LOGO_OFF_VALUES:
        return ""
    return raw or ICO_URL_DEFAULT


def _bg1_remote_url() -> str:
    """登录页背景图 1；被显式关掉时返回空串。"""
    raw = ""
    try:
        import envcfg
        raw = envcfg.bg1_url_from_env()
    except Exception:
        pass
    if raw and raw.lower() in LOGO_OFF_VALUES:
        return ""
    return raw or BG1_URL_DEFAULT


def _bg2_remote_url() -> str:
    """登录页背景图 2；被显式关掉时返回空串。"""
    raw = ""
    try:
        import envcfg
        raw = envcfg.bg2_url_from_env()
    except Exception:
        pass
    if raw and raw.lower() in LOGO_OFF_VALUES:
        return ""
    return raw or BG2_URL_DEFAULT


@app.route("/api/brand/logo")
def api_brand_logo():
    """品牌 logo 底纹：优先用数据目录里的 images/ui/logo.png，否则走远程。

    为什么不再随包带图：程序目录里的 static/webuiimg 是个二进制文件夹，
    换图要改仓库、传公开仓库还得考虑图片版权与体积。现在统一远程取，
    仓库里不存任何图片文件。

    想用自己的图：把 logo.png 放进数据目录的 images/ui/ 就覆盖远程地址
    （这个目录跟数据一起活，换新版不会被删）。
    想彻底不显示：设环境变量 ONOBN_LOGO_URL=off。
    """
    try:
        custom = os.path.join(paths.image_sub("ui"), "logo.png")
        if os.path.exists(custom):
            return send_file(custom, mimetype="image/png")
    except Exception:
        pass
    url = _logo_remote_url()
    if not url:
        return make_response("no logo", 404)
    return redirect(url, code=302)


@app.route("/api/brand/ico")
def api_brand_ico():
    """标签页小图标（favicon）：优先数据目录 images/ui/ico.png，否则走远程。

    跟 logo 同源同规则：仓库里不放二进制图，换图改图床仓库或换环境变量
    ONOBN_ICO_URL 即可；设成 off 就彻底不发这次请求（离线部署用）。

    ⚠️ 这里**不能**加登录校验：浏览器取 favicon 的请求既不带 cookie 也不带
    自定义头，登录页自己也要显示图标，加了校验就等于永远取不到。
    """
    try:
        custom = os.path.join(paths.image_sub("ui"), "ico.png")
        if os.path.exists(custom):
            return send_file(custom, mimetype="image/png")
    except Exception:
        pass
    url = _ico_remote_url()
    if not url:
        return make_response("no ico", 404)
    return redirect(url, code=302)


@app.route("/api/data/home", methods=["GET", "POST"])
def api_data_home():
    """数据放在哪 —— 可以自己选。

    GET  返回当前数据目录、是怎么定的（env / 面板选的 / 默认）、各子目录情况
    POST 把数据整体搬到新位置（搬完需要重启，因为密钥和图片目录是导入时定死的）
    """
    if not _auth_ok():
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    if request.method == "GET":
        home = paths.data_home()
        info = {"ok": True, "home": home, "source": paths.home_source(),
                "pointer": paths.pointer_path(), "dirs": {}, "images": {}}
        for k, fn in (("config", paths.config_path),
                      ("state", paths.state_path),
                      ("logs", paths.log_dir),
                      ("backups", paths.backup_dir)):
            try:
                info["dirs"][k] = fn()
            except Exception:
                info["dirs"][k] = ""
        try:
            from media import LONG_DIR, TMP_DIR
            info["images"] = {
                "dir": paths.image_dir(),
                "avatars": _count_size(LONG_DIR),
                "push": _count_size(TMP_DIR),
                "ui": _count_size(paths.image_sub("ui")),
            }
        except Exception:
            pass
        return jsonify(info)

    new_path = (request.json or {}).get("path", "") if request.is_json else \
        (request.form.get("path") or "")
    r = paths.set_data_home(new_path)
    if r.get("ok"):
        AUDIT.log("改数据目录", client_ip(),
                  f"{r.get('from')} → {r.get('to')}", "info")
    return jsonify(r)


@app.route("/api/data/legacy")
def api_data_legacy_scan():
    """扫描旧版数据文件（data.db 等）。

    老用户升级时手里那份 data.db 是唯一的订阅记录，这里先列出来
    让人确认里面有多少群、多少订阅，再决定要不要导入。
    """
    if not _auth_ok():
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    try:
        items = db.scan_legacy_files()
    except Exception as e:
        return jsonify({"ok": False, "msg": f"扫描失败：{e}"})
    for it in items:
        it["size"] = int(it.get("size") or 0)
    return jsonify({"ok": True, "items": items})


@app.route("/api/data/legacy/import", methods=["POST"])
def api_data_legacy_import():
    """把旧版 data.db 合并进当前数据（合并，不覆盖）。"""
    if not _auth_ok():
        return jsonify({"ok": False, "msg": "请先登录"}), 401
    data = request.json or {}
    path = str(data.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "msg": "没给文件路径"})
    # 只接受扫描出来的候选，避免拿这个接口去读任意文件
    try:
        allow = {os.path.abspath(i["path"]) for i in db.scan_legacy_files()}
    except Exception:
        allow = set()
    if os.path.abspath(path) not in allow:
        return jsonify({"ok": False, "msg": "这个文件不在可导入的旧数据里"})
    try:
        res = get_store().import_legacy_file(path)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"导入失败：{e}"})
    if not res.get("ok"):
        return jsonify({"ok": False, "msg": res.get("reason") or "导入失败"})
    try:
        db._save_imported(
            dict(db._load_imported(),
                 **{os.path.abspath(path): db._fp_of(path)}))
    except Exception:
        pass
    sm = res.get("summary") or {}
    AUDIT.log("导入旧数据", client_ip(),
              f"{os.path.basename(path)}：{sm.get('groups', 0)} 群 / "
              f"{sm.get('subs', 0)} 订阅", "info")
    return jsonify({"ok": True, "summary": sm, "report": res.get("report")})


def _count_size(d: str) -> dict:
    """目录下的文件数与总字节（图片目录要能看出占了多少）。"""
    n = size = 0
    try:
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                n += 1
                try:
                    size += os.path.getsize(p)
                except OSError:
                    pass
    except OSError:
        pass
    return {"n": n, "size": size}


@app.route("/api/backup/export", methods=["POST"])
def api_backup_export():
    """立即打一个完整备份（设定 + 凭据 + 订阅）。"""
    try:
        from tools import backup_all
        r = backup_all.make_backup(quiet=True)
    except Exception as e:
        return jsonify({"ok": False, "msg": "备份失败：" + redact(str(e))})
    if not r.get("ok"):
        return jsonify({"ok": False,
                        "msg": "备份失败：" + str(r.get("error", ""))})
    AUDIT.log("备份", "-", os.path.basename(r["path"]), "info")
    return jsonify({"ok": True, "path": r["path"],
                    "name": os.path.basename(r["path"]),
                    "stats": r.get("stats") or {},
                    "msg": "已备份（设定 + 凭据 + 订阅）"})


@app.route("/api/backup/download")
def api_backup_download():
    """下载完整备份。不带参数=最新一份。"""
    from tools import backup_all
    name = (request.args.get("name") or "").strip()
    bks = backup_all.list_full_backups()
    target = ""
    for b in bks:
        if name and b["name"] == name:
            target = b["path"]
            break
    if not target and bks:
        target = bks[0]["path"]
    if not target or not os.path.exists(target):
        return make_response("还没有备份，先点「立即备份」", 404)
    base = os.path.basename(target)
    try:
        return send_file(target, as_attachment=True, download_name=base)
    except TypeError:
        # 老版本 Flask 用 attachment_filename
        return send_file(target, as_attachment=True, attachment_filename=base)


@app.route("/api/backup/restore", methods=["POST"])
def api_backup_restore():
    """从上传的完整备份包恢复。"""
    import tempfile
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "msg": "没收到备份文件"})
    if not f.filename.lower().endswith(".zip"):
        return jsonify({"ok": False, "msg": "请上传备份的 zip 文件"})
    tmp = os.path.join(tempfile.gettempdir(),
                       "bili-restore-%d.zip" % int(time.time()))
    try:
        f.save(tmp)
        from tools import restore_all
        r = restore_all.restore(tmp)
    except Exception as e:
        return jsonify({"ok": False, "msg": "恢复失败：" + redact(str(e))})
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass
    if not r.get("ok"):
        return jsonify({"ok": False,
                        "msg": "恢复失败：" + str(r.get("error", ""))})
    AUDIT.log("恢复备份", "-", str(f.filename), "warn")
    return jsonify({"ok": True, "msg": "已恢复，重启机器人后生效",
                    "stats": (r.get("meta") or {}).get("stats") or {}})


# ── 依赖分类（「设置 → 关于」页显示）──────────────────────────────
# 必装：缺一个就跑不起来。与 src/requirements.txt 保持一致。
#   ⚠️ playwright 是必装，不是可选 —— 面板两条 B 站登录路线（浏览器登录 /
#      手动粘贴 Cookie）最终都靠它把 Cookie 换成长效登录态。它曾一度被误
#      降到可选，结果点了登录只会报「缺少依赖」，已移回。
REQUIRED_MODULES = ("aiohttp", "botpy", "yaml", "flask", "playwright")
# 可选：目前没有。qrcode / Pillow 随扫码登录整条链路一起删了；
#       这份空列表保留，是为了页面能如实显示「目前没有可选依赖」，
#       而不是让前端猜。
OPTIONAL_MODULES: tuple = ()
# import 用的模块名 → pip 安装时的包名。requirements.txt 里写的是 pip 名，
# 页面显示 pip 名，用户照着补装才对得上（比如 import 是 botpy、装的是 qq-botpy）。
PIP_NAMES = {
    "aiohttp": "aiohttp",
    "botpy": "qq-botpy",
    "yaml": "PyYAML",
    "flask": "Flask",
    "playwright": "playwright",
}


@app.route("/api/about")
def api_about():
    """「设置 → 关于」页的数据来源。

    为什么单独一个接口：反馈 bug 时最常被问的就是"你用的哪个版本、
    数据在哪、装了哪些依赖"，以前这些信息散在日志、配置文件里，
    得来回翻。这里一次性给全，页面上还能一键复制去贴反馈。
    """
    info = {
        "ok": True,
        "name_zh": "OnOB站通知订阅工具",
        "name_en": "OnO Bilibili Notifier",
        "abbr": "OnOBN",
        "author": "波萝Buono",
        "github": "https://github.com/XxBoLuoxX",
        "version": _version.current(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "data_dir": "",
        "state_path": "",
        "log_dir": "",
        "home_source": "",
        "started": "",
        "deps": {},
        "stats": {},
        "notes": [
            "服务器端从未测试过：只在本机 Windows 桌面环境开发调试过，"
            "Linux 服务器 / Docker / 云主机的部署路径没有实测，不保证能跑起来",
            "代码由 AI 生成（腾讯元宝「Hy4 preview」），由作者 波萝Buono "
            "整理、测试与维护；生成的代码未经人工逐行复核",
            "安全功能未做安全审计：面板口令、登录限流、凭据加密等只验证过"
            "「功能能跑通」，未经过安全测试、渗透测试或代码审计，请勿暴露到公网",
            "维护状态：项目已稳定，此后仅更新「功能更新」与「严重 bug 修复」"
            "（程序起不来 / 数据丢失 / 推送完全失效 / 凭据泄露）；"
            "文案措辞、界面观感、代码风格类改动不再发布新版本，详见 README「维护政策」",
        ],
    }
    try:
        info["data_dir"] = paths.data_home()
        info["state_path"] = get_store().path
        info["log_dir"] = paths.log_dir()
        info["home_source"] = paths.home_source()
    except Exception:
        pass
    # 依赖装在不在：反馈时一眼看出是不是缺包
    #
    # ⚠️ 分类只在这里定义一份，前端不再自己写死必装/可选清单。
    #    以前前端硬编码 `opt = ['playwright']`，而 playwright 早已移回必装
    #    （两条 B 站登录路线都要靠它），于是「关于」页一直显示
    #    「可选：playwright」——和 requirements.txt 对不上，装漏了也会被
    #    当成"只是少了个可选功能"。现在由后端给，两边不可能再不一致。
    for mod in REQUIRED_MODULES + OPTIONAL_MODULES:
        try:
            __import__(mod)
            info["deps"][mod] = True
        except Exception:
            info["deps"][mod] = False
    info["deps_required"] = list(REQUIRED_MODULES)
    info["deps_optional"] = list(OPTIONAL_MODULES)
    info["deps_pip"] = dict(PIP_NAMES)
    try:
        from tools import backup_all
        info["stats"] = backup_all._stats(info["state_path"])
    except Exception:
        pass
    # 机器人这次启动的时间：判断"是不是刚重启过"
    try:
        hb = heartbeat.read() or {}
        ts = hb.get("ts") or hb.get("time") or 0
        if ts:
            info["started"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except Exception:
        pass
    return jsonify(info)


@app.route("/api/diagnose")
def api_diagnose():
    """自检：B 站接口通不通、凭据对不对。

    用的是固定测试 UID（默认 259149243），不读任何输入框 ——
    自检和「添加订阅」是两回事，不该互相牵扯。
    """
    cfg = load_cfg()
    try:
        uid = int(cfg.get("selfcheck_uid") or SELFCHECK_UID)
    except (TypeError, ValueError):
        uid = SELFCHECK_UID
    out = {"bili": {}, "qq": {}, "selfcheck_uid": uid,
           "selfcheck_uname": _selfcheck_uname(uid) or "",
           "at": time.strftime("%H:%M:%S")}
    _t0 = time.time()
    try:
        out["bili"] = {"ok": True, **run_async(_probe_bili(uid))}
    except Exception as e:
        out["bili"] = {"ok": False, "msg": redact(str(e))}

    if creds_ready(cfg):
        qq = QQClient(cfg["appid"], cfg["secret"], sandbox=bool(cfg.get("is_sandbox")))
        try:
            run_async(qq.get_token())          # 只验证能否拿到，不回显内容
            out["qq"] = {"ok": True, "msg": "凭据有效，已成功换取 token"}
        except Exception as e:
            out["qq"] = {"ok": False, "msg": redact(str(e))}
        finally:
            run_async(qq.close())
    else:
        out["qq"] = {"ok": False, "msg": "还没填 AppID / AppSecret"}
    # 耗时与来源：前端结果卡片要显示「用了几秒 / 是真实接口还是占位」
    out["elapsed"] = round(time.time() - _t0, 1)
    out["source"] = "真实数据" if out["bili"].get("ok") else "接口失败（B站未返回）"
    return jsonify(out)


def _probe_desc(r) -> str:
    """把检测结果说成人话。

    ⚠️ 旧写法把 title、bvid、dyn_id 三个字段用 or 串起来取，
    但 DynamicInfo **没有 bvid 字段**（那是 VideoInfo 的），
    直接访问就抛 AttributeError；而调用处只 catch BiliError，
    抓不住它 → 整个「B站」自检显示成
    "DynamicInfo object has no attribute bvid"，
    看起来像接口挂了，其实是自检自己的 bug。
    这里改成按对象实际有的字段取，缺哪个都不炸。
    """
    if not r:
        return "无（这个 UP 主可能还没有）"
    parts = []
    title = getattr(r, "title", "") or ""
    if title:
        parts.append(f"《{title[:30]}》")
    # 视频：BV 号；动态：dyn_id。两者字段不通用，分开取
    bvid = getattr(r, "bvid", "") or ""
    dyn_id = getattr(r, "dyn_id", "") or ""
    if bvid:
        parts.append(f"BV {bvid}")
    if dyn_id:
        parts.append(f"动态 id {dyn_id}")
    # 动态专属信息：类型、是否置顶、指向哪
    major = getattr(r, "major_type", "") or ""
    if major and major != "normal":
        label = {"live": "开播卡片", "video": "投稿卡片",
                 "reserve": "直播预约"}.get(major, major)
        parts.append(f"类型 {label}")
    if getattr(r, "pinned", False):
        parts.append("置顶")
    ref_b = getattr(r, "ref_bvid", "") or ""
    ref_r = getattr(r, "ref_room_id", 0) or 0
    if ref_b:
        parts.append(f"指向视频 {ref_b}")
    if ref_r:
        parts.append(f"指向直播间 {ref_r}")
    return "　".join(parts) if parts else "有数据但没有可展示的字段"


async def _probe_bili(uid: int) -> dict:
    """探一个 UP 主的 B 站接口。

    每一项都单独 try，并且 catch 全部 Exception（不只是 BiliError）——
    自检自己的 bug 不该伪装成"接口失败"，但也不能让一个字段的异常
    把整个自检拖垮。
    """
    c = bili_client()
    res = {"uname": "", "room_id": 0, "live": "", "video": "",
           "dynamic": "", "pinned": "", "top_comment": "", "season": ""}
    try:
        up = await c.get_up(uid)
        res["uname"] = up.uname
        res["room_id"] = up.room_id
        res["short_room_id"] = getattr(up, "short_room_id", 0)
        if up.room_id:
            try:
                live = await c.get_live(up.room_id)
                # ⚠️ 用户要求：不要写 0/1/2 这种原始数字，直接说人话
                #    （未开播 / 已开播 / 轮播中）。
                #    以前写成 "直播中（live_status=1，1=直播中）"，
                #    右边那串数字既没信息量又占地方。
                res["live"] = live_status_cn(live.live_status)
            except Exception as e:
                res["live"] = f"失败：{e}"
        else:
            res["live"] = "该 UP 主没有直播间（直播提醒不会触发）"

        # 投稿
        try:
            v = await c.get_latest_video(uid)
            res["video"] = _probe_desc(v)
        except Exception as e:
            res["video"] = f"失败：{e}"

        # 最新动态
        try:
            d = await c.get_latest_dynamic(uid)
            res["dynamic"] = _probe_desc(d)
        except Exception as e:
            res["dynamic"] = f"失败：{e}"

        # 置顶动态（和上面那条可能不是同一条）
        try:
            p = await c.get_pinned_dynamic(uid)
            res["pinned"] = _probe_desc(p) if p else "没有置顶动态"
        except Exception as e:
            res["pinned"] = f"失败：{e}"

        # 置顶评论（依赖置顶动态，失败要说明原因）
        try:
            pd = await c.get_pinned_dynamic(uid)
            if not pd or not pd.dyn_id:
                res["top_comment"] = "没有置顶动态，取不到置顶评论"
            else:
                # ⚠️ rid / comment_type / comment_id 必须一起传 ——
                #    带图动态（相簿）的评论区是 type=11、oid=rid，
                #    不传就只能碰运气，命中不了就是 code=-404「啥都木有」。
                #    （推送测试的真实数据已经这么修了，这里同步过来，
                #     否则会出现"测试能取到、自检取不到"的怪现象。）
                cm = await c.get_pinned_comment(
                    pd.dyn_id, uid,
                    rid=getattr(pd, "rid", "") or "",
                    comment_type=int(getattr(pd, "comment_type", 0) or 0),
                    comment_id=getattr(pd, "comment_id_str", "") or "")
                if cm:
                    who = str(cm.get("uname") or "").strip()
                    same = "（UP 主本人）" if str(cm.get("mid") or "") == str(uid) \
                           else "（非 UP 主本人，默认不推送）"
                    txt = str(cm.get('text') or '')[:30]
                    # ⚠️ 评论者昵称取不到时不要再拼一个孤零零的「：」——
                    #    以前不管有没有昵称都把昵称和冒号拼上去，
                    #    昵称为空就显示成 "：（UP 主本人）"，开头多一个冒号。
                    lead = (who + "：") if who else ""
                    res["top_comment"] = f"{lead}{txt}{same}"
                else:
                    res["top_comment"] = "该置顶动态下没有置顶评论"
        except Exception as e:
            res["top_comment"] = f"失败：{e}"

        # 合集检测（第七项）：没订阅合集时自动挑"最近更新的那个合集"
        try:
            res["season"] = await _probe_season_diag(c, uid)
        except Exception as e:
            res["season"] = f"失败：{e}"
    finally:
        await c.close()
    return res


async def _probe_season_diag(c, uid) -> str:
    """⚠️ 名字别叫 _probe_season —— 那个是「添加合集订阅前先校验」
    用的（签名不同），重名会把后者整个覆盖掉。"""
    """自检第七项：合集检测。

    ⚠️ 合集里视频的顺序**不保证**：可能正序（旧在前）也可能倒序（新在前），
    所以取到列表后一律按发布时间挑最大的那条，绝不信任接口给的顺序。

    没订阅过这个 UP 的合集时，默认读"该 UP 最近更新的合集"里最新的视频 ——
    这样即使一个合集都没订，也能验证合集接口到底通不通。
    """
    # ① 先看有没有订阅过这个 UP 的合集
    sid = 0
    try:
        st = get_store()
        for gid in (st.data.get("groups") or {}):
            for sv in (st.seasons_of_group(gid) or []):
                if isinstance(sv, dict) and str(sv.get("uid") or "") == str(uid):
                    sid = int(sv.get("season_id") or 0)
                    break
            if sid:
                break
    except Exception:
        sid = 0
    # ② 走 BiliClient 的共用方法（推送测试的「用真实数据」也走它）：
    #    没订阅就自动挑最近更新的合集，并按发布时间挑最新那条视频。
    got = await c.newest_season_video(int(uid), sid)
    if not got or not got.get("push"):
        return "该 UP 主没有公开合集（合集检测不会触发）"
    p = got["push"]
    title = (p.title or "").strip()[:30]
    sname = (got.get("season_title") or "").strip() or "未命名"
    # ⚠️ 只有**真的分了小节**才有这一项（只有一个"正片"时不显示，
    # 那时 get_video_section_title 返回空）—— 见 section_map 的说明。
    sec = f"　小节《{(p.section or '').strip()}》" if (p.section or "").strip() else ""
    return (f"《{title}》 BV {p.bvid}　"
            f"合集《{sname}》id {got.get('season_id')}{sec}　{got.get('src', '')}")


def bot_alive() -> bool:
    """根据心跳文件判断机器人是否在线（不再用 pgrep —— Windows 没有这个命令）。"""
    return heartbeat.status().get("alive", False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=None,
                   help="监听地址，默认 127.0.0.1（只有本机可访问，最安全）")
    p.add_argument("--port", type=int, default=8088)
    p.add_argument("--allow-insecure", action="store_true",
                   help="忽略「对外暴露且无口令」的风险检查（不推荐）")
    p.add_argument("--parent", type=int, default=0,
                   help="父进程 PID：由 start.py 传入，父进程没了就自动退出")
    args = p.parse_args()
    # 记成模块级：算"残留进程"时要用它把监督进程排除掉（见 _keep_pids）
    global PARENT_PID
    PARENT_PID = int(args.parent or 0)

    # 看门狗：关窗口时 start.py 是被系统强杀的，它的 finally 跑不到，
    # 面板会留在后台继续占端口。所以面板自己盯着父进程，爹没了就走。
    try:
        from parentwatch import start_watch
        start_watch(args.parent, name="webui")
    except Exception:
        pass

    # ⚠️ 面板**自己**也必须登记进程号。
    #    以前只有 start.py 替它登记（start.py 拉起面板时记 web.pid），
    #    而用「打开管理面板.bat」独立启动时面板是主进程，谁都没记它 ——
    #    .bot.pids 里没有它，清理时只剩"按命令行匹配"这一条路。
    #    这条路在 PowerShell 被禁 / wmic 被移除的机器上会返回空，于是
    #    面板明明还在跑（端口还占着），清理却显示"没有残留"——
    #    用户看到的就是"关了但杀不干净"。登记后它一定在清理名单里。
    try:
        import cleanup as _cu
        _cu.register_pid()
        atexit.register(lambda: _cu.unregister_pid())
    except Exception:
        pass

    cfg = load_cfg()
    host = args.host or str(cfg.get("web_host") or "127.0.0.1")
    level, msg = bind_risk(host, bool(str(cfg.get("web_password") or "")))

    if level == "fatal" and not args.allow_insecure:
        print()
        print("!" * 60)
        print(" 已阻止启动：存在高风险")
        print("!" * 60)
        print(f" {msg}")
        print()
        print(" 两种选择：")
        print("   1) 推荐：不要用 --host 0.0.0.0，改回默认（只有本机可访问）")
        print("      python webui.py")
        print("   2) 确实要远程访问：先设口令再启动")
        print("      python security.py --hash 你的口令   # 生成哈希")
        print("      把输出的那行写进 config.yaml，然后重新启动")
        print()
        print(" 明白风险仍要继续：加 --allow-insecure")
        print("!" * 60)
        sys.exit(1)

    if level == "warn":
        print()
        print("~" * 60)
        print(" 安全提醒：" + msg)
        print("~" * 60)

    if not os.path.exists(cfg_path()):
        print("提示：还没找到 config.yaml，请先运行 python bot.py 或复制 config.example.yaml。")
    else:
        _migrate_cfg_once()

    # 收紧含密钥的文件权限
    # ⚠️ 数据文件也要带上真实那一份：用户改过存放位置后，
    #    paths.state_path() 只是默认路径，实际文件可能根本没被收紧。
    _real_state = ""
    try:
        _real_state = db.data_path_from_cfg(load_cfg())
    except Exception:
        pass
    for f in (cfg_path(), paths.state_path(), _real_state,
              paths.key_file(".webui_secret")):
        if f and os.path.exists(f):
            restrict_perms(f)

    # ⚠️ 打印程序目录：登录页/面板"改了没生效"的最常见原因是机器人还在跑
    #    另一个目录（旧的那份），或覆盖解压后没重启。Flask 默认不热加载模板
    #    （jinja_env.auto_reload=False），不重启就拿不到新模板。
    #    把这行打出来，用户一眼能比对"我解压到的目录"和"实际跑的目录"。
    try:
        print(f"面板程序目录 → {os.path.dirname(os.path.abspath(__file__))}")
    except Exception:
        pass
    print(f"管理面板已启动 → http://{host}:{args.port}")
    if str(cfg.get("web_password") or ""):
        print("访问需要口令（登录失败会被限流锁定）")
    AUDIT.log("面板启动", "-", f"host={host} port={args.port}", "info")
    app.run(host=host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
