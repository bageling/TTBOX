// OutputBackend.hpp — 统一输出后端抽象（对齐 YU 外设后端）
//
// 目标：AimThread 只产生 OutputAction（dx/dy/buttons），不判断设备类型。
//       OutputBackend 作为 IHidOutput 的兼容实现，按配置选择一种物理后端：
//         LocalHidBackend（现有 /dev/hidg0） / KmboxNetBackend（UDP 盒）
//         MakcuBackend / FerrumBackend / KmboxBBackend（串口，架构预留）
//
// 设计依据：docs/research/YU_OUTPUT_BACKEND_RESEARCH.md（YU 真机实证）。
// 纪律：
//   - Hotkey Gate / mouse.enabled 实时判定逻辑保持与现 AiboxHidOutput 完全一致；
//   - send() 热路径零分配、无锁、无日志；
//   - 协议字节布局未实证处标注 UNVERIFIED，不引用网络资料猜测。
#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>

#include "output/IHidOutput.hpp"

namespace ttbox::core { class RuntimeConfig; }

namespace ttbox::core::output {

struct OutputAction;  // 来自 IHidOutput.hpp

// ---------------------------------------------------------------------------
// 后端状态 / 健康
// ---------------------------------------------------------------------------
enum class BackendState {
    kDisconnected,
    kConnecting,
    kConnected,
    kError,
};

struct BackendHealth {
    BackendState state = BackendState::kDisconnected;
    std::string detail;           // 人类可读（Web 展示）
    uint64_t send_ok = 0;
    uint64_t send_fail = 0;
    uint64_t reconnect_count = 0;
    int64_t last_send_ok_us = 0;  // 最近成功发送时刻（steady，us）
};

// ---------------------------------------------------------------------------
// 按钮/动作编码（对齐 YU usb-proxy 实证；各后端映射到自己协议）
// ---------------------------------------------------------------------------
constexpr uint8_t kBtnLeft = 1;
constexpr uint8_t kBtnRight = 2;
constexpr uint8_t kBtnMiddle = 3;
constexpr uint8_t kBtnBack = 4;
constexpr uint8_t kBtnForward = 5;
constexpr uint8_t kActDown = 1;
constexpr uint8_t kActUp = 2;
constexpr uint8_t kActClick = 3;

// ---------------------------------------------------------------------------
// IOutputBackend：一种物理设备协议
// ---------------------------------------------------------------------------
class IOutputBackend {
public:
    virtual ~IOutputBackend() = default;

    // 生命周期
    virtual bool connect(std::string* error = nullptr) = 0;
    virtual void disconnect() = 0;
    virtual bool reconnect(std::string* error = nullptr) = 0;
    virtual BackendHealth health() const = 0;

    // 输出
    virtual bool mouse_move(int32_t dx, int32_t dy, int32_t wheel = 0) = 0;
    virtual bool mouse_button(uint8_t button, uint8_t action) = 0;
    virtual bool mouse_click(uint8_t button) = 0;

    virtual const char* name() const = 0;

    // ---- Hotkey Gate / 总闸（基类实现，与现 AiboxHidOutput 一致）----
    void set_enabled(bool enabled) { enabled_ = enabled; }
    void set_button_source(std::atomic<uint16_t>* source) { button_source_ = source; }
    void set_config_source(RuntimeConfig* config) { config_source_ = config; }

protected:
    // 发送前调用：false = 被 Gate 拦截（不发送）。
    // 判定顺序与 AiboxHidOutput::send 完全一致（fail-closed）：
    //   1) 静态总闸；2) config 缺失；3) mouse.enabled；4) 热键 mask 缺失；5) 热键未按下。
    // 实现位于 OutputBackend.cpp（需 RuntimeConfig 完整定义）。
    bool gate_allows() const;

    std::atomic<uint16_t>* button_source_ = nullptr;
    RuntimeConfig* config_source_ = nullptr;
    bool enabled_ = false;
};

// ---------------------------------------------------------------------------
// OutputBackend：设备选择器（IHidOutput 兼容实现，AimThread 零改动）
// ---------------------------------------------------------------------------
class OutputBackend final : public IHidOutput {
public:
    struct Params {
        std::string kind;                 // local_hid | kmboxnet | makcu | ferrum | kmboxb
        // LocalHid
        std::string hidg_path = "/dev/hidg1";
        // KmboxNet（UDP 盒，协议框架实证；报文布局见 KmboxNetBackend UNVERIFIED 段）
        std::string kmboxnet_ip;          // 空=不启用
        uint16_t kmboxnet_port = 0;       // 0=使用协议默认
        uint16_t kmboxnet_monitor_port = 5001;
        uint32_t kmboxnet_timeout_ms = 300;
        std::string kmboxnet_uuid;        // 8 hex 或空
        bool kmboxnet_encrypted = false;
        // 串口类（makcu/ferrum/kmboxb）
        std::string serial_port;          // "auto" 或设备路径
        bool makcu_high_speed = true;
        // Gate / 运行时
        RuntimeConfig* runtime_config = nullptr;
        std::atomic<uint16_t>* button_source = nullptr;
        bool enabled = false;
    };

    OutputBackend() = default;
    ~OutputBackend();

    // 按 kind 创建并配置后端（返回 false 说明 kind 未知或参数非法）
    bool configure(const Params& p, std::string* error = nullptr);

    // 当前选中后端（未配置=nullptr）
    IOutputBackend* backend() { return backend_.get(); }
    const IOutputBackend* backend() const { return backend_.get(); }
    const Params& params() const { return params_; }

    // IHidOutput：AimThread 调用 send(OutputAction)。热路径拆解到选中后端。
    bool send(const OutputAction& action) override;

    // Gate 绑定透传到当前后端（与 AiboxHidOutput 相同接口，确保行为一致）
    void set_enabled(bool enabled);
    void set_button_source(std::atomic<uint16_t>* source);
    void set_config_source(RuntimeConfig* config);

private:
    std::unique_ptr<IOutputBackend> backend_;
    Params params_;
};

}  // namespace ttbox::core::output
