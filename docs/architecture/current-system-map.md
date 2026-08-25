# TTBOX Current System Map

> 基于分支 `develop/platform-v1`、基线 `44fcc361d69ac2e038d4f445fd93e019c2cb4e7b` 的离线源码盘点。本文只记录现状，不代表目标能力已实现。自动瞄准算法文件未修改。

## A. Repository Structure

当前顶层：`core/`、`config/`、`docs/`、`hid/`、`models/`、`scripts/`、`ttbox-hid-bridge.c`、`pyproject.toml`。Platform V1 新增骨架位于 `platform/`，不替换现有 Core。

## B. Build System

唯一明确 C++ 构建入口是 `core/CMakeLists.txt`，C++17；主机默认测试/工具开启，硬件测试关闭。Unix 下按环境探测 `librga` 与 `librknnrt`。Python 项目入口为 `pyproject.toml`，但仓库未提供可运行的 `aibox*` 包目录。

## C. Runtime Entry

`core/src/main.cpp` → `Application::initialize/run/shutdown`。另有 `CoreRuntime` 与 `HardwareRunner` 作为流水线生命周期类；后者配套 `core/tools/hardware_runner_main.cpp`，仅在 Unix 且 RKNN 可用时构建。当前没有 Platform Supervisor 接管这些入口。

## D. Capture Pipeline

`V4L2Capture` 与 `DmaBuf` 位于 `core/src/capture/`，Unix 条件编译；`CoreRuntime` 启动顺序为 capture open/start → WorkerPool → AimThread。Windows 无 V4L2 验证条件。

## E. RGA

`RgaProcessor` 位于 `core/src/rga/`，仅在 `/usr/local/include/im2d.h` 与 `librga` 同时发现时加入 Core。仓库提供 RGA 单测/硬件测试源，但本主机未进行硬件验证。

## F. RKNN

`RKNNEngine`、`WorkerPool`、`NpuMonitor` 位于 `core/src/rknn/`，依赖 `librknnrt` 与 `core/third_party/rknn/rknn_api.h`；缺失 runtime 时不编译相关源。模型 raw 推理入口属于板端能力。

## G. Model System

`ModelRegistry` 提供 registry/installed/staging/cache/quarantine 目录与 import/validate/install/activate/rollback 相关生命周期；生产 validate 需要注入 validator。模型文件位于 `models/`，当前仓库有 `models/sjz_xcsh/manifest.json`，未见已提交 `.rknn` 文件。

## H. RuntimeProfile

`core/src/model/RuntimeProfile.*` 承载运行时模型/推理配置结构；`config/*.json` 保存当前配置模板。Platform V1 的配置层级枚举已建立，但尚未接入 Core 热加载。

## I. HID

`core/src/hid/` 提供 HID 解析、包注册、运行时与 forwarder；`core/src/output/` 提供 `IHidOutput`、FIFO 与 AIBOX 输出实现。真实 hidraw/hidg/UDC 链路只能在 RK3588 验证；本阶段默认不启用真实 AI HID。

## J. IPC

`IpcServer` 位于 `core/src/ipc/`，Application 启动本地 IPC 并提供 status/config provider；`ipc_ping` 是命令行工具。尚无 `/api/v1` HTTP API。

## K. Web

仓库没有独立 Web 源码应用；`core/tools/web/` 主要是静态页面、脚本和 systemd/部署文件。AIBOX 包中的 Flutter Web 与后端资源仅作能力参考，未复制第三方代码。

## L. API

当前 Core IPC 支持状态/配置查询；目标 `/api/v1/system`、`runtime`、`models`、`config`、`health`、`metrics`、`update`、`logs` 尚未实现 HTTP 路由。

## M. systemd

仓库中的 service 文件主要位于 `core/tools/web/`，包括 `ttbox-infer.service`、`ttbox-hid-forward.service`、`ttbox-hid.service`、firstboot/watchdog/EDID 等。尚无统一 Platform Supervisor unit。

## N. Config

`config/default.json`、`hardware_display.json`、`hdmirx_edid_identity.json`、`yolo261n-rk3588.json` 是静态配置入口；`ConfigManager` 负责 Core 读取。Factory/Device/Runtime/Override 合并、版本、备份和热更新尚未接入。

## O. Logging

`core/src/common/Logger.*` 为 Core 日志实现，另有 `docs/` 与脚本约定。平台级日志收集/轮转/API 暴露未实现。

## P. Metrics

`common/Metrics.hpp`、`Stats.hpp` 与 HardwareRunner 状态字段提供指标基础；Application 的视觉链路 metrics 仍是占位。无统一 `/api/v1/metrics` 服务。

## Q. Update System

仓库有部署/打包/回滚脚本与 `docs/architecture/update-architecture.md`，但没有平台统一的 component manifest、staging/validate/activate/rollback 服务。Platform V1 仅建立更新状态契约。

## R. Scripts

`scripts/` 覆盖 EDID、HID gadget、部署、硬件调查、包安装/回滚、验收；`core/tools/web/` 还包含 image/rootfs/package/release 脚本。脚本入口多，当前未由单一 Supervisor 编排。

## S. Tests

CMake 注册 Core 单元测试、邮箱/AimThread/FOV 测试；硬件测试需显式开启并依赖 RK3588。Platform V1 已新增 3 个纯 Python 合同测试。本 Windows 环境已实际运行 Python 合同测试；C++ 配置失败，因为缺少 `nmake` 与 C++ 编译器。

## T. Device Deployment

目标设备部署事实来自脚本与文档，涉及 `/opt/ttbox`、systemd、V4L2/RGA/RKNN/HID。未连接 RK3588，因此 Boot、服务、HDMI、V4L2、RGA、RKNN、HID、Recovery 均未在本阶段验证。

## AIBox package reference (offline)

已离线读取用户提供的四类 `.deb` 包目录与 service/路径清单：`aibox-rk3588` 提供 `aibox.service`、`/usr/bin/aibox`、`aibox-bl`、模型目录与 RKNN runtime；`aiboxkm` 提供 `aiboxkm.service`、USB udev rule 与 `/usr/bin/aiboxkm`；`web-aibox` 提供 `web-aibox.service`、`web-aibox-ctl` 与 `/opt/web-aibox/web`；`autobl_upgrade` 提供 `cloud-file-manager.service`、`CloudFileManagerBackend` 与 `/opt/autobl/webui`。这些包仅作为离线能力/运行方式参考，未复制其受版权保护的源码、未访问包内云端链接或凭证。

## Evidence boundary

- **FACT**：以上路径和 CMake 条件来自当前检出的源码；Platform Python 测试退出码为 0。
- **NOT VERIFIED**：C++ 编译、RK3588 硬件、HTTP/Web 实际交互、systemd 启动、模型切换、更新回滚、恢复流程。
