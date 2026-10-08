# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""B 站登录态到期提醒：分级、防重推、真实过期时间。

⚠️ 这些断言做过反向验证：把改动退回旧写法，断言必须变红。
   否则就是"摆设测试"——代码坏了照样全绿。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import bili_expiry_alert as B  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + (("  ← " + extra) if extra else ""))


# ---------- 分级阈值 ----------
check("剩 10 天不提醒", B.level_of(10) == B.LEVEL_OK)
check("剩 6 天不提醒", B.level_of(6) == B.LEVEL_OK)
check("剩 5 天 UI 提醒", B.level_of(5) == B.LEVEL_UI)
check("剩 3 天 UI 提醒", B.level_of(3) == B.LEVEL_UI)
check("剩 2 天 UI 提醒（不是群提醒）", B.level_of(2) == B.LEVEL_UI)
check("剩 1 天推群", B.level_of(1) == B.LEVEL_QQ)
check("剩 0 天推群", B.level_of(0) == B.LEVEL_QQ)
check("已过期推群", B.level_of(99, expired=True) == B.LEVEL_QQ)
check("未知天数不提醒", B.level_of(None) == B.LEVEL_OK)

# ---------- 防重推 ----------
a = B.ExpiryAlert()
r1 = a.evaluate(3)
check("首次 3 天 → UI 档", r1["level"] == B.LEVEL_UI)
check("UI 档不推群", r1["need_qq"] is False)
r2 = a.evaluate(3)
check("同档重复不再触发", r2["need_qq"] is False)

a2 = B.ExpiryAlert()
c1 = a2.evaluate(1)
check("1 天首次 → 推群", c1["need_qq"] is True)
a2.mark_sent(c1["level"])
c2 = a2.evaluate(1)
check("1 天第二次不推（防刷屏）", c2["need_qq"] is False)
a2.evaluate(20)
a2.mark_sent  # noqa
a3 = B.ExpiryAlert()
a3.evaluate(20)          # 回到健康区间
check("回到健康区间才重置", a3.evaluate(1)["need_qq"] is True)

# ---------- 文案 ----------
m = B.build_message(1)
check("1 天文案含天数", "1 天" in m, m)
m2 = B.build_message(0, expired=True)
check("过期文案说清楚后果", "过期" in m2 and "重新登录" in m2, m2)

# ---------- 真实过期时间（面板显示 30 天的根因） ----------
S = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
BL = open(os.path.join(S, "bili_browser_login.py"), encoding="utf-8").read()
# ⚠️ 不能写成「or」的两可判断：退化成写死 30 天时另一半仍成立，
#    断言就变假通过了（反向验证实测过，确实没红）。必须直接断言返回值来源。
_ret = BL.split('"cookies": found,')[-1][:400]
check("浏览器登录用真实过期时间（不是写死 30 天）",
      "_expires_from_cookies(found_raw)" in _ret and
      '"expires": time.time() + 30 * 86400' not in _ret, _ret[:160])
check("有 _expires_from_cookies 函数", "def _expires_from_cookies" in BL)

# 毫秒/秒两种都能认
cookies = [{"name": "SESSDATA", "value": "x",
            "expires": time.time() + 3 * 86400}]
import bili_browser_login as BBL  # noqa: E402
sec = BBL._expires_from_cookies(cookies)
check("秒级时间戳能读", abs(sec - (time.time() + 3 * 86400)) < 5, str(sec))
ms = BBL._expires_from_cookies(
    [{"name": "SESSDATA", "value": "x",
      "expires": (time.time() + 3 * 86400) * 1000}])
check("毫秒级时间戳能读", abs(ms - (time.time() + 3 * 86400)) < 5, str(ms))
check("会话 cookie（expires=-1）读不到",
      BBL._expires_from_cookies([{"name": "SESSDATA", "value": "x",
                                  "expires": -1}]) == 0.0)
check("读到的剩余天数正确", B.days_left_of(sec)[0] == 2, str(B.days_left_of(sec)))

# ---------- 接入：bot 每轮检查、webui 暴露、前端横幅 ----------
BOT = open(os.path.join(S, "bot.py"), encoding="utf-8").read()
check("轮询里会调到到期检查", "_check_bili_expiry" in BOT)
check("检查方法存在", "async def _check_bili_expiry" in BOT)
check("推到沙盒群", "sandbox_group" in BOT)
WEB = open(os.path.join(S, "webui.py"), encoding="utf-8").read()
check("面板接口暴露 bili_expiry", "bili_expiry" in WEB)
JS = open(os.path.join(S, "static", "app.js"), encoding="utf-8").read()
check("前端有到期横幅", "renderExpiryBanner" in JS)
check("横幅区分红/橙两档", "lv === 'qq'" in JS or "lv == 'qq'" in JS)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for f in FAIL:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
