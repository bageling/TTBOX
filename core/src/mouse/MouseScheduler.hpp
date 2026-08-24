// MouseScheduler.hpp — A10 AI 鼠标调度器
//
// 独立线程消费最新检测 → 运行完整 AI 链（TargetSelector → AimTracker →
// Prediction → CoordinateTransform → MotionController → OutputScale →
// Deadzone → Smooth → RateLimit）→ 输出 AI dx/dy 到 FIFO（C 桥读取注入）。
//
// 设计约束：
//   - 不阻塞 Capture/RKNN Worker（独立线程，不 sleep 推理线程）
//   - AI 未启用（mouse.enabled=false）时输出零增量，物理透传行为与 A9 完全一致
//   - 禁止逐帧 JSON：配置经 RuntimeConfig 原子快照读取
#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <string>
#include <thread>

#include "mouse/AimStateMachine.hpp"
#include "mouse/AimTracker.hpp"
#include "mouse/LatestDetections.hpp"
#include "mouse/MotionController.hpp"
#include "mouse/MouseTypes.hpp"
#include "mouse/RateLimit.hpp"
#include "mouse/Smooth.hpp"
#include "mouse/TargetSelector.hpp"
#include "model/RuntimeProfile.hpp"

namespace ttbox::core::aim {

class MouseScheduler {
public:
    struct Params {
        std::string fifo_path;              // AI 增量输出 FIFO（C 桥读取）
        RuntimeConfig* runtime_config = nullptr;  // RuntimeProfile 快照（含 mouse 段）
        LatestDetections* latest = nullptr;       // 最新检测（WorkerPool 发布）
        uint32_t frame_w = 0;               // detection 全帧宽（映射到 crop 用）
        uint32_t frame_h = 0;
        int interval_us = 4000;             // 调度周期（默认 4ms ≈ 250Hz）
    };

    // 运行时状态（web 展示）
    struct RunStatus {
        AimState state = AimState::kIdle;
        bool enabled = false;
        float target_x = 0.0f, target_y = 0.0f;
        int target_class = -1;
        float target_confidence = 0.0f;
        uint32_t detection_count = 0;
        float aim_x = 0.0f, aim_y = 0.0f;        // aim point（crop 系）
        float err_x = 0.0f, err_y = 0.0f;        // 误差（含预测修正）
        float vel_x = 0.0f, vel_y = 0.0f;        // 目标速度（px/s）
        float pred_x = 0.0f, pred_y = 0.0f;      // 预测 aim point（crop 系）
        bool pull_curve_active = false;          // 拉枪曲线激活（trace 对齐）
        float pull_curve_offset_x = 0.0f;
        float pull_curve_offset_y = 0.0f;
        int16_t ai_dx = 0, ai_dy = 0;
        uint64_t frames = 0;
        uint64_t last_us = 0;
    };

    MouseScheduler() = default;
    ~MouseScheduler() { stop(); }

    bool start(const Params& p, std::string* error = nullptr);
    void stop();
    bool running() const { return running_.load(); }
    RunStatus status() const { return status_; }

private:
    void loop();
    // FIFO 写入（非阻塞；无读者时忽略）
    void open_fifo();
    void write_move(bool enabled, int16_t dx, int16_t dy);
    void write_control(uint8_t flags, uint8_t hotkey);
    void apply_control(const MouseProfile& mp, uint8_t* last_flags, uint8_t* last_hotkey, bool force = false);
    // 读 C 桥真实热键/瞄准状态（/run/ttbox-mouse-stats.json），每 50ms 缓存
    bool read_cbridge_aiming();
    // 检测框（全帧系）→ crop 系（扣除 ROI 原点）
    std::vector<DetectionBox> to_crop(const std::vector<DetectionBox>& dets,
                                      const CaptureProfile& cap,
                                      uint32_t frame_w, uint32_t frame_h,
                                      uint32_t* roi_w, uint32_t* roi_h) const;

    Params p_;
    std::atomic<bool> running_{false};
    std::thread thread_;
    int fifo_fd_ = -1;
    bool hotkey_active_ = false;                          // C 桥真实热键状态（50ms 缓存）
    std::chrono::steady_clock::time_point last_hotkey_read_{};

    AimStateMachine state_machine_;
    AimTracker tracker_;
    MotionController controller_;
    SmoothFilter smooth_;
    RateLimiter limiter_;
    TargetSelector selector_;     // 多目标追踪选择器（状态化，跨帧维护 track）
    float residual_x_ = 0.0f;       // 残差量化累计（AndroidQuantizeMove 语义）
    float residual_y_ = 0.0f;
    int64_t last_target_id_ = -1;
    DetectionBox last_box_;        // 锁定目标框（AIMING 输出用）
    bool last_box_valid_ = false;
    int lock_miss_ = 0;            // 目标锁定连续失配计数（远离锁定目标帧数）
    float aim_ramp_ = 1.0f;        // AIMING 启动渐变（0.2→1.0，防猛拉）
    bool was_aiming_ = false;      // 上一帧是否 AIMING（渐变复位检测）
    RunStatus status_;
};

}  // namespace ttbox::core::aim
