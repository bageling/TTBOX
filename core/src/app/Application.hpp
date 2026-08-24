// Application.hpp — C++ Core 应用生命周期（initialize / run / shutdown）
#pragma once

#include <atomic>
#include <string>

#include "common/Metrics.hpp"
#include "config/ConfigManager.hpp"
#include "ipc/IpcServer.hpp"

namespace ttbox::core {

class Application {
public:
    Application() = default;
    ~Application() = default;

    // 解析 CLI + 初始化 Logger/Config/IPC。成功返回 0；失败返回非 0（含明确错误日志）。
    int initialize(int argc, char** argv);

    // 事件循环：阻塞直到收到 shutdown 请求（SIGINT/SIGTERM 或 request_shutdown()）。
    void run();

    // 请求退出（线程安全；signal handler 可直接调用，仅置原子标志）。
    static void request_shutdown();

    // 清理：停止 IPC、写日志。
    void shutdown();

    bool running() const { return running_.load(); }
    const ConfigManager& config() const { return config_; }
    SystemStatus status() const;

private:
    // 供 IpcServer providers 使用
    SystemStatus status_provider() const;
    JsonValue config_provider() const;

    ConfigManager config_;
    IpcServer ipc_;
    std::string ipc_path_ = "/tmp/ttbox_core.sock";
    std::string config_path_;
    std::atomic<bool> running_{false};
    double start_time_ms_ = 0.0;
    bool initialized_ = false;
};

}  // namespace ttbox::core
