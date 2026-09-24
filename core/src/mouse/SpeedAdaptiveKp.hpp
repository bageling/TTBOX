// SpeedAdaptiveKp.hpp — 速度自适应 Kp，BB 对标移植（2026-09-24）
//
// 小白理解：
//   目标在跑的时候，自瞄要"跟得紧"（Kp 大）；目标站着不动时，Kp 大的话准星会来回抖，
//   所以要把 Kp 调小。判据就是"最近几帧目标的平均位移"。
//
// 算法（对齐 bb-port/02 号 §6.1）：
//   ① 每帧把目标位置 (x,y) 压入滑动窗口，窗口长度 = frames。
//   ② 窗口内不足 2 帧 → 返回 1.0（不干预）。
//   ③ 平均每帧位移 = 窗口首尾距离 / 窗口帧数。
//   ④ ≥ threshold → move_mult（默认 1.5）；否则 static_mult（默认 0.8）。
//
// ★ 输出是**乘子**：AimThread 在每帧 PID 计算前临时乘到 kp 上，算完还原。
// ★ 默认 enabled=false ⇒ 返回 1.0，输出链与本模块加入前逐字节一致。
#pragma once

#include <cmath>
#include <cstdint>
#include <deque>

#include "mouse/MouseTypes.hpp"

namespace ttbox::core::aim {

class SpeedAdaptiveKp {
public:
    // 返回 Kp 乘子。has_target=false 时返回 1.0（不干预，窗口保留）。
    float multiplier(const SpeedAdaptiveKpConfig& cfg, bool has_target, float target_x, float target_y) {
        if (!cfg.enabled || !has_target) return 1.0f;

        hist_.push_back({target_x, target_y});
        const int win = (cfg.frames > 0) ? cfg.frames : 1;
        while (static_cast<int>(hist_.size()) > win) hist_.pop_front();
        if (hist_.size() < 2) return 1.0f;

        const auto& first = hist_.front();
        const auto& last = hist_.back();
        const float dx = last.first - first.first;
        const float dy = last.second - first.second;
        const float total_dist = std::sqrt(dx * dx + dy * dy);
        const float avg_speed = total_dist / static_cast<float>(hist_.size());  // px/帧

        return (avg_speed >= cfg.threshold) ? cfg.move_mult : cfg.static_mult;
    }

    void reset() { hist_.clear(); }

private:
    std::deque<std::pair<float, float>> hist_;  // 目标位置滑动窗口
};

}  // namespace ttbox::core::aim
