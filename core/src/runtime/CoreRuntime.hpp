// CoreRuntime.hpp — 正式 AI Runtime 生命周期入口。
// 本类统一拥有 AimThread；Capture/Worker 接入在板端硬件阶段完成。
#pragma once
#include <atomic>
#include <memory>
#include <string>
#include "aim/AimThread.hpp"
#include "output/IHidOutput.hpp"
#include "pipeline/AimTargetMailbox.hpp"
namespace ttbox::core {
class CoreRuntime {
public:
    CoreRuntime() = default; ~CoreRuntime(){stop();}
    bool initialize(std::size_t worker_count, std::shared_ptr<output::IHidOutput> output, std::string* error=nullptr);
    bool start(); void stop(); bool running() const { return running_.load(); }
    aim::AimTargetMailbox* aim_mailbox(){return mailbox_.get();}
private:
    std::unique_ptr<aim::AimTargetMailbox> mailbox_;
    aim::AimThread aim_thread_;
    std::shared_ptr<output::IHidOutput> output_;
    std::atomic<bool> running_{false};
};
}
