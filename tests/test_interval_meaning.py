# -*- coding: utf-8 -*-
"""间隔调整的三类动作，翻译文案必须各不相同。

以前三类（撞风控放慢 / 解除回最快 / 平稳逐步加快）全部被同一条
「轮询间隔 Xs → Ys」泛化规则吃掉，日志页里显示成**同一句**
「检查频率自动调整了…」——连着看就是同一条消息被重复推送。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from logtranslate import explain

PASS = FAIL = 0


def ck(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


LINES = {
    "risk": ("2026-09-27 05:01:27 [WARNING] (bot.py:1546)_adapt_interval "
             "撞到 B 站风控，轮询间隔 12s → 24s（稳定后会自动加快）"),
    "back": ("2026-09-27 05:01:27 [INFO] (bot.py:1560)_adapt_interval "
             "风控风险已解除，检查频率已回到最快：24s → 8s（最快档）"),
    "calm": ("2026-09-27 05:01:27 [DEBUG] (bot.py:1588)_adapt_interval "
             "检查频率加快中：24s → 19s"),
}


def main():
    ms = {k: (explain(v)["meaning"] or "") for k, v in LINES.items()}
    ck("撞风控 / 解除 文案不同", ms["risk"] != ms["back"] and ms["risk"])
    ck("解除 / 加快 文案不同", ms["back"] != ms["calm"] and ms["back"])
    ck("撞风控 / 加快 文案不同", ms["risk"] != ms["calm"])
    ck("三条都翻得出话", all(ms.values()))
    ck("撞风控提到放慢", "放慢" in ms["risk"])
    ck("解除提到恢复", "恢复" in ms["back"])
    ck("加快提到逐步加快", "逐步加快" in ms["calm"])
    ck("不再共用旧文案", all("检查频率自动调整了" not in m for m in ms.values()))
    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
