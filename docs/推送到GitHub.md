# 推送到 GitHub —— 一步一步

> 本文档对应仓库：<https://github.com/XxBoLuoxX/OnO_Bilibili-Notifier>
> 面向第一次把本项目传上 GitHub 的情况。**按顺序执行即可**，每一步都写了"做完了该看到什么"。

---

## 第 0 步：推送前先自查（最重要，别跳）

真密钥一旦推上去，**改配置救不回来**——只能去 QQ 开放平台重置 AppSecret、B 站退出登录重扫。
所以先跑一遍自查：

```bash
# 1）确认 .gitignore 真的挡住了密钥文件（.env 后面应显示 ignored）
git status --ignored --short | grep -E "\.env$|config\.yaml|machine_key" 

# 2）确认仓库里没有形似真密钥的字符串
grep -rniE "(secret|token|password|api_?key)\s*[:=]\s*['\"][A-Za-z0-9_-]{16,}['\"]" \
  src/ tests/ docs/ tools/ deploy/ *.md
# ↑ 没有输出 = 通过。有输出就先改掉再往下走。

# 3）确认没有把真实数据带进去
ls data 2>/dev/null && echo "⚠️ data/ 里有本机数据，确认它已被忽略"
```

**`.env` 绝不能出现在 `git status` 的文件列表里**（只能出现在 ignored 里）。
万一它已经进了暂存区，先执行：

```bash
git rm --cached .env
git rm --cached config.yaml
```

再往下走。

---

## 第 1 步：本地建仓库

```bash
cd OnO_Bilibili-Notifier

git init
git config user.name  "XxBoLuoxX"
git config user.email "你在 GitHub 用的邮箱"

# 换行符处理：仓库里统一 LF，Windows 取出 bat 时自动转回 CRLF（靠 .gitattributes）
git config core.autocrlf input
```

## 第 2 步：加进暂存区并检查

```bash
git add .
git status --short | head -50
```

逐项看一眼这个列表：

- ✅ 应该有：`README.md`、`LICENSE`、`.gitignore`、`.env.example`、`.gitattributes`、
  5 个启动脚本、`src/`、`docs/`、`tests/`、`tools/`、`deploy/`
- ❌ 不该有：`.env`、`config.yaml`、`*.machine_key`、`data/`、`logs/`、`*.db`、
  `state.json`、`__pycache__/`、`dist/`

有 ❌ 项就说明 `.gitignore` 没生效，回到第 0 步处理。

## 第 3 步：首次提交

```bash
git commit -m "feat: OnO Bilibili Notifier v2.1.0 首次公开

B 站更新提醒机器人：开播 / 投稿 / 动态 / 置顶评论自动推送到 QQ 群。
代码由腾讯元宝 Hy4 preview (AI) 生成，作者 波萝Buono 整理、测试与维护。
凭据一律从环境变量 / .env 读取，仓库内不含任何明文密钥。"
```

## 第 4 步：在 GitHub 上建空仓库

1. 打开 <https://github.com/new>
2. **Repository name**：`OnO_Bilibili-Notifier`
3. **Public**（公开）
4. ⚠️ **不要**勾 "Add a README file"、**不要**选 .gitignore、**不要**选 license
   —— 本地都已经有了，勾了会冲突
5. 点 Create repository

## 第 5 步：关联并推送

```bash
git remote add origin https://github.com/XxBoLuoxX/OnO_Bilibili-Notifier.git
git branch -M main
git push -u origin main
```

第一次会让你登录。推荐用 **GitHub 个人访问令牌（PAT）** 当密码：
GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens，
权限只勾 `Contents: Read and write` 就够。

推送成功的标志：命令行出现 `branch 'main' set up to track`，浏览器刷新仓库页能看到文件。

## 第 6 步：在 GitHub 网页上补三件事

| 做什么 | 在哪 |
|---|---|
| **About 描述** | 仓库页右上齿轮 → 填「QQ 开放平台机器人 · B 站开播/投稿/动态自动推送到 QQ 群」，Homepage 填你的 B 站空间 |
| **Topics 标签** | 同上 → 加 `bilibili` `qq-bot` `qq-open-platform` `notification` `python` `botpy` |
| **License 显示** | GitHub 会自动识别 `LICENSE`；若显示 "Other"，点进去确认内容与本地一致即可 |

## 第 7 步：推完再验一次

在 GitHub 仓库页确认：

- 左侧 License 一栏有内容（不是 No license）
- 打开 `.env.example`，里面全是 `your_appid_here` 这类占位符
- **搜不到**任何真实 AppSecret / Cookie（页面搜索 `SESSDATA=` 应无结果）
- `data/`、`logs/` 目录不存在

---

## 以后怎么更新

```bash
git add .
git commit -m "fix: 一句话说明改了什么"
git push
```

每次推送前重跑第 0 步的自查，尤其是新增了配置文件的时候。

## 万一真的泄露了密钥

1. **立刻**去 [QQ 开放平台](https://q.qq.com) 重置 AppSecret
2. B 站退出登录、重新扫码
3. 删掉本地 `.env`
4. ⚠️ GitHub 的提交历史是删不干净的——改掉旧密钥才是唯一的止损办法，
   单纯"删掉那个文件再提交一次"没有用
