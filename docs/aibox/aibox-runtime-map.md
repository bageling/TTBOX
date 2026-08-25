# AIBOX Runtime Map

## 实际链路

```text
systemd
  ├─ aibox.service → /usr/bin/aibox → aibox-bl → HDMI/V4L2 → RGA → RKNN → aim/HID
  ├─ aiboxkm.service → /usr/bin/aiboxkm → USB gadget/hidraw/hidg
  ├─ web-aibox.service → nginx/static Flutter Web
  └─ cloud-file-manager.service → :5200 HTTPS/HTTP backend → system tools/service control
```

## Core 事实

`aibox-bl.md.gz` 描述 Core 为 RK3588 单可执行产品：V4L2/DMA、RGA、RKNN、多 NPU mailbox、独立 aim thread、Poco WebSocket 8080/8081、FunctionFS/f_hid HID。本次不修改 TTBOX 算法或高速链路。

## 启停行为

Core unit 使用 `User=aibox`、`WorkingDirectory=/var/lib/aibox`、`AIBOX_CONFIG_PATH=/etc/aibox`、`Restart=always`、`RestartSec=5`，并通过 `ExecReload=/bin/kill -HUP $MAINPID` 支持重载。
