# -*- coding: utf-8 -*-
"""合集订阅：作者必须以「链接里解析出来的」为准，不能记成手填的那个。

用户实测反馈（图三那个界面）：
    UP 主 UID 填一个人的，合集链接填另一个人的 —— 也能订阅成功，
    于是库里出现「写着 A 的名字、装着 B 的合集」这种二合一怪物，
    界面上看不出错，但合集根本不归这位 UP 主。

根因是作者 uid 有**两处来源**：
    · 手填的 uid 输入框
    · 合集链接里解析出来的 mid
而旧代码写的是 `uid = int(uid or p_uid or 0)` —— 手填优先。
只要手填了，链接里那个真作者就被丢弃了。

现在反过来：**链接优先**，手填的只当"只给了纯数字 ID"时的补充。
同时新增 /api/season/lookup：先查一眼再确认订阅，
查的时候就把作者名一起回给前端，让用户看到"这个合集归谁"再决定。
"""
import os
import sys

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE)

FAILS = []


def chk(name, cond, extra=""):
    if cond:
        print(f"  [ok] {name}")
    else:
        print(f"  [FAIL] {name} {extra}")
        FAILS.append(name)


UID_A = 401315430          # 手填的那个（星瞳）
UID_B = 320070259          # 合集链接里真正的作者（波萝Buono）
SID_B = 3175975
LINK_B = f"https://space.bilibili.com/{UID_B}/lists/{SID_B}?type=season"
GID = "6FBBD9B5AAAAAAAAAAAAAAAAAAAAAAAA"


class FakeStore:
    def __init__(self, ups=None):
        self.seasons = []
        self.ups = ups or {}

    def get_up(self, uid):
        return self.ups.get(int(uid))

    def add_season(self, gid, uid, season_id, title, uname="", cover=""):
        self.seasons.append({
            "gid": gid, "uid": int(uid), "season_id": int(season_id),
            "title": title, "uname": uname, "cover": cover,
        })


def install(monkey_probe=True):
    """把网络和不相关依赖换成桩，返回 (webui, store)。"""
    import webui

    store = FakeStore(ups={UID_B: {"uname": "波萝Buono"}})

    async def fake_probe(uid, season_id):
        """⚠️ 故意**只看 season_id，不看 uid** —— 这是 B 站接口的真实行为。

        /x/polymer/web-space/seasons_archives_list 里 season_id 才是主键，
        mid 主要用来拼 referer 和签名，**传错也能正常返回合集内容**。
        正因为这样，旧代码（手填 uid 优先）才不会报错：
        它拿着 A 的 uid 去查 B 的合集，接口照样给内容，
        于是「写着 A 的名字有着 B 的合集」被成功写进库 —— 正是用户撞到的
        那个二合一。桩必须复现这一点，否则测试验的是假场景。
        """
        if int(season_id) == SID_B:
            return {"title": "合集·波萝的录播", "total": 12, "cover": "cv.jpg"}
        raise Exception("模拟：这个合集 ID 取不到内容")

    orig_probe = webui._probe_season
    orig_store = webui.get_store
    webui._probe_season = fake_probe
    webui.get_store = lambda: store
    return webui, store, orig_probe, orig_store


def restore(webui, orig_probe, orig_store):
    webui._probe_season = orig_probe
    webui.get_store = orig_store


print("=== ① 二合一：uid 填 A、链接是 B 的，库里必须记 B ===")
import webui as _w  # noqa: E402


def post_json(fn, payload):
    with _w.app.test_request_context(json=payload):
        return fn().get_json()


webui, store, op, os_ = install()
try:
    # 先查一眼：作者必须是链接里那个
    d = post_json(webui.api_season_lookup,
                  {"ref": LINK_B, "uid": str(UID_A)})
    chk("查询认出作者是链接里的那位", d.get("uid") == UID_B, f"实得 {d.get('uid')}")
    chk("查询把作者名带回来", d.get("uname") == "波萝Buono", f"实得 {d.get('uname')!r}")
    chk("查询标出作者被纠正过", d.get("owner_fixed") is True, f"实得 {d.get('owner_fixed')}")
    chk("查询认出合集 ID", d.get("season_id") == SID_B, f"实得 {d.get('season_id')}")

    # 再订阅：写库时用的也必须是链接里那位
    d2 = post_json(webui.api_season_add,
                   {"gid": GID, "uid": str(UID_A), "ref": LINK_B})
    chk("订阅成功", d2.get("ok") is True, f"实得 {d2}")
    chk("库里记的作者是 B（不是手填的 A）",
        store.seasons and store.seasons[0]["uid"] == UID_B,
        f"实得 {[s['uid'] for s in store.seasons]}")
    chk("库里名字也跟着 B",
        store.seasons and store.seasons[0]["uname"] == "波萝Buono",
        f"实得 {[s['uname'] for s in store.seasons]}")
finally:
    restore(webui, op, os_)

print("=== ② 只给纯数字 ID、没作者 → 不能瞎认 ===")
webui, store, op, os_ = install()
try:
    d = post_json(webui.api_season_lookup, {"ref": str(SID_B)})
    chk("纯数字 ID 且无作者时报错", d.get("ok") is False, f"实得 {d}")
    chk("报错里教用户去粘链接", "链接" in str(d.get("msg", "")),
        f"实得 {d.get('msg')!r}")
finally:
    restore(webui, op, os_)

print("=== ③ 纯数字 ID + 上面查过的 UP 主 → 用那位补上 ===")
webui, store, op, os_ = install()
try:
    d = post_json(webui.api_season_lookup,
                  {"ref": str(SID_B), "uid": str(UID_B)})
    chk("补充的作者被采用", d.get("ok") is True and d.get("uid") == UID_B, f"实得 {d}")
    chk("标出这次作者来自补充而非链接", d.get("from_link") is False,
        f"实得 {d.get('from_link')}")
finally:
    restore(webui, op, os_)

print("=== ④ 链接里没作者、也不给 uid → 直接拒绝写库 ===")
webui, store, op, os_ = install()
try:
    d = post_json(webui.api_season_add,
                  {"gid": GID, "ref": f"collectiondetail?sid={SID_B}"})
    chk("认不出作者时不订阅", d.get("ok") is False, f"实得 {d}")
    chk("库里没有被写进东西", not store.seasons, f"实得 {store.seasons}")
finally:
    restore(webui, op, os_)

print()
if FAILS:
    print(f"FAIL {len(FAILS)} 项：" + "、".join(FAILS))
    sys.exit(1)
print("ALL PASS")
