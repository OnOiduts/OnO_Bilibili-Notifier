# -*- coding: utf-8 -*-
"""安装脚本必须跟上当前的依赖清单（v2.2.1 起 playwright 是必装）。

背景：v2.2.1 把 playwright 从可选移回必装，同时删掉了 qrcode / Pillow。
但「安装依赖.bat」一直在装 requirements-core.txt（只有 4 个包、不含
playwright），并且还在逐个安装已经删除的 qrcode / Pillow。

后果：Windows 用户双击这个脚本装完，点面板里的「🌐 浏览器登录」只会报
「缺少依赖」——装了等于没装。这三条断言就是为了防止它再次悄悄过时。
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _bat(name):
    return os.path.join(ROOT, name)


def _real_pip_targets(script):
    """只取真正的 pip 调用行，跳过 rem / # 注释。

    第一版断言直接全文找 "requirements-core.txt"，结果脚本里说明性的注释
    （写着「不要装 -core.txt」）也被当成真调用，改对了也照样报红 ——
    等于没守住。现在只对实际执行的命令行做判断。
    """
    out = []
    for line in script.splitlines():
        t = line.strip()
        if not t or t.lower().startswith("rem") or t.startswith("#"):
            continue
        if "pip install" in t:
            out.append(t)
    return out


# ---------- 安装依赖.bat ----------

def test_install_bat_uses_full_requirements():
    """必须装全量清单 src\\requirements.txt，而不是只有 4 个包的 -core.txt。"""
    s = _read(_bat("安装依赖.bat"))
    calls = _real_pip_targets(s)
    assert calls, "安装脚本里没有任何 pip 调用"
    assert any("requirements.txt" in c for c in calls), (
        "安装脚本没有装全量清单 requirements.txt"
    )
    assert not any("requirements-core.txt" in c for c in calls), (
        "安装脚本仍在装 requirements-core.txt —— 那份清单不含 playwright，"
        "装完点「浏览器登录」会报缺依赖"
    )


def test_install_bat_no_removed_packages():
    """qrcode 与 Pillow 已随扫码登录整条链路删除，不该再装。"""
    s = _read(_bat("安装依赖.bat"))
    for pkg in ("qrcode", "Pillow"):
        assert not re.search(r"pip install[^\n]*\b%s\b" % pkg, s), (
            "安装脚本还在装 %s —— 它唯一用途是画登录二维码，"
            "扫码登录已删除" % pkg
        )


def test_install_bat_verifies_playwright():
    """收尾校验必须包含 playwright，否则装漏了也不会报错。"""
    s = _read(_bat("安装依赖.bat"))
    assert "'playwright'" in s or '"playwright"' in s, (
        "收尾校验没有检查 playwright —— 必装项漏装时用户不会收到任何提示"
    )


def test_install_bat_title_is_onobn():
    """标题别再挂着改名前的老名字。"""
    s = _read(_bat("安装依赖.bat"))
    assert "OnO Bilibili Notifier" in s or "OnOBN" in s
    assert "Bili Notify Bot" not in s


def test_install_bat_is_crlf_ascii():
    """bat 是 LF 会在 Windows 上闪退/乱码。"""
    with open(_bat("安装依赖.bat"), "rb") as f:
        raw = f.read()
    assert b"\r\n" in raw, "bat 必须是 CRLF"
    try:
        raw.decode("ascii")
    except UnicodeDecodeError:
        raise AssertionError("bat 里混进了非 ASCII 字符，chcp 之前会乱码")


# ---------- 启动机器人.sh ----------

def test_launch_sh_uses_full_requirements():
    """Linux/Mac 启动脚本同样要装全量清单，并下载浏览器内核。"""
    s = _read(os.path.join(ROOT, "启动机器人.sh"))
    calls = _real_pip_targets(s)
    assert calls, "启动脚本里没有任何 pip 调用"
    assert any("requirements.txt" in c for c in calls), (
        "启动脚本没有装全量清单 requirements.txt"
    )
    assert not any("requirements-core.txt" in c for c in calls), (
        "启动脚本仍在装 requirements-core.txt（不含 playwright）"
    )
    assert "playwright install chromium" in s, (
        "启动脚本没有下载 chromium 内核，浏览器登录会失败"
    )


# ---------- 依赖清单本身 ----------

def test_requirements_txt_has_playwright():
    """必装清单里必须有 playwright，且不带写死的版本号（会跟别的包打架）。"""
    s = _read(os.path.join(ROOT, "src", "requirements.txt"))
    assert re.search(r"^playwright", s, re.M), "必装清单里缺 playwright"
    assert "==" not in s, "依赖里写死了版本号，容易跟其他包冲突"
    for pkg in ("qrcode", "Pillow"):
        assert not re.search(r"^%s" % pkg, s, re.M), "%s 已删除" % pkg


def test_optional_requirements_is_empty():
    """可选清单应当为空（要用的都进了必装），且不含已删除的包。"""
    p = os.path.join(ROOT, "src", "requirements-optional.txt")
    if not os.path.isfile(p):
        return
    s = _read(p)
    for pkg in ("qrcode", "Pillow", "playwright"):
        assert not re.search(r"^%s" % pkg, s, re.M), (
            "%s 不该出现在可选清单里" % pkg
        )
