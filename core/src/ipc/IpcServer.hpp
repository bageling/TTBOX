// IpcServer.hpp — AF_UNIX JSON 行协议服务端（阶段 A-1：最小 IPC）
//
// 协议（详见 docs/ipc-protocol.md）：
//   - 传输：AF_UNIX SOCK_STREAM（Unix）/ TCP loopback（Windows，路径 "tcp:PORT"）
//   - 帧格式：一行 JSON（'\n' 分隔）
//   - 请求：{"id":<可选>,"type":"PING|GET_STATUS|GET_CONFIG","params":<可选>}
//   - 响应：{"id":...,"type":...,"status":<错误码>,"data":{...},"error":<可选>}
//   - 错误码：0 OK / 1 BAD_REQUEST / 2 NOT_FOUND / 3 INTERNAL / 4 UNSUPPORTED
#pragma once

#include <atomic>
#include <functional>
#include <string>
#include <thread>

#include "common/Json.hpp"
#include "common/Metrics.hpp"

namespace ttbox::core {

// IPC 错误码（与 docs/ipc-protocol.md 保持一致）
enum class IpcError : int {
    kOk = 0,
    kBadRequest = 1,
    kNotFound = 2,
    kInternal = 3,
    kUnsupported = 4,
};

struct IpcResponse {
    IpcError status = IpcError::kInternal;
    std::string id;
    std::string type;
    std::string error;
    JsonValue data = JsonValue::object();

    // 序列化为一行 JSON（含换行）
    std::string to_json() const;
};

class IpcServer {
public:
    ~IpcServer();

    // 启动监听。socket_path: Unix 下为文件路径（默认 /tmp/ttbox_core.sock）；
    // Windows 下为 "tcp:<port>"。
    bool start(const std::string& socket_path, std::string* error = nullptr);
    void stop();
    bool running() const { return running_.load(); }
    const std::string& socket_path() const { return socket_path_; }

    // 数据提供回调（由 Application 注入）
    using StatusProvider = std::function<SystemStatus()>;
    using ConfigProvider = std::function<JsonValue()>;
    void set_status_provider(StatusProvider p) { status_provider_ = std::move(p); }
    void set_config_provider(ConfigProvider p) { config_provider_ = std::move(p); }

private:
    void accept_loop();
    void handle_connection(int fd);
    IpcResponse handle_request(const JsonValue& request);

    std::string socket_path_;
    int listen_fd_ = -1;
    std::atomic<bool> running_{false};
    std::thread accept_thread_;
    StatusProvider status_provider_;
    ConfigProvider config_provider_;
};

// 同步请求客户端：发送一行 JSON 请求，读取一行 JSON 响应。
// 成功返回 true，response 为响应 JSON 文本；失败返回 false（error 说明）。
bool ipc_request(const std::string& socket_path, const std::string& request_json,
                 std::string& response, int timeout_ms = 2000, std::string* error = nullptr);

// 便捷 PING：成功返回 true（可同时拿到 data.pong）
bool ipc_ping(const std::string& socket_path, std::string* error = nullptr);

// SystemStatus -> JSON（供 GET_STATUS 输出）
JsonValue system_status_to_json(const SystemStatus& status);

}  // namespace ttbox::core
