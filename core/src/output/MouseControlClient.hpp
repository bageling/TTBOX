// MouseControlClient.hpp — usb-proxy 官方 mouse-control 薄客户端
// 只负责 MOVE 包编码与 cmd.sock 写入，不直接访问 HID/raw-gadget。
#pragma once

#include <atomic>
#include <cstdint>
#include <string>
#include <vector>

#include "common/Paths.hpp"   // A-PATH-5：mouse cmd.sock 默认单点真源

namespace ttbox::core::output {

struct MouseControlTelemetry {
    bool connected = false;
    uint64_t socket_write_ok = 0;
    uint64_t socket_write_fail = 0;
    uint64_t send_count = 0;
    uint64_t button_count = 0;      // 按键命令成功投递数（诊断：扳机到底点没点出去）
    int32_t last_dx = 0;
    int32_t last_dy = 0;
    int32_t last_wheel = 0;
    uint64_t last_timestamp_us = 0;
};

class MouseControlClient {
public:
    explicit MouseControlClient(std::string socket_path = paths::kMouseCmdSocketDefault)
        : socket_path_(std::move(socket_path)) {}
    ~MouseControlClient();

    MouseControlClient(const MouseControlClient&) = delete;
    MouseControlClient& operator=(const MouseControlClient&) = delete;

    static std::vector<uint8_t> encode_move(uint32_t request_id, int32_t dx, int32_t dy, int32_t wheel = 0);
    // BUTTON_CMD（type=5）：payload = <B button编号(1..8), B action(down/up/click)>
    static std::vector<uint8_t> encode_button(uint32_t request_id, uint8_t button, uint8_t action);
    bool connect(std::string* error = nullptr);
    void disconnect();
    bool send_move(int32_t dx, int32_t dy, int32_t wheel = 0, std::string* error = nullptr);
    bool send_button(uint8_t button, uint8_t action, std::string* error = nullptr);
    MouseControlTelemetry telemetry() const;
    bool connected() const { return fd_ >= 0; }
    uint32_t next_request_id() const { return next_request_id_; }

private:
    std::string socket_path_;
    int fd_ = -1;
    uint32_t next_request_id_ = 1;
    std::atomic<uint64_t> socket_write_ok_{0};
    std::atomic<uint64_t> socket_write_fail_{0};
    std::atomic<uint64_t> send_count_{0};
    std::atomic<uint64_t> button_count_{0};
    std::atomic<int32_t> last_dx_{0};
    std::atomic<int32_t> last_dy_{0};
    std::atomic<int32_t> last_wheel_{0};
    std::atomic<uint64_t> last_timestamp_us_{0};
};

}  // namespace ttbox::core::output
