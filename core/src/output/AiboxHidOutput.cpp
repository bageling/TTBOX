// AiboxHidOutput.cpp — AIBOX 兼容鼠标报告写入 /dev/hidg0
#include "output/AiboxHidOutput.hpp"
#if !defined(_WIN32)
#include <fcntl.h>
#include <unistd.h>
#include <cerrno>
#endif
namespace ttbox::core::output {
bool AiboxHidOutput::open_if_needed() {
#if defined(_WIN32)
    return false;
#else
    if (fd_ >= 0) return true;
    fd_ = ::open(path_.c_str(), O_WRONLY | O_NONBLOCK);
    return fd_ >= 0;
#endif
}
void AiboxHidOutput::close() {
#if !defined(_WIN32)
    if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
#endif
}
bool AiboxHidOutput::send(const OutputAction& a) {
#if defined(_WIN32)
    (void)a; return false;
#else
    // 安全门：未显式启用时不写入真实鼠标 Gadget。
    if (!enabled_) return false;
    if (button_source_ && (button_source_->load(std::memory_order_acquire) & button_mask_) == 0) return false;
    if (!open_if_needed()) return false;
    // 当前 gadget 鼠标描述符要求 9 字节：ReportID=2 + buttons(16bit LE)
    // + X(int16 LE) + Y(int16 LE) + wheel + pan。
    // 只注入鼠标移动，不改按钮状态。
    const unsigned char report[9] = {
        0x02, 0x00, 0x00,
        static_cast<unsigned char>(a.move_x & 0xff), static_cast<unsigned char>((a.move_x >> 8) & 0xff),
        static_cast<unsigned char>(a.move_y & 0xff), static_cast<unsigned char>((a.move_y >> 8) & 0xff),
        0x00, 0x00};
    const ssize_t n = ::write(fd_, report, sizeof(report));
    if (n == static_cast<ssize_t>(sizeof(report))) return true;
    if (errno == EPIPE || errno == ENXIO) close();
    return false;
#endif
}
}  // namespace ttbox::core::output
