// AiboxHidOutput.hpp — AIBOX 兼容的 /dev/hidg0 直接输出后端
// 报告格式: buttons(16bit LE) + X(int16 LE) + Y(int16 LE) + wheel(8) + pan(8)
#pragma once
#include "output/IHidOutput.hpp"
#include <string>
#include <atomic>
namespace ttbox::core::output {
class AiboxHidOutput final : public IHidOutput {
public:
    explicit AiboxHidOutput(std::string hidg_path = "/dev/hidg1") : path_(std::move(hidg_path)) {}
    ~AiboxHidOutput() override { close(); }
    bool send(const OutputAction& action) override;
    void set_enabled(bool enabled) { enabled_ = enabled; }
    void set_button_source(std::atomic<uint16_t>* source, uint16_t mask) { button_source_ = source; button_mask_ = mask; }
    void close();
private:
    bool open_if_needed();
    std::string path_;
    int fd_ = -1;
    bool enabled_ = false;
    std::atomic<uint16_t>* button_source_ = nullptr;
    uint16_t button_mask_ = 0;
};
}  // namespace ttbox::core::output
