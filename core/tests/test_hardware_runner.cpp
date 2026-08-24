// test_hardware_runner.cpp — 仅验证 HardwareRunner 接口可构建，不打开板端设备。
#include <cassert>
#include <memory>
#include "runtime/HardwareRunner.hpp"
#include "output/IHidOutput.hpp"
int main(){
 ttbox::core::HardwareRunner r;
 ttbox::core::HardwareRunner::Params p;
 p.capture.device="/dev/video0"; p.workers.worker_cores={1};
 p.output=std::make_shared<ttbox::core::output::NullHidOutput>();
 assert(!r.initialize(p)); // 缺少模型/运行时参数时，initialize 需拒绝不完整配置
 return 0;
}
