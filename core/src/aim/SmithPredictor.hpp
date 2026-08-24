// SmithPredictor.hpp — 延迟在途量补偿。
// 只记录已经发出的移动，在 dead_ms 窗口内从当前误差中扣除，避免反馈延迟造成重复拉动。
#pragma once
#include <chrono>
#include <deque>
#include <cstdint>
#include <algorithm>
namespace ttbox::core::aim {
class SmithPredictor {
public:
    struct Summary { float dx=0.0f; float dy=0.0f; };
    void set_dead_ms(float ms) { dead_ms_ = std::max(0.0f, ms); }
    void reset() { moves_.clear(); }
    void record(uint64_t frame, float dx, float dy, uint64_t now_us) {
        prune(now_us); moves_.push_back({frame, dx, dy, now_us});
    }
    Summary predicted(uint64_t now_us) {
        prune(now_us); Summary s; for (const auto& m : moves_) { s.dx += m.dx; s.dy += m.dy; } return s;
    }
private:
    struct Move { uint64_t frame; float dx; float dy; uint64_t t_us; };
    void prune(uint64_t now_us) {
        const uint64_t window = static_cast<uint64_t>(dead_ms_ * 1000.0f);
        while (!moves_.empty() && now_us >= moves_.front().t_us && now_us - moves_.front().t_us > window) moves_.pop_front();
    }
    float dead_ms_ = 0.0f;
    std::deque<Move> moves_;
};
}
