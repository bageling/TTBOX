# YU_TTBOX_MAPPING.md — yu → TTBOX 能力映射表

> yu 侧 = 板端实测（0.53）；TTBOX 侧 = Core IPC 实际能力。

| yu 功能 | yu 控件/键 | TTBOX 当前能力 | 能否直接接 | 缺什么 |
|---|---|---|---|---|
| 移动倍率 | `sens` | sens | ✅ | —（纯 UI 映射） |
| X轴拉力 | `controller_kp_x` | mouse.kp_x | ✅ | —（纯 UI 映射） |
| Y轴拉力 | `controller_kp_y` | mouse.kp_y | ✅ | —（纯 UI 映射） |
| X轴刹车 | `controller_kd_x` | mouse.kd_x | ✅ | —（纯 UI 映射） |
| Y轴刹车 | `controller_kd_y` | mouse.kd_y | ✅ | —（纯 UI 映射） |
| X轴积分 | `controller_ki_x` | mouse.ki_x | ✅ | —（纯 UI 映射） |
| Y轴积分 | `controller_ki_y` | mouse.ki_y | ✅ | —（纯 UI 映射） |
| X轴预判 | `controller_predict_x` | mouse.predict_x | ✅ | —（纯 UI 映射） |
| Y轴预判 | `controller_predict_y` | mouse.predict_y | ✅ | —（纯 UI 映射） |
| X轴跟随 | `controller_rate_x` | mouse.rate_x | ✅ | —（纯 UI 映射） |
| Y轴跟随 | `controller_rate_y` | mouse.rate_y | ✅ | —（纯 UI 映射） |
| 基础死区 | `controller_output_deadzone` | mouse.output_deadzone | ✅ | —（纯 UI 映射） |
| 转火延迟 | `controller_selector_lost_grace_ms` | mouse.selector_max_lost_frames(语义差:ms→frames) | ✅ | —（纯 UI 映射） |
| X轴平滑 | `controller_smooth_x` | mouse.smooth_x | ✅ | —（纯 UI 映射） |
| Y轴平滑 | `controller_smooth_y` | mouse.smooth_y | ✅ | —（纯 UI 映射） |
| Y轴锁定热键 | `controller_y_axis_fire_hotkey` | mouse.y_axis_fire_hotkey | ✅ | —（纯 UI 映射） |
| 释放延迟 | `controller_y_axis_fire_release_delay_sec` | mouse.release_delay | ✅ | —（纯 UI 映射） |
| 拉枪曲线开关 | `controller_pull_curve_enabled` | —(无对应) | ❌ | True |
| 持续提前开关 | `controller_continuous_lead_enabled` | —(无对应) | ❌ | False |
| 目标置信度 | `video_detection_confidence` | inference.confidence | ✅ | —（纯 UI 映射） |
| 重叠过滤 | `video_detection_iou` | inference.iou | ✅ | —（纯 UI 映射） |
| 截取尺寸 | `capture_crop_size` | capture.crop_size | ✅ | —（纯 UI 映射） |
| 截取偏移X | `capture_crop_offset_x` | capture.offset_x | ✅ | —（纯 UI 映射） |
| 截取偏移Y | `capture_crop_offset_y` | capture.offset_y | ✅ | —（纯 UI 映射） |
| 范围大小 | `range_factor` | fov.radius(语义近似) | ✅ | —（纯 UI 映射） |
| 热键总开关 | `hotkey_guard_enabled` | hotkey_guard.enabled | ✅ | —（纯 UI 映射） |
| 状态总览 | `/api/state` | GET_STATUS（部分） | ⚠️ | 缺 metrics 接线（frames/fps 全 0） |
| 启停控制 | `/api/control/start|stop` | RUNTIME_CONTROL | ✅ | — |
| 配置读取 | `/api/state.config` | GET_CONFIG | ✅ | — |
| 配置修改 | `PUT /api/config` | SET_CONFIG | ✅ | — |
| 模型列表 | `/api/models` | MODEL_LIST | ✅ | — |
| 模型导入 | `/api/models/import` | MODEL_IMPORT(multipart→IPC) | ✅ | — |
| 模型切换 | `/api/models/select` | MODEL_ACTIVATE | ✅ | 切换后需重启推理（Web 提示待补） |
| 模型删除 | `/api/models/delete` | MODEL_REMOVE | ✅ | — |
| 实时画面 | `/api/preview.jpg|mjpg` | 无 | ❌ | Core 无视频出流 |
| 系统资源 | `/api/system` | 无 | ❌ | 需板端独立小服务 |
| 事件推送 | `/api/events (SSE)` | 无 | ❌ | 可轮询替代 |
| 预设 | `/api/presets*` | 无 | ❌ | Core 无 presets IPC |
| 自动校准 | `/api/control/calibration*` | 无 | ❌ | 暂不做 |
| 移动训练 | `/api/motion-profiles*` | 无 | ❌ | 放弃（C 级） |
| 压枪 | `recoil_* 13 控件` | 无 | ❌ | 放弃（C 级） |
| 连点 | `rapid_fire_* 4 控件` | 无 | ❌ | 放弃（C 级） |
| 自动背闪 | `auto_back_flick_* 9 控件` | 无 | ❌ | 放弃（C 级） |
| 准星找色 | `crosshair_* 17 控件` | 无 | ❌ | 放弃（C 级） |
| 热键总开关 | `hotkey_guard_enabled` | SET_CONFIG(hotkey_guard) | ✅ | — |
| 显示配置 | `/api/hardware/display` | GET_HDMI（只读） | ⚠️ | TTBOX 只读；写配置不接 |
| 鼠标硬件 | `/api/hardware/mouse*` | 无（HID 描述符固化） | ❌ | 放弃 |
| WiFi | `/api/network/wifi*` | 无 | ❌ | 放弃 |
| 风扇 | `fan_control_* 5 控件` | 无 | ❌ | 放弃 |
| Hailo | `/api/hailo/*` | 无 | ❌ | 放弃（TTBOX 用 RKNN） |
| 键鼠盒子 | `/api/makcu|ferrum|kmboxb` | 无 | ❌ | 放弃 |
| 主题商店 | `/api/themes*` | 无 | ❌ | 放弃 |
| 授权激活 | `/api/license*` | 无（TTBOX 授权占位） | ❌ | 放弃 |
| 系统更新 | `/api/update/*` | 无 | ❌ | 放弃 |