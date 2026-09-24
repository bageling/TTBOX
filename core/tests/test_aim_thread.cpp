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
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <thread>

using namespace ttbox::core::aim;

namespace {

// 记录每帧实际发出的 move，并累加出"真正交给输出链的 count 总和"。
// 用来把 AimStatus::out_counts_* 这个**标定分母**与逐帧输出对账。
class CountingHidOutput final : public ttbox::core::output::IHidOutput {
public:
    bool send(const ttbox::core::output::OutputAction& a) override {
        std::lock_guard<std::mutex> lk(mu_);
        sum_x_ += a.move_x;
        sum_y_ += a.move_y;
        ++frames_;
        return true;
    }
    int64_t sum_x() { std::lock_guard<std::mutex> lk(mu_); return sum_x_; }
    int64_t sum_y() { std::lock_guard<std::mutex> lk(mu_); return sum_y_; }
    uint64_t frames() { std::lock_guard<std::mutex> lk(mu_); return frames_; }
private:
    std::mutex mu_;
    int64_t sum_x_ = 0;
    int64_t sum_y_ = 0;
    uint64_t frames_ = 0;
};

ttbox::core::DetectionBox calib_box() {
    ttbox::core::DetectionBox b;
    b.x1 = 560.0f; b.y1 = 180.0f; b.x2 = 640.0f; b.y2 = 300.0f;
    b.score = 0.9f;
    b.class_id = 0;
    return b;
}

}  // namespace

// 自动标定的**分母真源**：累计请求投递的 HID count 必须与逐帧实际发出的 move 逐位一致。
//
// 为什么单独锁这一条：标定要算的是物理增益 gain(px/count) = Δ目标位移px / ΔΣcounts。
// 旧实现把「偏置的 px」当分母（px/px），量纲就不对 ⇒ 比值恒 ≈1.0、与游戏灵敏度无关，
// 标定"成功"也只能写出与真实手感无关的 kp。分母换成 Σcounts 之后，这个累计量
// 一旦与真实输出错一位，全部标定结论都错 ⇒ 必须有用例钉住，不能靠肉眼。
TEST(aim_thread_out_counts_match_sent_moves) {
    AimTargetMailbox mailbox(1);
    auto output = std::make_shared<CountingHidOutput>();
    auto profile = std::make_shared<ttbox::core::RuntimeProfile>();
    ttbox::core::RuntimeConfig config;
    std::atomic<uint16_t> buttons{0};

    profile->mouse.enabled = true;
    profile->mouse.calibrating = true;          // 标定期无视物理热键强制放行注入
    profile->mouse.calibration_bias_x = 20.0f;  // 参考点像素偏置（控制误差域）
    profile->mouse.aim_hotkey = 0x02;
    profile->mouse.kp_x = 1.0f;
    profile->mouse.kp_y = 1.0f;
    profile->mouse.kd_x = 0.0f;
    profile->mouse.kd_y = 0.0f;
    // smooth 是**削弱倍率**（outputScale = 10000 - smooth，只削 Kp/Kd）：
    // 默认 9900 会把 Kp 砍到 1/100，配合 output_deadzone 默认 1.0 ⇒ 本用例可能全帧零输出，
    // 断言就退化成 0 == 0。这里显式关掉削弱与死区，保证确实产生非零输出。
    profile->mouse.smooth_x = 0.0f;
    profile->mouse.smooth_y = 0.0f;
    profile->mouse.output_deadzone = 0.0f;
    config.update(profile);

    AimThread thread;
    CHECK(thread.start(&mailbox, output, 1000, &config, &buttons));

    for (uint64_t f = 1; f <= 40; ++f) {
        AimTargetTask t;
        t.frame_number = f;
        t.timestamp_us = 1000ULL * f;
        t.frame_width = 1280;
        t.frame_height = 720;
        t.has_target = true;
        t.target = calib_box();
        t.aim_point = {600.0f, 240.0f};
        t.detections.push_back(calib_box());
        mailbox.offer(0, t);
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    thread.stop();  // join 后不再有新的 send，读数与累计量稳定

    const auto st = thread.status();
    CHECK(output->frames() > 0);
    // 核心不变式：累计量 == 逐帧实际发出量之和（两条轴各自成立）
    CHECK_EQ(st.out_counts_x, output->sum_x());
    CHECK_EQ(st.out_counts_y, output->sum_y());
    // 防止"全帧零输出 ⇒ 0 == 0 的弱断言"：本场景必然有非零输出
    CHECK(st.out_counts_x != 0);
}

// Gate 关（未按热键且不在标定）时输出被归零 ⇒ 累计量不得增长。
// 若漏在 Gate 之前累计，标定期间会把"被拦掉的帧"也算进分母 ⇒ gain 偏小。
TEST(aim_thread_out_counts_ignore_gated_frames) {
    AimTargetMailbox mailbox(1);
    auto output = std::make_shared<CountingHidOutput>();
    auto profile = std::make_shared<ttbox::core::RuntimeProfile>();
    ttbox::core::RuntimeConfig config;
    std::atomic<uint16_t> buttons{0};

    profile->mouse.enabled = true;
    profile->mouse.calibrating = false;
    profile->mouse.aim_hotkey = 0x02;
    profile->mouse.kp_x = 1.0f;
    profile->mouse.kp_y = 1.0f;
    profile->mouse.smooth_x = 0.0f;
    profile->mouse.smooth_y = 0.0f;
    profile->mouse.output_deadzone = 0.0f;
    config.update(profile);

    AimThread thread;
    CHECK(thread.start(&mailbox, output, 1000, &config, &buttons));

    for (uint64_t f = 1; f <= 20; ++f) {  // buttons 恒 0 ⇒ 热键未按
        AimTargetTask t;
        t.frame_number = f;
        t.timestamp_us = 1000ULL * f;
        t.frame_width = 1280;
        t.frame_height = 720;
        t.has_target = true;
        t.target = calib_box();
        t.aim_point = {600.0f, 240.0f};
        t.detections.push_back(calib_box());
        mailbox.offer(0, t);
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    thread.stop();

    const auto st = thread.status();
    CHECK(output->frames() > 0);          // 帧照常走完输出链（Gate 在末端归零）
    CHECK_EQ(st.out_counts_x, 0);
    CHECK_EQ(st.out_counts_y, 0);
    CHECK_EQ(output->sum_x(), 0);
    CHECK(st.gated_frames > 0);
}

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
