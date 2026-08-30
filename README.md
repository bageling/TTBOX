# TTBOX

RK3588 HDMI AI 采集、推理、目标控制与 HID 输出系统。

## 当前主线

当前唯一主线源码位于：

```text
core/
```

本项目采用 C++ Core 承载高速链路：

```text
HDMI/V4L2 (2560x1440 BGR3, 144Hz)
    ↓
DMA-BUF
    ↓
RGA (硬件缩放/ROI)
    ↓
RKNN WorkerPool (多 worker 多 NPU core)
    ↓
Decode/NMS + ModelAdapter (单输出 / DFL 多输出自动分发)
    ↓
AimTargetMailbox
    ↓
AimThread (Pid1Controller + AlphaBetaGammaFilter + FOV + 预测)
    ↓
OutputAction
    ↓
HID/FIFO
```

## 当前状态（性能二期后）

### 高速链路性能（板端 RK3588 真机实测）

```text
Capture FPS      : ~147  (capture_buffers=12，修复 V4L2 buffer 饥饿)
Inference/Detection: ~147 (3 worker 并行，最新帧直通无排队)
E2E              : P50 ~11.5ms / P95 ~14.4ms / P99 ~17.2ms
Preview          : 640x360 12fps（RGA 硬件缩放 + 真 MJPEG 流）
```

生产模型：`巨无敌乱杀sjzv11`（256x256 INT8，6 输出 DFL，7 类），
模型二进制不进仓库（仓库 `models/` 只保存 manifest/元数据）。

### 参数链路（Web ↔ Core 双向翻译已打通）

- 保存路径：Web PUT /api/config → ttbox-bridge.js → Gateway PUT /api/v1/config → Core SET_CONFIG → RuntimeConfig 内存热更新
- worker 每帧应用 runtime_profile（conf/iou/FOV/ROI/class_filter/preview.fps）
- FOV：`range_factor < 1.0 → fov.enabled=true` 才生效（AimThread/解码器双消费）
- 预览帧率：`latency.preview_interval_ms → preview.fps`（RuntimeProfile 已支持 preview.fps）
- 观测：E2E/Infer/Decode 的 P50/P95/P99/Max 分位经 GET_STATUS 暴露

### 架构现状

- `AimThread` 已接管目标跟踪/控制：Pid1Controller + AlphaBetaGammaFilter + FOV + 预测 + 输出策略（旧 AiboxPpidController / SmithPredictor 已删除）
- `WorkerPool` 独立 RKNN context 并行（core_mask 1/2/4），latest-frame 认领（seq%N）无重复处理
- 检测解码按输出结构自动分发：单输出(yolo) / DFL 多输出(巨无敌乱杀 6 输出)
- 无第二套 Capture；Preview 复用 Capture 最新帧，异步 RGA+JPEG，不阻塞 AI

## 目录说明

```text
core/src/app/       应用生命周期
core/src/capture/   V4L2 与 DMA-BUF 采集
core/src/rga/       RGA 硬件预处理
core/src/rknn/      RKNN、WorkerPool、Decode/NMS、NPU 监控
core/src/model/     ModelAdapter、模型元数据、Runtime 配置(RuntimeProfile)
core/src/pipeline/  Frame/AimTarget 数据通道
core/src/aim/       AimThread、Pid1Controller、AlphaBetaGammaFilter
core/src/output/    AI 输出后端抽象与 FIFO 后端
core/src/mouse/     AimThread 控制链路
core/src/hid/       HID 透传与 Gadget 管理
core/src/ipc/       本地 IPC (Unix socket /tmp/ttbox_core.sock)
core/tests/         单元测试与硬件测试
config/             配置模板
scripts/            EDID、HID、部署、网关(ttbox_gateway.py)等脚本
web/static/         Web 前端桥接(ttbox-bridge.js)
docs/               架构、协议、性能和部署文档
```

## Web 控制台（板端）

- TTBOX Web Gateway：`scripts/ttbox_gateway.py`（0.0.0.0:8081），托管 `web/dist` 前端构建产物
- 桥接层：`web/static/ttbox-bridge.js`（在 yu 的 app.js 之前加载，拦截 fetch 双向翻译）
- Preview：`/api/preview.jpg`（单帧）与 `/api/preview.mjpg`（MJPEG 流，前端 img 依赖流刷新）
- 板端 systemd：`ttbox-core` + `ttbox-web@8081`（与 yu 8080 独立并存）

## 构建

Windows 主机逻辑测试建议使用纯英文路径，例如：

```text
C:/ttbox-mainline
```

板端 RK3588 负责完整 V4L2/RGA/RKNN/HID 硬件构建和验证。

```bash
cmake -S core -B core/build -DTTBOX_CORE_BUILD_HW_TESTS=OFF
cmake --build core/build -j2
ctest --test-dir core/build --output-on-failure
```

## 重构原则

1. 先理解数据流，再修改实现。
2. Worker 不直接执行 PID 或 HID。
3. AimThread 统一处理目标、FOV、预测、PID 和输出策略。
4. 输出通过 `IHidOutput` 抽象，先保留 FIFO，后续再支持直接 FFS。
5. 当前主线只使用 AimThread；旧调度器/旧控制链已删除。
6. 高速链路不传逐帧 JSON，不复制图像（DMA-BUF/RGA 零拷贝）。
7. 所有架构模块必须写中文职责注释。
8. 每个阶段必须编译、测试、提交并可回滚。
9. 参数链路必须端到端真实生效（Web→Gateway→IPC→RuntimeConfig→worker），禁止前端假值。
