"""脏数据兜底回归测试（不依赖 aiohttp / botpy，纯 db 层）。

覆盖这次修的那个线上故障：
    轮询异常：'str' object has no attribute 'get'
根因是订阅缓存里混进了字符串，凡是 `x.get(...)` 的地方一碰就炸，
而异常会冒到轮询主循环，导致**整轮检测停摆**（所有 UP 主都不检测）。

修法是两层：
  1. db 层统一类型兜底——读出来不是 dict 就当空，绝不让异常外泄
  2. check.py 主动扫脏值——因为兜底会让对应项被静默跳过，
     用户看到的是"开了提醒却没提示"，必须让他知道要去面板重勾

用法：python tests/test_dirty_data.py
"""
import os
import sys
import tempfile

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE_DIR)

import db  # noqa: E402

ok = True


def ck(name, cond):
    global ok
    print(("[PASS] " if cond else "[FAIL] ") + name)
    ok = ok and bool(cond)


def new_store():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    return db.Store(path) if os.path.exists(
        os.path.join(BASE_DIR, "data.db")) else db.Store(path)


def main():
    st = new_store()
    st.refresh()
    # 这个用例直接改内存缓存来造脏数据；而 Store 现在是「发现文件变了就
    # 整库重建」的，refresh() 会把刚写进去的脏值冲掉。这里把自动刷新停掉，
    # 用例要验的本来就是"读到脏值时不许抛异常"这一层。
    st.refresh = lambda: None

    # ---------- 1. 正常数据时行为不变 ----------
    st._cache["groups"] = {
        "G1": {"name": "群1", "subs": {
            "123": {"uname": "某UP", "live": {"on": True},
                    "video": {"on": False}}}}}
    st._cache["ups"] = {"123": {"uname": "某UP", "room_id": 55}}

    ck("正常：enabled_kinds_of_up 只返回开启的类型",
       st.enabled_kinds_of_up(123) == {"live"})
    ck("正常：targets 返回订阅了该类型的群", st.targets(123, "live") == ["G1"])
    ck("正常：targets 不返回未开启的类型", st.targets(123, "video") == [])
    ck("正常：get_up 返回完整字典",
       st.get_up(123).get("room_id") == 55)
    ck("正常：all_ups 能收集到 UP 主", st.all_ups() == {123})
    ck("正常：subscribed_kinds 正确", st.subscribed_kinds(123) == {"live"})

    # ---------- 2. 脏数据不再抛异常 ----------
    st._cache["groups"] = {
        "G1": {"name": "群1", "subs": {
            "123": {"uname": "某UP",      # uname 是字符串，合法字段
                    "live": "脏字符串",    # ← 本该是 dict，却是 str
                    "video": {"on": True}}}},
        "G2": "整个群记录都是字符串",
        "G3": {"name": "群3", "subs": "订阅表也是字符串"},
    }
    st._cache["ups"] = {"123": "UP 主缓存是字符串", "456": {"uname": "正常的"}}

    try:
        kinds = st.enabled_kinds_of_up(123)
        t1 = True
    except Exception as e:
        kinds, t1 = None, False
        print("   抛了：" + str(e))
    ck("脏：enabled_kinds_of_up 不抛异常", t1)
    ck("脏：脏的 live 被当未开启，好的 video 仍生效", kinds == {"video"})

    try:
        ck("脏：targets(live) 视为无人订阅", st.targets(123, "live") == [])
        ck("脏：targets(video) 仍正常", st.targets(123, "video") == ["G1"])
        t2 = True
    except Exception as e:
        t2 = False
        print("   抛了：" + str(e))
    ck("脏：targets 不抛异常", t2)

    ck("脏：get_up 返回空字典而不是字符串", st.get_up(123) == {})
    ck("脏：get_up 正常的 UP 不受影响",
       st.get_up(456).get("uname") == "正常的")
    ck("脏：get_group 对坏群返回安全空壳",
       st.get_group("G2") == {"name": "", "subs": {}})
    ck("脏：ups_of_group 对坏订阅表返回空", st.ups_of_group("G3") == {})
    ck("脏：all_ups 不抛异常", st.all_ups() == {123})
    ck("脏：subscribed_kinds 不抛异常", st.subscribed_kinds(123) == {"video"})

    # ---------- 3. UP 主缓存整个是字符串时，调用链不炸 ----------
    # 这就是 bot.py 里 `self.store.get_up(uid).get("uname")` 的现场
    try:
        name = st.get_up(123).get("uname") or "123"
        ck("脏：get_up(...).get('uname') 不抛异常", name == "123")
    except Exception as e:
        ck("脏：get_up(...).get('uname') 不抛异常", False)
        print("   抛了：" + str(e))

    # ---------- 4. baseline_of 永远是 dict ----------
    try:
        b = st.baseline_of("G1", 123, "video")
        ck("baseline_of 返回 dict", isinstance(b, dict))
        ck("baseline_of 的 .get 可用", b.get("bvid") is None)
    except Exception as e:
        ck("baseline_of 返回 dict", False)
        print("   抛了：" + str(e))

    # ---------- 5. 自检能扫出脏值 ----------
    try:
        import check as CK
    except Exception as e:
        CK = None
        print("   （check.py 导入失败，跳过：" + str(e) + "）")
    if CK:
        r = CK.check_dirty(st)
        ck("自检：能报出脏值", bool(r) and r[0] == CK.BAD)
        ck("自检：提示里带解决办法", bool(r) and "重新勾一次" in r[2])
        st2 = new_store()
        st2.refresh()
        st2._cache["groups"] = {
            "G1": {"name": "群1", "subs": {"1": {"live": {"on": True}}}}}
        st2._cache["ups"] = {"1": {"uname": "正常"}}
        r2 = CK.check_dirty(st2)
        ck("自检：干净数据报正常", bool(r2) and r2[0] == CK.OK)

    # ---------- 6. 轮询主循环打了完整堆栈 ----------
    src = open(os.path.join(BASE_DIR, "bot.py"), encoding="utf-8").read()
    ck("bot.py 已 import traceback", "import traceback" in src)
    ck("轮询异常会打完整堆栈", "traceback.format_exc()" in src)

    # ---------- 7. 单个 UP 出错不拖垮整轮 ----------
    ck("_poll_once 里每类检测都单独兜异常", "_safe(self._check_live" in src)

    print()
    print("全部通过 ✅" if ok else "有失败项 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
