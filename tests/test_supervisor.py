# -*- coding: utf-8 -*-
"""启动器（bat + start.py）与机器人守护行为的锁定测试。

这些是真实踩过的坑，每条 FAIL 都对应一个用户可见的故障：

  - 退出码 1 时面板被 cleanup 杀掉 → 浏览器 TypeErrror: Failed to fetch，
    用户连排查的入口都没有
  - bot.py 非登录失败（如运行时崩溃）也被当成凭据问题锁死，
    机器人再也不会自动起来
  - _adapt_interval / sleep 在 try 之外，一抛异常整个轮询协程就结束，
    表现是「机器人在线但不提醒」
  - 未捕获异常不留痕迹，用户只看到「退出码 1」
"""
import io
import os
import re
import sys

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE)

FAIL = []


def ck(name, cond):
    print(("  [PASS] " if cond else "  [FAIL] ") + name)
    if not cond:
        FAIL.append(name)


def _read(name):
    """源码在 src/，但 bat 这类随包文件在程序根目录 —— 两边都找一遍。"""
    import os as _o
    p = _o.path.join(BASE, name)
    if not _o.path.exists(p):
        p = _o.path.join(_o.path.dirname(BASE), name)
    return io.open(p, encoding="utf-8").read()


print("== 启动机器人.bat：退出码与面板存留 ==")
bat = _read("启动机器人.bat")
# 只有干净退出（0）才允许清理；任何非 0 都必须保留面板
ck("RC=0 才走清理分支", 'if "%RC%"=="0" goto :DO_CLEANUP' in bat)
ck("其余退出码统一保留面板", re.search(r'goto\s+:KEEP_PANEL', bat) is not None)
# 老的「只保留 2/3」写法必须彻底消失，否则退出码 1 又会杀面板
ck("不再只对 2/3 保留面板",
   'if "%RC%"=="2" goto :KEEP_PANEL' not in bat
   and 'if "%RC%"=="3" goto :KEEP_PANEL' not in bat)

print("== start.py：重启策略 ==")
st = _read("start.py")
# 只有退出码 3（登录失败 = 凭据问题）才锁住凭据等用户改
ck("退出码 3 才锁定凭据",
   re.search(r"if code == 3:\s*\n\s*failed_fp = last_fp", st) is not None)
ck("其他退出码允许自动重启",
   re.search(r"else:\s*\n\s*failed_fp = None", st) is not None)
# 崩溃重启要有退避，否则每 2 秒重启一次刷屏且暴力访问
ck("重启有退避（不是立刻重试）", "_wait = min(60, 5 * _crashes)" in st)
ck("崩溃计数用局部变量递增", "_crashes += 1" in st)
# 启动脚本自身崩溃时不能杀面板
ck("脚本自身异常时保留面板",
   "except Exception:" in st and "_keep_panel = True" in st)
ck("保留面板时跳过 kill", "if _keep_panel:" in st)

print("== bot.py：轮询循环不能被一次异常打死 ==")
bot = _read("bot.py")
# 定位 _poll_loop 的函数体
m = re.search(r"async def _poll_loop\(self\):(.*?)\n    def _adapt_interval",
              bot, re.S)
body = m.group(1) if m else ""
ck("_adapt_interval 在 try 保护内",
   re.search(r"try:\s*\n\s*self\._adapt_interval\(\)", body) is not None)
ck("休眠也在 try 保护内",
   re.search(r"try:\s*\n\s*await asyncio\.sleep\(self\.interval\)", body)
   is not None)
# CancelledError 必须继续抛，否则正常关闭会被吞掉
ck("CancelledError 仍然向上传播",
   body.count("except asyncio.CancelledError:\n                raise") >= 2)

print("== bot.py：异常必须留痕 ==")
ck("安装了 sys.excepthook", "_install_excepthook()" in bot
   and "sys.excepthook = _hook" in bot)
ck("hook 会把 traceback 写进日志",
   "traceback.format_exception(exc_type, exc, tb)" in bot)
ck("asyncio task 异常有 handler",
   "set_exception_handler(self._on_asyncio_error)" in bot)

print()
if FAIL:
    print("存在失败：")
    for f in FAIL:
        print("  - " + f)
else:
    print("全部通过 OK")
sys.exit(1 if FAIL else 0)
