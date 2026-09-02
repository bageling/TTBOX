# TTBOX 自动标定行为模型

> 版本：Phase 8.4（行为级逆向与重构）
> 日期：2026-09-01
> 目标：把参考实现中确认的“行为”抽象为 TTBOX 自己的 Calibration Domain，不复制外部产品代码、daemon、ABI 或命名。

---

## 1. 行为模型结论

自动标定优化的不是一个孤立的“灵敏度数字”，而是一个闭环响应模型：

```text
给定已知输入移动量 count
  ↓
观察真实目标在画面中的位移 px、误差变化和到达时间
  ↓
过滤目标切换、丢失、抖动和异常事件
  ↓
按 X/Y 轴分别估计 px/count 与响应延迟
  ↓
检查多轮测量的一致性和收敛质量
  ↓
生成候选 CalibrationResult
  ↓
通过范围/质量门槛后写入配置并应用
```

优化目标按优先级为：

1. **响应增益可测**：实际画面位移与已知鼠标 count 的比例稳定。
2. **响应延迟可测**：输入动作到目标/误差发生有效变化的时间稳定。
3. **观测可靠**：目标没有切换、丢失或异常跳跃。
4. **多轮一致**：不能由一次假阳或一次偶然移动生成结果。
5. **应用可回滚**：失败不污染旧参数；取消恢复标定前状态。

---

## 2. 参考实现的真实行为（基于真机代码、AArch64 符号和指令证据）

### 2.1 触发与入口

| 环节 | 事实 |
|---|---|
| Web 按钮 | `startAutoCalibrationButton` 打开确认框；确认按钮调用 `POST /api/control/calibration/start` |
| Web 保存 | `PUT /api/control/calibration` 只校验三个人工参数，再透传 daemon |
| Web 状态 | `GET /api/control/calibration` 透传完整 `runtime` + `calibration` |
| 算法 owner | `aiassistance::AppDaemon::AimLoop()`，不是 Web Python |
| 取消 | `POST /api/control/calibration/cancel` → daemon 状态取消 |
| 清除 | `DELETE /api/control/calibration` → daemon 清除标定结果 |

### 2.2 状态机

从 YU 二进制字符串、前端状态枚举和运行字段确认的状态：

```text
IDLE
  ↓ start_auto_calibration
PREPARING / STARTING
  ↓ 检查运行状态、鼠标输出、按键状态、目标候选
STABILIZE_X
  ↓ X 轴候选窗口稳定
MEASURE_X_RESPONSE
  ↓ X 轴已知幅度输入 + 观测
MEASURE_X_SETTLE
  ↓ X 轴测量拟合/稳定
STABILIZE_Y
  ↓ Y 轴候选窗口稳定
MEASURE_Y_RESPONSE
  ↓ Y 轴已知幅度输入 + 观测
MEASURE_Y_SETTLE
  ↓ Y 轴测量拟合/稳定
SAVING
  ↓ 结果范围、质量、一致性通过
COMPLETED

任意阶段：
  CANCELLED（用户取消/状态失效）
  FAILED（无目标、目标不稳定、拟合失败、未收敛、输入异常）
```

前端显示字段包括：

```text
phase
status
running
ready
reason/error
round / total_rounds
progress
candidate_track_id
candidate_class_id
candidate_count
candidate_rect
stable_frames
stable_ms
center_jitter_px
size_variation
amplitude_counts
elapsed_ms
```

### 2.3 输入条件

从 YU 页面提示、二进制错误字符串和状态字段确认：

- 推理和采集必须运行。
- 鼠标输出必须已连接。
- 物理鼠标按键必须释放，视角和鼠标保持静止。
- 画面必须有真实检测目标。
- 候选目标需要完整可见、尺寸合理、远离画面边缘。
- 自动标定期间不能同时进行运动训练：二进制包含 `stop automatic calibration before motion training`。
- 目标候选不是简单 `target_found` 布尔值，而是带 track/class/rect 的候选窗口。

### 2.4 稳定候选窗口

YU 二进制存在：

- `CalibrationCandidateWindow::Reset(reason)`
- `IsSameAimTargetCandidate(TrackedTarget, class_id, cv::Rect, ...)`
- `candidate_track_id`
- `candidate_class_id`
- `candidate_count`
- `candidate_rect`
- `stable_frames`
- `center_jitter_px`
- `size_variation`

因此 YU 的稳定判断语义是：在连续窗口内，同一个候选目标持续存在，中心抖动和尺寸变化在阈值内；目标切换或丢失会重置窗口，而不是继续使用旧目标。

### 2.5 采样与观测

YU 二进制存在 `CalibrationObservation` deque 和 `MedianObservationCenter`，说明观测不是单帧直接使用：

```text
每轮动作：
  记录动作开始时刻
  记录动作前候选目标中心
  注入已知鼠标 count
  连续采集目标观测
  每条观测保存时间/中心/误差/动作方向等数据
  使用窗口中心的中位数降低异常点影响
```

关键字段字符串：

```text
measurement_dt
measurement_delta
raw_measured_error
measured_error
measured_target_motion
target_motion_recovered
target_motion_recovery_samples
target_motion_vector
```

说明：目标短暂丢失后，YU 有恢复采样路径；丢失不一定立即接受或立即失败，而是记录恢复样本并在质量判断时决定是否可用。

### 2.6 输入幅度与方向

从 `.rodata` 直接读出 `kCalibrationAmplitudes`：

```text
8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128
```

从二进制符号确认还有 `AimLoop()::kCalibrationLegMultipliers`，其原始整数内容为：

```text
+1, -2, +1
```

这是动作腿方向/回中策略的证据，不能解释成单一正向位移。实际行为是分腿往返，正向动作后有反向/回中动作，避免把目标永久推离可观测区域。

### 2.7 中位数与轴向拟合

`MedianObservationCenter()` 的 AArch64 指令显示：

- 分别收集 X/Y 观测值。
- 对 X/Y 独立排序并求中位数。
- 返回中位数中心，而不是均值中心。

`CalibrationEstimatedAxisGain(AutoCalibrationSession)` 和 `CalibrationAxisFit` 说明：

- X/Y 轴分别拟合。
- 结果不是简单取某一帧最大位移。
- 至少存在轴拟合失败信息和候选结果质量判断。
- 增益范围是 `0.03~8.0 px/count`。
- 响应延迟范围是 `0~50ms`。

当前可确认的数学目标：

```text
axis_gain ≈ robust_center(measured_delta_px / injected_delta_count)
response_delay ≈ robust_center(first_valid_motion_time - injection_time)
```

其中 `robust_center` 至少包含中位数/窗口稳健处理；YU 的完整拟合函数体在二进制中，未提供可直接阅读的源代码，因此不把未知细节伪装成已知公式。

### 2.8 进度计算

`CalibrationProgress()` 指令级证据显示：

- 进度以当前阶段和轮次共同计算。
- 轮次占比使用总计 10 轮相关字段。
- 在未开始/异常阶段返回基于状态的固定或阶段进度。
- 进度不是“请求返回后固定 100%”。

### 2.9 保存与应用

成功路径：

```text
拟合 X/Y
  ↓
检查增益范围、延迟范围、观测数量、一致性
  ↓
写 calibration 配置
  ↓
mouse_calibration_applied=true
  ↓
更新运行参数
  ↓
状态 completed/success
```

失败/取消路径不应覆盖原有效标定。人工保存只允许三项有限数值，并由后端校验范围。

---

## 3. TTBOX 当前行为

### 3.1 当前实现链路

```text
浏览器
  ↓ POST /api/control/calibration/start
scripts/ttbox_web.py::start_auto_calibration
  ↓ _calib_target() 读取 Core GET_STATUS.metrics.aim_pos_x/y
_calib_worker()
  ↓ SET_CONFIG mouse.calibrating=true
  ↓ _calib_worker_inner()
  ↓ 稳定窗口：10 个位置样本、X/Y 抖动范围 <1px、持续 800ms
  ↓ 10 轮 X 轴动作
  ↓ 每轮固定 20×50ms 采样
  ↓ 最大 X 位移 / 注入幅度
  ↓ 至少 5 个有效 gain
  ↓ X 中位数，Y 直接复用 X
  ↓ 写 calibration.json + 换算 kp
  ↓ finally 恢复 enabled/calibrating
```

### 3.2 TTBOX 当前真实优点

- 已经有真实 Core 目标中心出口：`AimThread Status.predicted_x/y → Metrics → IPC GET_STATUS`。
- 已经有 `RuntimeConfig` 热更新和 `mouse.calibrating` 放行模式。
- 已经有临时文件替换写入。
- 已经有无目标拒绝、至少 5 个有效测量门槛、取消后恢复开关状态。
- 自动/人工保存都不会依赖外部参考服务。

### 3.3 TTBOX 当前具体不足

| 行为 | 当前问题 | 后果 |
|---|---|---|
| 目标候选 | 只读 `aim_has_target + aim_pos`，没有 track/class/rect 身份 | 目标切换时可能把不同目标拼成一轮数据 |
| 稳定判断 | 只看 10 个中心点范围 | 没有尺寸变化、候选身份、恢复样本判断 |
| 采样轴 | 只测 X，Y 直接等于 X | 无法反映 X/Y 不同增益和延迟 |
| 观测中心 | 每次只拿当前点，最终取最大位移 | 对异常点敏感，最大值会放大噪声 |
| 拟合 | `max_dx / amp` | 不是分轴稳健拟合，也没有残差/一致性 |
| 收敛 | 只有 `len(gains) >= 5` | 有效数量够但结果分散时仍可能接受 |
| 动作 | 注入 FIFO/偏置的路径与 Core 当前输出后端不完全统一 | 标定输入和正式输出可能不是同一执行路径 |
| 状态 | `stabilize/moving/measuring/done/error` 较粗 | Web 无法显示 X/Y 具体阶段和拟合过程 |
| 失败信息 | 只有“有效测量不足/目标不稳定”等少数原因 | 用户无法知道是切换、抖动、恢复还是拟合失败 |
| 参数应用 | 自动标定直接覆盖 kp | 结果与原 PID 状态的关系不够透明，回滚信息不足 |

---

## 4. TTBOX 行为级重构方案

### 4.1 Domain 对象

```text
CalibrationSession
├── id
├── state: IDLE/PREPARING/STABILIZE_X/SAMPLE_X/ANALYZE_X/
│         STABILIZE_Y/SAMPLE_Y/ANALYZE_Y/VALIDATING/APPLYING/
│         COMPLETED/CANCELLED/FAILED
├── started_at
├── elapsed_ms
├── current_axis: X/Y
├── current_iteration
├── total_iterations
├── amplitude_counts
├── candidate
├── stable_window
├── measurements[]
├── valid_sample_count
├── error_metrics
├── candidate_parameters
└── final_parameters
```

```text
CalibrationObservation
├── timestamp_ms
├── axis
├── injected_count
├── target_center_x/y
├── target_motion_x/y
├── measured_error_x/y
├── valid
├── target_identity
└── rejection_reason
```

```text
CalibrationAxisFit
├── axis
├── gain_px_per_count
├── response_delay_ms
├── sample_count
├── median_error
├── mad_error
├── consistency
├── converged
└── failure_reason
```

### 4.2 TTBOX 状态机决策

- `PREPARING`：确认 Core running、输出后端可用、物理按键释放、没有运动训练会话。
- `STABILIZE_X/Y`：候选身份、中心抖动、尺寸稳定必须同时满足。
- `SAMPLE_X/Y`：两轴独立采样；每个幅度至少正向和回中动作。
- `ANALYZE_X/Y`：每轮使用中位数中心，生成增益/延迟候选。
- `VALIDATING`：检查范围、有效样本数、MAD/一致性和 X/Y 结果完整性。
- `APPLYING`：先保存旧 RuntimeProfile 快照，再原子更新 calibration 与运行参数。
- `COMPLETED`：只在保存和 Core 回读都成功后进入。
- `CANCELLED/FAILED`：恢复标定临时状态，不覆盖旧有效结果。

### 4.3 阈值决策

首版 TTBOX 采用可解释阈值：

- 稳定窗口：至少 10 个连续观测，中心 X/Y 范围 `<1.0px`，尺寸变化 `<5%`。
- 每轴至少 5 个有效动作观测；X/Y 都满足才可完成。
- 单轮观测使用中位数中心，不使用最大位移作为唯一结果。
- 增益必须在 `0.03~8.0 px/count`。
- 响应延迟必须在 `0~50ms`。
- 轴向增益的 median absolute deviation（MAD）相对中位数不得超过 `35%`；超出进入 FAILED，不强行应用。
- 目标身份改变、连续丢失超过恢复窗口或输入动作无新鲜回执时，该轮作废并重新稳定，不混入结果。

这些阈值属于 TTBOX 产品决策，不声称是参考实现的逐字常量；参考实现提供的是行为方向和质量门槛，TTBOX 保持可解释、可测试和可回滚。

---

## 5. “已保存”反复出现的根因调查

### 5.1 已确认调用链

普通参数：

```text
[data-config] input/change
  ↓ web/static/app.js::requestConfigApply()
  ↓ applyConfigNow()
  ↓ PUT /api/config
  ↓ setApplyStatus("ready", "已同步")
  ↓ queueCurrentPresetAutosave()
```

手动标定：

```text
saveAutoCalibrationValuesButton click
  ↓ saveAutoCalibrationValues()
  ↓ PUT /api/control/calibration
  ↓ renderAutoCalibration(result)
  ↓ showToast("手动标定参数已保存")
```

自动标定：

```text
confirmAutoCalibrationButton click
  ↓ startAutoCalibration()
  ↓ POST /api/control/calibration/start
  ↓ showToast("自动标定已开始")
  ↓ pollAutoCalibration() 每 250ms
  ↓ renderAutoCalibration()
```

### 5.2 当前能确认的结论

- 自动标定状态轮询没有直接调用 `showToast("已保存")`。
- 普通配置自动保存使用 `setApplyStatus`，不是 Toast；所以“已保存”如果在自动标定页面连续出现，来源更可能是手动保存按钮、预设自动保存或其他页面事件。
- `runUiAction()` 对异常统一使用错误 Toast，不会产生成功 Toast。
- 仍需在真实浏览器点击一次控件并记录 `PUT /api/config`、`PUT /api/control/calibration` 请求数量，才能排除前端重复事件绑定。

### 5.3 TTBOX 处理决策

建立操作来源语义：

```text
USER_SAVE          用户明确点击保存/手动标定保存
CALIBRATION_APPLY  自动标定内部应用候选参数
SYSTEM_SYNC        后台轮询/刷新状态
AUTO_PRESET_SAVE   当前预设自动保存
```

自动标定内部只更新状态徽标和阶段信息，不调用用户保存 Toast；只有用户明确点击手动保存，才显示“手动标定参数已保存”。

---

## 6. 验收标准

### 代码级

- Python 语法通过。
- Core 编译通过。
- Domain 状态机、稳态窗口、中位数、MAD、分轴拟合单元测试通过。
- 取消/失败不污染旧 calibration 和 RuntimeProfile。

### API 级

- start：无运行/无目标/按键按下时返回真实原因。
- status：至少返回当前阶段、进度、轴、轮次、候选和有效样本数。
- cancel：状态进入 CANCELLED，标定临时开关恢复。
- result：只有 COMPLETED 才有 valid=true。
- apply：Core 回读与保存结果一致。

### 浏览器级

```text
读取当前状态
→ 浏览器点击开始
→ 页面显示 PREPARING/STABILIZE/SAMPLE/ANALYZE/VALIDATING
→ 目标真实存在时显示真实进度
→ 完成后显示候选参数和结果
→ 浏览器点击应用/或自动应用完成
→ 刷新页面
→ 状态和参数保持
→ 恢复原值
```

没有真实目标时只能验证：页面读取、真实拒绝、错误原因、取消和状态恢复；不把无目标拒绝写成完成。

---

## 7. 未解析边界

- YU `CalibrationEstimatedAxisGain` 的完整拟合公式无法从 stripped/局部保留符号的二进制中直接恢复；当前结论只使用已验证的函数名、字段、常量和中位数指令证据。
- YU 候选窗口的全部数值阈值尚未从指令级逐常量还原；TTBOX 阈值明确标为产品决策，不冒充参考常量。
- 真机没有真实目标时，自动标定完整 10 轮的浏览器闭环待后续 HDMI 场景验收。
