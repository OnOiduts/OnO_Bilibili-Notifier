#!/usr/bin/env python3
"""打发布包。

跟 v1.98.6 保持一致：顶层只有 5 个脚本 + src/ docs/ tests/ tools/ deploy/。
绝不打进本机的 config.yaml / data.db / state.json / .machine_key / data/，
也排除 __pycache__ / backups / 日志等运行时残留。
"""
import io
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP_DIRS = ("src", "docs", "tests", "tools", "deploy")
KEEP_FILES = ("启动机器人.bat", "启动机器人.sh", "备份全部数据.bat",
              "安装依赖.bat", "恢复全部数据.bat",
              # ⚠️ 许可文件必须进包：v2.0.6 之前一直漏了它，
              #    源码目录里有 LICENSE，但不在 KEEP_FILES 里，
              #    结果用户拿到的压缩包里根本没有许可文件（放 GitHub 会没有 License）。
              "LICENSE",
              # GitHub 导出版四件套：说明 + 凭据模板 + 忽略清单。
              # README.md 必须进包：GitHub 仓库主页只渲染根目录这份，
              #    漏了的话仓库首页就是一片空白，看不出项目是干嘛的（v2.1.0 实测漏过）。
              # ⚠️ .env.example 里只有占位符，可以进包；真 .env 绝不能进（见 EXCLUDE_FILES）。
              "README.md", ".env.example", ".gitignore", ".gitattributes")
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "backups", "data",
                ".git", "logs", "cache", "run", "dist", "dist_obf"}
EXCLUDE_FILES = {"config.yaml", "data.db", "state.json", ".machine_key",
                 "_t_name.json", ".DS_Store",
                 # ⚠️ 真密钥文件：里面是用户填的真实 AppSecret / Cookie，
                 #    一旦进包等于把密钥公开。只有 .env.example 能进。
                 ".env"}
# 疑似明文密钥的正则：赋值语句后面跟一长串字母数字。
# 命中就中止打包 —— 宁可挡错，也不能把真密钥发出去。
_SECRET_PATTERNS = (
    r"(secret|app_secret|appsecret|token|password|passwd|api_?key)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]",
    r"SESSDATA\s*=\s*[A-Za-z0-9%,\-\.]{20,}",
    r"bili_jct\s*=\s*[A-Za-z0-9]{20,}",
)
_SCAN_SUFFIX = (".py", ".js", ".html", ".md", ".yaml", ".yml", ".cfg", ".txt")
# 白名单：这些是占位符/文档示例/代码框架，不是真密钥
_SCAN_ALLOW = ("your_", "xxx", "changeme", "请填写", "example",
               "placeholder", "<", "${", "os.environ", "getenv",
               # 环境变量名本身不是密钥：ENV_PANEL_PASSWORD = "ONOBN_PANEL_PASSWORD"
               # 这样的写法命中的是"变量名"，不是值。前缀 ONOBN_ 一律放行。
               "onobn_",
               # 字母表 / BASE64 表这类连续字母串，不是密钥
               "abcdefghijklmnopqrstuvwxyz", "abc...", "0123456789abc")
# 值长得像环境变量名（全大写 + 数字 + 下划线，没有小写字母）—— 不是真密钥。
# 真密钥都是大小写混合或纯小写十六进制，不会是这种形态。
_ENVNAME_RE = re.compile(r"['\"][A-Z0-9_]{16,}['\"]")


def _scan_secrets(entries):
    """打包前扫一遍：有没有把明文密钥写进源码。

    为什么需要：secretbox 只保证 config.yaml 里是密文，管不了有人
    图省事把 AppSecret 直接写死在 .py 里。公开仓库场景下这种写法
    等于直接把密钥交出去，所以这里硬拦。
    """
    import re
    pats = [re.compile(p, re.I) for p in _SECRET_PATTERNS]
    # ⚠️ 只看变量名是不够的。实测发现「变量名不含敏感词、值是 32 位混合大小写」
    #    的写法能直接绕过上面那组（故意注入验证过，确实拦不住），
    #    而 AppSecret 恰恰就是这种形态。所以补一条通用的：
    #    任何 24 位以上、大小写混合或纯小写的字母数字长串都拦。
    #    tests/ 里构造的假夹具（纯大写十六进制）不在此列，见下面的目录区分。
    generic = re.compile(r"=\s*['\"](?=[A-Za-z0-9]*[a-z])[A-Za-z0-9]{24,}['\"]")
    hits = []
    for full, rel in entries:
        if not rel.lower().endswith(_SCAN_SUFFIX):
            continue
        try:
            text = io.open(full, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        # tests/ 里必然有有意为之的假密钥夹具，只对"敏感变量名"那组严格，
        # 通用长串规则在 tests/ 下不启用，否则满屏误报。
        use_generic = not rel.replace("\\", "/").startswith("tests/")
        for pat in pats + ([generic] if use_generic else []):
            for m in pat.finditer(text):
                frag = m.group(0)
                if any(a in frag.lower() for a in _SCAN_ALLOW):
                    continue
                # 值本身是环境变量名（全大写形态），不是密钥，放行
                vm = _ENVNAME_RE.search(frag)
                if vm and vm.group(0)[1:-1].isupper():
                    continue
                line = text[:m.start()].count("\n") + 1
                hits.append((rel, line, frag[:40]))
    return hits


def _is_residue(fn):
    """测试跑完在源码目录留下的临时文件（.tmp-xxxx.json 之类）。

    这些一旦进了包，用户装完会莫名其妙多出群/订阅，必须挡住。"""
    return fn.startswith(".tmp-") or fn.startswith("_t_")


# ⚠️ 包名固定叫 OnO_Bilibili-Notifier，但**必须带版本号**。
#    v2.0.1 那版我把版本号去掉了，结果打出来的包跟上一版同名、
#    又不能从文件名看出是哪一版，用户没法判断自己装的是哪个。
#    现在统一成 OnO_Bilibili-Notifier_v2.0.3.zip 这种。
OUT_NAME = "OnO_Bilibili-Notifier_v%s.zip"
OUT_NAME_GITHUB = "OnO_Bilibili-Notifier_v%s_github.zip"

# GitHub 导出版必须的几件套：缺任何一个，传到 GitHub 都会出问题
# （没 LICENSE = 显示 No license，没 .env.example = 别人不知道配什么，
#   没 .gitignore = 用户很容易把真密钥提交上去，没 .gitattributes = .bat 变 LF 后双击闪退）
GITHUB_REQUIRED = ("README.md", "LICENSE", ".gitignore",
                   ".env.example", ".gitattributes")


def main(github: bool = False):
    ver = open(os.path.join(ROOT, "src", "VERSION"), encoding="utf-8").read().strip()
    if github:
        missing = [f for f in GITHUB_REQUIRED
                   if not os.path.isfile(os.path.join(ROOT, f))]
        if missing:
            print("  \u274c GitHub 导出版缺文件：%s" % "、".join(missing))
            return None
        out = os.path.join(os.path.dirname(ROOT), OUT_NAME_GITHUB % ver)
    else:
        out = os.path.join(os.path.dirname(ROOT), OUT_NAME % ver)
    n = 0
    if os.path.exists(out):
        os.remove(out)
    _entries = []
    for name in KEEP_FILES:
        p = os.path.join(ROOT, name)
        if os.path.isfile(p):
            _entries.append((p, name))
    for d in KEEP_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in EXCLUDE_DIRS]
            for fn in filenames:
                if fn in EXCLUDE_FILES or fn.endswith((".pyc", ".log")):
                    continue
                if _is_residue(fn):
                    continue
                _entries.append((os.path.join(dirpath, fn),
                                 os.path.relpath(os.path.join(dirpath, fn), ROOT)))
    # 密钥闸门：宁可中止打包，也不把明文密钥发出去
    _hits = _scan_secrets(_entries)
    if _hits:
        print("  \u274c 打包中止：检出疑似明文密钥：")
        for rel, line, frag in _hits[:10]:
            print("     %s:%d  %s" % (rel, line, frag))
        return None

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in KEEP_FILES:
            p = os.path.join(ROOT, name)
            if os.path.isfile(p):
                z.write(p, name)
                n += 1
            else:
                print("  ⚠️ 缺脚本 %s" % name)
        for d in KEEP_DIRS:
            base = os.path.join(ROOT, d)
            if not os.path.isdir(base):
                print("  ⚠️ 缺目录 %s" % d)
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [x for x in dirnames if x not in EXCLUDE_DIRS]
                for fn in filenames:
                    if fn in EXCLUDE_FILES or fn.endswith((".pyc", ".log")):
                        continue
                    if _is_residue(fn):
                        print("  ⚠️ 挡下测试残留 %s" % fn)
                        continue
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, ROOT)
                    z.write(full, rel)
                    n += 1
    print("打包完成：%s（v%s，%d 个文件）" % (out, ver, n))
    print("  %d 个文件，%.0f KB" % (n, os.path.getsize(out) / 1024))
    return out


if __name__ == "__main__":
    # --github：出 GitHub 导出版（会先检查四件套齐全）
    sys.exit(0 if main(github="--github" in sys.argv) else 1)
