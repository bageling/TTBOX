// Application.hpp — C++ Core 应用生命周期（initialize / run / shutdown）
#pragma once

#include <atomic>
#include <memory>
#include <string>

#include "auth/AiboxLicenseClient.hpp"
#include "auth/LicenseDaemon.hpp"
#include "common/Metrics.hpp"
#include "config/ConfigManager.hpp"
#include "ipc/IpcServer.hpp"
#include "model/RuntimeProfile.hpp"
#include "output/IHidOutput.hpp"
#include "runtime/CoreRuntime.hpp"

namespace ttbox::core {

class Application {
public:
    Application() = default;
    ~Application();

    // 解析 CLI + 初始化 Logger/Config/IPC/CoreRuntime/授权。
    // 成功返回 0；失败返回非 0（含明确错误日志）。
    int initialize(int argc, char** argv);

    // 事件循环：启动 CoreRuntime，阻塞直到收到 shutdown 请求。
    // 每 tick 执行状态同步 + IPC 心跳；退出时按序停止 CoreRuntime。
    void run();

    // 请求退出（线程安全；signal handler 可直接调用，仅置原子标志）。
    static void request_shutdown();

    // 清理：停止 CoreRuntime → 停止 IPC → 停止 授权 → 写日志。
    void shutdown();

    bool running() const { return running_.load(); }
    const ConfigManager& config() const { return config_; }
    SystemStatus status() const;

    // ---- 对外查询接口（授权状态 / 卡号 / Pro 功能） ----
    auth::LicenseStatus license_status_snapshot() const;
    bool license_allow_run() const;
    bool license_is_pro() const;

private:
    // 供 IpcServer providers 使用
    SystemStatus status_provider() const;
    JsonValue config_provider() const;

    // 从配置构造 CoreRuntime 参数
    bool build_runtime_params(CoreRuntime::Params& out_params, std::string* error);

    // 加载卡号：优先级 --license > /etc/ttbox/license.key > 配置 license_card_key
    std::string resolve_license_card(const std::string& cli_license) const;

    ConfigManager config_;
    IpcServer ipc_;
    std::string ipc_path_ = "/tmp/ttbox_core.sock";
    std::string config_path_;
    std::atomic<bool> running_{false};
    double start_time_ms_ = 0.0;
    bool initialized_ = false;
    bool verify_only_ = false;  // --verify-only：授权完立即退出，不启推理

    // ---- 核心链路（接入 Application 生命周期）----
    RuntimeConfig runtime_config_;
    std::shared_ptr<output::IHidOutput> hid_output_;
    std::unique_ptr<CoreRuntime> core_runtime_;
    bool runtime_started_ = false;

    // ---- 授权（等价原 aibox-bl cardVerifyThreadFunc）----
    std::unique_ptr<auth::AiboxLicenseClient> license_client_;
    std::unique_ptr<auth::LicenseDaemon> license_daemon_;
    std::string license_override_pro_endpoint_;   // --debug-license-pro-endpoint
    std::string license_override_normal_endpoint_; // --debug-license-normal-endpoint
    std::string license_server_secret_;           // ACCESS_KEY 签名密钥（仅开发环境显式传入）
};

}  // namespace ttbox::core
