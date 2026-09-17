# 已弃用：unit 副本已删除（DEP-07 / T1.05）

本目录**不再存放任何 `.service` 文件**。

## 为什么（探针 N2-2：这份副本是"带病第三份"）

`ttbox-usbproxy.service` 在这里曾有一份副本，是仓库里的**第三份** usbproxy unit，且
`:5` 写的是 `StartLimitIntervalSec=0`——**主动关闭限流**（正是"82 次无限重启"级别的配置）；
`:9/:18` 的路径指 `/opt/ttbox/usbproxy`（缺 `current/`），并且没有 `User=`。

关键事实：**两份现存副本各有不同的病，没有一份是对的**——

| 副本（删除前） | 病 |
|---|---|
| `deploy/systemd/ttbox-usbproxy.service` | 路径未随 releases/current 布局修正（探针时 `:7-8,14-16,25` 与实装不符） |
| `usbproxy/systemd/ttbox-usbproxy.service`（本目录） | `StartLimitIntervalSec=0` 限流关死 + 路径旧 + 无 `User=` |

所以收敛动作**不是"挑一份对的留下"，而是**以 `deploy/systemd/` 为骨架**同时**修正路径、
补齐 `StartLimitBurst=5/StartLimitIntervalSec=300`（写 `[Unit]` 段）与 `User=`。

## 唯一权威源

```
deploy/systemd/ttbox-usbproxy.service
```

收敛后该单份**同时**满足（T1.05 验收③）：

1. `ExecStart` 经 `current` 解析后可执行（`/opt/ttbox/current/usbproxy/board/run-ttbox-usb-proxy.sh`）；
2. `StartLimitBurst=5` + `StartLimitIntervalSec=300` 位于 `[Unit]` 段；
3. 含 `User=`（`root`）+ `Group=ttbox`。

## 部署时从哪来（DEP-06 / DEP-01）

unit 是**版本产物的一部分**，随发布树交付：

```
/opt/ttbox/current/deploy/systemd/ttbox-usbproxy.service   # current -> releases/<ver>/
```

- 安装/切换：`scripts/ttbox_release_install.sh <ver> <payload_dir> --activate`
- 幂等自愈：`scripts/ttbox_ensure_services.sh`（unit 源 = `/opt/ttbox/current/deploy/systemd/`）

> 请勿再从本目录拷贝 `*.service` 到 `/etc/systemd/system/`。
