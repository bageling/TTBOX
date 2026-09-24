// AimThread.cpp — 独立瞄准线程最小可验证实现。
#include "aim/AimThread.hpp"
#include "aim/PipelineDebug.hpp"
#include <chrono>
#include <cstdint>
#include <limits>
#include <utility>
#include "aim/AimError.hpp"
#include "mouse/FovAngle.hpp"
#include "mouse/CoordinateTransform.hpp"
#include "mouse/AimPointProfile.hpp"
#include "mouse/PersonalMotion.hpp"
#include "mouse/PersonalTrajectoryShader.hpp"
#include "common/CpuAffinity.hpp"
#include "common/Logger.hpp"
namespace ttbox::core::aim {
bool AimThread::start(AimTargetMailbox* mailbox, std::shared_ptr<output::IHidOutput> output, int interval_us, RuntimeConfig* runtime_config, std::atomic<uint16_t>* physical_buttons) {
    if (!mailbox || !output || running_.exchange(true)) return false;
    mailbox_ = mailbox; output_ = std::move(output); interval_us_ = interval_us > 0 ? interval_us : 4000; runtime_config_ = runtime_config; physical_buttons_ = physical_buttons;
    reset_runtime_state();
    { std::lock_guard<std::mutex> lk(status_mutex_); status_ = {}; status_.running = true; }
    // pid1.cpp main() 原始参数：X predict=3.0，Y predict=0.0。
    pid_x_.init(25.0, 25.0, 3.0, 0.3, 9900.0);
    pid_y_.init(25.0, 25.0, 0.0, 0.3, 9900.0);
    thread_ = std::thread(&AimThread::loop, this);
    return true;
}

void AimThread::reset_runtime_state() {
    selector_.reset();
    state_machine_.reset();
    tracker_.reset();
    pid_x_.reset();
    pid_y_.reset();
    pull_curve_.reset();
    continuous_lead_.reset();  // 持续提前量累计/方向/渐入电平清零（destroy→init 重建一致）
    personal_shader_.reset();
    recoil_.reset();
    // BB 对标第二批（2026-09-24）：新模块状态同样必须随世代清零，禁止 A 模型状态漏到 B
    lead_pred_.reset();
    humanize_shaper_.reset();
    anti_overshoot_.reset();
    speed_kp_.reset();
    global_wave_.reset();
    lead_last_move_y_ = 0.0f;
    last_injection_allowed_ = false;
    display_smooth_x1_.reset();
    display_smooth_y1_.reset();
    display_smooth_x2_.reset();
    display_smooth_y2_.reset();
    last_display_target_id_ = -1;
    last_display_ts_us_ = 0;
    target_age_ms_ = 0.0f;
    last_timestamp_us_ = 0;
    remainder_x_ = 0.0f;
    remainder_y_ = 0.0f;
    last_target_id_ = -1;
    // 热键保护：挂起状态**跨世代保留** —— 它是用户按出来的意图，换个模型不该被悄悄解除。
    // 但边沿判据要重新对齐到当前物理位图：否则 start 那一刻会把"键仍按着"误读成一次
    // 新的上升沿，刚挂起就被自己解掉（start 后第一周期就会翻转一次）。
    last_raw_buttons_ = physical_buttons_ ? physical_buttons_->load(std::memory_order_acquire) : 0;
}
void AimThread::stop() {
    if (!running_.exchange(false)) return;
    if (thread_.joinable()) thread_.join();
    std::lock_guard<std::mutex> lk(status_mutex_); status_.running = false;
}
AimThread::Status AimThread::status() const {
    std::lock_guard<std::mutex> lk(status_mutex_);
    return status_;
}
void AimThread::loop() {
    // 瞄准控制链对延迟敏感，固定在大核运行，避免被调度到小核产生抖动。
    {
        std::string aerr;
        if (!CpuAffinity::set_thread_affinity(CpuAffinity::kBigCoreMask, &aerr)) {
            TTBOX_LOG_WARN("AimThread 绑定大核失败: " + aerr);
        }
    }
    uint64_t last_frame = 0;
    while (running_.load(std::memory_order_acquire)) {
        AimTargetTask task;
        if (mailbox_->take_latest(&task, last_frame)) {
            last_frame = task.frame_number;
            const uint64_t previous_timestamp_us = last_timestamp_us_;
            const int target_id_before = last_target_id_;  // 本帧切换判定基准
            // 新控制链：目标选择 → 误差 → 纯 PID/P 控制 → OutputAction。
            TargetSelectorConfig scfg;
            scfg.roi_w = task.frame_width; scfg.roi_h = task.frame_height;
            // 用户置信度阈值（RuntimeProfile.mouse.confidence）：0 = 用模型默认（选 0.25 底线）。
            // 修复点：此前写死 0.0f 导致 Web 置信度阈值参数无效（中看不中用）。
            float out_sensitivity = 1.0f, out_scale = 1.0f;
            float out_deadzone = 1.0f;
            float recoil_px_per_count = 0.65f;  // 压枪 px→count 换算（默认 0.65 px/count，标定可覆盖）
            PersonalMotionConfig personal_motion;
            PullCurveConfig pull_curve_cfg;  // 拉枪曲线配置（默认 enabled=true, min_distance=80, strength=0.8）
            ContinuousLeadConfig lead_cfg;  // 持续提前量配置（默认 enabled=false ⇒ 不动输出）
            PersonalTrajectoryConfig personal_traj_cfg;  // 拟人化整形引擎配置（默认 enabled=false，保持现有行为）
            RecoilConfig recoil_cfg;  // 压枪配置（默认 enabled=false，保持现有行为）
            // ---- BB 对标第二批（2026-09-24）：默认全关 ⇒ 不跑即零输出，行为零变化 ----
            RecoilBbConfig recoil_bb_cfg;           // 三段查表压枪引擎（默认关）
            VerticalCorrectionConfig vc_cfg;        // 垂直修正 + 力度渐变
            Lead1Config lead1_cfg;                  // 提前量一代（帧窗口投票）
            Lead2Config lead2_cfg;                  // 提前量二代（积分累积）
            HumanizeShaperConfig humanize_cfg;      // BB 拟人化整形链
            AntiOvershootConfig anti_over_cfg;      // 抗过冲
            SpeedAdaptiveKpConfig speed_kp_cfg;     // 速度自适应 Kp
            GlobalWaveConfig global_wave_cfg;       // 全局正弦扰动
            float kp_x = 0.0f, kp_y = 0.0f, kd_x = 0.0f, kd_y = 0.0f;
            AimPointProfile aim_point;
            LockConfirmConfig lock_confirm_cfg;  // 目标锁定确认（ENTER/HOLD，第2项）
            std::shared_ptr<const RuntimeProfile> frame_profile;
            if (runtime_config_) {
                frame_profile = runtime_config_->snapshot();
            }
            if (frame_profile) {
                scfg.fov_range = frame_profile->fov.enabled ? frame_profile->fov.radius * 2.0f : 1.0f;
                scfg.lost_grace_ms = frame_profile->mouse.lost_grace_ms;
                // 切靶防抖（对齐 BB 的 hysteresis / cooldown）：配置缺省即取 MouseProfile 默认
                // 0.5 / 600ms；两者置 0 可完全关闭，回到加此机制前的行为。
                scfg.switch_hysteresis = frame_profile->mouse.switch_hysteresis;
                scfg.switch_cooldown_ms = frame_profile->mouse.switch_cooldown_ms;
                // 选靶四项机制（2026-09-24 补通路）：锁定期 / 打分制 / 粘滞 / 头身稳定。
                // ★ 此前 TargetSelector 里算法已实现但这里没赋值 ⇒ 永远吃结构体默认（全关），
                //   面板也开不了。默认值仍是 0/false ⇒ 不开时选靶行为逐字节不变。
                scfg.lock_hold_ms = frame_profile->mouse.lock_hold_ms;
                scfg.priority_scoring = frame_profile->mouse.priority_scoring;
                scfg.weight_dist = frame_profile->mouse.weight_dist;
                scfg.weight_size = frame_profile->mouse.weight_size;
                scfg.stickiness = frame_profile->mouse.stickiness;
                scfg.switch_threshold_px = frame_profile->mouse.switch_threshold_px;
                scfg.head_body_stable = frame_profile->mouse.head_body_stable;
                scfg.hb_body1 = frame_profile->mouse.hb_body1;
                scfg.hb_head1 = frame_profile->mouse.hb_head1;
                scfg.hb_body2 = frame_profile->mouse.hb_body2;
                scfg.hb_head2 = frame_profile->mouse.hb_head2;
                scfg.confidence = frame_profile->mouse.confidence > 0.0f
                                      ? frame_profile->mouse.confidence : 0.25f;
                scfg.aim_ratio_x = frame_profile->mouse.aim_point.offset_x;
                scfg.aim_ratio_y = frame_profile->mouse.aim_point.offset_y;
                kp_x = frame_profile->mouse.kp_x; kp_y = frame_profile->mouse.kp_y;
                kd_x = frame_profile->mouse.kd_x; kd_y = frame_profile->mouse.kd_y;
                aim_point = frame_profile->mouse.aim_point;
                // 输出链参数：sens 全局缩放 × output_scale × output_deadzone
                out_sensitivity = frame_profile->mouse.sensitivity;
                out_scale = frame_profile->mouse.output_scale;
                out_deadzone = frame_profile->mouse.output_deadzone;
                recoil_px_per_count = frame_profile->mouse.gain_y_px_per_count > 0.05f
                                          ? frame_profile->mouse.gain_y_px_per_count : 0.65f;
                personal_motion = frame_profile->mouse.personal_motion;
                pull_curve_cfg = frame_profile->mouse.pull_curve;
                lead_cfg = frame_profile->mouse.continuous_lead;
                personal_traj_cfg = frame_profile->mouse.personal_trajectory;
                lock_confirm_cfg = frame_profile->mouse.lock_confirm;
                recoil_cfg = frame_profile->mouse.recoil;
                // BB 对标第二批：每周期重读（改配置即时生效，无需重启）
                recoil_bb_cfg = frame_profile->mouse.recoil_bb;
                vc_cfg = frame_profile->mouse.vertical_correction;
                lead1_cfg = frame_profile->mouse.lead1;
                lead2_cfg = frame_profile->mouse.lead2;
                humanize_cfg = frame_profile->mouse.humanize;
                anti_over_cfg = frame_profile->mouse.anti_overshoot;
                speed_kp_cfg = frame_profile->mouse.speed_adaptive_kp;
                global_wave_cfg = frame_profile->mouse.global_wave;
                pid_x_.configure(kp_x, kd_x, frame_profile->mouse.predict_x,
                                 frame_profile->mouse.rate_x, frame_profile->mouse.smooth_x);
                pid_y_.configure(kp_y, kd_y, frame_profile->mouse.predict_y,
                                 frame_profile->mouse.rate_y, frame_profile->mouse.smooth_y);
            }
            const auto selected = selector_.select(task.detections, scfg,
                static_cast<uint32_t>(task.timestamp_us / 1000ULL));
            const uint32_t now_ms32 = static_cast<uint32_t>(task.timestamp_us / 1000ULL);
            // ---- BB 速度自适应 Kp：动目标加大 Kp、静目标减小（默认关 ⇒ 乘子恒 1.0）----
            // 放在 select 之后（需要本帧目标位置），且用 frame_profile 里的**原始 kp** 重算，
            // 避免乘子逐帧自我叠乘（pid_x_.configure 只赋值，不累积）。
            if (frame_profile && speed_kp_cfg.enabled) {
                const float skp_mult = speed_kp_.multiplier(
                    speed_kp_cfg, selected.valid,
                    selected.valid ? (selected.box.x1 + selected.box.x2) * 0.5f : 0.0f,
                    selected.valid ? (selected.box.y1 + selected.box.y2) * 0.5f : 0.0f);
                if (skp_mult != 1.0f) {
                    pid_x_.configure(kp_x * skp_mult, kd_x, frame_profile->mouse.predict_x,
                                     frame_profile->mouse.rate_x, frame_profile->mouse.smooth_x);
                    pid_y_.configure(kp_y * skp_mult, kd_y, frame_profile->mouse.predict_y,
                                     frame_profile->mouse.rate_y, frame_profile->mouse.smooth_y);
                }
            }
            // ---- Hotkey Gate 输入解析（每周期独立计算，AI 链路照常运行）----
            // any 模式：主键或副键任一按下即触发；all 模式：两者同时按下。
            const uint16_t raw_buttons =
                physical_buttons_ ? physical_buttons_->load(std::memory_order_acquire) : 0;
            // ---- 热键保护（hotkey_guard）：toggle 键**上升沿**翻转「全部挂起」----
            // 挂起 = 本控制周期把热键位图清零 ⇒ 瞄准 Gate 与压枪一并失效（两者都读这个位图）。
            // 物理鼠标透传不受影响：那条路在 usbproxy 侧，不经过本变量。
            // guard 关掉（或 toggle 键未配）时立即恢复"未挂起"，不保留幽灵挂起状态。
            uint8_t guard_toggle = 0;
            if (frame_profile) {
                const auto& guard = frame_profile->mouse.hotkey_guard;
                if (guard.enabled) guard_toggle = guard.toggle_hotkey;
            }
            if (guard_toggle != 0) {
                const bool now_down = (raw_buttons & guard_toggle) != 0;
                const bool was_down = (last_raw_buttons_ & guard_toggle) != 0;
                if (now_down && !was_down) hotkeys_suspended_ = !hotkeys_suspended_;
            } else {
                hotkeys_suspended_ = false;
            }
            last_raw_buttons_ = raw_buttons;
            uint16_t hotkey_bits = hotkeys_suspended_ ? 0u : raw_buttons;
            bool injection_allowed = false;
            if (frame_profile) {
                const bool a = (hotkey_bits & frame_profile->mouse.aim_hotkey) != 0;
                const bool b = frame_profile->mouse.aim_hotkey2 != 0 && (hotkey_bits & frame_profile->mouse.aim_hotkey2) != 0;
                // 鼠标五键统一位图：左1、右2、中4、侧1 8、侧2 16。
                // 热键位全部来自用户配置快照（每周期重读 → 改配置即时生效，无需重启）。
                // mouse.enabled 是总开关；any 模式主/副键任一命中即可，all 模式需同时按下。
                // A11 标定闭环：calibrating=true 期间无视物理热键强制放行
                // （标定线程注入运动帧，物理鼠标不参与），与 C 桥 compute_aiming 语义一致。
                injection_allowed = frame_profile->mouse.calibrating ||
                                    (frame_profile->mouse.enabled &&
                                    (frame_profile->mouse.aim_hotkey_mode == 1 ? (a && b) : (a || b)));
            }
            // ---- BB 热键边沿（对齐「按下 shuwuResetPid、松开完全重置」）----
            // 上升沿：热键刚按下 → 清 PID 在途量，本次瞄准从零起算（BB 的原生行为）。
            // 下降沿：热键松开 → 连提前量/拟人化/抗过冲/速度Kp 一并复位，
            //   下次按下是全新一轮（否则上一轮的收帧窗/积分/衰减帧数会带过来）。
            if (injection_allowed && !last_injection_allowed_) {
                pid_x_.reset();
                pid_y_.reset();
                remainder_x_ = 0.0f;
                remainder_y_ = 0.0f;
            } else if (!injection_allowed && last_injection_allowed_) {
                pid_x_.reset();
                pid_y_.reset();
                remainder_x_ = 0.0f;
                remainder_y_ = 0.0f;
                lead_pred_.reset();
                humanize_shaper_.reset();
                anti_overshoot_.reset();
                speed_kp_.reset();
                global_wave_.reset();
                lead_last_move_y_ = 0.0f;
            }
            last_injection_allowed_ = injection_allowed;
            AimStateEvent event; event.has_target = selected.valid;
            event.hotkey_active = injection_allowed;
            event.now_ms = task.timestamp_us / 1000ULL;
            event.target_confidence = selected.valid ? selected.box.score : 0.0f;
            event.target_distance = selected.distance;
            if (state_machine_.update(event, scfg.lost_grace_ms, lock_confirm_cfg)) { pid_x_.reset(); pid_y_.reset(); remainder_x_=0.0f; remainder_y_=0.0f; last_target_id_=-1; }
            int16_t move_x = 0, move_y = 0; float ex = 0.0f, ey = 0.0f;
            float pred_ex = 0.0f, pred_ey = 0.0f;  // 第15阶段：预测误差
            float tx = 0.0f, ty = 0.0f, ref_x = 0.0f, ref_y = 0.0f;
            float smooth_tx = 0.0f, smooth_ty = 0.0f;  // 第15阶段：平滑后瞄准点（滤检测框抖动，遥测/控制共用）
            float aibox_x = 0.0f, aibox_y = 0.0f, scaled_x = 0.0f, scaled_y = 0.0f;
            bool fov_mode_active = false;  // FOV 模式：fov_move 已是 count 域最终移动量，旁路 PID 的 kp×err
            float fov_out_x = 0.0f, fov_out_y = 0.0f;  // FOV 模式输出（count 域）
            float trace_control_x = 0.0f, trace_control_y = 0.0f;
            float trace_smith_dx = 0.0f, trace_smith_dy = 0.0f;
            // ---- Hotkey Gate：最终输出安全边界 ----
            // 热键未按下时：无论目标/误差/PID 状态如何，
            // 本周期一律跳过移动计算（不积分、不累计余数），
            // 只保留误差遥测；最终发送的 OutputAction 强制 dx=dy=0。
            // AI 链路（mailbox→selector→误差遥测）不受热键影响，始终运行。
            if (!injection_allowed) {
                pid_x_.reset();      // 清在途量/PID 状态，防止旧状态绕过 Gate
                pid_y_.reset();
                remainder_x_ = 0.0f;
                remainder_y_ = 0.0f;
            }
            if (selected.valid && task.frame_width > 0 && task.frame_height > 0) {
                if (!aim_point_at(selected.box, selected.box.class_id, aim_point, &tx, &ty)) {
                    tx = (selected.box.x1 + selected.box.x2) * 0.5f;
                    ty = selected.box.y1 + (selected.box.y2 - selected.box.y1) * 0.15f;
                }
                // 第3项：头部瞄准约束（默认关）。若启用且瞄头，把瞄准点钳进头区安全区
                // 并限制单帧滞后，防止锁头时瞄准点飘出头部。约束在 AimPointProfile.cpp。
                if (aim_point.head_aim.enabled) {
                    constrain_aim_point_to_head(selected.box, aim_point, &tx, &ty);
                }
                // 第15阶段：目标跟踪器（速度估计 + 预测）。
                // 目标切换（target_id 变化）→ tracker 内部 Reset（速度清零）。
                // prediction_time_s_>0 时用预测点做控制误差；=0 保持原行为（直接用瞄准点）。
                if (tracker_.target_switched(selected.target_id)) {
                    tracker_.reset();
                }
                tracker_.update(tx, ty, selected.target_id, task.timestamp_us);
                // 第15阶段：控制误差必须用「平滑后瞄准点」。
                // 此前 prediction_time_s_=0 时 control 直接用原始 ex/ey，
                // 检测框上边缘 y1 帧间跳变（±18px，模型头顶边界）直接进 PID：
                //   K_p 输出 ±2 count + K_d 反向 ±4 count → 输出在 deadzone(1.0)
                //   反复穿越 → 「停顿（阻塞）+ 锁定后上下抖动」。
                // 平滑位置来自 AimTracker 内置 One-Euro（min_cutoff=0.8Hz），
                // 预测关闭时也用它做控制误差（不平滑只做速度估计）。
                smooth_tx = tracker_.state().x;
                smooth_ty = tracker_.state().y;
                float pred_tx = smooth_tx, pred_ty = smooth_ty;
                if (prediction_time_s_ > 0.0f) {
                    tracker_.predict(prediction_time_s_, &pred_tx, &pred_ty);
                }
                if (last_target_id_ != -1 && selected.target_id != last_target_id_) {
                    // 目标切换：速度/加速度来自旧目标，必须清除预测状态。
                    pid_x_.reset(); pid_y_.reset(); remainder_x_ = remainder_y_ = 0.0f;
                    pull_curve_.reset();  // 拉枪曲线时间基准清零（新目标重新拉枪）
                    continuous_lead_.reset();  // 持续提前量累计清零（新目标重新累计"同向距离"）
                    personal_shader_.reset();  // 拟人化整形重置（新目标重新整形）
                    recoil_.reset();  // 压枪计时/残差清零（新目标重新压枪）
                    lead_pred_.reset();       // BB 提前量：新目标重新收帧/清零积分
                    anti_overshoot_.reset();  // 抗过冲：新目标重新计算衰减帧数
                    humanize_shaper_.reset(); // 拟人化链：历史低通值属于旧目标，必须清
                    global_wave_.reset();
                }
                last_target_id_ = selected.target_id;
                // AIBOX 对标：不做位置外推；误差直接来自本帧检测结果。
                // 速度信息只进入 P_PID 的前馈/Kalman，不在目标坐标层 coast。
                CoordinateTransform::reference_point(static_cast<float>(task.frame_width),
                                                     static_cast<float>(task.frame_height),
                                                     aim_point, &ref_x, &ref_y);
                // 第15阶段：原始误差用瞄准点，控制误差用预测点（若启用）。
                ex = tx - ref_x;
                ey = ty - ref_y;
                pred_ex = pred_tx - ref_x;
                pred_ey = pred_ty - ref_y;
                float control_x = (prediction_time_s_ > 0.0f) ? pred_ex : (smooth_tx - ref_x);
                float control_y = (prediction_time_s_ > 0.0f) ? pred_ey : (smooth_ty - ref_y);
                // ---- BB 提前量（两代，只改 X 轴）----
                // ★ 叠加在**控制误差**上、而不是塞进 tracker 之前的瞄准点：
                //   BB 原版是 `at.x += offset` 后立刻算 `fx = at.x - chX` —— 偏移不经任何滤波
                //   直接进 PID。若加在 tracker 之前，会被 One-Euro 低通吃掉大半，等于没效果。
                // ★ 一代天然滞后一帧（本帧投票 → 下帧用），二代同帧生效；两代默认都关。
                const float dtt_px = std::hypot(ex, ey);
                const float box_cx = (selected.box.x1 + selected.box.x2) * 0.5f;
                const float box_cy = (selected.box.y1 + selected.box.y2) * 0.5f;
                const float box_w = selected.box.x2 - selected.box.x1;
                const float box_h = selected.box.y2 - selected.box.y1;
                if (lead1_cfg.enabled || lead2_cfg.enabled) {
                    const float lead_dx = lead_pred_.prepare_x_offset(
                        ref_x, ref_y, tx, ty, lead_last_move_y_, now_ms32, lead1_cfg, lead2_cfg);
                    if (lead_dx != 0.0f) control_x += lead_dx;
                } else if (lead_pred_.lead1_offset() != 0.0f) {
                    lead_pred_.reset();  // 关掉后清掉残留偏移，保证零输出
                }
                if (frame_profile) {
                    // 自动标定偏置进入同一控制误差域，复用正式 PID/输出链测量响应。
                    if (frame_profile->mouse.calibrating) {
                        control_x += frame_profile->mouse.calibration_bias_x;
                        control_y += frame_profile->mouse.calibration_bias_y;
                    }
                    if (frame_profile->mouse.fov_mode) {
                        // FOV 模式：像素误差 → 角度 → HID count（fov_move 输出已是最终移动量）。
                        // 修复点：此前把 count 域输出替换 control_x 再进 PID（kp=25×count）双重缩放。
                        // 现在 control_x 保持像素域（个人曲线/拉枪距离判定需要像素域），
                        // fov 输出存入 fov_out，在 PID 调用处直接旁路（见 L191-193）。
                        fov_out_x = fov_move_x(ex, static_cast<float>(task.frame_width),
                                               frame_profile->mouse.hfov, frame_profile->mouse.move_speed_x);
                        fov_out_y = fov_move_y(ey, static_cast<float>(task.frame_height),
                                               frame_profile->mouse.vfov, frame_profile->mouse.move_speed_y);
                        fov_mode_active = true;
                    }
                }
                const float dt = previous_timestamp_us > 0 && task.timestamp_us > previous_timestamp_us
                    ? static_cast<float>(task.timestamp_us - previous_timestamp_us) / 1000000.0f : 0.004f;
                const float dt_ms = dt * 1000.0f;  // 拉枪曲线抖动需要毫秒级时间基准
                // pid1.cpp P_PID 直接消费控制域误差（像素域）。
                // FOV 模式：fov_out 已是 count 域最终移动量，直接作为控制器输出（旁路 kp×err）。
                trace_smith_dx = 0.0f; trace_smith_dy = 0.0f;
                trace_control_x = control_x; trace_control_y = control_y;
                if (fov_mode_active) {
                    aibox_x = fov_out_x;
                    aibox_y = fov_out_y;
                } else {
                    // pid1.cpp P_PID：X predict=3.0，Y predict=0（main() 原始参数）。
                    aibox_x = static_cast<float>(pid_x_.update(control_x));
                    aibox_y = static_cast<float>(pid_y_.update(control_y));
                }
                // 输出链：P_PID 输出 × sens（全局灵敏度） × output_scale。
                // rate_x/y 已在 Pid1 内部作为 kp_gain_rate 消费，此处不再重复。
                // ---- BB 提前量反馈环：把本帧横向输出喂给一代做"帧窗口投票"，
                //      产出的 offset 供**下一帧**的 control_x 使用（一代天生滞后一帧）。
                //      喂的是 aibox_x（PID 域），与 continuous_lead 的量纲约定一致：
                //      阈值必须与用户灵敏度解耦，不能喂 scaled_x。
                if (lead1_cfg.enabled) {
                    lead_pred_.feed_move_x(aibox_x, dtt_px, now_ms32, true, box_cx, box_cy,
                                           box_w, box_h, lead1_cfg);
                }
                // 二代提前量的 Y 轴抑制读"上一帧纵向输出"，此处记下本帧值供下一帧用。
                lead_last_move_y_ = aibox_y;
                const float out_gain = out_sensitivity * out_scale;
                scaled_x = aibox_x * out_gain;
                scaled_y = aibox_y * out_gain;
                // 个人曲线只改变输出倍率，不绕过 PID、死区和热键安全门。
                const float personal_distance = std::hypot(control_x, control_y);
                const float personal_gain = PersonalMotion::scale(personal_distance, personal_motion);
                scaled_x *= personal_gain;
                scaled_y *= personal_gain;
                // 拉枪曲线：目标误差 ≥ min_distance 时，在拉枪方向附加弧线/抖动（Y 轴附加量）。
                // 位置在 personal_gain 之后、deadzone 之前（PullCurve.hpp 设计输出链顺序）。
                // err 用控制域误差（control_x/y），out 用当前缩放输出（scaled_x/y）。
                scaled_y += pull_curve_.apply(control_x, control_y, scaled_x, scaled_y,
                                             pull_curve_cfg, dt_ms);
                // 持续提前量（continuous_lead）：AI 输出**持续同向**累计超过 enter 阈值后，
                // 在 X 轴附加偏置（渐入渐出），用于跟住横向持续移动的目标。
                // 位置在 pull_curve 之后、recoil 之前（同为输出链附加项）；
                // 依然受 deadzone → remainder → int16 → 拟人化整形 → 热键安全门约束，
                // 热键放开时最终输出照样被归零（不绕过任何安全门）。
                //
                // ★ 量纲（关键）：喂入的是 PID 输出 aibox_x/aibox_y（count 域的"AI 输出"），
                //   **不是** scaled_x/scaled_y。原因：enter 阈值必须与用户灵敏度解耦 ——
                //   若喂 scaled_*（= aibox × sens × output_scale × personal_gain），
                //   用户一调高灵敏度就会让提前量**静默地更早触发**，行为不可预期。
                //   截断成 int32 只丢 <1 count 的残差，对"累计同向距离"判定无实质影响。
                //
                // ★★ 新老互斥（业主 2026-09-24 裁定「新版替老版，界面只留一套」）：
                //   新版提前量（lead1/lead2）只要有一代开启，老的持续提前量就**不参与输出**，
                //   否则两条 X 轴偏移会叠加（老的在 scaled_x 上、新的在 control_x 上，
                //   同一功能的两个补丁同时生效 ⇒ 提前量翻倍且互相打架）。
                //   切到新版时把老引擎的累计/渐入电平清掉，避免关掉新版后残留一个旧偏置。
                if (!lead1_cfg.enabled && !lead2_cfg.enabled) {
                    scaled_x += continuous_lead_.apply(static_cast<int32_t>(aibox_x),
                                                       static_cast<int32_t>(aibox_y),
                                                       dt_ms, lead_cfg);
                } else if (lead_cfg.enabled) {
                    continuous_lead_.reset();
                }
                // 压枪（recoil）：开火期间持续下压补偿后坐力。
                // 位置在 pull_curve 之后、deadzone 之前（与 pull_curve 同一注入点语义）。
                // 压枪量（count 域）与 PID 输出融合，之后统一走 deadzone → remainder →
                // int16 → 拟人化整形 → 热键安全门；Gate 关闭时最终输出仍被归零。
                {
                    if (recoil_bb_cfg.enabled) {
                        // BB 三段查表引擎（含垂直修正 + 力度渐变）。
                        // adv/simple 倍率由 BB 扳机（trigger2）决定；扳机没跑时恒 1.0。
                        const auto bbo = recoil_.update_bb(
                            hotkey_bits, selected.valid, dtt_px, ty, ref_y,
                            recoil_cfg, recoil_bb_cfg, vc_cfg, dt_ms, recoil_px_per_count,
                            1.0f, 1.0f);
                        scaled_y += bbo.recoil_y + bbo.vert_y;
                        scaled_x += bbo.recoil_x + bbo.vert_x;
                    } else {
                        const auto rd = recoil_.update(hotkey_bits, selected.valid, recoil_cfg, dt_ms,
                                                       recoil_px_per_count);
                        scaled_y += rd.y;  // 下压为正（目标偏下方向）
                        scaled_x += rd.x;  // 拟人 X 微动（可正可负）
                    }
                }
                // ---- BB 抗过冲（recoil 之后、deadzone 之前）----
                // 靠近目标时按内/外圈强度分段衰减位移；各圈"最多衰减 N 帧"，跑满即本轮停手。
                if (anti_over_cfg.enabled) {
                    anti_overshoot_.apply(&scaled_x, &scaled_y, dtt_px, now_ms32, anti_over_cfg);
                }
                // ---- BB 全局正弦扰动（deadzone 之前）----
                if (global_wave_cfg.enabled) {
                    global_wave_.apply(&scaled_x, &scaled_y, now_ms32, global_wave_cfg);
                }
                // output_deadzone（自适应死区基准）：低于死区的输出归零（防微抖）。
                if (std::abs(scaled_x) < out_deadzone) scaled_x = 0.0f;
                if (std::abs(scaled_y) < out_deadzone) scaled_y = 0.0f;
                // 保留小数余量，避免小幅连续误差被整数 HID count 截断。
                remainder_x_ += scaled_x; remainder_y_ += scaled_y;
                // int16 截断保护：单帧输出 clamp 到 HID count 范围（-32768..32767），
                // 防异常大值 static_cast 产生实现定义行为（乱飞）。
                constexpr float kHidMax = 32767.0f;
                constexpr float kHidMin = -32768.0f;
                const float cx_f = std::clamp(remainder_x_, kHidMin, kHidMax);
                const float cy_f = std::clamp(remainder_y_, kHidMin, kHidMax);
                move_x = static_cast<int16_t>(cx_f);
                move_y = static_cast<int16_t>(cy_f);
                remainder_x_ -= static_cast<float>(move_x); remainder_y_ -= static_cast<float>(move_y);
                // 兜底：若余数已非有限（理论上 validate 已挡），立即清零防持续乱飞
                if (!std::isfinite(remainder_x_) || !std::isfinite(remainder_y_)) {
                    remainder_x_ = 0.0f; remainder_y_ = 0.0f;
                    pid_x_.reset(); pid_y_.reset();
                }
                // ---- 拟人化整形引擎（第 1 项落地）：对 move_x/move_y 做 Fitts 时长+速度包络+垂直抖动 ----
                // 只作用于热键 Gate 之前；Gate 关闭时输出仍被归零（安全边界不变）。
                // 输入：已量化 count(dx,dy) + 控制误差 px(ex,ey)；按需激活（新目标首次有效帧）。
                // ---- BB 拟人化链（humanize.enabled 时替掉旧 personal_shader_）----
                // 顺序固定：低通 → 反应延迟 → 过冲 → 制动 → 高斯噪声。
                // 注入点与旧引擎相同（int16 量化之后、热键 Gate 之前）⇒ 安全边界不变。
                // ★ speed_fluctuation / accuracy_sim 两个"附加项"本期只提供模块与配置键、
                //   **未接线**（默认关 ⇒ 无影响）；等面板那批确认取值口径后再接。
                if (humanize_cfg.enabled) {
                    float hx = static_cast<float>(move_x);
                    float hy = static_cast<float>(move_y);
                    HumanizeShaper::Context hctx;
                    hctx.dtt = dtt_px;
                    hctx.now_ms = now_ms32;
                    hctx.aiming = true;
                    humanize_shaper_.apply(&hx, &hy, humanize_cfg, hctx);
                    move_x = static_cast<int16_t>(std::clamp(hx, kHidMin, kHidMax));
                    move_y = static_cast<int16_t>(std::clamp(hy, kHidMin, kHidMax));
                } else if (personal_traj_cfg.enabled && injection_allowed) {
                    if (!personal_shader_.active()) {
                        // 激活一次移动：用当前控制误差距离作为本次移动目标距离
                        personal_shader_.activate(std::hypot(control_x, control_y), personal_traj_cfg);
                        target_age_ms_ = 0.0f;
                    }
                    // 运行时参数：目标速度（tracker 估）、目标年龄、目标框半径
                    const auto& ts = tracker_.state();
                    personal_shader_.set_error_speed_px_s(ts.valid ? std::hypot(ts.vx, ts.vy) : 0.0f);
                    personal_shader_.set_target_age_ms(target_age_ms_);
                    target_age_ms_ += dt_ms;
                    personal_shader_.set_target_radius_px(
                        (selected.box.y2 - selected.box.y1) * 0.5f);
                    personal_shader_.shape(&move_x, &move_y, control_x, control_y, dt_ms,
                                           personal_traj_cfg);
                }
            }
            // ---- Hotkey Gate 兜底（安全边界最后一行）----
            // 无论前面算出什么，热键未按下时最终动作强制归零。
            if (!injection_allowed) {
                move_x = 0;
                move_y = 0;
            }
            output_->send(output::OutputAction{move_x, move_y, 0, 0, task.frame_number, task.timestamp_us});
            // ---- 第13阶段：链路诊断采样（默认关闭；开启后每 N 帧输出一次完整链路）----
            {
                PipelineDebug::Snapshot ds;
                ds.frame_number = task.frame_number;
                ds.frame_w = task.frame_width;
                ds.frame_h = task.frame_height;
                ds.detections = task.detections.size();
                ds.target.valid = selected.valid;
                ds.target.class_id = selected.valid ? selected.box.class_id : 0;
                ds.target.confidence = selected.valid ? selected.box.score : 0.0f;
                ds.target.box = selected.valid ? selected.box : DetectionBox{};
                ds.target.center_x = selected.valid ? (selected.box.x1 + selected.box.x2) * 0.5f : 0.0f;
                ds.target.center_y = selected.valid ? (selected.box.y1 + selected.box.y2) * 0.5f : 0.0f;
                ds.target.target_id = selected.valid ? selected.target_id : -1;
                ds.point.valid = selected.valid;
                ds.point.x = tx;
                ds.point.y = ty;
                ds.error_x = ex;
                ds.error_y = ey;
                ds.command.dx = move_x;
                ds.command.dy = move_y;
                ds.command.valid = true;
                ds.command.frame_number = task.frame_number;
                ds.command.timestamp_us = task.timestamp_us;
                ds.mouse_disabled = !injection_allowed;  // 本阶段恒 true（禁止注入）
                pipeline_debug_.sample(ds);
            }
            // ---- 第13阶段：PID 逐帧 Trace 采集（默认关闭；只记录不改变控制行为）----
            if (pid_trace_.enabled()) {
                PidTrace::Entry e;
                e.timestamp_us = task.timestamp_us;
                e.frame_number = task.frame_number;
                e.target_id = selected.valid ? selected.target_id : -1;
                e.target_x = selected.valid ? tx : 0.0f;
                e.target_y = selected.valid ? ty : 0.0f;
                e.reference_x = selected.valid ? ref_x : 0.0f;
                e.reference_y = selected.valid ? ref_y : 0.0f;
                e.error_x = ex;
                e.error_y = ey;
                // 第15阶段：预测点/预测误差（AimTracker 输出；未启用预测时=瞄准点/原始误差）
                e.predicted_x = selected.valid ? (prediction_time_s_ > 0.0f ? pred_ex + ref_x : tx) : 0.0f;
                e.predicted_y = selected.valid ? (prediction_time_s_ > 0.0f ? pred_ey + ref_y : ty) : 0.0f;
                e.pred_error_x = selected.valid ? pred_ex : 0.0f;
                e.pred_error_y = selected.valid ? pred_ey : 0.0f;
                e.controller_raw_x = selected.valid ? aibox_x : 0.0f;
                e.controller_raw_y = selected.valid ? aibox_y : 0.0f;
                // 第15阶段：输出链中间值（死区前原始缩放输出 / 死区后 / 余数）
                e.deadzone_out_x = selected.valid ? scaled_x : 0.0f;
                e.deadzone_out_y = selected.valid ? scaled_y : 0.0f;
                e.rate_limited_x = selected.valid ? scaled_x : 0.0f;
                e.rate_limited_y = selected.valid ? scaled_y : 0.0f;
                e.remainder_x = remainder_x_;
                e.remainder_y = remainder_y_;
                e.final_command_x = move_x;
                e.final_command_y = move_y;
                e.target_switch = (selected.valid && target_id_before != -1 &&
                                   selected.target_id != target_id_before) ? 1 : 0;
                e.target_lost = selected.valid ? 0 : 1;
                e.confidence = selected.valid ? selected.box.score : 0.0f;
                pid_trace_.record(e);
            }
            last_timestamp_us_ = task.timestamp_us;
            std::lock_guard<std::mutex> lk(status_mutex_);
            status_.has_task = true;
            status_.detection_boxes = task.detections;
            status_.has_target = selected.valid;
            status_.target_id = selected.valid ? selected.target_id : -1;
            status_.target_class_id = selected.valid ? selected.box.class_id : -1;
            // 显示框使用同一目标的关联检测框并集，避免只显示头/躯干局部框。
            // 控制链仍使用 selected.box，显示框扩展不会改变瞄准行为。
            if (selected.valid) {
                float display_x1 = selected.box.x1;
                float display_y1 = selected.box.y1;
                float display_x2 = selected.box.x2;
                float display_y2 = selected.box.y2;
                const float selected_cx = (selected.box.x1 + selected.box.x2) * 0.5f;
                const float selected_cy = (selected.box.y1 + selected.box.y2) * 0.5f;
                const float selected_w = std::max(1.0f, selected.box.x2 - selected.box.x1);
                const float selected_h = std::max(1.0f, selected.box.y2 - selected.box.y1);
                for (const auto& candidate : task.detections) {
                    if (&candidate == &selected.box) continue;
                    const float candidate_cx = (candidate.x1 + candidate.x2) * 0.5f;
                    const float candidate_cy = (candidate.y1 + candidate.y2) * 0.5f;
                    const bool vertical_overlap = candidate.y2 >= selected.box.y1 &&
                                                  candidate.y1 <= selected.box.y2;
                    const bool horizontal_near = std::fabs(candidate_cx - selected_cx) <=
                                                 std::max(selected_w * 1.5f, 120.0f);
                    const bool vertical_near = std::fabs(candidate_cy - selected_cy) <= selected_h * 0.75f;
                    if (vertical_overlap && horizontal_near && vertical_near) {
                        display_x1 = std::min(display_x1, candidate.x1);
                        display_y1 = std::min(display_y1, candidate.y1);
                        display_x2 = std::max(display_x2, candidate.x2);
                        display_y2 = std::max(display_y2, candidate.y2);
                    }
                }
                // 显示框 One-Euro 平滑：检测框上边缘 y1 帧间跳变 ±18px（模型头顶边界），
                // 平滑后预览框稳定、标定稳定检测（aim_pos_x/y + width/height 变化 <5%）可过。
                // 目标切换时重建平滑状态（新目标坐标完全不同，避免旧轨迹拖尾）。
                if (last_display_target_id_ != selected.target_id) {
                    display_smooth_x1_.reset();
                    display_smooth_y1_.reset();
                    display_smooth_x2_.reset();
                    display_smooth_y2_.reset();
                    last_display_target_id_ = selected.target_id;
                }
                const float display_dt = last_display_ts_us_ > 0 &&
                                                 task.timestamp_us > last_display_ts_us_
                                             ? static_cast<float>(task.timestamp_us - last_display_ts_us_) /
                                                   1000000.0f
                                             : 0.004f;
                last_display_ts_us_ = task.timestamp_us;
                display_x1 = display_smooth_x1_.update(display_x1, display_dt);
                display_y1 = display_smooth_y1_.update(display_y1, display_dt);
                display_x2 = display_smooth_x2_.update(display_x2, display_dt);
                display_y2 = display_smooth_y2_.update(display_y2, display_dt);
                status_.target_x1 = display_x1;
                status_.target_y1 = display_y1;
                status_.target_x2 = display_x2;
                status_.target_y2 = display_y2;
                // 尺寸用平滑后的显示框：标定稳定检测（_calib_target）读 width/height，
                // 原始 h 因 y1 抖动 7.5% > 5% 阈值会导致「目标稳定检测超时」。
                status_.target_width = display_x2 - display_x1;
                status_.target_height = display_y2 - display_y1;
            } else {
                status_.target_x1 = 0.0f;
                status_.target_y1 = 0.0f;
                status_.target_x2 = 0.0f;
                status_.target_y2 = 0.0f;
                status_.target_width = 0.0f;
                status_.target_height = 0.0f;
            }
            if (selected.valid) ++status_.target_frames; else ++status_.no_target_frames;
            uint32_t active_tracks = 0;
            for (const auto& te : selector_.tracks()) {
                if (te.active) ++active_tracks;
            }
            status_.tracks = active_tracks;
            // 遥测：平滑后瞄准点（滤检测框 y1 抖动）。
            // CoreRuntime 把 predicted_x/y 映射为 metrics.aim_pos_x/y，
            // 标定稳定检测（_calib_target 的 center jitter <1px）读的就是它，
            // 必须用平滑位置，否则 18px 级框抖动直接导致「目标稳定检测超时」。
            status_.predicted_x = selected.valid ? smooth_tx : 0.0f;
            status_.predicted_y = selected.valid ? smooth_ty : 0.0f;
            status_.target_point_x = selected.valid ? tx : 0.0f;
            status_.target_point_y = selected.valid ? ty : 0.0f;
            status_.reference_x = selected.valid ? ref_x : 0.0f;
            status_.reference_y = selected.valid ? ref_y : 0.0f;
            status_.error_x = ex;
            status_.error_y = ey;
            status_.pid_output_x = selected.valid ? aibox_x : 0.0f;
            status_.pid_output_y = selected.valid ? aibox_y : 0.0f;
            status_.scheduler_input_x = selected.valid ? scaled_x : 0.0f;
            status_.scheduler_input_y = selected.valid ? scaled_y : 0.0f;
            status_.control_x = trace_control_x;
            status_.control_y = trace_control_y;
            status_.smith_dx = trace_smith_dx;
            status_.smith_dy = trace_smith_dy;
            status_.move_x = move_x;
            status_.move_y = move_y;
            if (status_.consumed == 0) {
                status_.min_move_x = status_.max_move_x = move_x;
                status_.min_move_y = status_.max_move_y = move_y;
            } else {
                status_.min_move_x = std::min(status_.min_move_x, move_x);
                status_.max_move_x = std::max(status_.max_move_x, move_x);
                status_.min_move_y = std::min(status_.min_move_y, move_y);
                status_.max_move_y = std::max(status_.max_move_y, move_y);
            }
            // move_x/move_y 已是 int16 HID count（见上文 clamp 到 ±32767），
            // 截断阈值必须用 int16 极值判定；旧的 ±127 是 8-bit 时代阈值，
            // 会把正常小幅移动误计为"截断"（E-09，仅遥测）。
            constexpr int16_t kMoveClampMin = std::numeric_limits<int16_t>::min();
            constexpr int16_t kMoveClampMax = std::numeric_limits<int16_t>::max();
            if (move_x <= kMoveClampMin || move_x >= kMoveClampMax ||
                move_y <= kMoveClampMin || move_y >= kMoveClampMax) {
                ++status_.clipped_frames;
            }
            if (!injection_allowed) ++status_.gated_frames;
            // 遥测口径：last_hotkey_bits 保留**原始物理**按键位图（挂起时也如实反映手上真按了什么），
            // 挂起本身单独用一个布尔字段表达，避免"挂起"与"没按键"在遥测里混成同一件事。
            status_.last_hotkey_bits = raw_buttons;
            status_.hotkeys_suspended = hotkeys_suspended_;
            status_.last_injection_allowed = injection_allowed;
            status_.last_timestamp_us = task.timestamp_us;
            status_.last_frame = task.frame_number;
            ++status_.consumed;
        }
        std::this_thread::sleep_for(std::chrono::microseconds(interval_us_));
    }
}
}  // namespace ttbox::core::aim
