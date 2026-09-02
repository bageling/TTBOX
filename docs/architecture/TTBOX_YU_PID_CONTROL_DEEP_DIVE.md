# TTBOX Phase 8.5 — PID / 控制链逻辑差异报告

> 日期：2026-09-01  
> 范围：TTBOX 当前 PID、预测、移动输出、自动标定，与真机 YU 参考实现的数学和调用顺序对比  
> 基线：TTBOX Git `91bb7a0` + 当前未提交 Phase 8.4 工作树  
> 结论：本阶段只做逆向分析和报告，不修改产品代码

---

## 1. 结论先行

TTBOX 当前效果可能明显不如 YU，首要原因不是简单的参数值没有对齐，而是两套系统对同名参数的数学语义、计算位置和时间模型并不相同。

最重要的结论有四个：

1. **TTBOX 的 `predict_x/y` 不是目标位置预测时间。**  当前实际用途是放大 PID 内部速度项对 `K_i` 的贡献；`AimTracker` 和 `AlphaBetaGammaFilter` 虽然存在，但没有接入当前 `AimThread` 主控制链。
2. **TTBOX 自动标定测量量的单位存在根本问题。**  YU 注入真实鼠标 count，再测目标移动 px，得到 `px/count`。TTBOX 当前把 `calibration_bias_x/y` 加到误差像素域，再把该 bias 当作 `injected_count`，实际测到的是“目标响应 px / 误差 bias px”，不是鼠标的 `px/count`。
3. **TTBOX 标定结果没有直接进入 px→count 的输出换算。**  Gateway 把 gain 折算成 kp，但当前 PID 还有 `smooth=9900` 的内部缩放和自适应增益，因此标定结果与实际 PID 输出尺度不匹配。
4. **YU 存在独立的分步输出和队列路径。**  真机二进制中存在 `MotionController::UpdateTarget`、`StepPendingMove`、`MouseOutputWorker::SubmitMove` 和队列派发符号。TTBOX 当前是每个检测任务直接生成一帧 int16 HID 移动，输出整形明显更简单。

因此，当前第一嫌疑是：**自动标定结果的物理含义和 TTBOX 当前 PID 输出尺度不一致**。第二嫌疑是：**预测没有真正作用于目标误差，`predict` 只是 PID 内部速度项倍率**。第三嫌疑是：**TTBOX 的时间基准和 YU 的分步/队列输出模型不同，导致检测帧率变化直接改变控制行为**。

---

## 2. 证据等级

本报告所有结论使用以下标签：

- **【SOURCE FACT】**：源码直接确认。
- **【BINARY FACT】**：真机二进制的符号、字符串、常量或反汇编直接确认。
- **【BEHAVIOR FACT】**：真机服务、配置、API 或运行行为直接确认。
- **【INFERENCE】**：由多个事实推导，尚未从完整源码或完整反汇编中完全确认。
- **【SEMANTIC MISMATCH】**：同名参数在两套系统中的含义、单位或位置不同，不能直接复制数值。

YU daemon 为未剥离 AArch64 PIE，文件：

```text
/opt/aiassistance/bin/aiassistance_daemon
```

真机读取时确认：

- `ttbox-core.service`：active
- `ttbox-web.service`：active
- `aiassistance-web.service`：active
- `aiassistance-daemon.service`：active
- TTBOX：8081
- YU：8080

本阶段没有停止或重启任何服务。

---

## 3. TTBOX 当前控制链

### 3.1 总体调用链

```text
RKNN 检测结果
  ↓
DecodeNMS / 几何过滤 / FOV 过滤
  ↓
WorkerPool 发布 AimTargetTask
  ↓
AimTargetMailbox::take_latest
  ↓
AimThread::loop
  ↓
TargetSelector::select
  ↓
目标框与瞄准点
  ↓
error = target_point - reference_point
  ↓
标定 bias（如果 calibrating）
  ↓
Pid1Controller::update
  ↓
sensitivity × output_scale × personal_motion
  ↓
output_deadzone
  ↓
fractional remainder accumulator
  ↓
int16 HID dx/dy
  ↓
OutputBackend / LocalHidBackend
  ↓
9 字节 /dev/hidg0 报告
```

### 3.2 检测结果与时间戳

【SOURCE FACT】`core/src/rknn/WorkerPool.cpp:377-386`：Worker 在推理和解码后读取 `steady_clock::now()`，把该时间写入：

```text
AimTargetTask.timestamp_us
```

同时写入 frame number、frame width、frame height 和 detections。

【SOURCE FACT】`core/src/rknn/WorkerPool.cpp:387-394`：当检测结果非空时，任务默认把 `detections_.front()` 写入 `task.target`，并用框中心初始化 `task.aim_point`。

注意：Worker 的 `task.timestamp_us` 是检测任务发布附近的时间，不是摄像头曝光时间，也不是原始 HDMI 帧的硬件时间戳。

### 3.3 TargetSelector

文件：

```text
core/src/mouse/TargetSelector.cpp
core/src/mouse/TargetSelector.hpp
```

【SOURCE FACT】`TargetSelector::collect_candidates()`：

1. 过滤置信度低于 `cfg.confidence` 的框。
2. 按类别过滤。
3. 计算瞄准点与选择中心的差值。
4. 按 FOV 半径过滤。
5. 按到选择中心的距离排序。

数学形式：

```text
candidate_x = x1 + (x2 - x1) × aim_ratio_x
candidate_y = y1 + (y2 - y1) × aim_ratio_y

selection_error_x = candidate_x - roi_width  × center_x
selection_error_y = candidate_y - roi_height × center_y

distance² = selection_error_x² + selection_error_y²
```

【SOURCE FACT】`TargetSelector::select()`：

- 已锁定目标优先走 track lock。
- track ID 变化时尝试按矩形位置复用旧 track。
- 无旧锁定时按距离选择。
- 目标短暂丢失时按 `lost_grace_ms` 保留锁定。

【INFERENCE】当前 `AimTargetTask` 中的 detections 已经过坐标映射，通常使用整帧坐标；但部分注释仍称 ROI/crop 坐标。因此当前代码存在“坐标数值实际使用”和“注释表达”不完全一致的风险，必须在后续以运行时尺寸和实际坐标打印进一步核对。

### 3.4 瞄准点与误差

文件：

```text
core/src/mouse/AimPointProfile.cpp
core/src/mouse/CoordinateTransform.cpp
core/src/aim/AimError.hpp
```

【SOURCE FACT】瞄准点：

```text
w = box.x2 - box.x1
h = box.y2 - box.y1

target_x = box.x1 + offset_x × w
target_y = box.y1 + offset_y × h
```

参考点：

```text
reference_x = roi_width  / 2 + aim_offset_x
reference_y = roi_height / 2 + aim_offset_y
```

控制误差：

```text
error_x = target_x - reference_x
error_y = target_y - reference_y
```

单位：当前实现以检测坐标系像素为主，通常是整帧/ROI 映射后的 `px`。该误差没有归一化到 `[-1, 1]`，也没有在进入 `Pid1Controller` 前按屏幕宽高归一化。

### 3.5 预测阶段

当前 `AimThread` 的注释明确写着：

```text
不做位置外推；误差直接来自本帧检测结果。
速度信息只进入 P_PID 的前馈/Kalman，不在目标坐标层 coast。
```

【SOURCE FACT】`core/src/aim/AimThread.cpp:118-119`：当前主链不使用目标位置预测。

虽然仓库存在：

```text
core/src/mouse/AimTracker.cpp/.hpp
core/src/aim/AlphaBetaGammaFilter.hpp
```

但【SOURCE FACT】当前 `AimThread::loop()` 没有调用 `AimTracker::update()`、`AimTracker::predict()` 或 `AlphaBetaGammaFilter::predicted()`。

因此当前实际链路不是：

```text
位置 → 速度估计 → 预测位置 → 误差
```

而是：

```text
当前检测框 → 当前瞄准点 → 当前误差
```

### 3.6 标定 bias

【SOURCE FACT】`core/src/aim/AimThread.cpp:130-135`：标定时执行：

```text
control_x = error_x + calibration_bias_x
control_y = error_y + calibration_bias_y
```

这个 bias 加在控制误差域，单位是误差像素，而不是鼠标 HID count。

### 3.7 FOV 模式

【SOURCE FACT】如果开启 FOV 模式，`AimThread` 会通过：

```text
fov_move_x(control_x, ...)
fov_move_y(control_y, ...)
```

把像素误差换成基于角度的移动量。

`FovAngle.hpp` 的数学形式是：

```text
half_fov = fov_deg × π / 180 / 2
sup      = resolution / 2 / tan(half_fov)
angle    = atan(abs(error_px) / sup)
move     = angle × per_pixel_rad
```

其中：

```text
per_pixel_rad_x = move_speed_x / (2π)
per_pixel_rad_y = move_speed_y / π
```

FOV 模式下进入 PID 的 `control_x/control_y` 已经不是原始像素误差，而是 FOV 转换后的移动量。由此可见，PID 输入单位会随 FOV 模式改变。

### 3.8 dt 实际没有进入 PID

【SOURCE FACT】`AimThread.cpp` 会计算：

```text
previous_timestamp_us = last_timestamp_us_
dt = current_timestamp - previous_timestamp
```

但随后存在：

```text
(void)dt;
```

并直接调用：

```text
pid_x_.update(control_x)
pid_y_.update(control_y)
```

`Pid1Controller::update()` 的签名只有一个 `error` 参数，没有 `dt`。

因此当前实际情况是：

- `dt` 被计算。
- `dt` 没有进入 P、I、D 公式。
- PID 使用“每次调用”作为一步。
- 如果 mailbox 跳过多帧，PID 仍然只看到一次误差更新。

### 3.9 PID 调用

【SOURCE FACT】`core/src/aim/AimThread.cpp:152-153`：

```text
pid_x_.update(control_x)
pid_y_.update(control_y)
```

`controller_` 旧版 `MotionController` 在当前主控制路径中没有执行 `update()`；它主要在状态切换时 reset。

---

## 4. TTBOX PID 数学公式

文件：

```text
core/src/aim/Pid1Controller.hpp
```

### 4.1 输入预处理

给定本次误差 `e_n`：

```text
如果 |e_n| < 0.3：
    e_n = 0
```

如果：

```text
|e_n - e_(n-1)| > 30
```

则执行 reset。

这意味着检测框抖动、目标切换或突然跳变可能清空 PID 内部状态。

### 4.2 自适应比例增益

控制器内部有：

```text
kp_gain_threshold = 1920
kp_gain_rate       = rate_x 或 rate_y
```

【SOURCE FACT】当误差小于阈值时，目标比例增益近似为：

```text
kp_target = 1 - |e| / 1920
```

当误差较大时，进入反比例软化区：

```text
kp_target ≈ 1920 / |e|
```

再用 `kp_gain_rate` 向目标值逼近：

```text
kp_gain_n = kp_gain_(n-1)
             + kp_gain_rate × (kp_target - kp_gain_(n-1))
```

### 4.3 自适应积分增益

内部还有：

```text
integral_gain_threshold = 50
integral_gain_rate       = 0.025
```

其作用是根据误差大小调整积分贡献。精确更新逻辑见 `Pid1Controller.hpp` 的 `adjust_integral()`。

### 4.4 速度滤波输入

【SOURCE FACT】

```text
error_diff = e_n - e_(n-1)
target_velocity_raw = error_diff + last_u
```

随后进入一个离散 Kalman 风格滤波器：

```text
q_velocity = 0.01
r_velocity = 1.0
```

其状态更新为：

```text
predicted_x = velocity_filter_x
predicted_p = velocity_filter_p + q
k           = predicted_p / (predicted_p + r)

velocity_filter_x = predicted_x
                    + k × (measurement - predicted_x)
velocity_filter_p = (1 - k) × predicted_p
```

这里的 measurement 是：

```text
error_diff + last_u
```

不是严格的：

```text
(error_diff / dt)
```

因此该“速度”实际上是“每次控制调用的误差差分 + 上次输出”，并不是物理单位 `px/s`。

### 4.5 P 项

```text
P_n = kp × e_n
```

### 4.6 I 项

当前实现没有消费 `MouseProfile.ki_x/ki_y`。

实际 `K_i` 来自内部速度项：

```text
raw_velocity_input = filtered(error_diff + last_u)

如果 |error| < 1 且 |error_diff| < 0.1：
    raw_velocity_input = error_diff + 0.5 × last_u

ki_raw = raw_velocity_input

如果 |ki_raw| <= 0.5：
    ki_raw = 0

ki_raw = ki_raw × predict × integral_gain
ki_raw = integral_filter(ki_raw)
```

因此：

```text
I_n = KalmanFilter(
        deadband(
          filtered(error_diff + last_u)
          × predict
          × integral_gain
        )
      )
```

重要结论：

```text
MouseProfile.ki_x/ki_y
```

虽然存在、能被 Gateway 保存，但没有传入 `Pid1Controller::configure()`，也没有进入 `Pid1Controller::update()`。

这是一个真实的“字段存在但数学上不生效”问题。

### 4.7 D 项

```text
D_n = kd × (e_n - e_(n-1))
```

没有除以 `dt`，所以它不是标准的：

```text
D = kd × de/dt
```

而是按控制调用次数计算的差分项。

### 4.8 smooth 内部软限制

当 `smooth != 0` 时，对 P、I、D 分量分别应用：

```text
ratio = value / bandwidth

soft(value) =
  [ ratio × (1 + (4/15) × ratio²)
    / (1 + (3/5) × ratio²) ]
  × (bandwidth - smooth)
```

其中：

```text
bandwidth = 10000
```

当前常见配置：

```text
smooth = 9900
```

所以最终缩放因子为：

```text
bandwidth - smooth = 100
```

在小信号区近似：

```text
soft(value) ≈ value × 100 / 10000
            = value × 0.01
```

也就是说，`smooth=9900` 并不是“平滑 9900 个单位”，而是把小信号 P/I/D 输出缩小到约 1%。

这是当前自动标定结果容易失配的关键原因之一。

### 4.9 PID 最终输出

```text
u_raw = soft(P_n) + soft(I_n) + soft(D_n)

u_pid = u_raw × kp_gain_n
```

首帧 reset 后：

```text
kp_gain = 0
integral_gain = 0
```

因此 reset 后第一帧可能输出为 0 或接近 0，之后逐步恢复内部增益。

### 4.10 TTBOX 当前 PID 结论

当前 TTBOX 名义上叫 PID，但在常见默认配置下实际更接近：

```text
P：有效，但经过 smooth 软限制和 kp_gain 调制
I：来自内部速度项，不使用 ki_x/ki_y
D：只有 kd 非零时才存在，且未按 dt 归一化
predict：内部 I/速度项倍率，不是位置预测时间
```

---

## 5. TTBOX 后处理和 HID 输出

### 5.1 sensitivity 与 output_scale

【SOURCE FACT】`AimThread.cpp:154-158`：PID 输出之后执行：

```text
out_gain = sensitivity × output_scale
scaled_x = u_pid_x × out_gain
scaled_y = u_pid_y × out_gain
```

个人曲线如果启用，还会继续乘个人模型倍率。

因此当前顺序是：

```text
PID 输出
  → sensitivity
  → output_scale
  → personal motion
```

### 5.2 deadzone

【SOURCE FACT】`Deadzone.hpp`：

```text
if abs(value) < deadzone:
    value = 0
```

当前输出死区默认约为 1.0，作用在缩放后的输出上。

数学形式：

```text
z(v) = 0              , |v| < deadzone
       v              , otherwise
```

### 5.3 小数余量 accumulator

【SOURCE FACT】Git 历史提交 `52a21ec` 引入小数余量：

```text
remainder_x += scaled_x
move_x = static_cast<int16_t>(remainder_x)
remainder_x -= move_x
```

Y 轴同理。

这意味着：

```text
0.3 + 0.4 + 0.5
```

不会全部消失，而是累计到足够大时输出一个 HID count。

不过 `static_cast<int16_t>` 本身是向零截断，而不是四舍五入。

### 5.4 限幅

当前 `AimThread` 对输出状态记录中以 `±127` 作为 clipped 统计边界；最终 `OutputAction` 使用 int16。

`LocalHidBackend::mouse_move()` 写入：

```text
ReportID = 0x02
buttons  = 16-bit little-endian
X        = int16 little-endian
Y        = int16 little-endian
wheel    = 0
pan      = 0
```

【SOURCE FACT】`core/src/output/LocalHidBackend.cpp:79-99`：最终传给 `/dev/hidg0` 的 dx/dy 是 int16 HID 相对移动量。

因此 TTBOX 最终输出单位是：

```text
USB HID relative mouse count
```

不是像素。

---

## 6. YU 真机逆向证据

### 6.1 YU 配置事实

【SOURCE FACT】【BEHAVIOR FACT】真机：

```text
/opt/aiassistance/config/config.json
```

关键字段：

```text
sens = 1.0

ai.controller.predict_x = 0.5
ai.controller.predict_y = 0.4
ai.controller.rate_x = 0.4
ai.controller.rate_y = 0.3
ai.controller.smooth_x = 9900
ai.controller.smooth_y = 9900
ai.controller.output_deadzone = 1.0

ai.controller.calibration.gain_x_px_per_count = 0.55
ai.controller.calibration.gain_y_px_per_count = 0.55
ai.controller.calibration.response_delay_ms = 8.333
ai.controller.calibration.valid = false
```

YU 顶层 aim profile 还存在：

```text
aim_profiles[0].sensitivity = 1.0
```

### 6.2 YU native 控制符号

【BINARY FACT】真机 daemon 中存在：

```text
aiassistance::AppDaemon::AimLoop()
aiassistance::CoreLoader::MotionController::UpdateTarget(...)
aiassistance::CoreLoader::MotionController::StepPendingMove(...)
aiassistance::CoreLoader::MotionController::PausePendingMotion()
aiassistance::(anonymous namespace)::AdaptiveAimOutputDeadzone(...)
aiassistance::(anonymous namespace)::BuildTargetPoint(...)
aiassistance::(anonymous namespace)::IsSameAimTargetCandidate(...)
```

这说明 YU 的主控制实现不是单纯的 Python Web 字段翻译，而是在 native daemon 中完成。

### 6.3 YU 输出队列符号

【BINARY FACT】真机 daemon 中还存在：

```text
MouseOutputWorker::SubmitMove(int, int, int, unsigned long, unsigned long)
DispatchCommandLocked
```

并且在 `StepPendingMove` 的调用路径中发现整数移动提交。

【INFERENCE】YU 的主输出路径包含 pending move 分步和队列派发；大移动不会像 TTBOX 当前路径一样只依赖一次控制周期直接写出，而是可能拆分成多次带时间信息的输出。

### 6.4 YU 自适应死区

【BINARY FACT】存在：

```text
AdaptiveAimOutputDeadzone(AndroidControllerConfig const&, cv::Rect_<int> const&)
```

同时字符串中存在：

```text
effective_aim_output_deadzone
output_smoothed
```

因此 YU 的死区至少具备以下特征：

- 不是单纯固定常数。
- 计算时会读取目标矩形。
- 存在实际生效值遥测。

【INFERENCE】YU 死区很可能会根据目标框大小或当前控制状态调整，而 TTBOX 当前死区主要是固定 count 域阈值。

### 6.5 YU 预测证据

【BINARY FACT】YU daemon 字符串存在：

```text
predicted_target
is_predicted
ultra_lost_grace_predict
```

这证明 YU 存在目标预测或预测状态概念。

【INFERENCE】YU 的 `predict_x/y` 更可能参与目标位置、速度或在途补偿，而不是简单等同于 TTBOX 当前 `Pid1Controller` 中的 `ki_raw × predict`。完整 native 函数体尚未全部恢复，因此这里不把具体预测公式写成 SOURCE FACT。

### 6.6 YU 自动标定符号

【BINARY FACT】存在：

```text
AutoCalibrationSession
CalibrationCandidateWindow
CalibrationObservation
MedianObservationCenter
CalibrationEstimatedAxisGain
AutoCalibrationRuntimeState
```

状态字符串包括：

```text
stabilize_x
stabilize_y
measure_x_response
measure_y_response
measure_x_settle
measure_y_settle
```

### 6.7 YU 自动标定幅度

【BINARY FACT】从 `.rodata` 读出：

```text
8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128
```

同时存在方向腿常量：

```text
+1, -2, +1
```

【INFERENCE】这对应先向一个方向移动、再反向回中、再进行下一段动作的往返腿策略，而不是单次永远向同一个方向推移。

### 6.8 YU 标定增益公式

【BINARY FACT】`CalibrationEstimatedAxisGain()` 位于约：

```text
0x162c10
```

反汇编显示每个观测会取：

```text
ratio = -measured_delta / injected_count
```

之后对多个 ratio 取中位数，并做范围判断：

```text
0.03 <= gain <= 8.0
```

无有效测量时使用约：

```text
0.55
```

对应浮点常量：

```text
0x3f0ccccd ≈ 0.55
```

因此 YU 标定的核心物理量是：

```text
目标画面位移 px / 实际注入鼠标 count
```

即：

```text
px/count
```

负号用于统一移动方向符号。

### 6.9 YU 的真实注入单位

【BINARY FACT】在 YU 自动标定动作路径中，反汇编看到浮点移动量经过 `lroundf` 等整数转换后，进入：

```text
MouseOutputWorker::SubmitMove(...)
```

因此 YU 自动标定注入的是实际鼠标移动 count，而不是误差像素 bias。

---

## 7. TTBOX 自动标定数学检查

### 7.1 TTBOX 当前观测过程

Gateway：

```text
scripts/ttbox_web.py:_calib_worker()
```

当前流程：

1. 读取 Core 目标观测。
2. 记录目标基准位置。
3. 将 `calibration_bias_x/y = amp` 写进 RuntimeProfile。
4. `AimThread` 把 bias 加入控制误差。
5. PID 计算输出。
6. 观察目标位置变化。
7. 计算 `measured_delta_px`。
8. 用 `measured_delta_px / injected_count` 拟合 gain。

关键代码证据：

```text
scripts/ttbox_web.py:1541-1547
calibration_bias_x/y = amp
```

以及：

```text
scripts/ttbox_web.py:1560-1567
measured_delta_px
injected_count = amp
```

### 7.2 TTBOX 当前“injected_count”的真实单位

`amp` 被写入：

```text
mouse.calibration_bias_x
mouse.calibration_bias_y
```

随后 Core 执行：

```text
control_x = error_x + calibration_bias_x
control_y = error_y + calibration_bias_y
```

所以 `amp` 实际进入的是：

```text
控制误差域 px
```

不是：

```text
HID mouse count
```

因此 TTBOX 当前实际测量的是：

```text
measured_delta_px / calibration_bias_px
```

它代表：

```text
闭环目标响应相对于误差偏置的比例
```

而不是：

```text
游戏画面 px / 鼠标 count
```

结论：

> **当前 TTBOX 自动标定的 gain 字段名叫 `gain_px_per_count`，但实际采样输入不是 count。该算法在物理单位上不正确。**

### 7.3 TTBOX 分子分母方向

TTBOX Domain：

```text
ttbox_motion/calibration.py:138
ratio = measured_delta_px / injected_count
```

分子分母顺序本身是：

```text
px / input
```

如果 input 真的是鼠标 count，方向是正确的。

问题在于：

```text
injected_count
```

实际上是误差域 bias px。

所以问题不是简单的分子分母反了，而是：

```text
分母的物理单位错了
```

### 7.4 TTBOX Median/MAD

TTBOX 使用：

```text
ratio = measured_delta_px / injected_count
center = median(ratios)
mad = median(abs(ratio - center))
```

并用相对 MAD 过滤异常值：

```text
mad / abs(center) <= 0.35
```

再检查：

```text
0.03 <= gain <= 8.0
0 <= delay <= 50ms
```

这一点比 YU 已确认的“中位数 + 范围”更严格。

结论：

- 统计方法本身合理。
- 稳健过滤方向正确。
- 输入物理单位错误，导致最终 gain 仍然不是目标参数。

### 7.5 TTBOX gain 写回

Gateway：

```text
scripts/ttbox_web.py:1418-1439
```

当前写回公式：

```text
K_LOOP = 1 / 7 ≈ 0.142857

sx = rate_x × sensitivity × output_scale
sy = rate_y × sensitivity × output_scale

kp_x = K_LOOP / (gain_x × sx)
kp_y = K_LOOP / (gain_y × sy)
```

这不是直接执行：

```text
mouse_count = pixel_error / gain_px_per_count
```

而是把 gain 折算成 kp。

更严重的是，实际 Core PID 还会继续经过：

```text
smoothTerm
kp_gain
sensitivity
output_scale
remainder
```

因此 Gateway 使用的 `K_LOOP=1/7` 是一个历史控制器尺度常数，和当前 `Pid1Controller` 的完整内部尺度并没有被证明一致。

### 7.6 自动标定当前算法判断

| 检查项 | 判断 |
|---|---|
| 分子分母排列 | 形式上正确 |
| 分母是否真实 mouse count | 错误，实际是误差 bias px |
| 是否得到 px/count | 没有，得到的是闭环 px/px 响应比 |
| X/Y 是否分开 | 是，方向正确 |
| 是否有稳健统计 | 有 Median/MAD |
| 是否有范围校验 | 有 |
| 是否考虑目标身份 | 有，读取 target ID/class |
| 是否考虑框尺寸和抖动 | 有，Gateway 层判断 |
| 是否直接进入 px→count 换算 | 没有 |
| 是否匹配当前 PID 输出尺度 | 未证明，现有公式高度可疑 |
| 是否考虑真实 HID 注入延迟 | 不完整，时间戳来自检测任务时间 |
| 是否是开环 count→px 测量 | 不是，当前是 bias→PID→HID→目标的闭环测量 |

最终判断：

> **TTBOX Phase 8.4 自动标定在稳态过滤和统计方法上有合理部分，但在输入单位和结果消费方式上不具备物理正确性。当前不能把它当成 YU 意义上的 `px/count` 标定。**

---

## 8. 参数语义对照表

| 参数 | TTBOX 实际含义 | YU 已确认/推断含义 | 单位 | 使用位置 | 使用顺序 | 判断 |
|---|---|---|---|---|---|---|
| `kp` | `P = kp × error`，随后进入 smooth 和 `kp_gain` | native `MotionController` 的完整公式未恢复 | TTBOX：输出/误差比例；YU：未知 | TTBOX PID 内部 | P 项最先形成 | 不能直接复制 |
| `ki` | `ki_x/y` 存在但 `Pid1Controller` 不消费 | YU 是否有独立 ki 未确认 | 名义上积分系数 | TTBOX 当前无实际消费 | 不进入公式 | BROKEN/语义未生效 |
| `kd` | `kd × (error_n-error_prev)` | YU native D 公式未恢复 | TTBOX：每调用误差差分；非 px/s | TTBOX PID | 与 P/I 相加前 | SEMANTIC MISMATCH |
| `sensitivity` | PID 输出后的全局倍率 | YU 同时有顶层 `sens` 和 aim profile sensitivity | 倾向 count 倍率 | TTBOX post-process | PID 之后 | 位置可能相近，完整 YU 顺序未证实 |
| `predict` | `ki_raw × predict × integral_gain`，是速度/I 项倍率 | YU 存在 predicted target 相关状态，完整公式未恢复 | TTBOX 实际为无量纲倍率；字段注释却写秒 | TTBOX PID 内部 | I 项内部 | **SEMANTIC MISMATCH** |
| `follow/rate` | `rate_x/y` 进入 `kp_gain_rate`，控制自适应增益追随速度 | YU 有 `rate_x/y`，native 消费位置未完全恢复 | TTBOX：增益变化率；YU：未知 | TTBOX PID 内部 | kp_gain 调节 | 不能当输出速率复制 |
| `smooth` | `bandwidth - smooth` 决定软限制输出尺度；9900≈小信号缩小 100 倍 | YU 有同名字段，native 公式未完全恢复 | TTBOX：内部 soft-limit 参数 | TTBOX P/I/D 后各自处理 | PID 内部 | 名字相同，数学位置未证实相同 |
| `output_deadzone` | 固定 count 域阈值，缩放后应用 | YU 有 `AdaptiveAimOutputDeadzone(config, rect)` | TTBOX：HID count；YU：目标框相关自适应值 | TTBOX post-process；YU native controller | 输出后 | **SEMANTIC MISMATCH** |
| `output_scale` | TTBOX 额外输出倍率 | YU config 中未确认有完全等价字段 | count 倍率 | PID 后 | sensitivity 后/附近 | 不能凭名字映射 |
| `gain_x/y_px_per_count` | 当前 Gateway 通过它反推 kp；Core 没有直接消费 | YU 目标画面像素 / 实际鼠标 count | px/count | TTBOX Gateway；YU native calibration/output | TTBOX 间接改 kp；YU 更可能用于 px→count | **核心语义不同** |
| `response_delay_ms` | Gateway 用检测任务时间与注入时间差估计 | YU 有 8.333ms 默认值，并存在响应/测量状态 | ms | TTBOX 标定结果 | 标定统计 | TTBOX 可能混入检测管线延迟 |
| `aim_offset` | 参考点在图像中心的像素偏移 | YU 有 aim reference offset | px | error 计算前 | 目标点→参考点 | 大体同类，但坐标系需统一 |
| `lost_grace_ms` | TargetSelector 按丢失帧近似换算宽限 | YU 有丢失/预测相关状态 | ms | 目标选择/状态机 | PID 前 | 语义可能相近 |

### 8.1 不能直接复制 YU 数值的具体例子

#### `predict_x`

YU 配置：

```text
predict_x = 0.5
```

TTBOX 默认源码：

```text
predict_x = 0.008
```

TTBOX 当前 PID 中：

```text
predict_x
```

直接乘在 `ki_raw` 上，不代表 0.008 秒的位置预测。

因此即使两个字段都叫 `predict_x`，也不能说：

```text
YU 0.5 = TTBOX 0.5
```

除非先证明两边的消费者和公式相同。

#### `rate_x`

TTBOX 中：

```text
rate_x → kp_gain_rate
```

它控制内部自适应比例增益的逼近速度，不是简单的“跟随速度”。

#### `output_deadzone`

TTBOX：

```text
固定 count 域死区
```

YU：

```text
AdaptiveAimOutputDeadzone(config, target_rect)
```

YU 至少会把目标矩形作为计算输入之一，因此同样设置为 `1.0` 不保证实际生效死区相同。

---

## 9. 控制顺序对比

### 9.1 TTBOX 当前实际顺序

```text
检测框
  → TargetSelector
  → 瞄准点
  → 参考点
  → error(px)
  → calibration bias(px)
  → FOV 转换（可选）
  → Pid1Controller
       ├─ P = kp × error
       ├─ I = velocity/filter × predict × integral_gain
       ├─ D = kd × error_diff
       ├─ smoothTerm(P/I/D)
       └─ × kp_gain
  → × sensitivity
  → × output_scale
  → × personal_motion
  → fixed output deadzone
  → fractional remainder
  → int16 cast
  → HID report
```

公式：

```text
u_pid = kp_gain × [ S(P) + S(I) + S(D) ]

u_post = u_pid
          × sensitivity
          × output_scale
          × personal_gain

u_dead = deadzone(u_post)

remainder_n = remainder_(n-1) + u_dead
hid_n       = trunc(remainder_n)
remainder_n = remainder_n - hid_n
```

### 9.2 YU 已确认的结构

```text
AimLoop
  → BuildTargetPoint
  → candidate / target continuity
  → MotionController::UpdateTarget
  → pending move / target motion state
  → StepPendingMove
  → adaptive deadzone / output shaping
  → SubmitMove(int dx, int dy, ...)
  → MouseOutputWorker queue
  → DispatchCommandLocked
  → backend
```

【BINARY FACT】以上函数和队列符号存在。

【INFERENCE】YU 的输出具有：

- pending move
- 分步 movement
- 队列时间信息
- adaptive deadzone
- native controller 内部目标/误差处理

完整顺序中 sensitivity、prediction、deadzone 每一个乘法/判断的确切指令级位置还没有全部恢复，因此不能把下列形式写成已证实事实：

```text
YU = error → sensitivity → PID
```

当前只能确认 YU 不是一个与 TTBOX 完全相同的“单函数 PID + 直接 HID”链。

---

## 10. 时间系统对比

### 10.1 TTBOX

【SOURCE FACT】

- Worker 使用 `steady_clock::now()` 生成任务时间。
- AimThread 使用 mailbox 中任务时间计算相邻任务差。
- mailbox 使用 `take_latest()`，会丢弃中间旧任务。
- AimThread 默认空闲 sleep 约 4000 微秒。
- `dt` 计算后被 `(void)dt` 丢弃。
- Pid1 的 D 项不除以 dt。
- velocity Kalman 使用固定 `q/r`，隐含“每次调用一步”。

所以 TTBOX 的有效 PID 时间模型是：

```text
离散调用步长 = 新检测任务到达步长
```

而不是：

```text
严格按实际秒数归一化的控制器
```

如果检测帧率从 240fps 降到 120fps，P/I/D 的每秒行为会改变。

### 10.2 YU

【BINARY FACT】YU `AimLoop` 及相关 native 路径存在多处时间读取，且状态字段/字符串包括：

```text
measurement_dt
response_delay_ms
mouse_queue_delay_ms
capture_buffer_age_ms
```

【INFERENCE】YU 的 native 控制和输出队列会显式考虑时间、队列延迟或采样阶段；但完整 `dt` 进入公式的位置尚未由完整反汇编恢复。

### 10.3 时间差异结论

| 项目 | TTBOX | YU | 影响 |
|---|---|---|---|
| 控制触发 | 新检测任务到达 | AimLoop/native controller | 两边控制周期不同 |
| 是否丢帧 | mailbox 只保留最新任务 | pending/queue 机制存在 | 输入序列不同 |
| dt 是否进 D | 否 | 存在时间字段，具体公式未恢复 | TTBOX 对帧率更敏感 |
| dt 是否进速度 | 否，按调用步 | 未完全确认 | 速度单位可能不同 |
| 输出调度 | 直接一帧写 HID | SubmitMove + queue + pending step | 大移动轨迹不同 |
| 延迟测量 | 检测任务时间差 | 有 response/queue/capture 字段 | TTBOX delay 可能包含管线延迟 |

---

## 11. 输出单位对比

### 11.1 TTBOX

```text
检测误差：px
PID 内部：与误差成比例的浮点输出
后处理：浮点移动量
remainder：浮点累计
最终：int16 HID relative count
```

当前存在：

- 小数余量累计。
- 向零截断。
- 最终 int16 报告。
- 发送前后端 gate。

### 11.2 YU

【BINARY FACT】YU `SubmitMove` 接收整数移动参数，并进入 `MouseOutputWorker`。

自动标定动作路径中，浮点移动量会先经过整数化，再提交输出。

【INFERENCE】YU 主路径可能存在自己的取整、分步或累计策略，但目前只确认了整数提交和队列，不把 YU 主路径的具体 rounding 方式写成事实。

### 11.3 重要差异

TTBOX 的 accumulator 发生在 PID 后、HID 前：

```text
float output → remainder → int16
```

YU 自动标定至少确认：

```text
float movement → integer SubmitMove → output queue
```

如果 YU 主链对大移动做 StepPendingMove，而 TTBOX 直接输出大值，两者在：

- 速度曲线
- 队列延迟
- 目标 overshoot
- 小移动保持
- 目标接近时收敛

都会出现明显差异。

---

## 12. 最终数学模型

### 12.1 TTBOX

定义：

```text
E_n = [target_x - reference_x,
       target_y - reference_y]
```

标定模式：

```text
C_n = E_n + B_n
```

其中 `B_n` 是误差域 bias，而非 HID count。

PID：

```text
ΔE_n = C_n - C_(n-1)

V_n = Kalman(ΔE_n + U_(n-1))

I_n = Kalman(
        deadband(V_n × predict × integral_gain)
      )

P_n = kp × C_n
D_n = kd × ΔE_n

U_pid,n = kp_gain_n × [S(P_n) + S(I_n) + S(D_n)]
```

后处理：

```text
U_post,n = U_pid,n
           × sensitivity
           × output_scale
           × personal_gain

U_dead,n = deadzone(U_post,n)

R_n = R_(n-1) + U_dead,n

HID_n = trunc(R_n)
R_n = R_n - HID_n
```

输出：

```text
HID_n = int16 relative mouse count
```

TTBOX 当前没有实际接入的位置预测：

```text
predicted_target = target + velocity × prediction_time
```

### 12.2 YU

已确认的抽象模型：

```text
目标候选 / 瞄准点
  → MotionController::UpdateTarget
  → 目标误差与控制状态
  → pending move
  → StepPendingMove
  → adaptive deadzone / output shaping
  → SubmitMove(int dx, int dy)
  → MouseOutputWorker queue
  → backend
```

自动标定：

```text
已知真实鼠标 count c
  → SubmitMove(c)
  → 游戏/设备响应
  → 目标画面位移 Δpx
  → observation ratio = -Δpx / c
  → 多轮 median
  → clamp 0.03~8.0
  → gain_px_per_count
```

如果 YU 将 gain 用于像素到鼠标移动换算，其物理形式应接近：

```text
mouse_count = pixel_error / gain_px_per_count
```

这一条的具体 native 消费位置尚未完全从二进制恢复，因此标记为【INFERENCE】，但 gain 的测量单位 `px/count` 已由配置命名、反汇编公式和标定流程共同支持。

---

## 13. 最终问题回答

### 13.1 TTBOX PID 参数是否可以直接照搬 YU？

不能直接照搬。

原因：

- `predict` 计算位置不同。
- `rate` 在 TTBOX 中控制 `kp_gain` 逼近速度。
- `deadzone` 一边固定 count 域，一边至少存在目标框自适应计算。
- `smooth` 的具体 native 数学未证明相同。
- YU 有 pending move 和输出队列，TTBOX 没有等价链路。
- YU gain 是真实 `px/count`，TTBOX 当前标定 input 不是实际 count。

### 13.2 如果不能，为什么？

因为参数值只有在以下条件都相同时才有可比性：

```text
输入单位
误差坐标系
计算顺序
时间基准
内部状态
限幅方式
输出队列
最终 HID 单位
```

TTBOX 与 YU 至少在预测、标定输入、死区、时间处理和输出调度上存在差异。

### 13.3 哪些参数只是名字一样？

重点包括：

- `predict_x/y`
- `rate_x/y`
- `smooth_x/y`
- `output_deadzone`
- `sensitivity`
- `gain_x/y_px_per_count`
- `response_delay_ms`

其中最确定的名字误导是 `predict_x/y` 和 `gain_x/y_px_per_count`。

### 13.4 哪些参数单位不同？

- TTBOX `predict_x/y`：实际是内部无量纲倍率；注释却称预测秒数。
- TTBOX `Pid1` D 项：误差/调用步，不是误差/秒。
- TTBOX calibration `injected_count`：实际是误差域 px bias。
- YU calibration gain：目标 px / 实际鼠标 count。
- TTBOX deadzone：固定输出 count 阈值。
- YU deadzone：至少会读取目标矩形，实际单位和公式需继续 native 还原。

### 13.5 哪些参数计算位置不同？

- TTBOX sensitivity 在 PID 之后。
- TTBOX gain 通过 Gateway 改写 kp。
- YU gain 更可能进入实际 px→count 输出转换。
- TTBOX predict 在 PID 的 I/速度项内部。
- YU predict 相关状态出现在目标预测/在途状态体系中。
- TTBOX deadzone 在 sensitivity/output_scale 后。
- YU deadzone 位于 native controller/output shaping 内，且具备自适应函数。

### 13.6 哪些参数应该重新标定？

优先重新定义和标定：

1. `gain_x/y_px_per_count`
2. `response_delay_ms`
3. 实际输出增益或 px→count 转换比例
4. X/Y 独立控制增益
5. 目标预测时间或预测倍率
6. 输出死区
7. 大移动分步/队列速度

### 13.7 自动标定当前算法是否正确？

结论：**统计方法部分正确，物理测量定义不正确。**

正确部分：

- X/Y 分轴。
- 同一目标身份过滤。
- 中心抖动和尺寸变化过滤。
- Median/MAD。
- 范围校验。
- 取消和失败保护。

错误或高风险部分：

- 将误差 bias 当成 mouse count。
- 用闭环响应比替代开环 `px/count`。
- 将 gain 通过历史 `K_LOOP=1/7` 折算成 kp。
- 没有证明该 kp 与当前 Pid1 的 smooth/adaptive 内部尺度匹配。
- 延迟测量时间点可能包含检测和 mailbox 管线延迟。

### 13.8 TTBOX 当前效果差的第一嫌疑是什么？

**第一嫌疑：自动标定 gain 的物理单位错误，且写回 kp 的尺度与当前 Pid1 不匹配。**

证据：

- YU 注入真实 count。
- TTBOX 注入 error-domain bias。
- TTBOX 仍把 bias 命名为 injected_count。
- TTBOX 用 gain 反推 kp，而 PID 内部另有 smooth/adaptive 缩放。

影响：

- 自动标定结果可能无法代表真实鼠标灵敏度。
- 标定后可能出现移动过慢、收敛慢或不同误差区间表现不一致。

### 13.9 第二嫌疑是什么？

**第二嫌疑：预测参数没有进入目标位置预测。**

证据：

- `AimTracker` 存在但没有进入主链。
- `AlphaBetaGammaFilter` 存在但没有进入主链。
- 当前 `predict` 只参与 PID 内部 `ki_raw`。
- YU 存在 `predicted_target`、`is_predicted` 等 native 状态。

影响：

- 动目标追踪时 TTBOX 使用滞后的当前检测点。
- `predict_x/y` 调大并不等于提前瞄准目标未来位置。
- 用户调参直觉与实际数学行为不一致。

### 13.10 第三嫌疑是什么？

**第三嫌疑：时间基准和输出调度不同。**

证据：

- TTBOX 计算 dt 后丢弃。
- mailbox 只保留最新任务，可能跳过中间帧。
- TTBOX 直接生成每周期 HID 输出。
- YU 存在 `StepPendingMove`、`SubmitMove`、队列派发和延迟字段。

影响：

- 实际帧率变化会改变 TTBOX 控制行为。
- 大移动和小移动的时间形状与 YU 不同。
- 过冲、抖动、响应迟滞和收敛速度可能不同。

---

## 14. 修复优先级建议

本阶段只提出修复位置，不施工。

### P0-1：修正自动标定输入单位

**问题**：TTBOX 把误差域 bias 当作鼠标 count。

**证据**：

- `scripts/ttbox_web.py:1541-1547` 写入 `calibration_bias_x/y`。
- `AimThread.cpp:130-135` 把 bias 加到误差。
- `ttbox_motion/calibration.py:138` 使用 `measured_delta / injected_count`。
- YU 通过 `SubmitMove(int, int, ...)` 注入实际 count。

**影响**：gain 不是物理 `px/count`，自动标定结果无法正确代表游戏灵敏度。

**建议修改位置**：

- Core：设计明确的 calibration count 注入路径。
- Gateway：记录实际送入 HID 的 count，而不是 bias 值。
- Domain：将观测字段拆为 `injected_mouse_count` 与 `control_bias_px`，禁止混用。

### P0-2：重新定义 gain 的消费方式

**问题**：Gateway 用 `K_LOOP=1/7` 把 gain 折算成 kp，但没有证明与 Pid1 内部尺度匹配。

**证据**：`scripts/ttbox_web.py:1418-1439`。

**影响**：标定后 kp 可能过小或过大，尤其受 `smooth=9900` 和 `kp_gain` 影响。

**建议修改位置**：

- `core/src/aim/AimThread.cpp`
- `core/src/aim/Pid1Controller.hpp`
- `core/src/model/RuntimeProfile.cpp`
- `scripts/ttbox_web.py`

优先建立明确公式：

```text
mouse_count = pixel_error / gain_px_per_count
```

再决定 PID 应工作在 px 域、角度域还是 count 域，不要继续使用未经证明的常数折算。

### P0-3：统一 predict 语义

**问题**：字段注释称秒，实际用于 I/速度项倍率；真正目标预测类没有接入。

**证据**：

- `Pid1Controller.hpp:64`：`ki_raw × predict`。
- `AimThread.cpp:118-119`：不做位置外推。
- `AimTracker` 和 `AlphaBetaGammaFilter` 未被主链调用。

**影响**：调大 predict 可能增强积分/速度项，而不是提前追踪目标，造成调参方向错误。

**建议修改位置**：

- `core/src/aim/Pid1Controller.hpp`
- `core/src/aim/AimThread.cpp`
- `core/src/mouse/AimTracker.*`
- `core/src/aim/AlphaBetaGammaFilter.hpp`
- RuntimeProfile 字段命名和产品文档

### P1-1：恢复真实 dt 进入控制器

**问题**：dt 计算后被丢弃。

**证据**：`AimThread.cpp` 中存在 `(void)dt`；`Pid1Controller::update()` 不接收 dt。

**影响**：控制效果随检测帧率、丢帧和 mailbox 跳帧变化。

**建议修改位置**：

- `AimThread.cpp`
- `Pid1Controller.hpp`
- 控制器测试

需要明确：

```text
D = kd × de/dt
velocity = de/dt
```

或正式声明控制器使用离散步模型，并固定控制频率。

### P1-2：确认并实现输出分步/队列策略

**问题**：TTBOX 直接生成当前周期 HID，YU 存在 pending move 和队列。

**证据**：YU 符号 `StepPendingMove`、`SubmitMove`、`DispatchCommandLocked`。

**影响**：大移动、拉枪曲线、近目标收敛和延迟形状不同。

**建议修改位置**：

- `core/src/aim/AimThread.cpp`
- `core/src/output/OutputBackend.*`
- 必要时新增正式的 TTBOX OutputScheduler Domain

先通过离线仿真比较：

```text
同一误差序列
同一目标移动序列
同一初始状态
```

再决定是否需要真实队列。

### P1-3：确认 deadzone 数学语义

**问题**：TTBOX 使用固定 count 死区，YU 存在目标矩形自适应死区。

**证据**：YU `AdaptiveAimOutputDeadzone` 符号；TTBOX `Deadzone.hpp`。

**影响**：不同目标大小、距离和框尺寸下，微调行为不一致。

**建议修改位置**：

- `core/src/mouse/Deadzone.hpp`
- `core/src/aim/AimThread.cpp`
- RuntimeProfile

### P1-4：接通或删除装饰性 ki

**问题**：Web/Gateway 可保存 `ki_x/y`，Core PID 不消费。

**证据**：`Pid1Controller::configure()` 没有 ki 参数，`update()` 没有 ki 配置输入。

**影响**：用户调节 ki 没有效果，造成产品认知错误。

**建议修改位置**：

- 如果产品需要独立 I：扩展 Pid1 数学和状态管理。
- 如果产品不需要独立 I：删除 Web/Gateway 的 ki 控件并更新契约。

### P2-1：统一坐标系说明和实际实现

**问题**：代码注释中同时出现 crop/ROI 与整帧坐标语义。

**影响**：同样的 kp、gain、deadzone 在不同分辨率下可能代表不同实际力度。

**建议修改位置**：

- `AimTargetTask.hpp`
- `TargetSelector.hpp`
- `CoordinateTransform.*`
- `AimError.hpp`
- 运行时遥测

### P2-2：延迟测量拆分

**问题**：TTBOX 使用检测任务时间估算响应延迟，可能包含 capture、推理、解码、mailbox 延迟。

**影响**：标定结果的 `response_delay_ms` 不一定是鼠标输入到画面响应的纯延迟。

**建议修改位置**：

- Worker 帧时间戳
- Core 控制输出时间戳
- Gateway 标定观测协议
- Metrics/trace

至少拆出：

```text
capture_to_inference_ms
inference_to_control_ms
control_to_hid_ms
hid_to_visual_response_ms
```

### P3-1：优化取整和小数累计策略

**问题**：TTBOX 使用向零截断 + remainder；YU 主路径取整/分步策略未完全确认。

**影响**：小幅移动长期偏差、大幅移动边界和方向换向可能存在不同。

**建议修改位置**：

- `AimThread.cpp`
- OutputScheduler
- HID 记录和离线回放测试

### P3-2：保留 Median/MAD，但增加物理闭环仿真

**问题**：TTBOX 的 Median/MAD 统计方向正确，但当前测试只覆盖 Domain 纯函数，未覆盖真实 count→px→回读链。

**影响**：统计测试通过不代表自动标定结果物理正确。

**建议修改位置**：

- `platform/tests/test_calibration_behavior.py`
- Core mock output
- Gateway calibration integration test

---

## 15. 当前证据边界

### 已确认

- TTBOX 当前控制链的调用顺序。
- TTBOX `Pid1Controller` 的 P/I/D 和 smooth 数学。
- TTBOX dt 被计算但未进入 PID。
- TTBOX `ki_x/y` 未被当前 Pid1 消费。
- TTBOX 当前没有在主链调用 AimTracker/ABG 位置预测。
- TTBOX sensitivity/output_scale/deadzone/remainder/HID 顺序。
- TTBOX 自动标定 bias 进入误差域。
- YU daemon 的 native 控制、标定、队列和 adaptive deadzone 符号。
- YU calibration gain 的 `-measured_delta / injected_count` 中位数和范围约束。
- YU 自动标定使用真实整数移动提交路径的证据。

### 尚未完全确认

- YU native `MotionController::UpdateTarget()` 每个乘法项的完整公式。
- YU `predict_x/y` 在 native 指令中的完整消费位置。
- YU sensitivity 与 output deadzone 的确切乘法顺序。
- YU 主路径的完整 float→int rounding 和 remainder 策略。
- YU `response_delay_ms` 是否完全等于输入到视觉反馈延迟，还是包含管线延迟。
- YU 完整 gain 的最终消费函数。
- YU 候选窗口所有数值阈值。

以上项目均没有被写成 SOURCE FACT，分别标记为 BINARY FACT 或 INFERENCE。

---

## 16. 本阶段执行声明

本阶段只做：

- 本地 TTBOX 源码读取。
- Git 历史读取。
- 真机 YU 代码、配置、二进制、符号、字符串、反汇编和日志读取。
- 数学公式整理。
- 逻辑差异报告生成。

本阶段没有：

- 修改 Core 产品代码。
- 修改 Gateway。
- 修改 Web。
- 修改配置。
- 修改真机运行状态。
- 停止或重启 TTBOX Core/Web。
- 停止 YU。
- 修改网络、SSH、防火墙。
- 执行整机重启、关机或断电。
- 标记任何新增能力为 REAL。

最终报告文件：

```text
docs/architecture/TTBOX_YU_PID_CONTROL_DEEP_DIVE.md
```
