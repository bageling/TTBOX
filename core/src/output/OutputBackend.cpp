// OutputBackend.cpp — 设备选择器 + IHidOutput 兼容层
#include "output/OutputBackend.hpp"

#include <utility>

#include "model/RuntimeProfile.hpp"
#include "output/KmboxNetBackend.hpp"
#include "output/LocalHidBackend.hpp"

namespace ttbox::core::output {

// 发送前 Gate：判定顺序与 AiboxHidOutput::send 完全一致（fail-closed）。
bool IOutputBackend::gate_allows() const {
    if (!enabled_) return false;
    if (config_source_) {
        auto p = config_source_->snapshot();
        if (!p) return false;
        if (!p->mouse.enabled) return false;
        const uint16_t mask = static_cast<uint16_t>(
            static_cast<uint16_t>(p->mouse.aim_hotkey) |
            static_cast<uint16_t>(p->mouse.aim_hotkey2));
        if (mask == 0) return false;  // 配置缺失 → 禁止注入
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
    if (p.kind == "kmboxnet") {
        // KmboxNetBackend：配置结构/会话流程/校验规则已实证
        // （docs/research/YU_OUTPUT_BACKEND_RESEARCH.md 3.x）；UDP 报文布局 UNVERIFIED。
        // 报文布局就绪前 backend 可用但发送返回 not-ready（不伪造协议）。
        KmboxNetBackend::Options opt;
        opt.ip = p.kmboxnet_ip;
        opt.port = p.kmboxnet_port;
        opt.monitor_port = p.kmboxnet_monitor_port;
        opt.timeout_ms = p.kmboxnet_timeout_ms;
        opt.uuid = p.kmboxnet_uuid;
        opt.encrypted = p.kmboxnet_encrypted;
        auto b = std::make_unique<KmboxNetBackend>(opt);
        b->set_enabled(p.enabled);
        b->set_button_source(p.button_source);
        b->set_config_source(p.runtime_config);
        backend_ = std::move(b);
        return true;
    }
    if (p.kind == "makcu" || p.kind == "ferrum" || p.kind == "kmboxb") {
        if (error) *error = p.kind + " backend 尚未接入（需串口设备真机验证）";
        return false;
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