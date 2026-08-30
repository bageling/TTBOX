// KmboxNetBackend.hpp — KMBOX NET 网络盒后端（UDP）
//
// 依据：docs/research/YU_OUTPUT_BACKEND_RESEARCH.md 3.x（YU 真机实证）。
// 已实证：
//   - 配置结构：ip / port / monitor_port(默认5001) / timeout_ms(默认300) / uuid(8 hex) / encrypted
//   - 会话流：send_connect → wait_for_ack → send_ping 保活 → send_mouse_command(seq, SoftMouse)
//     → button_down/up/click → monitor 通道（盒子状态上报）
//   - 校验规则（daemon 错误串实证）：IP 非空/合法；monitor_port 0 或 1024-65535；
//     uuid 必须恰好 8 个 hex；超时/空响应判定
// 未实证（UNVERIFIED）：
//   - UDP 报文具体字节布局（YU daemon 为发布二进制；本机无 kmbox 盒子可抓包）
//   - SoftMouse 字段内存布局
//   因此报文生成器 BuildMovePacket/BuildButtonPacket 目前返回 not-ready，
//   待真机盒子抓包/字节对齐后填充 —— 不引用网络资料猜测。
#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>

#include "output/OutputBackend.hpp"

namespace ttbox::core::output {

class KmboxNetBackend final : public IOutputBackend {
public:
    struct Options {
        std::string ip;              // 空=禁用
        uint16_t port = 0;           // 0=协议默认（待实证）
        uint16_t monitor_port = 5001; // 盒子状态上报端口（实证默认）
        uint32_t timeout_ms = 300;   // 实证默认
        std::string uuid;            // 8 hex（实证校验）
        bool encrypted = false;      // 加密通道（实证开关；加密算法未实证）
    };

    explicit KmboxNetBackend(Options opt) : opt_(std::move(opt)) {}
    ~KmboxNetBackend() override { disconnect(); }

    bool connect(std::string* error = nullptr) override;
    void disconnect() override;
    bool reconnect(std::string* error = nullptr) override;
    BackendHealth health() const override;

    bool mouse_move(int32_t dx, int32_t dy, int32_t wheel = 0) override;
    bool mouse_button(uint8_t button, uint8_t action) override;
    bool mouse_click(uint8_t button) override;

    const char* name() const override { return "kmboxnet"; }

    // 配置校验（实证规则）；connect 前调用
    bool validate(std::string* error = nullptr) const;

private:
    // ---- 会话（实证流程）----
    bool send_connect(std::string* error);      // UDP → 盒子 command 端口
    bool wait_for_ack(uint32_t seq, uint32_t timeout_ms);
    bool send_ping(std::string* error);
    bool send_command(uint32_t seq, const std::string& raw, bool encrypt, std::string* error);

    // ---- 报文生成（UNVERIFIED：待真机抓包填充）----
    // 当前实现返回空串并置 not_ready_，不猜测字节布局。
    std::string build_connect_packet(uint32_t seq);
    std::string build_ping_packet(uint32_t seq);
    std::string build_move_packet(uint32_t seq, int32_t dx, int32_t dy, int32_t wheel);
    std::string build_button_packet(uint32_t seq, uint8_t button, uint8_t action);

    Options opt_;
    std::atomic<bool> connected_{false};
    std::atomic<bool> not_ready_{true};  // 报文布局未实证 → 拒绝发送
    std::atomic<uint32_t> seq_{0};

    // UDP socket（POSIX only；Windows 占位）
    int fd_ = -1;
    mutable std::mutex io_mutex_;
    mutable BackendHealth health_;
};

}  // namespace ttbox::core::output