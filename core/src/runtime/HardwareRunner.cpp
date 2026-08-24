// HardwareRunner.cpp — 严格的下游先停、上游后停生命周期。
#include "runtime/HardwareRunner.hpp"
namespace ttbox::core {
bool HardwareRunner::initialize(const Params& p,std::string* error){
 if(!p.output){if(error)*error="HardwareRunner 输出后端为空";return false;}
 if(p.workers.worker_cores.empty()||p.workers.worker_cores.size()>aim::AimTargetMailbox::kMaxWorkers){if(error)*error="Worker 数量必须为 1~3";return false;}
 params_=p; capture_=std::make_unique<V4L2Capture>(); workers_=std::make_unique<WorkerPool>(); mailbox_=std::make_unique<aim::AimTargetMailbox>(p.workers.worker_cores.size());
 return capture_->configure(p.capture,error);
}
bool HardwareRunner::start(std::string* error){
 if(running_.exchange(true)||!capture_||!workers_||!mailbox_)return false;
 if(!capture_->open(error)||!capture_->start(error)){running_=false;return false;}
 auto wp=params_.workers; const auto& f=capture_->format(); wp.latest=capture_->latest_frame_ref(); wp.frame_w=f.width; wp.frame_h=f.height; wp.aim_mailbox=mailbox_.get(); wp.runtime_config=params_.runtime_config;
 if(!workers_->start(wp,error)){capture_->stop();capture_->close();running_=false;return false;}
 if(!aim_thread_.start(mailbox_.get(),params_.output,4000,params_.runtime_config)){workers_->stop();capture_->stop();capture_->close();running_=false;return false;}
 return true;
}
void HardwareRunner::stop(){if(!running_.exchange(false))return; aim_thread_.stop(); workers_->stop(); capture_->stop(); capture_->close();}
}
