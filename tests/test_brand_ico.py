# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""标签页小图标（favicon）同样不随包带图 —— 从远程取。

跟 logo 是同一套规则，只是用途不同：
    logo    → 面板里的底纹装饰（JS 探测式加载，取不到就 none）
    favicon → 浏览器标签页上的小图标（必须写在 HTML head 的 link 里）

favicon 有两条自己的约束，是 logo 没有的：

1. **不能要求登录**。浏览器取 favicon 的请求既不带 cookie，也不带任何
   自定义头。加了登录校验就等于永远取不到 —— 而且登录页自己也要显示图标，
   那样会变成"没登录连图标都没有"。
2. **必须写在 head 里**，不能像 logo 那样用 JS 探测：标签页图标是浏览器
   解析 HTML 时就去取的，JS 之后再补上去不生效（或生效很晚）。

图床与路径
----------
    https://github.com/XxBoLuoxX/OnO-ImageHost-/blob/main/Web/OnOBN/ico.png
    → cdn.jsdelivr.net/gh/XxBoLuoxX/OnO-ImageHost-@main/Web/OnOBN/ico.png

三个易错点，断言里专门盯住：
1. 仓库名 **OnO-ImageHost-** 末尾有连字符；
2. 路径是 **Web/OnOBN/ico.png**，两处目录名都是大写开头（GitHub 大小写敏感）；
3. 不能跟 logo 的地址写成同一张图（42x42 的方图当底纹会很糊，反之亦然）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

SKIP_DIRS = ("__pycache__", ".git", "data", "backups", "node_modules", ".venv")


def _tpl(name):
    return os.path.join(SRC, "templates", name)


def test_default_url_points_to_imagehost():
    """默认地址必须指向图床仓库的那张 ico.png。"""
    import webui
    url = webui.ICO_URL_DEFAULT
    assert url.startswith("https://"), f"必须 https，实际：{url}"
    assert "XxBoLuoxX" in url, "应指向作者仓库"
    assert url.lower().endswith("ico.png"), "应指向 ico.png"

    # 仓库名末尾带连字符；路径两处目录名都是大写开头
    assert "OnO-ImageHost-" in url, (
        f"图床仓库名是 OnO-ImageHost-（末尾带连字符），实际：{url}")
    assert "/Web/OnOBN/ico.png" in url, (
        f"路径是 Web/OnOBN/ico.png（两处目录名都大写开头），实际：{url}")
    assert "xxboluoxx.github.io" not in url, "旧图床地址已废弃"


def test_ico_differs_from_logo():
    """ico 与 logo 不能是同一张图 —— 尺寸与用途都不一样。"""
    import webui
    assert webui.ICO_URL_DEFAULT != webui.LOGO_URL_DEFAULT, (
        "favicon 和 logo 指向了同一张图")
    assert webui.ICO_URL_DEFAULT.lower().endswith("ico.png"), "ico 应是 ico.png"
    assert webui.LOGO_URL_DEFAULT.lower().endswith("logo.png"), "logo 应是 logo.png"


def test_route_exists():
    """/api/brand/ico 路由必须存在。"""
    import webui
    eps = {r.endpoint for r in webui.app.url_map.iter_rules()}
    assert "api_brand_ico" in eps, "/api/brand/ico 路由不见了"


def test_route_needs_no_login():
    """取图标不能要求登录：浏览器取 favicon 时既不带 cookie 也不带头。"""
    import envcfg
    envcfg.load_dotenv(force=True)
    import webui
    c = webui.app.test_client()
    r = c.get("/api/brand/ico")
    assert r.status_code != 401, "favicon 路由不能要求登录，否则永远取不到"
    assert r.status_code in (200, 302), f"预期 200（自定义图）或 302（远程），实际 {r.status_code}"


def test_env_override(monkeypatch):
    """ONOBN_ICO_URL 能换成任意直链。"""
    monkeypatch.setenv("ONOBN_ICO_URL", "https://example.com/my-ico.png")
    import envcfg
    envcfg.load_dotenv(force=True)
    import webui
    assert webui._ico_remote_url() == "https://example.com/my-ico.png"


def test_env_off_disables(monkeypatch):
    """设成 off 就彻底不发请求（离线部署用）。"""
    import envcfg
    import webui
    for v in ("off", "OFF", "none", "disable", "0"):
        monkeypatch.setenv("ONOBN_ICO_URL", v)
        envcfg.load_dotenv(force=True)
        assert webui._ico_remote_url() == "", f"{v} 也应视为关闭"


def test_templates_have_icon_link():
    """两个页面（面板 + 登录页）的 head 都要有 favicon link。"""
    for name in ("index.html", "login.html"):
        text = open(_tpl(name), encoding="utf-8").read()
        head = text.split("</head>")[0]
        assert 'rel="icon"' in head, f"{name} 的 head 里没有 favicon link"
        assert "/api/brand/ico" in head, f"{name} 的 favicon 应指向 /api/brand/ico"
        # 图标路由要走接口，不能指回已经删掉的本地图片目录
        assert "webuiimg" not in head, f"{name} 里仍在引用本地图片目录"


def test_no_bundled_ico_file():
    """仓库里不能有随包的 ico 文件（图片一律远程取）。"""
    left = []
    for base in (SRC, os.path.join(ROOT, "docs"), os.path.join(ROOT, "tools")):
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.lower().endswith((".ico", ".png", ".jpg", ".jpeg")):
                    left.append(os.path.join(dirpath, fn))
    assert not left, f"仓库里仍有随包图片：{left}"


def main():
    import traceback

    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    passed = failed = 0
    for name, fn in tests:
        try:
            # 脚本模式下给 monkeypatch 一个最小替身
            if "monkeypatch" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                class _MP:
                    def setenv(self, k, v):
                        os.environ[k] = v

                    def delenv(self, k, raising=True):
                        os.environ.pop(k, None)
                fn(_MP())
            else:
                fn()
            print(f"  ✓ {name}")
            passed += 1
        except Exception:
            failed += 1
            print(f"  ✗ {name}")
            traceback.print_exc()
    print(f"\n通过 {passed} 项，失败 {failed} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
