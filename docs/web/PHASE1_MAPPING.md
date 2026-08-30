# Phase 1 映射报告 — YU UI → TTBOX 真实后端

> 结论先行：yu 前端保存配置 = PUT /api/config（collectConfig() 扁平结构化 body）；
> TTBOX Core = SET_CONFIG(RuntimeProfile)。**桥接层负责双向翻译**，UI 零修改。

## A. 参数系统（Phase 2 核心）

### yu body（collectConfig 输出）→ TTBOX RuntimeProfile

| yu 键 | 类型 | TTBOX 目标 | 状态 |
|---|---|---|---|
| ai.controller.kp_x/kp_y | number | mouse.kp_x/kp_y | ✅ 真绑定 |
| ai.controller.kd_x/kd_y | number | mouse.kd_x/kd_y | ✅ 真绑定 |
| ai.controller.ki_x/ki_y | number | mouse.ki_x/ki_y | ✅ 真绑定 |
| ai.controller.predict_x/predict_y | number | mouse.predict_x/predict_y | ✅ 真绑定 |
| ai.controller.rate_x/rate_y | number | mouse.rate_x/rate_y | ✅ 真绑定 |
| ai.controller.smooth_x/smooth_y | number | mouse.smooth_x/smooth_y | ✅ 真绑定 |
| ai.controller.selector_lost_grace_ms | number | mouse.lost_grace_ms | ✅ 真绑定（yu=ms，TTBOX=ms） |
| ai.controller.output_deadzone | number | mouse.output_deadzone | ⚠️ Core 无消费（B2）→ 保存但提示 |
| ai.controller.y_axis_fire_* | number/str | mouse.y_axis_fire_* | ⚠️ Core 无消费（B1）→ 保存但提示 |
| video_detection_confidence | number | inference.confidence | ✅ 真绑定 |
| video_detection_iou | number | inference.iou | ✅ 真绑定 |
| capture.crop_size | number | capture.width/height | ✅ 真绑定（crop_size→宽高） |
| capture.crop_offset_x/y | number | capture.offset_x/y | ✅ 真绑定 |
| sens | number | 无（TTBOX 用 rate 缩放） | ⚠️ 忽略并提示 |
| range_factor | number | fov.radius | ✅ 近似真绑定 |
| pos | number | — | ⚠️ 忽略 |
| hotkey_guard.enabled | bool | — | ⚠️ Core 无 hotkey_guard（B7） |
| model_id | string | model_id | ✅ 真绑定（MODEL_ACTIVATE） |
| recoil/rapid_fire/auto_back_flick/crosshair | 段 | 无 | ⚠️ 保存到"待接入"（不伪造成功） |

### 热键编码转换（Phase 3）
| yu 字符串 | TTBOX 位图 |
|---|---|
| left | 1 |
| right | 2 |
| middle | 4 |
| back | 8 |
| forward | 16 |

## B. 实时状态（Phase 6）

| yu UI 字段 | 来源 | TTBOX Core | 状态 |
|---|---|---|---|
| state.capture.capture_fps | V4L2Metrics.capture_fps | ✅ G1 已接 | ✅ 真数据 |
| state.capture.input_width/height | V4L2 G_FMT | 需网关补 | ✅ 可接 |
| state.detection.inference_fps | WorkerStats published/秒 | ✅ G1 已接 | ✅ 真数据 |
| state.detection.inference_ms | stages.total.avg | ✅ G1 已接 | ✅ 真数据 |
| state.detection.detections | mailbox 最近任务 | ✅ G1 已接 | ✅ 真数据 |
| state.latency.capture_to_mouse_send_ms | e2e avg | ✅ G1 已接 | ✅ 真数据 |
| state.aim.active / target | mailbox | 需网关补 | ✅ 可接 |
| state.running | RUNTIME 状态 | ✅ | ✅ |
| state.mouse_output | HID 状态 | 网关补 | 可接 |
| 温度/CPU/NPU | 无 Core 采集 | G3 | ⚠️ 开发中 |

## C. HDMI/Display（Phase 4）

| UI | TTBOX 来源 | 状态 |
|---|---|---|
| 显示器名称/分辨率/刷新率/EDID | GET_HDMI（真机 1440p144） | ✅ 已接（/api/hardware/display 转发） |
| 首选模式列表 | 驱动 EDID 模式 | 网关补全 |
| 环出/写回 | 需 EDID 写回硬件 | ⚠️ HW |

## D. Mouse/HID（Phase 5）
- TTBOX HID = AiboxHidOutput/FifoHidOutput（fail-closed 已审计）
- Web 只显示状态：AI 开关= mouse.enabled ✅；DX/DY 输出=网关补（可从 AimThread status 读）

## E. Preview（Phase 7）
- 占位帧已给；真预览=Core JPEG 编码（G2）→ 网关 /api/preview.jpg 直通

## 结论
- **可 1:1 真绑定**：参数 14 项 / 实时 8 项 / HDMI 4 项 / 热键 5 项
- **桥接层翻译**：PUT /api/config（yu body→profile）、热键字符串→位图、GET /api/state（Core→yu 形状，capture/检测真数据）
