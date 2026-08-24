// MotionController.hpp — A10 运动控制器
//
// PID：out = kp × err + ki 积分 + kd 微分（X/Y 独立）。
// ki/kd 默认 0 → 行为 = V1 纯 P（向后兼容）。
// 参数来自 RuntimeProfile（mouse.kp_x/y、ki_x/y、kd_x/y），不写死在控制逻辑。
#pragma once

namespace ttbox::core::aim {

struct MotionOutput {
    float out_x = 0.0f;  // 像素/帧（未缩放）
    float out_y = 0.0f;
};

class MotionController {
public:
    // err 单位 = 像素误差。kd 项用帧间误差差分近似微分。
    MotionOutput update(float err_x, float err_y, float kp_x, float kp_y,
                        float ki_x, float ki_y, float kd_x, float kd_y) {
        // 积分项（ki 比例系数；clamp ±500 防饱和/漂移）
        i_x_ += err_x * ki_x;
        i_y_ += err_y * ki_y;
        if (i_x_ > 500.0f) i_x_ = 500.0f;
        if (i_x_ < -500.0f) i_x_ = -500.0f;
        if (i_y_ > 500.0f) i_y_ = 500.0f;
        if (i_y_ < -500.0f) i_y_ = -500.0f;
        // 微分项（误差差分；首帧/重置后 prev 未初始化则不产生微分）
        const float d_x = inited_ ? (err_x - prev_x_) * kd_x : 0.0f;
        const float d_y = inited_ ? (err_y - prev_y_) * kd_y : 0.0f;
        inited_ = true;
        prev_x_ = err_x;
        prev_y_ = err_y;
        return MotionOutput{err_x * kp_x + i_x_ + d_x,
                            err_y * kp_y + i_y_ + d_y};
    }
    void reset() {
        i_x_ = i_y_ = 0.0f;
        prev_x_ = prev_y_ = 0.0f;
        inited_ = false;
    }

private:
    float i_x_ = 0.0f, i_y_ = 0.0f;
    float prev_x_ = 0.0f, prev_y_ = 0.0f;
    bool inited_ = false;
};

}  // namespace ttbox::core::aim
