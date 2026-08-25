// AimThread.cpp — 独立瞄准线程最小可验证实现。
#include "aim/AimThread.hpp"
#include <chrono>
#include <utility>
#include "aim/AimError.hpp"
#include "mouse/FovAngle.hpp"
namespace ttbox::core::aim {
bool AimThread::start(AimTargetMailbox* mailbox, std::shared_ptr<output::IHidOutput> output, int interval_us, RuntimeConfig* runtime_config, std::atomic<uint16_t>* physical_buttons) {
    if (!mailbox || !output || running_.exchange(true)) return false;
    mailbox_ = mailbox; output_ = std::move(output); interval_us_ = interval_us > 0 ? interval_us : 4000; runtime_config_ = runtime_config; physical_buttons_ = physical_buttons;
    { std::lock_guard<std::mutex> lk(status_mutex_); status_ = {}; status_.running = true; }
    aibox_pid_x_.init(25.0, 25.0, 3.0, 0.03, 9900.0);
    aibox_pid_y_.init(25.0, 25.0, 0.0, 0.03, 9900.0);
    thread_ = std::thread(&AimThread::loop, this);
    return true;
}
void AimThread::stop() {
    if (!running_.exchange(false)) return;
    if (thread_.joinable()) thread_.join();
    std::lock_guard<std::mutex> lk(status_mutex_); status_.running = false;
}
AimThread::Status AimThread::status() const { std::lock_guard<std::mutex> lk(status_mutex_); return status_; }
void AimThread::loop() {
    uint64_t last_frame = 0;
    while (running_.load(std::memory_order_acquire)) {
        AimTargetTask task;
        if (mailbox_->take_latest(&task, last_frame)) {
            last_frame = task.frame_number;
            last_timestamp_us_ = task.timestamp_us;
            // 新控制链：目标选择 → 误差 → 纯 PID/P 控制 → OutputAction。
            TargetSelectorConfig scfg;
            scfg.roi_w = task.frame_width; scfg.roi_h = task.frame_height;
            scfg.confidence = 0.0f;
            float kp_x = 0.0f, kp_y = 0.0f, ki_x = 0.0f, ki_y = 0.0f, kd_x = 0.0f, kd_y = 0.0f;
            if (runtime_config_) {
                auto profile = runtime_config_->snapshot();
                if (profile) {
                    scfg.fov_range = profile->fov.enabled ? profile->fov.radius * 2.0f : 1.0f;
                    scfg.lost_grace_ms = profile->mouse.lost_grace_ms;
                    kp_x = profile->mouse.kp_x; kp_y = profile->mouse.kp_y;
                    ki_x = profile->mouse.ki_x; ki_y = profile->mouse.ki_y;
                    kd_x = profile->mouse.kd_x; kd_y = profile->mouse.kd_y;
                    smith_.set_dead_ms(profile->mouse.smith_dead_ms);
                    abg_.configure(profile->mouse.alpha, profile->mouse.beta, profile->mouse.gamma, profile->mouse.predict_dt_ms);
                }
            }
            const auto selected = selector_.select(task.detections, scfg,
                static_cast<uint32_t>(task.timestamp_us / 1000ULL));
            AimStateEvent event; event.has_target = selected.valid;
            event.hotkey_active = true;
            if (physical_buttons_ && runtime_config_) {
                auto p = runtime_config_->snapshot();
                if (p) {
                    const uint16_t buttons = physical_buttons_->load(std::memory_order_acquire);
                    const bool a = (buttons & p->mouse.aim_hotkey) != 0;
                    const bool b = p->mouse.aim_hotkey2 != 0 && (buttons & p->mouse.aim_hotkey2) != 0;
                    // 鼠标五键统一位图：左1、右2、中4、侧1 8、侧2 16。
                    // any 模式下主键命中即可；配置副键时，副键也可单独作为触发键。
                    event.hotkey_active = p->mouse.aim_hotkey_mode == 1 ? (a && b) : (a || b);
                }
            }
            event.now_ms = task.timestamp_us / 1000ULL;
            if (state_machine_.update(event, scfg.lost_grace_ms)) { controller_.reset(); smith_.reset(); abg_.reset(); remainder_x_=0.0f; remainder_y_=0.0f; last_target_id_=-1; }
            int16_t move_x = 0, move_y = 0; float ex = 0.0f, ey = 0.0f;
            float trace_control_x = 0.0f, trace_control_y = 0.0f;
            float trace_smith_dx = 0.0f, trace_smith_dy = 0.0f;
            if (selected.valid && task.frame_width > 0 && task.frame_height > 0) {
                // 目标框上部瞄准点：避免框中心落到躯干/裆部，先取框高 25% 处。
                const float tx = (selected.box.x1 + selected.box.x2) * 0.5f;
                const float ty = selected.box.y1 + (selected.box.y2 - selected.box.y1) * 0.25f;
                const float dt_target = last_timestamp_us_ > 0 && task.timestamp_us > last_timestamp_us_
                    ? static_cast<float>(task.timestamp_us - last_timestamp_us_) / 1000000.0f : 0.004f;
                if (last_target_id_ != -1 && selected.target_id != last_target_id_) {
                    // 目标切换：速度/加速度来自旧目标，必须清除预测状态。
                    abg_.reset(); smith_.reset(); controller_.reset(); remainder_x_ = remainder_y_ = 0.0f;
                }
                last_target_id_ = selected.target_id;
                abg_.update(tx, ty, dt_target);
                const auto predicted = abg_.predicted();
                ex = predicted.x - task.frame_width * 0.5f; ey = predicted.y - task.frame_height * 0.5f;
                float control_x = ex;
                float control_y = ey;
                if (runtime_config_) {
                    auto profile = runtime_config_->snapshot();
                    if (profile && profile->mouse.fov_mode) {
                        // FOV 模式：先将像素误差转换为角度对应的鼠标移动量。
                        control_x = fov_move_x(ex, static_cast<float>(task.frame_width),
                                               profile->mouse.hfov, profile->mouse.move_speed_x);
                        control_y = fov_move_y(ey, static_cast<float>(task.frame_height),
                                               profile->mouse.vfov, profile->mouse.move_speed_y);
                    }
                }
                float dt = last_timestamp_us_ > 0 && task.timestamp_us > last_timestamp_us_
                    ? static_cast<float>(task.timestamp_us - last_timestamp_us_) / 1000000.0f : 0.004f;
                // Smith 的在途量单位是最终输出 count；只有 FOV 换算后的控制域与其一致时才扣除。
                // 非 FOV 模式下 control 是像素误差，不能直接减 HID count，避免量纲混用导致振荡。
                SmithPredictor::Summary pending{};
                bool smith_enabled = false;
                if (runtime_config_) {
                    auto profile = runtime_config_->snapshot();
                    smith_enabled = profile && profile->mouse.fov_mode;
                }
                if (smith_enabled) {
                    pending = smith_.predicted(task.timestamp_us);
                    control_x -= pending.dx; control_y -= pending.dy;
                }
                trace_smith_dx = pending.dx; trace_smith_dy = pending.dy;
                trace_control_x = control_x; trace_control_y = control_y;
                // AIBOX P_PID：X predict=3.0，Y predict=0；不再走简化 PID。
                const auto motion = output::OutputAction{};
                const float aibox_x = static_cast<float>(aibox_pid_x_.update(control_x));
                const float aibox_y = static_cast<float>(aibox_pid_y_.update(control_y));
                // 保留小数余量，避免小幅连续误差被整数 HID count 截断。
                remainder_x_ += aibox_x; remainder_y_ += aibox_y;
                move_x = static_cast<int16_t>(remainder_x_);
                move_y = static_cast<int16_t>(remainder_y_);
                remainder_x_ -= static_cast<float>(move_x); remainder_y_ -= static_cast<float>(move_y);
                smith_.record(task.frame_number, static_cast<float>(move_x), static_cast<float>(move_y), task.timestamp_us);
            }
            output_->send(output::OutputAction{move_x, move_y, 0, 0, task.frame_number, task.timestamp_us});
            std::lock_guard<std::mutex> lk(status_mutex_);
            status_.has_task = true;
            status_.has_target = selected.valid;
            if (selected.valid) ++status_.target_frames; else ++status_.no_target_frames;
            status_.predicted_x = selected.valid ? (selected.box.x1 + selected.box.x2) * 0.5f : 0.0f;
            status_.predicted_y = selected.valid ? selected.box.y1 + (selected.box.y2 - selected.box.y1) * 0.25f : 0.0f;
            status_.error_x = ex;
            status_.error_y = ey;
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
            if (move_x <= -127 || move_x >= 127 || move_y <= -127 || move_y >= 127) ++status_.clipped_frames;
            status_.last_frame = task.frame_number;
            ++status_.consumed;
        }
        std::this_thread::sleep_for(std::chrono::microseconds(interval_us_));
    }
}
}  // namespace ttbox::core::aim
