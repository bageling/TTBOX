# TTBOX Web 能力映射表（Web Capability Map）

> **本文件是 Web 界面字段的唯一依据。** Web UI 不允许出现本表之外的参数。
> 生成方式：扫描 `core/src/model/RuntimeProfile.cpp`（to_json/from_json/validate）与消费方源码，非人工臆测。
> 更新约定：Core 字段变更时同步更新本表；UI 只消费 `expose: true` 的字段。

图例：
- **expose**：是否在 Web 暴露控件（`expose: true` = 已验证有真实消费者；`false` = Core 无消费者或无场景，不显示）
- **热更新**：SET_CONFIG 后 Core 运行时是否立即生效（核对消费者代码路径）
- **消费模块**：Core 中实际读取该字段的模块（决定"是否支持热更新"的判断依据）

---

## 1. mouse（AI 鼠标控制）— 消费模块 `aim/AimThread.cpp` + `mouse/*`

| 字段 | 中文名称 | 白话说明 | 默认值 | 范围 | 控件 | 热更新 | 消费模块 |
|---|---|---|---|---|---|---|---|
| `mouse.enabled` | AI 辅助 | 总开关。关闭时 AI 仍分析画面，但鼠标输出强制为 0 | false | bool | Switch | ✅ 每周期读快照（AimThread:57-88 Gate + TtboxHidOutput::send 保险门） | AimThread / TtboxHidOutput |
| `mouse.aim_hotkey` | 主热键 | 按住此键时 AI 才移动鼠标 | 2（右键） | 1/2/4/8/16（左/右/中/侧1/侧2） | Select | ✅ 每周期读快照 | AimThread Gate |
| `mouse.aim_hotkey2` | 副热键 | 可选第二触发键；0 = 不使用 | 0 | 0/1/2/4/8/16 | Select | ✅ 每周期读快照 | AimThread Gate |
| `mouse.aim_hotkey_mode` | 触发方式 | any = 任一键按下；all = 两键同时按住 | "any" | any/all | Select | ✅ 每周期读快照 | AimThread Gate |
| `mouse.kp_x` | X 轴拉力 | X 方向把准星拉向目标的力度。追不上就加；来回冲就降 | 17.0 | ≥0（validate 无上限） | Slider | ✅ 每周期 `pid.configure()` | Pid1Controller |
| `mouse.kp_y` | Y 轴拉力 | Y 方向拉力。抬不上去就加；Y 轴晃就降 | 10.0 | ≥0 | Slider | ✅ 同上 | Pid1Controller |
| `mouse.kd_x` | X 轴刹车 | 对 X 轴速度变化做刹车；数值大容易抖 | 0.0 | ≥0 | Slider | ✅ 同上 | Pid1Controller |
| `mouse.kd_y` | Y 轴刹车 | 跳跃落地发抖时先调低或关掉 | 0.0 | ≥0 | Slider | ✅ 同上 | Pid1Controller |
| `mouse.predict_x` | X 轴预判 | 目标横移时提前看一点。滞后就加，过头就降 | 0.008 | ≥0（秒） | Slider | ✅ 同上 | Pid1Controller |
| `mouse.predict_y` | Y 轴预判 | Y 方向预判；落地抖就别加 | 0.008 | ≥0（秒） | Slider | ✅ 同上 | Pid1Controller |
| `mouse.rate_x` | X 轴跟随 | X 轴输出变强的速度。反应慢就加；突然一窜就降 | 1.0 | ≥0 | Slider | ✅ 同上 | Pid1Controller |
| `mouse.rate_y` | Y 轴跟随 | 压不住高度差就加；Y 轴突跳就降 | 1.0 | ≥0 | Slider | ✅ 同上 | Pid1Controller |
| `mouse.smooth_x` | X 轴平滑 | X 轴移动柔和度；9900 ≈ 不平滑 | 9900.0 | ≥0 | Slider | ✅ 同上 | Pid1Controller |
| `mouse.smooth_y` | Y 轴平滑 | Y 轴移动柔和度 | 9900.0 | ≥0 | Slider | ✅ 同上 | Pid1Controller |
| `mouse.lost_grace_ms` | 丢失宽限 | 目标消失后保持瞄准的时长。太长会跟错人，太短容易断 | 78.0 | ≥0（ms） | Slider | ✅ selector 配置每帧读 | TargetSelector / AimStateMachine |
| `mouse.aim_offset_x` | 瞄准点 X 偏移 | 准星相对画面中心的左右偏移（crop 系 px） | 0.0 | 无限制 | Slider | ✅ 每帧读 | CoordinateTransform::reference_point |
| `mouse.aim_offset_y` | 瞄准点 Y 偏移 | 准星相对画面中心的上下偏移 | 0.0 | 无限制 | Slider | ✅ 每帧读 | CoordinateTransform |
| `mouse.offset_x` | 瞄准点比例 X | 0~1，目标框内归一化位置（0.5=中心） | 0.5 | [0,1] | Slider | ✅ 每帧 `scfg.aim_ratio_x` | TargetSelector |
| `mouse.offset_y` | 瞄准点比例 Y | 0~1，越小越靠框顶（打头就调小） | 0.5 | [0,1] | Slider | ✅ 每帧 | TargetSelector |
| `mouse.selector_search_radius` | 目标搜索半径 | 准星附近多大范围内挑目标 | 170.0 | ≥0（px） | Slider | ✅ 每帧 `scfg.fov_range` 派生 | TargetSelector |

### mouse 段中 expose:false 的字段（Core 有字段但 Web 不显示）

| 字段 | 不暴露原因 |
|---|---|
| `mouse.ki_x / ki_y` | pid1 控制器不使用积分项（V1 纯 P 语义，字段注释"预留"） |
| `mouse.confidence` | 与 `inference.confidence` 语义重复；TargetSelector 实际消费 `inference` 段 |
| `mouse.fov_range` | 与 `fov.radius` 功能重叠（selector 用 fov_range 派生）；避免两处开关 |
| `mouse.prediction_s` | 已被 `predict_x/y` 取代（AimThread 注释"旧值会产生转圈，已修"） |
| `mouse.fov_mode / hfov / vfov / move_speed_x / move_speed_y` | 角度换算模式（AimThread:115 有消费分支），但 TTBOX 当前主链路用像素模式；留给后续 |
| `mouse.sensitivity / output_scale / deadzone_x / deadzone_y / output_deadzone` | **Core 无消费者**（grep 全仓只有定义与序列化）——显示会造成假开关 |
| `mouse.proxy_mode` | V1 仅 full_passthrough 一种 |
| `mouse.smooth` | 与 `smooth_x/y`（0~9990）不同的 0~1 参数，Core 消费路径未接入 |
| `mouse.aim_part` | AimThread 未消费（瞄准点由 offset_x/y 决定） |
| `mouse.pull_curve / continuous_lead / humanize / class_offsets` | Core 无消费者（TTBOX 插件位，未移植） |
| `mouse.aim_fire_lock_y / y_axis_fire_hotkey / y_axis_fire_release_delay_sec` | Core 无消费者（TTBOX 压枪联动位） |
| `mouse.calibrating / calibration_bias_x / calibration_bias_y` | 标定流程专用，非用户参数 |
| `mouse.block_physical_x / block_physical_y` | HID 层能力位，当前链路未接 |
| `mouse.switch_delay_ms` | AimThread 未消费 |

## 2. inference（检测）— 消费模块 `rknn/DecodeNMS.cpp` + `mouse/TargetSelector.cpp`

| 字段 | 中文名称 | 白话说明 | 默认值 | 范围 | 控件 | 热更新 | 消费模块 |
|---|---|---|---|---|---|---|---|
| `inference.confidence` | 目标置信度 | 低于该可信度的目标忽略；误识别多就调高 | 0.25 | [0,1] validate | Slider | ✅ 每帧读 | DecodeNMS / WorkerPool |
| `inference.iou` | 重叠过滤 | 过滤重复目标的程度 | 0.45 | [0,1] validate | Slider | ✅ 每帧读 | DecodeNMS |
| `inference.max_detections` | 最大目标数量 | 单帧最多保留的检测框数 | 20 | ≥0 validate | Input | ✅ 每帧读 | DecodeNMS |
| `inference.class_filter` | 识别类别 | 空数组 = 全部类别（类别名需模型 metadata，暂无来源） | [] | int[] | 暂不显示 | ✅ | TargetSelector |

## 3. fov（辅助范围）— 消费模块 `aim/AimThread.cpp`（scfg）

| 字段 | 中文名称 | 白话说明 | 默认值 | 范围 | 控件 | 热更新 | 消费模块 |
|---|---|---|---|---|---|---|---|
| `fov.enabled` | 辅助范围开关 | 开启后只处理准星附近的目标 | false | bool | Switch | ✅ 每帧读 | TargetSelector |
| `fov.radius` | 辅助范围 | 0~1 相对画面的选择半径（validate 限 (0,1]，UI min 取 0.01） | 0.5 | (0,1] | Slider | ✅ 每帧读 | TargetSelector |
| `fov.center_x` | 范围中心 X | 0 最左 1 最右 | 0.5 | [0,1] validate | Slider | ✅ 每帧读 | TargetSelector |
| `fov.center_y` | 范围中心 Y | 0 最上 1 最下 | 0.5 | [0,1] validate | Slider | ✅ 每帧读 | TargetSelector |
| `fov.shape` | 范围形状 | 0=圆 1=矩形 | 0 | 0/1 | 暂不显示（TargetSelector 消费 center/radius，形状分支未接） | — | — |

## 4. capture（采集）— 消费模块：板端 V4L2（**不热更新**）

| 字段 | 说明 | expose |
|---|---|---|
| `capture.width/height/offset_x/offset_y` | 采集裁剪参数，仅启动时读取 | false（修改需重启 Core，不适合热改 UI；后续可做"重启生效"提示组） |

## 5. preview / geometry_filter / model_id — 暂不暴露

| 字段 | 原因 |
|---|---|
| `preview.*` | 预览尺寸参数，MJPEG 预览未接入前无消费场景 |
| `geometry_filter.*` | Head/身体几何过滤，Core 有 validate+序列化，AimThread 未接 |
| `model_id` | 由 ModelRegistry activate 管理（模型库页），不由配置页直接改 |

---

## 6. GET_STATUS 指标映射（总览/系统状态数字来源）

| UI 指标 | Core 字段 | 来源线程/结构 | 更新周期 | 无数据时 |
|---|---|---|---|---|
| AI 状态 | `runtime_running` | Application::status_provider → core_runtime_->running() | 5s 轮询 | "已停止"（布尔，必有值） |
| 服务在线 | `running` | Application running_ 原子标志 | 5s | 离线态（请求失败本身即信号） |
| 采集 FPS | `metrics.fps` | PipelineMetrics（**占位结构**，注释"视觉链路接入后填充"） | 5s | ≤0 → "暂无数据" |
| 端到端延迟 | `metrics.e2e_ms` | 同上（占位） | 5s | ≤0 → "暂无数据" |
| 检测目标数 | `metrics.detect_count` | 同上（占位） | 5s | ≤0 → "暂无数据" |
| 运行时长 | `uptime_ms` | now_ms() - start_time_ms_ | 5s | 恒有值 |
| 程序版本 | `version` | kVersion 宏 | 恒定 | 恒有值 |
| IPC 通道 | `ipc_socket` | Application ipc_path_ | 恒定 | 恒有值 |
| 配置文件 | `config_file` | ConfigManager path_ | 恒定 | 恒有值 |
| CPU / NPU / 温度 / HID / 内存 | **Core 未上报** | — | — | **不显示数字**，显示"暂未提供" |

> PipelineMetrics 当前为占位（Metrics.hpp 注释明示）。板端接入真实统计前，前端对 `fps/e2e_ms/detect_count ≤ 0` 一律显示"暂无数据"，**绝不显示 0 FPS 冒充实测**。

## 7. 模型管理（MODEL_* IPC v0.3）

| 能力 | Core | IPC | Web |
|---|---|---|---|
| 列表 | ModelRegistry::list() | MODEL_LIST ✅ | 模型库页 ✅ |
| 导入 | import()（收件目录约束） | MODEL_IMPORT ✅ | 上传表单 ✅ |
| 校验 | validate()（validator 注入） | MODEL_VALIDATE ✅ | 校验按钮 ✅ |
| 安装 | install() | MODEL_INSTALL ✅ | （校验通过后 install 由 Core 链路处理） |
| 激活 | activate()（写 active.json） | MODEL_ACTIVATE ✅ | 启用按钮 ✅ |
| 删除 | remove()（激活中拒绝） | MODEL_REMOVE ✅ | 删除按钮 ✅ |
| 当前模型 | active_model() | MODEL_LIST 返回 active ✅ | 总览"当前模型" ✅ |
| **热加载** | **无**（需重启 AI 流水线） | — | UI 明示"更换后需重启 AI"，不装即时生效 |
| 模型信息（输入尺寸/类别） | 需 RKNN validator 解析 metadata | Windows 文件级校验拿不到 | 显示"暂无数据" |

## 8. 明确不做的（防过度设计）

压枪 / 连点 / 背闪 / 拉枪曲线 / 持续提前量 / 拟人化（Core 无消费者）；WiFi / 风扇 / Hailo / kmbox / EDID / 系统更新 / 日志大屏（Core 无此能力）；JSON 直编入口（预设页只读快照）。
