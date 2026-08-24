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
                    event.hotkey_active = p->mouse.aim_hotkey_mode == 1 ? (a && b) : (a || (p->mouse.aim_hotkey2 == 0 && a));
                }
            }
            event.now_ms = task.timestamp_us / 1000ULL;
            if (state_machine_.update(event, scfg.lost_grace_ms)) { controller_.reset(); smith_.reset(); abg_.reset(); remainder_x_=0.0f; remainder_y_=0.0f; last_target_id_=-1; }
            int16_t move_x = 0, move_y = 0; float ex = 0.0f, ey = 0.0f;
            if (selected.valid && task.frame_width > 0 && task.frame_height > 0) {
                const float tx = (selected.box.x1 + selected.box.x2) * 0.5f;
                const float ty = (selected.box.y1 + selected.box.y2) * 0.5f;
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
                (void)dt; // 当前 MotionController 接口尚未接收 dt，先记录真实时基。
                const auto pending = smith_.predicted(task.timestamp_us);
                control_x -= pending.dx; control_y -= pending.dy;
                const auto motion = controller_.update(control_x, control_y, kp_x, kp_y, ki_x, ki_y, kd_x, kd_y, dt);
                // 保留小数余量，避免小幅连续误差被整数 HID count 截断。
                remainder_x_ += motion.out_x; remainder_y_ += motion.out_y;
                move_x = static_cast<int16_t>(remainder_x_);
                move_y = static_cast<int16_t>(remainder_y_);
                remainder_x_ -= static_cast<float>(move_x); remainder_y_ -= static_cast<float>(move_y);
                smith_.record(task.frame_number, static_cast<float>(move_x), static_cast<float>(move_y), task.timestamp_us);
            }
            output_->send(output::OutputAction{move_x, move_y, 0, 0, task.frame_number, task.timestamp_us});
            std::lock_guard<std::mutex> lk(status_mutex_);
            status_.has_task = true; status_.has_target = selected.valid; status_.error_x = ex; status_.error_y = ey; status_.move_x = move_x; status_.move_y = move_y; status_.last_frame = task.frame_number; ++status_.consumed;
            status_.has_task = true; status_.last_frame = task.frame_number; ++status_.consumed;
        }
        std::this_thread::sleep_for(std::chrono::microseconds(interval_us_));
    }
}
}  // namespace ttbox::core::aim
