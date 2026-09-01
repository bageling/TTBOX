# TTBOX 更新引擎 (Update Engine)

## 1. 概述

TTBOX Update Engine 是设备端负责系统更新的核心组件。它独立于 TTBOX Core 运行，不与任何其他组件耦合。

## 2. 架构

```
TTBOX 设备
 │
 ├── TTBOX Core (systemd: ttbox-core)
 │    └── 与 Update Engine 无关
 │
 ├── TTBOX Gateway (systemd: ttbox-web)
 │    └── /api/update/* → 委托给 Update Engine
 │
 └── TTBOX Update Engine (systemd: ttbox-update)
      ├── Update State Machine
      ├── OTA Client
      ├── OTG Scanner
      ├── Package Verifier
      ├── Backup Manager
      ├── Apply Engine
      └── Rollback Manager
```

## 3. 组件职责

| 组件 | 职责 |
|------|------|
| Gateway | 接收 HTTP 请求，转发到 Update Engine |
| Update State Machine | 管理更新状态，持久化状态 |
| OTA Client | 连接 Update Server，检查/下载更新 |
| OTG Scanner | 扫描 USB 设备，查找更新包 |
| Package Verifier | 验证 SHA256 + Ed25519 签名 |
| Backup Manager | 备份当前版本，管理回滚点 |
| Apply Engine | 解压、安装、配置迁移 |
| Rollback Manager | 故障时恢复旧版本 |

## 4. 更新状态机

```
                    ┌─────────────┐
                    │    IDLE     │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  CHECKING   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
     ┌────────▼───┐  ┌─────▼──────┐  ┌─▼──────────┐
     │ NO_UPDATE  │  │ DOWNLOADING│  │ OTG_FOUND  │
     └────────────┘  └─────┬──────┘  └─────┬──────┘
                           │               │
                    ┌──────▼──────┐  ┌─────▼──────┐
                    │  VERIFYING  │  │ VERIFYING  │
                    └──────┬──────┘  └─────┬──────┘
                           │               │
                    ┌──────▼──────┐  ┌─────▼──────┐
                    │   STAGING   │  │  STAGING   │
                    └──────┬──────┘  └─────┬──────┘
                           │               │
                    ┌──────▼───────────────▼──────┐
                    │          READY               │
                    └──────┬──────────────────────┘
                           │
                    ┌──────▼──────┐
                    │  APPLYING   │
                    └──────┬──────┘
                           │
               ┌───────────┼───────────┐
               │           │           │
        ┌──────▼───┐  ┌────▼────┐  ┌──▼──────────┐
        │  SUCCESS  │  │  FAILED │  │ ROLLBACKING │
        └──────┬────┘  └────┬────┘  └──────┬──────┘
               │            │              │
        ┌──────▼──────┐     │      ┌───────▼───────┐
        │ COMMITTED   │     │      │  ROLLED_BACK  │
        └─────────────┘     │      └───────────────┘
                             │
                    ┌────────▼────────┐
                    │  ERROR_REPORT   │
                    └─────────────────┘
```

## 5. 状态持久化

状态文件：`/var/lib/ttbox/update/update_state.json`

```json
{
  "state": "IDLE",
  "current_version": "0.1.0",
  "previous_version": "0.0.9",
  "last_update_time": "2026-09-01T10:00:00Z",
  "attempted_version": "",
  "attempted_channel": "",
  "error_count": 0,
  "last_error": "",
  "rollback_available": true,
  "backup_path": "/var/lib/ttbox/update/backup/v0.1.0/"
}
```

## 6. 断电保护

| 阶段 | 断电后行为 | 数据完整性 |
|------|-----------|-----------|
| CHECKING | 重新检查 | ✅ 无损 |
| DOWNLOADING | 重新下载 | ✅ 可恢复下载 |
| VERIFYING | 重新验证 | ✅ 下载包可重用 |
| STAGING | 清理 staging | ✅ staging 不持久 |
| READY | 继续更新 | ✅ 状态持久化 |
| APPLYING | 开机后检查 → 回滚 | ⚠️ 可能部分应用 |
| HEALTH_CHECK | 重新健康检查 | ✅ 无损 |
| COMMITTED | 完成 | ✅ 已完成 |

### 关键保护

`APPLYING` 阶段是唯一可能损坏系统的阶段。保护措施：

1. 应用前创建完整备份
2. 应用中使用原子操作（先复制、再切换符号链接）
3. 应用完成后创建健康检查标记
4. 开机时检测到 `APPLYING` 状态 → 自动进入回滚

## 7. 文件系统布局

```
/etc/ttbox/                 # 配置（应用后更新）
├── ttbox.conf
├── update.conf
└── hardware.json

/var/lib/ttbox/             # 持久数据
├── update/
│   ├── state.json          # 更新状态
│   ├── backup/             # 版本备份
│   │   └── v0.1.0/
│   ├── staging/            # 更新暂存区
│   │   └── v1.0.0/
│   ├── downloads/          # 下载缓存
│   └── trusted_keys/       # 信任的公钥
│       └── key-20260901.pub
├── models/                 # AI 模型
└── profiles/               # 用户预设

/run/ttbox/                 # 运行时临时数据
└── update.lock             # 更新锁

/usr/local/
├── bin/ttbox-core
├── bin/ttbox-update        # Update Engine 主程序
└── share/ttbox/
    └── web/                # Web 前端
```

## 8. Update Engine 接口

### 内部 Unix Socket

监听 `/var/run/ttbox/update.sock`，Gateway 通过此接口调用 Update Engine。

**命令：**

| 命令 | 参数 | 说明 |
|------|------|------|
| GET_STATUS | — | 获取更新状态 |
| CHECK_UPDATE | — | 检查 OTA 更新 |
| SCAN_OTG | — | 扫描 USB 更新 |
| START_UPDATE | version | 开始更新 |
| CANCEL_UPDATE | — | 取消更新 |
| ROLLBACK | — | 回滚到上一版本 |
| GET_LOG | — | 获取更新日志 |

## 9. 更新锁

```
/run/ttbox/update.lock
```

- 防止同时运行多个更新
- 防止更新过程中重启设备
- 定期检查锁是否过期（异常断电后自动释放）

## 10. 日志

```
/var/log/ttbox/update.log
```

日志轮转：每周轮转，保留 4 周。

## 11. 健康检查

更新后检查：

1. Core 进程是否运行
2. Gateway 是否响应
3. 模型文件是否存在
4. 关键配置是否正确
5. 版本号是否匹配

健康检查通过后提交更新。