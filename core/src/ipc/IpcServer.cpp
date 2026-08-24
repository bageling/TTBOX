// IpcServer.cpp — IPC 服务端/客户端实现（Unix AF_UNIX，Windows TCP loopback）
#include "ipc/IpcServer.hpp"

#include <atomic>
#include <chrono>
#include <cstring>
#include <sstream>
#include <string>
#include <thread>

#include "common/Logger.hpp"

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>
#endif

namespace ttbox::core {

namespace {

#if defined(_WIN32)
// Windows：路径 "tcp:<port>"。返回监听 fd（SOCKET 转 int），失败 -1。
int listen_tcp(const std::string& path, std::string* error) {
    static bool ws_inited = []() {
        WSADATA wsa{};
        return WSAStartup(MAKEWORD(2, 2), &wsa) == 0;
    }();
    (void)ws_inited;

    std::string port_str = path;
    const std::string prefix = "tcp:";
    if (port_str.rfind(prefix, 0) == 0) port_str = port_str.substr(prefix.size());
    int port = 0;
    try {
        port = std::stoi(port_str);
    } catch (...) {
        if (error) *error = "非法 TCP 端口: " + path;
        return -1;
    }

    SOCKET fd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (fd == INVALID_SOCKET) {
        if (error) *error = "socket() 失败";
        return -1;
    }
    int opt = 1;
    ::setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&opt), sizeof(opt));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(static_cast<uint16_t>(port));
    if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        if (error) *error = "bind() 失败 (端口 " + port_str + " 可能被占用)";
        ::closesocket(fd);
        return -1;
    }
    if (::listen(fd, 8) != 0) {
        if (error) *error = "listen() 失败";
        ::closesocket(fd);
        return -1;
    }
    return static_cast<int>(fd);
}

int connect_tcp(const std::string& path, std::string* error) {
    std::string port_str = path;
    const std::string prefix = "tcp:";
    if (port_str.rfind(prefix, 0) == 0) port_str = port_str.substr(prefix.size());
    int port = 0;
    try {
        port = std::stoi(port_str);
    } catch (...) {
        if (error) *error = "非法 TCP 端口: " + path;
        return -1;
    }
    SOCKET fd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (fd == INVALID_SOCKET) {
        if (error) *error = "socket() 失败";
        return -1;
    }
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(static_cast<uint16_t>(port));
    if (::connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        if (error) *error = "connect() 失败";
        ::closesocket(fd);
        return -1;
    }
    return static_cast<int>(fd);
}

ssize_t sock_send(int fd, const void* buf, size_t len) {
    return static_cast<ssize_t>(::send(static_cast<SOCKET>(fd),
                                       reinterpret_cast<const char*>(buf),
                                       static_cast<int>(len), 0));
}

ssize_t sock_recv(int fd, void* buf, size_t len) {
    return static_cast<ssize_t>(::recv(static_cast<SOCKET>(fd),
                                       reinterpret_cast<char*>(buf),
                                       static_cast<int>(len), 0));
}

void sock_close(int fd) { ::closesocket(static_cast<SOCKET>(fd)); }

#else  // !_WIN32 (Unix)

int listen_unix(const std::string& path, std::string* error) {
    int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        if (error) *error = "socket() 失败: " + std::string(std::strerror(errno));
        return -1;
    }
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (path.size() >= sizeof(addr.sun_path)) {
        if (error) *error = "socket 路径过长: " + path;
        ::close(fd);
        return -1;
    }
    std::strncpy(addr.sun_path, path.c_str(), sizeof(addr.sun_path) - 1);
    // 清理残留 socket 文件（若存在且可写）
    ::unlink(path.c_str());
    if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        if (error) *error = "bind() 失败 (" + path + "): " + std::string(std::strerror(errno));
        ::close(fd);
        return -1;
    }
    if (::listen(fd, 8) != 0) {
        if (error) *error = "listen() 失败: " + std::string(std::strerror(errno));
        ::close(fd);
        ::unlink(path.c_str());
        return -1;
    }
    ::chmod(path.c_str(), 0666);  // 允许非 root 客户端连接
    return fd;
}

int connect_unix(const std::string& path, std::string* error) {
    int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        if (error) *error = "socket() 失败";
        return -1;
    }
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (path.size() >= sizeof(addr.sun_path)) {
        if (error) *error = "socket 路径过长: " + path;
        ::close(fd);
        return -1;
    }
    std::strncpy(addr.sun_path, path.c_str(), sizeof(addr.sun_path) - 1);
    if (::connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        if (error) *error = "connect() 失败 (" + path + "): " + std::string(std::strerror(errno));
        ::close(fd);
        return -1;
    }
    return fd;
}

ssize_t sock_send(int fd, const void* buf, size_t len) {
    return ::send(fd, buf, len, 0);
}

ssize_t sock_recv(int fd, void* buf, size_t len) {
    return ::recv(fd, buf, len, 0);
}

void sock_close(int fd) { ::close(fd); }

#endif  // _WIN32

std::string read_line(int fd, bool* ok) {
    std::string buf;
    char tmp[4096];
    *ok = true;
    while (true) {
        ssize_t n = sock_recv(fd, tmp, sizeof(tmp));
        if (n <= 0) {
            *ok = false;
            break;
        }
        buf.append(tmp, static_cast<size_t>(n));
        auto pos = buf.find('\n');
        if (pos != std::string::npos) {
            buf.resize(pos);
            break;
        }
        if (buf.size() > 65536) {
            *ok = false;  // 超长请求，防滥用
            break;
        }
    }
    return buf;
}

}  // namespace

// ---------------------------------------------------------------------------
// IpcResponse
// ---------------------------------------------------------------------------

std::string IpcResponse::to_json() const {
    JsonValue resp = JsonValue::object();
    resp.set("id", JsonValue::string(id));
    resp.set("type", JsonValue::string(type));
    resp.set("status", JsonValue::number(static_cast<double>(static_cast<int>(status))));
    resp.set("data", data);
    if (!error.empty()) {
        resp.set("error", JsonValue::string(error));
    }
    return resp.dump() + "\n";
}

// ---------------------------------------------------------------------------
// IpcServer
// ---------------------------------------------------------------------------

IpcServer::~IpcServer() {
    stop();
}

bool IpcServer::start(const std::string& socket_path, std::string* error) {
    if (running_.load()) {
        if (error) *error = "IpcServer 已在运行";
        return false;
    }
    int fd = -1;
#if defined(_WIN32)
    fd = listen_tcp(socket_path, error);
#else
    fd = listen_unix(socket_path, error);
#endif
    if (fd < 0) return false;

    socket_path_ = socket_path;
    listen_fd_ = fd;
    running_.store(true);
    accept_thread_ = std::thread(&IpcServer::accept_loop, this);
    TTBOX_LOG_INFO("IPC 服务已启动: " + socket_path);
    return true;
}

void IpcServer::stop() {
    if (!running_.exchange(false)) return;

#if defined(_WIN32)
    if (listen_fd_ >= 0) {
        ::shutdown(static_cast<SOCKET>(listen_fd_), SD_BOTH);
    }
#else
    if (listen_fd_ >= 0) {
        ::shutdown(listen_fd_, SHUT_RDWR);  // 使阻塞的 accept 立即返回
    }
#endif
    if (accept_thread_.joinable()) {
        accept_thread_.join();
    }
    if (listen_fd_ >= 0) {
        sock_close(listen_fd_);
        listen_fd_ = -1;
    }
#if !defined(_WIN32)
    ::unlink(socket_path_.c_str());
#endif
    TTBOX_LOG_INFO("IPC 服务已停止: " + socket_path_);
}

void IpcServer::accept_loop() {
    while (running_.load()) {
        int client_fd = -1;
#if defined(_WIN32)
        SOCKET c = ::accept(static_cast<SOCKET>(listen_fd_), nullptr, nullptr);
        client_fd = c == INVALID_SOCKET ? -1 : static_cast<int>(c);
#else
        client_fd = ::accept(listen_fd_, nullptr, nullptr);
#endif
        if (client_fd < 0) {
            if (running_.load()) {
                TTBOX_LOG_WARN("accept() 失败（服务停止中则忽略）");
            }
            continue;
        }
        std::thread([this, client_fd] { handle_connection(client_fd); }).detach();
    }
}

void IpcServer::handle_connection(int fd) {
    bool ok = false;
    std::string request_text = read_line(fd, &ok);
    std::string response_text;
    if (ok && !request_text.empty()) {
        JsonParseResult parsed = json_parse(request_text);
        IpcResponse resp;
        if (!parsed.ok) {
            resp.status = IpcError::kBadRequest;
            resp.type = "";
            resp.error = "invalid JSON request: " + parsed.error;
        } else if (!parsed.value.is_object()) {
            resp.status = IpcError::kBadRequest;
            resp.error = "request must be a JSON object";
        } else {
            resp = handle_request(parsed.value);
        }
        response_text = resp.to_json();
    } else {
        // 空/异常连接：无需响应
    }
    if (!response_text.empty()) {
        sock_send(fd, response_text.data(), response_text.size());
    }
    sock_close(fd);
}

IpcResponse IpcServer::handle_request(const JsonValue& request) {
    IpcResponse resp;
    const JsonValue* id_v = request.find("id");
    resp.id = id_v ? id_v->as_string() : "";

    const JsonValue* type_v = request.find("type");
    if (type_v == nullptr || !type_v->is_string()) {
        resp.status = IpcError::kBadRequest;
        resp.error = "missing string field 'type'";
        return resp;
    }
    const std::string type = type_v->as_string();
    resp.type = type;

    if (type == "PING") {
        JsonValue data = JsonValue::object();
        data.set("pong", JsonValue::boolean(true));
        data.set("server", JsonValue::string("ttbox_core"));
        resp.status = IpcError::kOk;
        resp.data = std::move(data);
        return resp;
    }

    if (type == "GET_STATUS") {
        if (!status_provider_) {
            resp.status = IpcError::kInternal;
            resp.error = "status provider not registered";
            return resp;
        }
        resp.status = IpcError::kOk;
        resp.data = system_status_to_json(status_provider_());
        return resp;
    }

    if (type == "GET_CONFIG") {
        if (!config_provider_) {
            resp.status = IpcError::kInternal;
            resp.error = "config provider not registered";
            return resp;
        }
        resp.status = IpcError::kOk;
        resp.data = config_provider_();
        return resp;
    }

    resp.status = IpcError::kUnsupported;
    resp.error = "unsupported request type: " + type;
    return resp;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

bool ipc_request(const std::string& socket_path, const std::string& request_json,
                 std::string& response, int timeout_ms, std::string* error) {
    int fd = -1;
#if defined(_WIN32)
    fd = connect_tcp(socket_path, error);
#else
    fd = connect_unix(socket_path, error);
#endif
    if (fd < 0) return false;

#if defined(_WIN32)
    struct timeval tv {};
    tv.tv_sec = timeout_ms / 1000;
    tv.tv_usec = (timeout_ms % 1000) * 1000;
    ::setsockopt(static_cast<SOCKET>(fd), SOL_SOCKET, SO_RCVTIMEO,
                 reinterpret_cast<const char*>(&tv), sizeof(tv));
#else
    struct timeval tv {};
    tv.tv_sec = timeout_ms / 1000;
    tv.tv_usec = (timeout_ms % 1000) * 1000;
    ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
#endif

    std::string payload = request_json;
    if (payload.empty() || payload.back() != '\n') payload.push_back('\n');

    ssize_t sent = sock_send(fd, payload.data(), payload.size());
    if (sent <= 0) {
        if (error) *error = "发送请求失败";
        sock_close(fd);
        return false;
    }

    bool ok = false;
    response = read_line(fd, &ok);
    sock_close(fd);
    if (!ok) {
        if (error) *error = "读取响应失败（超时或连接关闭）";
        return false;
    }
    return true;
}

bool ipc_ping(const std::string& socket_path, std::string* error) {
    std::string resp_text;
    if (!ipc_request(socket_path, R"({"type":"PING"})", resp_text, 2000, error)) {
        return false;
    }
    JsonParseResult parsed = json_parse(resp_text);
    if (!parsed.ok || !parsed.value.is_object()) {
        if (error) *error = "PING 响应解析失败";
        return false;
    }
    const JsonValue* status_v = parsed.value.find("status");
    return status_v != nullptr && status_v->as_int() == static_cast<int64_t>(IpcError::kOk);
}

// ---------------------------------------------------------------------------
// SystemStatus -> JSON
// ---------------------------------------------------------------------------

JsonValue system_status_to_json(const SystemStatus& status) {
    JsonValue data = JsonValue::object();
    data.set("running", JsonValue::boolean(status.running));
    data.set("app_name", JsonValue::string(status.app_name));
    data.set("version", JsonValue::string(status.version));
    data.set("uptime_ms", JsonValue::number(status.uptime_ms));
    data.set("ipc_socket", JsonValue::string(status.ipc_socket));
    data.set("config_file", JsonValue::string(status.config_file));

    JsonValue m = JsonValue::object();
    m.set("fps", JsonValue::number(status.metrics.fps));
    m.set("capture_ms", JsonValue::number(status.metrics.capture_ms));
    m.set("resize_ms", JsonValue::number(status.metrics.resize_ms));
    m.set("infer_ms", JsonValue::number(status.metrics.infer_ms));
    m.set("decode_ms", JsonValue::number(status.metrics.decode_ms));
    m.set("aim_ms", JsonValue::number(status.metrics.aim_ms));
    m.set("e2e_ms", JsonValue::number(status.metrics.e2e_ms));
    m.set("detect_count", JsonValue::number(static_cast<double>(status.metrics.detect_count)));
    m.set("dropped_frames", JsonValue::number(static_cast<double>(status.metrics.dropped_frames)));
    m.set("frames_total", JsonValue::number(static_cast<double>(status.metrics.frames_total)));
    data.set("metrics", std::move(m));
    return data;
}

}  // namespace ttbox::core
