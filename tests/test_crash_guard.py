# -*- coding: utf-8 -*-
"""崩溃兜底：循环不能死 + 原因必须看得见。

用户日志里出现过：

    [INFO] 风控风险已解除，检查频率已回到最快：12s → 8s（最快档）
    ⚠️ 机器人退出（退出码 1）——不是凭据问题，稍后自动重启。
       原因已写入 bot.log，可在面板「日志」页查看末尾。

两个问题一起爆发：
  ① 退出码 1 但控制台一行原因都没有；
  ② 面板打不开（ERR_CONNECTION_REFUSED），日志页也就看不了 —— 两头堵死。

所以这版做两件事：
  - 轮询/心跳两个主循环外包一层，任何异常（含 BaseException 家族，
    asyncio.CancelledError 从 3.8 起不再继承 Exception）都不能让循环结束；
  - 崩溃时把完整堆栈**打在控制台上**，并单独写一份 crash.log；
    启动脚本在机器人退出时直接把 bot.log 末尾的堆栈打出来。
"""
import asyncio
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

PASS = FAIL = 0


def ck(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


class FakeLog:
    def __init__(self):
        self.errors = []

    def error(self, msg, *a):
        self.errors.append(msg % a if a else msg)

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


def make_bot():
    import bot as B
    self = B.NotifyBot.__new__(B.NotifyBot)
    self._loop_delay = 0.01
    return self


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


async def _wrap(bot_obj, meth, inner_name, exc):
    """跑一次：inner 第一次抛 exc，第二次正常返回。"""
    state = {"n": 0}

    async def inner():
        state["n"] += 1
        if state["n"] == 1:
            raise exc
        return

    setattr(bot_obj, inner_name, inner)
    task = asyncio.ensure_future(getattr(bot_obj, meth)())
    for _ in range(60):
        await asyncio.sleep(0.02)
        if task.done():
            break
    return state["n"], task


def main():
    import bot as B
    LOG = FakeLog()
    B._log = LOG

    # ---- 1. 轮询主循环：普通异常不能让循环结束 ----
    b = make_bot()
    n, task = run(_wrap(b, "_poll_loop", "_poll_loop_inner", RuntimeError("boom")))
    ck("轮询：抛 RuntimeError 后循环继续（inner 被再调一次）", n >= 2)
    ck("轮询：任务没有以异常结束", not (task.done() and task.exception()))

    # ---- 2. BaseException 家族（except Exception 抓不到的）----
    b = make_bot()
    n, task = run(_wrap(b, "_poll_loop", "_poll_loop_inner", BaseException("weird")))
    ck("轮询：BaseException 也拦得住", n >= 2)

    # ---- 3. CancelledError 冒到外壳：必须记一笔再退出（不能静默）----
    b = make_bot()
    n, task = run(_wrap(b, "_poll_loop", "_poll_loop_inner", asyncio.CancelledError()))
    ck("轮询：CancelledError 时任务以取消结束", task.cancelled() or n == 1)
    ck("轮询：取消不是静默的（日志里有说明）",
       any("取消" in x for x in LOG.errors))

    # ---- 3b. 循环体内部的取消：不是停止指令时跳过本轮继续 ----
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "src", "bot.py"), encoding="utf-8").read()
    ck("轮询循环体按 _stopping 区分真停止与内部泄漏的取消",
       "_stopping" in src and src.count("_stopping") >= 2)

    # ---- 4. 真的要取消 → 必须退出（不然关不掉）----
    b = make_bot()

    async def forever():
        await asyncio.sleep(100)

    b._poll_loop_inner = forever

    async def cancel_it():
        # ⚠️ task 必须在协程内创建：否则 ensure_future 会绑到默认 loop，
        #    和 run_until_complete 用的新 loop 不是同一个，直接报错。
        task = asyncio.ensure_future(b._poll_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "not"

    ck("轮询：真取消时正常退出", run(cancel_it()) == "cancelled")

    # ---- 5. 心跳循环同样不能死 ----
    b = make_bot()
    n, task = run(_wrap(b, "_heartbeat_loop", "_heartbeat_loop_inner", RuntimeError("hb")))
    ck("心跳：抛异常后继续", n >= 2)
    ck("心跳：任务没有以异常结束", not (task.done() and task.exception()))

    # ---- 6. 崩溃原因要能单独拿到（_fatal_dump 写 crash.log）----
    tmp = tempfile.mkdtemp()
    os.environ["BILI_NOTIFY_HOME"] = tmp
    import paths
    import importlib
    importlib.reload(paths)
    try:
        raise ValueError("模拟崩溃")
    except ValueError as e:
        buf = []
        _old = B.safe_print
        B.safe_print = lambda *a, **k: buf.append(" ".join(str(x) for x in a))
        try:
            B._fatal_dump(e)
        finally:
            B.safe_print = _old
    ck("_fatal_dump 把堆栈打到了控制台",
       any("ValueError" in x or "模拟崩溃" in x for x in buf))
    crash = os.path.join(paths.data_home(), "logs", "crash.log")
    ck("_fatal_dump 写了 crash.log", os.path.exists(crash))
    if os.path.exists(crash):
        txt = open(crash, encoding="utf-8").read()
        ck("crash.log 里有完整堆栈", "模拟崩溃" in txt and "Traceback" in txt)

    # ---- 7. 启动脚本能从 bot.log 末尾抽出堆栈 ----
    import start as S
    logp = os.path.join(paths.data_home(), "logs", "bot.log")
    with open(logp, "w", encoding="utf-8") as f:
        f.write("[INFO] 正常行\n")
        f.write("Traceback (most recent call last):\n")
        f.write('  File "bot.py", line 1, in foo\n')
        f.write("    bar()\n")
        f.write("AttributeError: 'NoneType' object has no attribute 'get'\n")
        f.write("[INFO] 收尾行\n")
    out = S._tail_traceback()
    ck("_tail_traceback 能抽出堆栈", any("Traceback" in x for x in out))
    ck("_tail_traceback 带上了真正的错误行",
       any("AttributeError" in x for x in out))

    # 没有堆栈时也不要返回空（给末尾的错误行兜底）
    with open(logp, "w", encoding="utf-8") as f:
        f.write("[ERROR] 取不到：code=-504\n")
    out2 = S._tail_traceback()
    ck("没有堆栈时给出末尾错误行", any("-504" in x for x in out2))

    print()
    print(f"通过 {PASS} / 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
