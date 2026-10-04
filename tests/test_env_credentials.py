# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""环境变量凭据 + 仓库无明文密钥 的守卫测试。

为什么要有这份测试：项目要传到公开 GitHub。密钥一旦提交上去，
改配置救不回来，只能去平台重置。所以这里用两条硬闸把住：

    1. 环境变量能真正盖住 config.yaml，且不会被写回配置文件
    2. 仓库里扫不出任何真实密钥（只许有占位符）
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")

sys.path.insert(0, SRC)

# 扫描范围：会被提交到仓库的文本文件
SCAN_EXT = (".py", ".js", ".html", ".md", ".txt", ".yml", ".yaml",
            ".bat", ".sh", ".json", ".example")

# 真实密钥的特征。注意只抓"写死的真值"，不抓 os.environ / cfg.get 这类读取
SECRET_PATTERNS = [
    # SESSDATA=xxxx 这种 B 站 Cookie 字段
    re.compile(r"\b(SESSDATA|bili_jct|DedeUserID__ckMd5|sid_[a-z0-9]{5,})"
               r"\s*[=:]\s*[\"']?[A-Za-z0-9%,\-_.]{12,}"),
    # 32 位十六进制（常见于 AppSecret）
    re.compile(r"[\"'][0-9a-fA-F]{32}[\"']"),
    # appid = 10 位以上纯数字字符串（QQ AppID 是 10 位左右）
    re.compile(r"[\"']\d{10,12}[\"']"),
]

# 这些一看就是示例 / 测试用的假值，不算真密钥
ALLOWED_VALUES = re.compile(
    r"1234567890|123456789012345678|your_|placeholder|example|changeme|"
    r"xxxx|xxxxxx|请填写|259149243|test|dummy|fake|REDACTED|"
    r"abcdefgh12345678|000000|111111", re.I)

# 扫描时要跳过的目录
# ⚠️ tests/ 为什么跳过：测试用例里必然要构造假的密钥/哈希来验证逻辑
#    （比如"A1B2C3D4E5F60718293A4B5C6D7E8F90"是故意写死的测试夹具）。
#    这些是有意为之的假数据，扫了只会满屏误报。
#    真正的风险在 src/ —— 历史上出过两次问题：发布包误带 .machine_key，
#    以及测试残留混进 src 变成假群。所以 src/ 一步不让。
SKIP_DIRS = {"__pycache__", ".git", "data", "logs", "backups",
             "dist", "dist_obf", ".pytest_cache", "cache", "images",
             "tests"}


def _iter_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".venv")]
        for name in filenames:
            if name.endswith(SCAN_EXT):
                yield os.path.join(dirpath, name)


def test_no_hardcoded_secret_in_repo():
    """仓库里不能有任何写死的真实密钥。"""
    hits = []
    for path in _iter_files():
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    line_s = line.strip()
                    # 注释里的说明、以及从环境变量读取的代码，一律跳过
                    if "os.environ" in line or "getenv" in line \
                            or "envcfg" in line or "ONOBN_" in line:
                        continue
                    for pat in SECRET_PATTERNS:
                        m = pat.search(line)
                        if not m:
                            continue
                        frag = m.group(0)
                        if ALLOWED_VALUES.search(frag):
                            continue
                        hits.append(f"{os.path.relpath(path, ROOT)}:"
                                    f"{lineno}: {frag[:40]}")
                        break
        except OSError:
            continue
    assert not hits, "疑似写死的真实密钥（提交前必须清掉）：\n" + "\n".join(hits)


def test_env_overrides_config(monkeypatch):
    """环境变量里的凭据要盖住 config.yaml 里那一份。"""
    import envcfg
    envcfg._DOT_ENV_LOADED = False
    monkeypatch.setenv("ONOBN_APPID", "7654321098")
    monkeypatch.setenv("ONOBN_APP_SECRET", "s" * 32)
    cred = envcfg.creds_from_env()
    assert cred["appid"] == "7654321098"
    assert cred["secret"] == "s" * 32

    merged = envcfg.apply_env({"appid": "111", "secret": "old", "other": 1})
    assert merged["appid"] == "7654321098"      # 被环境变量盖掉
    assert merged["secret"] == "s" * 32
    assert merged["other"] == 1                 # 无关字段不受影响


def test_placeholder_env_ignored(monkeypatch):
    """填了占位符等于没填，不能把示例值当真密钥。"""
    import envcfg
    envcfg._DOT_ENV_LOADED = False
    for bad in ("", "xxx", "your_appid", "changeme", "请填写"):
        monkeypatch.setenv("ONOBN_APPID", bad)
        assert envcfg.creds_from_env().get("appid") is None, f"占位符 {bad!r} 不该被采用"


def test_env_creds_never_written_back(tmp_path, monkeypatch):
    """⚠️ 关键：来自环境变量的凭据不能写回 config.yaml。

    否则面板一保存，环境变量里的真密钥就落到文件里，
    "密钥不落盘"这个前提当场失效。
    """
    import envcfg
    envcfg._DOT_ENV_LOADED = False
    # 两个都设成"来自环境变量"，才能验证两个都不会被写回
    monkeypatch.setenv("ONOBN_APPID", "7654321098")
    monkeypatch.setenv("ONOBN_APP_SECRET", "s" * 32)

    saved = {"appid": "7654321098", "secret": "s" * 32,
             "bili_cookie": "", "poll": 12}
    out = dict(saved)
    for field in envcfg.env_sourced_fields():
        out.pop(field, None)
    assert "secret" not in out, "环境变量里的 secret 不该被写回配置文件"
    assert "appid" not in out, "环境变量里的 appid 不该被写回配置文件"
    assert out["poll"] == 12


def test_dotenv_file_loading(tmp_path, monkeypatch):
    """能从 .env 文件读到值，且已存在的系统环境变量不被 .env 覆盖。"""
    import envcfg
    envfile = tmp_path / ".env"
    envfile.write_text(
        "# 注释行\nONOBN_APPID=1111111111\nONOBN_APP_SECRET=\"s\" * 0\n"
        "ONOBN_PANEL_PORT=9090\n", encoding="utf-8")
    monkeypatch.setenv("ONOBN_ENV_FILE", str(envfile))
    monkeypatch.delenv("ONOBN_APPID", raising=False)
    monkeypatch.delenv("ONOBN_APP_SECRET", raising=False)
    monkeypatch.delenv("ONOBN_PANEL_PORT", raising=False)
    envcfg._DOT_ENV_LOADED = False
    envcfg.load_dotenv(force=True)

    assert os.environ.get("ONOBN_APPID") == "1111111111"
    assert envcfg.panel_port_from_env() == 9090

    # 系统里已有的，.env 不能改
    monkeypatch.setenv("ONOBN_APPID", "2222222222")
    envfile.write_text("ONOBN_APPID=3333333333\n", encoding="utf-8")
    envcfg._DOT_ENV_LOADED = False
    envcfg.load_dotenv(force=True)
    assert os.environ["ONOBN_APPID"] == "2222222222", ".env 不该盖掉系统环境变量"


def test_dotenv_example_has_no_real_value():
    """.env.example 里只许有占位符，不能混进真实值。"""
    path = os.path.join(ROOT, ".env.example")
    assert os.path.isfile(path), "缺少 .env.example"
    text = open(path, encoding="utf-8").read()
    for pat in SECRET_PATTERNS:
        for m in pat.finditer(text):
            frag = m.group(0)
            assert ALLOWED_VALUES.search(frag) or "here" in frag, \
                f".env.example 里出现了非占位符的值：{frag[:40]}"


def test_gitignore_excludes_env():
    """.gitignore 必须排除 .env 与数据目录，否则密钥会被推上去。"""
    path = os.path.join(ROOT, ".gitignore")
    assert os.path.isfile(path), "缺少 .gitignore"
    text = open(path, encoding="utf-8").read()
    for must in (".env", "config.yaml", "data/", "*.db", "state.json",
                 "__pycache__/", "*.machine_key"):
        assert must in text, f".gitignore 缺少对 {must} 的排除"


def test_readme_documents_third_party_licenses():
    """README 必须列出第三方库及其许可证。"""
    path = os.path.join(ROOT, "README.md")
    assert os.path.isfile(path), "缺少根目录 README.md"
    text = open(path, encoding="utf-8").read()
    for lib in ("qq-botpy", "aiohttp", "Flask", "PyYAML", "playwright"):
        assert lib in text, f"README 未提及依赖 {lib}"
    for lic in ("MIT", "Apache-2.0", "BSD-3-Clause"):
        assert lic in text, f"README 未注明许可证 {lic}"
    # 三条声明
    assert "服务器端从未测试过" in text or "服务器端从未实测" in text
    assert "Hy4 preview" in text
    assert "波萝Buono" in text


def test_license_credits_author():
    """署名与来源：LICENSE 保持纯 MIT，补充说明一律放 README。

    ⚠️ v2.1.3 起用户明确要求「MIT 就正儿八经写中英文的，别写啥补充了，
       补充的都放 README」。所以：
         LICENSE  —— 只有标准 MIT（中英双语）+ 版权人（含 GitHub 链接）
         README   —— 作者署名、AI 生成说明、测试范围声明
       旧断言要求 LICENSE 里出现「Hy4 preview」，与这条要求冲突，已改。
    """
    path = os.path.join(ROOT, "LICENSE")
    assert os.path.isfile(path), "缺少 LICENSE"
    text = open(path, encoding="utf-8").read()
    # 版权人署名（含 GitHub 链接，这是标准 MIT 写法，不算附加条款）
    assert "波萝Buono" in text
    assert "XxBoLuoxX" in text
    # 许可正文必须是标准 MIT，不能夹带额外限制条款
    assert "MIT License" in text
    assert "Permission is hereby granted, free of charge" in text

    # 补充说明搬去 README，LICENSE 里不许再出现
    for extra in ("Hy4 preview", "腾讯元宝", "禁止", "未经作者书面许可"):
        assert extra not in text, "LICENSE 里夹带了补充说明：%s" % extra

    # README 必须承担起署名与来源说明
    rd = ""
    for cand in ("README.md", os.path.join("docs", "README.md")):
        fp = os.path.join(ROOT, cand)
        if os.path.isfile(fp):
            rd += open(fp, encoding="utf-8").read()
    for must in ("波萝Buono", "XxBoLuoxX", "Hy4 preview", "腾讯元宝"):
        assert must in rd, "README 缺少署名/来源说明：%s" % must


def test_dotenv_search_path_includes_data_home(tmp_path, monkeypatch):
    """⚠️ 回归：查 .env 时曾经写了 paths.data_dir()，而 paths 里只有 data_home()。

    抛出的 AttributeError 被 except 悄悄吞掉，表现是「.env 明明放对了地方却读不到」，
    端到端实测才暴露。这里锁住：候选路径里必须包含数据目录下的 .env。
    """
    import envcfg
    import paths
    monkeypatch.setenv("ONOBN_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ONOBN_ENV_FILE", raising=False)
    cands = envcfg._candidate_dotenv_paths()
    assert any(os.path.abspath(str(tmp_path)) in os.path.abspath(c)
               for c in cands), \
        f"候选路径里没有数据目录（当前 data_home={paths.data_home()}）：{cands}"
    # 数据目录下真放一个 .env，要能读到
    (tmp_path / ".env").write_text("ONOBN_APPID=5550001112\n", encoding="utf-8")
    monkeypatch.delenv("ONOBN_APPID", raising=False)
    envcfg._DOT_ENV_LOADED = False
    loaded = envcfg.load_dotenv(force=True)
    assert loaded and os.path.abspath(loaded) == os.path.abspath(
        str(tmp_path / ".env")), f"没加载到数据目录下的 .env，实际={loaded}"
    assert os.environ.get("ONOBN_APPID") == "5550001112"


def test_envcfg_module_importable():
    """模块本身要能被导入且语法正确（防止发布时被漏掉）。"""
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, %r); import envcfg; "
                        "print(envcfg.env_sourced_fields())" % SRC],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"envcfg 导入失败：{r.stderr[-500:]}"
