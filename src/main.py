#!/usr/bin/env python3
# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""OnOBN 统一入口 —— 源码版与 exe 版共用。

为什么需要这个文件
------------------
源码版是三个独立脚本在跑：

    start.py  ──拉起──>  webui.py   （网页面板）
              └─拉起──>  bot.py     （机器人）

start.py 靠 `[解释器, "webui.py", ...]` 这种方式起子进程。
一旦 PyInstaller 打包成 exe，这两行会同时坏掉：

  1. `webui.py` / `bot.py` 不再以 .py 文件存在（被编进 exe），路径根本找不到；
  2. `sys.executable` 变成了 exe 自己，`[OnOBN.exe, "webui.py"]`
     等于让 exe 又跑一遍 start 逻辑 —— 界面套界面，直接乱套。

所以这里提供**子命令模式**：

    OnOBN.exe                       → 正常运行（等价于 start.py）
    OnOBN.exe --child webui ...     → 只跑面板
    OnOBN.exe --child bot   ...     → 只跑机器人

一个 exe 身兼三职，不用打三个各带一份解释器的大包。

源码版用不用它？
    用。直接 `python main.py` 效果和 `python start.py` 完全一样，
    这样两条路共用同一套代码，不会出现"exe 版和源码版行为不一样"。
"""
import os
import sys

# ⚠️ PyInstaller 打包后，__file__ 指向解压出来的临时目录（_MEIPASS），
#    源码不在这儿。别在这里用 os.path.join(BASE, "webui.py") 去找脚本。
FROZEN = bool(getattr(sys, "frozen", False))

# 允许作为子进程拉起的角色 → 对应的模块与 main 函数
_CHILDREN = {
    "webui": ("webui", "main"),
    "bot": ("bot", "main"),
}


def _run_child(name: str, rest: list):
    """以某个子角色运行：把参数伪装成该脚本自己的 argv，再调它的 main()。"""
    import importlib

    mod_name, fn_name = _CHILDREN[name]
    try:
        mod = importlib.import_module(mod_name)
    except Exception:
        # 缺依赖时给一句人话，比一堆 traceback 有用
        sys.stderr.write(f"[X] 无法载入 {mod_name} 模块（依赖缺失？）\n")
        raise
    # argparse 读的是 sys.argv，所以要把子命令那几个参数摘掉再传进去，
    # 否则 webui 会把 "--child" 当成自己的参数而报错退出。
    sys.argv = [f"{mod_name}.py"] + list(rest)
    getattr(mod, fn_name)()


def main():
    argv = sys.argv[1:]

    # --child <角色> [其余参数]
    if len(argv) >= 2 and argv[0] == "--child":
        name = argv[1]
        if name not in _CHILDREN:
            sys.stderr.write(
                f"[X] 未知子角色：{name}（可用：{'、'.join(_CHILDREN)}）\n")
            return 2
        _run_child(name, argv[2:])
        return 0

    # 普通模式：交给 start.py 的一键启动流程
    import start
    start.main()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
