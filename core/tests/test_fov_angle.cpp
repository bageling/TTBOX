// test_fov_angle.cpp — FOV 像素误差换算测试。
//
// ★ item 11 口径统一：本文件已由"裸 main() + assert"并入 ttbox_test 框架（理由同
//   test_aim_thread.cpp：assert 受 NDEBUG 控制 ⇒ Release 下被编译掉、测试变空操作恒绿；
//   裸 assert 失败即 SIGABRT、无 passed/skipped/failed 摘要行、无法计 skip）。
//   注：本 TU 是**单文件可执行**（core/CMakeLists.txt:575-577 仅编本文件），故底部 main()
//       直接调用 run_all()，**无需改动 core/CMakeLists.txt**，CTest 项 test_fov_angle
//       计数（1）保持不变。
#include "test_util.hpp"
#include "mouse/FovAngle.hpp"
#include <cmath>

using namespace ttbox::core::aim;

// X 轴 FOV 换算：中心误差→0；右偏为正、左偏为负、左右对称。
TEST(fov_move_x_sign_and_symmetry) {
    const float center = fov_move_x(0.0f, 1920.0f, 90.0f, 500.0f);
    const float right  = fov_move_x(100.0f, 1920.0f, 90.0f, 500.0f);
    const float left   = fov_move_x(-100.0f, 1920.0f, 90.0f, 500.0f);
    CHECK(center == 0.0f);
    CHECK(right > 0.0f);
    CHECK(left < 0.0f);
    CHECK(std::fabs(right) == std::fabs(left));
}

int main() {
    std::printf("=== ttbox_core tests (fov_angle) ===\n");
    const int failed = ::ttbox_test::run_all();
    std::printf("=== tests done (exit=%d) ===\n", failed == 0 ? 0 : 1);
    return failed == 0 ? 0 : 1;
}
