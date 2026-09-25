// IHidOutput.hpp — AI 输出后端抽象。
// AimThread 只生成 OutputAction，不直接写 FIFO/HID，避免控制逻辑与设备输出耦合。
#pragma once
#include <cstdint>
namespace ttbox::core::output {

// 按钮位掩码（与 MouseProfile.aim_hotkey / HOTKEY_BITS 同域：1 左 2 右 4 中 8 后 16 前）
constexpr uint8_t kMaskLeft = 0x01;
constexpr uint8_t kMaskRight = 0x02;
constexpr uint8_t kMaskMiddle = 0x04;
constexpr uint8_t kMaskBack = 0x08;
constexpr uint8_t kMaskForward = 0x10;

// 掩码 → 协议按钮编号（1=左 2=右 3=中 4=后 5=前；与 usb-proxy 的 BUTTON_CMD 一致）。
// 多位置位时取**最低位**（扳机一帧只点一个键，掩码本就只有一位）。
inline uint8_t button_index_from_mask(uint8_t mask) {
    if (mask == 0) return 0;
    uint8_t i = 1;
    while ((mask & 0x01) == 0) { mask >>= 1; ++i; }
    return i;
}

struct OutputAction {
    int16_t move_x = 0;
    int16_t move_y = 0;
    uint8_t button_mask = 0;
    uint8_t control_flags = 0;
    uint64_t frame_number = 0;
    uint64_t timestamp_us = 0;
};
class IHidOutput {
public:
    virtual ~IHidOutput() = default;
    virtual bool send(const OutputAction& action) = 0;
    // ---- 按键注入（自动扳机用）----
    // button 一律是**位掩码**，action 见 OutputBackend.hpp 的 kActDown/kActUp/kActClick。
    // 默认实现返回 false（不支持按键注入）⇒ 老后端行为不变，调用方自行处理失败。
    virtual bool mouse_button(uint8_t button, uint8_t action) {
        (void)button; (void)action; return false;
    }
    virtual bool mouse_click(uint8_t button) { return mouse_button(button, 3); }
};
class NullHidOutput final : public IHidOutput {
public:
    bool send(const OutputAction&) override { return true; }
};
}  // namespace ttbox::core::output
