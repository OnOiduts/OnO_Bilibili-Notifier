# -*- coding: utf-8 -*-
"""从「凭据备份.json」把凭据写回 config.yaml。

换新版目录后，把备份文件放进来，运行本脚本即可，
不用重新手填 AppID / cookie。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfgutil import BASE, load_cfg                  # noqa: E402

# 用 webui 的保存函数：它会顺手把敏感字段加密落盘
from webui import save_cfg as save_cfg_safe         # noqa: E402

SKIP = ("_exported_at",)


def main() -> int:
    src = os.path.join(BASE, "凭据备份.json")
    if not os.path.exists(src):
        print("  没找到 凭据备份.json")
        print(f"  请把它放到这个目录：{BASE}")
        print("  （在旧版本目录运行「备份凭据.bat」可以生成）")
        return 1

    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)

    cfg = load_cfg() or {}
    n = 0
    for k, v in (data or {}).items():
        if k in SKIP or not v:
            continue
        cfg[k] = v
        n += 1
    save_cfg_safe(cfg)

    print(f"  已恢复 {n} 项到 config.yaml")
    print()
    for k, v in (data or {}).items():
        if k in SKIP:
            continue
        if k in ("secret", "bili_cookie", "web_password"):
            shown = (str(v)[:6] + "…（已隐藏）") if v else "（空）"
        else:
            shown = v
        print(f"    · {k}: {shown}")
    print()
    print("  现在可以直接启动机器人了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
