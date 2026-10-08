# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""B 站登录态到期提醒。

为什么要有它：
    cookie 失效是**静默**的——程序不报错、面板不提示，只是订阅的投稿/动态
    悄悄不再推送。用户往往要过好几天才发现"怎么一直没消息"。
    这里按剩余天数分级提醒，把静默失效变成看得见的提示。

分级（用户定的规则）：
    剩余 > 5 天              → 安静
    剩余 2 ~ 5 天（含）       → 只在面板顶部提醒（UI）
    剩余 < 2 天，或已经过期   → 面板红色告警 + 向沙盒群推一条 QQ 消息

⚠️ 为什么必须防重推：
    轮询是每几十秒一轮，不记状态的话同一个提醒会每轮推一次，
    沙盒群被同一句话刷满。这里用「等级 + 已通知过」的状态机，
    而且**只有回到健康区间（> 5 天）才重置** —— 否则剩余天数在区间内
    抖动（比如网络请求取整）会反复触发。

⚠️ 为什么沙盒群没配置时不报错：
    沙盒群是「设置 → 通用 → 沙盒群」里可选项，留空 = 不启用。
    没配就不推群，但仍要 UI 告警，绝不能因此把整个提醒逻辑崩掉
    （以前就踩过：配置对象缺字段抛异常，把轮询整轮带崩）。
"""
import time

# 阈值（天）。用常量而不是散在代码里的字面量，测试和文档才能对得上。
UI_FROM_DAYS = 5        # 剩余 ≤ 5 天开始 UI 提醒
QQ_UNDER_DAYS = 2       # 剩余 < 2 天（或已过期）开始推沙盒群
RESET_ABOVE_DAYS = 5    # 剩余 > 5 天才重置"已通知"状态

LEVEL_OK = "ok"
LEVEL_UI = "ui"
LEVEL_QQ = "qq"


def level_of(days_left, expired=False) -> str:
    """按剩余天数定等级。days_left 可以是 None（未知）。"""
    if expired:
        return LEVEL_QQ
    if days_left is None:
        return LEVEL_OK
    d = float(days_left)
    if d < QQ_UNDER_DAYS:
        return LEVEL_QQ
    if d <= UI_FROM_DAYS:
        return LEVEL_UI
    return LEVEL_OK


def build_message(days_left, expired=False, expires_str="") -> str:
    """推到沙盒群的那句话。纯文本——这是条通知，不需要按钮。"""
    if expired:
        head = "⚠️ B 站登录态已过期"
        tail = "订阅的投稿/动态现在查不动了，请到面板「🔑 B 站登录」重新登录。"
    else:
        d = int(days_left)
        head = f"⚠️ B 站登录态还剩 {d} 天"
        tail = "快到期了，建议现在就去面板「🔑 B 站登录」续期或重新登录。"
    line = f"{head}\n{tail}"
    if expires_str:
        line += f"\n到期时间：{expires_str}"
    return line


class ExpiryAlert:
    """记住"通知到哪一档了"，避免每轮重复推。"""

    def __init__(self):
        self._sent = LEVEL_OK      # 已经通知到过的等级
        self._last_level = LEVEL_OK

    @property
    def level(self) -> str:
        return self._last_level

    def already_sent(self, level: str) -> bool:
        """这一档是不是已经通知过了。"""
        order = {LEVEL_OK: 0, LEVEL_UI: 1, LEVEL_QQ: 2}
        return order.get(level, 0) <= order.get(self._sent, 0)

    def mark_sent(self, level: str):
        order = {LEVEL_OK: 0, LEVEL_UI: 1, LEVEL_QQ: 2}
        if order.get(level, 0) > order.get(self._sent, 0):
            self._sent = level

    def reset(self):
        self._sent = LEVEL_OK

    def evaluate(self, days_left, expired=False) -> dict:
        """算一次：当前等级、要不要推群、给面板看的文案。

        返回 {"level","need_qq","text","days_left","expired"}
        """
        lv = level_of(days_left, expired)
        self._last_level = lv

        # 回到健康区间才重置——否则区间内抖动会反复触发
        if lv == LEVEL_OK:
            self.reset()
            return {"level": lv, "need_qq": False, "text": "",
                    "days_left": days_left, "expired": bool(expired)}

        need_qq = (lv == LEVEL_QQ) and not self.already_sent(LEVEL_QQ)
        if lv == LEVEL_UI:
            text = f"B 站登录态还剩 {int(days_left)} 天，快到期了"
        else:
            text = ("B 站登录态已过期，订阅查不动了" if expired
                    else f"B 站登录态还剩 {int(days_left)} 天，请尽快续期")
        return {"level": lv, "need_qq": need_qq, "text": text,
                "days_left": days_left, "expired": bool(expired)}


def days_left_of(expires: float, now: float = None) -> tuple:
    """(剩余天数, 是否已过期)。expires 为 0/空时返回 (None, False)。"""
    if not expires:
        return (None, False)
    now = now or time.time()
    left = float(expires) - now
    return (int(left // 86400), left <= 0)
