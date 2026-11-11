# -*- coding: utf-8 -*-
"""群名已经在本地了，就不要每轮再去问官方。

控制台里同一条「已获取群名：试剂管理室（554484ED3D9C…）」连着出现三次，
这三个请求全是白发的 —— 名字第一次就已经存进库了。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.abspath("/data/workspace/_stub_botpy"))

from bot import NotifyBot  # noqa: E402

PASS = FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f"  {extra}" if extra else ""))


class FakeStore:
    def __init__(self, name=""):
        self._name = name
        self.saved = []

    def get_group(self, gid):
        return {"name": self._name}

    def set_group_name(self, gid, name):
        self.saved.append(name)
        self._name = name


class FakeQQ:
    def __init__(self, name="试剂管理室"):
        self._name = name
        self.calls = 0

    async def get_group_info(self, gid):
        self.calls += 1
        return {"ok": True, "name": self._name}


def make(store, qq):
    b = NotifyBot.__new__(NotifyBot)
    b.store = store
    b.qq = qq
    b._group_name_denied = set()
    return b


async def main():
    import asyncio

    # 1. 本地还没有名字 → 要查一次并保存
    st, qq = FakeStore(name=""), FakeQQ()
    b = make(st, qq)
    r = await NotifyBot._fetch_group_name(b, "G1")
    ck("无名时会去查官方", qq.calls == 1, f"查了 {qq.calls} 次")
    ck("查到后写入本地", st.saved == ["试剂管理室"], f"saved={st.saved}")
    ck("返回值是群名", r == "试剂管理室", f"返回 {r!r}")

    # 2. 已经有名字 → 不再查接口（这是日志里刷三遍的原因）
    st2, qq2 = FakeStore(name="试剂管理室"), FakeQQ()
    b2 = make(st2, qq2)
    r2 = await NotifyBot._fetch_group_name(b2, "G1")
    ck("已有名字时不再打接口", qq2.calls == 0, f"仍查了 {qq2.calls} 次")
    ck("仍返回已有群名", r2 == "试剂管理室", f"返回 {r2!r}")

    # 3. 连着调三次也只查零次
    for _ in range(3):
        await NotifyBot._fetch_group_name(b2, "G1")
    ck("重复调用不再刷请求", qq2.calls == 0, f"累计 {qq2.calls} 次")

    # 4. 用户手动备注的名字也不会被官方覆盖
    st3, qq3 = FakeStore(name="我自己起的名字"), FakeQQ(name="官方名")
    b3 = make(st3, qq3)
    await NotifyBot._fetch_group_name(b3, "G1")
    ck("用户备注不被官方覆盖", st3.saved == [], f"saved={st3.saved}")

    # 5. store 出异常时不能把整条链路打断
    class Boom:
        def get_group(self, gid):
            raise RuntimeError("db down")
        def set_group_name(self, gid, n):
            pass
    st5, qq5 = Boom(), FakeQQ()
    b5 = make(st5, qq5)
    try:
        r5 = await NotifyBot._fetch_group_name(b5, "G1")
        ck("store 异常时不崩", r5 == "试剂管理室", f"返回 {r5!r}")
    except Exception as e:
        ck("store 异常时不崩", False, f"抛了 {type(e).__name__}: {e}")

    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
