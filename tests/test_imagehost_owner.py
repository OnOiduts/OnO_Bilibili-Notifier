# -*- coding: utf-8 -*-
"""图床仓库已转移到 OnOiduts 组织 —— 默认地址必须跟着改。

背景
----
图床 `OnO-ImageHost-` 和工具仓库都从个人账号 XxBoLuoxX 转移到了组织
OnOiduts。转移后 GitHub 的旧地址会自动重定向，但**重定向救不了程序**：

- jsDelivr 是按 `gh/owner/repo@ref` 直接查的，不走 GitHub 重定向；
  S3 里可能还留着旧副本撑一阵，但更新一定断，而且随时失效。
- raw.githubusercontent.com 虽能重定向，但同样不稳定。

所以程序里这几处硬编码地址必须一起改，否则：面板 logo、标签页图标、
登录页四张背景图会一起消失（且是延迟发作，极难排查）。

⚠️ 注意区分两类链接，**不要一锅端**：
    - **图床地址** → 必须改成 OnOiduts（本文件管这部分）
    - **作者署名** → 保持 XxBoLuoxX（许可证条款要求标注「来源于
      波萝Buono（GitHub: XxBoLuoxX）」，改掉会让署名不完整）

仓库名 `OnO-ImageHost-` 末尾那个连字符是历史遗留（当初多打的），
转移时保留了原名，所以地址里仍然带它 —— 漏了就 404。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

NEW_OWNER = "OnOiduts"
OLD_OWNER = "XxBoLuoxX"
REPO = "OnO-ImageHost-"          # 末尾连字符不能丢
TOOL_REPO = "OnO_Bilibili-Notifier"


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_all_four_default_urls_use_new_owner():
    """logo / ico / bg1 / bg2 四个默认地址都指向新组织。"""
    import webui
    urls = {
        "LOGO": webui.LOGO_URL_DEFAULT,
        "ICO": webui.ICO_URL_DEFAULT,
        "BG1": webui.BG1_URL_DEFAULT,
        "BG2": webui.BG2_URL_DEFAULT,
    }
    for name, url in urls.items():
        assert NEW_OWNER in url, (
            f"{name} 默认地址还指向旧账号，图会取不到：{url}")
        assert REPO in url, (
            f"{name} 仓库名必须完整（末尾带连字符）：{url}")


def test_no_default_url_left_on_old_owner():
    """四个默认地址里不许再出现旧账号名。

    这条专治"改了一半"：只改了 logo 忘了 ico/bg，或正则替换漏了一处。
    光看"含 OnOiduts"是不够的 —— 一条地址里两个名字可以同时存在。
    """
    import webui
    urls = {
        "LOGO": webui.LOGO_URL_DEFAULT,
        "ICO": webui.ICO_URL_DEFAULT,
        "BG1": webui.BG1_URL_DEFAULT,
        "BG2": webui.BG2_URL_DEFAULT,
    }
    for name, url in urls.items():
        assert OLD_OWNER not in url, (
            f"{name} 仍残留旧账号名 {OLD_OWNER}：{url}")


def test_repo_name_keeps_trailing_hyphen():
    """仓库名末尾的连字符不能丢（转移时保留了原名）。"""
    import webui
    for url in (webui.LOGO_URL_DEFAULT, webui.ICO_URL_DEFAULT,
                webui.BG1_URL_DEFAULT, webui.BG2_URL_DEFAULT):
        assert "OnO-ImageHost-@main" in url or "OnO-ImageHost-/main" in url, (
            f"仓库名必须是 OnO-ImageHost-（末尾带连字符），实际：{url}")


def test_clone_url_in_readme_updated():
    """README 的安装地址要指向组织下的仓库（能 clone 才有用）。"""
    for rel in ("README.md", "docs/README.md"):
        text = _read(rel)
        assert f"github.com/{NEW_OWNER}/{TOOL_REPO}.git" in text, (
            f"{rel} 的 clone 地址没更新到组织下")
        assert f"github.com/{OLD_OWNER}/{TOOL_REPO}.git" not in text, (
            f"{rel} 还留着旧账号的 clone 地址")


def test_author_attribution_still_personal():
    """署名必须保持个人账号 —— 许可证要求标注作者。

    这条是给上一个测试的"刹车"：改图床地址时很容易顺手把署名一起换掉，
    那样许可证条款里的「来源于 波萝Buono（GitHub: XxBoLuoxX）」就不成立了。
    """
    text = _read("README.md")
    assert f"github.com/{OLD_OWNER}" in text, (
        "README 里的作者署名不应改掉（许可证条款要求）")
    assert NEW_OWNER + "/" + TOOL_REPO in text, (
        "README 里应有组织下的仓库地址")
