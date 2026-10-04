# -*- coding: utf-8 -*-
"""把 AppID / AppSecret / B站cookie / 面板口令 导出成一个文件。

为什么要这个：用户每版都是"解压覆盖整个文件夹"。
万一某个大版本建议"删掉旧文件夹重新解压"，
config.yaml 里已经填好的凭据就会一起没掉，得重新填一遍。

导出的文件可以直接在新版目录里用「恢复凭据.bat」导入。

注意：导出的是 **解密后的明文**，请妥善保管，别发给别人。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfgutil import BASE, load_cfg          # noqa: E402

try:
    from secretbox import reveal_cfg
except Exception:
    def reveal_cfg(cfg):
        return cfg

KEYS = ("appid", "secret", "bili_cookie", "web_password",
        "bili_cookie_expire", "panel_port", "poll_interval",
        "at_all_style", "message_style", "image_send_mode")


def main() -> int:
    cfg = reveal_cfg(load_cfg() or {})
    if not cfg:
        print("  没有读到 config.yaml，请先启动一次机器人。")
        return 1

    out = {k: cfg.get(k, "") for k in KEYS if cfg.get(k)}
    out["_exported_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    path = os.path.join(BASE, "凭据备份.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("  已导出到：")
    print(f"    {path}")
    print()
    print("  包含内容：")
    for k, v in out.items():
        if k.startswith("_"):
            continue
        if k in ("secret", "bili_cookie", "web_password"):
            shown = (str(v)[:6] + "…（已隐藏）") if v else "（空）"
        else:
            shown = v
        print(f"    · {k}: {shown}")
    print()
    print("  ⚠️ 这是明文文件，别发给别人。")
    print("  换新版后把它放进新目录，运行「恢复凭据.bat」即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
