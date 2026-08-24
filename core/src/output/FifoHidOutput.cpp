// FifoHidOutput.cpp — 保持现有移动帧协议：0x01 + dx(int16 LE) + dy(int16 LE)。
#include "output/FifoHidOutput.hpp"
#if !defined(_WIN32)
#include <fcntl.h>
#include <unistd.h>
#include <cerrno>
#endif
namespace ttbox::core::output {
bool FifoHidOutput::open_if_needed() {
#if defined(_WIN32)
    return false;
#else
    if (fd_ >= 0) return true;
    fd_ = ::open(path_.c_str(), O_WRONLY | O_NONBLOCK);
    return fd_ >= 0;
#endif
}
void FifoHidOutput::close() {
#if !defined(_WIN32)
    if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
#endif
}
bool FifoHidOutput::send(const OutputAction& a) {
#if defined(_WIN32)
    (void)a; return false;
#else
    if (!open_if_needed()) return false;
    const unsigned char frame[5] = {0x01, static_cast<unsigned char>(a.move_x & 0xff),
        static_cast<unsigned char>((a.move_x >> 8) & 0xff), static_cast<unsigned char>(a.move_y & 0xff),
        static_cast<unsigned char>((a.move_y >> 8) & 0xff)};
    const ssize_t n = ::write(fd_, frame, sizeof(frame));
    if (n == static_cast<ssize_t>(sizeof(frame))) return true;
    if (errno == EPIPE || errno == ENXIO) close();
    return false;
#endif
}
}
