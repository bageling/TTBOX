// CoreRuntime.cpp — Capture/Worker/AimThread 统一生命周期。
#include "runtime/CoreRuntime.hpp"
namespace ttbox::core {
bool CoreRuntime::initialize(const Params& p, std::string* error){
    if(!p.output){if(error)*error="输出后端不能为空";return false;}
    if(p.workers.worker_cores.empty() || p.workers.worker_cores.size()>aim::AimTargetMailbox::kMaxWorkers){if(error)*error="Worker 数量必须为 1~3";return false;}
    runtime_config_=p.runtime_config; output_=p.output; worker_params_=p.workers; mailbox_=std::make_unique<aim::AimTargetMailbox>(p.workers.worker_cores.size());
    capture_=std::make_unique<V4L2Capture>(); workers_=std::make_unique<WorkerPool>();
    if(!capture_->configure(p.capture,error)) return false;
    return true;
}
bool CoreRuntime::start(std::string* error){
    if(!capture_||!workers_||!mailbox_||running_.exchange(true)) return false;
    if(!capture_->open(error) || !capture_->start(error)){running_=false;return false;}
    worker_params_.latest = capture_->latest_frame_ref();
    worker_params_.aim_mailbox = mailbox_.get();
    worker_params_.runtime_config = runtime_config_;
    const auto& fmt = capture_->format();
    worker_params_.frame_w = fmt.width; worker_params_.frame_h = fmt.height;
    if(!workers_->start(worker_params_, error)){ capture_->stop(); capture_->close(); running_=false; return false; }
    if(!aim_thread_.start(mailbox_.get(),output_,4000,runtime_config_)){capture_->stop();capture_->close();running_=false;return false;}
    return true;
}
void CoreRuntime::stop(){if(!running_.exchange(false))return; aim_thread_.stop(); if(workers_)workers_->stop(); if(capture_){capture_->stop();capture_->close();}}
}
