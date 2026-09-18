"""TTBOX Web 插件包。

S1-2026-09-18（审查发现 N01）：本文件原先 re-export
``install_framework_api``。``framework_api.py`` 已随交付减法移出出货包
（见 ``scripts/ttbox_fhs_init.sh`` 的 payload 排除段，以及
``plugins/web/bin/ttbox-web.py`` 里已摘除的注册点），所以这里不再导入它 ——
否则出货包内任何 ``import plugins.web`` 都会因缺模块而 ImportError。

本文件**必须保留**：``plugins`` 是包，``plugins.system_host`` 等子模块
要靠它拼出包路径。
"""

__all__: list[str] = []
