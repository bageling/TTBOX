// test_bezier_trajectory.cpp — 贝塞尔移动轨迹（BezierTrajectory）单元测试
//
// 来源算法：BB_927 内部会员版 algo/bezier.lua（两代），本文件锁死其关键性质。
//
// 覆盖场景：
//   Case1  未启用（enabled=false）→ 退化为单段直线，总位移不变
//   Case2  一代分段数自适应：dist=123→12 段、dist=200→15 段、dist=10→下限 5 段
//   Case3  一代累加精确回到终点（120,-30），与 BB demo 实测一致
//   Case4  一代 min_move 过滤：段数变少但累加仍精确（位移被下一段吸收）
//   Case5  一代超大分段数被 kMaxSteps 截断，不越界
//   Case6  二代近距（≤ linear_threshold）退化为直线，curved=false
//   Case7  二代远距走弧线，累加精确回到终点
//   Case8  二代弧线确实偏离直线（不是"伪弧线"）
//   Case9  二代弓高上限 60：极端距离下控制点推出量被钳住
//   Case10 rollOnce 只在"松开→按下"瞬间抽签，持续按住不重抽
//   Case11 rollOnce 抽中的方向落在允许集合内，peak 落在 [peak_min, peak_max]
//   Case12 固定种子 ⇒ 抽签序列可复现
#include <cstdio>
#include <cmath>

#include "mouse/BezierTrajectory.hpp"

using namespace ttbox::core::aim;

namespace {

int failures = 0;

void check(bool cond, const char* msg) {
    if (!cond) { std::printf("  FAIL: %s\n", msg); failures++; }
    else { std::printf("  PASS: %s\n", msg); }
}

void check_near(float a, float b, float eps, const char* msg) {
    if (std::fabs(a - b) > eps) {
        std::printf("  FAIL: %s (got %.6f want %.6f)\n", msg, a, b);
        failures++;
    } else {
        std::printf("  PASS: %s (%.4f)\n", msg, a);
    }
}

void sum_path(const BezierPath& p, float* sx, float* sy) {
    *sx = 0.0f;
    *sy = 0.0f;
    for (int i = 0; i < p.count; ++i) {
        *sx += p.steps[i].dx;
        *sy += p.steps[i].dy;
    }
}

BezierTrajectoryConfig make_cfg() {
    BezierTrajectoryConfig c;
    c.enabled = true;
    c.generation = 1;
    c.segments = 10.0f;
    c.linear_threshold = 45.0f;
    c.curvature = 0.2f;
    c.peak_min = 0.2f;
    c.peak_max = 0.6f;
    c.dir_up = true;
    c.dir_down = true;
    c.dir_left = false;
    c.dir_right = false;
    c.min_move = 0.1f;
    return c;
}

}  // namespace

int main() {
    std::printf("== test_bezier_trajectory ==\n");

    // Case1 未启用 → 单段直线，总位移不变
    {
        BezierTrajectory bz;
        BezierTrajectoryConfig c = make_cfg();
        c.enabled = false;
        bz.configure(c);
        const BezierPath p = bz.build(120.0f, -30.0f);
        float sx, sy;
        sum_path(p, &sx, &sy);
        check(p.count == 1 && !p.curved, "Case1 未启用：单段直线");
        check_near(sx, 120.0f, 1e-4f, "Case1 X 位移不变");
        check_near(sy, -30.0f, 1e-4f, "Case1 Y 位移不变");
    }

    // Case2 一代分段数自适应（seg = max(5, floor(segments × min(1.5, dist/100)))）
    {
        BezierTrajectory bz;
        bz.configure(make_cfg());
        // dist = hypot(120,30) = 123.69 → min(1.5,1.2369)=1.2369 → floor(12.369)=12
        check(bz.path1(120.0f, -30.0f).count == 12, "Case2 dist≈123.7 → 12 段");
        // dist = 200 → min(1.5,2.0)=1.5 → floor(15)=15
        check(bz.path1(200.0f, 0.0f).count == 15, "Case2 dist=200 → 15 段");
        // dist = 10 → min(1.5,0.1)=0.1 → floor(1)=1 → 下限 5
        check(bz.path1(10.0f, 0.0f).count == 5, "Case2 dist=10 → 下限 5 段");
    }

    // Case3 一代累加精确回到终点
    {
        BezierTrajectory bz;
        bz.configure(make_cfg());
        const BezierPath p = bz.path1(120.0f, -30.0f);
        float sx, sy;
        sum_path(p, &sx, &sy);
        check_near(sx, 120.0f, 1e-3f, "Case3 累加 X 精确回到 120");
        check_near(sy, -30.0f, 1e-3f, "Case3 累加 Y 精确回到 -30");
    }

    // Case4 min_move 过滤后仍不丢位移（收尾段兜住残差；原版会整段丢失）
    {
        BezierTrajectory bz;
        BezierTrajectoryConfig c = make_cfg();
        c.min_move = 20.0f;  // 很粗的阈值：单段平均才 10px，会把 12 段全过滤掉
        bz.configure(c);
        const BezierPath p = bz.path1(120.0f, -30.0f);
        float sx, sy;
        sum_path(p, &sx, &sy);
        check(p.count >= 1 && p.count < 12, "Case4 大 min_move：段数被过滤但至少剩收尾段");
        check_near(sx, 120.0f, 1e-3f, "Case4 过滤后累加 X 仍精确（不丢位移）");
        check_near(sy, -30.0f, 1e-3f, "Case4 过滤后累加 Y 仍精确（不丢位移）");
    }

    // Case5 超大分段数：钳段数而非截断循环 ⇒ 仍精确到达终点
    {
        BezierTrajectory bz;
        BezierTrajectoryConfig c = make_cfg();
        c.segments = 100.0f;  // dist=200 → 理论 150 段，应被钳到 32
        bz.configure(c);
        const BezierPath p = bz.path1(200.0f, 0.0f);
        float sx, sy;
        sum_path(p, &sx, &sy);
        check(p.count <= BezierPath::kMaxSteps, "Case5 段数不超过 kMaxSteps，不越界");
        check(p.count == BezierPath::kMaxSteps, "Case5 恰好填满 kMaxSteps（钳段数）");
        check_near(sx, 200.0f, 1e-3f, "Case5 钳段数后仍精确到达终点");
    }

    // Case6 二代近距退化为直线
    {
        BezierTrajectory bz;
        bz.configure(make_cfg());
        const BezierPath p = bz.path2(30.0f, 0.0f);  // dist=30 ≤ 45
        float sx, sy;
        sum_path(p, &sx, &sy);
        check(p.count == 1 && !p.curved, "Case6 近距退化为单段直线");
        check_near(sx, 30.0f, 1e-4f, "Case6 退化后 X 位移不变");
    }

    // Case7 二代远距走弧线且累加精确
    {
        BezierTrajectory bz;
        BezierTrajectoryConfig c = make_cfg();
        c.dir_up = true;
        c.dir_down = false;  // 只允许 up，方向确定
        bz.configure(c);
        bz.set_seed(1);
        bz.rollOnce(true);
        const BezierPath p = bz.path2(120.0f, -30.0f);  // dist=123.69 > 45
        float sx, sy;
        sum_path(p, &sx, &sy);
        check(p.curved, "Case7 远距走弧线（curved=true）");
        check(p.count == 3, "Case7 二代固定 3 段");
        check_near(sx, 120.0f, 1e-3f, "Case7 累加 X 精确回到 120");
        check_near(sy, -30.0f, 1e-3f, "Case7 累加 Y 精确回到 -30");
    }

    // Case8 弧线确实偏离直线（不是"伪弧线"）：
    //   沿点列累加得到各时刻的实际位置，算它到 (0,0)-(dx,dy) 直线的垂直距离，
    //   取最大值。注意弓高出现在后段（peak 把第二个控制点推得更远），
    //   只看第一段会误判成直线。
    {
        BezierTrajectory bz;
        BezierTrajectoryConfig c = make_cfg();
        c.dir_up = true;
        c.dir_down = false;
        c.min_move = 0.0f;  // 关掉过滤，保证 3 段都在
        bz.configure(c);
        bz.set_seed(1);
        bz.rollOnce(true);
        const float dx = 120.0f, dy = -30.0f;
        const BezierPath p = bz.path2(dx, dy);
        const float len = std::sqrt(dx * dx + dy * dy);
        const float nx = -dy / len, ny = dx / len;  // 直线法向
        float cx = 0.0f, cy = 0.0f;
        float max_dev = 0.0f;
        for (int i = 0; i < p.count; ++i) {
            cx += p.steps[i].dx;
            cy += p.steps[i].dy;
            // 点到过原点直线的距离 = |(cx,cy) · n|
            const float d = std::fabs(cx * nx + cy * ny);
            if (d > max_dev) max_dev = d;
        }
        check(max_dev > 1.0f, "Case8 弧线偏离直线（存在真实弓高）");
        std::printf("        (max_dev=%.3f px)\n", max_dev);
    }

    // Case9 弓高上限 60：极端大距离时弓高被钳住（不随距离无限增长）
    {
        BezierTrajectory bz;
        BezierTrajectoryConfig c = make_cfg();
        c.dir_up = true;
        c.dir_down = false;
        c.min_move = 0.0f;
        bz.configure(c);
        bz.set_seed(1);
        bz.rollOnce(true);
        const BezierPath pa = bz.path2(1000.0f, 0.0f);
        const BezierPath pb = bz.path2(4000.0f, 0.0f);
        // 弓高 = min(60, dist*0.2*0.5)：dist=1000 → 60（钳住）；dist=4000 → 60（钳住）
        // 两处偏离量应几乎相同（都取到上限 60）
        const float ya = pa.steps[0].dy;
        const float yb = pb.steps[0].dy;
        check(std::fabs(ya - yb) < 1e-2f, "Case9 弓高被上限 60 钳住，不随距离增长");
    }

    // Case10 rollOnce 只在"松开→按下"瞬间抽签
    {
        BezierTrajectory bz;
        bz.configure(make_cfg());
        check(!bz.rollOnce(false), "Case10 松开时不抽签");
        check(bz.rollOnce(true), "Case10 松开→按下：抽签");
        const float p1 = bz.peak();
        check(!bz.rollOnce(true), "Case10 持续按住：不重抽");
        check_near(bz.peak(), p1, 1e-6f, "Case10 持续按住 peak 不变");
        check(!bz.rollOnce(false), "Case10 松开：不抽签");
        check(bz.rollOnce(true), "Case10 再次按下：重新抽签");
    }

    // Case11 方向落在允许集合内，peak 落在 [peak_min, peak_max]
    {
        BezierTrajectory bz;
        BezierTrajectoryConfig c = make_cfg();
        c.dir_up = true;
        c.dir_down = false;
        c.dir_left = false;
        c.dir_right = false;
        bz.configure(c);
        bz.set_seed(7);
        bool all_up = true;
        bool peak_ok = true;
        for (int i = 0; i < 50; ++i) {
            bz.rollOnce(false);
            bz.rollOnce(true);
            if (bz.direction() != BezierDirection::kUp) all_up = false;
            if (bz.peak() < c.peak_min - 1e-6f || bz.peak() > c.peak_max + 1e-6f) peak_ok = false;
        }
        check(all_up, "Case11 方向只落在允许集合内（仅 up）");
        check(peak_ok, "Case11 peak 始终落在 [peak_min, peak_max]");
    }

    // Case12 固定种子 ⇒ 序列可复现
    {
        BezierTrajectory a;
        BezierTrajectory b;
        a.configure(make_cfg());
        b.configure(make_cfg());
        a.set_seed(2026);
        b.set_seed(2026);
        float sa = 0.0f, sb = 0.0f;
        for (int i = 0; i < 20; ++i) {
            a.rollOnce(false); a.rollOnce(true);
            b.rollOnce(false); b.rollOnce(true);
            sa += a.peak();
            sb += b.peak();
        }
        check_near(sa, sb, 1e-6f, "Case12 同种子 ⇒ 抽签序列完全一致");
    }

    std::printf("== failures=%d ==\n", failures);
    return failures == 0 ? 0 : 1;
}
