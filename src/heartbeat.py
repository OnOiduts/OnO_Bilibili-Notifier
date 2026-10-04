# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""机器人心跳：让网页面板能准确判断机器人是不是真的还活着。

为什么不用 pgrep / psutil：
    Windows 没有 pgrep 命令，调用必然失败，会一直误判成「机器人未运行」
    ——哪怕机器人其实好好地在线。所以改成 bot.py 自己写心跳文件，
    webui.py 读文件看时间戳，跨平台且能附带更多运行信息。

用法：
    bot.py   启动时 heartbeat.mark(logged_in=True)，运行中定期 heartbeat.touch()
    webui.py heartbeat.status() → 是否在心跳有效期内 + 运行详情
"""
import json
import os
import time

import paths as _paths
BASE = os.path.dirname(os.path.abspath(__file__))
PATH = _paths.run_file(".bot_heartbeat")
# 超过这个秒数没更新就认为机器人停了（心跳周期 10 秒，留足余量）
FRESH_SEC = 30

# 「最近动态」最多留几条（概况页卡片用）
RECENT_MAX = 12
# 同一次更新推给多个群：每个群各调一次 push_event，
# 在这个秒数内算作**同一件事**，累加群数而不是插成好几条。
# 推送之间有 0.6s 间隔，3~4 个群也就 2 秒左右，90 秒足够宽。
RECENT_MERGE_SEC = 90

_state = {
    "ts": 0.0,
    "started_at": 0.0,
    "logged_in": False,
    "pid": 0,
    "groups": 0,
    "ups": 0,
    "polls": 0,
    "pushes": 0,
    "errors": 0,
    "last_push_at": 0.0,
    "last_push": "",
    "last_error": "",
    # 当日累计（面板「运行状态」显示的是"已推送今日"，
    # 跨天要自动归零，否则数字一直涨、看不出今天到底推了多少）
    "day": "",
    "pushes_today": 0,
    "errors_today": 0,
}


def _rollover_day():
    """跨天就把当日计数归零。"""
    today = time.strftime("%Y-%m-%d")
    if _state.get("day") != today:
        _state["day"] = today
        _state["pushes_today"] = 0
        _state["errors_today"] = 0


def _atomic_write(data: dict):
    """直接写盘并 fsync。

    心跳文件很小、丢失也不影响正确性（读不到就当作机器人没在跑），
    所以不走 tmp+rename，避免个别文件系统上 rename 的可见性延迟。
    """
    directory = os.path.dirname(PATH)
    os.makedirs(directory, exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())


def touch(**kw):
    """更新心跳。kw 里的字段会合并进状态。"""
    _state.update(kw)
    _state["ts"] = time.time()
    if not _state.get("started_at"):
        _state["started_at"] = _state["ts"]
    _state["pid"] = os.getpid()
    try:
        _atomic_write(dict(_state))
    except OSError:
        pass


def mark(**kw):
    touch(**kw)          # 语义别名，读起来更清楚


def bump(field: str, n: int = 1):
    _rollover_day()
    _state[field] = int(_state.get(field) or 0) + n
    # 当日累计只在对应字段上加，跨天由 _rollover_day 归零
    if field in ("pushes", "errors"):
        key = field + "_today"
        _state[key] = int(_state.get(key) or 0) + n
    touch()


def push_event(kind: str, uid, name: str, groups: int = 1):
    """记一条「最近动态」，给面板概况页用。

    ⚠️ 为什么单独记一份，而不是拿 notes 拼：
    notes 是**检测结果**——每一轮都会写，没推送也会更新，
    里面还是 dyn_id、BV 号这类原始值。拿它拼"最近动态"，
    结果就是「没推也显示、时间也不对、内容是一串号」。
    这里只在**确实发出去之后**才写，并记下推给了几个群。
    """
    try:
        d = read() or {}
        lst = [x for x in (d.get("recent") or []) if isinstance(x, dict)]
        now = time.time()
        head = lst[0] if lst else None
        if (head and str(head.get("kind")) == str(kind)
                and str(head.get("uid")) == str(uid)
                and (now - float(head.get("ts") or 0)) <= RECENT_MERGE_SEC):
            head["groups"] = int(head.get("groups") or 0) + int(groups or 0)
            head["ts"] = now
            if name:
                head["name"] = str(name)[:40]
        else:
            lst.insert(0, {
                "kind": str(kind),
                "uid": str(uid),
                "name": str(name or "")[:40],
                "groups": int(groups or 0),
                "ts": now,
            })
            del lst[RECENT_MAX:]
        touch(recent=lst)
    except Exception:
        pass


def read() -> dict:
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def status() -> dict:
    """返回 {alive, age, ...}。alive 只在心跳新鲜且已登录时为 True。"""
    d = read()
    if not d or not d.get("ts"):
        return {"alive": False, "age": None, "seen": False,
                "detail": "还没收到机器人心跳"}
    age = time.time() - float(d["ts"])
    logged = bool(d.get("logged_in"))
    fresh = age <= FRESH_SEC
    alive = fresh and logged

    if not fresh:
        detail = f"心跳已中断 {_fmt(age)}（机器人可能已停止）"
    elif not logged:
        detail = "机器人进程在，但还没完成登录"
    else:
        detail = f"运行中 · 最后心跳 {int(age)} 秒前"

    out = dict(d)
    out.update({"alive": alive, "age": int(age), "seen": True,
                "detail": detail, "logged_in": logged})
    return out


def _fmt(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec} 秒"
    if sec < 3600:
        return f"{sec // 60} 分钟"
    if sec < 86400:
        return f"{sec // 3600} 小时"
    return f"{sec // 86400} 天"


def clear():
    try:
        os.remove(PATH)
    except OSError:
        pass
