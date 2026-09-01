// OutputBackend.cpp — 设备选择器 + IHidOutput 兼容层
/*
 * TTBOX 文件说明
 *
 * 文件：OutputBackend.cpp
 *
 * 作用：
 *   将瞄准指令转换成真实的鼠标/外设输出。
 *
 * 小白理解：
 *   AimThread 算出了"应该往右移动 10 个像素"，
 *   OutputBackend 负责把这个指令发给 HID 设备，
 *   HID 设备再通过 USB 线告诉电脑："鼠标向右动 10 个像素"。
 *
 * 注意：
 *   本注释仅用于说明代码，不改变程序逻辑。
 */

#include "output/OutputBackend.hpp"

#include <utility>

#include "model/RuntimeProfile.hpp"
#include "output/LocalHidBackend.hpp"

namespace ttbox::core::output {

// 发送前 Gate：判定顺序与 AiboxHidOutput::send 完全一致（fail-closed）。
bool IOutputBackend::gate_allows() const {
    if (!enabled_) return false;
    if (config_source_) {
        auto p = config_source_->snapshot();
        if (!p) return false;
        const bool calibrating = p->mouse.calibrating;
        if (!p->mouse.enabled && !calibrating) return false;
        const uint16_t mask = static_cast<uint16_t>(
            static_cast<uint16_t>(p->mouse.aim_hotkey) |
            static_cast<uint16_t>(p->mouse.aim_hotkey2));
        if (mask == 0 && !calibrating) return false;  // 配置缺失 → 禁止注入
        // 标定模式：无视热键放行（标定线程自己注入运动帧，物理鼠标不参与）
        if (calibrating) return true;
        if (button_source_ && (button_source_->load(std::memory_order_acquire) & mask) == 0) {
            return false;
        }
    } else if (button_source_) {
        return false;  // 无配置源 → fail-closed
    }
    return true;
}

OutputBackend::~OutputBackend() = default;

bool OutputBackend::configure(const Params& p, std::string* error) {
    params_ = p;
    backend_.reset();

    if (p.kind == "local_hid" || p.kind.empty()) {
        auto b = std::make_unique<LocalHidBackend>(p.hidg_path);
        b->set_enabled(p.enabled);
        b->set_button_source(p.button_source);
        b->set_config_source(p.runtime_config);
        backend_ = std::move(b);
        return true;
    }
    if (error) *error = "未知输出后端: " + p.kind;
    return false;
}

// ---------------------------------------------------------------------------
// IHidOutput 兼容：AimThread 仍调用 send(OutputAction)，零改动。
// 热路径：无分配、无锁、无日志；Gate 判定在后端内部（与 AiboxHidOutput 相同）。
// ---------------------------------------------------------------------------
bool OutputBackend::send(const OutputAction& action) {
    if (!backend_) return false;
    // 行为与原 AiboxHidOutput 一致：整帧写入（含零移动帧=复位帧）；
    // 按键状态本机后端不注入（按钮接口保留给网络/串口后端）。
    return backend_->mouse_move(action.move_x, action.move_y, 0);
}

void OutputBackend::set_enabled(bool enabled) {
    if (backend_) backend_->set_enabled(enabled);
}

void OutputBackend::set_button_source(std::atomic<uint16_t>* source) {
    if (backend_) backend_->set_button_source(source);
}

void OutputBackend::set_config_source(RuntimeConfig* config) {
    if (backend_) backend_->set_config_source(config);
}

}  // namespace ttbox::core::output