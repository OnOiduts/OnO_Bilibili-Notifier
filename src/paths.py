# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""统一路径：把「程序」和「数据」彻底分开。

背景（这次为什么要做）：
    平时的升级方式是「删掉整个文件夹，用新的压缩包替换」。
    以前 config.yaml、订阅数据、加密密钥、日志全跟源码混在同一个目录里，
    于是每升级一次就丢一次东西——配置要重填、订阅要重加、密钥丢了连
    凭据都解不开。

现在的划分：
    程序目录（可以整个删掉，随时用新包替换）
        ├─ src/        源码，谁都不用碰
        ├─ docs/       文档
        ├─ tests/      测试
        └─ 启动脚本    用户只用这几个

    数据目录（在程序目录之外，删文件夹删不到）
        ├─ config.yaml         设定：轮询、图片大小…
        ├─ state.json          订阅、推送基线
        ├─ .machine_key        凭据加密密钥（丢了凭据就解不开）
        ├─ logs/               运行日志
        ├─ cache/              图片缓存
        ├─ run/                进程号、锁、面板地址等运行时小文件
        └─ backups/            自动滚动备份

想改数据目录位置：设环境变量 BILI_NOTIFY_HOME。
"""
import os
import shutil

# ---------------------------------------------------------------- 程序区


def src_dir() -> str:
    """源码目录（本文件所在目录）。"""
    return os.path.dirname(os.path.abspath(__file__))


def app_dir() -> str:
    """程序根目录：src/ 的上一层。

    兼容老布局（源码直接摊在根目录）：那时没有 src/，程序根就是源码目录。
    """
    d = src_dir()
    if os.path.basename(d).lower() == "src":
        return os.path.dirname(d)
    return d


def asset(*parts) -> str:
    """找随包文件：新版在 src/ 或 docs/，老版摊在根目录 —— 都认。

    为什么要这么找：v1.96 把根目录瘦身成「只有启动脚本」之后，
    VERSION / requirements / config.example.yaml 都搬进了子目录。
    但用户手上是「删文件夹换压缩包」的升级方式，同一份代码也可能
    被按老布局解开（没有 src/）。两边都得找得到，否则一上来就报
    「找不到 VERSION」，面板版本号显示 unknown。
    """
    name = os.path.join(*parts)
    for base in (src_dir(), docs_dir(), app_dir()):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(src_dir(), name)


def docs_dir() -> str:
    d = os.path.join(app_dir(), "docs")
    if os.path.isdir(d):
        return d
    return app_dir()


# ---------------------------------------------------------------- 数据区


def _default_home() -> str:
    """默认数据目录：**程序本体目录下**的 data/ 子目录。

    为什么改回本体目录（v1.97 的修正）：
        v1.96 把数据挪到了 ~/.bili-notify（用户目录下一个隐藏文件夹）。
        实际用起来问题很大——日志、配置、订阅跑到"别的软件目录"里，
        想看一眼日志得先去翻 C:\\Users\\xxx\\.bili-notify，
        备份也要额外记一个位置。用户明确要求：数据就跟程序放一起。

    为什么是 data/ 子目录而不是直接摊在根目录：
        根目录要保持「只有启动脚本」的清爽（v1.96 的目录规范），
        全部数据收进一个 data/ 里，一眼能认出、整体复制就能备份。
    """
    return os.path.join(app_dir(), "data")


def _old_default_home() -> str:
    """v1.96 用过的旧默认位置（用户目录下的隐藏文件夹）。

    已经在那儿的数据要自动搬回来 —— 否则用户手动填好的凭据、
    重新加过的订阅会因为一次升级又"消失"一次。
    """
    if os.name == "nt":
        base = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~")
    return os.path.join(base, ".bili-notify")


def pointer_path() -> str:
    """「数据放在哪」的指路文件。

    为什么必须单独一个小文件、而且不能放在数据目录里：
        数据目录是可以被用户改的（面板里换个盘、换个文件夹）。
        如果"当前用哪个目录"这件事也存在数据目录里，就变成
        「先有鸡还是先有蛋」——搬到新位置后，程序一启动先读不到
        指路文件，于是又回到默认目录，搬迁等于白做。
        所以它固定放在用户目录下，跟程序目录、数据目录都无关。
    """
    env = os.environ.get("BILI_NOTIFY_POINTER", "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if os.name == "nt":
        base = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~")
    return os.path.join(base, ".bili-notify-home")


def home_source() -> str:
    """当前数据目录是从哪儿定的：env / pointer / default（排查用）。"""
    if os.environ.get("BILI_NOTIFY_HOME", "").strip():
        return "env"
    try:
        with open(pointer_path(), encoding="utf-8") as f:
            if f.read().strip():
                return "pointer"
    except Exception:
        pass
    return "default"


def read_pointer() -> str:
    try:
        with open(pointer_path(), encoding="utf-8") as f:
            v = f.read().strip()
    except Exception:
        return ""
    return v


def data_home() -> str:
    """数据目录根目录。默认在用户目录下，不在程序目录里。

    三档优先级（就高不就低）：
        1. 环境变量 BILI_NOTIFY_HOME —— 给批量部署 / Docker 用
        2. 指路文件 —— 用户在面板里选的位置，记在用户目录下
        3. 默认位置 ~/.bili-notify
    """
    env = os.environ.get("BILI_NOTIFY_HOME", "").strip()
    if not env:
        # ONOBN_DATA_DIR 是本项目统一的命名前缀（见 envcfg.py），
        # 与历史遗留的 BILI_NOTIFY_HOME 等价，两个都认。
        try:
            import envcfg
            env = envcfg.data_dir_from_env()
        except Exception:
            env = ""
    if env:
        return os.path.abspath(os.path.expanduser(env))
    v = read_pointer()
    if v:
        return os.path.abspath(os.path.expanduser(v))
    return _default_home()


def _sub(name: str) -> str:
    p = os.path.join(data_home(), name)
    try:
        os.makedirs(p, exist_ok=True)
    except OSError:
        pass
    return p


def log_dir() -> str:
    return _sub("logs")


def run_dir() -> str:
    return _sub("run")


def backup_dir() -> str:
    return _sub("backups")


def image_dir() -> str:
    """所有图片的总目录（数据目录/images）。"""
    return _sub("images")


def config_path() -> str:
    """config.yaml：优先环境变量 BOT_CONFIG，否则放数据目录。"""
    env = os.environ.get("BOT_CONFIG", "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(data_home(), "config.yaml")


def state_path(name: str = "state.json") -> str:
    """订阅数据文件。"""
    return os.path.join(data_home(), name)


def key_file(name: str) -> str:
    """密钥类文件（.machine_key 等）——必须跟着数据走，不能留在程序目录。"""
    return os.path.join(data_home(), name)


def log_file(name: str) -> str:
    return os.path.join(log_dir(), name)


def run_file(name: str) -> str:
    return os.path.join(run_dir(), name)


def image_sub(name: str) -> str:
    """图片下的分类子目录：avatars（头像）/ push（推送封面）/ ui（界面图）。"""
    p = os.path.join(image_dir(), name)
    try:
        os.makedirs(p, exist_ok=True)
    except OSError:
        pass
    return p


# ------------------------------------------------- 换数据目录（面板里可选位置）


def _move(old: str, new: str) -> bool:
    """把一个文件/目录搬到新位置。目标已存在就跳过（绝不覆盖）。"""
    if not os.path.exists(old) or os.path.exists(new):
        return False
    try:
        os.makedirs(os.path.dirname(new), exist_ok=True)
    except OSError:
        pass
    try:
        shutil.move(old, new)
        return True
    except OSError:
        try:
            if os.path.isdir(old):
                shutil.copytree(old, new)
                shutil.rmtree(old, ignore_errors=True)
            else:
                shutil.copy2(old, new)
                os.remove(old)
            return True
        except OSError:
            return False


# 数据目录里这些文件/目录要整体跟着搬（运行时小文件 run/ 不搬，重建即可）
_DATA_ITEMS = ("config.yaml", "state.json", "data.db", "data.json",
               ".machine_key", ".machine_key.prev", ".webui_secret",
               "logs", "cache", "images", "backups")


def set_data_home(new_path: str, move: bool = True) -> dict:
    """把数据目录改到用户指定的位置，并把已有数据搬过去。

    返回 {ok, from, to, moved, msg, need_restart}。
    ⚠️ 搬完之后**必须重启**：secretbox / media 在导入时就把密钥、图片
       目录算成了固定值，运行中改指路文件只会让新旧两边各写一份。
    """
    # ⚠️ 先判空再 abspath：os.path.abspath("") 会返回**当前工作目录**，
    #    空输入会被当成"搬到程序目录里"，config / 订阅被搬进一个
    #    下次删文件夹就没了的地方，而且用户完全不知情。
    raw = (new_path or "").strip()
    res = {"ok": False, "from": data_home(), "to": raw, "moved": [],
           "msg": "", "need_restart": True}
    if not raw:
        res["msg"] = "没填路径"
        return res
    new_path = os.path.abspath(os.path.expanduser(raw))
    res["to"] = new_path
    old = res["from"]
    if os.path.abspath(old) == new_path:
        res["ok"] = True
        res["msg"] = "就是这个位置，没变"
        res["need_restart"] = False
        return res
    try:
        os.makedirs(new_path, exist_ok=True)
    except OSError as e:
        res["msg"] = "这个位置建不了目录：%s" % e
        return res
    if not os.path.isdir(new_path):
        res["msg"] = "这不是一个文件夹"
        return res
    # 写不进去就别搬，免得搬一半两边都没有
    probe = os.path.join(new_path, ".write_test")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
    except OSError:
        res["msg"] = "这个位置没有写入权限，换一个试试"
        return res

    if move:
        for name in _DATA_ITEMS:
            src = os.path.join(old, name)
            dst = os.path.join(new_path, name)
            if os.path.abspath(src) == os.path.abspath(dst):
                continue
            if _move(src, dst):
                res["moved"].append(name)
    try:
        with open(pointer_path(), "w", encoding="utf-8") as f:
            f.write(new_path)
    except OSError as e:
        res["msg"] = "位置写好了，但没能记下来（%s）——重启后会回到原来的目录" % e
        return res
    res["ok"] = True
    res["msg"] = ("已把数据搬到新位置"
                  + ("（搬了 %d 项：%s）" % (len(res["moved"]), "、".join(res["moved"]))
                     if res["moved"] else "")
                  + "。重启机器人后生效")
    # 程序目录里的数据目录 = 删文件夹就没了，明确提示
    try:
        if new_path.startswith(os.path.abspath(app_dir()) + os.sep):
            res["msg"] += "。⚠️ 这个位置在程序目录里，删文件夹换新版时会一起没掉"
    except Exception:
        pass
    return res


# ---------------------------------------------------------------- 旧文件迁移

# 旧布局里这些文件散落在程序目录 / 源码目录，升级后要搬到数据目录。
_LEGACY = (
    # (旧文件名, 新归属: "root" 数据根 / "logs" / "run" / "cache")
    ("config.yaml", "root"),
    ("data.db", "root"),
    ("data.json", "root"),
    ("state.json", "root"),
    (".machine_key", "root"),
    (".machine_key.prev", "root"),
    (".webui_secret", "root"),
    (".integrity.json", "run"),
    (".bot.pids", "run"),
    (".bot_heartbeat", "run"),
    (".banned_ips", "run"),
    (".bot.lock", "run"),
    (".panel_url", "run"),
    (".restart_bot", "run"),
    ("bot.log", "logs"),
    ("backend.log", "logs"),
    ("security.log", "logs"),
)


def _dest(kind: str, name: str) -> str:
    if kind == "logs":
        return log_file(name)
    if kind == "run":
        return run_file(name)
    if kind == "images":
        return os.path.join(image_dir(), name)
    return os.path.join(data_home(), name)


def migrate_from_old_home(verbose=False) -> list:
    """把 v1.96 挪到用户目录下的那批数据搬回本体目录。

    只在「用户没指定位置」时才做：
        - 环境变量 BILI_NOTIFY_HOME 没设
        - 指路文件里也没写（用户没在面板里选过位置）
    否则会跟用户自己选的位置打架。
    """
    if os.environ.get("BILI_NOTIFY_HOME", "").strip() or read_pointer():
        return []
    old = _old_default_home()
    new = _default_home()
    try:
        if (os.path.abspath(old) == os.path.abspath(new)
                or not os.path.isdir(old)):
            return []
    except OSError:
        return []
    moved = []
    for name in _DATA_ITEMS:
        src = os.path.join(old, name)
        dst = os.path.join(new, name)
        if _move(src, dst):
            moved.append(name)
            if verbose:
                print(f"  [迁移] 数据 {name} → {dst}")
    # 搬空了就把旧目录删掉，别留一个空壳让人以为数据还在那儿
    try:
        if not os.listdir(old):
            os.rmdir(old)
    except OSError:
        pass
    return moved


def migrate_images(verbose=False) -> list:
    """把散在 cache/ 里的图片统一收进 images/ 这一个大类。

    以前图片分三处：cache/ 根、cache/long（头像）、cache/tmp（封面），
    名字还看不出用途。现在全部归到 images/ 下按用途分：
        images/avatars  长期保留（UP 头像，反复复用）
        images/push     短期（推送封面，到点自动清）
        images/ui       界面图（logo 等，跟着数据走，换新版不会被删）
    """
    moved = []
    old_root = os.path.join(data_home(), "cache")
    if not os.path.isdir(old_root):
        return moved
    pairs = (("long", "avatars"), ("tmp", "push"))
    for old_name, new_name in pairs:
        src = os.path.join(old_root, old_name)
        dst = os.path.join(image_dir(), new_name)
        if os.path.isdir(src) and not os.path.exists(dst):
            if _move(src, dst):
                moved.append((src, dst))
                if verbose:
                    print(f"  [迁移] 图片 {old_name}/ → images/{new_name}/")
    # 散在 cache/ 根上的图片文件（index.json / recent.json / 早期裸图）
    try:
        for name in sorted(os.listdir(old_root)):
            src = os.path.join(old_root, name)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(image_dir(), name)
            if _move(src, dst):
                moved.append((src, dst))
                if verbose:
                    print(f"  [迁移] 图片 {name} → images/{name}")
        if not os.listdir(old_root):
            os.rmdir(old_root)
    except OSError:
        pass
    return moved


def migrate_legacy(verbose=False) -> list:
    """把旧布局散落在程序目录里的运行时文件搬到数据目录。

    只在目标位置还没有文件时才搬（已有的以数据目录为准，绝不覆盖）。
    搬成功才删原件——宁可留一份旧的，也不能搬丢。
    """
    moved = []
    srcs = []
    for d in (src_dir(), app_dir()):
        if d and d not in srcs:
            srcs.append(d)
    for d in srcs:
        if not os.path.isdir(d):
            continue
        for name, kind in _LEGACY:
            old = os.path.join(d, name)
            if not os.path.isfile(old):
                continue
            new = _dest(kind, name)
            if os.path.abspath(old) == os.path.abspath(new):
                continue
            if os.path.exists(new):
                continue
            try:
                os.makedirs(os.path.dirname(new), exist_ok=True)
                shutil.copy2(old, new)
                os.remove(old)
                moved.append((old, new))
                if verbose:
                    print(f"  [迁移] {name} → {new}")
            except OSError:
                continue
    return moved


if __name__ == "__main__":
    print("程序目录:", app_dir())
    print("源码目录:", src_dir())
    print("数据目录:", data_home())
    print("配置文件:", config_path())
    print("订阅数据:", state_path())
    for old, new in migrate_legacy(verbose=True):
        pass
    print("（以上为本次迁移的文件，没有输出说明已是新布局）")
