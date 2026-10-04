# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""从环境变量（或 .env 文件）读取凭据，让密钥可以完全不落盘。

为什么需要这个模块
------------------
原来凭据只有一条来源：config.yaml。哪怕 secretbox 把 AppSecret 存成密文，
明文终究会经过这个文件（面板填一次 → 加密落盘 → 运行时解密回内存）。
对要把代码传到公开仓库的场景来说，最好的办法是让密钥**根本不写在仓库里**。

本模块提供第二条来源，且优先级更高：

    环境变量 / .env  >  config.yaml

即：只要环境变量里配了，就永远以环境变量为准，config.yaml 里那一份被忽略。
这样即便有人误把 config.yaml 提交上去，里面也不含真密钥。

支持的环境变量
--------------
    ONOBN_APPID         QQ 开放平台的 AppID（纯数字）
    ONOBN_APP_SECRET    QQ 开放平台的 AppSecret
    ONOBN_BILI_COOKIE   B 站登录 Cookie（可选，只影响登录态接口）
    ONOBN_DATA_DIR      数据目录位置（可选，等价于面板里的"存放位置"）
    ONOBN_PANEL_PORT    面板端口（可选，默认 8088）
    ONOBN_PANEL_PASSWORD 面板访问口令（可选）

.env 文件
---------
放在项目根目录（与「启动机器人.bat」同级）。格式是 KEY=VALUE，一行一个，
`#` 开头是注释。**已在进程环境变量里的不会被 .env 覆盖**——真实环境变量
优先，方便部署时用系统环境变量覆盖本地文件。

⚠️ .env 里是真密钥，必须放进 .gitignore（已放）。仓库里只留 .env.example，
   里面全是占位符。
"""
import os

# 环境变量名 → config.yaml 里的字段名。改这里就能同时改掉两边。
FIELD_ENV = {
    "appid": "ONOBN_APPID",
    "secret": "ONOBN_APP_SECRET",
    "bili_cookie": "ONOBN_BILI_COOKIE",
}

# 不写进 config.yaml、只供运行时读取的环境变量
ENV_DATA_DIR = "ONOBN_DATA_DIR"
ENV_PANEL_PORT = "ONOBN_PANEL_PORT"
ENV_PANEL_PASSWORD = "ONOBN_PANEL_PASSWORD"
ENV_LOGO_URL = "ONOBN_LOGO_URL"

# 占位符：填了等于没填，不做特殊判断会把示例值当成真密钥
PLACEHOLDERS = ("", "xxx", "xxxx", "your_appid", "your_app_secret",
                "changeme", "请填写", "your_", "example")

_DOT_ENV_LOADED = False


def _candidate_dotenv_paths():
    """按可能性依次找 .env：环境变量指定的 → 数据目录 → 源码目录 → 项目根。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)          # 项目根（src 的上级）
    cands = []
    env_override = os.environ.get("ONOBN_ENV_FILE")
    if env_override:
        cands.append(env_override)
    try:
        import paths
        # ⚠️ 这里曾经写成 paths.data_dir() —— paths 里根本没有这个函数，
        #    抛的 AttributeError 被下面的 except 悄悄吞掉，结果 .env 永远找不到，
        #    端到端实测才发现。现在两个名字都认，取不到就跳过。
        home = getattr(paths, "data_home", None) or getattr(paths, "data_dir", None)
        if home is not None:
            cands.append(os.path.join(home(), ".env"))
    except Exception:
        pass
    cands.append(os.path.join(here, ".env"))
    cands.append(os.path.join(root, ".env"))
    return cands


def load_dotenv(force: bool = False) -> str:
    """把 .env 里的键值对读进 os.environ。返回实际加载的文件路径（没找到则空串）。

    已存在的环境变量不会被覆盖——这是故意的：部署环境（systemd / Docker）
    注入的真值应当压过仓库里的本地文件。
    """
    global _DOT_ENV_LOADED
    if _DOT_ENV_LOADED and not force:
        return ""
    _DOT_ENV_LOADED = True

    for path in _candidate_dotenv_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip()
                    # 去掉成对引号，值里带 = 的情况 partition 已正确处理
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                        val = val[1:-1]
                    if key and key not in os.environ:
                        os.environ[key] = val
        except OSError:
            continue
        return path
    return ""


def env_value(env_name: str) -> str:
    """读一个环境变量，去掉引号与占位符。没配或填的是占位符则返回空串。"""
    load_dotenv()
    val = (os.environ.get(env_name) or "").strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        val = val[1:-1]
    val = val.strip()
    if val.lower() in PLACEHOLDERS:
        return ""
    return val


def creds_from_env() -> dict:
    """返回环境变量里配好的凭据字段（只含真正填了的）。"""
    load_dotenv()
    out = {}
    for field, env_name in FIELD_ENV.items():
        val = env_value(env_name)
        if val:
            out[field] = val
    return out


def apply_env(cfg: dict) -> dict:
    """把环境变量里的凭据盖到配置上（环境变量优先）。原字典不被修改。"""
    if not isinstance(cfg, dict):
        return {}
    out = dict(cfg)
    out.update(creds_from_env())
    return out


def env_sourced_fields() -> list:
    """当前有哪些凭据字段来自环境变量——面板据此提示"这项由环境变量提供"。"""
    return sorted(creds_from_env().keys())


def data_dir_from_env() -> str:
    """数据目录：ONOBN_DATA_DIR。没配返回空串（调用方回落默认逻辑）。"""
    return env_value(ENV_DATA_DIR)


def panel_port_from_env() -> int:
    """面板端口：ONOBN_PANEL_PORT。没配或不是合法端口返回 0。"""
    raw = env_value(ENV_PANEL_PORT)
    if not raw:
        return 0
    try:
        port = int(raw)
    except ValueError:
        return 0
    return port if 1 <= port <= 65535 else 0


def panel_password_from_env() -> str:
    """面板访问口令：ONOBN_PANEL_PASSWORD。"""
    return env_value(ENV_PANEL_PASSWORD)


def logo_url_from_env() -> str:
    """品牌 logo 的远程地址：ONOBN_LOGO_URL。没配返回空串（用内置默认）。"""
    return env_value(ENV_LOGO_URL)
