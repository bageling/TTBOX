# AIBox 最终商用架构总览

> 状态：**A-1~A-6 已全部由 C++ 实现并验收**，C++ Runtime 完整覆盖高速链路
> 对应仓库 `aibox/core/`（C++ Core）+ `docs/architecture/`
> 当前：**进入统一性能优化阶段**（1080p60 / 2K144 / 1080p240，按实际 V4L2 时序动态测试，吞吐 + NPU 三核利用率）

## 1. 设计目标

- **C++ 为核心**的完整独立 AIBox 系统，运行于 RK3588（OrangePi 5 Plus/Ultra）
- **低延迟高速链路**：HDMI → V4L2 → DMA-BUF → RGA → RKNN → Worker → Decode/NMS → Control → HID
- **模块解耦**：Capture / RGA / RKNN / Worker / Decode-NMS / ModelAdapter 边界清晰，各自独立演进
- **后端与设备解耦**：后台异常不得影响已授权 AI Core 运行（本地授权缓存 + 宽限期）
- **可独立升级**：Core / Model / Web / Agent / Config 互不阻塞更新，支持校验、失败恢复、回滚
- **不写死输入模式**：EDID 声明能力，V4L2 报告实际时序，Pipeline 动态适配（2K144 / 1080P240 仅是能力标杆）

### 1.1 产品原则（A1-A6 之后冻结）

- **C++ Runtime 原则**：A1-A6 所有核心功能（采集/预处理/推理/Worker/解码/NMS/模型适配）必须使用 C++ 实现，形成完整 C++ Runtime
- **Python 边界**：Python **不得**进入正式 AI 高速链路，**不得**作为生产依赖；仅保留为参考实现、模型验证、数据对齐、性能分析和测试辅助工具
- **统一 ModelAdapter**：禁止把 YOLO 某个版本的解析逻辑硬编码进 Runtime；Runtime 提供统一 ModelAdapter 接口，不同 YOLO/RKNN 模型通过 **metadata + adapter** 接入（至少支持 yolo261n 640、黄瓦 320 INT8；后续新增模型不得修改 Capture/RGA/RKNN/Worker 核心链路）
- **性能优先**：A6 完成后不再机械堆功能，进入统一性能优化阶段；目标不是单纯降低单帧延迟，而是让三个 NPU Core **有效并行、最大化吞吐**

## 2. 系统全景

```
┌─────────────────────────── 硬件层 ───────────────────────────┐
│  HDMI IN(RX)   USB Host(键盘/鼠标)   Type-C USB Gadget(HID) │
└──────────────┬───────────────────────────────┬──────────────┘
               │ /dev/video0 (rk_hdmirx)        │ /dev/hidg0,1 (configfs)
┌──────────────▼───────────────────────────────▼──────────────┐
│                     Linux / 内核驱动层                        │
│  V4L2 MPLANE · DMA-BUF heap · RGA (/dev/rga) · RKNN (librknnrt) │
└──────────────┬───────────────────────────────────────────────┘
┌──────────────▼──────────────────── 进程层 ───────────────────┐
│  aibox-core  (C++，主进程 · 完整 C++ Runtime)                │
│   Capture → RGA → RKNN → Worker → Decode/NMS(ModelAdapter)  │
│   → Target → Control → HID Out · Runtime(生命周期/配置/模型) │
│                                                              │
│  aibox-web    (C++，HTTP + WebSocket + Dashboard，纯管理)     │
│  aibox-agent  (C++，注册/认证/授权缓存/版本/后台/Update 编排) │
│  (开发期 Python 参考实现：decode.py/controller.py 仅对齐验证) │
└──────────────┬───────────────────────────────────────────────┘
               │ 网络
┌──────────────▼──────────────────── 云端层 ───────────────────┐
│  Backend：设备注册/认证/License/Update Manifest/Model/Config/│
│           Telemetry（与设备端完全解耦，非 AI 运行依赖）        │
└──────────────────────────────────────────────────────────────┘
```

## 3. 进程模型

| 进程 | 语言 | 职责 | 与 Core 关系 |
|---|---|---|---|
| `aibox-core` | C++ | 高速链路（Capture/RGA/RKNN/Worker/Decode-NMS/ModelAdapter）+ Runtime | 唯一承载 AI 推理逻辑 |
| `aibox-web` | C++ | Dashboard/API/WebSocket | Unix Socket + 共享状态；禁止承载 AI 逻辑 |
| `aibox-agent` | C++ | 后台通信/授权/版本/更新 | Unix Socket |
| `aibox-core` 内部线程 | — | capture / rga / rknn worker / decode / control | 线程级流水线 |
| 参考实现（开发期） | Python | decode.py / controller.py 等，仅模型验证/数据对齐/性能分析/测试 | 不进入正式 AI 高速链路，非生产依赖 |

故障隔离：任一管理进程崩溃不影响 Core；Core 崩溃由 systemd 拉起（阶段 D 加固）。

## 4. 两条数据链路（核心原则）

### 4.1 高速链路（帧级，无 JSON）
```
HDMI RX ──V4L2──▶ DMA-BUF fd ──RGA──▶ RKNN 输入 ──▶ Worker ──▶ Decode/NMS(ModelAdapter) ──▶ Target ──▶ Control ──▶ HID
   │              (zero-copy)  (硬件缩放)     (NPU)      (并行)      (metadata+adapter)      (目标)    (PID/预测)  (hidg)
```
- 数据载体：`FrameBuffer{info.dma_fd}`（A-2 已建立）→ 未来 `RgaFrame` → RKNN 输入
- **禁止逐帧 JSON**；禁止跨线程拷贝像素（DMA-BUF→RGA→RKNN 全程零 CPU memcpy）
- 高速 IPC（若跨进程）使用 Unix Socket / shared memory；当前为进程内线程 + 引用传递
- Decode/NMS 由 **ModelAdapter** 驱动：模型输出解析/坐标解码/DFL/sigmoid/量化反解/类别映射
  全部在 ModelAdapter 内完成，Runtime 不包含任何 YOLO 版本解析逻辑

### 4.2 管理链路（JSON 允许）
```
Web/Agent ──JSON──▶ aibox-core（Runtime 控制面：start/stop/config/model/status）
```
- 仅用于：配置、模型切换、状态查询、控制命令、监控（非逐帧）

## 5. 关键设计原则

1. **JSON 边界**：只出现在 Web/API/管理/调试；帧路径零 JSON
2. **零拷贝优先**：DMA-BUF → RGA → RKNN 输入，避免 CPU memcpy（实测 DMA 直读慢 ~7×，A-2 已规避）
3. **Latest-frame**：无队列、新帧覆盖旧帧、consumer 慢不阻塞（A-2 已验证）
4. **实际时序驱动**：Pipeline 一切以 V4L2 实际时序为准，禁止硬编码分辨率/帧率；性能测试按实际 EDID/V4L2 输入时序动态进行，不写死理论 FPS；EDID 测试资源库（`resources/edid/`，真实 EDID 不伪造）+ 工具（`scripts/edid/`）构成商用输入兼容测试体系
5. **Web 纯管理**：Web 不参与任何推理路径，仅消费 Core 发布的指标与状态
6. **离线优先**：AI Core 运行不依赖网络/后台；授权本地缓存 + 宽限期
7. **独立升级单元**：Core/Model/Web/Agent/Config 可分别升级、分别回滚
8. **Python 不进生产链路**：正式 AI 高速链路全部 C++；Python 仅作参考实现/验证/对齐/测试（Golden Reference 保留但非运行依赖）
9. **ModelAdapter 统一接口**：模型接入 = metadata + adapter；输入尺寸/输入格式/量化参数/输出数量/tensor 类型/布局/stride/DFL/类别数/Decode 类型/NMS 参数统一描述；新增模型不改 Capture/RGA/RKNN/Worker
10. **性能评价核心**：吞吐量 + NPU 三核利用率（1/2/3 Worker 对比；Capture FPS / Pipeline FPS / E2E / NPU Core0/1/2 / CPU / DDR / 丢帧）

## 6. 状态与指标发布

- Core 周期性（如 1Hz）向共享状态区发布：各阶段 FPS、E2E latency、drop、CPU/NPU 使用率、当前时序
- Web 展示：**EDID 声明能力**（来自 `resources/edid/`/当前注入 EDID）、**当前 V4L2 实际输入**（G_FMT/DV timings）、**当前 Pipeline FPS**（sequence 实测）——不允许把 EDID FPS 当成实际 FPS
- Web 通过 Unix Socket/共享内存读取；不轮询 JSON 大对象
- 详见 `runtime-pipeline.md` 第 7 节

## 7. 目录布局（目标态）

```
/opt/aibox2/
├── core/            # C++ Core（版本化：/opt/aibox2/core/<version>/ 符号链接 current）
├── models/          # 模型（版本化）
├── web/             # Web 控制台（静态 + 后端）
├── agent/           # Agent 进程
├── config/          # 配置（版本化 + 运行时覆盖）
├── data/            # 状态/授权缓存/日志
└── update/          # Update Manager 暂存/备份/回滚
```

> 演进路径：当前仓库 `aibox/core/`（源码）→ 部署布局 `/opt/aibox2/core/`（阶段 C/D 落地）
