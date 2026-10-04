# 公共 API 契约（类名固定，不得随意改名）

> 🤖 **代码由 AI 生成**：腾讯元宝「Hy4 preview」；作者整理、测试与维护。

> 项目：**OnOB站通知订阅工具**（OnO Bilibili Notifier / OnOBN）

> **约定**：本文件列出的类名、方法名**固定不变**。
> 新功能请新增，不要重命名已有成员。
> `tests/test_security.py::t_public_api_contract_stable` 会自动守住这份清单，
> 一旦有人改名或删除，测试立即失败。

## 为什么需要这份契约

此前出现过：调用方写 `get_pinned_comment(dyn_id, uid, rid)` 三个参数，
而被调方只声明两个 → TypeError 被 except 静默吞掉 →
表现为"功能没反应"且日志无异常。类名方法名不稳定是同类事故的根源。

## 核心类

| 类 | 所在模块 | 职责 |
|---|---|---|
| `NotifyBot` | bot.py | 机器人主类（继承 `botpy.Client`） |
| `Store` | db.py | 订阅存储（SQLite 内存引擎 + JSON 落盘） |
| `BiliClient` | bilibili.py | B 站接口客户端（含 WBI 签名） |
| `QQClient` | qqapi.py | QQ 开放平台接口客户端 |
| `Guard` | harden.py | 加固（限流 / 封禁 / 完整性） |
| `LoginLimiter` | security.py | 登录口令防爆破 |
| `Audit` | security.py | 安全审计日志 |
| `BiliLogin` | bili_login.py | B 站扫码登录 |
| `Up` / `LiveInfo` / `VideoInfo` / `DynamicInfo` | bilibili.py | B 站数据模型 |

## NotifyBot 固定方法

检测链：`_check_live` `_check_video` `_check_dynamic` `_check_top_comment`
轮询：`_poll_loop` `_poll_once` `_startup_sync` `_adapt_interval`
推送：`_push`
指令：`_handle`

## Store 固定方法

`ensure_group` `set_sub` `set_up` `set_group_name` `remove_up`
`targets` `subscribed_kinds` `enabled_kinds_of_up`
`all_ups` `all_groups` `ups_of_group`
`baseline` `set_baseline` `refresh` `get_up`

## BiliClient 固定方法

`get_up` `get_live` `get_room_id_by_mid`
`get_latest_video` `get_latest_dynamic` `get_pinned_dynamic`
`get_pinned_comment`
（签名改动必须同步更新测试桩，见 `t_fake_bili_matches_real_signature`）

## 异常类

`BiliError`（bilibili.py）`QQError`（qqapi.py）
`BiliLoginError`（bili_login.py）`BrowserLoginError`（bili_browser_login.py）

## 路径解析（`paths.py`）

程序目录与数据目录是**分开**的，任何读写数据文件的地方都必须走 `paths`：

| 函数 | 返回 |
|---|---|
| `data_dir()` | 数据目录（配置 / 订阅 / 日志 / 图片） |
| `log_dir()` | 日志目录 |
| `asset(name)` | 包内静态资源（兼容新旧两种布局） |

⚠️ 直接拼相对路径会导致**面板和机器人读到两个不同的文件**（v1.98.3 修过一次），
新增代码一律走这三个函数。

## 已删除，不要再恢复

- `store.py` —— v1.2.0 起被 `db.py` 取代
- `data.json` 作为主存储 —— 早期迁到 SQLite；v1.93 起落盘改为 JSON 快照，SQLite 仅作内存引擎
- 群内指令与指令面板 —— 见「废弃功能清单.md」
