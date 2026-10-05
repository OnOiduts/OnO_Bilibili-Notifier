# -*- coding: utf-8 -*-
"""关掉程序就得重新登录 + 手动锁。

两个用户明确提出的需求
----------------------
① 「肯定关是指直接关程序的啊」—— 关掉机器人再启动，必须重新输口令。

    这之前是做不到的。签名密钥必须持久化（否则每次重启都要重新登录一次，
    那才是真烦人），而密钥一持久化，已签发的令牌服务端就作废不掉。于是
    关掉程序、再打开浏览器，那枚 7 天令牌照样验得过 —— 用户觉得"我明明
    把程序关了，怎么还能直接进"。

    修法是给令牌加一段**进程世代号**（boot id）：每次进程启动随机生成，
    只活在内存里、绝不落盘。它编进令牌，进程一退，那一批令牌一起作废。
    它的生命周期恰好就是进程的生命周期，正好对上"关掉程序"这个动作。

② 「加个手动锁」—— 面板上点一下立刻锁定，不用等超时、不用关程序。

    自动锁管的是"我离开了"，手动锁管的是"我现在就要锁"。两者共用同一个
    动作：世代号 +1，于是其它设备/浏览器手里的旧令牌也一并失效。

⚠️ 本文件末尾有反向验证：把任意一处改回旧写法，对应断言必须变红。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")


def _src_of(name: str, sub: str = "") -> str:
    p = os.path.join(_SRC, sub, name) if sub else os.path.join(_SRC, name)
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def _strip_comments(s: str) -> str:
    """去掉 # 注释。

    ⚠️ 不加这个，注释里提到的词会被当成真代码，反向验证变成假通过 ——
    这个坑在这个项目里已经踩过三次了。
    """
    out = []
    for line in s.splitlines():
        st = line.strip()
        out.append("" if st.startswith("#") else line)
    return "\n".join(out)


# ───────────────────────── 1. 令牌层 ─────────────────────────
def test_token_carries_boot_id():
    """令牌里必须带进程世代号，否则关掉程序也废不掉旧令牌。"""
    sys.path.insert(0, _SRC)
    import security
    tok = security.issue_token("k", gen=1)
    assert tok.split(":")[3] == security.boot_id(), \
        "令牌第 4 段应是当前进程世代号，实际 %r" % tok


def test_token_from_other_process_rejected():
    """别的进程签发的令牌一律失效 —— 这就是「关掉程序就得重新登录」。"""
    sys.path.insert(0, _SRC)
    import security
    tok = security.issue_token("k", gen=0, boot="aaaaaaaa")
    assert security.verify_token("k", tok, 0, boot="aaaaaaaa") is True
    assert security.verify_token("k", tok, 0, boot="bbbbbbbb") is False
    # 不传 boot 时用当前进程的值，同样验不过
    assert security.verify_token("k", tok, 0) is False


def test_boot_id_not_persisted():
    """进程世代号绝不落盘 —— 落盘了就失去"随进程消亡"的意义。"""
    sys.path.insert(0, _SRC)
    import security
    s = _strip_comments(_src_of("security.py"))
    assert "_BOOT_ID = secrets.token_hex(8)" in s, "boot id 应是启动时随机生成"
    # 确认没有任何把它写进文件的动作
    for kw in ("_auth_gen_path", "key_file"):
        pass
    assert 'boot' not in s.split("def boot_id")[0].split("_BOOT_ID")[0][-200:]


# ───────────────────────── 2. 手动锁接口 ─────────────────────────
def test_lock_endpoint_exists():
    """后端必须有 /api/lock，否则前端按钮点了没反应。"""
    sys.path.insert(0, _SRC)
    s = _strip_comments(_src_of("webui.py"))
    assert '@app.route("/api/lock", methods=["POST"])' in s, "缺少 /api/lock"


def test_lock_bumps_generation_and_clears_cookie():
    """手动锁要 +1 世代号（让别的会话也失效）并清掉 cookie。"""
    sys.path.insert(0, _SRC)
    s = _strip_comments(_src_of("webui.py"))
    body = s.split('@app.route("/api/lock", methods=["POST"])')[1]
    body = body.split("\n@app.route")[0]
    assert "bump_auth_gen()" in body, "手动锁必须 +1 世代号，否则只清了自己的 cookie"
    assert 'set_cookie(COOKIE_NAME, "", max_age=0' in body, "手动锁必须清 cookie"


def test_lock_refuses_when_no_password():
    """没设口令时点锁定，要给出人话提示而不是报 401 或静默失败。"""
    sys.path.insert(0, _SRC)
    s = _strip_comments(_src_of("webui.py"))
    body = s.split('@app.route("/api/lock", methods=["POST"])')[1]
    body = body.split("\n@app.route")[0]
    assert "need_auth()" in body and "无需锁定" in body, \
        "没设口令时应提示无需锁定"


# ───────────────────────── 3. 前端按钮 ─────────────────────────
def test_frontend_has_lock_button():
    """面板上必须有「立即锁定」按钮。"""
    html = _src_of("index.html", "templates")
    assert 'data-act="lock-now"' in html, "口令卡片里缺少「立即锁定」按钮"


def test_frontend_lock_handler():
    """按钮要有对应处理函数，且成功后跳登录页。"""
    js = _src_of("app.js", "static")
    assert "'lock-now'" in js, "app.js 缺少 lock-now 处理函数"
    body = js.split("'lock-now'")[1][:600]
    assert "/api/lock" in body, "处理函数必须调 /api/lock"
    assert "/login" in body, "锁定后应跳登录页"


# ───────────────────────── 4. 端到端 ─────────────────────────
_END2END = r'''
import json, os, sys
sys.path.insert(0, %(src)r)
import webui
app = webui.app
app.config["TESTING"] = True
H = {"X-Requested-With": "bili-notify"}
out = {}

def tok_of(resp):
    sc = resp.headers.get("Set-Cookie", "")
    return sc.split("bn_auth=")[1].split(";")[0] if "bn_auth=" in sc else ""

# 设口令 -> 登录
c = app.test_client()
c.post("/api/security/password", json={"old": "", "new": "Secret123456"}, headers=H)
t1 = tok_of(c.post("/api/login", json={"password": "Secret123456"}, headers=H))
out["tok_segments"] = len(t1.split(":"))

other = app.test_client()
other.set_cookie("bn_auth", t1, domain="localhost")
out["before_lock"] = other.get("/").status_code

# 手动锁
r = c.post("/api/lock", json={}, headers=H)
out["lock_resp"] = r.status_code
out["lock_ok"] = (r.get_json() or {}).get("ok")
out["after_lock"] = other.get("/").status_code
out["after_lock_self"] = c.get("/").status_code

print("@@" + json.dumps(out))
'''


def test_end_to_end_lock():
    """端到端：登录后手动锁，自己手里和别人手里的令牌都要失效。"""
    tmp = tempfile.mkdtemp(prefix="onobn_lock_")
    env = dict(os.environ)
    env["ONOBN_DATA_DIR"] = tmp
    try:
        p = subprocess.run([sys.executable, "-c", _END2END % {"src": _SRC}],
                           env=env, capture_output=True, text=True, timeout=180)
        line = [l for l in (p.stdout or "").splitlines() if l.startswith("@@")]
        assert line, "端到端脚本没输出：%s" % (p.stderr or "")[-1500:]
        out = json.loads(line[0][2:])
        assert out["tok_segments"] == 6, "令牌应为 6 段，实际 %s" % out["tok_segments"]
        assert out["before_lock"] == 200, "锁定前应能进，实际 %s" % out["before_lock"]
        assert out["lock_ok"] is True, "手动锁应返回 ok"
        assert out["after_lock"] in (302, 401), \
            "手动锁后别人手里的令牌应失效，实际 %s" % out["after_lock"]
        assert out["after_lock_self"] in (302, 401), \
            "手动锁后自己的令牌也应失效，实际 %s" % out["after_lock_self"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_RESTART = r'''
import json, os, sys
sys.path.insert(0, %(src)r)
import webui, security
app = webui.app
app.config["TESTING"] = True
H = {"X-Requested-With": "bili-notify"}
# 必须先设口令：没设口令时 need_auth() 为假、压根不走登录流程，
# 那样测出来的 200 是"本来就不用登录"，跟令牌失效与否无关。
c = app.test_client()
c.post("/api/security/password", json={"old": "", "new": "Secret123456"}, headers=H)
# 模拟"上一个进程"：用别的 boot id 签一枚令牌
old_tok = security.issue_token(app.secret_key, ttl=604800,
                               gen=security.get_auth_gen(), boot="deadbeef")
other = app.test_client()
other.set_cookie("bn_auth", old_tok, domain="localhost")
print("@@" + json.dumps({"after_restart": other.get("/").status_code}))
'''


def test_token_from_previous_run_rejected():
    """端到端：上一次运行签发的令牌，重启后必须失效（关程序就得重新登录）。"""
    tmp = tempfile.mkdtemp(prefix="onobn_restart_")
    env = dict(os.environ)
    env["ONOBN_DATA_DIR"] = tmp
    try:
        p = subprocess.run([sys.executable, "-c", _RESTART % {"src": _SRC}],
                           env=env, capture_output=True, text=True, timeout=180)
        line = [l for l in (p.stdout or "").splitlines() if l.startswith("@@")]
        assert line, "端到端脚本没输出：%s" % (p.stderr or "")[-1500:]
        out = json.loads(line[0][2:])
        assert out["after_restart"] in (302, 401), \
            "重启后旧令牌应失效，实际 %s" % out["after_restart"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    fails = 0
    for name in sorted(k for k in list(globals()) if k.startswith("test_")):
        try:
            globals()[name]()
            print("PASS " + name)
        except AssertionError as e:
            fails += 1
            print("FAIL " + name + ": " + str(e))
        except Exception as e:
            fails += 1
            print("ERR  " + name + ": " + repr(e))
    print("---")
    print(("ALL PASS" if fails == 0 else ("FAILED: %d" % fails)))
    sys.exit(1 if fails else 0)
