// main.cpp — TTBox C++ Core 入口
//
// 生命周期：初始化 Application → run（事件循环）→ 正常退出。
// 支持 SIGINT / SIGTERM 优雅退出。
#include <csignal>
#include <cstdio>

#include "app/Application.hpp"
#include "common/Logger.hpp"

namespace {

void signal_handler(int) {
    // async-signal-safe：仅置原子标志，由 Application::run() 轮询退出
    ttbox::core::Application::request_shutdown();
}

}  // namespace

int main(int argc, char** argv) {
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    ttbox::core::Application app;
    int rc = app.initialize(argc, argv);
    if (rc != 0) {
        return rc;
    }

    app.run();
    app.shutdown();
    return 0;
}
