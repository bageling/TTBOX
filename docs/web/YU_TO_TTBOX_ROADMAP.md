# YU → TTBOX 功能迁移路线图（YU_TO_TTBOX_ROADMAP）

> 标记含义：
> - READY：TTBOX 已具备能力，可直接接入
> - WEB_ONLY：只需 Web 层工作
> - IPC_REQUIRED：需要新增 IPC/API
> - CORE_REQUIRED：需要新增 Core 能力
> - HARDWARE_REQUIRED：需要真实硬件支持
> - DEFERRED：暂不迁移（由用户最终决定）

## A. 核心瞄准链（READY/WEB_ONLY）

| yu 功能 | 状态 | TTBOX 现状 | 备注 |
|---|---|---|---|
| 运行启停 | READY | RUNTIME_CONTROL ✅ | Web 已接 |
| 配置读写 | READY | GET/SET_CONFIG ✅ | Web 已接（PUT 回读 canonical） |
| PID 六组参数 | READY | mouse.kp/kd/ki/predict/rate/smooth ✅ | Web 移动控制页已接 |
| 基础死区 | READY | mouse.output_deadzone ✅ | Web 已接 |
| 转火延迟 | READY | mouse.lost_grace_ms ✅（ms 语义） | yu 用 ms；TTBOX 已对齐 |
| 开火延迟释放 Y | CORE_REQUIRED | 字段已定义但 Core 未消费（死配置，2026-08-30 BUG 扫描发现）；需要 AimThread 实现开火判定 | 修复后 Web 再接 |
| 移动倍率 | READY | mouse.rate? 语义：sens 无对应 → WEB_ONLY 映射到 fov? | **差异：TTBOX 无全局 sens**，移动倍率=rate 已含 |
| 目标置信度/IOU | READY | inference.confidence/iou ✅ | Web 已接 |
| 截取尺寸/偏移 | READY | capture.* ✅ | Web 检测页已接 |
| FOV 范围 | READY | fov.* ✅ | Web 总览已接 |
| 热键总开关/主副热键/any-all | READY | mouse.aim_hotkey/2/mode + hotkey_guard ✅ | Web 辅助页已接 |
| 屏蔽物理按键 | HARDWARE_REQUIRED | 字段已定义但无消费路径；yu 靠 makcu/usb-proxy 硬件拦截物理鼠标，TTBOX 物理鼠标是只读旁路（供热键门控），需硬件通路才能实现 |
| 当前目标状态 | WEB_ONLY | mailbox 有数据（GET_STATUS detect_count） | 总览"实时锁定"待补 UI |
| 模型列表/导入/切换/删除 | READY | MODEL_* 五条 ✅ | Web 模型库已接 |
| 模型类别名 | WEB_ONLY | 无类别名字段 → WEB_ONLY 需 Core 扩展 class_names | 待定 |
| 预设保存/加载 | WEB_ONLY | Web 端 localStorage JSON（真配置快照，非假保存） | 见 G5 |

## B. 需要新增 IPC（IPC_REQUIRED）

| yu 功能 | 需新增 | 工作量 |
|---|---|---|
| 实时画面预览 | GET_PREVIEW（JPEG 帧）+ 编码器 | 大（G2） |
| 系统资源（温度/CPU/内存） | GET_SYSTEM（板端旁路服务，不进 Core） | 小（G3） |
| 事件推送 | GET_EVENTS SSE | 中（轮询可替代） |
| 自启动开关 | SET_AUTOSTART | 小（systemd 管理） |

## C. 需要新增 Core 能力（CORE_REQUIRED，分阶段）

| yu 功能 | 说明 | 阶段 |
|---|---|---|
| 热键多档案（aim_profiles） | RuntimeProfile 需扩展 profiles 数组 | P1 |
| 开火延迟释放 Y 轴 | AimThread 增加开火判定（y_axis_fire_hotkey 位图比对）+ 释放计时；字段已存在（死配置已发现） | P1 |
| 转火搜索半径 | TargetSelector 增加配置化搜索半径（当前用自适应 0.06×框宽） | P2 |
| 基础死区 output_deadzone | 输出链路增加死区过滤（AimThread send 前） | P2 |
| 屏蔽物理移动 | 需要 USB 中间人硬件通路（yu 用 usb-proxy），纯软件无法实现 | P4/HW |
| 拉枪曲线 | PID1 需新增 pull_curve 模块（或输出后处理） | P2 |
| 持续提前量 | 需新增 lead 预测模块（X 轴同向累计偏置） | P2 |
| 自动标定 | 需新增标定流程（增益测定） | P2 |
| 压枪 | 需新增 recoil 模块（独立于瞄准的 Y 轴输出） | P3 |
| 连点 | 需新增 rapid_fire 模块（HID 按键注入） | P3 |
| 自动背闪 | 需新增 back_flick 模块（转向序列） | P3 |
| 准星找色 | 需新增 crosshair 模块（ROI 颜色检测） | P3 |

## D. 需要真实硬件（HARDWARE_REQUIRED）

| yu 功能 | 依赖 |
|---|---|
| 显示器模式切换 | HDMI EDID 写回（TTBOX 目前只读） |
| USB 鼠标硬件描述 | USB gadget 配置（需要 USB OTG 硬件） |
| 键鼠盒子协议 | MAKCU/Ferrum/kmboxB 硬件设备 |
| 风扇控制 | PWM 风扇硬件 |

## E. 暂不迁移（DEFERRED，标记待用户决策）

| yu 功能 | 原因 |
|---|---|
| Hailo-8 加速 | TTBOX 架构用 RKNN NPU，Hailo 是另一套硬件 |
| 主题商店 | 运营向，与 TTBOX 定位冲突（自定义主题可后做） |
| WiFi/网络配置 | 平台层，TTBOX 作为独立设备可后做 |
| 授权激活 | TTBOX 无在线授权（license 占位），产品决策待定 |
| 系统更新 | TTBOX 无 OTA 通道 |
| 远程导入（连 Windows） | 依赖远程服务架构 |
