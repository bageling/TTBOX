// CoreRuntime.cpp — 统一控制线程生命周期实现。
#include "runtime/CoreRuntime.hpp"
namespace ttbox::core {
bool CoreRuntime::initialize(std::size_t workers, std::shared_ptr<output::IHidOutput> output, std::string* error){
    if(workers==0 || workers>aim::AimTargetMailbox::kMaxWorkers){if(error)*error="worker 数量必须为 1~3";return false;}
    if(!output){if(error)*error="输出后端不能为空";return false;}
    mailbox_=std::make_unique<aim::AimTargetMailbox>(workers); output_=std::move(output); return true;
}
bool CoreRuntime::start(){if(!mailbox_||running_.exchange(true))return false; if(!aim_thread_.start(mailbox_.get(),output_,4000)){running_=false;return false;} return true;}
void CoreRuntime::stop(){if(!running_.exchange(false))return; aim_thread_.stop();}
}
