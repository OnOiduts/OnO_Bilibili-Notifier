"""沙箱错误码 11292 不能被当成「机器人已不在该群」。

真实事故：用户开着「沙箱环境」往**正式群**发消息，官方回
    HTTP 400 {"message":"沙箱环境不能访问此资源","code":11292,
              "err_code":40012004}
而 11292 同时也是官方"机器人已不在该群"的码。旧代码只按码判断，
于是 is_gone_group_error() 命中 → _note_send_error() → _drop_group()
→ **群连同订阅数据一起被删掉**。

这群根本没丢，只是环境不匹配。本文件把这条边界锁死。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import bot  # noqa: E402

FAILS = []


def ck(name, cond):
    if cond:
        print(f"  [OK] {name}")
    else:
        print(f"  [FAIL] {name}")
        FAILS.append(name)


# 用户实际收到的那条报错
REAL_SANDBOX_ERR = (
    'HTTP 400: {"message":"沙箱环境不能访问此资源","code":11292,'
    '"err_code":40012004,"trace_id":"2d808ac29c702f426fbc325d7615013b"}'
)


def t_is_sandbox_error():
    print("[1] is_sandbox_error 认得出沙箱限制")
    ck("用户真实报错认得出", bot.is_sandbox_error(REAL_SANDBOX_ERR))
    ck("err_code 40012004 认得出", bot.is_sandbox_error('"err_code":40012004'))
    ck("只有中文也能认", bot.is_sandbox_error("沙箱环境不能访问此资源"))
    ck("小写 sandbox 认得出", bot.is_sandbox_error("sandbox not allowed"))
    ck("空串不算", not bot.is_sandbox_error(""))
    ck("None 不算", not bot.is_sandbox_error(None))
    ck("普通报错不误判", not bot.is_sandbox_error("推送超时"))
    ck("11293 不算沙箱", not bot.is_sandbox_error(
        '{"code":11293,"message":"鉴权失败，机器人非群成员"}'))


def t_gone_not_sandbox():
    print("[2] 沙箱限制绝不能判成群失效（否则会删群）")
    ck("11292+沙箱message → 不算群失效",
       not bot.is_gone_group_error(REAL_SANDBOX_ERR))
    ck("裸 40012004 → 不算群失效",
       not bot.is_gone_group_error("HTTP 400 err_code 40012004"))
    ck("沙箱 + 11293 同现 → 也不算",
       not bot.is_gone_group_error(
           '{"code":11293,"message":"沙箱环境不能访问此资源"}'))


def t_gone_still_works():
    print("[3] 真被踢出群的情况照旧认定（不能修过头）")
    ck("11293 非群成员 仍认", bot.is_gone_group_error(
        '{"code":11293,"message":"鉴权失败，机器人非群成员"}'))
    ck("11242 仍认", bot.is_gone_group_error('{"code":11242}'))
    ck("11252 仍认", bot.is_gone_group_error('{"code":11252}'))
    ck("11292 纯群失效 仍认", bot.is_gone_group_error(
        '{"code":11292,"message":"机器人已不在该群"}'))
    ck("中文关键词'非群成员' 仍认",
       bot.is_gone_group_error("鉴权失败，机器人非群成员"))
    ck("网络抖动不认", not bot.is_gone_group_error("Connection reset"))
    ck("频控不认", not bot.is_gone_group_error("HTTP 429 too many requests"))


def t_note_send_error_no_drop():
    print("[4] 推送失败是沙箱时，不删群")

    class FakeStore:
        def __init__(self):
            self.removed = []

        def remove_group(self, gid):
            self.removed.append(gid)
            return {"subs": 3, "seasons": 1, "tpl": 2}

        def get_group(self, gid):
            return {"name": "测试群"}

        def refresh(self):
            pass

    class FakeBot:
        def __init__(self):
            self.store = FakeStore()
            self._up_tpl_cache = {}
            self._greeted_groups = set()
            self._role_cache = {}

        _note_send_error = bot.NotifyBot._note_send_error
        _drop_group = bot.NotifyBot._drop_group

    b = FakeBot()
    b._note_send_error("GROUP123", Exception(REAL_SANDBOX_ERR))
    ck("沙箱报错没删掉群", b.store.removed == [])

    b2 = FakeBot()
    b2._note_send_error(
        "GROUP456",
        Exception('{"code":11293,"message":"鉴权失败，机器人非群成员"}'))
    ck("真被踢出群仍然会删", b2.store.removed == ["GROUP456"])


def t_sync_sandbox():
    print("[5] 沙箱开关热切换（不用重启）")

    class FakeQQ:
        def __init__(self):
            self.base = "https://api.sgroup.qq.com"
            self._token = "old"
            self._token_expire = 9999999999.0

    class FakeBot:
        def __init__(self):
            self.qq = FakeQQ()
            self._sandbox_on = False

        sync_sandbox = bot.NotifyBot.sync_sandbox

    # 造一个假 cfgutil 让配置看起来"开"了
    import types
    mod = types.ModuleType("cfgutil")
    mod.load_cfg = lambda: {"is_sandbox": True}
    old = sys.modules.get("cfgutil")
    sys.modules["cfgutil"] = mod
    try:
        b = FakeBot()
        b.sync_sandbox()
        ck("域名切到沙箱", b.qq.base == "https://sandbox.api.sgroup.qq.com")
        ck("token 已作废（换域要重取）", b.qq._token == "")
        ck("_sandbox_on 跟着变", b._sandbox_on is True)
        # 再切回正式
        mod.load_cfg = lambda: {"is_sandbox": False}
        b.sync_sandbox()
        ck("切回正式域名", b.qq.base == "https://api.sgroup.qq.com")
        ck("_sandbox_on 切回", b._sandbox_on is False)
    finally:
        if old is not None:
            sys.modules["cfgutil"] = old
        else:
            sys.modules.pop("cfgutil", None)


def main():
    print("=" * 60)
    print("沙箱错误码 11292 不得误判为群失效（防误删群）")
    print("=" * 60)
    t_is_sandbox_error()
    t_gone_not_sandbox()
    t_gone_still_works()
    t_note_send_error_no_drop()
    t_sync_sandbox()
    print("=" * 60)
    if FAILS:
        print(f"FAILED: {len(FAILS)} 项")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
