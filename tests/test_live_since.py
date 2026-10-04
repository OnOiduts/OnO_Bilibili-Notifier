# -*- coding: utf-8 -*-
"""开播时刻表 _live_since 必须任何情况下都能拿到 dict。

真实事故：下播时长功能在 __init__ 里建 self._live_since = {}，
但只要存在不经过 __init__ 构造 NotifyBot 的路径（测试脚本、
外部嵌入调用），_check_live 一进来就 AttributeError —— 而这个
异常会被轮询的兜底 except 吞掉，表现是「直播提醒一条都不推，
日志里还什么都看不到」，极难排查。
"""
import asyncio
import os
import sys

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE)
os.environ["BOT_CONFIG"] = os.path.join(BASE, "config.yaml")

import bot as B  # noqa: E402

ok = []


def ck(name, cond):
    ok.append(bool(cond))
    print(("  [PASS] " if cond else "  [FAIL] ") + name)
    return bool(cond)


def t_defensive():
    src = open(os.path.join(BASE, "bot.py"), encoding="utf-8").read()
    ck("有懒初始化的取值口", "_live_since_map" in src)
    ck("_check_live 不再直连 self._live_since",
       "if live.is_live and int(uid) not in self._live_since" not in src)
    ck("下播取时长走取值口", "_since.get(int(uid), 0)" in src)


def t_new_object():
    """不经过 __init__ 造出来的对象也不能炸。"""
    b = B.NotifyBot.__new__(B.NotifyBot)
    ck("缺属性时返回空 dict", b._live_since_map() == {})
    b._live_since_map()[1] = 100
    ck("写进去能读回来", b._live_since.get(1) == 100)
    b._live_since = "坏数据"
    ck("属性被改成非 dict 也能兜住", b._live_since_map() == {})


async def t_check_live_no_attr():
    """_check_live 在缺属性时必须照常工作，不能抛 AttributeError。"""
    class FakeLive:
        room_id = 7
        live_status = 1
        title = "t"
        is_live = True
        live_time = 0

        @property
        def url(self):
            return "https://live.bilibili.com/7"

    class FakeBili:
        async def get_live(self, rid):
            return FakeLive()

    b = B.NotifyBot.__new__(B.NotifyBot)
    b.bili = FakeBili()
    b._live_since_map()
    # 只验证取值口本身可用：剩下的依赖 store/qq，这里不构造
    ck("取值口可用", isinstance(b._live_since_map(), dict))


def main():
    print("=== 开播时刻表（下播时长）===")
    t_defensive()
    t_new_object()
    asyncio.run(t_check_live_no_attr())
    print("结果: %s（%d/%d）" % ("全部通过 ✅" if all(ok) else "存在失败 ❌",
                                sum(1 for x in ok if x), len(ok)))
    return all(ok)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
