# Runtime Pipeline（运行时流水线）

> 高速链路 + 线程模型 + 时序来源 + FPS 采集点定义。
> 原则：**实际 FPS 必须来自 EDID → HDMI 实际输出 → V4L2 实际 timing**，禁止写死 2K144/1080P240。
> 产品原则：**高速链路全部 C++**；Decode/NMS 由 ModelAdapter 驱动（metadata + adapter），禁止 YOLO 版本逻辑硬编码进 Runtime。

## 1. 高速链路（帧级，zero-copy）

```
HDMI IN
  │ rk_hdmirx
  ▼
[1] HDMI RX      —— 硬件接收，输出到 rk_hdmirx
[2] V4L2         —— /dev/video0 MPLANE，G_FMT 返回【实际锁定时序】
  │ VIDIOC_EXPBUF
  ▼
[3] DMA-BUF      —— dma_fd（A-2 已建立；buffer 归属/归还由 refcount 管理）
  │ im2d (librga)
  ▼
[4] RGA          —— 硬件 crop/resize → 模型输入尺寸（A-3；目标尺寸来自模型 metadata）
  │ RGA 输出 buffer（dma-buf 或堆内存）
  ▼
[5] RKNN         —— RKNN C API（A-4）：set_inputs + run；输入 dtype/layout 按模型 query 适配
  │ 输出 tensor（want_float=0 原生零转换）
  ▼
[6] Worker       —— 多 worker/多 context 并行推理（A-5）；latest-frame 语义
  ▼
[7] Decode/NMS   —— ModelAdapter 解析输出（DFL/sigmoid/量化反解/坐标解码）→ 候选 → classwise NMS（A-6）
  ▼
[8] Target       —— 目标选择（类别优先级/最近中心；后续阶段）
  ▼
[9] Control      —— PID + 预测 + 平滑（后续阶段；Python controller.py 仅参考）
  ▼
[10] HID         —— /dev/hidg 输出（后续阶段）
USB Host
```

## 2. 阶段职责与数据载体

| # | 阶段 | 输入 | 输出 | 数据载体 | 零拷贝策略 |
|---|---|---|---|---|---|
| 2 | V4L2 | /dev/video0 | dma_fd + 元数据 | `FrameBuffer` | mmap 不读 CPU |
| 3 | DMA-BUF | dma_fd | RGA 可消费 fd | fd | 引用传递 |
| 4 | RGA | fd | 模型输入 buffer | `RgaFrame` | 硬件缩放，无 CPU memcpy |
| 5 | RKNN | 输入 buffer | 原生 tensors（want_float=0） | 内存 | 输入零拷贝优先（若 Runtime 支持） |
| 7 | Decode/NMS | 原生 tensors + ModelAdapter | Detection 向量 | 内存 | 向量传递（无深拷贝）；INT8 反量化在 adapter 内 |
| 9 | Control | Detection | 动作序列 | 值类型 | — |

## 3. 线程/Worker 模型

- **capture 线程**：V4L2 poll/DQBUF/publish/QBUF（A-2 已实现）
- **rga 线程**：消费 latest frame → RGA 缩放 → 发布模型输入（A-3）
- **worker 池**：N 个独立 RKNN context（A-5 已验 1/2/3 并行；是否启用多 worker 以实际吞吐/NPU 三核利用率为准）
- **decode 线程**：ModelAdapter 解码 + NMS → Detection
- **control 线程**：消费 Detection → 动作 → HID 写
- 阶段间用 **LatestFrame 语义**（无队列、覆盖旧帧）；禁止帧级 JSON
- 所有线程/模块 C++；Python 参考实现仅开发期对齐用

## 4. 帧生命周期与 buffer 归属

1. V4L2 DQBUF → `FrameBuffer`（shared_ptr 保活，refcount 判定可归还，A-2 已实现）
2. RGA 消费 dma_fd（A-3）：RGA 完成（同步等待 fence）后才可释放该帧引用
3. 帧引用在 RGA 阶段结束后释放 → V4L2 buffer 归还
4. **禁止**在 RGA/RKNN 使用期间归还 V4L2 buffer

## 5. 时序来源与动态运行

```
EDID（声明 RX 能力，允许 source 输出的模式）
   ↓ 协商
HDMI source 实际输出
   ↓
V4L2 实际锁定时序（G_FMT：width/height/pixelformat + 实际帧率）
   ↓
Pipeline 动态参数：
   - RGA 目标尺寸 = 模型输入尺寸（来自 ModelAdapter metadata，yolo261n 640×640 / 黄瓦 320×320）
   - 坐标系：原图 → 模型 → 回原图（缩放系数随实际 width/height 计算）
   - 帧率上限 = min(V4L2 实际帧率, 各阶段吞吐)
```

- **不写死** 2K144 / 1080P240；这些只是能力标杆（参考：2560×1440@144、1920×1080@240）
- 分辨率/时序变化 → Runtime 检测到 G_FMT 变化 → 触发 pipeline 重配置（重新协商 RGA/坐标映射）
- 详见 `edid-and-input-timing.md`

## 6. 延迟与吞吐预算（实测基线，C++）

| 阶段 | yolo261n 640×640（实测） | 黄瓦 320×320（实测） | 说明 |
|---|---|---|---|
| Capture (V4L2) | ~0.01ms（已验） | ~0.01ms（已验） | DQBUF 开销 |
| RGA crop+resize | ~2.2ms（A-3 实测） | 同左（目标尺寸更小更快） | vs CPU 22ms（Python 基线） |
| RKNN set_input | ~0.94ms（pass_through=1）/ 24-37ms（pass_through=0 FP16） | ~0.08ms（UINT8 直喂） | 输入类型/转换路径差异 |
| RKNN run | ~33ms | ~6.3ms | NPU 负载随输入尺寸 |
| output（want_float=0） | ~0.33ms | ~0.34ms | A-6：A-5 8.5ms → 0.35ms（24×） |
| Decode/NMS（C++） | ~9.6ms（8400 anchors） | ~0.27ms（1600 anchors 空候选） | ModelAdapter 调度 |
| E2E 目标 | <50ms（P50） | — | 后续阶段 |

> 注：数字随 V4L2 实际时序/模型动态变化；性能优化阶段以实测为准，不写死理论值。

## 6.1 统一性能优化阶段（A6 之后）

- **范围**：1080p60 / 2K144 / 1080p240，按实际 EDID/V4L2 输入时序**动态测试**，不写死理论 FPS
- **评价核心**：吞吐量 + NPU 三核利用率（目标：三个 NPU Core 有效并行、最大化吞吐，而非单纯降低单帧延迟）
- **测试矩阵**：单/双/三 Worker × 各实际时序，记录 Capture FPS / Pipeline FPS / E2E / NPU Core0/1/2 / CPU / DDR / 丢帧
- **判定**：以实测为唯一依据；多 Worker 是否有收益由 NPU 三核利用率与吞吐数据决定

## 7. FPS / 指标采集点（Web 展示）

每个阶段维护**原子计数器 + 最近耗时**（min/avg/p95/max 由监控线程周期汇总）：

| 指标 | 定义 |
|---|---|
| Input FPS | V4L2 实际帧率（驱动 sequence 差分） |
| Capture FPS | capture 线程成功 DQBUF 速率 |
| RGA FPS | RGA 完成缩放速率 |
| Inference FPS | RKNN 完成推理速率 |
| Pipeline FPS | 完整链路（capture→decode）处理速率 |
| PostProcess FPS | Decode/NMS 完成速率 |
| Output FPS | HID 输出动作速率 |
| E2E latency | 帧进入 → Control 输出 的端到端耗时（P50/P95/P99） |
| Drop FPS | latest-frame 覆盖丢弃速率 |
| CPU 使用率 | 每核采样（/proc/stat） |
| NPU 使用率 | **Core0/1/2 分核**（/sys/kernel/debug/rknpu/load，A-4 已实现） |
| DDR 使用率 | 内存/DDR 带宽或占用（性能优化阶段接入） |

发布方式：Core 每 1s 原子写共享状态区（`/dev/shm/aibox_status` 或 mmap），Web 读取并 WebSocket 推送。
**管理链路 JSON；帧链路零 JSON。**

## 8. 错误处理

- V4L2 无信号/时序变化 → Runtime 记录并进入待机，恢复后自动重启链路
- RGA 失败 → 该帧跳过（不崩溃），计数 errors
- RKNN 失败 → worker 隔离；连续失败触发 Runtime 重启模型
- HID 端点丢失 → 重试（A-2 passthrough 已有类似逻辑）


## 11. TTBOX 当前重构状态（A11）

当前 Worker 到控制侧采用双链路，确保新架构验证期间旧行为不变：

```text
Worker
├── LatestDetections → MouseScheduler → FIFO（兼容旧链路）
└── AimTargetTask → AimTargetMailbox → AimThread（新链路）
```

### AimTargetTask 职责

- 只传递帧号、时间戳、Worker 编号、目标框、瞄准点、目标尺寸和检测结果。
- 不传图像、DMA-BUF、RKNN tensor 或逐帧 JSON。
- `frame_number` 用于多 Worker 乱序帧过滤。

### AimTargetMailbox 职责

- 每个 Worker 保留一个最新任务槽位。
- AimThread 扫描槽位并选择最新 `frame_number`。
- Worker 不等待 AimThread，允许旧任务被新任务覆盖。

### AimThread 当前边界

AimThread 当前负责线程生命周期、任务消费和目标相对画面中心误差计算；当前暂不接管 PID、FOV、Smith、真实 HID 输出。

### OutputAction 与输出后端

控制线程只生成 `OutputAction`，通过 `IHidOutput` 输出。当前保留 FIFO 后端，协议为：

```text
0x01 + dx(int16 little-endian) + dy(int16 little-endian)
```

后续依次接入目标选择、热键门控、PID、FOV、Smith 和实机 HID。
