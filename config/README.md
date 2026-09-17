# TTBOX 配置目录说明

板端真源：`/opt/ttbox/config/default.json`（core 以 `--config` 指定；web 每请求热读，
`TTBOX_CONFIG` 环境变量可覆盖路径）。本目录的 `*.json` 是**样例/模板**，部署时合并进
板端 default.json。

> ⚠️ **凭据纪律**：`cloud.example.json` 中的 `app_secret` / `license_base_url` 一律是
> **占位符**。真实云端凭据**唯一落点** = 板端 `/opt/ttbox/config/default.json` 与本地
> 密钥库，**严禁写入本目录 / 严禁入库**（gitignore 已拦截 `config/cloud.json` /
> `config/cloud.local.json`）。

## `cloud` 段（M2.07 云端卡密激活）

来源样例：`cloud.example.json`。字段：

| 字段 | 说明 | 缺省 |
|---|---|---|
| `license_base_url` | 云端 License-SaaS 根地址（quick tunnel，**URL 重启会变**，改后下一次请求立即生效，web 层每请求热读，无需重启） | **无编译期缺省**；须由板端配置提供 |
| `app_key` | 应用标识 | `ttbox` |
| `app_secret` | HMAC-SHA256 签名密钥 | **无编译期缺省**；真值只在板端 `/opt/ttbox/config/default.json` 与本地密钥库，**严禁入库** |

要求：

- **权限（实际值）**：default.json 含 app_secret，属敏感凭据 ⇒ **目录 `root:ttbox 0775`、
  文件 `root:ttbox 0640`**（`scripts/ttbox_fhs_init.sh` 显式幂等 `chgrp ttbox` + `chmod 0640`）。
  世界不可读（收敛泄露面）、组可读（`User=ttbox` 的 web 读得到）；写侧原子写必须**保留属组**
  （见 `scripts/ttbox_m207_accept.py::atomic_write_json`），否则 root 重写会把属主重置为
  `root:root` ⇒ web EACCES ⇒ 云端凭据丢失（激活 502）。
- app_secret 缺失/空 ⇒ card_login 直接返回"云端凭据未配置"（fail-closed，且**无编译期缺省**兜底）。
- web 层心跳会话态落 `/opt/ttbox/config/cloud_session.json`（0600，web 启动自动管理，
  含 card_key 明文 —— 自动重登前提，风险已在 m2.07-impl-spec.md §8-2 登记）。

## 其它样例

- `hardware_display.json` / `hdmirx_edid_identity.json`：显示与 EDID 身份配置。
- `yolo261n-rk3588.json`：RKNN 模型描述样例。
