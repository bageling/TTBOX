# TTBOX 前端 API 审计报告

## 审计范围

- `web/static/app.js` — 前端主应用（459KB）
- `web/static/ttbox-bridge.js` — 桥接层（10KB）
- `web/templates/index.html` — 模板（117KB）
- `scripts/ttbox_web.py` — Web API 后端（110 路由）
- `core/src/ipc/IpcServer.cpp` — IPC 核心（12 命令）
- `core/src/common/Metrics.hpp` — 指标（29 字段）

## 1. 页面结构

| 页面 | 状态 | 备注 |
|------|------|------|
| home-page（总览） | 🟢 显示 | 实时状态 + 板载资源 + 预览 |
| profiles-page（热键控制） | 🟢 显示 | PID 参数 |
| control-page（移动控制） | 🟢 显示 | 控制器设置 |
| assist-page（辅助功能） | 🟢 显示 | 压枪/连点/背闪 |
| model-page（模型库） | 🟢 显示 | 模型管理 |
| hardware-page（显示与鼠标） | 🟢 显示 | 显示器 + 鼠标 + 设备枚举 |
| hailo-page（Hailo-8加速） | 🔴 隐藏 | 无此硬件 |
| kmbox-page（键鼠盒子） | 🔴 隐藏 | 无此硬件 |
| wifi-page（网络配置） | 🟢 显示 | Wi-Fi |
| preset-page（预设参数） | 🟢 显示 | 预设管理 |
| theme-store-page（主题商店） | 🔴 隐藏 | 无此功能 |
| license-page（系统状态） | 🟢 显示 | 授权 + 更新 + 存储 |

## 2. 前端 API 调用分析

前端 app.js 调用了 **60 个 API**。

按功能分组：

| 功能组 | API 数 | 真实 | 假桩 | 备注 |
|--------|--------|------|------|------|
| Dashboard | 4 | 4 | 0 | state / system / hardware/display / hardware/mouse |
| Runtime | 2 | 2 | 0 | control/start, control/stop |
| Models | 12 | 9 | 3 | import/select/delete/class-names/rknn-concurrency 真实；bind-preset/game-profile/remote-frame-format/hailo-pipeline-depth 假桩 |
| Presets | 3 | 3 | 0 | GET/POST/load 全部真实 |
| Hardware | 4 | 2 | 2 | display 真实；mouse 部分真实 |
| Network | 6 | 6 | 0 | Wi-Fi 全真实 |
| System | 7 | 7 | 0 | 存储/主机名/重启/关机/自启全部真实 |
| License | 2 | 1 | 1 | GET 真实；activate 假桩 |
| Update | 4 | 0 | 4 | 全部假桩 |
| Themes | 4 | 0 | 4 | 全部假桩 |
| Calibration | 3 | 0 | 3 | 全部假桩 |
| Diagnostics | 1 | 1 | 0 | Aim trace 真实 |
| Remote | 2 | 0 | 2 | 全部假桩 |
| Motion | 1 | 0 | 1 | 全部假桩 |
| Announcement | 1 | 0 | 1 | 全部假桩 |
| Hailo | 2 | 0 | 2 | 全部假桩（页面已隐藏） |

## 3. API 状态统计

| 状态 | 数量 |
|------|------|
| 🟢 真实（完整实现） | 36 |
| 🟡 假桩（返回假数据） | 19 |
| 🔴 已隐藏页面 | 5 |

## 4. 假桩清单

| API | 问题 |
|-----|------|
| POST /api/models/bind-preset | 返回 '已绑定' 但未真正写入 |
| POST /api/models/game-profile | 返回 '已更新' 但未真正写入 |
| POST /api/models/remote-frame-format | 返回 '已更新' 但未真正写入 |
| POST /api/models/hailo-pipeline-depth | 返回 '已更新' 但未真正写入 |
| POST /api/models/cloud-encrypted | 返回 '开发中' |
| POST /api/license/activate | 返回 '已激活' 但未真正激活 |
| POST /api/update/check | 返回固定 false |
| POST /api/update/versions | 返回固定数据 |
| GET /api/update/status | 返回固定 idle |
| POST /api/update/install | 返回 '已安装' |
| POST /api/update/cleanup-stuck | 返回 '已清理' |
| GET /api/themes | 返回空列表 |
| POST /api/themes/redeem | 返回 '已兑换' |
| POST /api/themes/install | 返回 '已安装' |
| PUT /api/themes/current | 返回 '已切换' |
| POST /api/control/calibration/start | 返回 '已开始' |
| POST /api/control/calibration/cancel | 返回 '已取消' |
| DELETE /api/control/calibration | 返回 '已清除' |
| PUT /api/control/calibration | 返回 '已更新' |

## 5. 桥接层分析

`ttbox-bridge.js`（10KB）当前状态：

- 0 个 API 调用（通过 `fetch` 或 `api()`）
- 似乎是一个过时的桥接层，当前未活跃使用
- 前端 app.js 直接调用 Gateway 的 HTTP API，未经过桥接层

## 6. IPC 核心命令

| 命令 | 状态 | 说明 |
|------|------|------|
| GET_STATUS | 🟢 | 返回完整运行状态 + Metrics |
| GET_CONFIG | 🟢 | 返回运行时配置 |
| SET_CONFIG | 🟢 | 更新运行时配置 |
| RUNTIME_CONTROL | 🟢 | 启动/停止 AI |
| GET_PREVIEW | 🟢 | 获取预览帧 |
| MODEL_LIST | 🟢 | 列出所有模型 |
| MODEL_IMPORT | 🟢 | 导入模型 |
| MODEL_VALIDATE | 🟢 | 校验模型 |
| MODEL_INSTALL | 🟢 | 安装模型 |
| MODEL_ACTIVATE | 🟢 | 激活模型 |
| MODEL_REMOVE | 🟢 | 删除模型 |
| PING | 🟢 | 心跳 |

## 7. Metrics 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| fps | double | 推理帧率 |
| capture_fps | double | 采集帧率 |
| capture_ms | double | 采集耗时 |
| resize_ms | double | 预处理耗时 |
| infer_ms | double | 推理耗时 |
| decode_ms | double | 解码耗时 |
| aim_ms | double | 瞄准耗时 |
| e2e_ms | double | 端到端延迟 |
| e2e_p50/95/99 | double | 延迟分位数 |
| detect_count | size_t | 检测目标数 |
| dropped_frames | size_t | 丢弃帧数 |
| frames_total | uint64 | 总帧数 |
| target_frames | uint64 | 有目标帧数 |
| no_target_frames | uint64 | 无目标帧数 |
| aim_active | bool | 瞄准激活 |
| preview_fps | double | 预览帧率 |
| preview_encode_ms | double | 预览编码耗时 |
| preview_width/height/bytes | uint32 | 预览尺寸 |
| preview_frames/dropped | uint64 | 预览统计 |

## 8. 关键发现

### 状态一致性

- 前端大量状态来自 `/api/state` 的 `state` 对象，这是正确的
- 但部分状态（如模型列表）同时从 `/api/state` 和 `/api/models` 获取，存在双源问题

### 配置 vs 状态

- 前端 `state.config` 存储配置，`state.data` 存储完整状态
- 配置和运行时状态在部分地方有混淆

### 假桩分布

- 假桩主要集中在：更新（4个）、主题（4个）、校准（3个）、模型管理（3个）
- 已隐藏的页面（Hailo、Kmbox、Theme）依然有假桩 API，但页面不可见

### 桥接层

- `ttbox-bridge.js` 目前未活跃使用，属于历史遗留

## 9. 建议

1. 优先修复模型管理假桩（bind-preset/game-profile）
2. 更新/校准/主题/远程 标记为 PLANNED
3. 清理 `ttbox-bridge.js` 确定其角色
4. 统一配置字段命名