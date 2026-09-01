# TTBOX API 实现矩阵

## 状态定义

| 状态 | 含义 |
|------|------|
| REAL | 完整实现，通过 Core → IPC → Gateway，返回真实数据 |
| PARTIAL | 部分实现，有 Gateway 但数据不完整或来自模拟 |
| NOT_IMPLEMENTED | 接口存在但返回假数据 |
| LEGACY | 历史遗留，不应在新代码中使用 |
| BROKEN | 接口存在但功能异常 |

## 矩阵

### Dashboard

| API | UI | Contract | Gateway | IPC | Core | 状态 | 备注 |
|-----|----|----------|---------|-----|------|------|------|
| GET /api/state | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | 完整运行状态 |
| GET /api/system | ✅ | ✅ | ✅ | — | — | REAL | CPU/内存/温度/存储 |
| GET /api/preview.jpg | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | MJPEG 预览 |
| GET /api/preview.mjpg | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | MJPEG 流 |

### Runtime

| API | UI | Contract | Gateway | IPC | Core | 状态 | 备注 |
|-----|----|----------|---------|-----|------|------|------|
| POST /api/control/start | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | 启动 AI |
| POST /api/control/stop | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | 停止 AI |
| GET /api/config | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | 获取配置 |
| PUT /api/config | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | 更新配置 |

### Models

| API | UI | Contract | Gateway | IPC | Core | 状态 | 备注 |
|-----|----|----------|---------|-----|------|------|------|
| GET /api/models | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | 列出模型 |
| POST /api/models/import | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | 导入模型 |
| POST /api/models/delete | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | 删除模型 |
| POST /api/models/select | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | 切换模型 |
| POST /api/models/class-names | ✅ | ✅ | ✅ | — | — | REAL | 编辑类别名 |
| POST /api/models/rknn-concurrency | ✅ | ✅ | ✅ | — | ✅ | REAL | NPU 并发 |
| POST /api/models/bind-preset | ✅ | — | ✅ | — | — | NOT_IMPLEMENTED | 返回假数据 |
| POST /api/models/game-profile | ✅ | — | ✅ | — | — | NOT_IMPLEMENTED | 返回假数据 |
| POST /api/models/remote-frame-format | ✅ | — | ✅ | — | — | NOT_IMPLEMENTED | 返回假数据 |
| POST /api/models/hailo-pipeline-depth | ✅ | — | ✅ | — | — | NOT_IMPLEMENTED | 返回假数据 |

### Presets

| API | UI | Contract | Gateway | IPC | Core | 状态 | 备注 |
|-----|----|----------|---------|-----|------|------|------|
| GET /api/presets | ✅ | ✅ | ✅ | — | — | REAL | 列出预设 |
| POST /api/presets | ✅ | ✅ | ✅ | — | — | REAL | 保存/删除/重命名 |
| POST /api/presets/load | ✅ | ✅ | ✅ | ✅ | ✅ | REAL | 加载预设 |

### Hardware

| API | UI | Contract | Gateway | IPC | Core | 状态 | 备注 |
|-----|----|----------|---------|-----|------|------|------|
| GET /api/hardware/display | ✅ | ✅ | ✅ | — | — | REAL | 显示器信息 |
| PUT /api/hardware/display | ✅ | ✅ | ✅ | — | — | REAL | 更新显示器配置 |
| GET /api/hardware/mouse | ✅ | ✅ | ✅ | — | — | REAL | 鼠标硬件信息 |
| PUT /api/hardware/mouse | ✅ | ✅ | ✅ | ✅ | — | REAL | 更新鼠标配置 |

### Network

| API | UI | Contract | Gateway | IPC | Core | 状态 | 备注 |
|-----|----|----------|---------|-----|------|------|------|
| GET /api/network/wifi | ✅ | ✅ | ✅ | — | — | REAL | Wi-Fi 状态 |
| POST /api/network/wifi/scan | ✅ | ✅ | ✅ | — | — | REAL | 扫描 |
| POST /api/network/wifi/connect | ✅ | ✅ | ✅ | — | — | REAL | 连接 |
| POST /api/network/wifi/fallback | ✅ | ✅ | ✅ | — | — | REAL | 回退 |
| POST /api/network/wifi/ap/apply | ✅ | ✅ | ✅ | — | — | REAL | 热点 |
| POST /api/network/wifi/client/activate | ✅ | ✅ | ✅ | — | — | REAL | 客户端模式 |

### System

| API | UI | Contract | Gateway | IPC | Core | 状态 | 备注 |
|-----|----|----------|---------|-----|------|------|------|
| GET /api/settings/auto-start | ✅ | ✅ | ✅ | — | — | REAL | 自启状态 |
| PUT /api/settings/auto-start | ✅ | ✅ | ✅ | — | — | REAL | 更新自启 |
| PUT /api/system/hostname | ✅ | ✅ | ✅ | — | — | REAL | 主机名 |
| POST /api/system/reboot | ✅ | ✅ | ✅ | — | — | REAL | 重启 |
| POST /api/system/poweroff | ✅ | ✅ | ✅ | — | — | REAL | 关机 |
| GET /api/system/storage | ✅ | ✅ | ✅ | — | — | REAL | 存储 |

### Diagnostics

| API | UI | Contract | Gateway | IPC | Core | 状态 | 备注 |
|-----|----|----------|---------|-----|------|------|------|
| POST /api/diagnostics/aim-trace | ✅ | ✅ | ✅ | ✅ | ✅ | PARTIAL | Web 端采样实现 |

### NOT_IMPLEMENTED

| API | 说明 |
|-----|------|
| POST /api/update/check | 假数据 |
| POST /api/update/versions | 假数据 |
| GET /api/update/status | 假数据 |
| POST /api/update/install | 假数据 |
| POST /api/update/cleanup-stuck | 假数据 |
| GET /api/themes | 假数据 |
| POST /api/themes/redeem | 假数据 |
| POST /api/themes/install | 假数据 |
| PUT /api/themes/current | 假数据 |
| POST /api/control/calibration/start | 假数据 |
| POST /api/control/calibration/cancel | 假数据 |
| DELETE /api/control/calibration | 假数据 |
| PUT /api/control/calibration | 假数据 |
| POST /api/remote/connect | 假数据 |
| POST /api/remote/import | 假数据 |
| POST /api/remote/delete | 假数据 |
| POST /api/license/activate | 假数据 |

## 统计

| 状态 | 数量 |
|------|------|
| REAL | 36 |
| PARTIAL | 1 |
| NOT_IMPLEMENTED | 17 |
| LEGACY | 0 |
| BROKEN | 0 |

## 总结

- 真实实现：36 个接口（占 67%）
- 未实现：17 个接口（占 31%）
- 部分实现：1 个接口（占 2%）
- 未实现接口主要集中在更新系统、主题、校准、远程等非核心功能