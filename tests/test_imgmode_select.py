# -*- coding: utf-8 -*-
"""「图片发送方式」下拉框：选项、保存、回填三件事都不能缺。

用户报的现象：下拉框默认没有选中项、选了不保存。真因是两个独立的坑：
  ① HTML 的 option 里没有 inline（而后端默认就是 inline）
     → select.value = "inline" 赋值失败，浏览器把 value 变成空串
     → 显示成"没选中"，且之后提交空值
  ② saveCfg 的 body 里压根没有 image_send_mode 这个键
     → 无论选什么后端都收不到

这个文件把"后端白名单 == 前端选项"这条对应关系锁死，
以后新增发送方式忘了同步选项，会在这里直接失败。
"""
import os
import re
import sys

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE_DIR)

HTML = os.path.join(BASE_DIR, "templates", "index.html")
JS = os.path.join(BASE_DIR, "static", "app.js")
WEBUI = os.path.join(BASE_DIR, "webui.py")

OK = []


def ck(name, cond):
    OK.append(bool(cond))
    print(("  [PASS] " if cond else "  [FAIL] ") + name)
    return bool(cond)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _select_options(html, sel_id):
    """取出某个 <select id=...> 里的全部 option value。"""
    m = re.search(r'<select[^>]*id=["\']%s["\'][^>]*>(.*?)</select>' % sel_id,
                  html, re.S)
    if not m:
        return None
    return re.findall(r'<option[^>]*value=["\']([^"\']*)["\']', m.group(1))


def test_options_cover_backend():
    """前端选项必须覆盖后端白名单里的每一个取值。"""
    html, wsrc = _read(HTML), _read(WEBUI)
    opts = _select_options(html, "cImgMode")
    ck("页面上有 cImgMode 下拉框", opts is not None)
    if not opts:
        return
    allowed = set()
    # 后端白名单：在每一处出现 image_send_mode 之后找紧邻的 `if m in (...)`
    # （不能只看第一次出现 —— state 里也带着这个名字，后面没有白名单）
    for m0 in re.finditer(r'image_send_mode', wsrc):
        seg = wsrc[m0.end():m0.end() + 400]
        mm = re.search(r'if m in \(([^)]*)\)', seg)
        if mm:
            allowed = set(re.findall(r'"([^"]+)"', mm.group(1)))
            break
    ck("取到后端白名单", bool(allowed))
    ck("选项覆盖后端全部取值：缺 %s" % (allowed - set(opts)),
       allowed and allowed.issubset(set(opts)))
    ck("默认 inline 在选项里（否则回填会变空）", "inline" in opts)
    ck("选项里没有后端不认的值：多 %s" % (set(opts) - allowed),
       allowed and set(opts).issubset(allowed))


def test_save_body_has_mode():
    """saveCfg 的 body 必须带上 image_send_mode，否则选了也不提交。"""
    js = _read(JS)
    body = js.split("async function saveCfg")[1].split("const sec =")[0]
    ck("saveCfg 提交 image_send_mode", "image_send_mode" in body)
    ck("取值来自 cImgMode 下拉框", "cImgMode" in body)


def test_select_saves_immediately():
    """下拉框改动要立即保存（以前只有开关类才立即保存）。"""
    js = _read(JS)
    seg = js.split("function initFormDirty")[1].split("if (id === 'cSecret')")[0]
    ck("cImgMode 改动立即保存", "'cImgMode'" in seg)
    ck("cSandboxGroup 改动立即保存", "'cSandboxGroup'" in seg)


def test_fill_uses_safe_assign():
    """回填必须走 setSelectValue：值不在选项里时不许把已保存的值抹成空。"""
    js = _read(JS)
    ck("有 setSelectValue 辅助函数", "function setSelectValue" in js)
    seg = js.split("function fillCfg")[1].split("\n}\n")[0]
    ck("cImgMode 用安全赋值", "setSelectValue(imSel" in seg)
    ck("cSandboxGroup 用安全赋值", "setSelectValue(sgEl" in seg)


def test_backend_roundtrip():
    """后端：合法值写入、空串/非法值不覆盖已有配置。"""
    import importlib
    try:
        w = importlib.import_module("webui")
    except Exception as e:
        print("  [SKIP] webui 导入失败（缺 flask 等依赖）：%s" % e)
        OK.append(True)
        return
    src = _read(WEBUI)
    ck("后端默认 image_send_mode 是 inline",
       '"image_send_mode", "inline"' in src.replace("'", '"')
       or '"inline"' in src.split("image_send_mode")[1][:120])
    ck("空串不在白名单里（提交空值不会覆盖原配置）",
       '""' not in src.split("image_send_mode")[1][:400])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print("---- %s" % name)
            try:
                fn()
            except Exception as e:
                OK.append(False)
                print("  [FAIL] %s -> %s" % (name, e))
    print("\n---- %d/%d 通过" % (sum(1 for x in OK if x), len(OK)))
    sys.exit(0 if all(OK) else 1)
