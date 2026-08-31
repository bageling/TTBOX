# TTBOX —— 给你看懂的项目说明书

## TTBOX 是什么

TTBOX 是一个**运行在 Orange Pi 5 Plus 开发板上的实时 AI 视觉辅助系统**。

简单说：它通过 HDMI 输入读取游戏画面，用 AI 模型分析画面中的目标，然后通过 USB 模拟鼠标（HID 设备）把控制指令发送给电脑。

### 解决什么问题

在 FPS 游戏中，玩家需要快速发现并锁定目标。TTBOX 利用 AI 和 NPU（神经网络处理器）来实现：

1. **实时采集**：通过 HDMI 采集卡获取游戏画面（最高 240fps）
2. **AI 分析**：用 NPU 加速 AI 模型，实时检测画面中的目标
3. **智能控制**：根据检测结果，模拟鼠标移动辅助瞄准

### 运行在哪里

- 硬件：Orange Pi 5 Plus（RK3588 芯片）
- 操作系统：Ubuntu 22.04 / Orange Pi OS
- 输入：HDMI 游戏画面（通过 HDMI RX 采集卡）
- 输出：USB HID 模拟鼠标

### 核心链路（一句话版）

```
游戏画面 → HDMI → 采集 → AI 检测 → 目标选择 → 瞄准控制 → 鼠标输出 → 电脑
```

## 完整架构图

### 数据流（帧处理链路）

```
游戏画面 ................................ 电脑上的游戏程序
   ↓ HDMI 线
HDMI 采集卡 .............................. 板载 HDMI RX 接口
   ↓ V4L2 驱动
Capture（V4L2Capture.cpp）............... 采集视频帧
   ↓ DMA-BUF（零拷贝：不复制内存，只传文件描述符）
RGA 硬件缩放（RgaProcessor.cpp）........ 裁剪/缩放到 AI 模型需要的尺寸
   ↓ DMA-BUF
RKNN / NPU 推理（RKNNEngine.cpp）........ 神经网络推理（AI 检测）
   ↓ 原始输出
Decode / NMS（DecodeNMS.cpp）........... 解析 AI 输出，提取目标框
   ↓ 目标框数组
Geometry Filter ......................... 过滤掉不合理的目标
   ↓
Target Selector（TargetSelector.cpp）.... 选择最佳目标
   ↓ 瞄准任务
AimThread（AimThread.cpp）............... 目标跟踪 + 瞄准控制
   ↓ PID / 预测
Output（OutputBackend.cpp）.............. 计算鼠标移动量
   ↓ HID 协议
HID 桥接（hid/）......................... 模拟鼠标硬件
   ↓ USB 线
电脑 .................................... 鼠标移动生效
```

### 配置数据流

```
浏览器（Web UI）
   ↓ HTTP
scripts/ttbox_gateway.py ................ Web API 后端
   ↓ Unix Socket
core/src/ipc/IpcServer.cpp .............. 进程间通信
   ↓ RuntimeConfig
Application（Application.cpp）........... 核心管家
   ↓
CoreRuntime → WorkerPool → AimThread
```

### 模型数据流

```
模型文件（.rknn）......................... AI 训练好的模型
   ↓
Model Registry .......................... 模型仓库（注册/导入/安装/激活）
   ↓
Model Management ........................ 模型管理
   ↓
Model Adapter .......................... 适配器（读取模型元数据）
   ↓
RKNNEngine .............................. 加载到 NPU 推理
```

## 目录地图

```
TTBOX/
├── README.md .......................... 本文档
├── core/ .............................. TTBOX 核心程序（C++ 源码）
│   ├── CMakeLists.txt ................ 构建配置
│   ├── src/ .......................... 全部源码
│   │   ├── main.cpp .................. 程序入口
│   │   ├── app/Application.cpp ...... 应用生命周期（总管家）
│   │   ├── runtime/CoreRuntime.cpp .. 运行时核心（启动/停止管线）
│   │   ├── capture/V4L2Capture.cpp .. HDMI 采集
│   │   ├── rga/RgaProcessor.cpp ..... 硬件缩放
│   │   ├── rknn/RKNNEngine.cpp ...... NPU 推理引擎
│   │   ├── rknn/WorkerPool.cpp ...... 多线程推理池
│   │   ├── rknn/DecodeNMS.cpp ....... AI 结果解码
│   │   ├── aim/AimThread.cpp ........ 目标控制
│   │   ├── mouse/TargetSelector.cpp . 目标选择
│   │   ├── mouse/Pid1Controller.hpp . PID 控制算法
│   │   ├── output/OutputBackend.cpp . 鼠标输出
│   │   ├── hid/HidRuntime.cpp ....... HID 运行时
│   │   ├── ipc/IpcServer.cpp ........ 进程间通信
│   │   ├── common/Json.cpp .......... JSON 解析
│   │   ├── common/Logger.cpp ........ 日志
│   │   ├── common/Metrics.hpp ....... 指标定义
│   │   ├── config/ConfigManager.cpp . 配置管理
│   │   ├── model/ModelRegistry.cpp .. 模型仓库
│   │   ├── model/RuntimeProfile.cpp . 运行时配置
│   │   ├── preview/PreviewModule.cpp 画面预览
│   │   ├── auth/* ................... 授权/激活
│   │   └── input/* .................. 物理鼠标输入
│   ├── tests/ ........................ 测试代码
│   └── tools/ ........................ 工具程序
├── config/ ............................ 配置文件
│   ├── default.json .................. 默认配置
│   ├── hardware_display.json ......... 显示器配置
│   └── hdmirx_edid_identity.json ..... EDID 身份
├── models/ ............................ AI 模型文件
├── web/ ............................... 浏览器控制面板
│   └── static/ttbox-bridge.js ....... 浏览器 ⇔ 设备桥接
├── scripts/ ........................... 部署/测试脚本
│   ├── ttbox_gateway.py .............. Web API 后端
│   ├── edid/ ......................... EDID 管理工具
│   └── a9_*.sh ....................... 硬件测试脚本
├── hid/ ............................... HID 桥接配置
├── docs/ .............................. 项目文档
└── ttbox-hid-bridge.c ................ HID 桥接 C 程序
```

## Web 架构

```
浏览器（你的电脑 Chrome）
   ↓ 访问 http://192.168.0.53:8081
Web UI（ttbox-bridge.js + 前端页面）
   ↓ HTTP API
scripts/ttbox_gateway.py .............. 网关（翻译 YU 协议的 API）
   ↓ Unix Socket /tmp/ttbox_core.sock
core/src/ipc/IpcServer.cpp ............ IPC 通信
   ↓
Application / CoreRuntime / ...  ...... C++ 核心
```

## 各目录详细说明

### core/ — 核心程序

这是 TTBOX 的大脑。全部用 C++ 编写，是真正的核心。

详见 `docs/TTBOX_CODE_MAP_CN.md`。

### config/ — 配置文件

- `default.json`：所有可调参数（AI 置信度、PID 参数、截取尺寸等）
- `hardware_display.json`：HDMI 显示器设置（分辨率、EDID 身份）
- `hdmirx_edid_identity.json`：EDID 身份信息（厂商/产品名/序列号）
- `yolo261n-rk3588.json`：AI 模型配置

### models/ — AI 模型

存放 .rknn 格式的 AI 模型文件（已训练好的神经网络）。

### web/ — 网页控制面板

通过浏览器访问设备 IP 就能看到控制面板。`ttbox-bridge.js` 是前端和后端通信的桥梁。

### scripts/ — 脚本和工具

- `ttbox_gateway.py`：Web API 后端，处理浏览器请求并转发给 C++ 核心
- `edid/`：EDID 生成和注入工具
- `a9_*.sh`：硬件测试脚本（USB 速度测试、HID 功能测试等）

### hid/ — HID 桥接

将 TTBOX 的鼠标指令通过 USB 模拟成真实鼠标。包括配置文件、描述符和清单文件。

### docs/ — 文档

项目设计文档、性能报告、架构说明等。

## 新人阅读路线

如果你完全不懂代码，建议按以下顺序阅读：

1. **README.md**（本文档）—— 先了解项目全貌
2. **docs/TTBOX_CODE_MAP_CN.md** —— 小白代码地图
3. 看架构图，理解数据流怎么走
4. 看 **Application**（总管家，程序怎么启动的）
5. 看 **Capture**（画面怎么进来的）
6. 看 **RKNN / WorkerPool**（AI 怎么推理的）
7. 看 **DecodeNMS**（AI 结果怎么解析的）
8. 看 **AimThread**（目标怎么控制的）
9. 看 **Output / HID**（鼠标指令怎么发出去的）
10. 最后看具体实现细节

## 硬件要求

- Orange Pi 5 Plus（RK3588）
- HDMI 输入（RK3588 内置 HDMI RX 接口）
- 至少 4GB 内存
- 散热片/风扇（满载时 NPU 温度可达 78°C+）

## 快速开始

```bash
# 构建核心
cd core && mkdir -p build && cd build
cmake .. && make -j4

# 运行
./build/ttbox_core_main --config ../config/default.json

# 打开 Web 控制面板
# 浏览器访问 http://<设备IP>:8081
```

## 技术栈

| 组件 | 技术 |
|------|------|
| 核心语言 | C++17 |
| AI 推理 | RKNN（Rockchip NPU API） |
| 硬件缩放 | RGA（Rockchip Graphics Accelerator） |
| 视频采集 | V4L2（Video for Linux 2） |
| 进程通信 | Unix Domain Socket |
| Web 后端 | Python Flask |
| 前端 | JavaScript（YU 前端移植） |
| 构建系统 | CMake |
| 系统服务 | systemd |
