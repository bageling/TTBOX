// TriggerController.hpp — 自动扳机（两套状态机：v7.26 + BB 扳机 2.0）
//
// 对标来源：竞品 BB_927 内部会员版 `13_auto_trigger.lua` / `14_bb_trigger.lua`，
// 算法结构与标定值见 `.workbuddy/artifacts/bb-port/01-选靶与扳机.md` §2/§3。
// 铁律：只取结构与标定值，实现全部自己写，不搬运一行 Lua。
//
// 设计要点
//   · **决策与注入分离**：本模块只产出 TriggerCmd（要不要开火、点哪个键、点几次），
//     真正的鼠标点击由 AimThread 拿到命令后调 OutputBackend::mouse_click 注入。
//     这样扳机逻辑可以脱离硬件单测。
//   · **两套独立**：trigger（v7.26）与 trigger2（BB 2.0）各有自己的状态，可分别开启；
//     都没开时 update() 立刻返回空命令 ⇒ 输出链与本模块加入前逐字节一致。
//   · **外部时钟**：全部时间判断用调用方传入的 now_ms，模块内不自取时间（可测）。
//   · **可播种随机**：fire_random 抖动用内部 xorshift，seed 可指定 ⇒ 单测确定性。
//   · 依赖外部的两个判定（保持本模块纯净）：
//       center_covered   —— 中心点是否被任一检测框覆盖（crosshair_check 用）
//       stop_detect_found—— 中心是否出现指定准星颜色（BB2 急停检测用，颜色识别在采集侧）
//
// ★ 键位一律是**位掩码**，与 MouseProfile.aim_hotkey 同域：
//   1=left 2=right 4=middle 8=back 16=forward；BB 编号 1/2/3/5/6 → 0x01/0x02/0x04/0x08/0x10。
//
// TTBOX 文件说明
//
// 文件：TriggerController.hpp
//
// 作用：
//   根据"有没有锁定目标、目标离准星多远、热键按没按"决定要不要自动开火。
//
// 小白理解：
//   它像一个副手：你说"按住侧键、目标进圈了"，它就帮你扣扳机；目标跑了就松手。
//   它自己不碰鼠标，只张嘴说"现在该开枪"，真正扣扳机的是 AimThread。
//
// 注意：
//   两套算法（trigger / trigger2）是竞品里并存的两代，默认都关着，
//   不开就等于这个文件不存在。
#pragma once

#include <cmath>
#include <cstdint>

#include "mouse/MouseTypes.hpp"

namespace ttbox::core::aim {

// 一帧的扳机决策（由 AimThread 消费）
struct TriggerCmd {
    bool fire = false;             // 本帧是否开火
    uint8_t button = 0x00;         // 要点的键位掩码（0 = 不点击）
    int count = 1;                 // 连点次数
    float press_duration_ms = 0.0f; // 按下保持时长
    // 压枪联动（首枪通知压枪模块，本帧有效）
    bool recoil_simple = false;
    bool recoil_adv = false;
    bool recoil_crosshair = false;
    // 压枪联动偏移（px）：目标框高度 × trigger.y_offset，由 AimThread 叠到瞄准点上。
    // 为什么在这里算：扳机才知道「这一枪打没打」，压枪模块只在扳机开火后才需要这份偏移。
    float recoil_y_offset_px = 0.0f;
    // 这一枪是哪套扳机打的：移动节流（trigger2.move_throttle_frames）只对 2.0 生效，
    // 两套共用同一个 TriggerCmd，不标来源的话会误伤 v7.26。
    bool fired_by_trigger2 = false;

    bool any() const { return fire; }
};

// 一帧的输入（全部由调用方准备好，模块不碰硬件）
struct TriggerInput {
    uint32_t now_ms = 0;            // 当前毫秒时钟
    float dt_ms = 16.667f;          // 距上一帧的间隔（ms）
    bool has_target = false;        // 本帧是否有锁定目标
    float dtt_px = 0.0f;            // 锁定目标到准星的距离（px）
    float target_conf = 0.0f;       // 锁定目标置信度
    uint16_t hotkey_bits = 0;       // 当前物理按键位图（位掩码，见文件头）
    bool center_covered = true;     // 准星中心是否被任一检测框覆盖
    bool stop_detect_found = true;  // 中心是否命中指定准星颜色（急停检测）
    float target_height_px = 0.0f;  // 锁定目标框高度（px，压枪联动偏移换算用）
};

// 自动扳机 v7.26（按住长键 + 点按激活 → 连发 / 计数）
class AutoTrigger {
public:
    explicit AutoTrigger(uint32_t seed = 0x12345678u) : rng_(seed ? seed : 1u) {}

    void reset() {
        activated_ = false;
        completed_ = false;
        aim_range_active_ = false;
        shot_count_ = 0;
        stable_count_ = 0;
        tap_prev_ = false;
        has_fire_ = false;
        first_delayed_ = false;
        last_fire_ms_ = 0;
        rifle_last_ms_ = 0;
    }

    // 返回本帧是否开火、点什么键、点几次
    TriggerCmd update(const TriggerConfig& cfg, const TriggerInput& in) {
        TriggerCmd cmd;
        if (!cfg.enabled) {
            reset();
            return cmd;
        }
        // 长按组合键：key2==0 视为常满足
        const bool long_down = key_down(in.hotkey_bits, cfg.key1) && key_down(in.hotkey_bits, cfg.key2);
        if (!long_down) {
            reset();
            return cmd;
        }
        // ---- 激活（点按边沿；key3==0 = 常满足）----
        if (completed_ && cfg.key3 == 0) {
            activated_ = true;
            completed_ = false;
            shot_count_ = 0;
            first_delayed_ = false;
        } else {
            const bool tap_now = (cfg.key3 == 0) || key_down(in.hotkey_bits, cfg.key3);
            if (tap_now && !tap_prev_) {
                activated_ = true;
                completed_ = false;
                shot_count_ = 0;
                first_delayed_ = false;
            }
            tap_prev_ = tap_now;
        }
        if (!activated_) return cmd;
        // 附带自瞄的置信度门（trigger.aim_confidence）：随扳机一起开的那份自瞄，
        // 只跟把握到这个数以上的目标。0 或负 = 不设这道门（沿用旧行为）。
        aim_range_active_ = cfg.with_aim &&
                            (cfg.aim_confidence <= 0.0f || in.target_conf >= cfg.aim_confidence);

        // ---- 计数模式打满即收工 ----
        if (!cfg.rifle_mode && shot_count_ >= cfg.click_count) {
            activated_ = false;
            completed_ = true;
            return cmd;
        }

        // ---- 开枪门（任一不过则清零稳定计数）----
        if (!in.has_target) {
            stable_count_ = 0;
            return cmd;
        }
        if (in.target_conf < cfg.confidence) {
            stable_count_ = 0;
            return cmd;
        }
        if (in.dtt_px > cfg.dist_threshold) {
            stable_count_ = 0;
            return cmd;
        }
        if (cfg.crosshair_check && !in.center_covered) {
            stable_count_ = 0;
            return cmd;
        }
        stable_count_++;

        if (cfg.rifle_mode) {
            // 连发：每 rifle_interval ms 一发
            if (!has_fire_ || elapsed(in.now_ms, rifle_last_ms_) >= cfg.rifle_interval) {
                rifle_last_ms_ = in.now_ms;
                return make_fire(cfg, in, cmd);
            }
            return cmd;
        }
        // 计数模式：需先稳定 stability_frames 帧，再按 fire_delay ± random 开火
        if (stable_count_ < cfg.stability_frames) return cmd;
        const float jitter = cfg.fire_random > 0.0f ? rand_pm(cfg.fire_random) : 0.0f;
        const float delay = std::fmax(1.0f, cfg.fire_delay + jitter);
        if (has_fire_ && elapsed(in.now_ms, last_fire_ms_) < delay) return cmd;
        if (shot_count_ == 0 && !first_delayed_) {
            // 首枪额外延迟（对齐 BB first_delay_min~max）
            const float lo = cfg.first_delay_min;
            const float hi = cfg.first_delay_max > lo ? cfg.first_delay_max : lo;
            const float hold = lo + rand_unit() * (hi - lo);
            if (elapsed(in.now_ms, last_fire_ms_) < hold) return cmd;
            first_delayed_ = true;
        }
        return make_fire(cfg, in, cmd);
    }

    // 是否处于激活态（供 AimThread 决定检测置信度 / 动态范围）
    bool activated() const { return activated_; }
    bool aim_range_active() const { return aim_range_active_ && activated_; }
    int shot_count() const { return shot_count_; }

public:
    // 位掩码判定（两个扳机类共用；mask == 0 表示「该键不参与判定」⇒ 恒真）
    static bool key_down(uint16_t bits, uint8_t mask) {
        return mask == 0 || (bits & static_cast<uint16_t>(mask)) != 0;
    }

private:
    static uint32_t elapsed(uint32_t now, uint32_t then) {
        return now >= then ? (now - then) : (then - now);
    }
    // [0,1) 均匀
    float rand_unit() {
        rng_ ^= rng_ << 13;
        rng_ ^= rng_ >> 17;
        rng_ ^= rng_ << 5;
        return static_cast<float>(rng_ & 0xFFFFFFu) / static_cast<float>(0x1000000u);
    }
    // [-amp, +amp]
    float rand_pm(float amp) { return (rand_unit() * 2.0f - 1.0f) * amp; }

    TriggerCmd make_fire(const TriggerConfig& cfg, const TriggerInput& in, TriggerCmd cmd) {
        cmd.fire = true;
        cmd.button = cfg.click_key;
        cmd.count = 1;
        cmd.press_duration_ms = cfg.press_duration;
        if (shot_count_ == 0 && cfg.recoil_enabled) {
            // 首枪通知压枪（BB 由扳机触发压枪）
            cmd.recoil_simple = true;
            cmd.recoil_adv = true;
            cmd.recoil_crosshair = true;
        }
        // 联动偏移（trigger.y_offset）：压枪量按「目标框高度 × 比例」换算，
        // 目标越大压得越多 —— 远处小目标自动少压。比例 0 时不产生偏移。
        if (cfg.recoil_enabled && cfg.y_offset > 0.0f) {
            cmd.recoil_y_offset_px = in.target_height_px * cfg.y_offset;
        }
        shot_count_++;
        last_fire_ms_ = in.now_ms;
        has_fire_ = true;
        return cmd;
    }

    bool activated_ = false;
    bool completed_ = false;
    bool aim_range_active_ = false;
    int shot_count_ = 0;
    int stable_count_ = 0;
    bool tap_prev_ = false;
    bool has_fire_ = false;
    bool first_delayed_ = false;
    uint32_t last_fire_ms_ = 0;
    uint32_t rifle_last_ms_ = 0;
    uint32_t rng_;
};

// BB 扳机 2.0（组合键按住即连发；首枪过后不限距离）
class AutoTrigger2 {
public:
    explicit AutoTrigger2(uint32_t seed = 0x87654321u) : rng_(seed ? seed : 1u) {}

    void reset() {
        activated_ = false;
        fired_ = false;
        has_first_enter_ = false;
        has_lost_ = false;
        has_fire_ = false;
        precision_count_ = 0;
        first_enter_ms_ = 0;
        last_fire_ms_ = 0;
        next_fire_ms_ = 0;
        lost_ms_ = 0;
    }
    // 只复位"首枪态"（目标丢失超时后重新走首枪门）
    void reset_first_shot() {
        fired_ = false;
        has_first_enter_ = false;
        has_lost_ = false;
        has_fire_ = false;
        precision_count_ = 0;
        next_fire_ms_ = 0;
    }

    TriggerCmd update(const Trigger2Config& cfg, const TriggerInput& in) {
        TriggerCmd cmd;
        if (!cfg.enabled) {
            reset();
            return cmd;
        }
        const bool combo = AutoTrigger::key_down(in.hotkey_bits, cfg.key1) &&
                           AutoTrigger::key_down(in.hotkey_bits, cfg.key2);
        if (!combo) {
            reset();
            return cmd;
        }
        activated_ = true;
        if (cfg.with_crosshair) cmd.recoil_crosshair = true;

        // 急停检测：中心没有准星颜色则本帧禁射（打狙急停）
        if (cfg.stop_detect_enabled && !in.stop_detect_found) return cmd;

        if (!fired_) {
            if (!in.has_target) {
                precision_count_ = 0;
                return cmd;
            }
            if (cfg.precision_enabled) {
                precision_count_ = (in.dtt_px < cfg.precision_range) ? precision_count_ + 1 : 0;
                if (precision_count_ < cfg.precision_frames) return cmd;
            }
            if (in.dtt_px < cfg.first_err) {
                if (!has_first_enter_) {
                    first_enter_ms_ = in.now_ms;
                    has_first_enter_ = true;
                }
                if (static_cast<float>(elapsed(in.now_ms, first_enter_ms_)) >= cfg.first_delay) {
                    return make_fire(cfg, in, cmd);
                }
            }
            return cmd;
        }

        // 已开首枪：有锁定就按间隔继续，不再校验距离
        if (in.has_target) {
            if (!has_fire_ || in.now_ms >= next_fire_ms_) return make_fire(cfg, in, cmd);
            return cmd;
        }
        // 目标丢失：超 retarget_reset_ms 后重置首枪态
        if (cfg.retarget_reset_ms > 0.0f) {
            if (!has_lost_) {
                lost_ms_ = in.now_ms;
                has_lost_ = true;
            } else if (static_cast<float>(elapsed(in.now_ms, lost_ms_)) >= cfg.retarget_reset_ms) {
                reset_first_shot();
            }
        }
        return cmd;
    }

    bool activated() const { return activated_; }
    bool fired() const { return fired_; }
    int precision_count() const { return precision_count_; }

private:
    static uint32_t elapsed(uint32_t now, uint32_t then) {
        return now >= then ? (now - then) : (then - now);
    }
    float rand_unit() {
        rng_ ^= rng_ << 13;
        rng_ ^= rng_ >> 17;
        rng_ ^= rng_ << 5;
        return static_cast<float>(rng_ & 0xFFFFFFu) / static_cast<float>(0x1000000u);
    }
    float rand_pm(float amp) { return (rand_unit() * 2.0f - 1.0f) * amp; }

    TriggerCmd make_fire(const Trigger2Config& cfg, const TriggerInput& in, TriggerCmd cmd) {
        cmd.fire = true;
        cmd.button = cfg.fire_button;
        cmd.count = cfg.fire_count > 1 ? cfg.fire_count : 1;
        cmd.press_duration_ms = cfg.press_duration;
        cmd.fired_by_trigger2 = true;
        if (cfg.with_simple_recoil) cmd.recoil_simple = true;
        if (cfg.with_adv_recoil) cmd.recoil_adv = true;
        fired_ = true;
        last_fire_ms_ = in.now_ms;
        // 连发间隔单位是帧，按 600Hz 折算成 ms（对齐 BB 宿主的 600Hz 刷新）
        const float jitter = cfg.fire_random > 0.0f ? rand_pm(cfg.fire_random) : 0.0f;
        const float frames = std::fmax(1.0f, cfg.fire_interval + jitter);
        next_fire_ms_ = in.now_ms + static_cast<uint32_t>(frames * (1000.0f / 600.0f) + 0.5f);
        has_fire_ = true;
        return cmd;
    }

    bool activated_ = false;
    bool fired_ = false;
    bool has_first_enter_ = false;
    bool has_lost_ = false;
    bool has_fire_ = false;
    int precision_count_ = 0;
    uint32_t first_enter_ms_ = 0;
    uint32_t last_fire_ms_ = 0;
    uint32_t next_fire_ms_ = 0;
    uint32_t lost_ms_ = 0;
    uint32_t rng_;
};

// 两套扳机的统一门面：一次 update 跑两套，合并出本帧唯一的开火决策。
// 两套同时激活时以 v7.26 优先（BB 侧只补压枪联动标志），避免一帧点两次不同键。
class TriggerController {
public:
    TriggerCmd update(const MouseProfile& mp, const TriggerInput& in) {
        TriggerCmd a = auto1_.update(mp.trigger, in);
        TriggerCmd b = auto2_.update(mp.trigger2, in);
        if (a.fire) {
            if (b.recoil_simple) a.recoil_simple = true;
            if (b.recoil_adv) a.recoil_adv = true;
            if (b.recoil_crosshair) a.recoil_crosshair = true;
            return a;
        }
        return b;
    }
    void reset() {
        auto1_.reset();
        auto2_.reset();
    }

    const AutoTrigger& auto_trigger() const { return auto1_; }
    const AutoTrigger2& auto_trigger2() const { return auto2_; }

private:
    AutoTrigger auto1_;
    AutoTrigger2 auto2_;
};

}  // namespace ttbox::core::aim
