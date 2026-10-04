# -*- coding: utf-8 -*-
"""从「完整备份」zip 里恢复：凭据 + 设定 + 订阅数据。

跟 backup_all.py 配对使用。找不到备份时会提示先备份。

⚠️ 恢复之前会把**当前**的 config.yaml 和数据文件先存一份快照，
   万一恢复错了还能退回去。
"""
import json
import os
import shutil
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfgutil import BASE, load_cfg, save_cfg          # noqa: E402
import paths                                            # noqa: E402
from tools.backup_all import (PREFIX, backup_dir, data_path,  # noqa: E402
                              list_full_backups)
try:
    import db
except Exception:
    db = None


def _snapshot_current() -> list:
    """恢复前先把现在的东西挪走一份，防止恢复错就没了。"""
    kept = []
    try:
        bd = backup_dir()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        src_cfg = paths.config_path()
        if os.path.exists(src_cfg):
            dst = os.path.join(bd, f"before-restore-{stamp}-config.yaml")
            shutil.copy2(src_cfg, dst)
            kept.append(dst)
        sp = data_path()
        if sp and os.path.exists(sp):
            dst = os.path.join(bd, f"before-restore-{stamp}-state.json")
            shutil.copy2(sp, dst)
            kept.append(dst)
    except Exception:
        pass
    return kept


def restore(zip_path: str) -> dict:
    if not zip_path or not os.path.exists(zip_path):
        return {"ok": False, "error": "找不到备份文件：" + str(zip_path)}
    try:
        z = zipfile.ZipFile(zip_path)
    except Exception as e:
        return {"ok": False, "error": "这个 zip 打不开：" + str(e)}

    info = {}
    try:
        names = z.namelist()
        if "config.yaml" not in names:
            return {"ok": False,
                    "error": "zip 里没有 config.yaml，不是完整备份包"}
        try:
            import yaml
            cfg = yaml.safe_load(z.read("config.yaml").decode("utf-8")) or {}
        except Exception:
            cfg = {}
        try:
            info = json.loads(z.read("meta.json").decode("utf-8"))
        except Exception:
            info = {}

        kept = _snapshot_current()

        # 1) 设定 + 凭据
        #    save_cfg 内部会把敏感字段重新加密，这里传明文即可
        if cfg:
            save_cfg(dict(cfg))

        # 2) 订阅数据
        st_text = "{}"
        if "state.json" in names:
            st_text = z.read("state.json").decode("utf-8")
        try:
            obj = json.loads(st_text)
        except Exception:
            obj = {}
        sp = data_path()
        written = ""
        if sp:
            os.makedirs(os.path.dirname(sp) or ".", exist_ok=True)
            if db is not None:
                db._atomic_write_json(sp, obj)
            else:
                with open(sp, "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False, indent=1)
            written = sp
    finally:
        try:
            z.close()
        except Exception:
            pass

    return {"ok": True, "path": zip_path, "meta": info,
            "state_path": written, "kept": kept}


def main() -> int:
    target = ""
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--file" and i + 1 < len(args):
            target = args[i + 1]
        elif a.startswith("--file="):
            target = a.split("=", 1)[1]

    if not target:
        bks = list_full_backups()
        if not bks:
            print("  没找到任何完整备份。")
            print("  先运行「备份全部数据.bat」，或在面板里点一次备份。")
            return 1
        target = bks[0]["path"]
        print(f"  使用最新的备份：{target}")
        print(f"  （共 {len(bks)} 份；要选别的：python tools/restore_all.py "
              f"--file 路径）")
        print()

    r = restore(target)
    if not r.get("ok"):
        print("  恢复失败：" + str(r.get("error")))
        return 1

    stats = (r.get("meta") or {}).get("stats") or {}
    print("  已恢复：")
    print("    · 设定与凭据 → config.yaml")
    if stats:
        print(f"    · 群 {stats.get('groups', 0)} 个 / 订阅 "
              f"{stats.get('subs', 0)} 条 / UP 主 {stats.get('ups', 0)} 位"
              f" / 合集 {stats.get('seasons', 0)} 个"
              f" / 文案 {stats.get('templates', 0)} 条")
    print(f"    · 数据 → {r.get('state_path') or '（无）'}")
    if r.get("kept"):
        print()
        print("  恢复前的原文件已留底：")
        for k in r["kept"]:
            print(f"    {k}")
    print()
    print("  重启机器人后生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
