# -*- coding: utf-8 -*-
"""把「凭据 + 全部设定 + 订阅数据」打包成一个 zip。

为什么要这个（而不是只备份凭据）：
    订阅关系、文案模板、推送基线才是攒了很久的东西；
    凭据没了可以重新扫码，订阅没了得一条条重新加。
    以前只导出凭据，结果"换新版 → 删文件夹 → 订阅全空"。

⚠️ 备份放哪：默认写到**数据目录里的 backups/**，也就是跟着「存放位置」走。
    以前固定写桌面，用户自己指定了存放位置后，备份还往桌面跑 ——
    两边不一致，找的时候不知道看哪头。现在统一：备份就在数据旁边。

    ⚠️ 数据目录本身必须在程序目录之外（v1.93 起的默认），否则删文件夹
       换新版时备份会跟着一起没。真出现这种情况（用户手动把数据目录
       指回了程序目录内），会额外在桌面再留一份兜底。

zip 里三样东西：
    config.yaml  设定全量（轮询间隔、图片大小、文案开关…）+ 凭据明文
    state.json   订阅关系 / 文案模板 / 推送基线
    meta.json    版本、导出时间、条目统计（给人看的）
"""
import json
import os
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfgutil import BASE, load_cfg                    # noqa: E402
import paths                                            # noqa: E402

try:
    import db
except Exception:
    db = None
try:
    from secretbox import reveal_cfg
except Exception:
    def reveal_cfg(cfg):
        return cfg

PREFIX = "B站机器人备份-"
KEEP = 10


def data_path() -> str:
    """当前数据文件的真实位置。"""
    try:
        cfg = load_cfg() or {}
        return db.resolve_data_path(cfg.get("data_file") or db.DATA_FILE_DEFAULT)
    except Exception:
        return ""


def _desktop() -> str:
    """桌面目录；找不到就退回用户目录。"""
    home = os.path.expanduser("~")
    try:
        if os.name == "nt":
            home = os.environ.get("USERPROFILE") or home
            d = os.path.join(home, "Desktop")
            # 部分系统桌面被 OneDrive 重定向
            if not os.path.isdir(d):
                od = os.path.join(home, "OneDrive", "Desktop")
                if os.path.isdir(od):
                    d = od
        else:
            d = os.path.join(home, "Desktop")
        return d if os.path.isdir(d) else home
    except Exception:
        return home


def backup_dir() -> str:
    """数据目录里的自动备份目录（跟着数据走，不会被删文件夹带走）。"""
    try:
        p = data_path()
        d = os.path.join(os.path.dirname(p) or ".", "backups")
    except Exception:
        d = paths.backup_dir()
    os.makedirs(d, exist_ok=True)
    return d


def _inside_prog(d: str) -> bool:
    """备份目录是不是在程序目录里面（删文件夹会一起带走）。"""
    try:
        prog = os.path.abspath(BASE)
        dd = os.path.abspath(d)
        return dd == prog or dd.startswith(prog + os.sep)
    except Exception:
        return False


def _stats(state_path: str) -> dict:
    """从数据文件里数一下有多少东西，写进 meta 给人看。

    数据文件的结构是「每张表一个列表」，不是按群嵌套的字典 ——
    以前按嵌套字典算，结果永远显示 0，看着像"备份里什么都没有"。
    """
    out = {"groups": 0, "ups": 0, "subs": 0, "seasons": 0, "templates": 0}
    try:
        if not state_path or not os.path.exists(state_path):
            return out
        with open(state_path, "r", encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            return out

        def n(key):
            v = st.get(key)
            return len(v) if isinstance(v, (list, dict)) else 0

        out["groups"] = n("groups")
        out["subs"] = n("subs")
        out["ups"] = n("ups")
        out["seasons"] = n("seasons")
        out["templates"] = (n("up_templates") + n("season_templates")
                            + n("group_templates"))
    except Exception:
        pass
    return out


def make_backup(out_dir: str = "", quiet: bool = False) -> dict:
    """打一个完整备份包。返回 {ok, path, stats}。"""
    cfg = reveal_cfg(load_cfg() or {})
    st_path = data_path()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{PREFIX}{stamp}.zip"
    # 默认落到数据目录的 backups/（跟着存放位置走），失败才退回桌面
    try:
        d = out_dir or backup_dir()
    except Exception:
        d = out_dir or ""
    if not d:
        d = _desktop()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = _desktop()
        os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, name)

    try:
        import yaml
        cfg_text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
    except Exception:
        cfg_text = json.dumps(cfg, ensure_ascii=False, indent=2)

    stats = _stats(st_path)
    meta = {
        "_backup_version": 1,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data_file": st_path,
        "stats": stats,
    }
    try:
        from version import __version__
        meta["version"] = __version__
    except Exception:
        try:
            with open(paths.asset("VERSION"), encoding="utf-8") as f:
                meta["version"] = f.read().strip()
        except Exception:
            meta["version"] = ""

    try:
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("config.yaml", cfg_text)
            z.writestr("meta.json", json.dumps(meta, ensure_ascii=False,
                                               indent=2))
            if st_path and os.path.exists(st_path):
                z.write(st_path, "state.json")
            else:
                z.writestr("state.json", "{}")
    except Exception as e:
        return {"ok": False, "path": "", "stats": stats, "error": str(e)}

    # 数据目录里也留一份（自动备份、面板恢复都从这里找）
    try:
        bd = backup_dir()
        if os.path.abspath(bd) != os.path.abspath(d):
            _copy_into(dst, bd)
        _prune(bd)
    except Exception:
        pass
    # ⚠️ 数据目录被指回程序目录内时，删文件夹换新版会把备份一起带走，
    #    这种情况额外在桌面留一份兜底。
    try:
        if _inside_prog(d):
            _copy_into(dst, _desktop())
            _prune(_desktop())
    except Exception:
        pass
    _prune(d)

    if not quiet:
        print("  已备份到：")
        print(f"    {dst}")
        print()
        print("  包含内容：")
        print(f"    · 设定与凭据：config.yaml（{len(cfg)} 项）")
        print(f"    · 群 {stats['groups']} 个 / 订阅 {stats['subs']} 条"
              f" / UP 主 {stats['ups']} 位 / 合集 {stats['seasons']} 个"
              f" / 文案 {stats['templates']} 条")
        print("    · 推送基线（已推过哪些，避免重复推）")
        print()
        print("  ⚠️ config.yaml 里是明文凭据，别发给别人。")
    return {"ok": True, "path": dst, "stats": stats}


def _copy_into(src: str, d: str):
    import shutil
    dst = os.path.join(d, os.path.basename(src))
    shutil.copy2(src, dst)
    return dst


def _prune(d: str, keep: int = KEEP):
    import glob
    try:
        fs = sorted(glob.glob(os.path.join(d, PREFIX + "*.zip")),
                    key=os.path.getmtime, reverse=True)
        for f in fs[keep:]:
            try:
                os.unlink(f)
            except Exception:
                pass
    except Exception:
        pass


def list_full_backups() -> list:
    """所有完整备份，新的在前。"""
    import glob
    out = []
    for d in (backup_dir(), _desktop()):
        try:
            fs = glob.glob(os.path.join(d, PREFIX + "*.zip"))
        except Exception:
            continue
        for f in fs:
            try:
                out.append({"path": f, "name": os.path.basename(f),
                            "mtime": os.path.getmtime(f),
                            "size": os.path.getsize(f)})
            except Exception:
                pass
    seen = set()
    uniq = []
    for b in sorted(out, key=lambda x: x["mtime"], reverse=True):
        if b["path"] in seen:
            continue
        seen.add(b["path"])
        uniq.append(b)
    return uniq


AUTO_FLAG = ".last-auto-backup"


def auto_backup_if_due(interval: float = 86400.0) -> str:
    """每天自动备份一次（给启动时调用）。返回备份路径，没到时间则空。"""
    try:
        bd = backup_dir()
        flag = os.path.join(bd, AUTO_FLAG)
        try:
            last = float(open(flag, encoding="utf-8").read().strip() or 0)
        except Exception:
            last = 0.0
        if time.time() - last < interval:
            return ""
        r = make_backup(out_dir=bd, quiet=True)
        if r.get("ok"):
            try:
                with open(flag, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))
            except Exception:
                pass
            return r["path"]
    except Exception:
        pass
    return ""


def main() -> int:
    out = ""
    args = [a for a in sys.argv[1:]]
    for i, a in enumerate(args):
        if a == "--out" and i + 1 < len(args):
            out = args[i + 1]
        elif a.startswith("--out="):
            out = a.split("=", 1)[1]
    r = make_backup(out_dir=out)
    if not r.get("ok"):
        print("  备份失败：" + str(r.get("error", "未知原因")))
        return 1
    print()
    print("  以后在面板「⚙️ 设置 → 数据与备份」里点一下就能备份。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
