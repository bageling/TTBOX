# AIBOX Package Map（离线事实）

分析对象：用户提供的四类 Debian 包；仅离线解包、读取文件和静态内容，未安装、未执行、未访问包内云端地址。

## 组件

| 包 | 版本/架构 | 关键产物 | 职责 |
|---|---|---|---|
| `aibox-rk3588` | 1.0.2-13 / arm64 | `/usr/bin/aibox`、`/usr/lib/aibox/aibox-bl`、RKNN runtime、model、`aibox.service` | RK3588 AI Core |
| `aiboxkm` | 1.0.90 / arm64 | `/usr/bin/aiboxkm`、`aiboxkm.service`、udev rules | USB HID Proxy |
| `web-aibox` | 1.0.3 / all | Flutter web `/opt/web-aibox/web`、`web-aibox.service` | 设备 Web 前端 |
| `autobl` | 1.0.15 / arm64 | `/opt/autobl/CloudFileManagerBackend`、`/opt/autobl/webui`、`cloud-file-manager.service` | 白狼控制台/API/升级编排 |

## 依赖事实

`aibox-rk3588` 依赖 Poco、OpenCV、RGA、RKNN、systemd；`aiboxkm` 依赖 kmod、udev、systemd；`autobl` 依赖 Poco/SSL/spdlog/fmt/systemd。AIBOX Core 以单一 `aibox-bl` 为高速链路，控制台通过本地服务命令/状态接口编排。

## 安全边界

包内存在云存储配置和凭证；本项目不复制凭证，不访问包内云端，不提交 `.deb`、ELF 或第三方源码。
