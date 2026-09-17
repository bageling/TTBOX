"""已废弃（DEP-07 / T1.05 去重）：唯一权威源 = deploy/systemd/。

历史背景
========
本模块曾通过 CORE_UNIT / SUPERVISOR_UNIT 两个字符串"再生成"一套 ttbox-core /
supervisor 的 systemd unit 模板。其内容属于 **FHS 迁移之前的旧布局**：

    ExecStart=/opt/ttbox/core/current/ttbox-core
    Environment=TTBOX_CONFIG_PATH=/opt/ttbox/config/current
    ReadWritePaths=/opt/ttbox/core /opt/ttbox/models /opt/ttbox/config /opt/ttbox/data ...

这与 DEP-07 收敛后的权威 unit（`deploy/systemd/ttbox-*.service`，路径经
`/opt/ttbox/current` + 客户数据目录 `/var/lib/ttbox`）**直接冲突**，若继续存在即构成
"第二权威源"，会导致 unit 漂移（同一服务两套定义）。

处置（引用点判定）
==================
全仓 grep `CORE_UNIT | SUPERVISOR_UNIT | systemd_units`：唯一引用点是
`platform/tests/test_systemd_units.py`（且该用例本身断言的即是上述旧布局）。
**无任何生产/部署路径引用本模块** ⇒ 已按"改为停用（no-op）"处置：

    * 不再产出任何 unit 文本（两个常量均置空串）；
    * 保留常量名仅为兼容旧 import；下游若仍读取将得到空串，从而**显式暴露**
      而不是静默沿用旧布局；
    * 新增唯一权威源声明 -D 见下。

权威源
======
任何需要 systemd unit 的地方（安装、巡检、还原）一律使用 `deploy/systemd/`：
`ttbox-core / ttbox-web / ttbox-preview / ttbox-usbproxy / ttbox-edid /
ttbox-ensure(.service/.timer)`。安装入口见 `scripts/ttbox_fhs_init.sh` 与
`scripts/ttbox_ensure_services.sh`。
"""

# 兼容占位：不再包含任何 unit 文本（no-op）。见上方"处置"。
CORE_UNIT = ""
SUPERVISOR_UNIT = ""
