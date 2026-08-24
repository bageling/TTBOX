// AimThread.hpp — 独立瞄准控制线程骨架。
// 当前阶段只验证 Worker -> Mailbox -> AimThread 的数据链路，不改变现有采集/推理行为。
#pragma once
#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <thread>
#include "pipeline/AimTargetMailbox.hpp"
#include "output/IHidOutput.hpp"
#include "mouse/AimStateMachine.hpp"
#include "mouse/MotionController.hpp"
#include "mouse/TargetSelector.hpp"
#include "model/RuntimeProfile.hpp"
#include "aim/SmithPredictor.hpp"
namespace ttbox::core::aim {
class AimThread {
public:
    struct Status {
        bool running = false;
        bool has_task = false;
        bool has_target = false;
        float error_x = 0.0f;
        float error_y = 0.0f;
        int16_t move_x = 0;
        int16_t move_y = 0;
        uint64_t last_frame = 0;
        uint64_t consumed = 0;
        uint64_t stale = 0;
    };
    AimThread() = default;
    ~AimThread() { stop(); }
    bool start(AimTargetMailbox* mailbox, std::shared_ptr<output::IHidOutput> output, int interval_us = 4000, RuntimeConfig* runtime_config = nullptr);
    void stop();
    Status status() const;
private:
    void loop();
    AimTargetMailbox* mailbox_ = nullptr;
    std::shared_ptr<output::IHidOutput> output_;
    std::atomic<bool> running_{false};
    std::thread thread_;
    int interval_us_ = 4000;
    RuntimeConfig* runtime_config_ = nullptr;
    TargetSelector selector_;
    MotionController controller_;
    AimStateMachine state_machine_;
    SmithPredictor smith_;
    uint64_t last_timestamp_us_ = 0;
    mutable std::mutex status_mutex_;
    Status status_{};
};
}  // namespace ttbox::core::aim
