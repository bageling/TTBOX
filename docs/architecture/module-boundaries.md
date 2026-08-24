# 模块边界（Module Boundaries）

> 定义模块的职责、边界、输入、输出、IPC/API、进程归属与独立升级能力。
> 原则：**各模块只通过定义好的接口交互，禁止跨模块直接调用内部实现。**
> 产品原则：**A1-A6 高速链路全部 C++**（Capture / RGA / RKNN / Worker / Decode-NMS / ModelAdapter）；
> Python 不得进入正式 AI 高速链路、不得作为生产依赖，仅作参考实现/验证/对齐/测试。

## 1. C++ AI Core 内部模块边界（A1-A6 高速链路）

A1-A6 六个模块构成完整 C++ Runtime。**核心链路模块之间只传递帧/张量/检测结果（零 JSON、零逐帧 IPC、尽量零 CPU memcpy）**，模型解析逻辑全部收口到 ModelAdapter。

| # | 模块（A阶段） | 职责 | 边界（不做什么） | 输入 | 输出 |
|---|---|---|---|---|---|
| 1 | **Capture**（A-2） | V4L2 MPLANE 采集：REQBUFS/MMAP/EXPBUF/DQBUF/QBUF/STREAMON、LatestFrame 共享、buffer 归还与 refcount | 不做缩放/推理/任何像素处理 | /dev/video0 | `FrameBuffer{dma_fd, info}`（latest 语义） |
| 2 | **RGA**（A-3） | 消费 `FrameBuffer.dma_fd` → librga imcrop+imresize → 模型输入尺寸；DMA-BUF 生命周期管理 | 不做推理；不做格式转换之外的处理；不直接访问 NPU | dma_fd | 模型输入 buffer（RGA 输出，零 CPU memcpy） |
| 3 | **RKNN**（A-4） | librknnrt C API：模型加载/init/query、set_input、rknn_run、outputs（原生零转换 want_float=0）；输入类型/量化按模型 query 结果适配 | 不做解码/NMS/坐标映射；不内嵌模型解析逻辑 | 模型输入 buffer | 原生输出 tensors（+ 各输出 type/fmt/scale/zp/dims 元数据） |
| 4 | **Worker**（A-5） | 多 Worker 池：独立 RKNN context 并行、latest-frame 认领调度（seq%N）、线程安全、帧生命周期 | 不做检测解析；不做控制决策 | 帧引用 + 模型句柄 | 推理完成回调 + 分阶段统计 |
| 5 | **Decode/NMS**（A-6） | 调度 ModelAdapter 解码输出 → 候选框 → classwise NMS → 坐标映射回原图；候选/检测/耗时统计 | **不含任何 YOLO 版本解析逻辑**（归 ModelAdapter）；不做目标选择 | 原生 tensors + ModelAdapter | `std::vector<DetectionBox>` |
| 6 | **ModelAdapter**（A-6） | 按 metadata + adapter 解析模型输出：输出解析/坐标解码/DFL/sigmoid/量化反解/类别映射；提供统一模型接口 | 不做采集/推理/NMS 调度；不进入 Worker 核心 | 模型 metadata + 原生 tensors | 候选框（模型空间，含 score/class） |

**模块依赖方向**（高速链路单向，禁止反向）：`Capture → RGA → RKNN → Worker → Decode/NMS → ModelAdapter`。
新增模型只增 **ModelAdapter + metadata**，Capture/RGA/RKNN/Worker 核心链路零修改。

## 2. ModelAdapter 统一模型接口（metadata）

每个模型（.rknn）配套一份 metadata，声明以下统一接口（不猜格式、不硬编码，均来自模型 query/配置）：

| 接口项 | 说明 | 示例 |
|---|---|---|
| 输入尺寸 | input_width / input_height | 640×640（yolo261n）、320×320（黄瓦） |
| 输入格式 | type（INT8/UINT8/FLOAT16/FLOAT32）+ 布局（NCHW/NHWC） | 黄瓦 INT8 NHWC |
| 量化参数 | 输入/输出 scale、zero point | INT8 输出反量化 (v-zp)*scale |
| 输出数量 | n_outputs | 1（yolo261n）、6（黄瓦） |
| 输出 tensor 类型 | 各输出 type | FLOAT16 / INT8 |
| 输出布局 | dims + NCHW/NHWC + 通道语义 | (1,1,4,M) box + (1,C,H,W) cls |
| stride | 多尺度 stride 列表 | 8/16/32（黄瓦） |
| DFL | 是否 DFL / 通道含义 | 黄瓦 4×距离（网格） |
| 类别数量 | n_classes + 类别映射 | 2（头） |
| Decode 类型 | 单输出 xywh / 多输出 DFL / objectness 变体 | 由 adapter 分发 |
| NMS 参数 | conf_thres / iou_thres / classwise | 0.25 / 0.45 / true |

**ModelAdapter 负责**：输出解析、坐标解码、DFL、sigmoid、量化反解、类别映射。
**Runtime 负责**：采集、预处理、推理、Worker、调度、帧生命周期、性能统计。

## 3. Python 角色边界

- Python Demo（`aibox/inference/` 等）保留为 **Golden Reference / 回归基准**：参考实现、模型验证、数据对齐、性能分析、测试辅助
- **禁止**：Python 进入正式 AI 高速链路；作为生产运行依赖；在高速链路引入 Python（逐帧调用/JSON 桥接/子进程）
- C++ 实现与 Python 参考逐项对齐（输出位级、NMS 语义、检测框），Python 侧发现的 bug 不作为 C++ 的实现依据（以模型/数据事实为准）

## 4. Control（控制）

| 项 | 说明 |
|---|---|
| 职责 | 目标选择（Target pick）、PID、运动预测、平滑（贝塞尔拟人）、控制策略、热键 |
| 边界 | **不**做推理；**不**直接写 HID 端点（经 HID 模块输出）；**不**做检测 |
| 输入 | Detection 列表、准星/屏幕信息、热键状态、配置 |
| 输出 | 鼠标/键盘动作序列（dx,dy,button,key） |
| IPC/API | 进程内接口：`ControlLoop::update(detections) -> OutputActions` |
| 独立进程 | 否 |
| 独立升级 | 否（随 Core） |

## 5. HID（键鼠）

| 项 | 说明 |
|---|---|
| 职责 | USB Gadget（configfs）、键盘/鼠标 HID report 构建、物理键鼠 Input Forwarding、输出端点写 |
| 边界 | **不**做控制决策；**不**做业务逻辑 |
| 输入 | 控制动作（来自 Control）、物理设备事件（evdev） |
| 输出 | `/dev/hidg0,1` report 写入、控制 socket（JSON，供 Core 查询按键状态） |
| IPC/API | Unix Socket（JSON 命令协议，已有 `km_passthrough.sock` 协议） |
| 独立进程 | 可独立（A-1 已验证独立进程形态）；最终可并入 Core 或独立 |
| 独立升级 | 是（与 Core 解耦时） |

## 6. Runtime（运行时）

| 项 | 说明 |
|---|---|
| 职责 | Pipeline 生命周期（start/stop/reload）、配置加载与热重载、模型切换、状态机（运行/错误/待机）、指标汇总 |
| 边界 | **不**执行任何帧级计算；**不**做识别/控制逻辑；**不**包含模型解析逻辑（归 ModelAdapter） |
| 输入 | 配置（JSON）、管理命令、各模块状态回调 |
| 输出 | 统一 SystemStatus / PipelineMetrics（共享状态发布） |
| IPC/API | 对外唯一管理面：Unix Socket JSON（PING/GET_STATUS/GET_CONFIG/… 已有 A-1 协议） |
| 独立进程 | 否（Core 内） |
| 独立升级 | 否（随 Core） |

## 7. Web（控制台）

| 项 | 说明 |
|---|---|
| 职责 | Dashboard、REST API、WebSocket 推送、配置页、模型管理页、日志页、监控图 |
| 边界 | **严禁承载任何 AI 推理逻辑**；**不**直接访问 /dev/video0、NPU、RGA |
| 输入 | 来自 Core 的共享状态/指标、用户操作 |
| 输出 | 管理命令（转交 Core）、WebSocket 推送指标 |
| IPC/API | HTTP(S) + WebSocket；与 Core：Unix Socket JSON；指标：共享状态区（原子读） |
| 独立进程 | 是（`aibox-web`） |
| 独立升级 | 是（Web 版本独立） |

## 8. Agent（设备代理）

| 项 | 说明 |
|---|---|
| 职责 | 设备注册、认证、授权缓存管理、版本上报、后台通信、Update 编排触发 |
| 边界 | **不**执行 AI；**不**在后台不可用时阻断 AI Core |
| 输入 | Backend 响应、设备本地凭证、Update Manifest |
| 输出 | 注册/心跳/Telemetry、更新指令（交 Update Manager） |
| IPC/API | 与 Core：Unix Socket（状态只读 + 指令转发）；与 Backend：HTTPS REST |
| 独立进程 | 是（`aibox-agent`） |
| 独立升级 | 是（Agent 版本独立） |

## 9. Update Manager（更新管理）

| 项 | 说明 |
|---|---|
| 职责 | 独立更新 Core/Model/Web/Agent/Config；版本校验（SHA256）、兼容性检查、失败恢复、回滚、离线包 |
| 边界 | **不**改变运行中 Core 的推理逻辑；更新 Core 前先停 Core 再切换（原子切换） |
| 输入 | Update Manifest（JSON）、离线安装包 |
| 输出 | 安装/回滚结果、版本状态 |
| IPC/API | 与 Agent：本进程内/Unix Socket；与 Backend：下载 Manifest 与包 |
| 独立进程 | 可独立或并入 Agent（建议独立目录 `aibox-update`，由 Agent 编排） |
| 独立升级 | 是（自身随 Agent 或独立） |

## 10. Backend（云端）

| 项 | 说明 |
|---|---|
| 职责 | 设备注册、认证、License、版本、Update Manifest、Model 分发、Config 下发、Telemetry 接收 |
| 边界 | **不参与设备端 AI 运行路径**；设备离线时完全自治 |
| 输入 | 设备请求（HTTPS） |
| 输出 | 响应/授权/Manifest/文件下载 |
| IPC/API | HTTPS REST（见 `backend-api.md`） |
| 独立进程 | 云端服务（不在设备上） |
| 独立升级 | 云端独立 |

## 模块依赖约束（防耦合）

```
        ┌────────────┐
        │    Web     │  ──管理命令──▶ ┌────────────┐
        └─────┬──────┘               │  Runtime   │
              │ 指标(共享状态)         └─────┬──────┘
        ┌─────▼──────┐                     │
        │   Agent    │ ◀──更新/授权────▶   │
        └─────┬──────┘                     │
              │ HTTPS                      ▼
        ┌─────▼──────┐    ┌──────────────────────────────────┐
        │  Backend   │    │ Core(C++ Runtime):                │
        └────────────┘    │  Capture→RGA→RKNN→Worker→        │
                          │  Decode/NMS→ModelAdapter→Target→  │
                          │  Control→HID                      │
                          └──────────────────────────────────┘
```
- 帧路径只存在于 Core 内部（Capture→…→HID Out），全部 C++
- **ModelAdapter 是模型解析唯一入口**：新增模型只增 metadata + adapter，Capture/RGA/RKNN/Worker 核心链路零修改
- Web/Agent 只能与 Runtime 管理面交互，禁止触碰帧/模型内部
- Control 与 HID 通过**动作序列接口**解耦（不直接写 hidg）
- Python 参考实现（decode.py/controller.py 等）仅在开发期用于模型验证/数据对齐/性能分析，**不进入高速链路**
