# TTBOX 更新系统架构

## 概述

TTBOX 更新系统支持 OTA（网络更新）和 OTG（USB 更新）两种更新方式，共用同一套更新引擎。

## 架构

```
                    TTBOX Update Engine
                            ▲
                    ┌───────┴───────┐
                    │               │
                   OTA             OTG
                    │               │
              Update Server      USB Storage
                    │               │
                    └───────┬───────┘
                            │
                    Verify / Stage
                            │
                     Apply / Rollback
```

## 更新组件

| 组件 | 类型 | 说明 |
|------|------|------|
| Core | 固件 | C++ 核心程序 |
| Web | 固件 | Python Web 后端 |
| Frontend | 固件 | 前端 JS/CSS/HTML |
| Model | 数据 | AI 模型文件 |
| System | 固件 | 系统镜像 |
| Firmware | 固件 | 硬件固件 |

## 当前状态

| 组件 | OTA | OTG | 回滚 |
|------|-----|-----|------|
| Core | 🔵 PLANNED | 🔵 PLANNED | 🔵 PLANNED |
| Web | 🔵 PLANNED | 🔵 PLANNED | 🔵 PLANNED |
| Frontend | 🔵 PLANNED | 🔵 PLANNED | 🔵 PLANNED |
| Model | 🔵 PLANNED | 🔵 PLANNED | 🔵 PLANNED |
| System | ⚪ RESERVED | ⚪ RESERVED | ⚪ RESERVED |
| Firmware | ⚪ RESERVED | ⚪ RESERVED | ⚪ RESERVED |

## 更新状态机

```
IDLE → CHECKING → AVAILABLE → DOWNLOADING → VERIFYING → STAGING → READY → APPLYING → REBOOTING → HEALTH_CHECK → SUCCESS
                                                                                          ↓
                                                                                     FAILED → ROLLING_BACK → ROLLED_BACK
```

## 包格式

| 字段 | 类型 | 说明 |
|------|------|------|
| product | string | 产品名称（TTBOX） |
| version | string | 版本号 |
| channel | string | 更新通道（stable/beta/dev） |
| min_version | string | 最低兼容版本 |
| hardware | string | 目标硬件 |
| components | Component[] | 更新组件列表 |
| sha256 | string | 包校验 |
| signature | string | 数字签名 |
| release_notes | string | 发布说明 |

## 安全

- 通信：HTTPS
- 校验：SHA256
- 签名：数字签名
- 验证：公钥（设备端存储）

## 当前实现状态

- ⚪ 尚未开始实现
- 此架构文档为未来开发提供设计蓝图
- 当前产品版本：0.1.0