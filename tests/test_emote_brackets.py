# -*- coding: utf-8 -*-
"""推送正文里的表情：要套方括号跟正文区分开，装扮表情不显示。

用户反馈原文：
  「动态和置顶评论消息推送目前是会把表情包和装扮读取出来，
    装扮可以不读，表情包要划分出来类似"今天真的好倒霉[OnO_气死我了]"，
    而不是混在正文内"今天真的好倒霉OnO_气死我了"」

所以两条硬要求：
  1. 表情必须是 [xxx]，不能是裸名混在正文里 —— 否则读的人分不清哪几个字是表情
  2. 装扮表情（名字带「装扮」）不显示 —— 又长又杂，没有信息量

⚠️ 反过来也要守住：[OnO_气死我了] 这种**带前缀但不是装扮**的
   属于正经表情包，必须保留。不能因为"名字长/带下划线"就砍掉。
"""

import io
import os
import sys

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BASE_DIR)

from bilibili import (_body_text, _collect_emotes, _drop_dress,      # noqa: E402
                      _dyn_text_nodes, _emote_code, _fmt_topic,
                      _is_dress_emote, _mark_emotes, _nodes_text,
                      _norm_emote, _parse_polymer)

FAILS = []


def ck(name, cond):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s" % name)
        FAILS.append(name)


def t_norm_emote():
    """裸名补方括号；已是括号 / 颜文字 / unicode 都不乱套。"""
    print("[裸名规范化]")
    ck("裸名补括号", _norm_emote("OnO_气死我了") == "[OnO_气死我了]")
    ck("已有括号不动", _norm_emote("[doge]") == "[doge]")
    # 颜文字套上括号就看不懂了
    ck("颜文字不套括号", _norm_emote("(￣▽￣)") == "(￣▽￣)")
    ck("unicode表情不套括号", _norm_emote("\U0001f60a") == "\U0001f60a")
    ck("空串返回空", _norm_emote("") == "")


def t_dress_filter():
    """装扮按**名字关键词**认，不按 type 认。"""
    print("[装扮判定]")
    ck("带装扮二字", _is_dress_emote("[塔菲_装扮_贴贴]") is True)
    ck("装扮前缀", _is_dress_emote("[装扮_开门啊]") is True)
    ck("suit形态", _is_dress_emote("[xx_suit_yy]") is True)
    # 这些是正经表情包，不能误杀
    ck("带前缀但不是装扮", _is_dress_emote("[OnO_气死我了]") is False)
    ck("收藏集不误杀", _is_dress_emote("[收藏集_牛]") is False)
    ck("充电表情不误杀", _is_dress_emote("[UP主名_贴贴]") is False)
    ck("普通小黄脸", _is_dress_emote("[doge]") is False)
    ck("过滤后顺序保留",
       _drop_dress(["[doge]", "[塔菲_装扮_贴贴]", "[打call]"])
       == ["[doge]", "[打call]"])


def t_nodes_text():
    """用户报的那个现象：opus 正文里表情是裸名。"""
    print("[富文本节点重建]")
    nodes = [
        {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "今天真的好倒霉"},
        {"type": "RICH_TEXT_NODE_TYPE_EMOJI", "text": "OnO_气死我了",
         "emoji": {"icon_url": "u"}},
    ]
    ck("裸名变成方括号",
       _nodes_text(nodes) == "今天真的好倒霉[OnO_气死我了]")

    # 装扮节点直接跳过
    nodes2 = [
        {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "好看"},
        {"type": "RICH_TEXT_NODE_TYPE_EMOJI", "text": "[塔菲_装扮_贴贴]"},
        {"type": "RICH_TEXT_NODE_TYPE_EMOJI", "text": "[doge]"},
    ]
    ck("装扮节点被跳过", _nodes_text(nodes2) == "好看[doge]")
    ck("关掉过滤则保留",
       _nodes_text(nodes2, drop_dress=False) == "好看[塔菲_装扮_贴贴][doge]")
    # 话题 / @用户 这类节点的 text 直接显示
    nodes3 = [
        {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "看这个"},
        {"type": "RICH_TEXT_NODE_TYPE_TOPIC", "text": "#抽风了#"},
    ]
    # 话题节点统一格式化成 #Tag:xxx（原来直接拼 "#抽风了#"，
    # 混在正文里跟普通文字分不开）
    ck("话题节点格式化", _nodes_text(nodes3) == "看这个#Tag:抽风了")
    # 没给节点要返回 None 让调用方回退，不能返回空串把正文清空
    ck("无节点返回None", _nodes_text(None) is None)
    ck("空列表返回None", _nodes_text([]) is None)


def t_mark_emotes():
    """没节点时：正文里的裸名就地补括号，不在正文里的才拼末尾。"""
    print("[纯文本回退]")
    ck("正文里裸名补括号",
       _mark_emotes("今天真的好倒霉OnO_气死我了", ["[OnO_气死我了]"])
       == "今天真的好倒霉[OnO_气死我了]")
    ck("已有方括号不重复",
       _mark_emotes("哈哈[doge]", ["[doge]"]) == "哈哈[doge]")
    ck("不在正文才拼末尾",
       _mark_emotes("大家好", ["[doge]"]) == "大家好 [doge]")
    # 长名优先：否则短名会先啃掉长名里的一段，变成 [[doge]_大笑]
    ck("长名优先",
       _mark_emotes("doge doge_大笑", ["[doge]", "[doge_大笑]"])
       == "[doge] [doge_大笑]")
    # 长名先替换后，短名不能再啃它
    ck("长名不被短名二次替换",
       _mark_emotes("x doge_大笑 y", ["[doge]", "[doge_大笑]"])
       == "x [doge_大笑] y [doge]")
    # 单字不就地补：中文正文里随便一个字都可能是表情名，补了满屏方括号
    # 但也不能丢 —— 走末尾追加
    ck("单字不就地补", _mark_emotes("我赞", ["[赞]"]) == "我赞 [赞]")


def t_body_text():
    """两条路都要通，且装扮在纯文本路径里也要被去掉。"""
    print("[组装正文]")
    # 路径 1：有节点
    got = _body_text("今天真的好倒霉OnO_气死我了",
                     [{"type": "RICH_TEXT_NODE_TYPE_TEXT",
                       "text": "今天真的好倒霉"},
                      {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                       "text": "OnO_气死我了"}],
                     ["[OnO_气死我了]"])
    ck("走节点路径", got == "今天真的好倒霉[OnO_气死我了]")
    # 路径 2：没节点
    ck("走纯文本路径",
       _body_text("今天真的好倒霉OnO_气死我了", None, ["[OnO_气死我了]"])
       == "今天真的好倒霉[OnO_气死我了]")
    # 装扮裸名混在纯文本里 → 删掉
    ck("纯文本里的装扮被删",
       _body_text("好看塔菲_装扮_贴贴", None,
                  ["[塔菲_装扮_贴贴]", "[doge]"]) == "好看 [doge]")
    # 空正文不会炸
    ck("空输入不炸", _body_text("", None, []) == "")


def t_parse_polymer_end_to_end():
    """真实结构走一遍：polymer 动态正文要有方括号、没装扮。"""
    print("[polymer 端到端]")
    item = {
        "id_str": "1",
        "modules": {"module_dynamic": {"desc": {
            "text": "今天真的好倒霉OnO_气死我了好看",
            "rich_text_nodes": [
                {"type": "RICH_TEXT_NODE_TYPE_TEXT",
                 "text": "今天真的好倒霉"},
                {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                 "text": "OnO_气死我了", "emoji": {"icon_url": "u"}},
                {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "好看"},
                {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                 "text": "[塔菲_装扮_贴贴]"},
            ],
        }}},
    }
    d = _parse_polymer(item)
    ck("表情带方括号", "[OnO_气死我了]" in d.text)
    ck("裸名不出现在正文", "好倒霉OnO_气死我了" not in d.text)
    ck("装扮不显示", "装扮" not in d.text)
    ck("正文完整", d.text == "今天真的好倒霉[OnO_气死我了]好看")


def t_wired_in():
    """三个入口都必须真的接上，不能只改了函数没改调用点。"""
    print("[接入点]")
    src = io.open(os.path.join(BASE_DIR, "bilibili.py"),
                  encoding="utf-8").read()
    ck("新版动态接入", "_clean_text(_body_text(" in src)
    ck("老版动态接入",
       "_body_text(text, None, _collect_emotes(card))" in src)
    ck("评论接入", '"content": _body_text(' in src)
    ck("装扮过滤存在", "_is_dress_emote" in src)


def t_collection_emote():
    """收藏集 / 充电大表情：名字带空格、「·」这类符号也要套上方括号。

    用户反馈：「还有像是收藏集表情也没有标注」。
    实测漏的两种形状：
      B) 节点自身 text 是空的，名字只在 emoji 子对象里 → 表情整块不见
      C/D) 名字含空格或「·」，字符集判断不通过 → 裸名混在正文里
    """
    print("[收藏集 / 大表情]")
    # B：节点 text 为空，只有 emoji.text
    ck("节点text空时取emoji子对象",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "今天"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI", "text": "",
                     "emoji": {"text": "[三只松鼠_收藏集_开心]",
                               "icon_url": "u", "type": 3}}])
       == "今天[三只松鼠_收藏集_开心]")
    # C：名字带空格
    ck("名字含空格也要套括号",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "今天"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "小星星 收藏集 拍手",
                     "emoji": {"icon_url": "u", "type": 3}}])
       == "今天[小星星 收藏集 拍手]")
    # D：名字带「·」
    ck("名字含·也要套括号",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "今天"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "喵·收藏集·打call",
                     "emoji": {"icon_url": "u", "type": 3}}])
       == "今天[喵·收藏集·打call]")
    # E：充电大表情（type=9）
    ck("充电大表情套括号",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "UP主专属_超级大笑",
                     "emoji": {"icon_url": "u", "type": 9}}])
       == "[UP主专属_超级大笑]")
    # 带图片就一定补括号（has_icon 参数）
    ck("has_icon强制补括号",
       _norm_emote("小星星 收藏集 拍手", has_icon=True)
       == "[小星星 收藏集 拍手]")
    # ⚠️ 反向守住：颜文字没有图片，绝不能被补成 [(￣▽￣)]
    ck("颜文字仍不套括号",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "(￣▽￣)"}]) == "(￣▽￣)")
    # 评论的 emote 字典：带 url 的裸名（含空格）也要补
    ck("评论emote字典带图补括号",
       _emote_code({"text": "小星星 收藏集 拍手", "url": "u"}, "")
       == "[小星星 收藏集 拍手]")
    ck("直播间专属表情补括号",
       _emote_code({"emoji": "直播间 专属 表情", "url": "u"}, "")
       == "[直播间 专属 表情]")
    # 装扮即使名字带空格，也照样不显示
    ck("装扮带空格仍不显示",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "好看"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "塔菲 装扮 贴贴",
                     "emoji": {"icon_url": "u"}}]) == "好看")




def t_user_cases():
    """用户反馈的三类（收藏集 / 装扮 / 话题 tag）。

    「收藏集表情【星回纪·时空轮转是我的翅膀】没标注」—— 名字带「·」，
    而且部分返回里节点自身不带 icon_url、emoji 子对象也是空的，
    只按"有没有图"判断就补不上括号，裸名混在正文里。
    「装扮【星瞳粉丝专属】不显示」—— 名字里没有"装扮"两个字，
    只按关键词认不出来。
    「tag【小星星的家】要 #Tag:xxx」—— 话题节点原来直接拼裸文本。
    """
    print("[用户反馈三类]")
    ck("收藏集裸节点也补括号",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "今天真的好倒霉"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "星回纪·时空轮转是我的翅膀"}])
       == "今天真的好倒霉[星回纪·时空轮转是我的翅膀]")
    ck("收藏集带type=3也补括号",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "起飞"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "星回纪·时空轮转是我的翅膀",
                     "emoji": {"type": 3, "icon_url": "u"}}])
       == "起飞[星回纪·时空轮转是我的翅膀]")
    ck("装扮【星瞳粉丝专属】不显示",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "来了"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "星瞳粉丝专属",
                     "emoji": {"type": 2, "icon_url": "u"}}]) == "来了")
    ck("装扮带type=2只写专属也剔除",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "来了"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "星瞳专属",
                     "emoji": {"type": 2, "icon_url": "u"}}]) == "来了")
    ck("充电大表情不被当装扮误杀",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "支持"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "UP主专属_超级大笑",
                     "emoji": {"type": 9, "icon_url": "u"}}])
       == "支持[UP主专属_超级大笑]")
    ck("会员表情不含专属不被误杀",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "好"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "热词系列_加鸡腿",
                     "emoji": {"type": 2, "icon_url": "u"}}])
       == "好[热词系列_加鸡腿]")
    ck("tag格式化#Tag:xxx",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "看看"},
                    {"type": "RICH_TEXT_NODE_TYPE_TOPIC",
                     "text": "小星星的家"}]) == "看看#Tag:小星星的家")
    ck("tag带#号也归一",
       _nodes_text([{"type": "RICH_TEXT_NODE_TYPE_TOPIC",
                     "text": "#小星星的家#"}]) == "#Tag:小星星的家")
    ck("tag不重复加前缀", _fmt_topic("#Tag:abc") == "#Tag:abc")
    ck("tag空则空", _fmt_topic("###") == "")


def _opus_item():
    """新版 opus 图文动态：正文和节点都在 major.opus.summary 里。

    ⚠️ 这是现在最常见的动态形态，也是之前"推送里没区分"的根源 ——
       老代码只从 module_dynamic.desc 取节点，opus 动态取到 None。
    """
    return {
        "id_str": "1",
        "modules": {"module_dynamic": {"major": {"type": "MAJOR_TYPE_OPUS",
            "opus": {"title": "", "summary": {
                "text": ("今天真的好倒霉星回纪·时空轮转是我的翅膀"
                         "#小星星的家#星瞳粉丝专属"),
                "rich_text_nodes": [
                    {"type": "RICH_TEXT_NODE_TYPE_TEXT",
                     "text": "今天真的好倒霉"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "星回纪·时空轮转是我的翅膀",
                     "emoji": {"text": "星回纪·时空轮转是我的翅膀",
                               "icon_url": "u", "type": 3}},
                    {"type": "RICH_TEXT_NODE_TYPE_TOPIC",
                     "text": "#小星星的家#"},
                    {"type": "RICH_TEXT_NODE_TYPE_EMOJI",
                     "text": "星瞳粉丝专属",
                     "emoji": {"text": "星瞳粉丝专属",
                               "icon_url": "u", "type": 2}},
                ]}, "pics": []}}}},
    }


def t_opus_nodes():
    """opus 动态：节点要从 major.opus.summary 取，不能只认 desc。"""
    print("[opus 动态节点来源]")
    item = _opus_item()
    md = item["modules"]["module_dynamic"]
    nodes = _dyn_text_nodes(md, md.get("major"), item)
    ck("opus 节点取得到", isinstance(nodes, list) and len(nodes) == 4)
    ck("空结构不炸", _dyn_text_nodes({}, {}, {}) is None)
    ck("只有 desc 时仍认老版",
       _dyn_text_nodes({"desc": {"rich_text_nodes": [
           {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "a"}]}}, {}, {})
       is not None)
    # 端到端：走完整解析
    d = _parse_polymer(item)
    ck("opus 收藏集补括号",
       "[星回纪·时空轮转是我的翅膀]" in d.text)
    ck("opus tag 加 #Tag 前缀", "#Tag:小星星的家" in d.text)
    ck("opus 装扮不显示", "星瞳粉丝专属" not in d.text)
    ck("opus 无裸 tag", "#小星星的家#" not in d.text)


def t_plain_fallback_topic_dress():
    """没有节点的纯文本兜底：话题也要 #Tag:、装扮也要删。

    部分动态/评论接口不给 rich_text_nodes，走的是纯文本那条路，
    只修节点重建的话这些场景依旧是裸的。
    """
    print("[纯文本兜底]")
    ck("兜底话题格式化",
       _body_text("看看#小星星的家#", None, [])
       == "看看#Tag:小星星的家")
    ck("兜底删装扮括号",
       _body_text("来了[星瞳粉丝专属]", None, [])
       == "来了")
    ck("单井号不误伤", "#1" in _body_text("今天第 #1 名", None, []))
    ck("空格不是话题",
       _body_text("a #不 是# b", None, []) == "a #不 是# b")
    ck("已是#Tag:不重复加",
       _body_text("#Tag:abc", None, []) == "#Tag:abc")
    ck("正经表情不被装扮误杀",
       _body_text("支持[UP主专属_超级大笑]", None, [])
       == "支持[UP主专属_超级大笑]")


def main():
    t_norm_emote()
    t_dress_filter()
    t_nodes_text()
    t_mark_emotes()
    t_body_text()
    t_parse_polymer_end_to_end()
    t_collection_emote()
    t_wired_in()
    t_user_cases()
    t_opus_nodes()
    t_plain_fallback_topic_dress()
    print("")
    if FAILS:
        print("FAILED %d: %s" % (len(FAILS), FAILS))
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
