// AntiOvershoot.hpp — 抗过冲状态机，BB 对标移植（2026-09-24）
//
// 小白理解：
//   自瞄快到位时容易"冲过头"来回抖。抗过冲 = 离目标越近，把每帧位移按百分比削掉一些，
//   削够次数就停手（本轮锁定不再干预）。等准星真的飘远了，过一会儿再允许重来一轮。
//
// 状态机（对齐 bb-port/02 号 §5.1）：
//   ① 距离 > outer_distance（="在圈外/过冲"）：记开始时刻；持续超 reset_cooldown 则整轮复位。
//   ② 本轮已完成（cycle_done）→ 不再衰减。
//   ③ dtt ≤ inner_distance 且内圈衰减帧数未满 → 位移 ×(1 - inner_strength%)，帧数 +1。
//   ④ dtt ≤ outer_distance 且外圈衰减帧数未满 → 位移 ×(1 - outer_strength%)，帧数 +1。
//   内圈与外圈**都**跑满 → cycle_done。
//
// ★ 默认 enabled=false ⇒ 不跑即零输出，输出链与本模块加入前逐字节一致。
#pragma once

#include <cstdint>

#include "mouse/MouseTypes.hpp"

namespace ttbox::core::aim {

class AntiOvershoot {
public:
    // 就地衰减。x/y 单位是 count（量化前）。
    // dtt：准星到目标距离（px）。返回是否本轮已完成（供状态 API / 日志用）。
    bool apply(float* x, float* y, float dtt, uint32_t now_ms, const AntiOvershootConfig& cfg) {
        if (!cfg.enabled) return false;

        // ① 越界检测
        if (dtt > cfg.outer_distance) {
            if (!has_out_of_range_) {
                has_out_of_range_ = true;
                out_of_range_start_ = now_ms;
            } else if (static_cast<float>(now_ms - out_of_range_start_) >= cfg.reset_cooldown_ms) {
                reset();
            }
            return cycle_done_;
        }
        has_out_of_range_ = false;

        // ② 本轮已完成
        if (cycle_done_) return true;

        // ③ 内圈
        if (dtt <= cfg.inner_distance && inner_frames_ < cfg.inner_frames) {
            const float p = 1.0f - cfg.inner_strength / 100.0f;
            *x *= p;
            *y *= p;
            ++inner_frames_;
            if (inner_frames_ >= cfg.inner_frames && outer_frames_ >= cfg.outer_frames) {
                cycle_done_ = true;
            }
            return cycle_done_;
        }

        // ④ 外圈
        if (dtt <= cfg.outer_distance && outer_frames_ < cfg.outer_frames) {
            const float p = 1.0f - cfg.outer_strength / 100.0f;
            *x *= p;
            *y *= p;
            ++outer_frames_;
            if (outer_frames_ >= cfg.outer_frames && inner_frames_ >= cfg.inner_frames) {
                cycle_done_ = true;
            }
            return cycle_done_;
        }

        return cycle_done_;
    }

    void reset() {
        outer_frames_ = 0;
        inner_frames_ = 0;
        cycle_done_ = false;
        has_out_of_range_ = false;
        out_of_range_start_ = 0;
    }

    bool cycle_done() const { return cycle_done_; }
    int outer_frames() const { return outer_frames_; }
    int inner_frames() const { return inner_frames_; }

private:
    int outer_frames_ = 0;              // 外圈已衰减帧数
    int inner_frames_ = 0;              // 内圈已衰减帧数
    bool cycle_done_ = false;           // 本轮是否已完成
    bool has_out_of_range_ = false;     // 是否处于"越界"状态
    uint32_t out_of_range_start_ = 0;   // 越界开始时刻（ms）
};

}  // namespace ttbox::core::aim
