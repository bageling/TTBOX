// LeadPredictor.hpp — 两代提前量（横向 X 轴），BB 对标移植（2026-09-24）
//
// 小白理解：
//   敌人横向跑动时，自瞄如果永远"照着它现在的位置打"，就会一直落后。
//   提前量 = 在瞄准点上往前加一点偏移，让准星落在敌人"将要去的地方"。
//
// 两代是两套完全不同的思路，可各自独立开关（默认都关）：
//   · 一代 Lead1（帧窗口投票）：看最近 N 帧的横向输出方向，若明显朝一边走，
//     就把"平滑速度 × 强度 × 方向"作为偏移，保持 hold_ms。靠"投票"确认目标在跑。
//   · 二代 Lead2（积分累积）：直接对"瞄准点横向误差"做积分，误差一直在同一边
//     就会越积越大，形成偏移；死区内按 decay 衰减；Y 轴输出大时抑制（防斜拉抛物线）。
//
// 调用关系（对齐 bb-port/03 号 §0 数据流）：
//   ① 算误差之前：at.x += lead1 上一帧的 offset + lead2 本帧积分输出
//   ② 算出本帧横向输出 mx 之后：把 mx 喂给 lead1 做投票（产出下一帧的 offset）
//   —— 也就是一代天然有**一帧延迟**，二代同帧生效。
//
// ★ 只改 X 轴。Y 轴不加提前量（垂直受后坐力/跳跃影响，预测帮倒忙）。
// ★ 默认 enabled=false ⇒ 不跑即零输出，输出链与本模块加入前逐字节一致。
#pragma once

#include <cmath>
#include <cstdint>
#include <deque>

#include "mouse/MouseTypes.hpp"

namespace ttbox::core::aim {

// ---------------------------------------------------------------------------
// 一代：帧窗口投票法
// ---------------------------------------------------------------------------
class Lead1 {
public:
    struct Input {
        float move_x = 0.0f;    // 本帧自瞄横向输出（px/帧）
        float dtt = 0.0f;       // 准星到目标距离（px）
        uint32_t now_ms = 0;
        bool has_box = false;   // 是否带目标框信息（动态过滤 + 框位移死区需要）
        float box_cx = 0.0f;
        float box_cy = 0.0f;
        float box_w = 0.0f;
        float box_h = 0.0f;
    };

    // 返回"下一帧应叠加到瞄准点的 X 偏移"（px，右为正）。
    // 注意：返回的是**本次调用产出**的偏移，调用方应缓存到下一帧使用。
    float update(const Lead1Config& cfg, const Input& in);

    void reset();
    bool active() const { return active_; }
    float offset() const { return offset_; }

private:
    // 动态过滤阈值：目标框越小（越远）允许更小的单帧位移入窗（见 03 号 §1.3）
    static float dynamic_filter_min(const Lead1Config& cfg, float box_w, float box_h);

    std::deque<float> history_;      // 横向位移帧窗口
    float speed_ = 0.0f;             // 指数平滑后的主导速度
    bool active_ = false;            // 本帧是否处于"已激活"状态
    float offset_ = 0.0f;            // 当前偏移（px）
    int direction_ = 0;              // 主导方向（+1 右 / -1 左 / 0 未定）
    uint32_t hold_start_ = 0;        // 保持窗起点
    // ★ 收帧态默认 true（= 空闲时就该收帧）。BB 脚本里这个状态若默认 false，
    //   则第 5 步的进入条件 `active or collecting` 首帧起就恒不成立 ⇒ 一代永不激活。
    //   本实现按显然的意图置 true，reset() 后也回到"可收帧"状态。
    bool collecting_ = true;         // 是否在收帧
    uint32_t settle_start_ = 0;      // 进入距离后的冷却起点
    bool distance_ok_ = false;       // 冷却是否已过
    bool has_last_box_ = false;
    float last_box_x_ = 0.0f;
    float last_box_y_ = 0.0f;
    int last_activation_direction_ = 0;  // 上次激活方向（摆动熔断用）
    int oscillation_count_ = 0;          // 连续反向激活次数
};

// ---------------------------------------------------------------------------
// 二代：积分累积法
// ---------------------------------------------------------------------------
class Lead2 {
public:
    struct Input {
        bool has_target = false;
        float target_x = 0.0f;
        float target_y = 0.0f;
        float crosshair_x = 0.0f;
        float crosshair_y = 0.0f;
        float last_move_y = 0.0f;  // 上一帧纵向输出（px，Y 轴抑制用）
        uint32_t now_ms = 0;
    };

    // 返回本帧应叠加到瞄准点的 X 偏移（px，右为正）。同帧生效。
    float update(const Lead2Config& cfg, const Input& in);

    void reset();
    bool active() const { return active_; }
    float offset() const { return current_offset_; }

private:
    float integral_ = 0.0f;        // 积分器（px）
    float current_offset_ = 0.0f;  // 当前输出偏移
    bool active_ = false;
    bool holding_ = false;
    uint32_t hold_start_ = 0;
    uint32_t cooldown_start_ = 0;
    bool cooldown_active_ = false;
};

// ---------------------------------------------------------------------------
// 门面：两代合并，显式两阶段调用（调用方控制"喂 mx"的时机）
// ---------------------------------------------------------------------------
class LeadPredictor {
public:
    // 先跑二代（同帧生效），再叠加一代上一帧的 offset。返回本帧总 X 偏置（px）。
    float prepare_x_offset(float crosshair_x, float crosshair_y,
                           float target_x, float target_y, float last_move_y,
                           uint32_t now_ms, const Lead1Config& c1, const Lead2Config& c2) {
        const float l2 = l2_.update(c2, [&] {
            Lead2::Input in;
            in.has_target = true;
            in.target_x = target_x;
            in.target_y = target_y;
            in.crosshair_x = crosshair_x;
            in.crosshair_y = crosshair_y;
            in.last_move_y = last_move_y;
            in.now_ms = now_ms;
            return in;
        }());
        return l1_offset_ + l2;
    }

    // 算出本帧横向输出 mx 之后调用：喂给一代投票，产出"下一帧用的 offset"。
    // 同时把二代清掉（无目标时调用方传 has_target=false 给 prepare 即可）。
    void feed_move_x(float move_x, float dtt, uint32_t now_ms,
                     bool has_box, float box_cx, float box_cy, float box_w, float box_h,
                     const Lead1Config& c1) {
        Lead1::Input in;
        in.move_x = move_x;
        in.dtt = dtt;
        in.now_ms = now_ms;
        in.has_box = has_box;
        in.box_cx = box_cx;
        in.box_cy = box_cy;
        in.box_w = box_w;
        in.box_h = box_h;
        l1_offset_ = l1_.update(c1, in);
    }

    // 无目标时把二代清零（一代走自己的距离熔断）
    void clear_target(uint32_t now_ms, const Lead2Config& c2) {
        Lead2::Input in;
        in.has_target = false;
        in.now_ms = now_ms;
        l2_.update(c2, in);
    }

    void reset() {
        l1_.reset();
        l2_.reset();
        l1_offset_ = 0.0f;
    }

    Lead1& lead1() { return l1_; }
    Lead2& lead2() { return l2_; }
    float lead1_offset() const { return l1_offset_; }

private:
    Lead1 l1_;
    Lead2 l2_;
    float l1_offset_ = 0.0f;  // 一代产出的偏移，缓一帧用
};

// ==================== Lead1 实现 ====================

inline void Lead1::reset() {
    history_.clear();
    speed_ = 0.0f;
    active_ = false;
    offset_ = 0.0f;
    direction_ = 0;
    hold_start_ = 0;
    collecting_ = true;   // 复位后回到"可收帧"状态
    settle_start_ = 0;
    distance_ok_ = false;
    has_last_box_ = false;
    last_box_x_ = 0.0f;
    last_box_y_ = 0.0f;
    last_activation_direction_ = 0;
    oscillation_count_ = 0;
}

inline float Lead1::dynamic_filter_min(const Lead1Config& cfg, float box_w, float box_h) {
    const float area = box_w * box_h;
    const float mid = cfg.filter_box_mid;
    if (mid <= 0.0f) return cfg.filter_base;
    if (area <= mid) {
        const float ratio = cfg.filter_min_ratio + (1.0f - cfg.filter_min_ratio) * (area / mid);
        return cfg.filter_base * ratio;
    }
    float ratio = 1.0f + (cfg.filter_max_ratio - 1.0f) * ((area - mid) / (mid * 2.0f));
    if (ratio > cfg.filter_max_ratio) ratio = cfg.filter_max_ratio;
    return cfg.filter_base * ratio;
}

inline float Lead1::update(const Lead1Config& cfg, const Input& in) {
    // [0] 总开关：关掉时清零，避免残留 offset
    if (!cfg.enabled) {
        if (active_) reset();
        return 0.0f;
    }
    const int win = (cfg.frames > 0) ? cfg.frames : 1;

    // [1] 距离超限熔断
    if (in.dtt > cfg.activation_distance) {
        reset();
        return 0.0f;
    }

    // [2] 进入距离后的冷却（settle）
    if (!distance_ok_) {
        if (settle_start_ == 0) {
            settle_start_ = in.now_ms;
            history_.clear();
        }
        if (static_cast<float>(in.now_ms - settle_start_) < cfg.settle_ms) {
            return offset_;  // 冷却中，维持上次 offset
        }
        distance_ok_ = true;
    }

    // [3] 保持窗
    if (active_ && hold_start_ != 0) {
        if (static_cast<float>(in.now_ms - hold_start_) < cfg.hold_ms) {
            return offset_;
        }
        hold_start_ = 0;
        collecting_ = true;
        history_.clear();
    }

    // [4] 目标框位移死区
    // ★ 与 BB 脚本的差异：BB 把 box_moved 存在全局状态里，框信息缺失时沿用旧值，
    //   框从未给过就恒为 0 ⇒ 恒 `0 < dead_zone` 直接 return，一代形同关闭。
    //   本实现改为「只在带框信息时才做死区判定」，不带框则跳过（调用方一律带框，
    //   所以实际行为一致，只是不会因缺信息而整块静默失效）。
    if (in.has_box) {
        float box_moved = 0.0f;
        if (has_last_box_) {
            const float dx = in.box_cx - last_box_x_;
            const float dy = in.box_cy - last_box_y_;
            box_moved = std::sqrt(dx * dx + dy * dy);
        }
        last_box_x_ = in.box_cx;
        last_box_y_ = in.box_cy;
        has_last_box_ = true;
        if (box_moved < cfg.dead_zone) return offset_;
    }

    // [5] 收帧（带动态过滤）
    if (active_ || collecting_) {
        const float filter_min = in.has_box ? dynamic_filter_min(cfg, in.box_w, in.box_h)
                                            : cfg.filter_base;
        if (std::fabs(in.move_x) >= filter_min) history_.push_back(in.move_x);
        while (static_cast<int>(history_.size()) > win) history_.pop_front();
        if (static_cast<int>(history_.size()) < win) return offset_;

        // [6] 投票统计
        int right_count = 0, left_count = 0;
        float right_sum = 0.0f, left_sum = 0.0f;
        for (const float v : history_) {
            if (v > 0.0f) {
                ++right_count;
                right_sum += v;
            } else if (v < 0.0f) {
                ++left_count;
                left_sum += -v;
            }
        }
        const int total = static_cast<int>(history_.size());
        const int max_count = (right_count > left_count) ? right_count : left_count;
        const float direction_ratio = (total > 0) ? (100.0f * max_count / total) : 0.0f;

        int dir = 0;
        float main_sum = 0.0f, other_sum = 0.0f;
        if (right_count > left_count) {
            dir = +1;
            main_sum = right_sum;
            other_sum = left_sum;
        } else if (left_count > right_count) {
            dir = -1;
            main_sum = left_sum;
            other_sum = right_sum;
        }

        // [7] 激活判定
        const bool ok = (dir != 0) && (direction_ratio >= cfg.direction_ratio) &&
                        (main_sum > other_sum * cfg.displacement_ratio) &&
                        (main_sum >= cfg.displacement_min) &&
                        (main_sum <= cfg.displacement_max);

        if (ok) {
            // [8] 激活
            const float avg_speed = main_sum / static_cast<float>(max_count > 0 ? max_count : 1);
            const float s = cfg.smooth;
            speed_ = speed_ * s + avg_speed * (1.0f - s);
            active_ = true;
            direction_ = dir;
            offset_ = speed_ * cfg.strength * static_cast<float>(dir);
            hold_start_ = in.now_ms;
            collecting_ = false;

            // 方向来回摆动熔断
            if (last_activation_direction_ != 0 && last_activation_direction_ != dir) {
                ++oscillation_count_;
            } else {
                oscillation_count_ = 0;
            }
            last_activation_direction_ = dir;
            if (oscillation_count_ >= cfg.oscillation_cancel) {
                active_ = false;
                offset_ = 0.0f;
                oscillation_count_ = 0;
                last_activation_direction_ = 0;
                collecting_ = true;
                history_.clear();
            }
            return offset_;
        }

        // [9] 取消
        // ★ 与 BB 脚本的差异：BB 在普通取消分支不重置 collecting，导致一代一旦
        //   取消就**永久停摆**（下次收帧条件 `active or collecting` 两边都不成立）。
        //   本实现统一置 collecting=true 并清窗，下个窗口可重新判定 —— 这正是
        //   "摆动熔断"分支的写法，也是显然的意图（否则开启后一次取消就再无输出）。
        active_ = false;
        offset_ = 0.0f;
        hold_start_ = 0;
        collecting_ = true;
        history_.clear();
        return 0.0f;
    }

    return offset_;
}

// ==================== Lead2 实现 ====================

inline void Lead2::reset() {
    integral_ = 0.0f;
    current_offset_ = 0.0f;
    active_ = false;
    holding_ = false;
    hold_start_ = 0;
    cooldown_start_ = 0;
    cooldown_active_ = false;
}

inline float Lead2::update(const Lead2Config& cfg, const Input& in) {
    // [0] 总开关
    if (!cfg.enabled) {
        reset();
        return 0.0f;
    }

    // [1] Y 轴抑制（垂直输出越大，横向提前量越小；平方衰减）
    float y_scale = 1.0f;
    if (cfg.y_suppress_enabled) {
        const float my_abs = std::fabs(in.last_move_y);
        if (my_abs >= cfg.y_suppress_max) {
            y_scale = 0.0f;
        } else if (my_abs > cfg.y_suppress_min) {
            y_scale = 1.0f - (my_abs - cfg.y_suppress_min) / (cfg.y_suppress_max - cfg.y_suppress_min);
        }
        const float cur_max = cfg.max_offset * y_scale;
        if (integral_ > cur_max) integral_ = cur_max;
        if (integral_ < -cur_max) integral_ = -cur_max;
    }

    // [2] 无目标
    if (!in.has_target) {
        reset();
        return 0.0f;
    }

    // [3] 误差与距离
    const float error_x = (in.target_x + integral_) - in.crosshair_x;
    const float error_y = in.target_y - in.crosshair_y;
    const float dist = std::sqrt(error_x * error_x + error_y * error_y);

    // [4] 保持窗
    if (holding_) {
        if (static_cast<float>(in.now_ms - hold_start_) < cfg.hold_ms) return current_offset_;
        holding_ = false;
    }

    // [5] 超出激活距离：全清
    if (dist > cfg.activation_distance) {
        reset();
        return 0.0f;
    }

    // [6] 进入距离后的冷却
    if (!cooldown_active_) {
        if (cooldown_start_ == 0) cooldown_start_ = in.now_ms;
        if (static_cast<float>(in.now_ms - cooldown_start_) < cfg.cooldown_ms) {
            integral_ = 0.0f;
            current_offset_ = 0.0f;
            return 0.0f;
        }
        cooldown_active_ = true;
    }

    // [7] 积分 / 衰减（注意 yScale 是平方）
    if (std::fabs(error_x) <= cfg.dead_zone) {
        integral_ *= cfg.decay;
    } else {
        integral_ += error_x * cfg.gain * y_scale * y_scale;
    }
    if (integral_ > cfg.max_offset) integral_ = cfg.max_offset;
    if (integral_ < -cfg.max_offset) integral_ = -cfg.max_offset;

    // [8] 输出
    current_offset_ = integral_;
    active_ = true;
    holding_ = true;
    hold_start_ = in.now_ms;
    return current_offset_;
}

}  // namespace ttbox::core::aim
