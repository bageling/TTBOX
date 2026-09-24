// mouse_control.hpp — TTBOX usb-proxy mouse_control 协议层（自研 0x4F50 协议）
// 对应 --enable_mouse_control 功能。
#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <vector>

#include "hid_report_layout.hpp"

namespace ttbox_usbproxy {

// ── 协议常量（自研，0x4F50）──
constexpr uint16_t kMagic = 0x4F50;
constexpr uint8_t kVersion = 1;

constexpr uint8_t kPingReq = 1;
constexpr uint8_t kPingResp = 2;
constexpr uint8_t kErrorResp = 3;
constexpr uint8_t kMoveCmd = 4;
constexpr uint8_t kButtonCmd = 5;
constexpr uint8_t kGetStateReq = 6;
constexpr uint8_t kGetStateResp = 7;
constexpr uint8_t kSubscribeReq = 8;
constexpr uint8_t kSubscribeAck = 9;
constexpr uint8_t kStateSnapshot = 10;
constexpr uint8_t kButtonEvent = 11;
constexpr uint8_t kGetConfigReq = 12;
constexpr uint8_t kGetConfigResp = 13;
constexpr uint8_t kSetConfigReq = 14;
constexpr uint8_t kSetConfigResp = 15;

// 按钮位掩码（自研）
constexpr uint8_t kBtnLeft = 1;
constexpr uint8_t kBtnRight = 2;
constexpr uint8_t kBtnMiddle = 4;
constexpr uint8_t kBtnBack = 8;
constexpr uint8_t kBtnForward = 16;

// 动作
constexpr uint8_t kActDown = 1;
constexpr uint8_t kActUp = 2;
constexpr uint8_t kActClick = 3;

#pragma pack(push, 1)
struct PacketHeader {
    uint16_t magic;
    uint8_t version;
    uint8_t type;
    uint32_t request_id;
};
#pragma pack(pop)

// ── Gadget 配置（GET_CONFIG_RESP 载荷，字段顺序自研定义）──
struct GadgetConfig {
    uint16_t usb_vid = 0x9A80;
    uint16_t usb_pid = 0x7072;
    uint16_t usb_bcd_usb = 0x0200;
    uint16_t usb_bcd_device = 0x0100;
    uint8_t usb_device_class = 0;
    uint8_t usb_device_subclass = 0;
    uint8_t usb_device_protocol = 0;
    uint8_t usb_max_power = 250;
    uint8_t hid_protocol = 2;       // boot
    uint8_t hid_subclass = 1;
    uint8_t hid_report_length = 4;  // synthetic 基本报告
    uint8_t hid_interval = 1;
    std::string manufacturer = "Corsair";
    std::string product = "Corsair USB Optical Mouse";
    std::string serial = "OPI5P-MOUSE";
    std::string configuration = "Mouse";
    std::string hid_report_desc_hex =
        "05010902a1010901a100050919012905150025019505750181029501750381010501"
        "0930093109381581257f750895038106c0c0";
};

// 一个 USB 接口的物理鼠标布局（从该接口的 HID report descriptor 解析得到）
struct InterfaceLayout {
    bool ready = false;    // 是否收到过该接口的描述符
    bool usable = false;   // 解析成功且找到了 X/Y 或 buttons
    HidMouseDescriptor desc;
};

// ── 共享状态 ──
struct MouseControlState {
    // AI 注入挂起位移（物理报告到达时"搭车"合并；synthetic 模式直接注入）
    std::atomic<int32_t> pending_dx{0};
    std::atomic<int32_t> pending_dy{0};
    std::atomic<int32_t> pending_wheel{0};

    // 物理按钮掩码（来自物理报告解析 / BUTTON_CMD）
    std::atomic<uint8_t> button_mask{0};

    // 事件订阅者连接（event.sock）
    std::mutex subscribers_mutex;
    std::vector<int> subscribers;

    // 运行时开关
    std::atomic<bool> mouse_control_enabled{false};
    std::atomic<bool> synthetic_mode{false};

    // ── 物理报告布局（每接口一份，从描述符解析）──
    //
    // 为什么是"每接口"：一个 dongle 常带 键盘/鼠标/厂商 多个 HID 接口，各接口的报告
    // 描述符不同；把键盘报告当鼠标改写会把按键字节写成位移。故只对"该接口自己的
    // 描述符里解析出了 X/Y（位移报告）或 buttons（按键报告）"时才动手。
    //
    // ★ 解析不出来 ⇒ ready/usable 为 false ⇒ merge/notify 一个字都不碰（fail-closed）。
    //   这就是 2026-09-24「自瞄一动就疯狂切枪」的修法：宁可不动，也不乱写。
    std::mutex layout_mutex;
    InterfaceLayout iface_layouts[8];   // 索引 = USB interface_number（HID 设备不会超 8 个）

    // 旧的写死布局字段（罗技 c53f 假设）：**已不再用于物理报告准入/写入**。
    // 保留仅为协议与面板字段兼容（GadgetConfig 的 hid_* 是合成 gadget 用的另一回事）。
    std::atomic<uint8_t> report_id{2};
    std::atomic<int> x_offset{3};
    std::atomic<int> y_offset{5};
    std::atomic<int> report_len{9};

    // 统计
    std::atomic<uint64_t> move_count{0};
    std::atomic<uint64_t> merge_count{0};
    std::atomic<int64_t> last_move_ts_us{0};
};

extern MouseControlState g_state;
extern GadgetConfig g_gadget_config;

// ── 接口 ──
// 启动 cmd.sock/event.sock 服务（独立线程）。返回 0 成功。
int mouse_control_start(const std::string& cmd_socket,
                        const std::string& event_socket,
                        bool synthetic);
void mouse_control_stop();

// 将当前 g_gadget_config 持久化到 gadget-config.json（重启后生效）
int persist_gadget_config();

// 收到物理设备某接口的 HID report descriptor（ep0 转发路径上）时调用：
// 解析并存进 iface_layouts[interface_number]。解析失败 ⇒ 该接口标记为不可用。
// 幂等：同一接口拿到相同内容重复调用无副作用（只重解析一次）。
void mouse_control_set_report_descriptor(uint8_t interface_number,
                                        const uint8_t* desc, uint32_t len);

// 查询某接口的解析结果（诊断用）。返回 false = 该接口还没有可用布局。
bool mouse_control_get_layout(uint8_t interface_number, HidMouseDescriptor* out);

// 物理报告到达时调用：将挂起 AI 位移合并进 HID 报告的 X/Y 字段。
// data/len 指向物理鼠标 HID 报告（可能原地修改）。返回是否发生合并。
bool mouse_control_merge_report(uint8_t interface_number, uint8_t* data, uint32_t len);

// 物理报告解析：按该接口解析出的 buttons 字段更新按钮掩码 + 通知订阅者。
void mouse_control_notify_physical_report(uint8_t interface_number,
                                          const uint8_t* data, uint32_t len);

// synthetic 模式：从挂起位移构造一个合成 HID 报告。
// 返回报告长度（>0 表示有数据要发）。
int mouse_control_build_synthetic_report(uint8_t* out, uint32_t cap);

}  // namespace ttbox_usbproxy
