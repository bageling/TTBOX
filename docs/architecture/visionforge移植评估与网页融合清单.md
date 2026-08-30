# VisionForge移植评估与网页融合清单

> 目标：以 TTBOX 为香橙派 5 Plus 主干，吸收 VisionForge 的网页界面思路、参数组织方式和经过验证的算法模块。

> **⚠ 已过时（2026-08-29 Web 重置）**：文中引用的 `core/tools/web/`（AIBOX 旧控制台）已整体移除；本文仅作历史评估参考，Web 前端待新底座重建。

## 一、当前结论

VisionForge 本身是 Windows Python/Tkinter 桌面程序，仓库中没有可直接拿来替换的现成业务网页前端。TTBOX 当前已经有一套可在板端运行的 HTML/CSS/JavaScript 控制台：

```text
core/tools/web/index.html
core/tools/web/static/style.css
core/tools/web/static/adapter.js
core/tools/web/ttbox_web.py
```

因此 UI 的正确处理方式不是把 Tkinter 搬上板，而是：

```text
VisionForge Tkinter UI 的页面组织、参数说明、主题令牌
        ↓
TTBOX HTML/CSS/JavaScript 控制台
        ↓
TTBOX /api/state /api/profile /api/models /api/inference /api/presets
```

当前 TTBOX 网页已经覆盖总览、热键控制、移动控制、辅助功能、模型库、显示与鼠标、网络、预设参数、主题和系统状态等页面，具备继续吸收 VisionForge UI 资产的基础。

## 二、VisionForge源码分层

### A. 可移植算法候选

| 文件 | 内容 | 迁移结论 |
|---|---|---|
| `src/target_selector.py` | 头部/身体框选择、置信度、中心距离、身体兜底、头身匹配 | 与 TTBOX `TargetSelector` 对照；迁移规则，不直接搬 Python |
| `src/detection_filter.py` | 头身几何过滤、小目标/远目标放宽、边缘框拒绝、边界统计 | 高价值候选；TTBOX 当前主要缺少这层，应先做 C++ 纯逻辑模块和固定框测试 |
| `src/target_lock.py` | 锁定确认、切换、丢失保持、GIoU、Mahalanobis、轨迹预测 | 高价值候选；与 TTBOX `TargetSelector`/`AimTracker` 对照后逐项补齐，不整体替换 |
| `src/bytetrack_tracker.py` | ByteTrack 二阶段关联 | 中高价值；先确认 TTBOX 当前 AimTracker 能力，避免重复造两套跟踪器 |
| `src/stability_profile.py` | 稳定强度、响应灵敏度、断检容忍度展开为控制参数 | 高价值配置层；适合转成 TTBOX RuntimeProfile 的派生配置 |
| `src/target_lock_profile.py` | 锁定稳定性、切换灵敏度、小目标召回、丢失保持参数展开 | 高价值配置层；先建立 TTBOX 对应字段，再接 Web |
| `src/head_aim_policy.py` | 头部限制和瞄准点约束 | 与 TTBOX `AimPointProfile` 对照，优先吸收规则和边界测试 |
| `src/onnx_input_geometry.py` | 输入尺寸、裁剪、缩放和几何换算 | 用来核对 TTBOX 模型输入和 ROI；板端最终由 RGA/RKNN 实现 |
| `src/personal_trajectory_shaper.py` | 个人轨迹曲线、时长、速度包络、输出保护 | 后置；先用 Trace 输出验证，不进入第一轮实时核心 |

### B. 可移植配置/UI资产

| 文件 | 内容 | 迁移结论 |
|---|---|---|
| `src/config_schema.py` | 参数元数据、范围、步长、说明、预设 | 直接提取数据结构思路；TTBOX Web 保留单一参数定义，避免前后端漂移 |
| `src/design_tokens.py`、`src/theme.py` | 主题、颜色和视觉令牌 | 转为 TTBOX CSS 变量；不引入 Tkinter |
| `src/gui_pages.py` | 页面分组和用户文案 | 转为 HTML 页面/分区，不迁移 Tk 控件代码 |
| `src/config_watcher.py` | 配置热更新和变更路径判断 | 对照 TTBOX RuntimeConfig 和 Web `/api/profile`，补齐实际消费证据 |
| `src/preview_output.py` | 预览帧生成 | 与 TTBOX `/frame.bmp`/实时预览对照，不引入桌面窗口 |

### C. 不进入板端实时核心

- `app_gui.py`、`gui_*.py`：Tkinter/CustomTkinter 桌面壳。
- `screen_capture.py`、`win32_sendinput_driver.py`、`interception_driver.py`、`ghub_driver.py`、`mouse_driver.py`：Windows 屏幕和输入后端。
- `dinvoke.py`、`stealth_injector.py`、`transacted_hollowing.py`、`inject_payload.py`：Windows 进程和注入路径。
- `time_billing.py`、账户、支付、日志上传：控制面可选，不能阻塞实时识别进程。
- `onnx_yolo_detector.py`、`ort_runtime_loader.py`：只作为 ONNX 参考，板端使用 RKNN Adapter。
- `server/visionforge-platform/`：这是网站/后台模板，不是板端实时控制台；只提取页面组织和管理接口设计。

## 三、TTBOX现有对应关系

```text
VisionForge detection_filter.py       → TTBOX DecodeNMS 后置过滤（待补几何层）
VisionForge target_selector.py         → TTBOX core/src/mouse/TargetSelector.*
VisionForge target_lock.py             → TTBOX TargetSelector + AimTracker（待逐项对照）
VisionForge bytetrack_tracker.py       → TTBOX AimTracker（确认是否需要新增）
VisionForge stability_profile.py       → TTBOX RuntimeProfile/MouseProfile（当前缺高层稳定画像）
VisionForge target_lock_profile.py     → TTBOX RuntimeProfile/MouseProfile（当前缺高层锁定画像）
VisionForge head_aim_policy.py         → TTBOX AimPointProfile.*
VisionForge config_schema.py           → TTBOX RuntimeProfile + Web 控件定义
VisionForge GUI pages/theme            → TTBOX index.html/style.css/adapter.js
VisionForge ONNX detector              → TTBOX RKNNEngine/ModelAdapter/DecodeNMS
VisionForge Windows input              → TTBOX PhysicalMouseReader/HID 单写者
```

## 四、网页融合判断

### 已有能力

- TTBOX 网页已是独立静态页面，不依赖 Tkinter。
- `adapter.js` 已连接状态、配置、推理、模型、预设、主题、HID 和硬件页面。
- 页面已包含 VisionForge 中高价值的用户参数概念：识别置信度、IoU、FOV、PID、预测、积分、微分、跟随、死区、拉枪曲线、持续提前量、物理鼠标屏蔽、预设、自动标定和个人轨迹入口。
- `ttbox_web.py` 已有 profile/preset/model/inference/state 等后端路由。

### 仍需补齐

- Web 控件与 C++ `RuntimeProfile` 字段的完整一一映射。
- `stability_profile` 和 `target_lock_profile` 的后端数据模型及 C++ 消费端。
- 检测几何过滤统计和小目标/远目标策略。
- ByteTrack 或现有 AimTracker 的明确取舍。
- 主题令牌的正式抽取，清除重复硬编码。
- 页面上的 Hailo-8 选项应明确标记为“不适用于当前 RK3588 主链”或隐藏，避免误导。

## 五、推荐施工顺序

### 第1步：先完成算法差异表

对照 VisionForge 和 TTBOX：

- 固定检测框列表。
- 固定帧序列。
- 固定 ROI/FOV。
- 固定控制参数。
- 固定目标丢失和重新出现场景。

输出每项的 FACT、差异和测试结果。

### 第2步：补检测几何过滤

新增独立 C++ 模块，输入 `vector<DetectionBox>`，输出过滤后的框和统计；不改 RKNN Worker。优先实现：

- 头部/身体置信度。
- 头身空间匹配。
- 小目标/远目标放宽。
- ROI 边缘伪框拒绝。
- 下边缘/侧边缘窄例外。

### 第3步：补高层画像

在 RuntimeProfile 增加：

```text
stability_profile
 target_lock_profile
```

先展开为已有底层字段，确认热更新链路，再把字段放进 Web。

### 第4步：补锁定和跟踪

对照 GIoU、Mahalanobis、ByteTrack、丢失保持和切换确认；只保留一套主跟踪实现，避免 TargetSelector、AimTracker、ByteTrack 三套逻辑同时抢控制权。

### 第5步：再做个人轨迹

个人轨迹曲线属于输出整形层，放在控制器之后、HID 之前；必须有 Trace 回放和限幅测试，默认关闭。

### 第6步：网页收尾

将高层参数、派生值、状态来源和是否热更新写进统一清单；每个控件必须对应后端字段和 C++ 消费点。

## 六、完成判断

VisionForge UI 融合完成的标准：

- 浏览器能打开 TTBOX 页面。
- 页面不依赖 Python GUI/Tkinter。
- 关键状态和参数来自 TTBOX API。
- 页面改参数后，C++ RuntimeProfile 消费端真实变化。
- 不适用的 Hailo/Windows 选项明确标记或移除。

算法融合完成的标准：

- 过滤、锁定、跟踪、瞄准点和控制都有固定输入测试。
- 桌面参考和 TTBOX 差异有记录、有取舍。
- 香橙派 Null/Trace 闭环通过。
- 真实 HID 只在最后独立验收。

## 七、当前状态标签

- **UI：PARTIAL/接近完成**：TTBOX 已有成熟网页控制台，VisionForge 的 Tkinter 不能直接作为网页代码，但页面组织和参数资产可继续吸收。
- **配置：PARTIAL**：TTBOX 现有 RuntimeProfile 字段较完整，但 VisionForge 的高层稳定/锁定画像尚未完整进入 C++。
- **检测过滤：UNVERIFIED**：VisionForge 的复杂几何过滤尚未在 TTBOX 复现和板端验证。
- **锁定跟踪：PARTIAL**：TTBOX已有基础 TargetSelector/AimTracker，尚未与 VisionForge 的 GIoU/Mahalanobis/ByteTrack 逐项对齐。
- **控制输出：PARTIAL**：TTBOX已有 PID/预测/Smith/FOV/Trace，`ki_x/ki_y` 当前仍有未消费警告，需要明确取舍。
- **硬件：PARTIAL**：目标板可编译，正式服务持续运行；独立测试仍受 `/dev/video0` 占用影响。

## 关联

- [[首页]]
- [[项目]]
- [[TTBOX算法融合与香橙派上板测试报告]]
- [[TTBOX桌面算法对照补齐与项目收尾方案]]
- [[TTBOX与桌面源码融合评估]]
