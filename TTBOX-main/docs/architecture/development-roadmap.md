# 开发路线图（Development Roadmap）

> 当前进度：**A-1 ✅ · A-2 ✅ · A-3 ✅ · A-4 ✅ · A-5 ✅ · A-6 ✅**
> 下一步：**统一性能优化阶段**（1080p60 / 2K144 / 1080p240，吞吐 + NPU 三核利用率）
> 产品原则：A1-A6 全部 C++；Python 不进入正式 AI 高速链路、不作为生产依赖；
> 禁止 YOLO 版本解析逻辑硬编码进 Runtime（统一 ModelAdapter：metadata + adapter 接入）；
> A6 之后不继续堆功能，先统一性能优化（详见下文「统一性能优化阶段」）。

## 阶段总览

| 阶段 | 名称 | 状态 |
|---|---|---|
| A-1 | C++ Core 地基（CMake/App/Logger/Config/IPC/JSON） | ✅ 已验收 |
| A-2 | V4L2 + DMA-BUF 采集（EXPBUF/LatestFrame/refcount） | ✅ 已验收 |
| A-3 | RGA（DMA-BUF→RGA crop/resize） | ✅ 已验收 |
| A-4 | RKNN C API（librknnrt 直调、FP16 零拷贝输入、NPU 监控） | ✅ 已验收 |
| A-5 | Worker（多 context 并行 + latest-frame 消费） | ✅ 已验收 |
| A-6 | Decode/NMS（C++，want_float=0 原生输出直解 + ModelAdapter 统一接口） | ✅ 已验收 |
| P-1 | **统一性能优化**（1080p60/2K144/1080p240，单/双/三 Worker，NPU 三核并行） | ⬜（当前） |
| A-7 | Aim/Control（PID/预测/平滑/目标选择） | ⬜（功能冻结，性能优化后） |
| A-8 | HID（C++ 输出 + gadget） | ⬜（功能冻结，性能优化后） |
| A-9 | Runtime/IPC（生命周期/配置/模型切换/状态发布） | ⬜（功能冻结，性能优化后） |
| A-10 | Core 完整链路（Capture→HID 全流程联调） | ⬜（功能冻结，性能优化后） |
| B | Web 正式控制台（WebSocket/硬件监控/模型/配置管理） | ⬜ |
| C | Agent/设备注册/License/Backend/Update/Rollback | ⬜ |
| D | systemd/Watchdog/日志/恢复/Factory Reset | ⬜ |
| E | EDID/多时序/1080P240/1440P144/性能验收 | ⬜（并入 P-1；EDID 测试资源库 `resources/edid/` + 工具 `scripts/edid/` 已建，1K240 EDID 已就绪） |
| F | 整机测试/稳定性/升级回滚/断网/掉电恢复/商用镜像 | ⬜ |

## A 阶段：C++ Core 高速链路

| 阶段 | 内容 | 关键验收 |
|---|---|---|
| A-1 ✅ | CMake 工程、Application 生命周期、Logger、ConfigManager、Json、IpcServer(PING/GET_STATUS/GET_CONFIG)、Types/Metrics | 15+3 单测通过；SIGTERM 优雅退出；RK3588 编译运行 |
| A-2 ✅ | V4L2Capture：QUERYCAP/G_FMT/REQBUFS/MMAP/EXPBUF/QBUF/STREAMON/DQBUF/LatestFrame/refcount 归还；DmaBuf RAII | 30s 硬件测试 1676 帧无错、fd 无泄漏、无 starvation |
| A-3 ✅ | RgaProcessor：消费 FrameBuffer.dma_fd → imcrop+imresize → 模型输入；librga 1.9.1 | 30s 硬件：1672 帧全成功，import 212us+crop 876us+resize 1013us=total 2.20ms，RGA FPS 55.7，无 fd/handle 泄漏 |
| A-4 ✅ | RKNNEngine（librknnrt C API）：模型加载/init/query、set_input（pass_through 零拷贝）、rknn_run、outputs；NpuMonitor（/sys/kernel/debug/rknpu/load）；单 Worker 统计 set_input/run/output/total(min/avg/p50/p95/p99/max) | 板端实测：pass_through=1（FP16 零拷贝）set_input 0.94ms + run 34.8ms + output 11.5ms = total 47.3ms，**21.1 FPS**（vs Python 13.6）；pass_through=0 12.8 FPS；NPU Core0≈60%；chain 全链路（capture→RGA→FP16→RKNN）30s 575 帧 19.1 FPS |
| A-5 ✅ | Worker 池：多 RKNN context 并行消费 latest 帧；帧调度 | 吞吐/延迟指标；无 starvation |
| A-6 ✅ | Decode/NMS C++ 实现 + ModelAdapter 统一模型接口（DFL/sigmoid/量化反解/坐标解码；yolo261n 单输出 + 黄瓦多输出 DFL）；want_float=0 原生输出直解 | 与 Python rknnlite 对齐（位级 + 检测级）；output <1ms；新增模型只增 adapter |
| P-1 | 统一性能优化：1080p60/2K144/1080p240 动态实测；单/双/三 Worker；NPU 三核利用率 | 三核有效并行、最大吞吐；实测为准不写死 FPS |
| A-7 | Target/Control：PID+预测+平滑（参考 controller.py）+ 目标选择 | 与 Python 回归一致；控制指标 |
| A-8 | HID 输出 C++（或保留独立 passthrough 进程 + C++ 客户端） | 键鼠动作输出；复用现有协议 |
| A-9 | Runtime 管理面：配置热重载、模型切换、状态/指标发布（共享状态）、管理 IPC 扩展 | Web 可读完整状态 |
| A-10 | 完整链路联调：HDMI→…→HID；E2E 指标；与 Python Golden Reference 对比 | E2E P50 目标 <50ms 推进 |

## B 阶段：Web 正式控制台

- Dashboard（FPS 9 项 + E2E + CPU/NPU + 时序）
- WebSocket 实时推送（替代轮询）
- 硬件监控（温度/频率/内存）
- 模型管理（列表/上传/切换/版本）、配置管理（表单 + 白名单）
- 日志页面（tail + 过滤）
- **Web 不承载 AI 逻辑**（模块边界约束）

## C 阶段：Agent / Backend / Update

- Agent 进程：注册/认证/授权缓存/版本上报/遥测
- Backend 接口（`backend-api.md` 契约）实现 + mock
- License 状态机 + 宽限期（`device-validation.md`）
- Update Manager：独立单元更新 + SHA256 + 兼容检查 + 回滚 + 离线包（`update-architecture.md`）

## D 阶段：系统加固

- systemd 服务完善（依赖/重启策略）
- Watchdog（软件 + 可选硬件）
- 日志轮转/集中
- Factory Reset / 恢复出厂
- 掉电恢复一致性

## E 阶段：EDID / 多时序 / 性能验收

- EDID 工具与配置（声明能力）
- 时序检测单元 + 动态重配置（`edid-and-input-timing.md`）
- 1080P240 / 1440P144 真机验收（需对应信号源；不满足如实标注）
- E2E <50ms 优化闭环

## F 阶段：商用镜像

- 整机稳定性（长稳/温度/内存）
- 升级/回滚演练（含断网、掉电中断）
- 最终商用镜像打包（参考 `docs/image-spec.md`）

## 当前准确进度（2026-08-14）

```
A-1 ✅  C++ Core 地基（已验收：21 单测通过、RK3588 编译运行）
A-2 ✅  V4L2 + DMA-BUF（已验收：30s 1676 帧、DQBUF=QBUF 成对、fd 无泄漏、55.8fps）
A-3 ✅  RGA（已验收：1672 帧全成功、total 2.20ms、RGA FPS 55.7、无泄漏）
A-4 ✅  RKNN C API（已验收：21.1 FPS（FP16 零拷贝 pass_through=1）、
        模型加载 41ms、NPU Core0 60%、chain 链路 19.1 FPS；详见 A-4 验收报告）
A-5 ✅  多 Worker（已验收：1/2/3 Worker 实测对比，三核并行验证；
        1W=18.9 FPS（Core0 54%）→ 2W=31.9（Core0/1 ≈51/51%）→
        3W=42.4（Core0/1/2 ≈44/43/43%，2.24×），capture 4 buffer 会饥饿
        （短超时轮询已修复），8 buffer 解除 capture 瓶颈；详见 A-5 验收报告）
A-6 ✅  Decode/NMS（已验收：C++ DecodeNMS + want_float=0 原生输出直解，
        output 8.5ms→0.35ms（24×）；模型输出与 rknnlite 位级一致（max diff=0）；
        发现 A-4/A-5 pass_through=1 直喂布局错误（垃圾检测），A-6 改 pass_through=0
        保证正确；发现 Python decode_outputs 的 run_classwise_nms 类间交错映射 bug，
        C++ NMS 与 Python nms_boxes 一致且语义正确；30 单测通过；详见 A-6 验收报告；
        黄瓦模型（VALORANT 头部 320x320 INT8，2 类，6 输出 DFL 多尺度）：
        INT8 模型 set_input 须以 UINT8 喂原始像素（runtime 量化）与 rknnlite 对齐；
        C++ raw vs rknnlite 3 图×6 输出 max_abs_diff=0；img0 检测完全一致
        （cands=2/NMS=1/score=0.424808/框一致）；1/2/3 Worker×300 帧：
        output 337-367us、decode_total 260-280us、1W=59.5 FPS 达 capture 源上限、
        0 错误；A-1~A-5 全回归通过）
P-1 ⬜  统一性能优化（当前）：1080p60 / 2K144 / 1080p240 按实际 V4L2 时序动态实测；
        单/双/三 Worker 对比；Capture FPS / Pipeline FPS / E2E / NPU Core0/1/2 / CPU / DDR / 丢帧；
        目标：三个 NPU Core 有效并行、最大化吞吐（不堆功能）
E  ⬜  EDID 测试体系（已建：resources/edid/ 真实 EDID 资源库 + scripts/edid/ 工具；
        1K240 目标 ASUS VG259QM EDID 已就绪（1920x1080@240 DP）；待源输出 240 实测；
        1440p144/165、4K120 分类待从 linuxhw 补充真实 EDID，不伪造）
```

## 统一性能优化阶段（P-1）

> A6 之后**不继续堆功能**，进入统一性能优化。所有新增能力必须保持 C++，不允许为方便重新引入 Python 到生产链路。

- **测试场景**：1080p60 / 2K144 / 1080p240，按实际 EDID/V4L2 输入时序动态测试，不写死理论 FPS
- **评价核心**：吞吐量 + NPU 三核利用率（目标：三个 NPU Core 有效并行、最大化吞吐，而非单纯降低单帧延迟）
- **测试矩阵**：单/双/三 Worker × 各实际时序；每项记录 Capture FPS、Pipeline FPS、E2E、NPU Core0/1/2、CPU、DDR、丢帧
- **优化方向**（候选）：输入转换路径、RGA/解码并行、buffer 复用、core 绑定策略、输出零拷贝链路、DDR 带宽
- **验收**：以实测数据为准；多 Worker 收益由 NPU 三核利用率与吞吐证明；性能报告与 roadmap 更新

## 依赖与约束

- Python Demo 全程保留为 **Golden Reference / 回归基准**，不删除不修改；**但不进入正式 AI 高速链路、不作为生产依赖**
- 每阶段先独立验证再接入；禁止为"漂亮"改实现（实测优先）
- 高速链路禁止逐帧 JSON；JSON 仅用于管理面
- **禁止把 YOLO 某版本解析逻辑硬编码进 Runtime**；模型接入统一走 ModelAdapter（metadata + adapter）
- 新增模型（输入尺寸/格式/量化/输出布局/DFL/类别数不同）不得修改 Capture/RGA/RKNN/Worker 核心链路
