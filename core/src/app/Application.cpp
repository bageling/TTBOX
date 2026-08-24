// Application.cpp — 应用生命周期实现
#include "app/Application.hpp"

#include <atomic>
#include <chrono>
#include <cstring>
#include <thread>

#include "common/Logger.hpp"
#include "ttbox/core/version.hpp"

namespace ttbox::core {

namespace {

// 全局 shutdown 标志：仅被 signal handler / request_shutdown() 置位（原子操作，async-signal-safe）
std::atomic<bool> g_shutdown_requested{false};

std::atomic<bool>& shutdown_flag() {
    return g_shutdown_requested;
}

// 默认配置文件路径：项目根 (ttbox2/) /config/default.json
// TTBOX_PROJECT_ROOT 由 CMake 注入（core 位于 <root>/ttbox/core）
#ifndef TTBOX_PROJECT_ROOT
#define TTBOX_PROJECT_ROOT "."
#endif
const char* kDefaultConfigPath = TTBOX_PROJECT_ROOT "/config/default.json";

std::string now_ms_str() {
    using clock = std::chrono::steady_clock;
    return std::to_string(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            clock::now().time_since_epoch())
            .count());
}

LogLevel parse_log_level(const std::string& s) {
    if (s == "debug") return LogLevel::kDebug;
    if (s == "warn") return LogLevel::kWarn;
    if (s == "error") return LogLevel::kError;
    if (s == "off") return LogLevel::kOff;
    return LogLevel::kInfo;
}

}  // namespace

int Application::initialize(int argc, char** argv) {
    // ---- 1. 解析 CLI（--config / --ipc / --log-level）----
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        auto need_value = [&](const char* name) -> bool {
            if (i + 1 >= argc) {
                Logger::instance().log(LogLevel::kError,
                                       std::string("缺少参数值: ") + name,
                                       __FILE__, __LINE__);
                return false;
            }
            ++i;
            return true;
        };
        if (arg == "--config") {
            if (!need_value("--config")) return 1;
            config_path_ = argv[i];
        } else if (arg == "--ipc") {
            if (!need_value("--ipc")) return 1;
            ipc_path_ = argv[i];
        } else if (arg == "--log-level") {
            if (!need_value("--log-level")) return 1;
            Logger::instance().set_level(parse_log_level(argv[i]));
        } else if (arg == "--help" || arg == "-h") {
            Logger::instance().log(
                LogLevel::kInfo,
                "用法: ttbox_core [--config <path>] [--ipc <path>] [--log-level debug|info|warn|error|off]",
                __FILE__, __LINE__);
            return 1;
        } else {
            Logger::instance().log(LogLevel::kWarn,
                                   std::string("忽略未知参数: ") + arg, __FILE__, __LINE__);
        }
    }

    Logger::instance().add_sink(std::make_shared<ConsoleSink>());
    TTBOX_LOG_INFO("=== " + std::string(kAppName) + " v" + std::string(kVersion) + " 启动 ===");

    // ---- 2. 加载配置（严格模式：失败即明确报错退出）----
    if (config_path_.empty()) {
        config_path_ = kDefaultConfigPath;
    }
    std::string cfg_error;
    if (!config_.load(config_path_, &cfg_error)) {
        TTBOX_LOG_ERROR(cfg_error);
        TTBOX_LOG_ERROR("配置加载失败，拒绝启动（不允许 silent fallback）");
        return 1;
    }
    TTBOX_LOG_INFO("配置已加载: " + config_path_ +
                   " (conf=" + std::to_string(config_.get_double("conf", 0.25)) +
                   ", model_input=" +
                   std::to_string(config_.get_int("model_input_width", 0)) + "x" +
                   std::to_string(config_.get_int("model_input_height", 0)) + ")");

    // ---- 3. 启动 IPC 服务 ----
    ipc_.set_status_provider([this] { return status_provider(); });
    ipc_.set_config_provider([this] { return config_provider(); });
    std::string ipc_error;
    if (!ipc_.start(ipc_path_, &ipc_error)) {
        TTBOX_LOG_ERROR("IPC 启动失败: " + ipc_error);
        return 1;
    }

    start_time_ms_ = std::stod(now_ms_str());
    initialized_ = true;
    return 0;
}

void Application::run() {
    if (!initialized_) {
        TTBOX_LOG_ERROR("Application 未初始化，拒绝 run()");
        return;
    }
    running_.store(true);
    TTBOX_LOG_INFO("Application 运行中 (Ctrl+C 或 SIGTERM 退出)");
    while (!shutdown_flag().load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    TTBOX_LOG_INFO("收到退出请求，正在停止...");
}

void Application::shutdown() {
    if (running_.exchange(false)) {
        TTBOX_LOG_INFO("Application shutdown() 开始");
    }
    ipc_.stop();
    TTBOX_LOG_INFO("=== " + std::string(kAppName) + " 已退出 ===");
}

void Application::request_shutdown() {
    shutdown_flag().store(true);
}

SystemStatus Application::status() const {
    return status_provider();
}

SystemStatus Application::status_provider() const {
    SystemStatus st;
    st.running = running_.load();
    st.app_name = kAppName;
    st.version = kVersion;
    if (start_time_ms_ > 0.0) {
        st.uptime_ms = std::stod(now_ms_str()) - start_time_ms_;
    }
    st.ipc_socket = ipc_.socket_path();
    st.config_file = config_.path();
    // 本阶段视觉链路未接入，metrics 保持占位
    return st;
}

JsonValue Application::config_provider() const {
    JsonValue data = JsonValue::object();
    for (const auto& [key, value] : config_.flatten()) {
        data.set(key, JsonValue::string(value));
    }
    return data;
}

}  // namespace ttbox::core
