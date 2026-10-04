# -*- coding: utf-8 -*-
"""根目录只允许有启动/安装/备份相关的脚本，其余一律进子目录。

背景（用户反馈）：根目录一堆 md / yml / txt / Dockerfile 混在一起，
分不清哪些能删、哪些一删就出事。v1.96 起根目录只留 5 个脚本，
其它按用途归进 src/（源码与随包文件）、docs/（文档）、deploy/（容器）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

FAILS = []


def ck(name, cond):
    if cond:
        print("  [ok]", name)
    else:
        print("  [FAIL]", name)
        FAILS.append(name)


def main():
    entries = sorted(os.listdir(ROOT))
    files = [e for e in entries if os.path.isfile(os.path.join(ROOT, e))]
    dirs = [e for e in entries if os.path.isdir(os.path.join(ROOT, e))]

    print("根目录:", entries)

    # 1. 根目录不能有任何非脚本文件（含隐藏文件）
    #    ⚠️ 这五类是 GitHub 仓库的"门面文件"，只认根目录：
    #       - LICENSE      —— 放 docs/ 里等于没有许可证
    #       - README.md    —— 仓库主页默认渲染根目录这份
    #       - .gitignore   —— Git 只认根目录（及子目录）的这份
    #       - .env.example —— 凭据模板，跟源码放一起才找得到
    #       - .gitattributes —— 换行符规则，只认根目录这份
    #         （.bat 被 Git 转成 LF 会在 Windows 上闪退/乱码，必须钉死 CRLF）
    #      所以显式放行，不算"多余"。除这五类之外仍然一个都不许有。
    allowed_scripts = {"启动机器人.bat", "启动机器人.sh", "安装依赖.bat",
                       "备份全部数据.bat", "恢复全部数据.bat"}
    allowed_extra = {"LICENSE", "README.md", ".gitignore", ".env.example",
                     ".gitattributes"}
    stray = [f for f in files
             if f not in allowed_scripts and f not in allowed_extra]
    ck("根目录没有多余文件（多余：%s）" % (stray or "无"), not stray)
    ck("LICENSE 在根目录（GitHub 才认）", "LICENSE" in files)
    ck("README.md 在根目录（GitHub 才渲染）", "README.md" in files)
    ck(".gitignore 在根目录（Git 才认）", ".gitignore" in files)
    ck(".env.example 在根目录（凭据模板）", ".env.example" in files)
    ck("5 个启动/安装脚本都在", allowed_scripts.issubset(set(files)))

    # 2. 子目录各就各位
    ck("有 src/", "src" in dirs)
    ck("有 docs/", "docs" in dirs)
    ck("有 tests/", "tests" in dirs)

    # 3. 原来摊在根目录的东西现在都在子目录里
    ck("VERSION 在 src/", os.path.isfile(os.path.join(ROOT, "src", "VERSION")))
    ck("requirements.txt 在 src/",
       os.path.isfile(os.path.join(ROOT, "src", "requirements.txt")))
    ck("requirements-core.txt 在 src/",
       os.path.isfile(os.path.join(ROOT, "src", "requirements-core.txt")))
    ck("config.example.yaml 在 docs/",
       os.path.isfile(os.path.join(ROOT, "docs", "config.example.yaml")))
    ck("README.md 在 docs/", os.path.isfile(os.path.join(ROOT, "docs", "README.md")))
    ck("CHANGELOG.md 在 docs/",
       os.path.isfile(os.path.join(ROOT, "docs", "CHANGELOG.md")))
    ck("Dockerfile 在 deploy/",
       os.path.isfile(os.path.join(ROOT, "deploy", "Dockerfile")))

    # 4. 代码还能找得到它们（不能搬完就读不到）
    import paths
    ck("paths.asset 找得到 VERSION", os.path.exists(paths.asset("VERSION")))
    ck("paths.asset 找得到 requirements.txt",
       os.path.exists(paths.asset("requirements.txt")))
    ck("paths.asset 找得到 config.example.yaml",
       os.path.exists(paths.asset("config.example.yaml")))

    # 5. 版本号读得出来（老布局也要认）
    import version
    v = version.current()
    ck("版本号读得到（%s）" % v, v and v != "0.0.0")

    # 6. 脚本里的依赖清单路径跟着改了
    sh = open(os.path.join(ROOT, "启动机器人.sh"), encoding="utf-8").read()
    ck("启动脚本指向 src/requirements.txt", "src/requirements.txt" in sh)
    bat = open(os.path.join(ROOT, "安装依赖.bat"), encoding="utf-8").read()
    # v2.2.2 起改回全量清单：core 清单漏了 playwright，装完点登录会报缺依赖
    ck("安装脚本指向 src\\requirements.txt",
       "src\\requirements.txt" in bat)

    print()
    if FAILS:
        print("FAIL %d: %s" % (len(FAILS), FAILS))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
