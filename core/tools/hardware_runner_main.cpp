// hardware_runner_main.cpp — 新架构 RK3588 硬件入口。
// 默认 NullHidOutput：只验证 Capture/RGA/RKNN/Worker/AimThread，不移动真实鼠标。
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>
#include <thread>
#include "runtime/HardwareRunner.hpp"
#include "model/ModelAdapter.hpp"
#include "output/IHidOutput.hpp"
int main(int argc,char** argv){
    std::string model; std::string device="/dev/video0"; int workers=1; int seconds=10;
    for(int i=1;i<argc;++i){std::string a=argv[i]; auto next=[&](){return i+1<argc?std::string(argv[++i]):std::string();};
        if(a=="--model")model=next(); else if(a=="--device")device=next(); else if(a=="--workers")workers=std::atoi(next().c_str()); else if(a=="--seconds")seconds=std::atoi(next().c_str());}
    if(model.empty()){std::fprintf(stderr,"用法: hardware_runner_main --model <model.rknn> [--device /dev/video0] [--workers 1] [--seconds 10]\n");return 2;}
    ttbox::core::HardwareRunner::Params p; p.capture.device=device; p.capture.num_buffers=4; p.workers.model_path=model; p.workers.worker_cores.clear();
    for(int i=0;i<workers;++i)p.workers.worker_cores.push_back(i==0?1:(1<<i));
    p.workers.pass_through=false; p.workers.out_w=0; p.workers.out_h=0; p.output=std::make_shared<ttbox::core::output::NullHidOutput>();
    // 适配器由 Worker 在初始化时使用；模型元数据由 RKNN 查询。当前入口先让 Runner 验证设备生命周期。
    ttbox::core::HardwareRunner runner; std::string error;
    if(!runner.initialize(p,&error)){std::fprintf(stderr,"[FAIL] initialize: %s\n",error.c_str());return 1;}
    if(!runner.start(&error)){std::fprintf(stderr,"[FAIL] start: %s\n",error.c_str());return 1;}
    std::printf("hardware_runner_main: running seconds=%d workers=%d output=null\n",seconds,workers);
    for(int i=0;i<seconds;++i){ std::this_thread::sleep_for(std::chrono::seconds(1)); auto s=runner.status(); std::printf("[HW] t=%d format=%ux%u capture=%llu worker=%llu errors=%llu skipped=%llu aim=%llu last_frame=%llu\n", i+1,s.width,s.height,(unsigned long long)s.capture_frames,(unsigned long long)s.worker_processed,(unsigned long long)s.worker_errors,(unsigned long long)s.worker_skipped,(unsigned long long)s.aim_consumed,(unsigned long long)s.aim_last_frame); }
    runner.stop(); std::printf("hardware_runner_main: stopped cleanly\n"); return 0;
}
