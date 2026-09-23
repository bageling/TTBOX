// RuntimeProfile.cpp — RuntimeProfile JSON 序列化/校验
/*
 * TTBOX 文件说明
 *
 * 文件：RuntimeProfile.cpp
 *
 * 作用：
 *   运行时配置文件的定义和翻译。
 *   定义所有可调参数，并在配置格式和 TTBOX 内部格式之间转换。
 *
 * 小白理解：
 *   你在 Web 页面上看到的每个参数（置信度、截取尺寸、PID 参数等），
 *   都在这里定义。它还负责把配置格式的参数翻译成 TTBOX 内部格式。
 *
 * 注意：
 *   本注释仅用于说明代码，不改变程序逻辑。
 */

#include <algorithm>
#include <cmath>
#include <cstdio>

#include "model/RuntimeProfile.hpp"

namespace ttbox::core {

// ---------------------------------------------------------------------------
// CaptureProfile
// ---------------------------------------------------------------------------

bool CaptureProfile::valid(uint32_t frame_w, uint32_t frame_h,
                           std::string* error) const {
    // 0 表示"用全帧"，等价于合法
    const uint32_t w = (width == 0) ? frame_w : width;
    const uint32_t h = (height == 0) ? frame_h : height;
    if (w == 0 || h == 0) {
        if (error) *error = "ROI 尺寸不能为 0";
        return false;
    }
    if (w > frame_w || h > frame_h) {
        if (error) *error = "ROI 尺寸超全帧: " + std::to_string(w) + "x" +
                            std::to_string(h) + " > " + std::to_string(frame_w) +
                            "x" + std::to_string(frame_h);
        return false;
    }
    // offset 为相对屏幕中心的偏移；计算左上角起点并 clamp 后必然界内
    const int32_t cx = static_cast<int32_t>(frame_w / 2) + offset_x;
    const int32_t cy = static_cast<int32_t>(frame_h / 2) + offset_y;
    const int32_t rx = std::max<int32_t>(0, std::min<int32_t>(
        cx - static_cast<int32_t>(w / 2), static_cast<int32_t>(frame_w - w)));
    const int32_t ry = std::max<int32_t>(0, std::min<int32_t>(
        cy - static_cast<int32_t>(h / 2), static_cast<int32_t>(frame_h - h)));
    (void)rx; (void)ry;  // 计算即校验（clamp 后必界内）；实际起点由 apply_runtime_profile 计算
    return true;
}

// ---------------------------------------------------------------------------
// 工具：读 object 成员（key 不存在返回默认值）
// ---------------------------------------------------------------------------
namespace {

// ---------------------------------------------------------------------------
// capture ROI 合法范围：kMinCaptureRoiPx / kMaxCaptureRoiPx 定义已提升到
// RuntimeProfile.hpp（单一权威源，DUP-10），此处与 Application SET_CONFIG 校验共用。
// ---------------------------------------------------------------------------

// 退化值消毒：落在 [1, kMinCaptureRoiPx) 的非法小值纠正为 0（全帧）。
// 用于 from_json 加载历史坏配置时自愈，避免服务因一个坏字段起不来或瞎跑。
uint32_t sanitize_capture_roi(uint32_t v) {
    return (v > 0 && v < kMinCaptureRoiPx) ? 0u : v;
}

int64_t obj_int(const JsonValue& o, const char* key, int64_t def) {
    const JsonValue* v = o.find(key);
    return (v && v->is_number()) ? v->as_int(def) : def;
}
double obj_num(const JsonValue& o, const char* key, double def) {
    const JsonValue* v = o.find(key);
    return (v && v->is_number()) ? v->as_number(def) : def;
}
bool obj_bool(const JsonValue& o, const char* key, bool def) {
    const JsonValue* v = o.find(key);
    return (v && v->is_bool()) ? v->as_bool(def) : def;
}
std::string obj_str(const JsonValue& o, const char* key, const std::string& def) {
    const JsonValue* v = o.find(key);
    return (v && v->is_string()) ? v->as_string(def) : def;
}

}  // namespace

// ---------------------------------------------------------------------------
// RuntimeProfile
// ---------------------------------------------------------------------------

bool RuntimeProfile::validate(std::string* error) const {
    // 非有限值总闸：JSON 1e999 等可产生 inf，NaN/inf 进入 PID 会输出乱飞（fail-closed 防线）。
    const float mouse_nums[] = {
        mouse.kp_x, mouse.kp_y, mouse.kd_x, mouse.kd_y,
        mouse.predict_x, mouse.predict_y, mouse.rate_x, mouse.rate_y,
        mouse.fov_range, mouse.confidence, mouse.sensitivity, mouse.output_scale,
        mouse.deadzone_x, mouse.deadzone_y, mouse.output_deadzone,
        mouse.hfov, mouse.vfov, mouse.move_speed_x, mouse.move_speed_y,
        mouse.aim_point.aim_offset_x, mouse.aim_point.aim_offset_y,
        mouse.aim_point.offset_x, mouse.aim_point.offset_y,
        mouse.personal_motion.curve_blend, mouse.personal_motion.speed_blend,
        mouse.personal_motion.reaction_blend, mouse.personal_motion.max_reaction_delay_ms,
        inference.confidence, inference.iou,
        fov.center_x, fov.center_y, fov.radius,
    };
    for (const float v : mouse_nums) {
        if (!std::isfinite(v)) {
            if (error) *error = "配置含非有限数值（NaN/Infinity），已拒绝";
            return false;
        }
    }
    if (inference.confidence < 0.0f || inference.confidence > 1.0f) {
        if (error) *error = "confidence 必须在 [0,1]";
        return false;
    }
    if (inference.iou < 0.0f || inference.iou > 1.0f) {
        if (error) *error = "iou 必须在 [0,1]";
        return false;
    }
    for (const int c : inference.class_filter) {
        if (c < 0) {
            if (error) *error = "class_filter 含负类别";
            return false;
        }
    }
    if (inference.max_detections < 0) {
        if (error) *error = "max_detections 不能为负";
        return false;
    }
    if (fov.enabled) {
        if (fov.center_x < 0.0f || fov.center_x > 1.0f ||
            fov.center_y < 0.0f || fov.center_y > 1.0f) {
            if (error) *error = "FOV 中心必须在 [0,1]";
            return false;
        }
        if (fov.radius <= 0.0f || fov.radius > 1.0f) {
            if (error) *error = "FOV 半径必须在 (0,1]";
            return false;
        }
    }
    if (mouse.fov_range < 0.0f || mouse.fov_range > 1.0f) {
        if (error) *error = "mouse.fov_range 必须在 [0,1]";
        return false;
    }
    if (mouse.confidence < 0.0f || mouse.confidence > 1.0f) {
        if (error) *error = "mouse.confidence 必须在 [0,1]";
        return false;
    }
    if (mouse.kp_x < 0.0f || mouse.kp_y < 0.0f) {
        if (error) *error = "mouse.kp 不能为负";
        return false;
    }
    if (mouse.hfov <= 0.0f || mouse.hfov >= 180.0f ||
        mouse.vfov <= 0.0f || mouse.vfov >= 180.0f) {
        if (error) *error = "mouse.hfov/vfov 必须在 (0,180)";
        return false;
    }
    if (mouse.move_speed_x < 0.0f || mouse.move_speed_y < 0.0f) {
        if (error) *error = "mouse.move_speed 不能为负";
        return false;
    }
    if (mouse.rate_x < 0.0f || mouse.rate_y < 0.0f ||
        mouse.sensitivity < 0.0f || mouse.output_scale < 0.0f) {
        if (error) *error = "mouse 输出系数不能为负";
        return false;
    }

    if (mouse.lost_grace_ms < 0.0f) {
        if (error) *error = "mouse.lost_grace_ms 不能为负";
        return false;
    }
    if (mouse.personal_motion.curve_blend < 0.0f || mouse.personal_motion.curve_blend > 1.0f ||
        mouse.personal_motion.speed_blend < 0.0f || mouse.personal_motion.speed_blend > 1.0f ||
        mouse.personal_motion.reaction_blend < 0.0f || mouse.personal_motion.reaction_blend > 1.0f ||
        mouse.personal_motion.max_reaction_delay_ms < 0.0f ||
        mouse.personal_motion.max_reaction_delay_ms > 1000.0f) {
        if (error) *error = "personal_motion 混合参数超出范围";
        return false;
    }
    for (const float knot : mouse.personal_motion.knots) {
        if (!std::isfinite(knot) || knot < 0.0f || knot > 1.0f) {
            if (error) *error = "personal_motion knots 必须在 [0,1]";
            return false;
        }
    }
    if (mouse.personal_motion.knots.size() > 32) {
        if (error) *error = "personal_motion knots 最多 32 个";
        return false;
    }
    // capture ROI 退化值防线（fail-closed）：0=全帧合法；非零则必须落在
    // [kMinCaptureRoiPx, kMaxCaptureRoiPx]。拦截 1×1 这类"静默摧毁流水线"的配置。
    // 注意：此处不校验"是否超全帧"（validate 无 frame_w/h 上下文），
    // 越界由 WorkerPool::apply_runtime_profile 的 rw<=fw/rh<=fh 守卫兜底（超界即不应用 ROI）。
    for (const uint32_t v : {capture.width, capture.height}) {
        if (v != 0 && (v < kMinCaptureRoiPx || v > kMaxCaptureRoiPx)) {
            if (error) *error = "capture 截取尺寸非法: " + std::to_string(v) +
                                "（0=全帧，或需在 " + std::to_string(kMinCaptureRoiPx) +
                                "~" + std::to_string(kMaxCaptureRoiPx) + " 之间）";
            return false;
        }
    }
    // P-ZC-1 采集层裁剪：0 = 沿用全局配置（合法）；非零走与 capture 同一道
    // 退化值防线，拦住 1×1 这种会把采集流做成 1 像素的配置。
    for (const uint32_t v : {video.crop_width, video.crop_height}) {
        if (v != 0 && (v < kMinCaptureRoiPx || v > kMaxCaptureRoiPx)) {
            if (error) *error = "video 采集截取尺寸非法: " + std::to_string(v) +
                                "（0=沿用全局配置，或需在 " + std::to_string(kMinCaptureRoiPx) +
                                "~" + std::to_string(kMaxCaptureRoiPx) + " 之间）";
            return false;
        }
    }
    if (preview.width == 0 || preview.height == 0 ||
        preview.width > 3840 || preview.height > 2160 ||
        preview.roi_w == 0 || preview.roi_h == 0) {
        if (error) *error = "preview 尺寸/ROI 必须为正";
        return false;
    }
    return true;
}

JsonValue RuntimeProfile::to_json() const {
    JsonValue root = JsonValue::object();
    root.set("model_id", JsonValue::string(model_id));

    JsonValue cap = JsonValue::object();
    cap.set("width", JsonValue::number(static_cast<double>(capture.width)));
    cap.set("height", JsonValue::number(static_cast<double>(capture.height)));
    cap.set("offset_x", JsonValue::number(static_cast<double>(capture.offset_x)));
    cap.set("offset_y", JsonValue::number(static_cast<double>(capture.offset_y)));
    root.set("capture", std::move(cap));

    JsonValue inf = JsonValue::object();
    inf.set("confidence", JsonValue::number(static_cast<double>(inference.confidence)));
    inf.set("iou", JsonValue::number(static_cast<double>(inference.iou)));
    JsonValue cf = JsonValue::array();
    for (const int c : inference.class_filter) cf.push_back(JsonValue::number(static_cast<double>(c)));
    inf.set("class_filter", std::move(cf));
    inf.set("max_detections", JsonValue::number(static_cast<double>(inference.max_detections)));
    root.set("inference", std::move(inf));

    // P-ZC-1：采集层裁剪 + 零拷贝开关（缺段/缺键 = 沿用全局配置，老机器升级行为不变）
    JsonValue vid = JsonValue::object();
    vid.set("crop_width", JsonValue::number(static_cast<double>(video.crop_width)));
    vid.set("crop_height", JsonValue::number(static_cast<double>(video.crop_height)));
    vid.set("zero_copy_input", JsonValue::boolean(video.zero_copy_input));
    root.set("video", std::move(vid));

    JsonValue gf = JsonValue::object();
    gf.set("enabled", JsonValue::boolean(geometry_filter.enabled));
    gf.set("min_head_conf", JsonValue::number(geometry_filter.min_head_conf));
    gf.set("min_body_conf", JsonValue::number(geometry_filter.min_body_conf));
    gf.set("paired_head_min_conf", JsonValue::number(geometry_filter.paired_head_min_conf));
    gf.set("head_only_min_conf", JsonValue::number(geometry_filter.head_only_min_conf));
    gf.set("head_only_center_max_px", JsonValue::number(geometry_filter.head_only_center_max_px));
    gf.set("min_body_width_px", JsonValue::number(geometry_filter.min_body_width_px));
    gf.set("min_body_height_px", JsonValue::number(geometry_filter.min_body_height_px));
    gf.set("border_reject_enabled", JsonValue::boolean(geometry_filter.reject_border));
    root.set("geometry_filter", std::move(gf));

    JsonValue fobj = JsonValue::object();
    fobj.set("enabled", JsonValue::boolean(fov.enabled));
    fobj.set("shape", JsonValue::number(static_cast<double>(fov.shape == FovShape::kRect ? 1 : 0)));
    fobj.set("radius", JsonValue::number(static_cast<double>(fov.radius)));
    fobj.set("center_x", JsonValue::number(static_cast<double>(fov.center_x)));
    fobj.set("center_y", JsonValue::number(static_cast<double>(fov.center_y)));
    root.set("fov", std::move(fobj));

    // A10：鼠标 AI 注入配置
    JsonValue m = JsonValue::object();
    m.set("enabled", JsonValue::boolean(mouse.enabled));
    m.set("aim_hotkey", JsonValue::number(static_cast<double>(mouse.aim_hotkey)));
    m.set("aim_hotkey2", JsonValue::number(static_cast<double>(mouse.aim_hotkey2)));
    m.set("aim_hotkey_mode", JsonValue::string(aim::mouse_hotkey_mode_name(mouse.aim_hotkey_mode)));
    m.set("fov_range", JsonValue::number(static_cast<double>(mouse.fov_range)));
    m.set("confidence", JsonValue::number(static_cast<double>(mouse.confidence)));
    m.set("kp_x", JsonValue::number(static_cast<double>(mouse.kp_x)));
    m.set("kp_y", JsonValue::number(static_cast<double>(mouse.kp_y)));
    m.set("kd_x", JsonValue::number(static_cast<double>(mouse.kd_x)));
    m.set("kd_y", JsonValue::number(static_cast<double>(mouse.kd_y)));
    m.set("fov_mode", JsonValue::boolean(mouse.fov_mode));
    m.set("hfov", JsonValue::number(static_cast<double>(mouse.hfov)));
    m.set("vfov", JsonValue::number(static_cast<double>(mouse.vfov)));
    m.set("move_speed_x", JsonValue::number(static_cast<double>(mouse.move_speed_x)));
    m.set("move_speed_y", JsonValue::number(static_cast<double>(mouse.move_speed_y)));
    m.set("rate_x", JsonValue::number(static_cast<double>(mouse.rate_x)));
    m.set("rate_y", JsonValue::number(static_cast<double>(mouse.rate_y)));
    m.set("sensitivity", JsonValue::number(static_cast<double>(mouse.sensitivity)));
    m.set("output_scale", JsonValue::number(static_cast<double>(mouse.output_scale)));
    m.set("deadzone_x", JsonValue::number(static_cast<double>(mouse.deadzone_x)));
    m.set("deadzone_y", JsonValue::number(static_cast<double>(mouse.deadzone_y)));
    // 对齐参数
    m.set("predict_x", JsonValue::number(static_cast<double>(mouse.predict_x)));
    m.set("predict_y", JsonValue::number(static_cast<double>(mouse.predict_y)));
    m.set("smooth_x", JsonValue::number(static_cast<double>(mouse.smooth_x)));
    m.set("smooth_y", JsonValue::number(static_cast<double>(mouse.smooth_y)));
    m.set("output_deadzone", JsonValue::number(static_cast<double>(mouse.output_deadzone)));
    // 插件配置（pull_curve / recoil / personal_motion / personal_trajectory）
    JsonValue pc = JsonValue::object();
    pc.set("enabled", JsonValue::boolean(mouse.pull_curve.enabled));
    pc.set("strength", JsonValue::number(static_cast<double>(mouse.pull_curve.strength)));
    pc.set("jitter_px", JsonValue::number(static_cast<double>(mouse.pull_curve.jitter_px)));
    pc.set("min_distance", JsonValue::number(static_cast<double>(mouse.pull_curve.min_distance)));
    m.set("pull_curve", std::move(pc));
    // 持续提前量（continuous_lead）：字段名逐一对齐 yu 的 controller.continuous_lead_*，
    // 使「对标 yu」的配置可直搬、可逐字段比对（yu 默认 enabled=false 且这 6 个字段齐全）。
    JsonValue lc = JsonValue::object();
    lc.set("enabled", JsonValue::boolean(mouse.continuous_lead.enabled));
    lc.set("enter_distance", JsonValue::number(static_cast<double>(mouse.continuous_lead.enter_distance)));
    lc.set("scale", JsonValue::number(static_cast<double>(mouse.continuous_lead.scale)));
    lc.set("fade_in_ms", JsonValue::number(static_cast<double>(mouse.continuous_lead.fade_in_ms)));
    lc.set("fade_out_ms", JsonValue::number(static_cast<double>(mouse.continuous_lead.fade_out_ms)));
    lc.set("near_disable_ratio", JsonValue::number(static_cast<double>(mouse.continuous_lead.near_disable_ratio)));
    m.set("continuous_lead", std::move(lc));
    JsonValue pm = JsonValue::object();
    pm.set("enabled", JsonValue::boolean(mouse.personal_motion.enabled));
    pm.set("curve_blend", JsonValue::number(static_cast<double>(mouse.personal_motion.curve_blend)));
    pm.set("speed_blend", JsonValue::number(static_cast<double>(mouse.personal_motion.speed_blend)));
    pm.set("reaction_blend", JsonValue::number(static_cast<double>(mouse.personal_motion.reaction_blend)));
    pm.set("max_reaction_delay_ms", JsonValue::number(static_cast<double>(mouse.personal_motion.max_reaction_delay_ms)));
    JsonValue knots = JsonValue::array();
    for (const float knot : mouse.personal_motion.knots) {
        knots.push_back(JsonValue::number(static_cast<double>(knot)));
    }
    pm.set("knots", std::move(knots));
    m.set("personal_motion", std::move(pm));
    JsonValue pt = JsonValue::object();
    pt.set("enabled", JsonValue::boolean(mouse.personal_trajectory.enabled));
    pt.set("fitts_intercept_ms", JsonValue::number(static_cast<double>(mouse.personal_trajectory.fitts_intercept_ms)));
    pt.set("fitts_slope_ms_per_bit", JsonValue::number(static_cast<double>(mouse.personal_trajectory.fitts_slope_ms_per_bit)));
    pt.set("speed_scale", JsonValue::number(static_cast<double>(mouse.personal_trajectory.speed_scale)));
    pt.set("stability_scale", JsonValue::number(static_cast<double>(mouse.personal_trajectory.stability_scale)));
    pt.set("variation_scale", JsonValue::number(static_cast<double>(mouse.personal_trajectory.variation_scale)));
    pt.set("max_extra_px", JsonValue::number(static_cast<double>(mouse.personal_trajectory.max_extra_px)));
    pt.set("max_visual_variation_px", JsonValue::number(static_cast<double>(mouse.personal_trajectory.max_visual_variation_px)));
    pt.set("curve_time_constant_ms", JsonValue::number(static_cast<double>(mouse.personal_trajectory.curve_time_constant_ms)));
    pt.set("curve_rms_px", JsonValue::number(static_cast<double>(mouse.personal_trajectory.curve_rms_px)));
    pt.set("jitter_amp_px", JsonValue::number(static_cast<double>(mouse.personal_trajectory.jitter_amp_px)));
    pt.set("adaptive_enabled", JsonValue::boolean(mouse.personal_trajectory.adaptive_enabled));
    pt.set("min_error_px", JsonValue::number(static_cast<double>(mouse.personal_trajectory.min_error_px)));
    pt.set("urgent_error_px", JsonValue::number(static_cast<double>(mouse.personal_trajectory.urgent_error_px)));
    pt.set("urgent_speed_px_s", JsonValue::number(static_cast<double>(mouse.personal_trajectory.urgent_speed_px_s)));
    pt.set("max_target_age_ms", JsonValue::number(static_cast<double>(mouse.personal_trajectory.max_target_age_ms)));
    pt.set("capture_priority_ms", JsonValue::number(static_cast<double>(mouse.personal_trajectory.capture_priority_ms)));
    pt.set("transport_gain", JsonValue::number(static_cast<double>(mouse.personal_trajectory.transport_gain)));
    pt.set("direction_change_cosine", JsonValue::number(static_cast<double>(mouse.personal_trajectory.direction_change_cosine)));
    pt.set("response_px_per_count", JsonValue::number(static_cast<double>(mouse.personal_trajectory.response_px_per_count)));
    m.set("personal_trajectory", std::move(pt));
    JsonValue lk = JsonValue::object();
    lk.set("confirmation_frames", JsonValue::number(static_cast<double>(mouse.lock_confirm.confirmation_frames)));
    lk.set("enter_conf", JsonValue::number(static_cast<double>(mouse.lock_confirm.enter_conf)));
    lk.set("hold_conf", JsonValue::number(static_cast<double>(mouse.lock_confirm.hold_conf)));
    lk.set("instant_enter_enabled", JsonValue::boolean(mouse.lock_confirm.instant_enter_enabled));
    lk.set("instant_enter_dist", JsonValue::number(static_cast<double>(mouse.lock_confirm.instant_enter_dist)));
    lk.set("instant_enter_conf", JsonValue::number(static_cast<double>(mouse.lock_confirm.instant_enter_conf)));
    m.set("lock_confirm", std::move(lk));
    // 压枪（recoil）：开火期间下压补偿后坐力（12 参数语义，输出链基于 TTBOX 自身）
    JsonValue rc = JsonValue::object();
    rc.set("enabled", JsonValue::boolean(mouse.recoil.enabled));
    rc.set("hotkey", JsonValue::number(static_cast<double>(mouse.recoil.hotkey)));
    rc.set("hotkey2", JsonValue::number(static_cast<double>(mouse.recoil.hotkey2)));
    rc.set("hotkey_mode", JsonValue::number(static_cast<double>(mouse.recoil.hotkey_mode)));
    rc.set("only_when_target_visible", JsonValue::boolean(mouse.recoil.only_when_target_visible));
    rc.set("target_lost_release_ms", JsonValue::number(static_cast<double>(mouse.recoil.target_lost_release_ms)));
    rc.set("trigger_delay_enabled", JsonValue::boolean(mouse.recoil.trigger_delay_enabled));
    rc.set("trigger_delay_ms", JsonValue::number(static_cast<double>(mouse.recoil.trigger_delay_ms)));
    rc.set("strength", JsonValue::number(static_cast<double>(mouse.recoil.strength)));
    rc.set("speed", JsonValue::number(static_cast<double>(mouse.recoil.speed)));
    rc.set("humanize_enabled", JsonValue::boolean(mouse.recoil.humanize_enabled));
    rc.set("humanize_curve_strength", JsonValue::number(static_cast<double>(mouse.recoil.humanize_curve_strength)));
    rc.set("humanize_jitter_px", JsonValue::number(static_cast<double>(mouse.recoil.humanize_jitter_px)));
    rc.set("humanize_jitter_frequency", JsonValue::number(static_cast<double>(mouse.recoil.humanize_jitter_frequency)));
    m.set("recoil", std::move(rc));
    // 热键保护（hotkey_guard）：按一次 toggle_hotkey 切换「热键挂起」。
    // 挂起状态本身是运行时状态（AimThread 成员），**不落盘**；这里只持久化配置。
    JsonValue hg = JsonValue::object();
    hg.set("enabled", JsonValue::boolean(mouse.hotkey_guard.enabled));
    hg.set("toggle_hotkey", JsonValue::number(static_cast<double>(mouse.hotkey_guard.toggle_hotkey)));
    m.set("hotkey_guard", std::move(hg));
    JsonValue ha = JsonValue::object();
    ha.set("enabled", JsonValue::boolean(mouse.aim_point.head_aim.enabled));
    ha.set("head_offset_top_fraction", JsonValue::number(static_cast<double>(mouse.aim_point.head_aim.head_offset_top_fraction)));
    ha.set("head_height_fraction", JsonValue::number(static_cast<double>(mouse.aim_point.head_aim.head_height_fraction)));
    ha.set("safe_inset_fraction", JsonValue::number(static_cast<double>(mouse.aim_point.head_aim.safe_inset_fraction)));
    ha.set("max_lag_fraction", JsonValue::number(static_cast<double>(mouse.aim_point.head_aim.max_lag_fraction)));
    ha.set("max_lag_px", JsonValue::number(static_cast<double>(mouse.aim_point.head_aim.max_lag_px)));
    m.set("head_aim", std::move(ha));
    m.set("aim_offset_x", JsonValue::number(static_cast<double>(mouse.aim_point.aim_offset_x)));
    m.set("aim_offset_y", JsonValue::number(static_cast<double>(mouse.aim_point.aim_offset_y)));
    m.set("offset_x", JsonValue::number(static_cast<double>(mouse.aim_point.offset_x)));
    m.set("offset_y", JsonValue::number(static_cast<double>(mouse.aim_point.offset_y)));
    m.set("switch_delay_ms", JsonValue::number(static_cast<double>(mouse.aim_point.switch_delay_ms)));
    m.set("lost_grace_ms", JsonValue::number(static_cast<double>(mouse.lost_grace_ms)));
    m.set("calibrating", JsonValue::boolean(mouse.calibrating));
    m.set("calibration_bias_x", JsonValue::number(static_cast<double>(mouse.calibration_bias_x)));
    m.set("calibration_bias_y", JsonValue::number(static_cast<double>(mouse.calibration_bias_y)));
    // 自动标定产物：游戏灵敏度（每 count 对应画面多少 px）。压枪（recoil_px_per_count）
    // 与拟人化（response_px_per_count 同语义）都依赖此值，标定后必须落盘生效。
    m.set("gain_x_px_per_count", JsonValue::number(static_cast<double>(mouse.gain_x_px_per_count)));
    m.set("gain_y_px_per_count", JsonValue::number(static_cast<double>(mouse.gain_y_px_per_count)));
    JsonValue cos = JsonValue::array();
    for (const auto& c : mouse.aim_point.class_offsets) {
        JsonValue o = JsonValue::object();
        o.set("class_id", JsonValue::number(static_cast<double>(c.class_id)));
        o.set("offset_x", JsonValue::number(static_cast<double>(c.offset_x)));
        o.set("offset_y", JsonValue::number(static_cast<double>(c.offset_y)));
        o.set("priority", JsonValue::number(static_cast<double>(c.priority)));
        cos.push_back(std::move(o));
    }
    m.set("class_offsets", std::move(cos));
    root.set("mouse", std::move(m));

    JsonValue pv = JsonValue::object();
    pv.set("width", JsonValue::number(static_cast<double>(preview.width)));
    pv.set("height", JsonValue::number(static_cast<double>(preview.height)));
    pv.set("roi_w", JsonValue::number(static_cast<double>(preview.roi_w)));
    pv.set("roi_h", JsonValue::number(static_cast<double>(preview.roi_h)));
    pv.set("center_crop", JsonValue::boolean(preview.center_crop));
    pv.set("fps", JsonValue::number(static_cast<double>(preview.fps)));
    root.set("preview", std::move(pv));

    return root;
}

RuntimeProfile RuntimeProfile::from_json(const JsonValue& v) {
    RuntimeProfile p;
    if (!v.is_object()) return p;

    p.model_id = obj_str(v, "model_id", "");

    if (const JsonValue* c = v.find("capture"); c && c->is_object()) {
        // 退化值自愈：历史坏配置（如前端把 0 钳成 1 产生的 1×1）加载时纠正为 0=全帧，
        // 保证 Core 重启不会因一个坏字段而瞎跑（AI ROI 1px=推理停摆）或起不来。
        p.capture.width = sanitize_capture_roi(
            static_cast<uint32_t>(std::max<int64_t>(obj_int(*c, "width", 0), 0)));
        p.capture.height = sanitize_capture_roi(
            static_cast<uint32_t>(std::max<int64_t>(obj_int(*c, "height", 0), 0)));
        // offset 相对屏幕中心，允许负值
        p.capture.offset_x = static_cast<int32_t>(std::max<int64_t>(-100000, std::min<int64_t>(obj_int(*c, "offset_x", 0), 100000)));
        p.capture.offset_y = static_cast<int32_t>(std::max<int64_t>(-100000, std::min<int64_t>(obj_int(*c, "offset_y", 0), 100000)));
    }
    if (const JsonValue* i = v.find("inference"); i && i->is_object()) {
        p.inference.confidence = static_cast<float>(obj_num(*i, "confidence", 0.0));
        p.inference.iou = static_cast<float>(obj_num(*i, "iou", 0.0));
        p.inference.max_detections = static_cast<int>(obj_int(*i, "max_detections", 0));
        if (const JsonValue* cf = i->find("class_filter"); cf && cf->is_array()) {
            for (const auto& e : cf->as_array()) {
                if (e.is_number()) p.inference.class_filter.push_back(static_cast<int>(e.as_int()));
            }
        }
    }
    if (const JsonValue* gf = v.find("geometry_filter"); gf && gf->is_object()) {
        p.geometry_filter.enabled = obj_bool(*gf, "enabled", false);
        p.geometry_filter.min_head_conf = static_cast<float>(obj_num(*gf, "min_head_conf", 0.18));
        p.geometry_filter.min_body_conf = static_cast<float>(obj_num(*gf, "min_body_conf", 0.26));
        p.geometry_filter.paired_head_min_conf = static_cast<float>(obj_num(*gf, "paired_head_min_conf", 0.20));
        p.geometry_filter.head_only_min_conf = static_cast<float>(obj_num(*gf, "head_only_min_conf", 0.75));
        p.geometry_filter.head_only_center_max_px = static_cast<float>(obj_num(*gf, "head_only_center_max_px", 175.0));
        p.geometry_filter.min_body_width_px = static_cast<float>(obj_num(*gf, "min_body_width_px", 8.0));
        p.geometry_filter.min_body_height_px = static_cast<float>(obj_num(*gf, "min_body_height_px", 26.0));
        p.geometry_filter.reject_border = obj_bool(*gf, "border_reject_enabled", true);
    }
    if (const JsonValue* f = v.find("fov"); f && f->is_object()) {
        p.fov.enabled = obj_bool(*f, "enabled", false);
        p.fov.shape = (obj_int(*f, "shape", 0) == 1) ? FovShape::kRect : FovShape::kCircle;
        p.fov.radius = static_cast<float>(obj_num(*f, "radius", 0.5));
        p.fov.center_x = static_cast<float>(obj_num(*f, "center_x", 0.5));
        p.fov.center_y = static_cast<float>(obj_num(*f, "center_y", 0.5));
    }
    // A10：鼠标 AI 注入配置
    if (const JsonValue* m = v.find("mouse"); m && m->is_object()) {
        p.mouse.enabled = obj_bool(*m, "enabled", false);
        p.mouse.aim_hotkey = static_cast<uint8_t>(obj_int(*m, "aim_hotkey", 2));
        p.mouse.aim_hotkey2 = static_cast<uint8_t>(obj_int(*m, "aim_hotkey2", 0));
        p.mouse.aim_hotkey_mode = aim::mouse_hotkey_mode_from_string(obj_str(*m, "aim_hotkey_mode", "any").c_str());
        p.mouse.fov_range = static_cast<float>(obj_num(*m, "fov_range", 1.0));
        p.mouse.confidence = static_cast<float>(obj_num(*m, "confidence", 0.25));
        p.mouse.kp_x = static_cast<float>(obj_num(*m, "kp_x", 25.0));
                p.mouse.kp_y = static_cast<float>(obj_num(*m, "kp_y", 25.0));
                p.mouse.kd_x = static_cast<float>(obj_num(*m, "kd_x", 25.0));
                p.mouse.kd_y = static_cast<float>(obj_num(*m, "kd_y", 25.0));
        p.mouse.fov_mode = obj_bool(*m, "fov_mode", false);
        p.mouse.hfov = static_cast<float>(obj_num(*m, "hfov", 83.105));
        p.mouse.vfov = static_cast<float>(obj_num(*m, "vfov", 53.0));
        p.mouse.move_speed_x = static_cast<float>(obj_num(*m, "move_speed_x", 500.0));
        p.mouse.move_speed_y = static_cast<float>(obj_num(*m, "move_speed_y", 500.0));
        p.mouse.rate_x = static_cast<float>(obj_num(*m, "rate_x", 0.3));
                p.mouse.rate_y = static_cast<float>(obj_num(*m, "rate_y", 0.3));
        p.mouse.sensitivity = static_cast<float>(obj_num(*m, "sensitivity", 1.0));
        p.mouse.output_scale = static_cast<float>(obj_num(*m, "output_scale", 1.0));
        p.mouse.deadzone_x = static_cast<float>(obj_num(*m, "deadzone_x", 1.0));
        p.mouse.deadzone_y = static_cast<float>(obj_num(*m, "deadzone_y", 1.0));
        // 对齐参数
        p.mouse.predict_x = static_cast<float>(obj_num(*m, "predict_x", 3.0));
                p.mouse.predict_y = static_cast<float>(obj_num(*m, "predict_y", 0.0));
        p.mouse.smooth_x = static_cast<float>(obj_num(*m, "smooth_x", 9900.0));
        p.mouse.smooth_y = static_cast<float>(obj_num(*m, "smooth_y", 9900.0));
        p.mouse.output_deadzone = static_cast<float>(obj_num(*m, "output_deadzone", 1.0));
    // 插件配置（pull_curve / recoil / personal_motion / personal_trajectory）
        if (const JsonValue* pc = m->find("pull_curve"); pc && pc->is_object()) {
            p.mouse.pull_curve.enabled = obj_bool(*pc, "enabled", true);
            p.mouse.pull_curve.strength = static_cast<float>(obj_num(*pc, "strength", 0.8));
            p.mouse.pull_curve.jitter_px = static_cast<float>(obj_num(*pc, "jitter_px", 3.0));
            p.mouse.pull_curve.min_distance = static_cast<float>(obj_num(*pc, "min_distance", 80.0));
        }
        // 持续提前量：缺字段一律取"保守默认"（enabled=false ⇒ 不动输出），
        // 故旧配置/旧预设文件加载后行为与本功能加入前完全一致（向后兼容）。
        if (const JsonValue* lc = m->find("continuous_lead"); lc && lc->is_object()) {
            p.mouse.continuous_lead.enabled = obj_bool(*lc, "enabled", false);
            p.mouse.continuous_lead.enter_distance = static_cast<float>(obj_num(*lc, "enter_distance", 150.0));
            p.mouse.continuous_lead.scale = static_cast<float>(obj_num(*lc, "scale", 0.5));
            p.mouse.continuous_lead.fade_in_ms = static_cast<float>(obj_num(*lc, "fade_in_ms", 300.0));
            p.mouse.continuous_lead.fade_out_ms = static_cast<float>(obj_num(*lc, "fade_out_ms", 300.0));
            p.mouse.continuous_lead.near_disable_ratio = static_cast<float>(obj_num(*lc, "near_disable_ratio", 0.66));
        }
        if (const JsonValue* pm = m->find("personal_motion"); pm && pm->is_object()) {
            p.mouse.personal_motion.enabled = obj_bool(*pm, "enabled", false);
            p.mouse.personal_motion.curve_blend = static_cast<float>(obj_num(*pm, "curve_blend", 1.0));
            p.mouse.personal_motion.speed_blend = static_cast<float>(obj_num(*pm, "speed_blend", 1.0));
            p.mouse.personal_motion.reaction_blend = static_cast<float>(obj_num(*pm, "reaction_blend", 0.7));
            p.mouse.personal_motion.max_reaction_delay_ms = static_cast<float>(obj_num(*pm, "max_reaction_delay_ms", 250.0));
            if (const JsonValue* knots = pm->find("knots"); knots && knots->is_array()) {
                for (const auto& item : knots->as_array()) {
                    if (item.is_number() && p.mouse.personal_motion.knots.size() < 32) {
                        p.mouse.personal_motion.knots.push_back(static_cast<float>(item.as_number()));
                    }
                }
            }
        }
        if (const JsonValue* pt = m->find("personal_trajectory"); pt && pt->is_object()) {
            p.mouse.personal_trajectory.enabled = obj_bool(*pt, "enabled", false);
            p.mouse.personal_trajectory.fitts_intercept_ms = static_cast<float>(obj_num(*pt, "fitts_intercept_ms", 120.0));
            p.mouse.personal_trajectory.fitts_slope_ms_per_bit = static_cast<float>(obj_num(*pt, "fitts_slope_ms_per_bit", 85.0));
            p.mouse.personal_trajectory.speed_scale = static_cast<float>(obj_num(*pt, "speed_scale", 1.0));
            p.mouse.personal_trajectory.stability_scale = static_cast<float>(obj_num(*pt, "stability_scale", 1.0));
            p.mouse.personal_trajectory.variation_scale = static_cast<float>(obj_num(*pt, "variation_scale", 1.0));
            p.mouse.personal_trajectory.max_extra_px = static_cast<float>(obj_num(*pt, "max_extra_px", 2.0));
            p.mouse.personal_trajectory.max_visual_variation_px = static_cast<float>(obj_num(*pt, "max_visual_variation_px", 1.5));
            p.mouse.personal_trajectory.curve_time_constant_ms = static_cast<float>(obj_num(*pt, "curve_time_constant_ms", 32.0));
            p.mouse.personal_trajectory.curve_rms_px = static_cast<float>(obj_num(*pt, "curve_rms_px", 0.8));
            p.mouse.personal_trajectory.jitter_amp_px = static_cast<float>(obj_num(*pt, "jitter_amp_px", 0.20));
            p.mouse.personal_trajectory.adaptive_enabled = obj_bool(*pt, "adaptive_enabled", true);
            p.mouse.personal_trajectory.min_error_px = static_cast<float>(obj_num(*pt, "min_error_px", 18.0));
            p.mouse.personal_trajectory.urgent_error_px = static_cast<float>(obj_num(*pt, "urgent_error_px", 72.0));
            p.mouse.personal_trajectory.urgent_speed_px_s = static_cast<float>(obj_num(*pt, "urgent_speed_px_s", 520.0));
            p.mouse.personal_trajectory.max_target_age_ms = static_cast<float>(obj_num(*pt, "max_target_age_ms", 18.0));
            p.mouse.personal_trajectory.capture_priority_ms = static_cast<float>(obj_num(*pt, "capture_priority_ms", 5.0));
            p.mouse.personal_trajectory.transport_gain = static_cast<float>(obj_num(*pt, "transport_gain", 0.16));
            p.mouse.personal_trajectory.direction_change_cosine = static_cast<float>(obj_num(*pt, "direction_change_cosine", 0.15));
            p.mouse.personal_trajectory.response_px_per_count = static_cast<float>(obj_num(*pt, "response_px_per_count", 0.65));
        }
        if (const JsonValue* lk = m->find("lock_confirm"); lk && lk->is_object()) {
            p.mouse.lock_confirm.confirmation_frames = static_cast<int>(obj_int(*lk, "confirmation_frames", 1));
            p.mouse.lock_confirm.enter_conf = static_cast<float>(obj_num(*lk, "enter_conf", 0.0));
            p.mouse.lock_confirm.hold_conf = static_cast<float>(obj_num(*lk, "hold_conf", 0.0));
            p.mouse.lock_confirm.instant_enter_enabled = obj_bool(*lk, "instant_enter_enabled", true);
            p.mouse.lock_confirm.instant_enter_dist = static_cast<float>(obj_num(*lk, "instant_enter_dist", 105.0));
            p.mouse.lock_confirm.instant_enter_conf = static_cast<float>(obj_num(*lk, "instant_enter_conf", 0.50));
        }
        // 压枪（recoil）解析：缺失字段用默认值（全部关/零输出，保持旧行为）
        if (const JsonValue* rk = m->find("recoil"); rk && rk->is_object()) {
            p.mouse.recoil.enabled = obj_bool(*rk, "enabled", false);
            p.mouse.recoil.hotkey = static_cast<int>(obj_int(*rk, "hotkey", 1));
            p.mouse.recoil.hotkey2 = static_cast<int>(obj_int(*rk, "hotkey2", 0));
            p.mouse.recoil.hotkey_mode = static_cast<int>(obj_int(*rk, "hotkey_mode", 1));
            p.mouse.recoil.only_when_target_visible = obj_bool(*rk, "only_when_target_visible", true);
            p.mouse.recoil.target_lost_release_ms = static_cast<float>(obj_num(*rk, "target_lost_release_ms", 200.0));
            p.mouse.recoil.trigger_delay_enabled = obj_bool(*rk, "trigger_delay_enabled", false);
            p.mouse.recoil.trigger_delay_ms = static_cast<float>(obj_num(*rk, "trigger_delay_ms", 120.0));
            p.mouse.recoil.strength = static_cast<float>(obj_num(*rk, "strength", 0.0));
            p.mouse.recoil.speed = static_cast<float>(obj_num(*rk, "speed", 1.0));
            p.mouse.recoil.humanize_enabled = obj_bool(*rk, "humanize_enabled", true);
            p.mouse.recoil.humanize_curve_strength = static_cast<float>(obj_num(*rk, "humanize_curve_strength", 0.45));
            p.mouse.recoil.humanize_jitter_px = static_cast<float>(obj_num(*rk, "humanize_jitter_px", 0.25));
            p.mouse.recoil.humanize_jitter_frequency = static_cast<float>(obj_num(*rk, "humanize_jitter_frequency", 8.0));
        }
        // 热键保护（hotkey_guard）解析：缺字段一律取"保守默认"（enabled=false ⇒ 不翻转、位图原样透传），
        // 故旧配置/旧预设文件加载后行为与本功能加入前完全一致（向后兼容）。
        // toggle_hotkey 只取低 5 位（鼠标五键位图：左1 右2 中4 侧8 侧16），越界位一律掩掉。
        if (const JsonValue* hg = m->find("hotkey_guard"); hg && hg->is_object()) {
            p.mouse.hotkey_guard.enabled = obj_bool(*hg, "enabled", false);
            p.mouse.hotkey_guard.toggle_hotkey =
                static_cast<uint8_t>(obj_int(*hg, "toggle_hotkey", 4) & 0x1F);
        }
        if (const JsonValue* ha = m->find("head_aim"); ha && ha->is_object()) {
            auto obj_num2 = [&](const char* k, double d) {
                const JsonValue* v = ha->find(k);
                return (v && v->is_number()) ? v->as_number() : d;
            };
            const JsonValue* en = ha->find("enabled");
            p.mouse.aim_point.head_aim.enabled = (en && en->is_bool()) ? en->as_bool(false) : false;
            p.mouse.aim_point.head_aim.head_offset_top_fraction = static_cast<float>(obj_num2("head_offset_top_fraction", 0.04));
            p.mouse.aim_point.head_aim.head_height_fraction = static_cast<float>(obj_num2("head_height_fraction", 0.28));
            p.mouse.aim_point.head_aim.safe_inset_fraction = static_cast<float>(obj_num2("safe_inset_fraction", 0.12));
            p.mouse.aim_point.head_aim.max_lag_fraction = static_cast<float>(obj_num2("max_lag_fraction", 0.18));
            p.mouse.aim_point.head_aim.max_lag_px = static_cast<float>(obj_num2("max_lag_px", 1.25));
        }
        p.mouse.aim_point.aim_offset_x = static_cast<float>(obj_num(*m, "aim_offset_x", 0.0));
        p.mouse.aim_point.aim_offset_y = static_cast<float>(obj_num(*m, "aim_offset_y", 0.0));
        p.mouse.aim_point.offset_x = static_cast<float>(obj_num(*m, "offset_x", 0.5));
        p.mouse.aim_point.offset_y = static_cast<float>(obj_num(*m, "offset_y", 0.5));
        p.mouse.aim_point.switch_delay_ms = static_cast<int>(obj_int(*m, "switch_delay_ms", 30));
        p.mouse.lost_grace_ms = static_cast<float>(obj_num(*m, "lost_grace_ms", 78.0));
        p.mouse.calibrating = obj_bool(*m, "calibrating", false);
        p.mouse.calibration_bias_x = static_cast<float>(obj_num(*m, "calibration_bias_x", 0.0));
        p.mouse.calibration_bias_y = static_cast<float>(obj_num(*m, "calibration_bias_y", 0.0));
        p.mouse.gain_x_px_per_count = static_cast<float>(obj_num(*m, "gain_x_px_per_count", 0.65));
        p.mouse.gain_y_px_per_count = static_cast<float>(obj_num(*m, "gain_y_px_per_count", 0.65));
        if (const JsonValue* co = m->find("class_offsets"); co && co->is_array()) {
            for (const auto& e : co->as_array()) {
                if (!e.is_object()) continue;
                aim::ClassOffset c;
                c.class_id = static_cast<int>(obj_int(e, "class_id", 0));
                c.offset_x = static_cast<float>(obj_num(e, "offset_x", 0.5));
                c.offset_y = static_cast<float>(obj_num(e, "offset_y", 0.5));
                c.priority = static_cast<int>(obj_int(e, "priority", 0));
                p.mouse.aim_point.class_offsets.push_back(c);
            }
        }
    }
    if (const JsonValue* pv = v.find("preview"); pv && pv->is_object()) {
        p.preview.width = static_cast<uint32_t>(std::max<int64_t>(obj_int(*pv, "width", 640), 1));
        p.preview.height = static_cast<uint32_t>(std::max<int64_t>(obj_int(*pv, "height", 640), 1));
        p.preview.roi_w = static_cast<uint32_t>(std::max<int64_t>(obj_int(*pv, "roi_w", 640), 1));
        p.preview.roi_h = static_cast<uint32_t>(std::max<int64_t>(obj_int(*pv, "roi_h", 640), 1));
        p.preview.center_crop = obj_bool(*pv, "center_crop", true);
        p.preview.fps = static_cast<uint32_t>(std::max<int64_t>(obj_int(*pv, "fps", 0), 0));
    }
    // P-ZC-1：缺 video 段时保持默认值（crop=0 沿用全局、zero_copy_input=true）。
    // 注意 zero_copy_input 的默认是 true：这一项本就是为了让已装机设备
    // （全局配置里 rknn_external_dma_input:false 且 OTA 覆盖不到）能在面板打开零拷贝。
    // 但"拿来即用"的前提是 profile 里**真的有**这个键 —— 老配置没有 video 段时，
    // 仍以全局配置为准，见 Application::apply_video_profile 的三态处理。
    p.video.zero_copy_input_set = false;
    if (const JsonValue* vd = v.find("video"); vd && vd->is_object()) {
        p.video.crop_width = static_cast<uint32_t>(std::max<int64_t>(obj_int(*vd, "crop_width", 0), 0));
        p.video.crop_height = static_cast<uint32_t>(std::max<int64_t>(obj_int(*vd, "crop_height", 0), 0));
        if (vd->find("zero_copy_input")) {
            p.video.zero_copy_input = obj_bool(*vd, "zero_copy_input", true);
            p.video.zero_copy_input_set = true;
        }
    }
    return p;
}

RuntimeProfile RuntimeProfile::from_json_file(const std::string& path,
                                              std::string* error) {
    auto res = json_parse_file(path);
    if (!res.ok) {
        if (error) *error = "解析失败(" + path + "): " + res.error;
        return {};
    }
    return from_json(res.value);
}

}  // namespace ttbox::core
