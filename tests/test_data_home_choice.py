"""① 数据存放位置可以自己选 ② 图片统一归到 images/ 一个大类 ③ 残留进程清得掉。

① 用户反馈：「可以自己选择保存数据的存放位置」。
   以前数据目录只能靠环境变量改，真实用户根本不会用；而默认位置在哪
   也只有日志里一行。现在面板里能直接改，改完自动把配置、订阅、密钥、
   日志、图片、备份整体搬过去。
   ⚠️ 关键设计：指路文件必须放在**用户目录**下，不能放在数据目录里 ——
      否则搬完家之后"当前用哪个目录"这件事自己也读不到了。

② 用户反馈：「规范一下图片存放位置，全部图片都在一个大类里面」。
   以前分三处：cache/ 根、cache/long（头像）、cache/tmp（封面），
   名字还看不出用途。现在统一 images/ 下：avatars / push / ui。

③ 用户反馈：「机器人又开始不清除残留了」。
   检测残留原来靠 PowerShell / wmic / netstat 三条路，前两条在不少
   机器上根本跑不起来 → 返回空 → 打印「没有检测到残留进程」，
   可旧实例还活着。现在多一张不依赖任何外部命令的**实例登记表**。
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

import paths          # noqa: E402
import cleanup        # noqa: E402

results = []


def chk(name, ok, msg=""):
    results.append((name, bool(ok), msg))


def _write(p, text="x"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------- ① 位置可选

def t_pointer_priority():
    """三档优先级：环境变量 > 指路文件 > 默认位置。"""
    tmp = tempfile.mkdtemp(prefix="dh_")
    ptr = os.path.join(tmp, "pointer.txt")
    try:
        os.environ["BILI_NOTIFY_POINTER"] = ptr
        os.environ.pop("BILI_NOTIFY_HOME", None)
        default = paths._default_home()
        chk("没指路文件时用默认位置", paths.data_home() == default,
            paths.data_home())
        chk("来源记为 default", paths.home_source() == "default",
            paths.home_source())
        chosen = os.path.join(tmp, "my-data")
        with open(ptr, "w", encoding="utf-8") as f:
            f.write(chosen)
        chk("指路文件生效", os.path.abspath(paths.data_home()) == os.path.abspath(chosen),
            paths.data_home())
        chk("来源记为 pointer", paths.home_source() == "pointer",
            paths.home_source())
        other = os.path.join(tmp, "env-data")
        os.environ["BILI_NOTIFY_HOME"] = other
        chk("环境变量优先级最高", os.path.abspath(paths.data_home()) == os.path.abspath(other),
            paths.data_home())
        chk("来源记为 env", paths.home_source() == "env", paths.home_source())
    finally:
        os.environ.pop("BILI_NOTIFY_POINTER", None)
        os.environ.pop("BILI_NOTIFY_HOME", None)
        shutil.rmtree(tmp, ignore_errors=True)


def t_set_data_home_moves():
    """换位置：东西要跟着搬，指路文件要写对。"""
    tmp = tempfile.mkdtemp(prefix="dm_")
    old = os.path.join(tmp, "old")
    new = os.path.join(tmp, "new")
    ptr = os.path.join(tmp, "ptr.txt")
    try:
        os.makedirs(old, exist_ok=True)
        _write(os.path.join(old, "config.yaml"), "a: 1\n")
        _write(os.path.join(old, "state.json"), "{}")
        _write(os.path.join(old, ".machine_key"), "k")
        _write(os.path.join(old, "images", "avatars", "a.jpg"), "img")
        _write(os.path.join(old, "logs", "bot.log"), "log")
        _write(os.path.join(old, "backups", "b.zip"), "z")
        os.environ["BILI_NOTIFY_POINTER"] = ptr
        os.environ.pop("BILI_NOTIFY_HOME", None)
        with open(ptr, "w", encoding="utf-8") as f:
            f.write(old)
        r = paths.set_data_home(new)
        chk("搬迁成功", r.get("ok"), str(r.get("msg")))
        chk("新位置有 config.yaml",
            os.path.exists(os.path.join(new, "config.yaml")))
        chk("新位置有订阅数据",
            os.path.exists(os.path.join(new, "state.json")))
        chk("新位置有密钥", os.path.exists(os.path.join(new, ".machine_key")))
        chk("新位置有图片",
            os.path.exists(os.path.join(new, "images", "avatars", "a.jpg")))
        chk("新位置有日志", os.path.exists(os.path.join(new, "logs", "bot.log")))
        chk("新位置有备份", os.path.exists(os.path.join(new, "backups", "b.zip")))
        chk("搬完指路文件指向新位置",
            os.path.abspath(paths.data_home()) == os.path.abspath(new),
            paths.data_home())
        chk("给了重启提示", "重启" in (r.get("msg") or ""), r.get("msg"))
        # 同一个位置再来一次：不该报错，也不该说需要重启
        r2 = paths.set_data_home(new)
        chk("重复设同一个位置不报错", r2.get("ok"), str(r2.get("msg")))
        chk("重复设同一个位置不用重启", not r2.get("need_restart"))
    finally:
        os.environ.pop("BILI_NOTIFY_POINTER", None)
        shutil.rmtree(tmp, ignore_errors=True)


def t_set_data_home_guard():
    """空路径、建不了的路径：要挡住并说明原因，不能搬一半。"""
    tmp = tempfile.mkdtemp(prefix="dg_")
    ptr = os.path.join(tmp, "ptr.txt")
    old = os.path.join(tmp, "old")
    try:
        os.makedirs(old, exist_ok=True)
        os.environ["BILI_NOTIFY_POINTER"] = ptr
        with open(ptr, "w", encoding="utf-8") as f:
            f.write(old)
        r = paths.set_data_home("")
        chk("空路径被挡住", not r.get("ok") and "没填路径" in (r.get("msg") or ""),
            r.get("msg"))
        # 指路文件不能因为失败被写坏
        chk("失败时指路文件没被改坏", paths.read_pointer() == old,
            paths.read_pointer())
        # 落在一个已存在的文件（不是目录）上 → 挡住
        fpath = os.path.join(tmp, "afile")
        _write(fpath, "x")
        r2 = paths.set_data_home(fpath)
        chk("目标是文件不是目录 → 挡住", not r2.get("ok"), r2.get("msg"))
    finally:
        os.environ.pop("BILI_NOTIFY_POINTER", None)
        shutil.rmtree(tmp, ignore_errors=True)


def t_pointer_outside_data():
    """指路文件必须在数据目录之外（否则搬完就找不到自己了）。"""
    tmp = tempfile.mkdtemp(prefix="dp_")
    ptr = os.path.join(tmp, "ptr.txt")
    try:
        os.environ["BILI_NOTIFY_POINTER"] = ptr
        with open(ptr, "w", encoding="utf-8") as f:
            f.write(os.path.join(tmp, "data"))
        pp = paths.pointer_path()
        home = paths.data_home()
        chk("指路文件不在数据目录里",
            os.path.abspath(pp) != os.path.abspath(os.path.join(home, ".bili-notify-home")),
            pp)
        chk("指路文件也不在程序目录里",
            not os.path.abspath(pp).startswith(os.path.abspath(paths.app_dir()) + os.sep),
            pp)
    finally:
        os.environ.pop("BILI_NOTIFY_POINTER", None)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- ② 图片归类

def t_images_one_category():
    """所有图片都在 images/ 这一个大类下，按用途分三个小类。"""
    tmp = tempfile.mkdtemp(prefix="di_")
    ptr = os.path.join(tmp, "ptr.txt")
    home = os.path.join(tmp, "home")
    try:
        os.makedirs(home, exist_ok=True)
        os.environ["BILI_NOTIFY_POINTER"] = ptr
        os.environ.pop("BILI_NOTIFY_HOME", None)
        with open(ptr, "w", encoding="utf-8") as f:
            f.write(home)
        img = paths.image_dir()
        chk("图片总目录叫 images",
            os.path.basename(os.path.normpath(img)) == "images", img)
        chk("图片总目录在数据目录里",
            os.path.abspath(img).startswith(os.path.abspath(home) + os.sep), img)
        for sub in ("avatars", "push", "ui"):
            chk("有 %s 小类" % sub,
                os.path.basename(os.path.normpath(paths.image_sub(sub))) == sub)
        chk("media 的长期目录在 images/avatars",
            os.path.basename(os.path.normpath(_media_long())) == "avatars",
            _media_long())
        chk("media 的短期目录在 images/push",
            os.path.basename(os.path.normpath(_media_tmp())) == "push",
            _media_tmp())
    finally:
        os.environ.pop("BILI_NOTIFY_POINTER", None)
        shutil.rmtree(tmp, ignore_errors=True)


def _media_long():
    import media as M
    return M.LONG_DIR


def _media_tmp():
    import media as M
    return M.TMP_DIR


def t_migrate_images():
    """旧的三处图片（cache 根 / long / tmp）要搬进 images/ 对应小类。"""
    tmp = tempfile.mkdtemp(prefix="dmi_")
    ptr = os.path.join(tmp, "ptr.txt")
    home = os.path.join(tmp, "home")
    try:
        os.makedirs(home, exist_ok=True)
        _write(os.path.join(home, "cache", "long", "a.jpg"), "1")
        _write(os.path.join(home, "cache", "tmp", "b.jpg"), "2")
        _write(os.path.join(home, "cache", "index.json"), "{}")
        os.environ["BILI_NOTIFY_POINTER"] = ptr
        os.environ.pop("BILI_NOTIFY_HOME", None)
        with open(ptr, "w", encoding="utf-8") as f:
            f.write(home)
        moved = paths.migrate_images()
        chk("搬了东西", len(moved) >= 3, str(len(moved)))
        chk("头像进了 images/avatars",
            os.path.exists(os.path.join(home, "images", "avatars", "a.jpg")))
        chk("封面进了 images/push",
            os.path.exists(os.path.join(home, "images", "push", "b.jpg")))
        chk("索引进了 images/",
            os.path.exists(os.path.join(home, "images", "index.json")))
        chk("旧的 cache 目录已收走", not os.path.exists(os.path.join(home, "cache")))
    finally:
        os.environ.pop("BILI_NOTIFY_POINTER", None)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- ③ 残留清理

def t_registry_catches_leftover():
    """命令行那条路完全不可用（PowerShell 被禁）时，登记表仍能找到残留。

    这是"机器人又不清除残留了"的根因场景：
    list_ours() 返回空、端口那条路也拿不到 → 以前就是 0 个残留。
    """
    import subprocess
    tmp = tempfile.mkdtemp(prefix="dr_")
    ptr = os.path.join(tmp, "ptr.txt")
    home = os.path.join(tmp, "home")
    child = None
    try:
        os.makedirs(home, exist_ok=True)
        os.environ["BILI_NOTIFY_POINTER"] = ptr
        os.environ.pop("BILI_NOTIFY_HOME", None)
        with open(ptr, "w", encoding="utf-8") as f:
            f.write(home)
        # 真起一个子进程当"别的实例"（用自己 PID 不行：all_ours 会排除自己）
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(20)"])
        chk("登记别的实例", cleanup.register_instance(child.pid, role="other"))
        chk("登记表能读到活着的实例", child.pid in cleanup.registry_pids(),
            str(cleanup.registry_pids()))
        # 把命令行和端口两条路都掐了，模拟 PowerShell / netstat 不可用
        _orig_list = cleanup.list_ours
        _orig_ports = cleanup.pids_on_ports
        _orig_file = cleanup._pids_from_file
        try:
            cleanup.list_ours = lambda: []
            cleanup.pids_on_ports = lambda *a, **k: []
            cleanup._pids_from_file = lambda: []
            got = [int(x) for x in cleanup.all_ours()]
            chk("命令行/端口都失效时登记表仍兜得住", child.pid in got, str(got))
        finally:
            cleanup.list_ours = _orig_list
            cleanup.pids_on_ports = _orig_ports
            cleanup._pids_from_file = _orig_file
    finally:
        if child is not None:
            try:
                child.kill()
                child.wait(timeout=5)
            except Exception:
                pass
        os.environ.pop("BILI_NOTIFY_POINTER", None)
        shutil.rmtree(tmp, ignore_errors=True)


def t_registry_prunes_dead():
    """死掉的登记要清掉，不能一直当残留（否则每次都"有残留"）。"""
    tmp = tempfile.mkdtemp(prefix="dd_")
    ptr = os.path.join(tmp, "ptr.txt")
    home = os.path.join(tmp, "home")
    try:
        os.makedirs(home, exist_ok=True)
        os.environ["BILI_NOTIFY_POINTER"] = ptr
        os.environ.pop("BILI_NOTIFY_HOME", None)
        with open(ptr, "w", encoding="utf-8") as f:
            f.write(home)
        dead = 999997
        cleanup._reg_write(dead, role="gone")
        chk("死进程不算残留", dead not in cleanup.registry_pids(),
            str(cleanup.registry_pids()))
        chk("死进程的纸条被清掉", not os.path.exists(cleanup._reg_file(dead)))
    finally:
        os.environ.pop("BILI_NOTIFY_POINTER", None)
        shutil.rmtree(tmp, ignore_errors=True)


def t_startup_cleans_once():
    """启动只清一次残留 —— 以前注释写着一次、实际连着调两遍。"""
    src = os.path.join(ROOT, "start.py")
    with open(src, encoding="utf-8") as f:
        body = f.read()
    calls = [ln for ln in body.splitlines()
             if ln.strip().startswith("_kill_leftovers()")]
    chk("start.py 只调一次清理", len(calls) == 1, str(len(calls)))


def main():
    t_pointer_priority()
    t_set_data_home_moves()
    t_set_data_home_guard()
    t_pointer_outside_data()
    t_images_one_category()
    t_migrate_images()
    t_registry_catches_leftover()
    t_registry_prunes_dead()
    t_startup_cleans_once()
    bad = [r for r in results if not r[1]]
    for name, ok, msg in results:
        print(("  OK   " if ok else "  FAIL ") + name + (("  → " + msg) if (msg and not ok) else ""))
    print("\n%d/%d 通过" % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
