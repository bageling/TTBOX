// BezierTrajectory.hpp — 贝塞尔移动轨迹（把一次位移拆成多帧点列）
//
// 来源：BB_927 内部会员版 `algo/bezier.lua` 的两代算法，忠实移植。
// 与 TTBOX 已有能力的区别（不要混用）：
//   - PullCurve        ：逐帧在 Y 上附加一个弧线/抖动偏移量，不拆帧。
//   - PersonalTrajectory：Fitts 时长 + 速度包络，整形的是"恒定 PID 输出"。
//   - 本模块           ：把一整段位移 (dx,dy) 拆成 N 段子位移，调用方逐帧发送，
//                        S 形/弧形路径让人眼看起来像一次手部移动而不是瞬移。
//
// 两代算法：
//   一代 path1：固定控制点比例的三次贝塞尔，起点(0,0) 终点(dx,dy)，
//               控制点取终点的 30%/10% 与 70%/50%，分段数随距离自适应。
//   二代 path2：极坐标式随机弧线，方向(上下左右) 与弓高比例 peak 在
//               「生效键由松开变按下」那一刻抽一次，一次瞄准过程保持不变。
//
// 关键性质：子位移累加精确等于总位移（被 min_move 过滤掉的段会被下一段吸收，
//           所以不会出现"走了半天没走到位"）。
#pragma once

#include <cmath>
#include <cstdint>

#include "mouse/MouseTypes.hpp"

namespace ttbox::core::aim {

// 一次移动的点列。固定容量，零堆分配（在实时主循环里调用）。
struct BezierPath {
    static constexpr int kMaxSteps = 32;
    struct Step {
        float dx = 0.0f;
        float dy = 0.0f;
        float t = 0.0f;
    };
    int count = 0;     // 有效点数量
    bool curved = false;  // 二代：是否真的走了弧线（false = 退化成直线）
    Step steps[kMaxSteps];
};

class BezierTrajectory {
public:
    void configure(const BezierTrajectoryConfig& cfg) { cfg_ = cfg; }
    const BezierTrajectoryConfig& config() const { return cfg_; }

    // 一代：固定控制点三次贝塞尔。返回相对位移点列（不含起点）。
    BezierPath path1(float dx, float dy) const {
        BezierPath out;
        const float dist = std::sqrt(dx * dx + dy * dy);
        int seg = static_cast<int>(std::floor(cfg_.segments * std::fmin(1.5f, dist / 100.0f)));
        if (seg < 5) seg = 5;
        // 段数钳在容量内（而不是循环截断）：截断会让路径走不到终点，
        // 位移被吃掉一截。钳段数则保证最后一段 t=1 精确落在 (dx,dy)。
        if (seg > BezierPath::kMaxSteps) seg = BezierPath::kMaxSteps;

        const Point p0{0.0f, 0.0f};
        const Point p1{dx * 0.3f, dy * 0.1f};
        const Point p2{dx * 0.7f, dy * 0.5f};
        const Point p3{dx, dy};

        float px = 0.0f, py = 0.0f;  // 曲线上的当前位置（无条件推进）
        float lx = 0.0f, ly = 0.0f;  // 最后一次「真正发出」的位置（只有发段才推进）
        for (int i = 1; i <= seg && out.count < BezierPath::kMaxSteps; ++i) {
            const float t = static_cast<float>(i) / static_cast<float>(seg);
            const Point b = cubic(p0, p1, p2, p3, t);
            const float ddx = b.x - px, ddy = b.y - py;
            // px/py 无条件推进：被过滤掉的位移会被下一段吸收，不会累积漂移。
            px = b.x;
            py = b.y;
            if (std::fabs(ddx) >= cfg_.min_move || std::fabs(ddy) >= cfg_.min_move) {
                BezierPath::Step& s = out.steps[out.count++];
                s.dx = b.x - lx;
                s.dy = b.y - ly;
                s.t = t;
                lx = b.x;
                ly = b.y;
            }
        }
        // 收尾段：保证子位移累加精确等于总位移。
        // ★ 刻意偏离 BB 原版：原版若 min_move 把所有段都过滤掉，会返回空点列，
        //   位移整段丢失、鼠标一动不动且不报错（BB 的"A 类静默失效"）。
        //   残差按「最后发出的位置」算，所以全被过滤时会补发整段位移。
        //   正常参数下残差为 0，不会多出段。
        const float rdx = dx - lx, rdy = dy - ly;
        if (out.count < BezierPath::kMaxSteps &&
            (std::fabs(rdx) > 1e-6f || std::fabs(rdy) > 1e-6f)) {
            BezierPath::Step& s = out.steps[out.count++];
            s.dx = rdx;
            s.dy = rdy;
            s.t = 1.0f;
        }
        out.curved = (out.count > 0);
        return out;
    }

    // 二代：按当前抽中的方向 + 弓高生成弧线点列（固定 3 段）。
    // 距离小于 linear_threshold 时直接退化为一段直线（省开销）。
    BezierPath path2(float dx, float dy) const {
        BezierPath out;
        const float dist = std::sqrt(dx * dx + dy * dy);
        const bool tiny = std::fabs(dx) < cfg_.min_move && std::fabs(dy) < cfg_.min_move;

        if (dist <= cfg_.linear_threshold || tiny || dist < 1.0f) {
            BezierPath::Step& s = out.steps[out.count++];
            s.dx = dx;
            s.dy = dy;
            s.t = 1.0f;
            out.curved = false;
            return out;
        }

        float dX = 0.0f, dY = 0.0f;
        switch (dir_) {
            case BezierDirection::kLeft:  dX = -1.0f; break;
            case BezierDirection::kUp:    dY = -1.0f; break;
            case BezierDirection::kDown:  dY = 1.0f; break;
            case BezierDirection::kRight:
            default:                      dX = 1.0f; break;
        }

        // 弓高上限 60px，随曲率与距离缩放；太小就退回直线
        const float om = std::fmin(60.0f, dist * cfg_.curvature * 0.5f);
        if (om < 0.1f) {
            BezierPath::Step& s = out.steps[out.count++];
            s.dx = dx;
            s.dy = dy;
            s.t = 1.0f;
            out.curved = false;
            return out;
        }

        const float pk = peak_;
        const Point p0{0.0f, 0.0f};
        const Point p1{dX * om * (1.0f - pk) * 0.5f, dY * om * (1.0f - pk) * 0.5f};
        const Point p2{dx + dX * om * pk * 0.5f, dy + dY * om * pk * 0.5f};
        const Point p3{dx, dy};

        float px = 0.0f, py = 0.0f;  // 曲线上的当前位置（无条件推进）
        float lx = 0.0f, ly = 0.0f;  // 最后一次「真正发出」的位置
        for (int i = 1; i <= 3 && out.count < BezierPath::kMaxSteps; ++i) {
            const float t = static_cast<float>(i) / 3.0f;
            const Point b = cubic(p0, p1, p2, p3, t);
            const float ddx = b.x - px, ddy = b.y - py;
            px = b.x;
            py = b.y;
            if (std::fabs(ddx) >= cfg_.min_move || std::fabs(ddy) >= cfg_.min_move) {
                BezierPath::Step& s = out.steps[out.count++];
                s.dx = b.x - lx;
                s.dy = b.y - ly;
                s.t = t;
                lx = b.x;
                ly = b.y;
            }
        }
        // 收尾段：同 path1，保证累加精确等于总位移（原版可能丢位移）。
        {
            const float rdx = dx - lx, rdy = dy - ly;
            if (out.count < BezierPath::kMaxSteps &&
                (std::fabs(rdx) > 1e-6f || std::fabs(rdy) > 1e-6f)) {
                BezierPath::Step& s = out.steps[out.count++];
                s.dx = rdx;
                s.dy = rdy;
                s.t = 1.0f;
            }
        }
        out.curved = true;
        return out;
    }

    // 按代次分发（调用方最常用入口）。enabled=false 时返回单段直线（不改变总位移）。
    BezierPath build(float dx, float dy) const {
        if (!cfg_.enabled) {
            BezierPath out;
            BezierPath::Step& s = out.steps[out.count++];
            s.dx = dx;
            s.dy = dy;
            s.t = 1.0f;
            out.curved = false;
            return out;
        }
        return (cfg_.generation == 2) ? path2(dx, dy) : path1(dx, dy);
    }

    // 二代的方向与弓高抽签：只在「生效键由松开变为按下」的瞬间抽一次，
    // 整个一次瞄准过程方向保持一致（"这一枪往左飘、下一枪往右飘"，不是帧帧抖动）。
    // @return 是否发生了新的抽签
    bool rollOnce(bool key_active) {
        const bool prev = key_active_;
        key_active_ = key_active;
        if (!(key_active && !prev)) return false;

        int allowed[4];
        int n = 0;
        if (cfg_.dir_up) allowed[n++] = static_cast<int>(BezierDirection::kUp);
        if (cfg_.dir_down) allowed[n++] = static_cast<int>(BezierDirection::kDown);
        if (cfg_.dir_left) allowed[n++] = static_cast<int>(BezierDirection::kLeft);
        if (cfg_.dir_right) allowed[n++] = static_cast<int>(BezierDirection::kRight);

        if (n > 0) {
            dir_ = static_cast<BezierDirection>(allowed[next_rand() % static_cast<uint32_t>(n)]);
        } else {
            dir_ = BezierDirection::kRight;
        }
        const float r = static_cast<float>(next_rand() % 10000) / 10000.0f;
        peak_ = cfg_.peak_min + r * (cfg_.peak_max - cfg_.peak_min);
        return true;
    }

    BezierDirection direction() const { return dir_; }
    float peak() const { return peak_; }

    // ------------------------------------------------------------------
    // 误差域弧线整形（2026-09-26 接线用的入口；对标 HEX `safety.lua` 的 Bezier:warp）
    //
    // 与 path1/path2 的区别：**不拆帧**。path* 是把一次位移拆成 N 段让调用方逐帧发，
    // 那套用法要跨帧排队，会与 PID 闭环抢输出（延迟到位 / 叠加）。
    // 这里改成在**误差域**加一个垂直于误差方向的偏移量，逐帧算、当帧生效：
    //   · 路径效果一样是"弧线靠近"而不是直线贴上去；
    //   · 偏移量 ∝ 距离 ⇒ 误差趋零时偏移也趋零，**不引入稳态残差**；
    //   · 不改总位移方向的大小（只加垂直分量）⇒ 闭环收敛性不变。
    // 距离 ≤ linear_threshold 时直接原样返回（近距离不绕路，省开销也避免抖）。
    //
    // @param dx,dy    本帧控制误差（像素域）
    // @param target_id 当前目标 id（切换时重新抽签 + 重置）
    // @param key_active 生效键是否按下（上升沿重新抽签：这一枪往左飘、下一枪往右飘）
    struct WarpOut {
        float dx = 0.0f;
        float dy = 0.0f;
        bool active = false;   // 本帧是否真的施加了弧线
    };
    WarpOut warp_error(float dx, float dy, int target_id, bool key_active) {
        WarpOut out{dx, dy, false};
        if (!cfg_.enabled) { last_target_id_ = target_id; return out; }
        const float dist = std::sqrt(dx * dx + dy * dy);
        if (dist <= cfg_.linear_threshold || dist < 1.0f) { last_target_id_ = target_id; return out; }

        // 目标切换 ⇒ 重新抽签（新的一次瞄准过程，方向可以换）
        if (target_id != last_target_id_) {
            last_target_id_ = target_id;
            rollOnce(true);
            rollOnce(false);   // 造一次上升沿，强制抽新方向/弓高
        } else {
            rollOnce(key_active);
        }

        // 垂直单位向量（基于当前误差方向）
        const float px = -dy / dist;
        const float py = dx / dist;
        // 方向抽签决定往哪一侧偏（up/left 取负，down/right 取正）
        const float sign = (dir_ == BezierDirection::kUp || dir_ == BezierDirection::kLeft) ? -1.0f : 1.0f;
        // 偏移 ∝ 距离 × 曲率 × 弓高抽签 ⇒ 越接近目标越收敛，稳态归零
        const float off = dist * cfg_.curvature * 0.5f * peak_ * sign;
        out.dx = dx + px * off;
        out.dy = dy + py * off;
        out.active = true;
        return out;
    }

    // 测试用：固定种子 ⇒ 抽签序列可复现。生产不调用即为默认种子。
    void set_seed(uint32_t seed) { rng_ = seed ? seed : 0x9E3779B9u; }

    void reset() {
        key_active_ = false;
        dir_ = BezierDirection::kRight;
        peak_ = 0.4f;
        last_target_id_ = -1;
    }

private:
    struct Point { float x, y; };

    static Point cubic(const Point& p0, const Point& p1, const Point& p2, const Point& p3, float t) {
        const float u = 1.0f - t;
        const float tt = t * t;
        const float uu = u * u;
        Point r;
        r.x = uu * u * p0.x + 3.0f * uu * t * p1.x + 3.0f * u * tt * p2.x + tt * t * p3.x;
        r.y = uu * u * p0.y + 3.0f * uu * t * p1.y + 3.0f * u * tt * p2.y + tt * t * p3.y;
        return r;
    }

    // xorshift32：状态在多次抽签间持续推进 ⇒ 固定种子也能产生变化的序列
    uint32_t next_rand() {
        rng_ ^= rng_ << 13;
        rng_ ^= rng_ >> 17;
        rng_ ^= rng_ << 5;
        return rng_;
    }

    BezierTrajectoryConfig cfg_{};
    int last_target_id_ = -1;   // warp_error 用：目标切换即重新抽签
    bool key_active_ = false;
    BezierDirection dir_ = BezierDirection::kRight;
    float peak_ = 0.4f;
    uint32_t rng_ = 0x9E3779B9u;
};

}  // namespace ttbox::core::aim
