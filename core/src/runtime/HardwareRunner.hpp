// HardwareRunner.hpp — RK3588 硬件流水线拥有者。
// 只负责 Capture/WorkerPool/AimThread 的生命周期，不承载算法实现。
#pragma once
#include <memory>
#include <string>
#include <atomic>
#include "capture/V4L2Capture.hpp"
#include "rknn/WorkerPool.hpp"
#include "pipeline/AimTargetMailbox.hpp"
#include "aim/AimThread.hpp"
#include "output/IHidOutput.hpp"
namespace ttbox::core {
class HardwareRunner {
public:
    struct Params {
        V4L2Capture::Params capture;
        WorkerPool::Params workers;
        RuntimeConfig* runtime_config=nullptr;
        std::shared_ptr<output::IHidOutput> output;
    };
    bool initialize(const Params&,std::string* error=nullptr);
    bool start(std::string* error=nullptr);
    void stop();
    bool running() const{return running_.load();}
private:
    Params params_{};
    std::unique_ptr<V4L2Capture> capture_;
    std::unique_ptr<WorkerPool> workers_;
    std::unique_ptr<aim::AimTargetMailbox> mailbox_;
    aim::AimThread aim_thread_;
    std::atomic<bool> running_{false};
};
}
