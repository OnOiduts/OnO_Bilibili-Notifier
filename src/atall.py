# -*- coding: utf-8 -*-
"""开播提醒的 @全体成员（QQ 群聊 2026-10-26 起开放）。

官方原文（变更记录 20261026）：
    群聊场景文本消息、markdown 消息支持使用 <qqbot-at-everyone /> @全部成员，
    机器人需为该群管理员，且每个群每天仅可发送 1 次。

⚠️ 别和旧标签混：频道/文字子频道用的是 `@everyone`（内嵌格式），
   群聊以前根本不支持，所以 v1.18.0 把整个 @全体 删掉了。现在开放的是
   `<qqbot-at-everyone />`，是**另一种**标签，且带上面两条硬限制。

为什么单独一个文件、配额单独落盘：
  · 官方「每群每天 1 次」是服务端限制，撞了不会报错"额度用完"，
    只会整条消息发不出去。所以本地必须自己记住"今天这个群用过了"。
  · db.Store 的 meta 表在导入时被 _SKIP_TABLES 跳过，重启会丢；
    写到独立 json 文件才真的能跨重启生效。
  · 只有机器人进程写这个文件（面板只读不写），单写者，无并发覆盖风险。
"""
import json
import os
import threading
import time

import paths

AT_ALL_TAG = "<qqbot-at-everyone />"

# 带标签发送失败（多半是机器人非群管理员 / 当天额度已用）后，
# 30 分钟内不再尝试，避免每次开播都撞一次墙、还多一条请求。
FAIL_COOLDOWN = 1800.0

_LOCK = threading.Lock()


class AtAllQuota:
    """每群、每自然日一次 @全体 的配额记录。

    now / path 可注入，测试里能造"跨天"和"重启后仍在"两种真机场景。
    """

    def __init__(self, path: str = None, now=None):
        self.path = path or paths.run_file("at_all_quota.json")
        self._now = now or time.time

    # ---------- 落盘 ----------
    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
        return {}

    def _save(self, data: dict) -> None:
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, self.path)
        except Exception:
            # 配额写失败不该影响开播提醒本身；最坏情况是重启后重新获得一次机会，
            # 由官方的每日 1 次兜底。
            pass

    def _day(self) -> str:
        # 自然日按本机时区算（用户在国内，就是 CST）
        return time.strftime("%Y-%m-%d", time.localtime(self._now()))

    # ---------- 判定 ----------
    def available(self, gid: str) -> bool:
        """这个群现在还该不该带 @全体。"""
        d = self._load()
        used = (d.get("used") or {}).get(gid)
        if used == self._day():
            return False
        try:
            failed = float((d.get("failed") or {}).get(gid) or 0)
        except (TypeError, ValueError):
            failed = 0.0
        if failed and (self._now() - failed) < FAIL_COOLDOWN:
            return False
        return True

    def explain(self, gid: str) -> str:
        """返回「现在不能 @ 全体」的原因；可以 @ 时返回空串。

        给运行日志用：以前 _atall_want 返回 False 时完全静默，用户看到
        「开播提醒没 @ 全体」根本分不清是开关关了、今天额度用完了，
        还是上一次被平台拒了在冷却。
        """
        d = self._load()
        if (d.get("used") or {}).get(gid) == self._day():
            return "该群今天已 @过一次（官方每群每天限 1 次）"
        try:
            failed = float((d.get("failed") or {}).get(gid) or 0)
        except (TypeError, ValueError):
            failed = 0.0
        if failed and (self._now() - failed) < FAIL_COOLDOWN:
            left = int(FAIL_COOLDOWN - (self._now() - failed))
            return f"上次 @ 被平台拒绝，冷却中（约 {left}s 后再试）"
        return ""

    def mark_used(self, gid: str) -> None:
        """带标签的消息确实发出去了，才占掉今天这一次。"""
        with _LOCK:
            d = self._load()
            d.setdefault("used", {})[gid] = self._day()
            (d.setdefault("failed", {})).pop(gid, None)
            self._save(d)

    def mark_failed(self, gid: str) -> None:
        """带标签发送被拒：不占配额，但冷却一段时间别反复撞。"""
        with _LOCK:
            d = self._load()
            d.setdefault("failed", {})[gid] = self._now()
            self._save(d)
