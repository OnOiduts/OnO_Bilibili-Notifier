# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""B 站登录态"实际失效"提醒（与"快到期"是两回事）。

为什么要单独做这一个：
    已有的 bili_expiry_alert 管的是**剩余天数**（按 cookie 上写的到期
    时间推算）。但 B站 常常在到期之前就把登录态作废——风控（-352）、
    权限不足（-403）、别处重新登录把它挤下线、长期没有真人操作被清掉。
    这类失效**剩余天数还是 179 天**，到期提醒永远触发不了，
    于是订阅静默地查不动了，用户要到好几天后才发现"怎么一直没消息"。

判定口径（用户定的规则）：
    连续 N 次（默认 5）检测都判定"登录态没生效" → 认定为 cookie 真的挂了
    → 面板红色告警 + 向沙盒群推一条 QQ 消息。

⚠️ 为什么必须是"连续"而不是"累计"：
    单个 UP 主的偶发失败、一次网络抖动，都可能让某轮判定失败。
    累计计数会把这些偶发当真，几天后必然误报。
    **中间成功一次就清零** —— 能成功说明登录态还在，只是那一下没问到。

⚠️ 为什么达到阈值后只提醒一次：
    轮询每 8~30 秒一轮，不记状态的话同一条提醒会每轮推一次，
    沙盒群被同一句话刷满。这里用 `_fired` 记账，
    只有登录态恢复（连续失败被清零）后才重置 —— 下次再挂还能提醒。

⚠️ 为什么认定后日志要降级：
    认定"cookie 挂了"并已经提醒之后，再每 10 分钟喊一遍没有意义，
    日志反而把真正的推送记录埋掉（这个项目踩过同样的坑）。
    提醒过一次之后，后续重复判定只写 debug。
"""
import time

# 连续失败多少次才算"真挂了"。做成常量，测试和文档才对得上。
DEFAULT_STREAK = 5
# 配置项名（config.yaml 里可覆盖）
STREAK_KEY = "auth_fail_alert_streak"


class AuthAlert:
    """记住"连续失败了几次、提醒过了没有"。"""

    def __init__(self, streak: int = DEFAULT_STREAK):
        self.streak = max(1, int(streak or DEFAULT_STREAK))
        self._n = 0            # 连续失败次数
        self._fired = False    # 这一轮"挂了"有没有提醒过
        self._bad = False      # 当前是否处于认定失效状态

    @property
    def count(self) -> int:
        return self._n

    @property
    def fired(self) -> bool:
        return self._fired

    @property
    def bad(self) -> bool:
        return self._bad

    def feed(self, bad: bool) -> dict:
        """喂一次本轮的判定结果。

        返回 {"bad","count","need_alert","recovered","text"}
          need_alert   → 这一轮要不要推提醒（只会在达到阈值那一轮为 True）
          recovered    → 这一轮是不是刚从失效恢复（用于清掉面板横幅）
        """
        if not bad:
            recovered = bool(self._bad or self._fired or self._n)
            self._n = 0
            self._fired = False
            self._bad = False
            return {"bad": False, "count": 0, "need_alert": False,
                    "recovered": recovered, "text": ""}

        self._n += 1
        self._bad = True
        reached = (self._n == self.streak)   # 刚跨过阈值那一轮
        # ⚠️ 这里**不**置 _fired —— 只有真的推送成功（mark_sent）才算通知过。
        # 否则沙盒群没配、或推送失败时会被误记成"已通知"，
        # 用户配好群之后反而永远收不到这条提醒（到期提醒那边踩过同样的坑）。
        need = (self._n >= self.streak) and not self._fired
        return {"bad": True, "count": self._n, "need_alert": need,
                "reached": reached, "recovered": False,
                "text": text_of(self._n, self.streak)}

    def mark_sent(self):
        """推成功了才记账；推失败不记，下一轮还会再试。"""
        self._fired = True


def build_message(reason: str = "", count: int = 0) -> str:
    """推到沙盒群的那句话。纯文本——这是条通知，不需要按钮。"""
    line = ("⚠️ B 站登录态失效，订阅已停摆\n"
            "连续多次检测都被 B站 拦回，投稿/动态现在查不动了。\n"
            "这通常不是过期（剩余天数可能还有很多），而是被风控踢下线或"
            "在别处重新登录把它挤掉了。\n"
            "请到面板「🔑 B 站登录」重新扫码，或重新粘贴完整 Cookie。")
    why = (reason or "").strip()
    if why:
        line += f"\nB站 返回：{why}"
    if count:
        line += f"\n（连续失败 {count} 次后判定）"
    return line


def text_of(count: int, streak: int = DEFAULT_STREAK) -> str:
    """面板横幅上的一句话。"""
    return (f"B 站登录态已失效：连续 {count} 次被拦（阈值 {streak}），"
            f"投稿/动态查不动了，请重新登录")
