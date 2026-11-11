# -*- coding: utf-8 -*-
"""群显示名：前端、运行日志、审计日志统一口径。

group_openid 是内部键（OpenAPI 调用、数据库主键、DOM 的 data-gid 都用它），
但**不给人看**：一串十六进制对用户没有意义。没有群名时也不回退到 openid，
而是给一个按登记顺序稳定编号的「未命名群 N」，多个未命名群仍能区分。

后端 /api/state 给每个群带上 label，机器人侧用 _gname() 取同一个函数，
两边永远一致（都基于 store 里同一份群名单排序）。
"""


def group_label(gid, name, all_gids=()):
    """有名字用名字；没名字给稳定编号，绝不吐 openid。"""
    name = (name or "").strip()
    if name:
        return name
    try:
        ordered = sorted(str(g) for g in all_gids)
        n = ordered.index(str(gid)) + 1
    except (ValueError, TypeError):
        n = 1
    return "未命名群 %d" % n


def labels_of(store):
    """{gid: 显示名}，供 /api/state、日志翻译、审计日志共用。"""
    data = store.data.get("groups") or {}
    all_gids = list(data.keys())
    out = {}
    for gid, info in data.items():
        info = info if isinstance(info, dict) else {}
        out[str(gid)] = group_label(gid, info.get("name"), all_gids)
    return out
