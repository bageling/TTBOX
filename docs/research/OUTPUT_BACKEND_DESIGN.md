# TTBOX OutputBackend 设计

- 日期：2026-08-30
- 依据：docs/research/YU_OUTPUT_BACKEND_RESEARCH.md（YU 真机实证）
- 纪律：AimThread 零改动、不破坏 PID1/Hotkey Gate、不降低 147 FPS、协议字节未实证处标注 UNVERIFIED

---

## 一、设计目标

```text
                    ┌─ LocalHidBackend   （现有 AiboxHidOutput 迁移）
AimThread           ├─ KmboxNetBackend   （UDP 网络盒，框架实证）
    ↓               ├─ MakcuBackend      （socket+代理，架构实证）
OutputAction        ├─ FerrumBackend     （串口，架构实证）
    ↓               └─ KmboxBBackend     （串口 115200，架构实证）
OutputBackend
    ↓
Physical Device
```

1. **AimThread 只产 OutputAction（dx/dy/buttons），不判断设备类型** —— 现有 send() 调用点保持。
2. **OutputBackend 是设备选择器**：按配置选一个后端，统一 connect/send/health 语义。
3. **每个后端独立文件**（core/src/output/），协议实现不进入 AimThread。
4. **Hotkey Gate / mouse.enabled 保持在后端内**（与现 AiboxHidOutput 相同的实时判定逻辑）。
5. **性能要求**：send() 路径零阻塞、零分配（不改变 AimThread 4ms 周期节拍）。

---

## 二、接口设计

### 2.1 后端基类（IOutputBackend）

```cpp
// output/OutputBackend.hpp
namespace ttbox::core::output {

// 物理设备连接状态（Web 展示 + health 轮询）
enum class BackendState {
    kDisconnected,   // 未连接 / 未启用
    kConnecting,     // 正在连接
    kConnected,      // 已连接可用
    kError,          // 出错（重试中/需人工）
};

struct BackendHealth {
    BackendState state = BackendState::kDisconnected;
    std::string detail;          // 人类可读描述（Web 显示）
    int64_t last_send_ok_us = 0; // 最近一次成功发送时刻
    uint64_t send_ok = 0;        // 成功计数
    uint64_t send_fail = 0;      // 失败计数
    uint64_t reconnect_count = 0;
};

// 统一后端接口：实现 = 一种物理设备协议
class IOutputBackend {
public:
    virtual ~IOutputBackend() = default;

    // 生命周期：connect 建立/保持连接；disconnect 主动断开；reconnect 断线重连
    virtual bool connect(std::string* error = nullptr) = 0;
    virtual void disconnect() = 0;
    virtual bool reconnect(std::string* error = nullptr) = 0;

    // 健康状态（供 Web /api/*/devices 与内部保活）
    virtual BackendHealth health() const = 0;

    // 核心输出：鼠标移动 / 按键 / 点击 / 键盘
    virtual bool mouse_move(int32_t dx, int32_t dy, int32_t wheel = 0) = 0;
    virtual bool mouse_button(uint8_t button, uint8_t action) = 0;   // 按钮编码/动作见协议
    virtual bool mouse_click(uint8_t button) = 0;

    // 后端类型标识（日志/Web）
    virtual const char* name() const = 0;

    // Hotkey Gate / mouse.enabled 总闸（与现 AiboxHidOutput 一致，实时判定）
    void set_button_source(std::atomic<uint16_t>* s) { button_source_ = s; }
    void set_config_source(RuntimeConfig* c) { config_source_ = c; }
    void set_enabled(bool e) { enabled_ = e; }

protected:
    // 供子类发送前调用：返回 false 表示被 Gate 拦截（不发送）
    bool gate_allows() const;

    std::atomic<uint16_t>* button_source_ = nullptr;
    RuntimeConfig* config_source_ = nullptr;
    bool enabled_ = false;
};

// 按钮/动作编码（对齐 YU usb-proxy 实证；各后端映射到自己协议）
constexpr uint8_t kBtnLeft = 1, kBtnRight = 2, kBtnMiddle = 3, kBtnBack = 4, kBtnForward = 5;
constexpr uint8_t kActDown = 1, kActUp = 2, kActClick = 3;

}  // namespace ttbox::core::output
```

### 2.2 设备选择器（OutputBackend）

```cpp
// output/OutputBackend.hpp
class OutputBackend final {
public:
    struct Params {
        std::string kind;                 // "local_hid" | "kmboxnet" | "makcu" | "ferrum" | "kmboxb"
        std::string hidg_path = "/dev/hidg1";
        std::string kmboxnet_ip;          // 空=禁用
        uint16_t kmboxnet_port = 0;       // 0=默认
        uint16_t kmboxnet_monitor_port = 5001;
        uint32_t kmboxnet_timeout_ms = 300;
        std::string kmboxnet_uuid;        // 8 hex 或空
        bool kmboxnet_encrypted = false;
        std::string serial_port;          // makcu/ferrum/kmboxb 端口（"auto" 或路径）
        bool makcu_high_speed = true;
        // 运行时配置/Hotkey Gate
        RuntimeConfig* runtime_config = nullptr;
        std::atomic<uint16_t>* button_source = nullptr;
        bool enabled = false;
    };

    bool configure(const Params& p, std::string* error = nullptr);
    IOutputBackend* backend();            // 当前选中后端（未配置=nullptr）
    const Params& params() const;

private:
    std::unique_ptr<IOutputBackend> backend_;
    Params params_;
};
```

### 2.3 与现有 IHidOutput 的关系（平滑迁移）

- `OutputBackend` **实现旧的 `IHidOutput::send(OutputAction)`**，即作为 IHidOutput 的兼容实现：
  - `send(OutputAction)`：Gate 判定 → 取 dx/dy/buttons → 按 backend 分发
  - 这样 **AimThread / CoreRuntime 完全零改动**（仍持 `shared_ptr<IHidOutput>`）
- 迁移顺序：
  1. `LocalHidBackend` 内部挪入 AiboxHidOutput 的 report 逻辑（行为不变）
  2. `OutputBackend` 包装：`AimThread → OutputBackend(send) → LocalHidBackend`，回归测试
  3. 新增 `KmboxNetBackend`（协议框架实证；报文布局待抓包）
  4. makcu/ferrum/kmboxb 留接口与配置，待设备验证后填充

---

## 三、Hotkey Gate 与总闸（保持现状）

现 AiboxHidOutput 的 Gate 逻辑（**原样迁移到基类**）：

```text
send 入口
  ├─ enabled_ == false            → 拦截（静态总闸）
  ├─ config_source_ == null       → 拦截（fail-closed）
  ├─ !mouse.enabled               → 拦截（运行时总开关）
  ├─ mask = aim_hotkey|aim_hotkey2; mask==0 → 拦截（配置缺失）
  ├─ button_source_ & mask == 0   → 拦截（热键未按下）
  └─ 通过 → 设备发送
```

---

## 四、与 Web 的对应关系（UI 1:1，不删 YU 页）

| Web 组件（YU 原 UI） | TTBOX API | 后端字段 |
|---|---|---|
| 输出设备模式选择（passthrough/full_passthrough/synthetic） | /api/v1/output/config (PUT) | Params.kind |
| kmboxnet 卡片（IP/port/monitor/timeout/uuid/encrypted/连接状态） | /api/v1/output/kmboxnet (GET/PUT) | kmboxnet_* |
| makcu 卡片（port/设备列表/状态） | /api/v1/output/makcu/devices | serial_port / health |
| ferrum 卡片 | /api/v1/output/ferrum/devices | serial_port / health |
| kmboxb 卡片 | /api/v1/output/kmboxb/devices | serial_port / health |
| 测试输出（圆测） | /api/v1/output/test-circle | mouse_move 循环 |
| 物理鼠标状态 | /api/v1/output/status | health + 连接 |

注：未实现的后端在 UI 上保持"尚未接入"文案（YU 页面不删）。

---

## 五、性能纪律

- `send()` 热路径：无锁（后端内部原子计数）、无堆分配、无 JSON、无日志（成功时）
- 连接/health 为慢路径：独立于发送线程（connect 失败仅置 health，不影响现有 send）
- 回归门槛：Capture ≈147 / Detection ≈147 / E2E P50 ≈11.5ms 不下降

---

## 六、实现顺序（本阶段）

1. ✅ 调查（docs/research/YU_OUTPUT_BACKEND_RESEARCH.md）
2. ⬜ OutputBackend.hpp + IOutputBackend + 选择器（本文件）
3. ⬜ LocalHidBackend（迁移 AiboxHidOutput，不改 report/协议/行为）
4. ⬜ OutputBackend 包装接入（AimThread 零改动）、本地回归
5. ⬜ KmboxNetBackend（connect/disconnect/reconnect/health/move/button；报文布局 UNVERIFIED 段标注）
6. ⬜ 测试：backend switch / invalid / unavailable / fallback / hotkey gate / zero movement / target lost
7. ⬜ 真机验证 + 性能回归