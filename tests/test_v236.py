# -*- coding: utf-8 -*-
"""v2.3.6 真值测试：群名替代 openid / 登录态判定 / @全体 配额同步。

不验「源码里有没有这段字」为主：
  · grouplabel / _gname / no_perm_hint / BiliClient / AtAllQuota 都是真跑
  · /api/state 用 Flask 请求上下文真调一次，查返回的 label
  · 前端 gLabel / biliEff 切进 node 的 vm 里真执行
  · 反向验证：把修复逐条退回旧写法，对应断言必须变红

BILI_TRUE_SRC 指向解包后的 src 时，测的就是**包内代码**。
"""
import json
import os
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(os.path.dirname(HERE), "src")
BASE = os.environ.get("BILI_TRUE_SRC") or REAL
STUBS = os.path.join(HERE, "_stubs")
DEPS = os.environ.get("ONOBN_DEPS", "/data/workspace/.deps")
for p in (DEPS, STUBS, REAL, BASE):
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, BASE)

sys.modules.setdefault("media", types.SimpleNamespace(
    send_image=None, remember_recent=lambda *a, **k: None))

import botpy  # noqa: E402  (来自 tests/_stubs)
import grouplabel  # noqa: E402
import atall  # noqa: E402
from bilibili import BiliClient  # noqa: E402
from bot import NotifyBot, no_perm_hint  # noqa: E402
import webui  # noqa: E402

FAILS = []


def ck(name, cond):
    if cond:
        print("  [ok]", name)
    else:
        print("  [FAIL]", name)
        FAILS.append(name)


def src_text(rel):
    with open(os.path.join(BASE, rel), encoding="utf-8") as f:
        return f.read()


class FakeStore:
    """最小可用的 Store，只喂 /api/state 需要的那几个方法。"""

    def __init__(self, groups=None):
        self.data = {"groups": groups or {
            "6FBBD9B5AB0B0A1693C81DEEF180ADDD": {"name": "试剂管理室", "subs": {}},
            "1BA9FBD0AB0B0A1693C81DEEF180ADDD": {"name": "", "subs": {}},
            "42901C95AB0B0A1693C81DEEF180ADDD": {"name": "", "subs": {}},
        }}
        self.ups = {}

    def get_group(self, gid):
        return self.data["groups"].get(gid, {}) or {}

    def all_groups(self):
        return list(self.data["groups"].keys())

    def get_up(self, uid):
        return {}

    def seasons_of_group(self, gid):
        return []

    def get_season_templates(self, *a, **k):
        return {}

    def sub_stats(self):
        return {"ups": 0, "seasons": 0, "total": 0}

    def up_tpl_counts(self, gid):
        return {}

    def groups_with_meta(self):
        return [{"gid": g} for g in self.data["groups"]]


GIDS = list(FakeStore().data["groups"].keys())


def section(t):
    print("\n---", t, "---")


# ---------------------------------------------------------------- A 群显示名
section("A group_label 真值")
ck("有群名时直接返回群名",
   grouplabel.group_label(GIDS[0], "试剂管理室", GIDS) == "试剂管理室")
ck("无群名给稳定编号（按 gid 排序，与传入顺序无关）",
   grouplabel.group_label(GIDS[1], "", list(reversed(GIDS)))
   == grouplabel.group_label(GIDS[1], "", GIDS)
   and grouplabel.group_label(GIDS[1], "", GIDS).startswith("未命名群"))
lab = grouplabel.labels_of(FakeStore())
ck("labels_of 里实名群用实名", lab[GIDS[0]] == "试剂管理室")
ck("多个未命名群编号互不相同",
   len({lab[GIDS[1]], lab[GIDS[2]]}) == 2)
ck("未命名群显示名里不含 openid 片段",
   all("6FBB" not in v and "1BA9" not in v and "4290" not in v
       for v in lab.values()))
ck("顺序打乱后 labels_of 结果完全一致",
   grouplabel.labels_of(FakeStore()) == lab)
ck("group_label 不回退到 openid",
   "AB0B0A16" not in grouplabel.group_label(GIDS[2], "", GIDS))

# ---------------------------------------------------------------- B 机器人侧
section("B 机器人侧日志真值")
bot = NotifyBot.__new__(NotifyBot)
bot.store = FakeStore()
ck("_gname 有名字返回名字", bot._gname(GIDS[0]) == "试剂管理室")
ck("_gname 无名字不吐 openid", "1BA9" not in bot._gname(GIDS[1]))
ck("_gname 与 webui 口径一致",
   bot._gname(GIDS[1]) == grouplabel.labels_of(FakeStore())[GIDS[1]])
ck("no_perm_hint 带 label 时写群名、不写 ID",
   "1BA9" not in no_perm_hint(GIDS[1], bot._gname(GIDS[1]))
   and bot._gname(GIDS[1]) in no_perm_hint(GIDS[1], bot._gname(GIDS[1])))
ck("no_perm_hint 不带 label 时绝不吐 ID",
   "1BA9" not in no_perm_hint(GIDS[1]))
bot_src = src_text("bot.py")
ck("bot.py 运行日志里已无 8 位 openid 截断",
   "gid[:8]" not in bot_src and "gid[:12]" not in bot_src)
ck("bot.py 里 _gname 确实调 grouplabel",
   "grouplabel.group_label(" in bot_src)

# ---------------------------------------------------------------- C 前端真值
section("C 前端 gLabel / biliEff（node vm 真跑）")
js = src_text("static/app.js")


def js_func(name):
    i = js.index("function %s(" % name)
    j = js.index("\n}", i) + 2
    return js[i:j]


probe = """
const vm = require('vm');
%s
%s
var STATE = { heartbeat: {}, config: {}, groups: [] };
var BILI_STATE = {};
function setHb(hb) { STATE.heartbeat = hb; }
function setBs(bs) { BILI_STATE = bs; }
var out = [];
out.push(gLabel({label:'群A'}));
out.push(gLabel({name:'手动备注'}));
out.push(gLabel({gid:'6FBBD9B5AB0B0A16'}));
out.push(gLabel(null));
setBs({state:'logged', has_login:true, expired:false});
setHb({alive:true, bili_login:{ok:false}});
out.push(biliEff());
setHb({alive:true, bili_auth_bad:{bad:true}});
out.push(biliEff());
setBs({state:'none', has_login:false, expired:false});
setHb({alive:true});
out.push(biliEff());
setBs({state:'logged', has_login:true, expired:true});
out.push(biliEff());
console.log(JSON.stringify(out));
""" % (js_func("gLabel"), js_func("biliEff"))
try:
    r = subprocess.run(["node", "-e", probe], capture_output=True,
                       text=True, timeout=30)
    vals = json.loads(r.stdout.strip().splitlines()[-1])
except Exception as e:
    vals = []
    print("  [ENV] node vm failed:", e)
ck("gLabel 优先用 label", len(vals) > 0 and vals[0] == "群A")
ck("gLabel 兼容旧数据 name", len(vals) > 1 and vals[1] == "手动备注")
ck("gLabel 无名字不吐 openid", len(vals) > 2 and vals[2] == "未命名群")
ck("gLabel 空对象也不吐 ID", len(vals) > 3 and vals[3] == "未命名群")
ck("瞬时 bili_login.ok=false 不再覆盖当场求证（仍 logged）",
   len(vals) > 4 and vals[4] == "logged")
ck("连续确认 bili_auth_bad 才判失效", len(vals) > 5 and vals[5] == "invalid")
ck("未登录仍是 none", len(vals) > 6 and vals[6] == "none")
ck("本地判定过期仍是 invalid", len(vals) > 7 and vals[7] == "invalid")
ck("app.js 已无 8/16 位 openid 显示截断",
   "slice(0, 16)" not in js and "slice(0,8)" not in js
   and "g.gid.slice(0, 8)" not in js and "String(x.gid).slice(0, 8)" not in js)
ck("app.js 的 GROUP_NAME 不再回退到 gid",
   "GROUP_NAME[g.gid] = gLabel(g);" in js)
idx = src_text("templates/index.html")
ck("登记表单注明只在此处填 openid",
   "仅登记新群时填" in idx and "登记后一律显示群名" in idx)

# ---------------------------------------------------------------- D /api/state
section("D /api/state 真值")
_orig_store, _orig_cfg = webui.get_store, webui.load_cfg
try:
    webui.get_store = lambda: FakeStore()
    webui.load_cfg = lambda: {}
    with webui.app.test_request_context("/api/state"):
        d = webui.api_state().get_json()
    groups = d.get("groups") or []
    ck("/api/state 返回了群", len(groups) == 3)
    ck("每个群都带 label", all("label" in g for g in groups))
    ck("label 与 grouplabel.labels_of 完全一致",
       {g["gid"]: g["label"] for g in groups} == lab)
    ck("label 里不含 openid 片段",
       all("6FBB" not in g["label"] and "1BA9" not in g["label"]
           for g in groups))
    ck("gid 仍作为内部键保留（不是删数据）",
       all(g["gid"] in GIDS for g in groups))
finally:
    webui.get_store, webui.load_cfg = _orig_store, _orig_cfg
w_src = src_text("webui.py")
ck("审计日志不再写裸 gid", "gid[:20]" not in w_src and "f\"{gid} uid=" not in w_src)
ck("刷新群名回执带 label", '"label"] = _dl.get' in w_src)
ck("_group_name_map 改用 grouplabel", "grouplabel.labels_of(get_store())" in w_src)

# ---------------------------------------------------------------- E 配额同步
section("E @全体 测试额度同步真值")
tmp = tempfile.mktemp(suffix=".json")
_orig_q = webui.atall.AtAllQuota
_RealQ = atall.AtAllQuota


class _TmpQuota(_RealQ):
    def __init__(self, *a, **k):
        super().__init__(path=tmp)


try:
    webui.atall.AtAllQuota = _TmpQuota
    webui._mark_test_at_used("TESTGID")
    q = _RealQ(path=tmp)
    ck("_mark_test_at_used 真让该群当天额度变不可用",
       q.available("TESTGID") is False)
    ck("其他群不受影响", q.available("OTHERGID") is True)
finally:
    webui.atall.AtAllQuota = _orig_q
    try:
        os.remove(tmp)
    except OSError:
        pass
ck("测试推送两个成功分支都同步配额",
   w_src.count("_mark_test_at_used(gid)") == 2)
ck("降级重发（去掉标签）不占配额",
   w_src.count("_mark_test_at_used(gid)") == 2
   and 'run_async(qq.send_markdown(gid, md, keyboard=keyboard))' in w_src)
ck("@全体 注释已订正（不再说合成模式没验证过）",
   "两种模式都已**真机验证生效**" in bot_src)

# ---------------------------------------------------------------- F 登录态阈值
section("F BiliClient 认证阈值真值")
c = BiliClient.__new__(BiliClient)
c._auth_hits, c._auth_bad, c._auth_at, c._auth_reason = 0, False, 0.0, ""
c._note_auth("code=-352")
ck("单次 -352 不判 cookie 失效", c.auth_bad() is False)
c._note_auth("code=-352")
ck("两次仍不判失效", c.auth_bad() is False)
c._note_auth("code=-352")
ck("连续 3 次才判失效", c.auth_bad() is True)
c._clear_auth()
ck("任一接口正常返回即清零", c.auth_bad() is False and c._auth_hits == 0)
ck("HTTP 412 归重试/风控、不再算认证码",
   412 in BiliClient.RETRY_STATUS and 412 not in BiliClient.AUTH_STATUS)
ck("-352 仍属认证类码（只是要连续确认）", -352 in BiliClient.AUTH_CODES)

# ---------------------------------------------------------------- G 版本与日志
section("G 版本与去重日志")
v = src_text("VERSION").strip()
ck("VERSION = 2.3.6", v == "2.3.6")
cl = src_text("../docs/CHANGELOG.md") if os.path.isfile(
    os.path.join(BASE, "../docs/CHANGELOG.md")) else ""
first = cl.splitlines()[0] if cl else ""
ck("CHANGELOG 首节是 v2.3.6", cl.startswith("## v2.3.6"))
ck("v2.3.6 按「同一问题只记一条」合并（主标题写合并 10 项）",
   "合并 10 项" in first)
ck("群名那条单列、不再重复记旧版群名修复",
   cl.count("群显示名统一") == 1)

# ---------------------------------------------------------------- H 反向验证
section("H 反向验证（退回旧写法后，对应断言必须变红）")
bl_src = src_text("bilibili.py")


def static_checks(b, j, bl, w):
    """正向断言在给定源码文本上的结果。"""
    return {
        "label": ("gid[:8]" not in b
                  and "GROUP_NAME[g.gid] = g.name || g.gid;" not in j),
        "bilieff": ("bl.ok === false" not in j
                    and "ab.bad === true" in j),
        "auth": "self._auth_hits >= self.AUTH_CONFIRM" in bl,
        "quota": w.count("_mark_test_at_used(gid)") == 2,
    }


base = static_checks(bot_src, js, bl_src, w_src)
ck("正向：四项静态断言全为真", all(base.values()))


def mut_label(b, j, bl, w):
    b = b.replace("self._gname(gid)}", "gid[:8]}…")
    j = j.replace("GROUP_NAME[g.gid] = gLabel(g);",
                  "GROUP_NAME[g.gid] = g.name || g.gid;")
    return b, j, bl, w


def mut_eff(b, j, bl, w):
    j = j.replace("  var ab = hb.bili_auth_bad || null;",
                  "  var bl = hb.bili_login || null;")
    j = j.replace("if (hb.alive && ab && ab.bad === true) st = 'invalid';",
                  "if (hb.alive && bl && bl.ok === false) st = 'invalid';")
    return b, j, bl, w


def mut_auth(b, j, bl, w):
    bl = bl.replace("        if self._auth_hits >= self.AUTH_CONFIRM:\n"
                    "            self._auth_bad = True",
                    "        self._auth_bad = True")
    return b, j, bl, w


def mut_quota(b, j, bl, w):
    w = w.replace("                _mark_test_at_used(gid)\n", "")
    return b, j, bl, w


variants = [
    ("群名显示（bot 日志 + 前端 GROUP_NAME）", "label", mut_label),
    ("biliEff 瞬时覆盖", "bilieff", mut_eff),
    ("_note_auth 单次即失效", "auth", mut_auth),
    ("测试配额同步", "quota", mut_quota),
]
for title, key, mut in variants:
    b2, j2, bl2, w2 = mut(bot_src, js, bl_src, w_src)
    got = static_checks(b2, j2, bl2, w2)
    ck("[反向] %s → %s 变红" % (title, key), got[key] is False)
    ck("[反向] %s 不误伤其它三项" % title,
       all(v for k, v in got.items() if k != key))

# 动态反向：把 biliEff 退回旧写法后，真的在 node 里复现「刚扫码却未生效」
j_old = mut_eff(bot_src, js, bl_src, w_src)[1]
_i = j_old.index("function biliEff(")
_k = j_old.index("\n}", _i) + 2
dyn = ("var STATE={heartbeat:{alive:true,bili_login:{ok:false}},config:{}};\n"
       "var BILI_STATE={state:'logged',has_login:true,expired:false};\n"
       + j_old[_i:_k] + "\nconsole.log(biliEff());\n")
try:
    r = subprocess.run(["node", "-e", dyn], capture_output=True,
                       text=True, timeout=30)
    ck("[反向·真跑] 退回瞬时覆盖后 biliEff 重现「未生效」误报",
       r.stdout.strip() == "invalid")
except Exception as e:
    ck("[反向·真跑] 退回瞬时覆盖后 biliEff 重现「未生效」误报", False)
    print("  [ENV] node:", e)

print("\n==== v2.3.6 tests: %d FAIL ====" % len(FAILS))
if FAILS:
    for f in FAILS:
        print("   -", f)
sys.exit(1 if FAILS else 0)
