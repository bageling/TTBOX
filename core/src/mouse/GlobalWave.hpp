// GlobalWave.hpp — 全局正弦扰动，BB 对标移植（2026-09-24）
//
// 小白理解：
//   给每一帧的最终鼠标位移叠一点点低频正弦摆动，模拟手部微动，避免轨迹太平直。
//
// 算法（对齐 bb-port/02 号 §4.1）：
//   raw      = sin(now/1000 × freq × 2π)          —— 同一相位同时驱动 X 与 Y
//   last_x   = last_x × smooth + raw × amp_x × (1 - smooth)
//   last_y   = last_y × smooth + raw × amp_y × (1 - smooth)
//   输出     = (mx + last_x, my + last_y)
//
// ★ 默认 enabled=false ⇒ 不跑即零输出，输出链与本模块加入前逐字节一致。
#pragma once

#include <cmath>
#include <cstdint>

#include "mouse/MouseTypes.hpp"

namespace ttbox::core::aim {

class GlobalWave {
public:
    // 就地叠加。x/y 单位是 count（量化前）。
    void apply(float* x, float* y, uint32_t now_ms, const GlobalWaveConfig& cfg) {
        if (!cfg.enabled) return;
        const float t_s = static_cast<float>(now_ms) * 0.001f;
        const float raw = std::sin(t_s * cfg.freq * 6.2831853f);
        const float s = cfg.smooth;
        last_x_ = last_x_ * s + raw * cfg.amp_x * (1.0f - s);
        last_y_ = last_y_ * s + raw * cfg.amp_y * (1.0f - s);
        *x += last_x_;
        *y += last_y_;
    }

    void reset() {
        last_x_ = 0.0f;
        last_y_ = 0.0f;
    }

private:
    float last_x_ = 0.0f;  // X 轴平滑后的扰动
    float last_y_ = 0.0f;  // Y 轴平滑后的扰动
};

}  // namespace ttbox::core::aim
