# -*- coding: utf-8 -*-
"""「测试推送」也能真 @全体：真跑 /api/test，查真正发出去的消息体。

以前 /api/test 直接 send_markdown，完全不经过 @全体 逻辑 —— 用户点测试
按钮永远看不到 @，会以为整个功能是坏的。这里锁住：
  · 默认不带 at_all（不消耗平台侧每天唯一的 1 次额度）
  · 勾了且 kind=live 时，默认把官方标签合并进同一张卡片
  · 关掉「合并进卡片」时回退到单独一条 @ 纯文本
  · 平台拒收带标签那条时去掉标签重发同一张卡片、回执说明原因
  · 面板开关默认关、前端确实提交该参数
"""
import asyncio
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(os.path.dirname(HERE), "src")
BASE = os.environ.get("BILI_TRUE_SRC") or REAL
STUBS = os.path.join(HERE, "_stubs")
for p in (STUBS, REAL, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, BASE)

import webui  # noqa: E402
import atall  # noqa: E402

OK = []
GID = "G_TESTPUSH_1"


def ck(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    OK.append(bool(cond))
    return bool(cond)


class FakeQQ:
    """记录真正发出去的消息体；可模拟平台拒收带 @全体 的那一条。"""

    def __init__(self, *a, **kw):
        self.sent = []
        self.reject_tag = bool(kw.pop("reject_tag", False))

    async def send_text(self, gid, content, **kw):
        if self.reject_tag and atall.AT_ALL_TAG in content:
            raise RuntimeError("HTTP 400: 机器人非群管理员或今日额度已用")
        self.sent.append(("text", gid, content))
        return {}

    async def send_markdown(self, gid, markdown, keyboard=None, **kw):
        if self.reject_tag and atall.AT_ALL_TAG in markdown:
            raise RuntimeError("HTTP 400: 机器人非群管理员或今日额度已用")
        self.sent.append(("markdown", gid, markdown))
        return {}

    async def close(self):
        return None

    def bodies(self, kind=None):
        return [b for k, g, b in self.sent if kind is None or k == kind]


def setup(reject_tag=False, merge=True):
    holder = {}

    def factory(*a, **kw):
        holder["qq"] = FakeQQ(reject_tag=reject_tag)
        return holder["qq"]

    webui.QQClient = factory
    webui.creds_ready = lambda c: True
    webui.load_cfg = lambda: {
        "appid": "1", "secret": "s", "is_sandbox": False,
        "send_image": False, "templates": {},
        "at_all_merge": merge,
    }
    webui._apply_render_cfg = lambda c: None
    webui.validate_gid = lambda g: g
    webui.AUDIT = type("A", (), {"log": lambda *a, **k: None})()
    webui.need_auth = lambda: False
    webui.app.config["TESTING"] = True
    return holder


def post(payload):
    with webui.app.test_client() as c:
        r = c.post("/api/test", json=payload,
                   headers={"X-Requested-With": "bili-notify"})
        try:
            return r.get_json() or {}
        except Exception:
            return {}


def src(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()


def main():
    tmp = tempfile.mkdtemp(prefix="atall_testpush_")
    try:
        # ① 默认：测试推送不带 @，不消耗额度
        h = setup()
        d = post({"gid": GID, "kind": "live", "real": False, "mark": True})
        ck("默认不发 @ 文本", not h["qq"].bodies("text"))
        ck("默认卡片照常发", bool(h["qq"].bodies("markdown")))
        ck("默认回执不提 @全体", "@全体" not in str(d.get("msg", "")))

        # ② 勾了 at_all 且是开播：默认把 @ 合成进同一张卡片
        h = setup()
        d = post({"gid": GID, "kind": "live", "real": False,
                  "mark": True, "at_all": True})
        tx = h["qq"].bodies("text")
        md = h["qq"].bodies("markdown")
        ck("默认只发一条卡片（@ 合成在内）",
           len(h["qq"].sent) == 1 and not tx)
        ck("卡片 content 最前面是官方 @全体 标签",
           bool(md) and md[0].startswith(atall.AT_ALL_TAG + "\n"))
        ck("卡片正文没被标签吃掉",
           bool(md) and len(md[0]) > len(atall.AT_ALL_TAG) + 20)
        ck("回执说明额度已被测试消耗",
           "额度" in str(d.get("msg", "")) and "@全体" in str(d.get("msg", "")))

        # ②b 关掉「合并进卡片」→ 回到单独一条纯文本（真机已验证）
        h = setup(merge=False)
        post({"gid": GID, "kind": "live", "real": False,
              "mark": True, "at_all": True})
        tx = h["qq"].bodies("text")
        md = h["qq"].bodies("markdown")
        ck("回退模式另发一条 @ 纯文本",
           len(tx) == 1 and tx[0].startswith(atall.AT_ALL_TAG + "\n"))
        ck("回退模式卡片不带标签",
           len(md) == 1 and atall.AT_ALL_TAG not in md[0])
        ck("回退模式 @ 文本先发、卡片后发",
           h["qq"].sent[0][0] == "text" and h["qq"].sent[-1][0] == "markdown")

        # ③ 非开播类型：勾了也不 @
        h = setup()
        post({"gid": GID, "kind": "video", "real": False,
              "mark": True, "at_all": True})
        ck("非开播类型不 @", not h["qq"].bodies("text"))

        # ④ 平台拒收带标签那条：去掉标签重发同一张卡片，测试不白跑
        h = setup(reject_tag=True)
        d = post({"gid": GID, "kind": "live", "real": False,
                  "mark": True, "at_all": True})
        md = h["qq"].bodies("markdown")
        ck("@ 被拒时重发一张不带标签的卡片",
           len(md) == 1 and atall.AT_ALL_TAG not in md[0])
        ck("@ 被拒时回执说明原因", "未生效" in str(d.get("msg", "")))

        # ⑤ 未 @ 的原因可判定（真值，不是字符串匹配）
        q = atall.AtAllQuota(path=os.path.join(tmp, "q.json"),
                             now=lambda: 1000000.0)
        ck("初始状态可 @", q.explain(GID) == "" and q.available(GID))
        q.mark_used(GID)
        ck("用过后 explain 说明今日额度已用", "今天已" in q.explain(GID))
        q2 = atall.AtAllQuota(path=os.path.join(tmp, "q2.json"),
                              now=lambda: 2000000.0)
        q2.mark_failed(GID)
        ck("被拒后 explain 说明冷却中", "冷却" in q2.explain(GID))

        # ⑥ 面板开关默认关 + 前端确实提交
        h = src("templates/index.html")
        a = src("static/app.js")
        w = src("webui.py")
        ck("面板新增「测试也 @全体」开关", 'data-opt="atall"' in h)
        ck("新开关默认关（不带 on）",
           'data-opt="atall"></button>' in h
           and 'data-opt="atall" class="sw-btn on"' not in h)
        ck("前端默认 false", "atall: false" in a)
        ck("两处 /api/test 都提交 at_all", a.count("at_all: testOpt('atall', false)") == 2)
        ck("后端默认关且只对 live 生效",
           'd.get("at_all") is True and kind == "live"' in w)
        ck("后端默认把 @ 合并进卡片，关开关才走 send_text",
           'merge_at = bool(at_all and cfg.get("at_all_merge", True))' in w
           and "qq.send_text(gid, probe)" in w
           and "md_body = (atall.AT_ALL_TAG" in w)
        ck("面板新增「@全体 合并进卡片」开关且默认开",
           'id="cAtAllMerge" checked' in h)
        ck("前端提交 at_all_merge",
           "at_all_merge: !$('cAtAllMerge')" in a)
        ck("测试 @ 成功后同步占掉机器人侧当天配额",
           "def _mark_test_at_used" in w
           and w.count("_mark_test_at_used(gid)") == 2
           and "at_all_quota.json" in w)
    finally:
        for f in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, f))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass

    bad = OK.count(False)
    print()
    print(f"{len(OK) - bad}/{len(OK)}")
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
