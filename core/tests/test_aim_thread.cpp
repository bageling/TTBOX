// test_aim_thread.cpp — AimThread 生命周期与最新任务消费测试。
//
// ★ item 11 口径统一：本文件已由"裸 main() + assert"并入 ttbox_test 框架。
//   理由（与 test_aim_target_mailbox.cpp 同款）：
//     ① assert 受 NDEBUG 控制 —— Release 构建下 `assert` 被整段编译掉 ⇒ 测试变**空操作**、
//        恒退 0（"零断言静默 PASS"的一种形态）。CHECK 恒生效，杜绝此坑。
//     ② 裸 assert 失败即 SIGABRT —— CTest 只见"异常退出"，无 passed/skipped/failed 摘要行、
//        无法计 skip（框架外无 report_skip），破坏"绿=真绿"。
//   改用 TEST/CHECK 后：断言失败被**计数**（不中断进程），退出码由 run_all() 统一汇总。
//   注：本 TU 是**单文件可执行**（core/CMakeLists.txt:515-517 仅编本文件），故底部 main()
//       直接调用 run_all()（等价 test_main.cpp 惯用法），**无需改动 core/CMakeLists.txt**，
//       CTest 项 test_aim_thread 计数（1）保持不变。
#include "test_util.hpp"
#include "aim/AimThread.hpp"
#include "output/IHidOutput.hpp"
#include <chrono>
#include <memory>
#include <thread>

using namespace ttbox::core::aim;

// 生命周期：start → offer(frame=7) → status 反映最新任务 → 无目标时位移归零。
TEST(aim_thread_lifecycle_consumes_latest_task) {
    AimTargetMailbox mailbox(1);
    auto output = std::make_shared<ttbox::core::output::NullHidOutput>();
    AimThread thread;
    CHECK(thread.start(&mailbox, output, 1000));

    AimTargetTask task;
    task.frame_number = 7;
    task.timestamp_us = 123;
    CHECK(mailbox.offer(0, task));

    // 活性上界（liveness bound），**非等值语义**：任务最终**必被** AimThread 消费，
    //   但**不承诺**在某个固定时限内完成——旧写法 `sleep_for(10ms)` 隐含"10ms 内必被
    //   调度消费"的延迟假设，高负载下（如紧跟并行 `cmake --build -j`）工作线程可能尚未
    //   被调度 ⇒ 偶发假红（实测 1/20）。故改为**带 deadline 的轮询等待**：1ms 间隔、上界 500ms。
    //   500ms 依据：远大于本场景实际所需（观测到的失败只差一个调度周期即可消费），又远小于
    //   CTest 默认超时，既不掩盖真故障、也不会误杀慢机器。
    //   到期**不提前 return**：照常取最终快照并交由下面的 CHECK 判定失败，避免退化成
    //   "零断言静默 PASS"（P0-2b 族反模式）。
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(500);
    while (!thread.status().has_task && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    // 在 stop() 之前取快照（与旧用例同序，保持语义不变）
    const auto status = thread.status();
    thread.stop();

    CHECK(status.has_task);
    CHECK_EQ(status.last_frame, static_cast<uint64_t>(7));
    CHECK(!status.has_target);
    CHECK(status.move_x == 0 && status.move_y == 0);
}

int main() {
    std::printf("=== ttbox_core tests (aim_thread) ===\n");
    const int failed = ::ttbox_test::run_all();
    std::printf("=== tests done (exit=%d) ===\n", failed == 0 ? 0 : 1);
    return failed == 0 ? 0 : 1;
}
