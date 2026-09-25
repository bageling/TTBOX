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
#include "output/OutputGate.hpp"

#include <utility>
#include <chrono>

#include "common/Logger.hpp"
#include "model/RuntimeProfile.hpp"
#include "output/LocalHidBackend.hpp"
#include "output/MouseControlClient.hpp"

namespace {
class UsbProxyBackend final : public ttbox::core::output::IOutputBackend {
public:
    explicit UsbProxyBackend(std::string path) : client_(std::move(path)) {}
    bool connect(std::string* e = nullptr) override { return client_.connect(e); }
    void disconnect() override { client_.disconnect(); }
    bool reconnect(std::string* e = nullptr) override { disconnect(); ++health_.reconnect_count; return connect(e); }
    ttbox::core::output::BackendHealth health() const override {
        const auto t = client_.telemetry();
        health_.state = t.connected ? ttbox::core::output::BackendState::kConnected : ttbox::core::output::BackendState::kDisconnected;
        health_.socket_write_ok = t.socket_write_ok;
        health_.socket_write_fail = t.socket_write_fail;
        health_.send_count = t.send_count;
        health_.last_dx = t.last_dx;
        health_.last_dy = t.last_dy;
        health_.last_wheel = t.last_wheel;
        health_.last_timestamp_us = t.last_timestamp_us;
        return health_;
    }
    bool mouse_move(int32_t x, int32_t y, int32_t w = 0) override {
        if (!gate_allows()) return false;
        std::string error;
        if (!client_.send_move(x, y, w, &error)) { health_.detail = error; ++health_.send_fail; return false; }
        ++health_.send_ok;
        health_.last_send_ok_us = static_cast<int64_t>(std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
        return true;
    }
    // 按键注入（自动扳机）：此前只做 gate 检查、一个字节都没发出去 ⇒ 面板上的
    // 「自动扳机」开了也点不动（TriggerController 决策全被丢在后端门口）。
    bool mouse_button(uint8_t button, uint8_t action) override {
        if (!gate_allows()) return false;
        std::string error;
        if (!client_.send_button(button, action, &error)) {
            health_.detail = error;
            ++health_.send_fail;
            return false;
        }
        ++health_.send_ok;
        return true;
    }
    bool mouse_click(uint8_t b) override { return mouse_button(b, ttbox::core::output::kActClick); }
    const char* name() const override { return "usb_proxy_mouse_control"; }
private:
    ttbox::core::output::MouseControlClient client_;
    mutable ttbox::core::output::BackendHealth health_;
};
}


namespace ttbox::core::output {

// 发送前 Gate：判据已抽到 OutputGate.hpp（**单一权威源**），与 AiboxHidOutput::send 共用同一份。
// 此前两处各写一遍然后漂移（aibox 侧缺标定豁免、两侧都在"按键源没绑"时 fail-open），
// 靠注释互相保证"口径一致"是不可靠的，改为共用函数。
bool IOutputBackend::gate_allows() const {
    return output_gate_allows(OutputGateInputs{enabled_, config_source_, button_source_});
}

OutputBackend::~OutputBackend() = default;

bool OutputBackend::configure(const Params& p, std::string* error) {
    params_ = p;
    backend_.reset();

    std::unique_ptr<IOutputBackend> backend;
    if (p.kind == "usb_proxy") {
        backend = std::make_unique<UsbProxyBackend>(p.proxy_socket_path);
    } else if (p.kind == "local_hid" || p.kind.empty()) {
        backend = std::make_unique<LocalHidBackend>(p.hidg_path);
    } else {
        if (error) *error = "未知输出后端: " + p.kind;
        return false;
    }
    backend->set_enabled(p.enabled);
    backend->set_button_source(p.button_source);
    backend->set_config_source(p.runtime_config);
    backend_ = std::move(backend);

    // 预连接：链路建立与注入门控解耦（connect ≠ 注入）。
    // 注入放行仍由 gate_allows() + 热键决定，这里只把管道先通上，
    // 让 health/遥测能反映真实链路，并消除首次按热键的建连延迟。
    if (p.enabled) {
        std::string connect_error;
        if (!backend_->connect(&connect_error)) {
            TTBOX_LOG_WARN("输出后端预连接失败（保持惰性重连）: " + connect_error);
        }
    }
    return true;
}

// ---------------------------------------------------------------------------
// IHidOutput 兼容：AimThread 仍调用 send(OutputAction)，零改动。
// 热路径：无分配、无锁、无日志；Gate 判定在后端内部（与 AiboxHidOutput 相同）。
// ---------------------------------------------------------------------------
bool OutputBackend::mouse_button(uint8_t button, uint8_t action) {
    if (!backend_) return false;
    const uint8_t index = button_index_from_mask(button);
    if (index == 0) return false;   // 空掩码 = 不点任何键（fail-closed，绝不猜成"点左键"）
    return backend_->mouse_button(index, action);
}

bool OutputBackend::mouse_click(uint8_t button) {
    return mouse_button(button, ttbox::core::output::kActClick);
}

bool OutputBackend::send(const OutputAction& action) {
    if (!backend_) return false;
    // 行为与原 AiboxHidOutput 一致：整帧写入（含零移动帧=复位帧）；
    // 按键状态本机后端不注入（按钮接口保留给网络/串口后端）。
    return backend_->mouse_move(action.move_x, action.move_y, 0);
}

BackendHealth OutputBackend::health() const {
    return backend_ ? backend_->health() : BackendHealth{};
}

const char* OutputBackend::backend_name() const {
    return backend_ ? backend_->name() : "none";
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
