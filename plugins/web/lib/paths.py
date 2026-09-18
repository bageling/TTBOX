"""paths.py — TTBOX Web 运行期路径单点真源（A-PATH-5 / B-CONST-2）。

与 C++ core 侧 ``core/src/common/Paths.hpp`` **同值**；跨语言无法 include ⇒ 以
docs/protocols/config-path-env-registry.md 登记 + scripts/ttbox_conventions_gate.sh
同值断言防漂移。

取值链（A-PATH-2 / A-PATH-4）：
  · 运行根前缀统一经 ``TTBOX_PREFIX``（默认 ``/opt/ttbox``）派生；
  · **仓库/release 树内** 的目录（scripts）经 ``Path(__file__)`` 相对派生（A-PATH-3），
    在开发机（仓库根）与板端（release 树 current/）都成立，杜绝硬编码 FHS 绝对路径。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 跨语言同值常量（与 Paths.hpp 逐值一致 —— 门禁断言；改动须同步登记表 §三）
# ---------------------------------------------------------------------------
IPC_SOCKET_DEFAULT = "/run/ttbox/core.sock"
MOUSE_CMD_SOCK_DEFAULT = "/run/ttbox-mouse-passthrough/cmd.sock"
MOUSE_EVENT_SOCK_DEFAULT = "/run/ttbox-mouse-passthrough/event.sock"

# 面板监听端口默认（B-CONST-1）：web LISTEN_PORT / 配网引导 URL / 验收脚本共用。
# 真源 = plugins/web/bin/ttbox-web.py::LISTEN_PORT（改端口改这里 + 该常量；门禁断言同值）。
WEB_PORT_DEFAULT = 8000

# FHS 运行根（A-PATH-4）：板端 = /opt/ttbox；env 可覆盖（联调/排障）。
_DEFAULT_PREFIX = "/opt/ttbox"


def ttbox_prefix() -> str:
    """运行根前缀（A-PATH-4）。默认 /opt/ttbox，env TTBOX_PREFIX 可覆盖。"""
    return os.environ.get("TTBOX_PREFIX", _DEFAULT_PREFIX)


def repo_root() -> str:
    """仓库 / release 树根（A-PATH-3 相对派生）。

    本文件位于 ``<root>/plugins/web/lib/paths.py`` ⇒ parents[3] = <root>。
    开发机 <root> = 仓库根；板端 <root> = release 树（``current/``）。
    """
    return str(Path(__file__).resolve().parents[3])


def scripts_dir() -> str:
    """scripts 目录：TTBOX_SCRIPTS_DIR > <repo_root>/scripts。

    用相对派生而非 FHS 绝对路径：秒级联调机（仓库根）与板端（release tree）都成立；
    ``wifi_manager`` / ``edid`` 工具链即在此目录。
    """
    env = os.environ.get("TTBOX_SCRIPTS_DIR")
    if env:
        return env
    return str(Path(repo_root()) / "scripts")


def config_dir() -> str:
    """运行期配置目录：TTBOX_CONFIG_DIR > <prefix>/config。"""
    return os.environ.get("TTBOX_CONFIG_DIR", ttbox_prefix() + "/config")


def presets_dir() -> str:
    """Web 预设目录：TTBOX_PRESETS_DIR > <prefix>/presets。"""
    return os.environ.get("TTBOX_PRESETS_DIR", ttbox_prefix() + "/presets")


def web_credentials_file() -> str:
    """Web 云端凭据文件：TTBOX_WEB_CREDENTIALS > <config_dir>/default.json。

    ★ 这是 **web 拥有的部署凭据文件**（cloud.app_secret / license_base_url），
    **不是**运行期配置真源（运行期配置 = Core，经 IPC）。历史命名 default.json 保留
    只为与部署/验收脚本（scripts/ttbox_m207_accept.py）写入路径一致。
    """
    return os.environ.get(
        "TTBOX_WEB_CREDENTIALS", config_dir() + "/default.json"
    )


def hdmirx_edid_tool() -> str:
    """TTBOX 自有 EDID 工具：TTBOX_HDMIRX_EDID > <scripts_dir>/edid/hdmirx_edid.py。"""
    return os.environ.get(
        "TTBOX_HDMIRX_EDID", scripts_dir() + "/edid/hdmirx_edid.py"
    )


def motion_profiles_dir() -> str:
    """动作曲线目录：TTBOX_MOTION_PROFILES_DIR > <prefix>/config/motion-profiles。"""
    return os.environ.get(
        "TTBOX_MOTION_PROFILES_DIR", ttbox_prefix() + "/config/motion-profiles"
    )


def ipc_socket() -> str:
    """IPC socket：TTBOX_IPC_SOCKET > IPC_SOCKET_DEFAULT。"""
    return os.environ.get("TTBOX_IPC_SOCKET", IPC_SOCKET_DEFAULT)
