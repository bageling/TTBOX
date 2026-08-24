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
#include "model/RuntimeProfile.hpp"
#include "mouse/MouseTypes.hpp"
#include "output/IHidOutput.hpp"
#include "output/TraceHidOutput.hpp"
#include "output/FifoHidOutput.hpp"
int main(int argc,char** argv){
    std::string model; std::string device="/dev/video0"; std::string fifo="/run/ttbox-aim.fifo"; int workers=1; int seconds=10; bool trace=false; bool fifo_mode=false; bool simulate=false;
    for(int i=1;i<argc;++i){std::string a=argv[i]; auto next=[&](){return i+1<argc?std::string(argv[++i]):std::string();};
        if(a=="--model")model=next(); else if(a=="--device")device=next(); else if(a=="--workers")workers=std::atoi(next().c_str()); else if(a=="--seconds")seconds=std::atoi(next().c_str()); else if(a=="--trace")trace=true; else if(a=="--fifo")fifo_mode=true; else if(a=="--simulate-hotkey")simulate=true;}
    if(model.empty()){std::fprintf(stderr,"用法: hardware_runner_main --model <model.rknn> [--device /dev/video0] [--workers 1] [--seconds 10] [--trace|--fifo] [--simulate-hotkey]\n");return 2;}
    ttbox::core::HardwareRunner::Params p; p.capture.device=device; p.capture.num_buffers=4; p.workers.model_path=model; p.workers.worker_cores.clear();
    for(int i=0;i<workers;++i)p.workers.worker_cores.push_back(i==0?1:(1<<i));
    ttbox::core::RuntimeConfig runtime_config;
    ttbox::core::RuntimeProfile profile;
    profile.model_id = model;
    profile.mouse.enabled = false;
    profile.mouse.aim_hotkey = 0x01;  // 与板端 bridge 配置一致：默认鼠标左键
    profile.mouse.aim_hotkey2 = 0x00;
    profile.mouse.aim_hotkey_mode = 0;
    // Trace 阶段使用温和增益，避免大像素误差一开始就长期撞 ±127。
    profile.mouse.kp_x = 0.20f;
    profile.mouse.kp_y = 0.20f;
    profile.mouse.ki_x = profile.mouse.ki_y = 0.0f;
    profile.mouse.kd_x = profile.mouse.kd_y = 0.0f;
    profile.mouse.smith_dead_ms = 28.4f;
    profile.mouse.alpha = 0.8f;
    profile.mouse.beta = 0.3f;
    profile.mouse.gamma = 0.1f;
    profile.mouse.predict_dt_ms = 50.0f;
    runtime_config.update(profile);
    p.runtime_config = &runtime_config;
    if (simulate && fifo_mode) { std::fprintf(stderr, "[FAIL] --simulate-hotkey 禁止与 --fifo 同时使用\n"); return 2; }
    p.simulated_buttons = simulate ? 0x01 : 0;
    p.workers.pass_through=false; p.workers.out_w=0; p.workers.out_h=0; auto trace_output=std::make_shared<ttbox::core::output::TraceHidOutput>();
    auto fifo_output=std::make_shared<ttbox::core::output::FifoHidOutput>(fifo);
    if (fifo_mode) p.output=fifo_output;
    else if (trace) p.output=trace_output;
    else p.output=std::make_shared<ttbox::core::output::NullHidOutput>();
    // 适配器由 Worker 在初始化时使用；模型元数据由 RKNN 查询。当前入口先让 Runner 验证设备生命周期。
    ttbox::core::HardwareRunner runner; std::string error;
    if(!runner.initialize(p,&error)){std::fprintf(stderr,"[FAIL] initialize: %s\n",error.c_str());return 1;}
    if(!runner.start(&error)){std::fprintf(stderr,"[FAIL] start: %s\n",error.c_str());return 1;}
    std::printf("hardware_runner_main: running seconds=%d workers=%d output=%s\n", seconds, workers, fifo_mode ? "fifo" : (trace ? "trace" : "null"));
    for(int i=0;i<seconds;++i){ std::this_thread::sleep_for(std::chrono::seconds(1)); auto s=runner.status(); std::printf("[HW] t=%d format=%ux%u capture=%llu rga=%llu infer=%llu decode=%llu publish=%llu candidates=%llu detections=%llu worker=%llu errors=%llu skipped=%llu aim=%llu target=%llu no_target=%llu pred=(%.1f,%.1f) control=(%.1f,%.1f) smith=(%.1f,%.1f) move_range=[%d..%d,%d..%d] clipped=%llu last_frame=%llu\n", i+1,s.width,s.height,(unsigned long long)s.capture_frames,(unsigned long long)s.worker_rga_ok,(unsigned long long)s.worker_inference_ok,(unsigned long long)s.worker_decode_ok,(unsigned long long)s.worker_published,(unsigned long long)s.worker_candidates,(unsigned long long)s.worker_detections,(unsigned long long)s.worker_processed,(unsigned long long)s.worker_errors,(unsigned long long)s.worker_skipped,(unsigned long long)s.aim_consumed,(unsigned long long)s.aim_target_frames,(unsigned long long)s.aim_no_target_frames,s.aim_predicted_x,s.aim_predicted_y,s.aim_control_x,s.aim_control_y,s.aim_smith_dx,s.aim_smith_dy,s.aim_min_move_x,s.aim_max_move_x,s.aim_min_move_y,s.aim_max_move_y,(unsigned long long)s.aim_clipped_frames,(unsigned long long)s.aim_last_frame); }
    runner.stop(); std::printf("hardware_runner_main: stopped cleanly\n"); return 0;
}
