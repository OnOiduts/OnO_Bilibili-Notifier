"""UID 位数兼容回归测试（1~16 位）。

B 站 UID 是自增的，位数会一直变长；早期注册的号位数又很短。
以前代码和测试都把 UID 写死在 3~12 位，位数一超就被拒 —— 表现是
"这个 UP 主加不进去"，而面板只提示"格式不对"，看的人想不到是位数问题。

用法：python tests/test_uid_len.py
"""
import os
import re
import sys

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src")
sys.path.insert(0, BASE_DIR)

try:
    import security as S
except Exception as e:  # pragma: no cover
    print("[FAIL] 无法导入 security：%s" % e)
    sys.exit(1)

ok = True


def ck(name, cond):
    global ok
    if not cond:
        ok = False
    print(("[PASS] " if cond else "[FAIL] ") + name)
    return bool(cond)


def _read(rel):
    p = os.path.join(BASE_DIR, rel)
    if not os.path.exists(p):
        return ""
    with open(p, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _strip_py(src):
    """去掉注释与 docstring —— 否则注释里写的旧正则会把断言顶住。

    ⚠️ 这个项目里注释顶住断言已经踩过不止一次：注释里写着旧写法，
    静态匹配就永远"找得到"，于是把代码改坏也照样全绿。
    """
    out = []
    in_doc = False
    for ln in src.splitlines():
        s = ln.strip()
        if s.count('"""') >= 2 or s.count("'''") >= 2:
            continue
        if s.startswith('"""') or s.startswith("'''"):
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        out.append(ln.split("#")[0])
    return "\n".join(out)


def _strip_js(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for ln in src.splitlines():
        out.append(re.sub(r"(^|[^:'\"])//.*$", r"\1", ln))
    return "\n".join(out)


def _uid(v):
    """调 validate_uid，被拒返回 None。

    ⚠️ 不要直接 S.validate_uid(x)：退化验证时它会抛 ValueError，
    测试当场崩在第一条断言上（退出码非 0 但**没打印任何 FAIL**），
    后面那些断言全跑不到 —— 看不出到底退化影响了哪几条。
    """
    try:
        return S.validate_uid(v)
    except Exception:
        return None


def t_validate():
    ck("1 位 UID 放行", _uid("1") == 1)
    ck("2 位 UID 放行", _uid("42") == 42)
    ck("16 位 UID 放行", _uid("1234567890123456") == 1234567890123456)
    for bad in ("", "abc", "12a", "-1", "1.5", "  ",
                "12345678901234567"):  # 17 位
        ck("拒绝 %r" % bad, _uid(bad) is None)


def t_url():
    ck("链接提取 10 位 UID",
       _uid("https://space.bilibili.com/672328094") == 672328094)
    ck("链接提取 16 位 UID",
       _uid("https://space.bilibili.com/1234567890123456")
       == 1234567890123456)
    ck("链接提取 1 位 UID", _uid("https://space.bilibili.com/7") == 7)
    ck("mid= 提取 UID", _uid("?mid=1234567890123456") == 1234567890123456)
    # 超长不能被截成前 16 位当合法 UID
    ck("17 位链接不被截断放行",
       _uid("https://space.bilibili.com/12345678901234567") is None)


def t_source():
    sec = _strip_py(_read("security.py"))
    ck("security.py 已无 3~12 位限制", "{3,12}" not in sec)
    ck("security.py 不再写 3~12", "3~12" not in sec)
    ck("security.py 用 1~16 位", "{1,16}" in sec)

    js = _strip_js(_read(os.path.join("static", "app.js")))
    # ⚠️ 不能用 \{ 之后非贪婪到第一个 \} 来取函数体：正则量词 {1,16} 里
    #    本身就带 }，会被当成函数结束，函数体被截断在 (\d{1,16 处 ——
    #    于是断言"写成什么样"都匹配不到，退化成假通过。按关键字取一段。
    m = re.search(r"function parseUid\b", js)
    body = js[m.start():m.start() + 400] if m else ""
    ck("找到 parseUid 函数体", bool(body))
    ck("前端 UID 提取是 1~16 位", r"\d{1,16}" in body)
    ck("前端不再用无上限的 3 位起提取", r"(\d{3,})" not in body)

    web = _strip_py(_read("webui.py"))
    ck("自检 UID 提取是 1~16 位", r"(\d{1,16})(?!\d)" in web)
    ck("自检 UID 不再用 \\d{3,}", re.search(r"\\d\{3,\}", web) is None)

    bl = _strip_py(_read("bilibili.py"))
    ck("直播间号正则放宽到 16 位", r"\d{1,16}" in bl)
    ck("直播间号不再写死 12 位", "{1,12}" not in bl)


def main():
    t_validate()
    t_url()
    t_source()
    print("\n结果:", "全部通过 ✅" if ok else "存在失败 ❌")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
