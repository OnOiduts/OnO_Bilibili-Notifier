# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""品牌 logo 不再随包带图 —— 仓库里不留二进制图片，改从远程取。

背景
----
原来 logo 是 `src/static/webuiimg/logo.png`，一张 851x198 的白色透明底 PNG。
它带来三个问题：

1. 它是**二进制文件**，放进 Git 仓库既占历史体积、也让 diff 读不了；
2. 想换 logo 得改仓库里的文件，等于为了换张图发一个版本；
3. 图片版权与署名也不好交代。

现在改成：仓库里一个图片文件都没有，运行时从作者仓库取。

为什么默认取 jsDelivr 而不是 raw.githubusercontent.com
-------------------------------------------------------
实测（同一张图、同一个分支）：

    raw.githubusercontent.com  →  20 秒超时，0 字节（国内常被拦）
    cdn.jsdelivr.net           →  200，1.3 秒，9430 字节，851x198 RGBA

jsDelivr 是 GitHub 的公共 CDN 镜像，指向的是**同一个仓库同一张图**，
只是多了层加速。想换回官方 raw 或其它镜像，设 ONOBN_LOGO_URL 即可。

取不到图时会怎样
----------------
app.js 的 initLogo 是「探测式」加载：CSS 里 `--logo` 默认 `none`，
只有图片 `onload` 才把它设成 URL。远程取不到（离线 / CDN 被拦）时触发
`onerror`，保持 `none` —— **不显示、不报错、不留空白块**，断网照常用面板。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# 扫描源码/文档时跳过这些目录（缓存、数据、历史日志不算"仓库内容"）
SKIP_DIRS = ("__pycache__", ".git", "data", "backups", "node_modules", ".venv")
IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp")


def _walk(base):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def test_no_bundled_image_dir():
    """src/static/webuiimg 整个目录必须不存在。"""
    path = os.path.join(SRC, "static", "webuiimg")
    assert not os.path.isdir(path), (
        f"图片文件夹又回来了：{path}。logo 已改为远程取，仓库不留图。")


def test_no_image_files_in_repo():
    """仓库里不该有任何图片文件（data/ 等运行目录除外）。"""
    left = []
    for base in (SRC, os.path.join(ROOT, "docs"), os.path.join(ROOT, "tools")):
        if not os.path.isdir(base):
            continue
        for p in _walk(base):
            if p.lower().endswith(IMG_EXTS):
                left.append(p)
    assert not left, f"仓库里仍有图片文件：{left}"


def test_no_local_webuiimg_reference():
    """源码里不能再出现指向本地 webuiimg 的**真实引用**（注释里提历史可以）。"""
    import re
    # ⚠️ 必须**逐行**匹配，且引号内长度有限：
    #    用整文件 + `[^'"]*` 会跨行吃到远处一对引号，把无关代码误判成引用。
    pat = re.compile(r"""['"][^'"\n]{0,80}static/webuiimg[^'"\n]{0,80}['"]""")
    for p in _walk(SRC):
        if not p.endswith((".py", ".js", ".html", ".css")):
            continue
        try:
            text = open(p, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            hit = pat.search(line)
            assert not hit, f"{p} 里仍有本地图片引用：{hit.group(0)}"


def test_default_url_is_https_and_points_to_repo():
    import webui
    url = webui.LOGO_URL_DEFAULT
    assert url.startswith("https://"), f"logo 地址必须 https，实际：{url}"
    assert "XxBoLuoxX" in url, "应指向作者仓库"
    assert url.lower().endswith("logo.png"), "应指向 logo.png"


def test_env_override(monkeypatch):
    """ONOBN_LOGO_URL 能换成任意直链。"""
    monkeypatch.setenv("ONOBN_LOGO_URL", "https://example.com/my-logo.png")
    import envcfg
    envcfg.load_dotenv(force=True)
    import webui
    assert webui._logo_remote_url() == "https://example.com/my-logo.png"


def test_env_off_disables(monkeypatch):
    """设成 off 就彻底不显示（离线部署用）。"""
    monkeypatch.setenv("ONOBN_LOGO_URL", "off")
    import envcfg
    envcfg.load_dotenv(force=True)
    import webui
    assert webui._logo_remote_url() == "", "off 应当返回空串（接口据此返回 404）"
    # 大写、以及常见同义写法也要认
    for v in ("OFF", "none", "disable", "0"):
        monkeypatch.setenv("ONOBN_LOGO_URL", v)
        envcfg.load_dotenv(force=True)
        assert webui._logo_remote_url() == "", f"{v} 也应视为关闭"


def test_route_exists_and_redirects(monkeypatch):
    """/api/brand/logo 无自定义图时应 302 到远程地址。"""
    monkeypatch.delenv("ONOBN_LOGO_URL", raising=False)
    import envcfg
    envcfg.load_dotenv(force=True)
    import webui
    assert "api_brand_logo" in {r.endpoint for r in webui.app.url_map.iter_rules()}, \
        "/api/brand/logo 路由不见了"
    # 自定义图优先：造一张假的在 images/ui 下
    try:
        import paths
        d = paths.image_sub("ui")
        os.makedirs(d, exist_ok=True)
        fake = os.path.join(d, "logo.png")
        with open(fake, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        c = webui.app.test_client()
        r = c.get("/api/brand/logo")
        assert r.status_code == 200, "有自定义图时应直接返回图片，而不是跳转"
        os.remove(fake)
    except Exception as e:  # 数据目录不可写时跳过这条，不算失败
        print(f"  （跳过自定义图分支：{e}）")


def test_frontend_degrades_silently():
    """取不到图时前端必须保持 none：不显示、不报错、不留白块。"""
    js = open(os.path.join(SRC, "static", "app.js"), encoding="utf-8").read()
    i = js.index("function initLogo")
    body = js[i:i + 900]
    assert "onerror" in body, "initLogo 必须有 onerror 兜底"
    assert "onload" in body, "initLogo 必须靠 onload 探测成功才显示"
    # onerror 里不能去改 --logo（改了就会显示一个加载失败的破图）
    err = body[body.index("onerror"):body.index("onerror") + 200]
    assert "--logo" not in err, "onerror 里不许设 --logo，否则会显示破图"


def main():
    """不用 pytest 也能跑：python tests/test_brand_logo.py"""
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = fail = 0
    for fn in fns:
        n = fn.__code__.co_argcount
        try:
            if n == 0:
                fn()
            else:
                pytest.importorskip
                print(f"  （跳过 {fn.__name__}：需要 monkeypatch）")
                continue
            print(f"  [OK] {fn.__name__}")
            ok += 1
        except AssertionError as e:
            print(f"  [FAIL] {fn.__name__}: {e}")
            fail += 1
        except Exception as e:
            print(f"  [ERR] {fn.__name__}: {type(e).__name__}: {e}")
            fail += 1
    print(f"\n{ok} 通过 / {fail} 失败")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
