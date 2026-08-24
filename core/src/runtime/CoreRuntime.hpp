// CoreRuntime.hpp — 正式 AI Runtime 生命周期入口。
// 本类统一拥有 AimThread；Capture/Worker 接入在板端硬件阶段完成。
#pragma once
#include <atomic>
#include <memory>
#include <string>
#include "aim/AimThread.hpp"
#include "output/IHidOutput.hpp"
#include "pipeline/AimTargetMailbox.hpp"
#include "capture/V4L2Capture.hpp"
#include "rknn/WorkerPool.hpp"
#include "model/RuntimeProfile.hpp"
namespace ttbox::core {
class CoreRuntime {
public:
    CoreRuntime() = default; ~CoreRuntime(){stop();}
    struct Params {
        V4L2Capture::Params capture;
        WorkerPool::Params workers;
        std::shared_ptr<output::IHidOutput> output;
        RuntimeConfig* runtime_config = nullptr;
    };
    bool initialize(const Params& params, std::string* error=nullptr);
    bool start(std::string* error=nullptr); void stop(); bool running() const { return running_.load(); }
    aim::AimTargetMailbox* aim_mailbox(){return mailbox_.get();}
private:
    std::unique_ptr<aim::AimTargetMailbox> mailbox_;
    std::unique_ptr<V4L2Capture> capture_;
    std::unique_ptr<WorkerPool> workers_;
    RuntimeConfig* runtime_config_ = nullptr;
    WorkerPool::Params worker_params_{};
    aim::AimThread aim_thread_;
    std::shared_ptr<output::IHidOutput> output_;
    std::atomic<bool> running_{false};
};
}
