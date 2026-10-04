#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""锁定：polymer 动态解析不得因字段是字符串而抛异常。

起因（v1.31.11）：UP 主「伊万酱哒油」的动态解析报
    AttributeError: 'str' object has no attribute 'get'
    bilibili.py:1155  live_play = content.get("live_play_info")
原因：B站 polymer 接口的 live_rcmd.content 在部分动态里是
JSON **字符串**而不是 dict，直接 .get() 就炸。

这个测试锁定两点：
1. 任何结构化字段是 str / 坏 JSON / 空值时，解析不得抛异常；
2. 字段是合法 JSON 字符串时，要**解析出里面的值**，
   不能只是"不崩了但数据丢了"（room_id 必须取到）。
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


# bilibili.py 顶层 import aiohttp，这里造个桩件避免依赖真实库
def _stub_aiohttp():
    if "aiohttp" in sys.modules:
        return
    m = types.ModuleType("aiohttp")

    class _S:
        def __init__(self, *a, **k):
            pass

    for n in ("ClientSession", "ClientTimeout", "TCPConnector", "BasicAuth",
              "CookieJar", "DummyCookieJar", "ClientResponse",
              "ClientWebSocketResponse"):
        setattr(m, n, _S)

    class _CE(Exception):
        pass

    m.ClientError = _CE
    m.WSMsgType = types.SimpleNamespace(TEXT=1, BINARY=2, CLOSE=8, ERROR=9)
    m.hdrs = types.SimpleNamespace(METH_GET="GET")
    sys.modules["aiohttp"] = m


_stub_aiohttp()

import bilibili  # noqa: E402

_parse_polymer = bilibili._parse_polymer
_as_dict = bilibili._as_dict

PASS = FAIL = 0


def _assert(cond, msg="断言失败"):
    assert cond, msg


def check(name, fn):
    """跑一个断言，计入统计。"""
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print("  ✅ %s" % name)
    except AssertionError as e:
        FAIL += 1
        print("  ❌ %s -> %s" % (name, e))
    except Exception as e:
        FAIL += 1
        print("  ❌ %s -> %s: %s" % (name, type(e).__name__, e))


def _item(major=None, **over):
    it = {
        "id_str": "900000000000000001",
        "basic": {"comment_type": 1, "rid_str": "1", "comment_id_str": "1"},
        "modules": {"module_dynamic": {"major": major or {},
                                       "desc": {"text": "正文"}}},
    }
    it.update(over)
    return it


def _no_raise(item, **expect):
    """解析不抛异常，并校验关键字段。"""
    def f():
        r = _parse_polymer(item)
        assert isinstance(r, bilibili.DynamicInfo), "应返回 DynamicInfo"
        assert r.dyn_id, "动态 id 不能为空"
        for k, v in expect.items():
            got = getattr(r, k)
            assert got == v, "%s 期望 %r，实际 %r" % (k, v, got)
    return f


print("\n[1] live_rcmd.content 是字符串（本次 BUG 的正主）")

check("合法 JSON 字符串 → 要解析出 room_id=7788（不能只做到不崩）",
      _no_raise(_item({"type": "live_rcmd",
                       "live_rcmd": {"content": '{"live_play_info":{"room_id":7788}}'}}),
                ref_room_id=7788))

check("非 JSON 字符串 → 不崩，room_id 退化为 0",
      _no_raise(_item({"type": "live_rcmd",
                       "live_rcmd": {"content": "不是JSON"}}),
                ref_room_id=0))

check("空字符串 → 不崩",
      _no_raise(_item({"type": "live_rcmd", "live_rcmd": {"content": ""}}),
                ref_room_id=0))

check("live_rcmd 整个是 JSON 字符串 → 解析出 room_id=123",
      _no_raise(_item({"type": "live_rcmd",
                       "live_rcmd": '{"content":{"live_play_info":{"room_id":123}},'
                                    '"room_id":123}'}),
                ref_room_id=123))

check("正常 dict → 行为不变（room_id=555）",
      _no_raise(_item({"type": "live_rcmd",
                       "live_rcmd": {"content": {"live_play_info": {"room_id": 555}}}}),
                ref_room_id=555))

print("\n[2] 其他同类字段是字符串")

check("opus 是字符串 → 解析出标题",
      _no_raise(_item({"type": "opus",
                       "opus": '{"title":"标题","summary":{"text":"摘要"}}'}),
                title="标题"))

check("archive 是字符串 → 解析出 BV 号",
      _no_raise(_item({"type": "archive",
                       "archive": '{"title":"视频","bvid":"BV1xx"}'}),
                ref_bvid="BV1xx"))

check("draw.items 混入字符串 → 跳过脏项、保留好项",
      _no_raise(_item({"type": "draw",
                       "draw": {"items": ["oops", {"src": "a.jpg"}]}}),
                images=["a.jpg"]))

check("major 本身是字符串 → 不崩",
      _no_raise(_item('{"type":"opus"}')))

print("\n[3] 上层容器是字符串")

check("module_dynamic 是字符串 → 不崩", _no_raise({
    "id_str": "1",
    "basic": {"comment_type": 1, "rid_str": "1", "comment_id_str": "1"},
    "modules": {"module_dynamic": '{"major":{}}'},
}))

check("basic 是字符串 → 不崩", _no_raise({
    "id_str": "1",
    "basic": '{"comment_type":1}',
    "modules": {"module_dynamic": {}},
}))

print("\n[4] _as_dict 本身")

check("dict 原样返回", lambda: _assert(_as_dict({"a": 1}) == {"a": 1}))
check("JSON 字符串 → dict", lambda: _assert(_as_dict('{"a":1}') == {"a": 1}))
check("非 JSON 字符串 → {}", lambda: _assert(_as_dict("abc") == {}))
check("JSON 数组字符串 → {}（不是 dict）", lambda: _assert(_as_dict("[1,2]") == {}))
check("None → {}", lambda: _assert(_as_dict(None) == {}))
check("int → {}", lambda: _assert(_as_dict(3) == {}))
check("list → {}", lambda: _assert(_as_dict([]) == {}))


print("\n" + "=" * 46)
print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
print("=" * 46)
sys.exit(1 if FAIL else 0)
