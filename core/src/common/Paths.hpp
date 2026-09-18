// Paths.hpp — TTBOX Core 运行期路径单点真源（A-PATH-5 / B-CONST-2）
//
// 为什么需要本头：同一路径字面量（IPC socket / 鼠标透传 socket / license 文件）曾散落在
// C++/Python/shell 共 20+ 处；改一处漏一处即"连不上 core"。规约：
//   · C++ 侧这些默认值**只允许**在本头定义一次，其余 .cpp/.hpp 一律引用（B-CONST-1）；
//   · 跨语言（Python/shell/usbproxy）无法 include 本头 ⇒ 以
//     docs/protocols/config-path-env-registry.md 登记 + scripts/ttbox_conventions_gate.sh
//     同值断言保证不漂移（A-PATH-5）。
//
// 取值链（A-PATH-2）：CLI 参数 > 环境变量 > 本头默认。
#pragma once

namespace ttbox::core::paths {

// IPC 控制通道 socket 默认（env 覆盖名 = TTBOX_IPC_SOCKET）。
inline constexpr const char* kIpcSocketDefault = "/run/ttbox/core.sock";

// 鼠标透传（usb-proxy）控制/事件 socket 默认。与 usbproxy 进程**必须同值**才通
// （usbproxy/usb-proxy.cpp 的 mouse_cmd_socket / mouse_event_socket 由门禁断言对齐）。
inline constexpr const char* kMouseCmdSocketDefault = "/run/ttbox-mouse-passthrough/cmd.sock";
inline constexpr const char* kMouseEventSocketDefault = "/run/ttbox-mouse-passthrough/event.sock";

// 系统级授权凭据文件（--license > 本文件 > 配置 > Store，见 Application::resolve_license_card）。
inline constexpr const char* kSystemLicenseFile = "/etc/ttbox/license.key";

}  // namespace ttbox::core::paths
