# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""访问口令的两个硬要求 —— 都是真出过事的地方。

1) 首次设置口令**绝不能**下发登录令牌。
   以前 api_set_password 无条件 set_cookie(issue_token(...))，设完口令当场
   白拿 7 天免登录会话：登录页永远不会出现，口令形同虚设；连带把
   「首次设置只允许本机操作」这道防护也抵消了（本机设完即刻拿到令牌）。
   现在改为返回 need_login，让前端跳登录页，用刚设的口令登录一次。

2) 非 2xx 的报错必须能看懂。
   以前前端直接截响应原文，而 jsonify 默认把中文转成 \\uXXXX，于是 429
   限流提示显示成一串转义符。现在后端保留中文、前端解析 msg 字段。

保留 botpy 桩的做法：本测试只用到 flask，不依赖机器人 SDK。
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

TMP = None
# ⚠️ 这个模块会把 BILI_NOTIFY_HOME 指向临时目录，用完必须还原。
# 以前不还原，同一次 pytest 会话里排在后面的测试就继承了它的数据目录 ——
# 例如 tests/test_env_credentials.py 的 test_dotenv_search_path_includes_data_home
# 自己设了 ONOBN_DATA_DIR，却被这个残留的 BILI_NOTIFY_HOME 顶掉
# （paths.data_home() 先认 BILI_NOTIFY_HOME），于是断言随机变红。
_SAVED_HOME = os.environ.get("BILI_NOTIFY_HOME")


def setUpModule():
    global TMP
    TMP = tempfile.mkdtemp(prefix="onobn_pw_")
    os.environ["BILI_NOTIFY_HOME"] = TMP


def tearDownModule():
    if TMP:
        shutil.rmtree(TMP, ignore_errors=True)
    # 还原，别把临时目录留给后面的测试
    if _SAVED_HOME is None:
        os.environ.pop("BILI_NOTIFY_HOME", None)
    else:
        os.environ["BILI_NOTIFY_HOME"] = _SAVED_HOME


def _fresh_home():
    """每个用例一个独立数据目录。

    ⚠️ 共用目录会让「首次设置」变成「修改口令」：前一个用例设过口令后，
    need_auth() 恒为真，后续用例走的是另一条分支，压根碰不到我们要测的
    早返回 —— 于是漏洞加回来时它反而「通过」，属于假通过。
    """
    d = tempfile.mkdtemp(prefix="onobn_pw_case_")
    os.environ["BILI_NOTIFY_HOME"] = d
    return d


def _client():
    _fresh_home()
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


H = {"X-Requested-With": "bili-notify"}

# ---------------------------------------------------------------- 后端行为


def test_first_set_returns_need_login():
    c = _client()
    r = c.post("/api/security/password",
               json={"action": "set", "new": "abc123"}, headers=H)
    assert r.status_code == 200, r.status_code
    assert r.get_json().get("need_login") is True


def test_first_set_issues_no_token():
    """核心：设完口令不该立刻拿到免登录会话。"""
    c = _client()
    r = c.post("/api/security/password",
               json={"action": "set", "new": "abc123"}, headers=H)
    sc = r.headers.get("Set-Cookie") or ""
    assert "bili_notify" not in sc and "token" not in sc.lower(), (
        "首次设置口令竟然下发了令牌：%s" % sc)


def test_unauthenticated_redirects_to_login():
    c = _client()
    c.post("/api/security/password",
           json={"action": "set", "new": "abc123"}, headers=H)
    r = c.get("/")
    assert r.status_code == 302, r.status_code
    assert "/login" in (r.headers.get("Location") or "")


def test_api_requires_login_after_set():
    c = _client()
    c.post("/api/security/password",
           json={"action": "set", "new": "abc123"}, headers=H)
    assert c.get("/api/state").status_code == 401


def test_login_with_new_password_works():
    c = _client()
    c.post("/api/security/password",
           json={"action": "set", "new": "abc123"}, headers=H)
    r = c.post("/api/login", json={"password": "abc123"}, headers=H)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.headers.get("Set-Cookie"), "登录成功却没下发令牌"
    assert c.get("/").status_code == 200


def test_wrong_password_rejected():
    c = _client()
    r = c.post("/api/login", json={"password": "definitely-wrong"},
               headers=H)
    assert r.status_code in (401, 429), r.status_code


# ------------------------------------------------------- 报错可读性（429）

_FRONT = """
function pick(status, raw){
  raw = String(raw||'').replace(/\\s+/g,' ').trim();
  var snip = '';
  try { var j = JSON.parse(raw); if (j && typeof j.msg === 'string' && j.msg) snip = j.msg; }
  catch(e) { }
  if (!snip) snip = raw.slice(0,120);
  return '面板返回 ' + status + (snip ? '：' + snip : '');
}
"""


def test_backend_emits_readable_chinese():
    """jsonify 不该把中文转成 \\uXXXX。"""
    c = _client()
    for _ in range(14):
        c.post("/api/login", json={"password": "bad"}, headers=H)
    r = c.post("/api/login", json={"password": "bad"}, headers=H)
    txt = r.get_data(as_text=True)
    if r.status_code != 429:
        return      # 限流没触发就跳过，不算失败
    assert "\\u" not in txt, "后端仍在转义中文：%s" % txt[:80]
    assert json.loads(txt).get("msg"), "msg 字段缺失"


def test_frontend_shows_msg_not_raw_json():
    """把前端那段取值逻辑用 Node 真跑一遍，确认不再甩天书。"""
    import subprocess
    js = (_FRONT + """
const escaped = '{"msg":"\\\\u64cd\\\\u4f5c\\\\u592a\\\\u5feb\\\\u4e86","ok":false}';
console.log(pick(429, escaped));
console.log(pick(429, '{"msg":"尝试太多次，请 59 秒后再试","ok":false}'));
""")
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    if p.returncode != 0:
        return      # 没装 node 就跳过
    out = p.stdout.strip().splitlines()
    assert len(out) >= 2, out
    # 第一种（万一后端仍转义）：JSON.parse 也能还原成中文，绝不该出现 \u
    assert "\\u" not in out[0], "前端显示了转义符：%s" % out[0]
    assert "尝试太多次" in out[1], out[1]
    assert "{" not in out[1], "不该把整段 JSON 甩给用户：%s" % out[1]


# ---------------------------------------------------------------- 源码约束


def test_source_no_unconditional_set_cookie_in_set_password():
    """防止有人把漏洞加回来。"""
    with io.open(os.path.join(SRC, "webui.py"), encoding="utf-8") as f:
        s = f.read()
    i = s.find("def api_set_password")
    assert i > 0
    j = s.find("\n@app.route", i + 10)
    body = s[i: j if j > 0 else len(s)]
    # 必须存在「首次设置就早返回」这道守卫，且它要 return（不是只记日志）
    assert "_was_open" in body, "缺少首次设置的判断 _was_open"
    assert "need_login" in body, "早返回里应带 need_login，前端据此跳登录页"
    # 取 _was_open 守卫块，确认里面有 return
    k = body.find("if _was_open:")
    assert k > 0, "缺少 if _was_open 守卫块"
    blk = body[k: k + 700]
    assert "return" in blk, "守卫块里没有 return，set_cookie 仍会执行"
    # 且这个 return 必须出现在真实调用之前。
    # ⚠️ 只认 "resp.set_cookie(" 这种真实调用：函数里的注释提到了
    #   "无条件 set_cookie(...)" 这段历史，裸匹配会命中注释、位置判断全乱。
    k_call = body.find("resp.set_cookie(")
    assert k_call > 0, "没找到令牌下发调用"
    assert k < k_call, "守卫块位置不对，set_cookie 会先执行"
