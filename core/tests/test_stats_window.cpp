// test_stats_window.cpp — S6 内存泄漏修复回归：StatsCollector 滑动窗口语义
// 背景：samples_ 曾无界 push_back（worker 每帧多路 add 且永不清空），
// 板上 2h 长稳定位 73.6 MB/h 泄漏，根因即此。本测试钉死窗口行为。
#include "common/Stats.hpp"

#include <cassert>
#include <cstdio>
#include <thread>
#include <vector>

using ttbox::core::StatsCollector;

static void test_unbounded_before_window_fills() {
    StatsCollector s;
    for (uint64_t i = 1; i <= 100; ++i) s.add(i);
    assert(s.count() == 100);
    assert(s.min() == 1);
    assert(s.max() == 100);
    assert(s.avg() == 50.5);
    assert(s.percentile(50) == 50 || s.percentile(50) == 51);  // 插值落在这两者
    std::printf("  [ok] 未满窗口顺序语义不变\n");
}

static void test_window_caps_and_overwrites_oldest() {
    StatsCollector s;
    for (uint64_t i = 1; i <= StatsCollector::kMaxSamples + 1000; ++i) {
        s.add(i);
        assert(s.count() <= StatsCollector::kMaxSamples);  // 任何时刻都有界
    }
    // 末窗口 = 最后 kMaxSamples 个样本 [1001+... ]，逐段核对：
    assert(s.count() == StatsCollector::kMaxSamples);
    assert(s.max() == StatsCollector::kMaxSamples + 1000);
    // 最旧样本 1001 已被覆盖：min 应 > 1000
    assert(s.min() > 1000);
    std::printf("  [ok] 窗口封顶 %zu，最旧样本被环形覆盖\n", StatsCollector::kMaxSamples);
}

static void test_window_keeps_recent_only() {
    StatsCollector s;
    // 先灌 1..kMax，再灌一个大值：大值必须保留，旧值被挤出
    for (uint64_t i = 1; i <= StatsCollector::kMaxSamples; ++i) s.add(i);
    s.add(9'999'999);
    assert(s.count() == StatsCollector::kMaxSamples);
    assert(s.max() == 9'999'999);
    // 挤出的是 1（head 指向的位置即最旧者）
    assert(s.min() == 2);
    std::printf("  [ok] 覆盖的是最旧样本（min 2..kMax + 新值）\n");
}

static void test_absorb_respects_window() {
    StatsCollector a, b;
    for (uint64_t i = 1; i <= StatsCollector::kMaxSamples; ++i) a.add(i);
    for (uint64_t i = 1; i <= 100; ++i) b.add(i);
    a.absorb(b);
    assert(a.count() <= StatsCollector::kMaxSamples);  // absorb 后仍有界
    assert(a.max() == 100);                            // b 的样本在最新端
    // 自吸收无害
    a.absorb(a);
    assert(a.count() <= StatsCollector::kMaxSamples);
    std::printf("  [ok] absorb 有界 + 自吸收无害\n");
}

static void test_clear_resets_head() {
    StatsCollector s;
    for (uint64_t i = 0; i < StatsCollector::kMaxSamples * 2; ++i) s.add(i);
    s.clear();
    assert(s.count() == 0);
    assert(s.avg() == 0.0);
    assert(s.percentile(95) == 0);
    s.add(42);
    assert(s.count() == 1);
    assert(s.avg() == 42.0);
    std::printf("  [ok] clear 重置窗口与游标\n");
}

static void test_percentile_bounded_memory() {
    // percentile 内部拷贝受窗口约束——间接验证：大样本量下调用不炸、语义对
    StatsCollector s;
    for (uint64_t i = 1; i <= StatsCollector::kMaxSamples; ++i) s.add(i);
    assert(s.percentile(0) == 1);
    assert(s.percentile(100) == StatsCollector::kMaxSamples);
    assert(s.percentile(50) == StatsCollector::kMaxSamples / 2 ||
           s.percentile(50) == StatsCollector::kMaxSamples / 2 + 1);
    std::printf("  [ok] percentile 边界正确\n");
}

static void test_concurrent_add_safe() {
    StatsCollector s;
    constexpr int kThreads = 4;
    constexpr int kPerThread = 5000;
    std::vector<std::thread> ts;
    for (int t = 0; t < kThreads; ++t) {
        ts.emplace_back([&s] {
            for (int i = 0; i < kPerThread; ++i) s.add(static_cast<uint64_t>(i));
        });
    }
    for (auto& th : ts) th.join();
    assert(s.count() == StatsCollector::kMaxSamples);  // 全部 add 都不丢锁、总量有界
    std::printf("  [ok] 多线程 add 安全且有界\n");
}

int main() {
    std::printf("== StatsCollector 滑动窗口（S6 修复回归）==\n");
    test_unbounded_before_window_fills();
    test_window_caps_and_overwrites_oldest();
    test_window_keeps_recent_only();
    test_absorb_respects_window();
    test_clear_resets_head();
    test_percentile_bounded_memory();
    test_concurrent_add_safe();
    std::printf("ALL %zu-window TESTS PASSED\n", StatsCollector::kMaxSamples);
    return 0;
}
