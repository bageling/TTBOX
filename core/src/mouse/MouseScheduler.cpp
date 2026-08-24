// MouseScheduler.cpp — A10 AI 鼠标调度器实现
#include "mouse/MouseScheduler.hpp"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <unistd.h>

#include "mouse/AimPointProfile.hpp"
#include "mouse/CoordinateTransform.hpp"
#include "mouse/Deadzone.hpp"
#include "mouse/FovAngle.hpp"
#include "mouse/OutputScale.hpp"
#include "mouse/TargetSelector.hpp"

namespace ttbox::core::aim {

namespace {
// FIFO 帧协议（C 桥 ttbox-hid-bridge 读取）：
//   移动帧：type=0x01 + dx(int16 LE) + dy(int16 LE) = 5 字节
//   控制帧：type=0x02 + flags(1B) + hotkey_mask(1B) = 6 字节
//   flags bit0 = ai_enabled；bit1 = block_physical_x；bit2 = block_physical_y
constexpr uint8_t kTypeMove = 0x01;
constexpr uint8_t kTypeControl = 0x02;
constexpr uint8_t kFlagEnabled = 0x01;
constexpr uint8_t kFlagBlockX = 0x02;
constexpr uint8_t kFlagBlockY = 0x04;
constexpr uint8_t kFlagAllHotkeys = 0x08;  // 瞄准热键触发方式：=1 需主+副同时按下（all），0=任一（any）
}  // namespace

bool MouseScheduler::start(const Params& p, std::string* error) {
    if (p.fifo_path.empty() || p.runtime_config == nullptr || p.latest == nullptr) {
        if (error) *error = "fifo_path/runtime_config/latest 不能为空";
        return false;
    }
    p_ = p;
    running_.store(true);
    thread_ = std::thread(&MouseScheduler::loop, this);
    return true;
}

void MouseScheduler::stop() {
    if (running_.exchange(false)) {
        if (thread_.joinable()) thread_.join();
    }
    if (fifo_fd_ >= 0) {
        ::close(fifo_fd_);
        fifo_fd_ = -1;
    }
}

void MouseScheduler::open_fifo() {
    // 仅创建一次
    static bool created = false;
    if (!created) {
        ::mkfifo(p_.fifo_path.c_str(), 0666);
        created = true;
    }
    if (fifo_fd_ < 0) {
        fifo_fd_ = ::open(p_.fifo_path.c_str(), O_WRONLY | O_NONBLOCK);
    }
}

void MouseScheduler::write_move(bool enabled, int16_t dx, int16_t dy) {
    open_fifo();
    if (fifo_fd_ < 0) return;
    uint8_t buf[5];
    buf[0] = kTypeMove;
    buf[1] = static_cast<uint8_t>(dx & 0xFF);
    buf[2] = static_cast<uint8_t>((dx >> 8) & 0xFF);
    buf[3] = static_cast<uint8_t>(dy & 0xFF);
    buf[4] = static_cast<uint8_t>((dy >> 8) & 0xFF);
    const ssize_t w = ::write(fifo_fd_, buf, sizeof(buf));
    if (w < 0) {
        if (errno == EPIPE) {
            ::close(fifo_fd_);
            fifo_fd_ = -1;
        }
    }
}

void MouseScheduler::write_control(uint8_t flags, uint8_t hotkey) {
    open_fifo();
    if (fifo_fd_ < 0) return;
    uint8_t buf[6];
    buf[0] = kTypeControl;
    buf[1] = flags;
    buf[2] = hotkey;
    buf[3] = 0;
    buf[4] = 0;
    buf[5] = 0;
    const ssize_t w = ::write(fifo_fd_, buf, sizeof(buf));
    if (w < 0) {
        if (errno == EPIPE) {
            ::close(fifo_fd_);
            fifo_fd_ = -1;
        }
    }
}

void MouseScheduler::apply_control(const MouseProfile& mp, uint8_t* last_flags, uint8_t* last_hotkey, bool force) {
    const uint8_t flags = (mp.enabled ? kFlagEnabled : 0) |
                          (mp.block_physical_x ? kFlagBlockX : 0) |
                          (mp.block_physical_y ? kFlagBlockY : 0) |
                          (mp.aim_hotkey_mode == 1 ? kFlagAllHotkeys : 0);
    // 主+副热键合并为位掩码（any=任一命中；all=全部按下才触发，由 C 桥按 flags 判定）
    const uint8_t hotkey_mask = static_cast<uint8_t>(mp.aim_hotkey | mp.aim_hotkey2);
    // force：C 桥可能被独立重启（g_ctrl_flags 清零）→ 周期重发控制帧兜底
    if (force || flags != *last_flags || hotkey_mask != *last_hotkey) {
        write_control(flags, hotkey_mask);
        *last_flags = flags;
        *last_hotkey = hotkey_mask;
    }
}

// 读 C 桥真实瞄准状态（aiming = enabled && 实际热键按住）
bool MouseScheduler::read_cbridge_aiming() {
    FILE* f = std::fopen("/run/ttbox-mouse-stats.json", "rb");
    if (!f) return false;
    char buf[256];
    const size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = 0;
    const char* p = std::strstr(buf, "\"aiming\":");
    return p != nullptr && p[9] == '1';
}

std::vector<DetectionBox> MouseScheduler::to_crop(const std::vector<DetectionBox>& dets,
                                                  const CaptureProfile& cap,
                                                  uint32_t frame_w, uint32_t frame_h,
                                                  uint32_t* roi_w, uint32_t* roi_h) const {
    // ROI 尺寸：capture.width/height > 0 用 ROI，否则全帧
    const uint32_t rw = (cap.width > 0 && cap.height > 0) ? cap.width : frame_w;
    const uint32_t rh = (cap.height > 0) ? cap.height : frame_h;
    *roi_w = rw;
    *roi_h = rh;
    std::vector<DetectionBox> out;
    out.reserve(dets.size());
    // 检测框为全帧系（DecodeNMS::map_coords 已加 roi_x/roi_y）。
    // ROI 原点必须与 WorkerPool::apply_runtime_profile 的 set_roi 完全一致：
    //   中心 = 屏幕中心 + offset（offset 语义=相对屏幕中心偏移）
    //   左上角 = 中心 - roi/2，clamp 到全帧内。
    float ox = 0.0f, oy = 0.0f;
    if (rw > 0 && rh > 0 && rw <= frame_w && rh <= frame_h) {
        const int32_t cx = static_cast<int32_t>(frame_w / 2) + cap.offset_x;
        const int32_t cy = static_cast<int32_t>(frame_h / 2) + cap.offset_y;
        ox = static_cast<float>(std::max<int32_t>(0, std::min<int32_t>(
            cx - static_cast<int32_t>(rw / 2), static_cast<int32_t>(frame_w - rw))));
        oy = static_cast<float>(std::max<int32_t>(0, std::min<int32_t>(
            cy - static_cast<int32_t>(rh / 2), static_cast<int32_t>(frame_h - rh))));
    }
    for (const auto& b : dets) {
        DetectionBox c = b;
        c.x1 -= ox;
        c.x2 -= ox;
        c.y1 -= oy;
        c.y2 -= oy;
        out.push_back(c);
    }
    return out;
}

void MouseScheduler::loop() {
    // 忽略 SIGPIPE（FIFO 无读者时 write 不导致进程退出）
    ::signal(SIGPIPE, SIG_IGN);

    auto last_tick = std::chrono::steady_clock::now();
    uint64_t tick_count = 0;
    int64_t current_target_id = -1;
    uint8_t last_flags = 0xFF;    // 强制首帧发送控制
    uint8_t last_hotkey = 0xFF;

    while (running_.load()) {
        // 定时节拍
        const auto now = std::chrono::steady_clock::now();
        const auto elapsed_us = std::chrono::duration_cast<std::chrono::microseconds>(now - last_tick).count();
        if (elapsed_us < p_.interval_us) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }
        last_tick = now;
        ++tick_count;
        const uint64_t now_us = static_cast<uint64_t>(
            std::chrono::duration_cast<std::chrono::microseconds>(now.time_since_epoch()).count());

        // 配置快照（原子；禁止逐帧 JSON）
        auto prof = p_.runtime_config->snapshot();
        const MouseProfile mp = prof ? prof->mouse : MouseProfile{};
        status_.enabled = mp.enabled;

        // 最新检测
        auto latest = p_.latest->get();
        uint32_t roi_w = p_.frame_w, roi_h = p_.frame_h;
        std::vector<DetectionBox> crop_dets;
        const CaptureProfile cap = prof ? prof->capture : CaptureProfile{};
        if (latest) {
            crop_dets = to_crop(latest->boxes, cap, p_.frame_w, p_.frame_h, &roi_w, &roi_h);
        }

        // 目标选择（crop 系，多目标追踪分层选择）
        TargetSelectorConfig sel_cfg;
        sel_cfg.fov_range = mp.fov_range;
        sel_cfg.confidence = mp.confidence;
        sel_cfg.roi_w = roi_w;
        sel_cfg.roi_h = roi_h;
        sel_cfg.lost_grace_ms = mp.lost_grace_ms;
        if (prof) sel_cfg.class_filter = prof->inference.class_filter;
        const uint32_t now_ms = static_cast<uint32_t>(now_us / 1000u);
        const TargetSelection sel = selector_.select(crop_dets, sel_cfg, now_ms);
        status_.detection_count = static_cast<uint32_t>(crop_dets.size());
        status_.target_class = last_box_valid_ ? last_box_.class_id : (sel.valid ? sel.box.class_id : -1);
        status_.target_confidence = last_box_valid_ ? last_box_.score : (sel.valid ? sel.box.score : 0.0f);
        // 目标 id：由选择器 track 表维护（track_lock 保持 / rect_lock 重锁 / score 新建）
        current_target_id = sel.valid ? static_cast<int64_t>(sel.target_id) : -1;

        // 状态机推进（热键门控在 C 桥：读其真实 aiming，50ms 缓存）。
        // 旧值 500ms：C 桥写盘 500ms + 本侧读取 500ms → 按下/松开热键后状态机
        // 最长滞后 ~1s（延迟开瞄；松键后的幽灵注入已由 C 桥 compute_aiming 门控
        // 兜底，这里只减小状态机本身的滞后）。
        if (now - last_hotkey_read_ >= std::chrono::milliseconds(50)) {
            hotkey_active_ = read_cbridge_aiming();
            last_hotkey_read_ = now;
        }
        AimStateEvent ev;
        // 标定模式（mp.calibrating）：last_box 有效也视为有目标——
        // 否则拉偏把目标移出画面 → has_target=false → LOST_GRACE → IDLE → 自瞄停，
        // err 停住不收敛 → 标定失败。last_box 在锁定段内持续有效。
        ev.has_target = sel.valid || (mp.calibrating && last_box_valid_);
        // 标定模式：绕过真实热键，强制 AIMING 让自瞄全程输出（用偏置拉偏）
        ev.hotkey_active = hotkey_active_ || (mp.enabled && mp.calibrating);
        ev.now_ms = now_us / 1000u;
        // 标定模式 lost_grace 拉长到 60s：目标短暂移出画面期间保持 AIMING/LOST_GRACE 不 IDLE
        const float grace = mp.calibrating ? 60000.0f : mp.lost_grace_ms;
        const bool need_reset = state_machine_.update(ev, grace);
        status_.state = state_machine_.state();
        const AimState st = state_machine_.state();
        if (need_reset || current_target_id != last_target_id_) {
            tracker_.reset();
            controller_.reset();
            limiter_.reset();
            // smooth_ 不 reset：低通滤波状态跨状态切换保持连续，
            // 否则检测抖动导致 AIMING↔IDLE 切换时首帧输出无平滑 → 突跳。
            last_target_id_ = current_target_id;
            last_box_valid_ = false;   // 目标切换/重置后不再沿用旧框
            status_.target_x = 0.0f;
            status_.target_y = 0.0f;
        }

        // ---- 目标锁定与跟踪 ----
        // 未锁定时：采用选择器结果（锁定进入 AIMING 的目标）。
        if (sel.valid && !last_box_valid_) {
            last_box_ = sel.box;
            last_box_valid_ = true;
            lock_miss_ = 0;
        } else if (last_box_valid_ && (st == AimState::kAiming || st == AimState::kLostGrace)) {
            // AIMING/LOST_GRACE：从所有检测框中匹配锁定目标。
            // AIMING 匹配范围收紧；LOST_GRACE（目标短暂丢失）扩大找回范围，
            // 减少"目标断续出现 → 解锁重选 → 换目标"导致的大幅跳动。
            // 匹配到则 EMA 平滑更新锁定框（抑制检测框抖动）。
            const float diag = std::hypot(static_cast<float>(roi_w), static_cast<float>(roi_h));
            const float lcx = (last_box_.x1 + last_box_.x2) * 0.5f;
            const float lcy = (last_box_.y1 + last_box_.y2) * 0.5f;
            const float match_th = diag * (st == AimState::kAiming ? 0.4f : 0.65f);
            float best_d = match_th;
            DetectionBox match;
            bool matched = false;
            for (const auto& b : crop_dets) {
                const float bcx = (b.x1 + b.x2) * 0.5f;
                const float bcy = (b.y1 + b.y2) * 0.5f;
                const float d = std::hypot(bcx - lcx, bcy - lcy);
                if (d < best_d) { best_d = d; match = b; matched = true; }
            }
            if (matched) {
                const float a = 0.15f;  // EMA 平滑系数（α=0.15：重滤波，抑制检测框抖动）
                last_box_.x1 = last_box_.x1 * (1.0f - a) + match.x1 * a;
                last_box_.x2 = last_box_.x2 * (1.0f - a) + match.x2 * a;
                last_box_.y1 = last_box_.y1 * (1.0f - a) + match.y1 * a;
                last_box_.y2 = last_box_.y2 * (1.0f - a) + match.y2 * a;
                last_box_.class_id = match.class_id;
                last_box_.score = match.score;
                lock_miss_ = 0;
            } else if (++lock_miss_ >= (mp.calibrating ? 1500 : 60)) {
                // 锁定目标丢失超时 → 解锁。标定模式 1500 帧(6s)：拉偏把目标移出
                // 画面期间保留 last_box（自瞄用 last_box 继续拉回，目标会重新入画）
                last_box_valid_ = false;
                lock_miss_ = 0;
            }
        }

        int16_t ai_dx = 0, ai_dy = 0;
        // AIMING 启动渐变：进 AIMING 首帧输出小幅递增（防 err 大时猛拉）
        if (st == AimState::kAiming && !was_aiming_) aim_ramp_ = 0.2f;
        was_aiming_ = (st == AimState::kAiming);
        // 仅 AIMING（热键已按且有目标）输出 AI 移动。
        // LOST_GRACE 不输出：目标丢失立即停，避免检测断续时沿旧位置
        // 猛拉导致乱飞/转圈。SELECTING（有目标未按键）与 IDLE 也不输出。
        // 例外：标定模式（mp.calibrating）下 LOST_GRACE 也输出（用锁定框
        // last_box 继续拉向目标）——否则目标检测短暂断续 → 自瞄暂停 →
        // err 不收敛 → 自动标定锁定阶段失败（用户全程不操作，需自动恢复）。
        const bool out_ok = (st == AimState::kAiming) ||
                            (mp.calibrating && st == AimState::kLostGrace);
        if (mp.enabled && out_ok && (sel.valid || last_box_valid_)) {
            const DetectionBox& use_box = last_box_valid_ ? last_box_ : sel.box;
            const float cx = (use_box.x1 + use_box.x2) * 0.5f;
            const float cy = (use_box.y1 + use_box.y2) * 0.5f;
            // 瞄准部位（aim_part 0-10 数值）：0=脚 10=头，offset_y = 1.0 - aim_part*0.09
            // 检测框 y1=头顶 y2=脚底：0→0.95(脚) 5→0.55(躯干) 10→0.1(头)
            AimPointProfile ap = mp.aim_point;
            if (mp.aim_part >= 0 && mp.aim_part <= 10) {
                ap.offset_y = 1.0f - static_cast<float>(mp.aim_part) * 0.09f;
            }
            // 目标框 → aim point（含 class offset，crop 系）
            float ax = 0.0f, ay = 0.0f;
            aim_point_at(use_box, use_box.class_id, ap, &ax, &ay);
            status_.aim_x = ax;
            status_.aim_y = ay;
            // 跟踪 + 预测（XY 独立预测时间：对齐 YU predict_x/predict_y）
            tracker_.update(cx, cy, current_target_id >= 0 ? static_cast<int>(current_target_id) : 0,
                            now_us);
            status_.target_x = cx;
            status_.target_y = cy;
            status_.vel_x = tracker_.state().vx;
            status_.vel_y = tracker_.state().vy;
            // 预测 aim point = aim point + 速度 × 预测时间（XY 独立）
            const float ptx = mp.predict_x > 0.0f ? mp.predict_x : mp.prediction_s;
            const float pty = mp.predict_y > 0.0f ? mp.predict_y : mp.prediction_s;
            // 预测提前量安全上限：不超过 ROI 的 20%。
            // 背景：旧默认 predict=0.5s 配合速度 clamp(±2500px/s) 会产生最大
            // 1250px 的提前量（ROI 才 320px）→ err 爆表 → 满速修正 → 准星转圈/
            // 瞄天瞄地。此上限兜底任何错误配置，正常 1~2 帧提前量远小于该值。
            const float max_lead_x = 0.2f * static_cast<float>(roi_w);
            const float max_lead_y = 0.2f * static_cast<float>(roi_h);
            float lead_x = tracker_.state().vx * ptx;
            float lead_y = tracker_.state().vy * pty;
            if (lead_x > max_lead_x) lead_x = max_lead_x;
            else if (lead_x < -max_lead_x) lead_x = -max_lead_x;
            if (lead_y > max_lead_y) lead_y = max_lead_y;
            else if (lead_y < -max_lead_y) lead_y = -max_lead_y;
            status_.pred_x = ax + lead_x;
            status_.pred_y = ay + lead_y;
            // 像素误差 = 预测 aim point - 瞄准参考点（roi 中心 + aim_offset）
            float rx = 0.0f, ry = 0.0f;
            CoordinateTransform::reference_point(static_cast<float>(roi_w), static_cast<float>(roi_h),
                                                 mp.aim_point, &rx, &ry);
            status_.err_x = status_.pred_x - rx;   // 真实误差（目标 vs 准星，供显示/标定采样）
            status_.err_y = status_.pred_y - ry;
            // 标定偏置（仅标定模式生效）：等效把参考点移至 ref+bias →
            // 自瞄把准星带到偏置位（左/右拉）；Python 清 bias 后自瞄按当前参数拉回，
            // 通过采样拉回过程中的 err 衰减率即可闭环辨识最优 kp。
            float out_err_x = status_.err_x - mp.calibration_bias_x;
            float out_err_y = status_.err_y - mp.calibration_bias_y;
            // 拉枪曲线（对齐参考系统：误差域 px 注入，仅新目标远距离单帧触发，锁定后停止）：
            //   2D 距离 hypot(err) ≥ min_distance + 新目标（track 切换/新建）→ 加弧线偏移。
            //   offset ≈ strength × dist / min_distance（参考 trace 验证公式），方向为误差方向。
            {
                const float dist = std::hypot(out_err_x, out_err_y);
                const bool new_target = (sel.reason == TargetSelection::kScore ||
                                         sel.reason == TargetSelection::kRectLock);
                if (mp.pull_curve.enabled && new_target && dist >= mp.pull_curve.min_distance) {
                    float mag = mp.pull_curve.strength * dist / std::max(mp.pull_curve.min_distance, 1.0f);
                    if (mag > 48.0f) mag = 48.0f;
                    // 弧线偏移：垂直方向附加（横向弧线 → 误差域 x 方向加偏移）
                    float off_x = 0.0f, off_y = 0.0f;
                    if (dist > 1.0f) {
                        off_x = mag * (-out_err_y / dist) * 0.5f;   // 垂直分量 → 横向弧线
                        off_y = mag * (out_err_x / dist) * 0.5f;
                    }
                    if (mp.pull_curve.jitter_px > 0.0f) {
                        const float jx = ((rand() % 2000) / 1000.0f - 1.0f) * mp.pull_curve.jitter_px;
                        const float jy = ((rand() % 2000) / 1000.0f - 1.0f) * mp.pull_curve.jitter_px;
                        off_x += jx; off_y += jy;
                    }
                    out_err_x += off_x;
                    out_err_y += off_y;
                    status_.pull_curve_active = true;
                    status_.pull_curve_offset_x = off_x;
                    status_.pull_curve_offset_y = off_y;
                } else {
                    status_.pull_curve_active = false;
                    status_.pull_curve_offset_x = 0.0f;
                    status_.pull_curve_offset_y = 0.0f;
                }
            }
            // 输出：fov_mode（角度换算）或 纯 P（kp×err）——用带偏置的误差
            float out_x = 0.0f, out_y = 0.0f;
            if (mp.fov_mode) {
                out_x = fov_move_x(out_err_x, static_cast<float>(roi_w), mp.hfov, mp.move_speed_x);
                out_y = fov_move_y(out_err_y, static_cast<float>(roi_h), mp.vfov, mp.move_speed_y);
            } else {
                const MotionOutput p_out = controller_.update(out_err_x, out_err_y,
                                                              mp.kp_x, mp.kp_y,
                                                              mp.ki_x, mp.ki_y, mp.kd_x, mp.kd_y);
                out_x = p_out.out_x;
                out_y = p_out.out_y;
            }
            // OutputScale（rate × sensitivity × output_scale；与 fov_range 分离）
            float sc_x = output_scale_x(out_x, mp);
            float sc_y = output_scale_y(out_y, mp);
            // ★ gain 换算（标定产物 px/count）：controller 输出是 px（kp×err），
            //   除以 gain 得到正确 counts。消除"px 当 count 用"的单位错乱 → 防过冲。
            //   count = px / (px/count)。gain 由自动标定测得（游戏灵敏度物理常数）。
            {
                const float gx = mp.gain_x_px_per_count > 0.05f ? mp.gain_x_px_per_count : 0.65f;
                const float gy = mp.gain_y_px_per_count > 0.05f ? mp.gain_y_px_per_count : 0.65f;
                sc_x /= gx;
                sc_y /= gy;
            }
            // YU 对齐输出链：
            //   controller → scaled(×0.7866≈rate×sens) → softened(≈scaled) →
            //   pending(×latency_feedback 0.65) → 量化(残差回存) → 发送
            // 插件（压枪 recoil/拉枪曲线 pull_curve/持续提前量 continuous_lead/
            //       开火锁Y/humanize）在 C 桥注入层（ttbox-hid-bridge.c inject_ai）
            //       调用，依赖物理鼠标按键状态，C++ 侧不做避免双重叠加。
            // 死区：仅滤除亚像素噪声（0.5 counts），不用自适应大死区
            // 旧 0.06×框宽 死区在 kp=0.15 时把 err<20px 的输出全杀了 → 锁不紧
            float eff_dz = std::max(0.5f, mp.output_deadzone);
            if (mp.calibrating) {
                // 标定模式：跳过死区（否则目标框大 → 死区大 → 小误差被吞 → 自瞄停 → 锁不定）
                sc_x = std::fabs(sc_x) < 0.5f ? 0.0f : sc_x;
                sc_y = std::fabs(sc_y) < 0.5f ? 0.0f : sc_y;
            } else {
                sc_x = std::fabs(sc_x) < eff_dz ? 0.0f : sc_x;
                sc_y = std::fabs(sc_y) < eff_dz ? 0.0f : sc_y;
            }
            // TEMP DEBUG: 标定锁定调试
            if (mp.calibrating && (tick_count % 60) == 0) {
                std::fprintf(stderr, "[DBG] err=(%.1f,%.1f) out=(%.2f,%.2f) sc=(%.2f,%.2f) dz=%.1f ramp=%.2f\n",
                             status_.err_x, status_.err_y, out_x, out_y, sc_x, sc_y, eff_dz, aim_ramp_);
            }
            // 标定模式输出限幅（±6 counts/帧）：拉偏 bias=FOV半径 时 err 巨大，
            // 若不限幅 → 猛甩过冲乱晃（目标出画面后 last_box 固定，err 不变持续猛拉）。
            // 限幅后拉偏/拉回平稳，Python 采样衰减率不受影响（衰减是渐进的）。
            if (mp.calibrating) {
                if (sc_x > 6.0f) sc_x = 6.0f; else if (sc_x < -6.0f) sc_x = -6.0f;
                if (sc_y > 6.0f) sc_y = 6.0f; else if (sc_y < -6.0f) sc_y = -6.0f;
            }
            // Smooth（低通；TTBox 自实现；YU smooth_x/smooth_y 对齐：9900≈不过滤 → alpha 取 1-smooth 归一化）
            float alpha_x = mp.smooth > 0.0f ? mp.smooth : 0.0f;
            float alpha_y = alpha_x;
            if (mp.smooth_x > 0.0f && mp.smooth_x < 9900.0f) alpha_x = 1.0f - mp.smooth_x / 9900.0f;
            if (mp.smooth_y > 0.0f && mp.smooth_y < 9900.0f) alpha_y = 1.0f - mp.smooth_y / 9900.0f;
            sc_x = smooth_.apply_x(sc_x, alpha_x);
            sc_y = smooth_.apply_y(sc_y, alpha_y);
            // AIMING 启动渐变（每帧 +0.2 → 1.0）
            sc_x *= aim_ramp_;
            sc_y *= aim_ramp_;
            aim_ramp_ = std::min(1.0f, aim_ramp_ + 0.2f);
            // 残差量化（AndroidQuantizeMove：lround + 残差回存到下一帧）
            const float tx = sc_x + residual_x_;
            const float ty = sc_y + residual_y_;
            const int32_t qx = static_cast<int32_t>(std::lround(tx));
            const int32_t qy = static_cast<int32_t>(std::lround(ty));
            residual_x_ = tx - static_cast<float>(qx);
            residual_y_ = ty - static_cast<float>(qy);
            // RateLimit（clamp ±127 + 拆包）
            const RateLimitStep st = limiter_.step(qx, qy, 127);
            ai_dx = st.dx;
            ai_dy = st.dy;
        } else {
            // 无目标/未启用：清零（残差复位，防重入旧值）
            residual_x_ = 0.0f;
            residual_y_ = 0.0f;
            if (sel.valid) {
                // 目标存在但未启用：仍记录 target 位置
                status_.target_x = (sel.box.x1 + sel.box.x2) * 0.5f;
                status_.target_y = (sel.box.y1 + sel.box.y2) * 0.5f;
            }
            limiter_.reset();
        }

        status_.ai_dx = ai_dx;
        status_.ai_dy = ai_dy;
        status_.frames = tick_count;
        status_.last_us = now_us;
        // 控制帧（enabled/block/hotkey 变化时发送；每 2s 强制重发兜底 C 桥重启）+ 移动帧
        apply_control(mp, &last_flags, &last_hotkey, (tick_count % 500) == 0);
        write_move(mp.enabled, ai_dx, ai_dy);
    }
}

}  // namespace ttbox::core::aim
