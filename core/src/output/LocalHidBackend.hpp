// LocalHidBackend.hpp — 本机 HID 输出后端（迁移自 AiboxHidOutput，行为不变）
#pragma once

#include "output/OutputBackend.hpp"
#include <string>

namespace ttbox::core::output {

// 本机 /dev/hidg0 直接输出。报告格式与 AiboxHidOutput 完全一致：
//   buttons(16bit LE) + X(int16 LE) + Y(int16 LE) + wheel(8) + pan(8)，共 9 字节。
// Hotkey Gate / mouse.enabled 实时判定在基类 gate_allows() 中（与 AiboxHidOutput 相同）。
class LocalHidBackend final : public IOutputBackend {
public:
    explicit LocalHidBackend(std::string hidg_path = "/dev/hidg1")
        : path_(std::move(hidg_path)) {}
    ~LocalHidBackend() override { disconnect(); }

    bool connect(std::string* error = nullptr) override;
    void disconnect() override;
    bool reconnect(std::string* error = nullptr) override;
    BackendHealth health() const override;

    bool mouse_move(int32_t dx, int32_t dy, int32_t wheel = 0) override;
    bool mouse_button(uint8_t button, uint8_t action) override;
    bool mouse_click(uint8_t button) override;

    const char* name() const override { return "local_hid"; }

private:
    bool open_if_needed();
    bool write_report(const unsigned char report[9]);
    bool emit_button_report();  // 只带按键状态变化的报告（dx=dy=wheel=0）

    std::string path_;
    int fd_ = -1;
    mutable BackendHealth health_;
    // 当前按下的按钮掩码。为什么后端自己要存：**每份报告都要带上按键状态** ——
    // 此前 mouse_move 硬编码 buttons=0 ⇒ 按下左键后只要鼠标一动，报告就把按键写成"全松开"，
    // 表现为"点了没反应 / 一移动就断"。按键与位移在同一份报告里，必须一起维护。
    uint16_t button_state_ = 0;
};

}  // namespace ttbox::core::output
