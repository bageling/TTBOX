# AIBOX Service Map

| Unit | ExecStart | User | Restart | 依赖/用途 |
|---|---|---|---|---|
| `aibox.service` | `/usr/bin/aibox` | `aibox:aibox` | always/5s | AI Core |
| `aiboxkm.service` | `/usr/bin/aiboxkm` | `root:aiboxkm` | 当前包 `no` | USB HID Proxy，ExecStartPre modprobe libcomposite |
| `web-aibox.service` | 包内 Web 服务/静态栈 | 以 unit 实际值为准 | 需板端确认 | Flutter Web |
| `cloud-file-manager.service` | `/opt/autobl/CloudFileManagerBackend` | root:root | always/1s | 白狼控制台，network-online 后启动 |

AIBOX `postinst` 创建 sysuser/tmpfiles、enable unit、daemon-reload，并按包分别启动或重载服务。TTBOX 本阶段只复制能力模型，未复制安装脚本中的危险 shell 命令。

## TTBOX 对应

Platform Supervisor 将把 Core、HID、Web、Control Plane 作为声明式组件；具体 systemd adapter 后续接入，不让 Web 直接散落执行 systemctl。
