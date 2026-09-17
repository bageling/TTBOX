# 已弃用：unit 副本已删除（DEP-07 / T1.05）

本目录**不再存放任何 `.service` 文件**（`preview.json` 仍在此，属插件配置，保留）。

## 为什么

`ttbox-preview.service` 在这里曾有一份副本，与权威源 `deploy/systemd/ttbox-preview.service`
**双份并存**。两份的 `ExecStart` 都指向旧 `current/plugins/preview` 布局，且此副本把
`StartLimitBurst/StartLimitIntervalSec` 写在 `[Service]` 段——systemd 会静默忽略该段位，
限流失效。按 DEP-07 收敛原则——**仓库内每 unit 有且仅有一份**——此副本已删除。

> 板端注意（探针 N1）：目标板当前**根本没装** `ttbox-preview.service`（只有 core/edid/usbproxy/web
> 四个 unit）。它没炸不是因为没病，是因为没上场；一旦按 DEP-03 的 `sync_tree` 正常部署，
> 旧副本会立刻变成"第 4 个 203/EXEC"。故本次一并收敛。

## 唯一权威源

```
deploy/systemd/ttbox-preview.service
```

## 部署时从哪来（DEP-06 / DEP-01）

unit 是**版本产物的一部分**，随发布树交付：

```
/opt/ttbox/current/deploy/systemd/ttbox-preview.service   # current -> releases/<ver>/
```

- 安装/切换：`scripts/ttbox_release_install.sh <ver> <payload_dir> --activate`
- 幂等自愈：`scripts/ttbox_ensure_services.sh`（unit 源 = `/opt/ttbox/current/deploy/systemd/`）

> 请勿再从本目录拷贝 `*.service` 到 `/etc/systemd/system/`。
