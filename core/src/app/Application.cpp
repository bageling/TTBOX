// Application.cpp — 应用生命周期实现
#include "app/Application.hpp"

#include <atomic>
#include <chrono>
#include <cstring>
#include <fstream>
#include <sstream>
#include <thread>

#include "common/Logger.hpp"
#include "output/AiboxHidOutput.hpp"
#include "output/FifoHidOutput.hpp"
#include "ttbox/core/version.hpp"

namespace ttbox::core {

namespace {

std::atomic<bool> g_shutdown_requested{false};
std::atomic<bool>& shutdown_flag() { return g_shutdown_requested; }

#ifndef TTBOX_PROJECT_ROOT
#define TTBOX_PROJECT_ROOT "."
#endif
const char* kDefaultConfigPath = TTBOX_PROJECT_ROOT "/config/default.json";
const char* kSystemLicenseFile = "/etc/ttbox/license.key";

double now_ms() {
    using clock = std::chrono::steady_clock;
    return static_cast<double>(
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

int parse_color_order(const std::string& s) {
    if (s == "rgb") return 1;
    return 0;
}

std::vector<int> parse_worker_cores(const std::string& s) {
    std::vector<int> result;
    if (s.empty()) { result = {1, 2, 4}; return result; }
    std::stringstream ss(s);
    std::string token;
    while (std::getline(ss, token, ',')) {
        if (!token.empty()) {
            try { result.push_back(std::stoi(token)); } catch (...) {}
        }
    }
    if (result.empty()) result = {1};
    return result;
}

std::string strip(const std::string& s) {
    size_t a = 0, b = s.size();
    while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    while (b > a && (std::isspace(static_cast<unsigned char>(s[b-1])) ||
                     s[b-1] == '\n' || s[b-1] == '\r')) --b;
    return s.substr(a, b - a);
}

}  // namespace

Application::~Application() {
    if (running_.load() || initialized_) {
        try { shutdown(); } catch (...) {}
    }
}

std::string Application::resolve_license_card(const std::string& cli_license) const {
    if (!cli_license.empty()) return cli_license;

    // 系统路径 /etc/ttbox/license.key：与原 aibox /etc/aibox/ 1:1 对齐语义
    {
        std::ifstream f(kSystemLicenseFile);
        if (f) {
            std::string s;
            std::getline(f, s);
            s = strip(s);
            if (!s.empty()) return s;
        }
    }

    // 配置 fallback（开发期）
    const std::string cfg = config_.get_string("license_card_key", "");
    if (!cfg.empty()) return cfg;
    return {};
}

bool Application::build_runtime_params(CoreRuntime::Params& out_params,
                                       std::string* error) {
    (void)error;
    out_params.capture.device =
        config_.get_string("capture_device", "/dev/video0");
    out_params.capture.num_buffers =
        static_cast<uint32_t>(config_.get_int("capture_buffers", 4));
    out_params.capture.poll_timeout_ms =
        config_.get_int("capture_poll_timeout_ms", 1000);

    out_params.workers.model_path =
        config_.get_string("model_path", "");
    if (out_params.workers.model_path.empty()) {
        const std::string label = config_.get_string("model_label", "");
        if (!label.empty()) {
            out_params.workers.model_path =
                std::string(TTBOX_PROJECT_ROOT) + "/models/" + label + "/" +
                label + ".rknn";
        }
    }
    out_params.workers.worker_cores =
        parse_worker_cores(config_.get_string("worker_cores", ""));
    out_params.workers.out_w =
        static_cast<uint32_t>(config_.get_int("model_input_width", 640));
    out_params.workers.out_h =
        static_cast<uint32_t>(config_.get_int("model_input_height", 640));
    out_params.workers.conf_thres =
        static_cast<float>(config_.get_double("conf", 0.25));
    out_params.workers.iou_thres =
        static_cast<float>(config_.get_double("nms", 0.45));
    out_params.workers.color_order =
        parse_color_order(config_.get_string("model_color_order", "bgr"));
    out_params.workers.pass_through =
        config_.get_bool("model_pass_through", false);

    const std::string output_kind = config_.get_string("output_backend", "aibox");
    if (output_kind == "fifo") {
        const std::string fifo_path =
            config_.get_string("output_fifo_path", "/tmp/ttbox_hid.fifo");
        hid_output_ = std::make_shared<output::FifoHidOutput>(fifo_path);
    } else {
        const std::string hidg_path =
            config_.get_string("output_hidg_path", "/dev/hidg0");
        auto output = std::make_shared<output::AiboxHidOutput>(hidg_path);
        bool enabled = config_.get_bool("output_enabled", false);
        if (auto profile = runtime_config_.snapshot()) {
            enabled = profile->mouse.enabled;
        }
        output->set_enabled(enabled);
        hid_output_ = std::move(output);
    }
    out_params.output = hid_output_;
    out_params.runtime_config = &runtime_config_;
    return true;
}

int Application::initialize(int argc, char** argv) {
    std::string cli_license;
    std::string cli_secret;
    bool verify_only = false;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        auto consume_value = [&](const char* name) -> bool {
            if (i + 1 >= argc) {
                TTBOX_LOG_ERROR(std::string("缺少参数值: ") + name);
                return false;
            }
            ++i;
            return true;
        };
        if (arg == "--config") {
            if (!consume_value("--config")) return 1;
            config_path_ = argv[i];
        } else if (arg == "--ipc") {
            if (!consume_value("--ipc")) return 1;
            ipc_path_ = argv[i];
        } else if (arg == "--log-level") {
            if (!consume_value("--log-level")) return 1;
            Logger::instance().set_level(parse_log_level(argv[i]));
        } else if (arg == "--license") {
            if (!consume_value("--license")) return 1;
            cli_license = argv[i];
        } else if (arg == "--license-server-secret") {
            if (!consume_value("--license-server-secret")) return 1;
            cli_secret = argv[i];
        } else if (arg == "--debug-license-pro-endpoint") {
            if (!consume_value("--debug-license-pro-endpoint")) return 1;
            license_override_pro_endpoint_ = argv[i];
        } else if (arg == "--debug-license-normal-endpoint") {
            if (!consume_value("--debug-license-normal-endpoint")) return 1;
            license_override_normal_endpoint_ = argv[i];
        } else if (arg == "--verify-only") {
            verify_only = true;
        } else if (arg == "--help" || arg == "-h") {
            TTBOX_LOG_INFO(
                "用法: ttbox_core [--config <path>] [--ipc <path>]\n"
                "                [--log-level debug|info|warn|error|off]\n"
                "                [--license <card>] [--license-server-secret <secret>]\n"
                "                [--verify-only]\n"
                "                [--debug-license-pro-endpoint <host>]\n"
                "                [--debug-license-normal-endpoint <host>]");
            return 1;
        } else {
            TTBOX_LOG_WARN(std::string("忽略未知参数: ") + arg);
        }
    }

    Logger::instance().add_sink(std::make_shared<ConsoleSink>());
    TTBOX_LOG_INFO("=== " + std::string(kAppName) + " v" +
                   std::string(kVersion) + " 启动 ===");

    if (config_path_.empty()) config_path_ = kDefaultConfigPath;
    std::string cfg_error;
    if (!config_.load(config_path_, &cfg_error)) {
        TTBOX_LOG_ERROR(cfg_error);
        TTBOX_LOG_ERROR("配置加载失败，拒绝启动");
        return 1;
    }
    TTBOX_LOG_INFO("配置已加载: " + config_path_);

    // ---- 3. 授权层初始化（等价 cardVerifyThreadFunc）----
    license_client_ = std::make_unique<auth::AiboxLicenseClient>();
    if (!license_override_pro_endpoint_.empty() ||
        !license_override_normal_endpoint_.empty()) {
        license_client_->override_endpoint(license_override_pro_endpoint_,
                                            license_override_normal_endpoint_);
    }
    license_server_secret_ = cli_secret.empty()
                                 ? config_.get_string("license_server_secret", "")
                                 : cli_secret;
    license_daemon_ = std::make_unique<auth::LicenseDaemon>(*license_client_);
    const std::string card = resolve_license_card(cli_license);
    if (!card.empty()) {
        license_daemon_->set_card(card);
        TTBOX_LOG_INFO("授权卡号已加载 (prefix: " +
                       card.substr(0, std::min<size_t>(8, card.size())) + "...)");
    }
    // 开发模式：没有卡号时允许 --license-server-secret 为空，后续 --verify-only 可快速失败
    verify_only_ = verify_only;
    if (!license_daemon_->start()) {
        TTBOX_LOG_ERROR("授权线程启动失败");
        return 1;
    }
    // --verify-only 模式：立即同步触发一次，打印结果后退出，不进入推理
    if (verify_only_) {
        std::string err;
        bool ok = license_daemon_->verify_now_blocking(&err);
        auto st = license_daemon_->status_snapshot();
        TTBOX_LOG_INFO(std::string("verify-only: ok=") + (ok ? "true" : "false") +
                       " state=" + std::to_string(static_cast<int>(st.state)) +
                       " is_pro=" + (st.is_pro ? "true" : "false") +
                       " error=" + (st.last_error.empty() ? err : st.last_error));
        // verify-only 模式：无论结果如何，打印后直接退出
        license_daemon_->stop();
        return (ok && (st.state == auth::LicenseState::kValid ||
                       st.state == auth::LicenseState::kFallback))
                   ? 0
                   : 2;
    }

    // ---- 4. 加载 RuntimeProfile：让配置文件真正进入 Worker/AimThread ----
    if (const JsonValue* profile_json = config_.root().find("runtime_profile")) {
        RuntimeProfile profile = RuntimeProfile::from_json(*profile_json);
        std::string profile_error;
        if (!profile.validate(&profile_error)) {
            TTBOX_LOG_ERROR("RuntimeProfile 校验失败: " + profile_error);
            return 1;
        }
        runtime_config_.update(profile);
        TTBOX_LOG_INFO("RuntimeProfile 已加载");
    }

    // ---- 5. 构建 CoreRuntime 参数 & 初始化 ----
    core_runtime_ = std::make_unique<CoreRuntime>();
    CoreRuntime::Params rt_params{};
    std::string rt_error;
    if (!build_runtime_params(rt_params, &rt_error)) {
        TTBOX_LOG_ERROR("CoreRuntime 参数构建失败: " + rt_error);
        return 1;
    }
    if (!core_runtime_->initialize(rt_params, &rt_error)) {
        TTBOX_LOG_ERROR("CoreRuntime 初始化失败: " + rt_error);
        return 1;
    }
    TTBOX_LOG_INFO("CoreRuntime 初始化完成 (workers=" +
                   std::to_string(rt_params.workers.worker_cores.size()) + ")");

    // ---- 5. 启动 IPC 服务 ----
    ipc_.set_status_provider([this] { return status_provider(); });
    ipc_.set_config_provider([this] { return config_provider(); });
    std::string ipc_error;
    if (!ipc_.start(ipc_path_, &ipc_error)) {
        TTBOX_LOG_ERROR("IPC 启动失败: " + ipc_error);
        return 1;
    }

    start_time_ms_ = now_ms();
    initialized_ = true;
    return 0;
}

void Application::run() {
    if (!initialized_) {
        TTBOX_LOG_ERROR("Application 未初始化，拒绝 run()");
        return;
    }
    running_.store(true);

    // 授权门控：先等待一次立即验卡；允许的状态：kValid 或 Fallback
    // 超过 60s 仍未通过 → 打印但仍然继续（开发期离线）
    {
        std::string err;
        (void)license_daemon_->verify_now_blocking(&err);
        int waited = 0;
        while (!license_daemon_->allow_run() && waited < 60) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            ++waited;
        }
        auto st = license_daemon_->status_snapshot();
        if (!license_daemon_->allow_run()) {
            TTBOX_LOG_WARN("授权未通过，但继续启动（离线开发模式/未填卡）；推理结果仍会按配置过滤");
        } else {
            TTBOX_LOG_INFO(std::string("授权通过 state=") +
                           std::to_string(static_cast<int>(st.state)) +
                           " is_pro=" + (st.is_pro ? "true" : "false"));
        }
    }

    std::string rt_error;
    if (core_runtime_ && !core_runtime_->start(&rt_error)) {
        TTBOX_LOG_ERROR("CoreRuntime 启动失败: " + rt_error);
        running_.store(false);
        return;
    }
    runtime_started_ = true;
    TTBOX_LOG_INFO("CoreRuntime 已启动 (Ctrl+C/SIGTERM 退出)");

    constexpr auto kTickMs = std::chrono::milliseconds(50);
    constexpr auto kHeartbeatSec = std::chrono::seconds(10);
    auto last_heartbeat = std::chrono::steady_clock::now();
    while (!shutdown_flag().load()) {
        std::this_thread::sleep_for(kTickMs);
        // 授权失效时仍保持进程存活（通过 supervisor recover() 重启恢复），不主动自杀
        const auto now = std::chrono::steady_clock::now();
        if (now - last_heartbeat >= kHeartbeatSec) {
            last_heartbeat = now;
            const bool rt_ok = core_runtime_ ? core_runtime_->running() : false;
            auto st = license_daemon_->status_snapshot();
            TTBOX_LOG_DEBUG(std::string("heartbeat: runtime=") +
                            (rt_ok ? "running" : "stopped") +
                            " license_state=" +
                            std::to_string(static_cast<int>(st.state)));
        }
    }
    TTBOX_LOG_INFO("收到退出请求，正在停止...");
}

void Application::shutdown() {
    const bool was_running = running_.exchange(false);
    if (was_running) TTBOX_LOG_INFO("Application shutdown() 开始");

    if (runtime_started_ && core_runtime_) {
        TTBOX_LOG_INFO("停止 CoreRuntime...");
        core_runtime_->stop();
        runtime_started_ = false;
    }
    core_runtime_.reset();
    hid_output_.reset();

    // 授权线程停在 IPC 之后（让 IPC 最后一个响应仍能拿到授权快照）
    if (license_daemon_) {
        license_daemon_->stop();
        license_daemon_.reset();
    }
    license_client_.reset();

    ipc_.stop();
    initialized_ = false;
    TTBOX_LOG_INFO("=== " + std::string(kAppName) + " 已退出 ===");
}

void Application::request_shutdown() {
    shutdown_flag().store(true);
}

SystemStatus Application::status() const { return status_provider(); }

auth::LicenseStatus Application::license_status_snapshot() const {
    return license_daemon_ ? license_daemon_->status_snapshot()
                           : auth::LicenseStatus{};
}
bool Application::license_allow_run() const {
    return license_daemon_ && license_daemon_->allow_run();
}
bool Application::license_is_pro() const {
    return license_daemon_ && license_daemon_->is_pro();
}

SystemStatus Application::status_provider() const {
    SystemStatus st;
    st.running = running_.load();
    st.app_name = kAppName;
    st.version = kVersion;
    if (start_time_ms_ > 0.0) st.uptime_ms = now_ms() - start_time_ms_;
    st.ipc_socket = ipc_.socket_path();
    st.config_file = config_.path();
    st.runtime_running = core_runtime_ ? core_runtime_->running() : false;
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
