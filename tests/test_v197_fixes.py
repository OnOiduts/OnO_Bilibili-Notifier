# -*- coding: utf-8 -*-
"""v1.97 四项修复的回归测试。

1. 机器人退出码 1：bot.py 里配置路径写死相对路径 "config.yaml"
2. 数据目录被放到用户目录（~/.bili-notify），要求回本体目录
3. 「第一次使用？」的「现在去填写」跳转到错的板块
4. 运行状态把监督进程 start.py 报成「1 个残留」
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import paths  # noqa: E402

APP = paths.app_dir()


# ----------------------------------------------------------- 2. 数据目录在本体

def test_default_home_inside_app_dir():
    """默认数据目录必须在程序本体目录下，不能跑到用户目录去。"""
    home = os.path.abspath(paths._default_home())
    app = os.path.abspath(APP)
    assert home.startswith(app + os.sep), (
        "数据目录 %s 不在本体目录 %s 里" % (home, app))


def test_default_home_not_in_user_profile():
    """不能再用用户目录下的隐藏文件夹（v1.96 的旧位置）。"""
    home = os.path.abspath(paths._default_home())
    for env_key in ("USERPROFILE", "XDG_DATA_HOME"):
        base = os.environ.get(env_key)
        if not base:
            continue
        assert not home.startswith(os.path.abspath(base) + os.sep), (
            "数据目录又跑回用户目录里了：%s" % home)


def test_data_subdirs_under_home():
    """logs / cache / images 这些子目录都跟着本体目录走。"""
    home = os.path.abspath(paths._default_home())
    for name in ("logs", "cache", "images"):
        sub = os.path.abspath(paths._sub(name))
        assert sub.startswith(home + os.sep), "%s 不在数据目录下" % name


def test_migrate_from_old_home(tmp_path, monkeypatch):
    """v1.96 放在用户目录里的数据，升级后要自动搬回本体目录。"""
    old = tmp_path / "oldhome"
    new = tmp_path / "newhome"
    old.mkdir()
    (old / "config.yaml").write_text("appid: '123'\n", encoding="utf-8")
    (old / "state.json").write_text("{}", encoding="utf-8")
    (old / "logs").mkdir()
    (old / "logs" / "bot.log").write_text("x", encoding="utf-8")

    monkeypatch.setattr(paths, "_old_default_home", lambda: str(old))
    monkeypatch.setattr(paths, "_default_home", lambda: str(new))
    monkeypatch.setattr(paths, "read_pointer", lambda: "")
    monkeypatch.delenv("BILI_NOTIFY_HOME", raising=False)

    moved = paths.migrate_from_old_home()
    assert "config.yaml" in moved, "配置没搬回来"
    assert (new / "config.yaml").read_text(encoding="utf-8") == "appid: '123'\n"
    assert (new / "state.json").exists(), "订阅数据没搬回来"
    assert (new / "logs" / "bot.log").exists(), "日志没搬回来"


def test_migrate_skipped_when_user_picked_path(tmp_path, monkeypatch):
    """用户在面板里自己选过位置 / 设了环境变量时，不许乱搬。"""
    old = tmp_path / "oldhome"
    old.mkdir()
    (old / "config.yaml").write_text("a: 1\n", encoding="utf-8")
    monkeypatch.setattr(paths, "_old_default_home", lambda: str(old))
    monkeypatch.setattr(paths, "_default_home", lambda: str(tmp_path / "new"))
    monkeypatch.setattr(paths, "read_pointer", lambda: str(tmp_path / "mine"))
    monkeypatch.delenv("BILI_NOTIFY_HOME", raising=False)
    assert paths.migrate_from_old_home() == [], "用户选过位置还去搬，会搬错"


# ------------------------------------------------------- 1. 机器人退出码 1

def test_bot_no_hardcoded_config_path():
    """bot.py 不许再用相对路径 "config.yaml" 当默认配置路径。

    v1.96 把配置挪出 src/ 之后，以 cwd=src/ 启动的 bot.py 用相对路径
    永远找不到文件 → sys.exit(1)。填完凭据后机器人立刻退出、无限重启，
    而提示还说"不是凭据问题"（确实不是，是压根没找到配置文件）。
    """
    src = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "bot.py")
    with open(src, encoding="utf-8") as f:
        code = f.read()
    assert 'get("BOT_CONFIG", "config.yaml")' not in code, \
        "bot.py 还在用相对路径找 config.yaml"
    assert 'paths.config_path()' in code, \
        "bot.py 应改用 paths.config_path()"


def test_bot_config_path_points_to_data_home():
    """bot.py 拿到的配置路径，必须和面板写配置的地方是同一个。"""
    # 没有 BOT_CONFIG 时，两条路都要落到数据目录
    os.environ.pop("BOT_CONFIG", None)
    assert os.path.abspath(paths.config_path()) \
        .startswith(os.path.abspath(paths.data_home()) + os.sep)


def test_config_example_still_findable():
    """data/ 放进程序目录后，config.example.yaml 仍然找得到。"""
    p = paths.asset("config.example.yaml")
    assert os.path.exists(p), "config.example.yaml 找不到了：%s" % p


# -------------------------------------------------- 4. 监督进程不算残留

def test_supervisor_pids_recognized(monkeypatch):
    """start.py（管家）要能被认出来，不能算进残留。"""
    import cleanup
    fake = [
        (111, "python C:\\app\\src\\start.py"),
        (222, "python C:\\app\\src\\webui.py --port 8088"),
        (333, "python C:\\app\\src\\bot.py --parent 111"),
    ]
    monkeypatch.setattr(cleanup, "list_ours", lambda: fake)
    got = cleanup.supervisor_pids()
    assert got == [111], "管家没被认出来（或误认了别的）：%s" % got


def test_supervisor_pids_empty_when_no_supervisor(monkeypatch):
    """没人跑 start.py 时（比如只单独开面板），不该凭空认出一个。"""
    import cleanup
    monkeypatch.setattr(cleanup, "list_ours",
                        lambda: [(9, "python C:\\app\\src\\webui.py")])
    assert cleanup.supervisor_pids() == []


def test_webui_keep_pids_includes_supervisor(monkeypatch):
    """运行状态页的 keep 必须包含管家，否则永远显示「1 个残留」。"""
    import cleanup as _cl
    monkeypatch.setattr(_cl, "supervisor_pids", lambda: [4242])
    monkeypatch.setattr(_cl, "all_ours", lambda: [1, 4242, 7777])
    import webui
    monkeypatch.setattr(webui, "PARENT_PID", 4242)
    monkeypatch.setattr(webui.heartbeat, "read", lambda: {"pid": 7777})
    monkeypatch.setattr(webui, "_cl", _cl, raising=False)
    keep = webui._keep_pids()
    assert 4242 in keep, "管家不在 keep 里 → 被当成残留"
    assert 7777 in keep, "正常跑的机器人不该算残留"
    assert os.getpid() in keep


# ------------------------------------------------- 3. 「现在去填写」跳转

def test_guide_button_goes_to_cred_not_setup():
    """「👋 第一次使用？」的按钮要跳到 AppID 真正所在的「🔐 凭据」。"""
    js = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "static", "app.js")
    with open(js, encoding="utf-8") as f:
        code = f.read()
    i = code.index("'goto-setup'")
    block = code[i:i + 400]
    assert "'cred'" in block, "还跳到别的板块：%s" % block[:200]
    assert "cAppid" in block, "跳过去了但没聚焦到 AppID 输入框"
    # AppID 输入框确实在 cred 板块
    html = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "templates", "index.html")
    with open(html, encoding="utf-8") as f:
        doc = f.read()
    assert 'data-sec="cred"' in doc and 'id="cAppid"' in doc


def test_cappid_is_in_cred_section():
    """AppID 输入框必须在 cred 板块内（防止以后改模板又跳错）。"""
    html = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "templates", "index.html")
    with open(html, encoding="utf-8") as f:
        doc = f.read()
    # ⚠️ 用 rfind：导航栏里也有 data-sec="cred"，真正的板块定义在后面
    cred = doc.rindex('data-sec="cred"')
    # 找下一个板块起点，cAppid 必须落在这两者之间
    nxt = doc.find('data-sec="setup"', cred)
    assert cred < doc.index('id="cAppid"') < nxt, \
        "AppID 输入框不在 cred 板块里了"


# --------------------------------------------- 端到端：三个进程不该有残留

def test_proc_info_no_leftover_for_normal_trio(monkeypatch):
    """start.py + webui(自己) + bot 三个进程时，残留必须是 0。"""
    import cleanup as _cl
    me = os.getpid()
    monkeypatch.setattr(_cl, "all_ours", lambda: [me, 4242, 7777])
    monkeypatch.setattr(_cl, "supervisor_pids", lambda: [4242])
    import webui
    monkeypatch.setattr(webui, "PARENT_PID", 4242)
    monkeypatch.setattr(webui.heartbeat, "read", lambda: {"pid": 7777})
    info = webui._proc_info()
    assert info["leftover"] == 0, "正常三进程被报成残留：%s" % info
    assert info["total"] == 3


def test_proc_info_still_reports_real_leftover(monkeypatch):
    """真的多开一个实例时，仍然要报出来（不能为了修而把真残留也吞了）。"""
    import cleanup as _cl
    me = os.getpid()
    monkeypatch.setattr(_cl, "all_ours", lambda: [me, 4242, 7777, 9999])
    monkeypatch.setattr(_cl, "supervisor_pids", lambda: [4242])
    import webui
    monkeypatch.setattr(webui, "PARENT_PID", 4242)
    monkeypatch.setattr(webui.heartbeat, "read", lambda: {"pid": 7777})
    info = webui._proc_info()
    assert info["leftover"] == 1 and info["leftover_pids"] == [9999], \
        "真残留没报出来：%s" % info
