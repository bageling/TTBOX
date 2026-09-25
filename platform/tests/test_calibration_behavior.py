import pytest

from ttbox_motion.calibration import (
    KP_FRACTION_PER_FRAME,
    CalibrationAxis,
    CalibrationObservation,
    CalibrationState,
    CalibrationSession,
    derive_pid_params,
    fit_axis_measurements,
)


# 板端实况的 Kp/Kd 削弱宽度（`mouse.smooth_x`）。derive_pid_params 把它改成必传参数
# （以前有默认 9900、调用点全不传 ⇒ 默认值静默生效，配置改成 9990/0 都不起作用）。
BOARD_SMOOTH = 9900.0


def observations(axis, values, delays=None, target_id="track-1"):
    delays = delays or [8.0] * len(values)
    return [
        CalibrationObservation(
            axis=axis,
            injected_count=float(index + 1) * 8,
            measured_delta_px=float(value) * float(index + 1) * 8,
            response_delay_ms=float(delay),
            target_id=target_id,
            valid=True,
        )
        for index, (value, delay) in enumerate(zip(values, delays))
    ]


def test_fit_axis_uses_robust_median_and_rejects_single_spike():
    result = fit_axis_measurements(
        CalibrationAxis.X,
        observations(CalibrationAxis.X, [1.0, 1.05, 0.975, 1.025, 10.0]),
    )
    assert result.converged is True
    assert result.sample_count == 5
    assert 0.9 < result.gain_px_per_count < 1.2
    assert result.rejected_count == 1


def test_fit_axis_rejects_inconsistent_measurements():
    result = fit_axis_measurements(
        CalibrationAxis.Y,
        observations(CalibrationAxis.Y, [2.0, 8.0, 20.0, 40.0, 80.0]),
    )
    assert result.converged is False
    assert "一致性" in result.failure_reason


def test_fit_axis_requires_same_target_identity():
    values = observations(CalibrationAxis.X, [8.0, 8.2, 7.9, 8.1, 8.0])
    values[-1].target_id = "track-2"
    result = fit_axis_measurements(CalibrationAxis.X, values)
    assert result.converged is False
    assert "目标" in result.failure_reason


def test_fit_axis_estimates_delay_from_valid_observations():
    result = fit_axis_measurements(
        CalibrationAxis.X,
        observations(CalibrationAxis.X, [8.0, 8.1, 7.9, 8.0, 8.2], [7.0, 8.0, 9.0, 8.5, 7.5]),
    )
    assert result.converged is True
    assert 7.0 <= result.response_delay_ms <= 9.0


def test_fit_axis_accepts_realistic_capture_loop_delay():
    """2026-09-24 板上实测：144fps 采集回路真实延迟 ≈51ms，
    旧上限 50ms 把两轴全判死（gain/一致性其实都合格）。51ms 必须收敛，
    200ms（假回路）仍要拒绝。"""
    ok = fit_axis_measurements(
        CalibrationAxis.X,
        observations(CalibrationAxis.X, [0.68, 0.69, 0.68, 0.70, 0.69], [51.0] * 5),
    )
    assert ok.converged is True
    assert ok.response_delay_ms == pytest.approx(51.0)
    bad = fit_axis_measurements(
        CalibrationAxis.Y,
        observations(CalibrationAxis.Y, [0.69, 0.70, 0.68, 0.70, 0.69], [200.0] * 5),
    )
    assert bad.converged is False
    assert "延迟" in bad.failure_reason


def test_calibration_session_has_explicit_state_transitions():
    session = CalibrationSession()
    assert session.state is CalibrationState.IDLE
    session.start()
    assert session.state is CalibrationState.PREPARING
    session.begin_axis(CalibrationAxis.X)
    assert session.state is CalibrationState.STABILIZE_X
    session.begin_sampling()
    assert session.state is CalibrationState.SAMPLING_X
    session.begin_analysis()
    assert session.state is CalibrationState.ANALYZING_X
    session.complete_axis()
    assert session.state is CalibrationState.STABILIZE_Y
    session.fail("目标不稳定")
    assert session.state is CalibrationState.FAILED
    assert session.failure_reason == "目标不稳定"


def test_derive_pid_params_scales_kp_inverse_to_gain():
    # 增益越大（1 count 移动越多 px），KP 应越小以防过冲
    low_gain = derive_pid_params(0.4, 0.4, 30, smooth=BOARD_SMOOTH)
    high_gain = derive_pid_params(1.5, 1.5, 30, smooth=BOARD_SMOOTH)
    assert low_gain["kp"] > high_gain["kp"]
    assert 0.0 < low_gain["kp"] <= 60.0


def test_derive_pid_params_increases_kd_with_delay():
    low_delay = derive_pid_params(0.65, 0.65, 10, smooth=BOARD_SMOOTH)
    high_delay = derive_pid_params(0.65, 0.65, 60, smooth=BOARD_SMOOTH)
    assert high_delay["kd"] > low_delay["kd"]


def test_derive_pid_params_reduces_predict_with_delay():
    low_delay = derive_pid_params(0.65, 0.65, 10, smooth=BOARD_SMOOTH)
    high_delay = derive_pid_params(0.65, 0.65, 60, smooth=BOARD_SMOOTH)
    assert high_delay["predict"] < low_delay["predict"]
    assert 0.1 <= high_delay["predict"] <= 0.35


def test_derive_pid_params_handles_extreme_gain_delay():
    # 超高增益 + 高延迟：KP 走保守分支，必须仍给出有效参数
    d = derive_pid_params(1.5, 1.5, 60, smooth=BOARD_SMOOTH)
    assert 4.0 <= d["kp"] <= 60.0
    assert 4.0 <= d["kd"] <= 50.0
    assert 0.1 <= d["predict"] <= 0.35


def test_derive_pid_params_rejects_zero_gain():
    import pytest as _p

    with _p.raises(ValueError):
        derive_pid_params(0.0, 0.65, 30, smooth=BOARD_SMOOTH)


def test_derive_pid_params_requires_smooth():
    """smooth 必须是必传参数：带默认值时调用方漏传会静默按默认算（这正是旧 bug）。"""
    import pytest as _p

    with _p.raises(TypeError):
        derive_pid_params(0.65, 0.65, 30)          # 故意不传 smooth


# ===========================================================================
# 分母口径：injected_count 必须是**真实注入 count**，不能是偏置的 px
#
# 闭环恒等式（与 PID 参数、与游戏灵敏度无关，对任意时间窗成立）：
#     目标在画面里的位移(px) ≡ gain(px/count) × Σ注入count
# ⇒ gain = Δpx / ΔΣcounts。
# 旧实现把"偏置的 px"当分母（量纲 px/px）⇒ 比值恒 ≈1.0 ⇒ 标定即使成功，
# 由这个假 gain 推出的 kp 也只由它决定（实测恒为 15），与真实手感无关。
# 下面两条用例把"对的会算出什么"和"错的会算出什么"都钉住。
# ===========================================================================

# 面板实际用的幅度表：正负交替 + 分量程（见 ttbox-web.py::CALIB_AMPLITUDES）
ALTERNATING_AMPLITUDES_PX = (8.0, -8.0, 16.0, -16.0, 24.0, -24.0, 32.0, -32.0)


def counts_observations(axis, gain_px_per_count, amplitudes=ALTERNATING_AMPLITUDES_PX,
                        overshoot=0.0, delay_ms=12.0, target_id="track-1"):
    """按真实物理关系造观测：闭环为 offset 走了 |amp| 的位移，付出的 count = |amp|/gain。

    overshoot 用来模拟"慢环冲过头再拉回"：净位移与净 count 同步增大，
    比值不变 —— 这正是用**有符号净量**配对的好处（路径无关）。
    """
    items = []
    for amp in amplitudes:
        net_px = abs(amp) * (1.0 + overshoot)
        net_counts = net_px / gain_px_per_count
        items.append(CalibrationObservation(
            axis=axis,
            injected_count=net_counts,
            measured_delta_px=net_px,
            response_delay_ms=delay_ms,
            target_id=target_id,
            valid=True,
        ))
    return items


def test_fit_axis_recovers_true_gain_from_real_injected_counts():
    result = fit_axis_measurements(
        CalibrationAxis.X,
        counts_observations(CalibrationAxis.X, 0.65),
    )
    assert result.converged is True
    assert result.sample_count == 8
    assert result.gain_px_per_count == pytest.approx(0.65, abs=1e-6)
    # 一致性应接近满分：增益是物理常数，不该随幅度变化
    assert result.consistency > 0.99


def test_fit_axis_is_robust_to_overshoot_when_pairing_net_quantities():
    """冲过头再拉回：净位移与净 count 同增同减，gain 不变（用有符号净量配对的原因）。"""
    clean = fit_axis_measurements(
        CalibrationAxis.Y, counts_observations(CalibrationAxis.Y, 0.42))
    overshot = fit_axis_measurements(
        CalibrationAxis.Y, counts_observations(CalibrationAxis.Y, 0.42, overshoot=0.35))
    assert clean.converged is True and overshot.converged is True
    assert overshot.gain_px_per_count == pytest.approx(clean.gain_px_per_count, abs=1e-6)


def _fit_with_px_denominator(axis):
    """复现修复前的算法：分母用"偏置的 px"（稳态位移恰好等于偏置 px）。"""
    return fit_axis_measurements(
        axis,
        [CalibrationObservation(axis=axis,
                                injected_count=abs(amp),      # ← 错：px 当 count
                                measured_delta_px=abs(amp),   # 稳态位移就等于偏置 px
                                response_delay_ms=12.0,
                                target_id="track-1", valid=True)
         for amp in ALTERNATING_AMPLITUDES_PX],
    )


def test_px_denominator_yields_gain_one_for_every_game():
    """★ 反向锁：分母退回 px 时，**无论真实游戏灵敏度是多少，拟合出的 gain 都是 1.0**。

    gain=1.0 又会让 derive_pid_params 恒返回 kp=KP_FRACTION_PER_FRAME/kp_scale
    （= 0.07/0.01 = 7.0），也就是"标定成功"却写下一个与任何游戏都无关的常数 ——
    自动调参整个失效。这条用例存在的意义：谁把分母改回 px，它就会红。
    """
    for _ in (0.25, 0.4, 0.65, 1.2, 2.0):            # 五种差异极大的游戏灵敏度
        fake = _fit_with_px_denominator(CalibrationAxis.X)
        assert fake.converged is True                 # 它会"成功"，这才是最坑的地方
        assert fake.gain_px_per_count == pytest.approx(1.0)
        assert derive_pid_params(fake.gain_px_per_count, fake.gain_px_per_count, 12.0,
                                 smooth=BOARD_SMOOTH)["kp"] \
            == pytest.approx(KP_FRACTION_PER_FRAME / 0.01)


def test_real_counts_denominator_makes_kp_track_the_actual_game():
    """对照：分母用真实 count 时，kp 随真实 gain 变化 ⇒ 自动调参才真的在调参。"""
    kps = {}
    for g in (0.25, 0.4, 0.65, 1.2, 2.0):
        fit = fit_axis_measurements(CalibrationAxis.X, counts_observations(CalibrationAxis.X, g))
        assert fit.gain_px_per_count == pytest.approx(g, rel=1e-9)
        kps[g] = derive_pid_params(fit.gain_px_per_count, fit.gain_px_per_count, 12.0,
                                   smooth=BOARD_SMOOTH)["kp"]
    assert len(set(kps.values())) == len(kps)          # 五个不同的游戏 → 五个不同的 kp
    # derive_pid_params 把 kp 保留 2 位小数，故用绝对容差
    assert kps[0.65] == pytest.approx(KP_FRACTION_PER_FRAME / 0.01 / 0.65, abs=0.005)
    assert kps[0.65] != pytest.approx(KP_FRACTION_PER_FRAME / 0.01)   # 不再是与游戏无关的常数


# ===========================================================================
# 自动调参锚点：必须对齐板端 A/B 实测**稳定组**，不能落回**振荡组**
#
# 2026-09-24 板端（gain 实测 x=0.686 / y=0.695、回路延迟 51ms、smooth=9900）：
#   kp=25/kd=25 → bias 阶跃打进持续振荡（准星 ±150px），标定必挂
#   kp=10/kd=30 → 同一链路 16 轮全稳
# 旧公式（单帧 15%）算出 kp=21.87/kd=24.60，几乎就是那个振荡组 ——
# "标定成功写回的参数正好是让标定失败的那组"。下面两条把它钉死。
# ===========================================================================


def test_derived_params_match_board_measured_stable_set():
    d = derive_pid_params(0.686, 0.695, 51.0, smooth=BOARD_SMOOTH)
    # 实测稳定组是 kp=10 / kd=30：允许小幅偏差，但不能差一个量级
    assert d["kp"] == pytest.approx(10.0, rel=0.15)
    assert d["kd"] == pytest.approx(30.0, rel=0.15)
    assert d["predict"] <= 0.35


def test_derived_kp_never_lands_in_board_measured_oscillating_band():
    """旧公式在 51ms 给出 kp=21.87/delay 比 1.13——落在实测振荡组附近，这里禁止回归。"""
    d = derive_pid_params(0.686, 0.695, 51.0, smooth=BOARD_SMOOTH)
    assert d["kp"] < 15.0                     # 明显低于振荡组 kp=25
    assert d["kd"] / d["kp"] > 2.0            # 阻尼比要够（实测稳定组是 3.0）


def test_derive_pid_params_kp_eff_is_invariant_to_smooth():
    """★ smooth 是"削弱 Kp/Kd 的倍率"，改它不该改**物理行为**。

    生效域 kp_eff = kp × (bandwidth-smooth)/bandwidth 必须由 gain 决定、与 smooth 无关
    （否则面板上一改 Smooth，同一份标定结果就推出完全不同的手感的极限环边界）。
    旧实现把 smooth 写死 9900 且调用点不传 ⇒ smooth=0 时 kp_eff 强 100 倍（发散）。
    """
    for smooth in (0.0, 3000.0, 9900.0):
        d = derive_pid_params(0.686, 0.686, 51.0, smooth=smooth)
        kp_eff = d["kp"] * (10000.0 - smooth) / 10000.0
        assert kp_eff == pytest.approx(KP_FRACTION_PER_FRAME / 0.686, rel=0.06)


def test_derive_pid_params_rejects_out_of_range_smooth():
    """smooth 越界会让 kp_scale 变负或 0 ⇒ 推导出的 kp 无意义，必须 fail-loud。"""
    for bad in (10000.0, 12000.0, -1.0):
        with pytest.raises(ValueError):
            derive_pid_params(0.65, 0.65, 30.0, smooth=bad)
