# TTBOX

RK3588 HDMI AI 采集、推理、目标控制与 HID 输出系统。

## 当前主线

当前唯一主线源码位于：

```text
core/
```

本项目采用 C++ Core 承载高速链路：

```text
HDMI/V4L2
    ↓
DMA-BUF
    ↓
RGA
    ↓
RKNN WorkerPool
    ↓
Decode/NMS + ModelAdapter
    ↓
AimTargetMailbox
    ↓
AimThread
    ↓
OutputAction
    ↓
HID/FIFO
```

## 当前架构重构状态

### 已完成

- `WorkerPool` 保留旧 `LatestDetections` 兼容链路
- 新增 `AimTargetTask`：Worker 到瞄准线程的数据契约
- 新增 `AimTargetMailbox`：每个 Worker 一个最新任务槽位
- 新增独立 `AimThread` 骨架
- 新增 `IHidOutput` 输出抽象
- 新增 `FifoHidOutput`，保留现有 FIFO 协议
- 新增目标相对画面中心误差计算
- 新增邮箱和 AimThread 单元测试

当前并行链路：

```text
旧链路：Worker → LatestDetections → MouseScheduler → FIFO
新链路：Worker → AimTargetMailbox → AimThread → OutputAction
```

新 AimThread 当前只验证数据流和误差方向，暂未接管 PID/FOV/Smith，也暂不改变真实鼠标输出。

## 目录说明

```text
core/src/app/       应用生命周期
core/src/capture/   V4L2 与 DMA-BUF 采集
core/src/rga/       RGA 硬件预处理
core/src/rknn/      RKNN、WorkerPool、Decode/NMS
core/src/model/     ModelAdapter、模型元数据、Runtime 配置
core/src/pipeline/  Frame/AimTarget 数据通道
core/src/aim/       AimThread、目标误差和后续控制算法
core/src/output/    AI 输出后端抽象与 FIFO 后端
core/src/mouse/     旧 MouseScheduler 兼容链路
core/src/hid/       HID 透传与 Gadget 管理
core/src/ipc/       本地 IPC
core/tests/         单元测试与硬件测试
config/             配置模板
docs/               架构、协议、性能和部署文档
scripts/            EDID、HID、部署和诊断脚本
```

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
5. 旧 MouseScheduler 在新链路验证前保持可回滚。
6. 高速链路不传逐帧 JSON，不复制图像。
7. 所有架构模块必须写中文职责注释。
8. 每个阶段必须编译、测试、提交并可回滚。
