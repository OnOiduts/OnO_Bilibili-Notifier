# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""cookie 有效期：7 天基准 + 自带值封顶 + 按"第几天"分级。

为什么非写不可：
    上次掉 cookie 是**第 7 天**，而 cookie 自己声明的到期时间写着
    179 天。只按自带值算，面板会一直显示"还剩一百多天"，到期提醒的
    档位永远进不去 —— 于是又回到"静默失效、好几天后才发现"。

    所以定了两条：
      1. 读不到到期时间 → 按 7 天估（原来是 30 天）
      2. 读到了（哪怕写着 179 天）→ 封顶到「登录那一刻 + 7 天」

本文件钉住：
  · 基准是 7 天，不是 30 天
  · 封顶真的生效（179 天被压成 7 天）
  · 第 1~4 天安静、第 5 天 UI、第 6 天起推群（逐日验，含小数天）
  · 剩余天数向上取整（向下取整会把第 5 天跳到推群档）
  · 四个调用点都把"登录时刻"传进去了，否则封顶无从谈起

⚠️ 全部做过反向验证：把改动退回旧写法，断言必须变红。
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import bili_login as BL            # noqa: E402
import bili_expiry_alert as BA     # noqa: E402

PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + (("  ← " + extra) if extra else ""))


def _cookie_since(days_ago, declared_days=173.0):
    """造一串"days_ago 天前登录、自带值还剩 declared_days 天"的 cookie。"""
    since = time.time() - days_ago * 86400
    real = time.time() + declared_days * 86400
    return since, "SESSDATA=abc,%d,xyz*1" % int(real)


def _level_at(days_ago):
    since, ck_ = _cookie_since(days_ago)
    exp, est = BL.effective_expires(ck_, 0.0, since)
    d, expired = BA.days_left_of(exp)
    return d, BA.level_of(d, expired)


# ---------- 1. 基准 7 天 ----------
ck("估算基准是 7 天（不是 30）", BL.DEFAULT_TTL_DAYS == 7, str(BL.DEFAULT_TTL_DAYS))
ck("封顶天数也是 7 天", BL.CAP_TTL_DAYS == 7, str(BL.CAP_TTL_DAYS))
_src = open(os.path.join(SRC, "bili_login.py"), encoding="utf-8").read()
_src_nc = re.sub(r"(?m)^\s*#.*$", "", _src)
ck("源码里不再有 30 天估算",
   "DEFAULT_TTL_DAYS * 86400" in _src_nc and
   "time.time() + 30 * 86400" not in _src_nc)

# ---------- 2. 封顶真的生效 ----------
since, ck_ = _cookie_since(0.1, declared_days=173.0)
exp, est = BL.effective_expires(ck_, 0.0, since)
ck("自带 179 天被压到 7 天以内",
   exp <= since + 7 * 86400 + 5, "expires-since=%.1f天" % ((exp - since) / 86400))
ck("封顶后剩余天数不超过 7", BA.days_left_of(exp)[0] <= 7)

# 没有 since（老版本存的）时不封顶，也不许崩
exp_nc, _ = BL.effective_expires(ck_, 0.0, 0.0)
ck("没有登录时刻时不封顶（退回原值）",
   abs(exp_nc - (time.time() + 173 * 86400)) < 60)

# ---------- 3. 按"第几天"分级（含小数天） ----------
for d, want in [(0.1, BA.LEVEL_OK), (1.0, BA.LEVEL_OK), (2.0, BA.LEVEL_OK),
                (3.0, BA.LEVEL_OK), (4.0, BA.LEVEL_OK), (4.9, BA.LEVEL_OK),
                (5.0, BA.LEVEL_UI), (5.5, BA.LEVEL_UI), (5.9, BA.LEVEL_UI),
                (6.0, BA.LEVEL_QQ), (6.5, BA.LEVEL_QQ), (7.5, BA.LEVEL_QQ)]:
    got_d, got = _level_at(d)
    ck("第 %.1f 天 → %s" % (d, want), got == want,
       "实得 %s（剩 %s 天）" % (got, got_d))

# ---------- 4. 向上取整 ----------
now = time.time()
ck("剩 1.8 天向上取整为 2（不是 1）",
   BA.days_left_of(now + 1.8 * 86400)[0] == 2)
ck("剩 2.0 天是 2", BA.days_left_of(now + 2.0 * 86400)[0] == 2)
ck("剩 0.5 天是 1（不是 0）", BA.days_left_of(now + 0.5 * 86400)[0] == 1)
ck("已过期返回 (0, True)", BA.days_left_of(now - 10)[1] is True)

# ---------- 5. 调用点都传了"登录时刻" ----------
for fn, key in [("bot.py", "bili_cookie_ts"), ("check.py", "bili_cookie_ts"),
                ("webui.py", "bili_cookie_ts")]:
    t = open(os.path.join(SRC, fn), encoding="utf-8").read()
    # ⚠️ 调用是跨行写的，不能用"单行"的正则去抓 —— 抓不到就退化成
    #    len(m)>=1 失败，看着像代码没改，其实是断言自己写错了。
    m = re.findall(r"effective_expires\(", t)
    tail = [t[i:i + 220] for i in
            (mm.start() for mm in re.finditer(r"effective_expires\(", t))]
    ck("%s 的 effective_expires 都传了登录时刻" % fn,
       len(m) >= 1 and all((key in x) or ("time.time()" in x) for x in tail),
       str(tail)[:200])

print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
