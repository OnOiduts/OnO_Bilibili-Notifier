# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""cookie 时效那格要有悬停说明。

为什么非写不可：
    面板上那句「cookie 有效期至 …（还剩 N 天）」看着像保险，其实不是。
    N 天只是 cookie **自己声明**的到期时间，B站 可以在到期前任何一刻
    让它失效（别处重新登录、长期没真人操作、被判定异常）。
    再加上本程序是拿 cookie 直接调接口，不像浏览器那样每次访问会自动续期，
    所以走完这条期限就是到期，没有宽限。

    不说清楚的话，用户会以为"还剩 179 天，稳的"，然后某天订阅悄悄
    不推了也不知道为什么 —— 这正是当初做到期提醒要解决的问题。

本文件钉住三点：
  1. cookieTip() 存在，且文案把「名义期限 ≠ 实际能用多久」讲明白，
     并写清到期提醒的分级规则
  2. 账户卡片两处 acc-exp（已登录 / 未验证）都挂上了这个提示
  3. **估算值也要标出来** —— 以前只有"未验证"那格写（估算），
     正常登录这格反而不写，于是最该看到的场景恰恰看不到
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

APP_JS = os.path.join(SRC, "static", "app.js")

OK = []


def ck(name, cond):
    OK.append(bool(cond))
    print(("  [PASS] " if cond else "  [FAIL] ") + name)


def _src():
    with open(APP_JS, "r", encoding="utf-8") as f:
        return f.read()


def _strip_comments(s):
    """剥掉注释。

    ⚠️ 必须剥：注释里举例提到的字眼会顶住断言，让"改坏了"看起来像
       "改好了"。本项目在这个坑上栽过好几次。
    """
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?m)^\s*//.*$", "", s)
    return s


def _func_body(name):
    """按花括号配对截取函数体（注释已剥）。"""
    s = _strip_comments(_src())
    m = re.search(r"(?m)^function\s+" + name + r"\s*\(", s)
    if not m:
        return ""
    i = s.find("{", m.end() - 1)
    depth, j = 0, i
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i:j + 1]
        j += 1
    return ""


def _cookie_tip(d):
    """把 cookieTip 的原函数体拿出来**真跑一遍**，而不是照抄期望值。

    ⚠️ 用 node 跑，不是用 Python 拼字符串猜结果：
       断言要做的是"这段 JS 真的会输出什么"，照抄一遍文案等于没测。
    """
    body = _func_body("cookieTip")
    if not body.strip():
        # 函数被删掉时让断言自然变红，而不是把整轮测试打断
        return ""
    js = ("function cookieTip(d) " + body + "\n"
          "console.log(JSON.stringify(cookieTip(" + json.dumps(d) + ")));")
    with tempfile.NamedTemporaryFile("w", suffix=".js",
                                     delete=False, encoding="utf-8") as f:
        f.write(js)
        p = f.name
    try:
        r = subprocess.run(["node", p], capture_output=True, text=True,
                           timeout=30)
        assert r.returncode == 0, f"node 跑失败：{r.stderr[:200]}"
        return json.loads(r.stdout)
    finally:
        try:
            os.unlink(p)
        except OSError:
            pass


def _exp_txt(d):
    """把登录卡里 estTag / expTxt 那两行原样拿出来用 node 跑。

    为什么不直接查源码字符串：只查"estTag 定义还在"会假通过 ——
    把它定义了却不用，断言照样全绿（反向验证实测踩到）。
    真跑一遍拼接，输出里有没有「（估算）」才说明问题。
    """
    src = _strip_comments(_src())
    i = src.find("const estTag")
    assert i > 0, "找不到 estTag"
    j = src.find("const expTxt", i)
    assert j > 0, "找不到 expTxt"
    end = src.find(";", j)
    body = src[i:end + 1]
    js = ("const d = " + json.dumps(d) + ";\n" + body +
          "\nconsole.log(JSON.stringify(expTxt));")
    with tempfile.NamedTemporaryFile("w", suffix=".js",
                                     delete=False, encoding="utf-8") as f:
        f.write(js)
        p = f.name
    try:
        r = subprocess.run(["node", p], capture_output=True, text=True,
                           timeout=30)
        assert r.returncode == 0, f"node 跑失败：{r.stderr[:200]}"
        return json.loads(r.stdout)
    finally:
        try:
            os.unlink(p)
        except OSError:
            pass


def test_cookie_tip_exists():
    src = _src()
    ck("app.js 里有 cookieTip", "function cookieTip(" in src)
    ck("cookieTip 接的是后端那份状态 d", "function cookieTip(d)" in src)


def test_tip_text_is_clear():
    """文案必须讲清三件事：期限来源、它不等于实际、提醒怎么分级。"""
    tip = _cookie_tip({"expired": False, "expires_estimated": False})
    ck("说了天数从哪来", "真实到期时刻" in tip or "SESSDATA" in tip)
    ck("说了它不等于实际能用多久",
       ("不等于" in tip) or ("能用那么久" in tip))
    ck("列了会提前失效的几种情况",
       "别处重新登录" in tip and "强制下线" in tip)
    ck("说了不会自动续期", "自动续期" in tip or "续期" in tip)
    ck("写了 2~5 天这一档", "2 ~ 5 天" in tip)
    ck("写了不足 2 天这一档", "不足 2 天" in tip)
    ck("说了会员到期是另一回事", "会员到期" in tip)


def test_tip_distinguishes_estimated():
    """估算值和真实值要给出不同的话，不能一视同仁。"""
    real = _cookie_tip({"expired": False, "expires_estimated": False})
    est = _cookie_tip({"expired": False, "expires_estimated": True})
    ck("估算时明说这是估算", "估算" in est)
    ck("估算时说清怎么拿到真值", "重新登录" in est)
    ck("非估算时不说估算", "估算" not in real)


def test_tip_when_expired():
    """已过期时的文案要能指导下一步，而不是继续讲天数。"""
    tip = _cookie_tip({"expired": True})
    ck("已过期时直接说过期", "已过期" in tip)
    ck("已过期时告诉用户去哪重登", "重新登录" in tip)
    ck("已过期时不谈还剩几天", "还剩" not in tip)


def test_attached_to_account_card():
    """两处 acc-exp 都得挂上，少一处就有一半场景看不到。"""
    src = _strip_comments(_src())
    hits = re.findall(r"class=\"acc-exp[^\"]*\"([^>]*?)>", src)
    ck("账户卡片里有 acc-exp 这两格", len(hits) >= 2)
    with_tip = [h for h in hits if "cookieTip(" in h]
    ck("两格都挂了 cookieTip", len(with_tip) >= 2)
    # 只判 data-tip 不够 —— 别的提示也用 data-tip
    ck("挂的是 cookieTip(d) 而不是别的文案",
       all("cookieTip(d)" in h for h in with_tip))


def test_estimated_marked_in_card():
    """正常登录那格也要标（估算）。

    以前只有"已保存但未验证"那格写，正常登录这格反而不写 ——
    "还剩 179 天"到底是真读出来的还是算出来的，不说就是误导。
    """
    src = _strip_comments(_src())
    i = src.find("const estTag")
    assert i > 0, "找不到 estTag"
    head = src[i:i + 400]
    ck("估算标记取自后端的 expires_estimated",
       "expires_estimated" in head)

    # ⚠️ 光检查"estTag 定义还在"是假通过 —— 退化成"定义了却不用"
    #    照样全绿（实测踩到）。所以必须**真跑**那段拼接，看输出。
    est = _exp_txt({"expired": False, "days_left": 179,
                    "expires_str": "2027-04-06 11:56",
                    "expires_estimated": True})
    ck("估算时天数那格真的显示（估算）", "（估算）" in est)
    real = _exp_txt({"expired": False, "days_left": 179,
                     "expires_str": "2027-04-06 11:56",
                     "expires_estimated": False})
    ck("非估算时不显示（估算）", "（估算）" not in real)
    ck("非估算时天数照常显示", "179" in real)


if __name__ == "__main__":
    test_cookie_tip_exists()
    test_tip_text_is_clear()
    test_tip_distinguishes_estimated()
    test_tip_when_expired()
    test_attached_to_account_card()
    test_estimated_marked_in_card()
    print(f"\n{sum(OK)}/{len(OK)}")
    sys.exit(0 if all(OK) else 1)
