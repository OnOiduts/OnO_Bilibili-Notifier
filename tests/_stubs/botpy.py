# 仅测试期使用的 botpy 替身。
# botpy（qq-botpy）在本沙盒里装不上，而 bot.py 在模块级就 import 它，
# 于是一批测试根本导入不了。这只补 bot.py 真正用到的部分，不参与打包。
import sys
import types


class Client:
    def __init__(self, *a, **k):
        pass


class Intents:
    def __init__(self, *a, **k):
        pass


_logging = types.ModuleType("botpy.logging")


class _Log:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def critical(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


_logging.get_logger = lambda *_a, **_k: _Log()
sys.modules.setdefault("botpy.logging", _logging)
