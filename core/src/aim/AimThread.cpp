// AimThread.cpp — 独立瞄准线程最小可验证实现。
#include "aim/AimThread.hpp"
#include <chrono>
#include <utility>
namespace ttbox::core::aim {
bool AimThread::start(AimTargetMailbox* mailbox, std::shared_ptr<output::IHidOutput> output, int interval_us) {
    if (!mailbox || !output || running_.exchange(true)) return false;
    mailbox_ = mailbox; output_ = std::move(output); interval_us_ = interval_us > 0 ? interval_us : 4000;
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
            output_->send(output::OutputAction{0, 0, 0, 0, task.frame_number, task.timestamp_us});
            std::lock_guard<std::mutex> lk(status_mutex_);
            status_.has_task = true; status_.last_frame = task.frame_number; ++status_.consumed;
        }
        std::this_thread::sleep_for(std::chrono::microseconds(interval_us_));
    }
}
}  // namespace ttbox::core::aim
