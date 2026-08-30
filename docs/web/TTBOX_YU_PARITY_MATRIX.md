# TTBOX_YU_PARITY_MATRIX.md — yu ↔ TTBOX 完全对齐矩阵

> 原则：**yu 每个用户功能都有 TTBOX 对应项**。Core 没有的保留 UI + 标记"开发中"，不删功能。
> 状态：READY=接真Core / TODO=UI在+Core待实现 / PLANNED=UI待建+Core待实现 / HW=需硬件

| # | yu 功能 | yu 页面 | TTBOX 页面 | 当前 Core | 状态 |
|---|---|---|---|---|---|
| 1 | 运行启停 | 总览 | 总览 | RUNTIME_CONTROL | ✅ READY |
| 2 | 开机自启动 | 总览侧栏 | 总览 | 无 IPC | 🔧 TODO（systemd 可做） |
| 3 | 实时画面预览 | 总览 | 总览 | 无编码 | 🔧 TODO（G2） |
| 4 | 运行数据（延迟/采集FPS/检测FPS） | 总览 | 总览 | G1 metrics | ✅ READY |
| 5 | 目标状态（待机/主控） | 总览 | 总览 | mailbox | ✅ READY |
| 6 | 实时锁定 | 总览 | 总览 | — | ✅ READY |
| 7 | 快速调整（截取尺寸/FOV/偏移/置信度/IoU） | 总览 | 总览 | capture/fov/inference | ✅ READY |
| 8 | 板载资源/重启/关机 | 总览 | 总览 | 无 | 🔧 TODO（G3+systemd） |
| 9 | 热键档案（多套） | 热键控制 | 热键控制 | 单套热键 | 🔧 TODO（Core P1） |
| 10 | 屏蔽物理按键 | 热键控制 | 热键控制 | 无硬件通路 | ⏸ HW |
| 11 | 一键禁用所有热键 | 热键控制 | 热键控制 | hotkey_guard | ✅ READY |
| 12 | 自动标定 | 移动控制 | 移动控制 | 无 | 🔧 TODO（Core P2） |
| 13 | PID 控制器 10 参数 | 移动控制 | 移动控制 | pid1 全字段 | ✅ READY |
| 14 | 基础死区 | 移动控制 | 移动控制 | 死配置→Core P2 | 🔧 TODO |
| 15 | 目标锁定（转火延迟） | 移动控制 | 移动控制 | lost_grace_ms | ✅ READY |
| 16 | 开火延迟释放Y轴 | 移动控制 | 移动控制 | 死配置→Core P1 | 🔧 TODO |
| 17 | 拉枪曲线 | 移动控制 | 移动控制 | 无 | 🔧 TODO（Core P2） |
| 18 | 持续提前量 | 移动控制 | 移动控制 | 无 | 🔧 TODO（Core P2） |
| 19 | 屏蔽物理移动 | 移动控制 | 移动控制 | 无硬件通路 | ⏸ HW |
| 20 | 个性曲线训练 | 移动控制+训练页 | 移动控制 | 无 | 🔧 TODO（Core P3） |
| 21 | 压枪 | 辅助功能 | 辅助功能 | 无 | 🔧 TODO（Core P3） |
| 22 | 自动开火 | 辅助功能 | 辅助功能 | 无 | 🔧 TODO（Core P3） |
| 23 | 连点 | 辅助功能 | 辅助功能 | 无 | 🔧 TODO（Core P3） |
| 24 | 自动背闪 | 辅助功能 | 辅助功能 | 无 | 🔧 TODO（Core P3） |
| 25 | 准星找色 | 辅助功能 | 辅助功能 | 无 | 🔧 TODO（Core P3） |
| 26 | 模型列表/当前模型 | 模型库 | 模型库 | MODEL_LIST | ✅ READY |
| 27 | 模型导入（RKNN/ONNX/HEF） | 模型库 | 模型库 | MODEL_IMPORT(rknn) | ✅ READY（ONNX/HEF 转换待 Core） |
| 28 | 模型切换/删除 | 模型库 | 模型库 | MODEL_ACTIVATE/REMOVE | ✅ READY |
| 29 | 连接 Windows 电脑 | 模型库 | 模型库 | 无 | 🔧 TODO |
| 30 | 类别名称编辑 | 模型库 | 模型库 | 无 class_names | 🔧 TODO |
| 31 | 游戏配置/绑定预设 | 模型库 | 模型库 | 无 | 🔧 TODO |
| 32 | 显示器模式/环出 | 显示与鼠标 | 画面输入 | GET_HDMI 只读 | 🔧 TODO（写回=HW） |
| 33 | USB 鼠标硬件信息 | 显示与鼠标 | 画面输入 | 无 | 🔧 TODO（HW） |
| 34 | Hailo-8 加速 | Hailo-8加速 | （并入辅助/系统） | RKNN 无 Hailo | ⏸ DEFERRED |
| 35 | 键鼠盒子 5 协议 | 键鼠盒子 | 键鼠盒子 | 无 | 🔧 TODO（HW） |
| 36 | Wi-Fi / AP 热点 | 网络配置 | 网络配置 | 无 | 🔧 TODO |
| 37 | 局域网黑名单 | 网络配置 | 网络配置 | 无 | 🔧 TODO |
| 38 | 预设保存/加载/导入/重命名 | 预设参数 | 预设参数 | 无 IPC | 🔧 TODO（Web localStorage 过渡） |
| 39 | 主题商店 | 主题商店 | 主题商店 | 无 | ⏸ DEFERRED |
| 40 | 设备授权 | 系统状态 | 系统状态 | 授权占位 | ⏸ DEFERRED（产品决策） |
| 41 | 系统更新/切换版本 | 系统状态 | 系统状态 | 无 OTA | ⏸ DEFERRED |
| 42 | 存储容量/扩容 | 系统状态 | 系统状态 | 无 | 🔧 TODO |
| 43 | 风扇控制 | 系统状态 | 系统状态 | 无 | 🔧 TODO（HW） |
| 44 | 温度来源 | 系统状态 | 系统状态 | 无 | 🔧 TODO（G3 可读温度） |

**对齐率：44 项功能全有对应页；READY=14（32%），TODO=24（55%），HW=3，DEFERRED=4。**

## TTBOX 页面结构（12 导航页 + 风扇并入系统）

```
01 总览（home）
02 热键控制（profiles）
03 移动控制（control）
04 辅助功能（assist：压枪/开火/连点/背闪/找色 全 UI 保留）
05 模型库（model）
06 显示与鼠标（hardware）
07 键鼠盒子（kmbox）
08 网络配置（wifi）
09 预设参数（preset）
10 系统状态（license：授权/更新/存储/风扇）
（Hailo 页并入系统状态 → 标记开发中）
（主题商店 → 并入系统状态 → 标记开发中）
```
