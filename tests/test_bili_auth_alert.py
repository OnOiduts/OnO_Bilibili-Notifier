# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""B 站登录态"实际失效"提醒：连续 N 次才认定、认定后只提醒一次。

与 test_bili_expiry_alert 的区别：那个管"剩余天数"，这个管"B站 当轮
到底认不认"。被风控踢下线时剩余天数可能还有 179 天，到期提醒永远
触发不了，只能靠这个。

⚠️ 这些断言做过反向验证：把改动退回旧写法，断言必须变红。
   否则就是"摆设测试"——代码坏了照样全绿。
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import bili_auth_alert as A  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + (("  ← " + extra) if extra else ""))


# ---------- 阈值：连续 5 次才认定 ----------
a = A.AuthAlert(streak=5)
for i in range(1, 5):
    r = a.feed(True)
    check(f"第 {i} 次失败还不认定", r["need_alert"] is False,
          f"count={r['count']}")
r5 = a.feed(True)
check("第 5 次失败 → 认定", r5["need_alert"] is True, f"count={r5['count']}")
check("认定后 count 是 5", r5["count"] == 5)
check("第 5 次是 reached（日志喊一次）", r5.get("reached") is True)
check("第 6 次不再是 reached", a.feed(True).get("reached") is False)

# ---------- 只有推送成功才算"通知过" ----------
# ⚠️ 不能 feed 一达到阈值就记账：沙盒群没配、或发送失败时
#    会被误记成"已通知"，用户配好群之后反而永远收不到。
b_ok = A.AuthAlert(streak=5)
for _ in range(5):
    b_ok.feed(True)
check("未推送前仍待提醒", b_ok.feed(True)["need_alert"] is True)
check("未推送前不算已通知", b_ok.fired is False, f"fired={b_ok.fired}")
b_ok.mark_sent()                       # 模拟推送成功
check("推送成功后不再提醒", b_ok.feed(True)["need_alert"] is False)
check("推送成功后记为已通知", b_ok.fired is True)
for _ in range(20):
    b_ok.feed(True)
check("连推 25 轮也只通知一次", b_ok.fired is True)

# ---------- 中间成功一次 → 清零（防误报） ----------
a2 = A.AuthAlert(streak=5)
a2.feed(True)
a2.feed(True)
a2.feed(True)
r_ok = a2.feed(False)
check("成功一次 → 计数清零", r_ok["count"] == 0)
check("成功一次 → 报告已恢复", r_ok["recovered"] is True)
check("成功一次 → 不再是失效状态", r_ok["bad"] is False)
for i in range(1, 5):
    check(f"清零后第 {i} 次不认定", a2.feed(True)["need_alert"] is False)
check("清零后重新数满 5 次才认定", a2.feed(True)["need_alert"] is True)

# ---------- 恢复后重新挂掉，还能再提醒一次 ----------
a3 = A.AuthAlert(streak=5)
for _ in range(5):
    a3.feed(True)
a3.mark_sent()
check("第一次挂掉已通知", a3.fired is True)
a3.feed(False)                      # 恢复
check("恢复后 fired 重置", a3.fired is False)
for _ in range(5):
    a3.feed(True)
check("再次挂掉能重新提醒", a3.feed(True)["need_alert"] is True)

# ---------- 阈值可配置 ----------
a4 = A.AuthAlert(streak=3)
a4.feed(True)
a4.feed(True)
check("阈值 3：第 3 次才认定", a4.feed(True)["need_alert"] is True)
check("默认阈值是 5", A.DEFAULT_STREAK == 5)
check("配置项名对得上", A.STREAK_KEY == "auth_fail_alert_streak")
a5 = A.AuthAlert(streak=0)
check("阈值 0 回落默认（不是 1）", a5.streak == A.DEFAULT_STREAK,
      str(a5.streak))
a6 = A.AuthAlert(streak=-3)
check("负阈值兜底成正数（不崩）", a6.streak >= 1, str(a6.streak))

# ---------- 文案 ----------
msg = A.build_message("code=-352", 5)
check("提醒文案说明这不是过期", "不是过期" in msg, msg[:40])
check("提醒文案带上 B站 返回", "-352" in msg)
check("提醒文案说明怎么修", "重新登录" in msg or "重新扫码" in msg)
check("文案含连续失败次数", "5" in msg)
txt = A.text_of(5, 5)
check("面板文案含次数与阈值", "5" in txt and "阈值" in txt)


# ---------- 端到端：bot 侧认定后才推，且只推一条 ----------
class _FakeQQ:
    def __init__(self):
        self.sent = []

    async def send_text(self, gid, text):
        self.sent.append((gid, text))


class _FakeSelf:
    """只带真实 _check_bili_auth_alert 用到的那些属性。

    ⚠️ 为什么不再复制一份推送逻辑到桩里：
        上一版就是复制的，结果桩里漏了"没配沙盒群时不 mark_sent"，
        真代码是对的、桩却是错的 —— 差点把对的改坏。
        这里直接把真实方法绑上来跑，桩与真代码不会再漂移。
    """

    def __init__(self, gid="G1"):
        self.qq = _FakeQQ()
        self._gid = gid
        self._auth_alert_need = False
        self._auth_last_reason = "code=-352"

    def _auth_alert(self):
        import bili_auth_alert
        a = getattr(self, "_auth_alert_obj", None)
        if a is None:
            a = bili_auth_alert.AuthAlert(streak=5)
            self._auth_alert_obj = a
        return a


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# 真实方法（不是复制品）
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "_stubs"))
import bot as botmod  # noqa: E402
import cfgutil  # noqa: E402

REAL_CHECK = botmod.NotifyBot._check_bili_auth_alert


def make(gid):
    """造一个假 self，并把配置里的沙盒群设成 gid。"""
    s = _FakeSelf(gid=gid)
    cfgutil.load_cfg = lambda: {"sandbox_group": gid}
    return s


b = make("G1")
alert = b._auth_alert()
for _ in range(5):
    b._auth_alert_need = alert.feed(True)["need_alert"]
run(REAL_CHECK(b))
check("端到端：认定后推了一条", len(b.qq.sent) == 1, str(len(b.qq.sent)))
check("端到端：推到沙盒群", b.qq.sent and b.qq.sent[0][0] == "G1")
check("端到端：内容是失效提醒",
      b.qq.sent and "失效" in b.qq.sent[0][1])
# 后续轮次不再推
alert.feed(True)
b._auth_alert_need = False
run(REAL_CHECK(b))
check("端到端：只推一条，不刷屏", len(b.qq.sent) == 1, str(len(b.qq.sent)))

# 沙盒群没配 → 不推，但也不算"已通知"
b2 = make("")
alert2 = b2._auth_alert()
for _ in range(5):
    b2._auth_alert_need = alert2.feed(True)["need_alert"]
run(REAL_CHECK(b2))
check("没配沙盒群时不推", len(b2.qq.sent) == 0)
check("没配沙盒群时不算已通知（配好后会补推）", alert2.fired is False,
      f"fired={alert2.fired}")

# 配好沙盒群后，下一轮会补推（因为没记成"已通知"）
b3 = make("G1")
alert3 = b3._auth_alert()
for _ in range(5):
    b3._auth_alert_need = alert3.feed(True)["need_alert"]
b3._auth_alert_need = True
run(REAL_CHECK(b3))
check("配好沙盒群后补推", len(b3.qq.sent) == 1, str(len(b3.qq.sent)))

# ---------- bot.py / webui.py / app.js 接线检查 ----------
SRC = os.path.join(HERE, "..", "src")
BOT = os.path.join(SRC, "bot.py")
WEB = os.path.join(SRC, "webui.py")
JS = os.path.join(SRC, "static", "app.js")


def read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


bot = read(BOT)
web = read(WEB)
js = read(JS)


def strip_comments(s):
    """去掉 # 注释再匹配——这个项目的注释顶住过不止一条断言。"""
    return "\n".join(ln.split("#")[0] if ln.lstrip().startswith("#") else ln
                     for ln in s.splitlines())


check("bot 已 import 新模块", "import bili_auth_alert" in strip_comments(bot))
check("bot 有 _auth_alert 惰性创建", "def _auth_alert(self)" in bot)
check("bot 有失效提醒推送方法", "async def _check_bili_auth_alert" in bot)
check("bot 轮询里调了失效提醒",
      "await self._check_bili_auth_alert()" in strip_comments(bot))
check("bot 把结果写进心跳", "bili_auth_bad" in bot)
check("bot 待推标记跟着认定结果走",
      "_auth_alert_need = bool(res.get(" in strip_comments(bot))
check("webui 把状态传给前端", "bili_auth_bad" in strip_comments(web))
check("前端有新横幅函数", "function renderAuthBadBanner" in js)
check("前端刷新时会调用它", "renderAuthBadBanner" in js)
check("前端只在 bad 时显示", "if (!bad) return" in js)

# ---------- 旧行为必须消失 ----------
check("旧的 10 分钟刷屏告警已移除",
      "10 分钟内只报一次" not in bot,
      "还留着旧的刷屏逻辑")

print("通过:")


for n in PASS:
    print("  ✅", n)
if FAIL:
    print("失败:")
    for n in FAIL:
        print("  ❌", n)
print(f"{len(PASS)}/{len(PASS) + len(FAIL)}")
sys.exit(1 if FAIL else 0)
