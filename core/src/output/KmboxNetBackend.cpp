// KmboxNetBackend.cpp — KMBOX NET UDP 后端
#include "output/KmboxNetBackend.hpp"

#if !defined(_WIN32)
#include <cerrno>
#include <cstring>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

#include <chrono>
#include <cctype>

namespace ttbox::core::output {

// ---------------------------------------------------------------------------
// 配置校验（实证规则：daemon 错误串）
// ---------------------------------------------------------------------------
bool KmboxNetBackend::validate(std::string* error) const {
    if (opt_.ip.empty()) {
        if (error) *error = "kmboxNet IP is empty";
        return false;
    }
    // 简单合法性：非空且不含明显非法字符（完整校验待网络层）
    for (char c : opt_.ip) {
        if (!std::isalnum(static_cast<unsigned char>(c)) && c != '.' && c != ':' && c != '-') {
            if (error) *error = "kmboxNet IP is invalid";
            return false;
        }
    }
    if (opt_.monitor_port != 0 && opt_.monitor_port < 1024) {
        if (error) *error = "kmboxNet monitor port must be 0 or 1024-65535";
        return false;
    }
    if (!opt_.uuid.empty() && opt_.uuid.size() != 8) {
        if (error) *error = "kmboxNet UUID must be exactly 8 hex characters";
        return false;
    }
    if (!opt_.uuid.empty()) {
        for (char c : opt_.uuid) {
            if (!std::isxdigit(static_cast<unsigned char>(c))) {
                if (error) *error = "kmboxNet UUID must be exactly 8 hex characters";
                return false;
            }
        }
    }
    return true;
}

// ---------------------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------------------
bool KmboxNetBackend::connect(std::string* error) {
    std::lock_guard<std::mutex> lock(io_mutex_);
    if (connected_.load()) return true;

    if (!validate(error)) {
        health_.state = BackendState::kError;
        health_.detail = error && !error->empty() ? *error : "invalid config";
        return false;
    }
    // 报文布局未实证：拒绝伪装连接成功
    if (not_ready_.load()) {
        if (error) *error = "kmboxnet 报文布局未实证（UNVERIFIED），等待真机抓包后接入";
        health_.state = BackendState::kError;
        health_.detail = "protocol UNVERIFIED";
        return false;
    }

#if defined(_WIN32)
    if (error) *error = "Windows 无 UDP 网络盒支持";
    health_.state = BackendState::kError;
    health_.detail = "unsupported platform";
    return false;
#else
    health_.state = BackendState::kConnecting;
    health_.detail = "connecting " + opt_.ip;
    int fd = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        if (error) *error = "socket create failed: " + std::string(std::strerror(errno));
        health_.state = BackendState::kError;
        return false;
    }
    fd_ = fd;
    connected_.store(true);
    health_.state = BackendState::kConnected;
    health_.detail = "connected";
    return true;
#endif
}

void KmboxNetBackend::disconnect() {
    std::lock_guard<std::mutex> lock(io_mutex_);
#if !defined(_WIN32)
    if (fd_ >= 0) {
        ::close(fd_);
        fd_ = -1;
    }
#endif
    connected_.store(false);
    health_.state = BackendState::kDisconnected;
    health_.detail = "disconnected";
}

bool KmboxNetBackend::reconnect(std::string* error) {
    disconnect();
    ++health_.reconnect_count;
    return connect(error);
}

BackendHealth KmboxNetBackend::health() const { return health_; }

// ---------------------------------------------------------------------------
// 输出
// ---------------------------------------------------------------------------
bool KmboxNetBackend::mouse_move(int32_t dx, int32_t dy, int32_t wheel) {
    if (!gate_allows()) return false;
    if (not_ready_.load()) return false;  // UNVERIFIED：不发假包
    std::lock_guard<std::mutex> lock(io_mutex_);
    if (!connected_.load()) return false;
    const uint32_t seq = seq_.fetch_add(1);
    const std::string pkt = build_move_packet(seq, dx, dy, wheel);
    if (pkt.empty()) return false;
    ++health_.send_ok;
    return true;
}

bool KmboxNetBackend::mouse_button(uint8_t button, uint8_t action) {
    if (!gate_allows()) return false;
    if (not_ready_.load()) return false;
    std::lock_guard<std::mutex> lock(io_mutex_);
    if (!connected_.load()) return false;
    const uint32_t seq = seq_.fetch_add(1);
    if (build_button_packet(seq, button, action).empty()) return false;
    ++health_.send_ok;
    return true;
}

bool KmboxNetBackend::mouse_click(uint8_t button) {
    return mouse_button(button, kActClick);
}

// ---------------------------------------------------------------------------
// 会话（实证流程；报文生成待真机抓包填充）
// ---------------------------------------------------------------------------
bool KmboxNetBackend::send_connect(std::string* error) {
    (void)error; return false;  // UNVERIFIED
}
bool KmboxNetBackend::wait_for_ack(uint32_t seq, uint32_t timeout_ms) {
    (void)seq; (void)timeout_ms; return false;  // UNVERIFIED
}
bool KmboxNetBackend::send_ping(std::string* error) {
    (void)error; return false;  // UNVERIFIED
}
bool KmboxNetBackend::send_command(uint32_t seq, const std::string& raw, bool encrypt, std::string* error) {
    (void)seq; (void)raw; (void)encrypt; (void)error; return false;  // UNVERIFIED
}

// ---- 报文生成：UNVERIFIED，返回空串（调用方据此拒绝发送）----
std::string KmboxNetBackend::build_connect_packet(uint32_t seq) { (void)seq; return {}; }
std::string KmboxNetBackend::build_ping_packet(uint32_t seq)   { (void)seq; return {}; }
std::string KmboxNetBackend::build_move_packet(uint32_t seq, int32_t dx, int32_t dy, int32_t wheel) {
    (void)seq; (void)dx; (void)dy; (void)wheel; return {};
}
std::string KmboxNetBackend::build_button_packet(uint32_t seq, uint8_t button, uint8_t action) {
    (void)seq; (void)button; (void)action; return {};
}

}  // namespace ttbox::core::output