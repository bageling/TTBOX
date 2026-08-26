// test_aim_algorithm.cpp — 锁定 AIBOX P_PID 与 Smith 帧号护栏契约。
#include <cassert>
#include <cmath>
#include <iostream>
#include "aim/AiboxPpidController.hpp"
#include "aim/SmithPredictor.hpp"

int main() {
    using ttbox::core::aim::AiboxPpidController;
    using ttbox::core::aim::SmithPredictor;

    // smooth 必须进入 AIBOX soft-limit 的 outputScale，不能被写死成 100。
    AiboxPpidController smooth_default;
    AiboxPpidController smooth_low;
    smooth_default.init(25.0, 25.0, 3.0, 0.3, 9900.0);
    smooth_low.init(25.0, 25.0, 3.0, 0.3, 5000.0);
    const double out_default = smooth_default.update(100.0);
    const double out_low = smooth_low.update(100.0);
    if (!std::isfinite(out_default) || !std::isfinite(out_low) ||
        std::abs(out_default - out_low) <= 1e-6) {
        std::cerr << "P_PID smooth 参数未接线\n";
        return 1;
    }

    // AIBOX 多 NPU 护栏：查询帧较旧时，队列中更新帧必须阻止再次下发。
    SmithPredictor smith;
    smith.set_dead_ms(100.0f);
    smith.record(10, 7.0f, -3.0f, 1000);
    const auto stale = smith.predicted(9, 1000);
    if (!stale.has_newer_frame || std::abs(stale.dx - 7.0f) >= 1e-6f ||
        std::abs(stale.dy + 3.0f) >= 1e-6f) {
        std::cerr << "Smith 帧号护栏未生效\n";
        return 1;
    }
    const auto current = smith.predicted(10, 1000);
    if (current.has_newer_frame) {
        std::cerr << "当前帧错误地被标记为旧帧\n";
        return 1;
    }

    std::cout << "test_aim_algorithm: PASS\n";
    return 0;
}
