# YU 外设输出后端完整调查

- 日期：2026-08-30
- 调查对象：板端 192.168.0.53 /opt/aiassistance（YU 2026.08.03.1，daemon + web + usb-proxy 全家桶）
- 调查方式：真机进程/服务/socket/配置/二进制度符号/前端/权威脚本直接读取，**未引用网上资料**
- 结论可靠性：全部来自真机当前运行代码与配置；无设备接入的部分明确标注"未实证"

---

## 一、YU 输出后端全景（真机实证）

YU 的鼠标输出不是单一 HID，而是一个 **MouseOutputWorker 统一分发 + 5 类物理后端**：

```text
AimThread / 宏引擎 (recoil/rapid/backflick/auto_trigger)
        ↓
MouseOutputWorker（统一调度：backend 选择、重连、物理按键事件、阻塞掩码）
        ↓
┌─────────────┬──────────────┬──────────────┬─────────────┬─────────────┐
│ USB HID 透传  │  KMBOX NET    │    MAKCU      │   FERRUM     │  KMBOX B+    │
│ (usb-proxy)  │  (UDP 网络盒)  │  (串口代理)    │   (串口)     │  (串口)      │
└─────────────┴──────────────┴──────────────┴─────────────┴─────────────┘
```

### 各后端进程/服务（真机正在运行）

| 后端 | 进程/服务 | 状态 |
|---|---|---|
| USB HID 透传 | `usb-proxy --device=fc000000.usb --driver=dwc3-gadget --enable_mouse_control` | ✅ active（模拟罗技 046d:c53f 接收器） |
| KMBOX NET | `aiassistance-kmboxnet-usb.service`（USB 转网卡自动配置脚本） | ✅ active（`configure_kmboxnet_usb.sh --watch`） |
| MAKCU | `aiassistance-makcu.service` → `/opt/aiassistance/bin/makcu_mouse_proxy --root /opt/aiassistance` | ✅ active |
| FERRUM | 无独立进程（daemon 内串口后端） | ⚠️ 未插入设备 |
| KMBOX B+ | 无独立进程（daemon 内串口后端） | ⚠️ 未插入设备 |

---

## 二、USB HID 透传（当前主力，协议完整实证）

### 2.1 架构

YU 的"本机 HID"不是简单写 /dev/hidg0，而是 **raw_gadget 全量 USB 鼠标透传**：

```text
物理 USB 鼠标（Linux 侧）──usb-proxy──▶ dwc3-gadget ──▶ 主机（Windows）
                                            │
                                    cmd.sock / event.sock（AI 注入通道）
```

- 运行参数：`--vendor_id=046d --product_id=c53f --hid_passthrough_compat --auto_remap_endpoints --set_config_ack_before_configure --enable_mouse_control --mouse_control_cmd_socket=/run/orangepi-mouse-passthrough/cmd.sock --mouse_control_event_socket=/run/orangepi-mouse-passthrough/event.sock`
- 当前模式：`full_passthrough`（物理鼠标 + AI 合成混合输出）
- socket：`/run/orangepi-mouse-passthrough/{cmd,event}.sock`（AF_UNIX SOCK_SEQPACKET）
- 客户端工具：`/opt/usb-proxy/bin/usb-proxy-mouse-client`（Python，协议权威）

### 2.2 协议（usb-proxy-mouse-client 完整源码实证）

**报文头（8 字节，小端）**：
```
struct { uint16 magic; uint16 version; uint16 msg_type; uint32 request_id; }
MAGIC   = 0x4F50
VERSION = 1
```

**消息类型表**（完整实证）：

| 类型 | 值 | 方向 | Payload |
|---|---|---|---|
| PING_REQ | 1 | C→S | 空 |
| PING_RESP | 2 | S→C | 空 |
| ERROR_RESP | 3 | S→C | `<H` code + `<H` len + text |
| MOVE_CMD | 4 | C→S | `<iii`：dx, dy, wheel（int32 小端） |
| BUTTON_CMD | 5 | C→S | `<BB`：button, action |
| GET_STATE_REQ | 6 | C→S | 空 |
| GET_STATE_RESP | 7 | S→C | `<BQ`：button_mask, timestamp_ns |
| SUBSCRIBE_REQ | 8 | C→S(ev) | 空 |
| SUBSCRIBE_ACK | 9 | S→C(ev) | 空 |
| STATE_SNAPSHOT | 10 | S→C(ev) | `<BQ`：mask, ts |
| BUTTON_EVENT | 11 | S→C(ev) | `<BBBQ`：button, pressed, mask, ts |
| GET_CONFIG_REQ | 12 | C→S | 空 |
| GET_CONFIG_RESP | 13 | S→C | 见下 |
| SET_CONFIG_REQ | 14 | C→S | `<B` apply + 配置 |
| SET_CONFIG_RESP | 15 | S→C | `\x01` |

**按钮编码**：left=1 right=2 middle=3 back=4 forward=5（掩码 bit0-4 同顺序）

**动作编码**：down=1 up=2 click=3

**按键/移动使用方式**：
- move：`send_only(cmd, packet(MOVE_CMD, rid, pack("<iii", dx,dy,wheel)))`（fire-and-forget，无响应）
- button/click：`send_only`（fire-and-forget）
- ping/state/config：`request()`（等待响应、校验 magic/version/rid）

**配置结构（GET_CONFIG_RESP / SET_CONFIG_REQ payload）**：
```
固定段 <HHHHBBBHBBBB:
  usb_vid, usb_pid, usb_bcd_usb, usb_bcd_device,
  usb_device_class, usb_device_subclass, usb_device_protocol, usb_max_power,
  hid_protocol, hid_subclass, hid_report_length, hid_interval
后面 5 个 <H+bytes 字符串: usb_manufacturer, usb_product, usb_serial,
  usb_configuration, hid_report_desc_hex
```

### 2.3 真机实测结果

```
$ usb-proxy-mouse-client ping          → pong
$ usb-proxy-mouse-client state         → left=False right=False middle=False back=False forward=False timestamp_ns=...
$ usb-proxy-mouse-client config        → usb_vid=0x046d usb_pid=0xc53f hid_report_length=9
$ usb-proxy-mouse-client listen        → snapshot left=False ...（物理按键实时事件）
```

---

## 三、KMBOX NET（UDP 网络盒，协议部分实证）

### 3.1 架构

```text
daemon (KmboxNetClient) ──UDP──▶ kmbox 盒子（如 192.168.2.228）──USB──▶ 主机
                             ├─ command 端口（config.port）
                             └─ monitor 端口（config.monitor_port, 默认 5001）
```

- USB 自动配置：`configure_kmboxnet_usb.sh --watch`，识别 USB 网卡 `1a86:5397`（CH340 USB 转网），把网卡配到 `192.168.2.x` 网段，盒子默认 `192.168.2.228`，本地默认 `192.168.2.10/24`
- 客户端类（二进制度符号实证）：`KmboxNetClient`：connect/disconnect/ping/move(x,y,z)/button_down/button_up/click/send_connect/send_monitor/send_ping/send_mouse_command(seq, SoftMouse)/wait_for_ack(seq,timeout)/monitor_loop/start_monitor/stop_monitor/set_physical_state_handler

### 3.2 配置（config.json 实证）

```json
"kmboxnet": { "enabled": false, "encrypted": false,
              "ip": "", "monitor_port": 5001, "port": 0,
              "timeout_ms": 300, "uuid": "" }
```

- uuid：必须恰好 8 个 hex 字符（"kmboxNet UUID must be exactly 8 hex characters"）
- encrypted=true 走加密命令（"send encrypted kmboxNet command"）
- 错误处理字符串："IP is empty/invalid"、"response timeout/empty"、"is not connected"

### 3.3 已知命令流（符号实证）

```
send_connect → 建立会话（等待 ack）
send_ping    → 保活
send_mouse_command(seq, SoftMouse{dx,dy,z?}) → 移动
button_down/up/click(MouseButton)
wait_for_ack(seq, timeout) → 等待确认
send_monitor(port) → 启动 monitor 通道（盒子上报状态）
```

⚠️ **UDP 报文具体字节布局未实证**（daemon 为发布二进制、无可读源码；本机无 kmbox 盒子可报文抓包）。需要：买盒子抓包 / 反汇编 KmboxNetClient / 找 YU 提供的协议文档。**实现时此处必须真实验证，不得网上猜。**

---

## 四、MAKCU（串口 + 代理进程，架构实证）

### 4.1 架构

```text
daemon (MouseOutputWorker)
    ↕ AF_UNIX: /run/aiassistance-makcu/{cmd,event}.sock
makcu_mouse_proxy（独立进程，与 daemon 同时运行）
    ↕ 串口（端口 "auto" 自动扫描）
MAKCU 盒子 ──USB──▶ 主机
```

- 代理进程参数：`makcu_mouse_proxy [--root DIR] [--cmd-socket PATH] [--event-socket PATH]`
- daemon 通过 `AIASSISTANCE_MAKCU_MOUSE_CMD_SOCKET / AIASSISTANCE_MAKCU_MOUSE_EVENT_SOCKET` 找到代理
- Web 枚举：`/api/makcu/devices` → daemon_call("list_makcu_devices")

### 4.2 配置

```json
"makcu": { "enabled": false, "high_speed": true, "port": "auto" }
```

### 4.3 代理二进制功能词（strings 实证）

```
km.move(   km.buttons(0/1)   km.click(0,1)   km.baud(   km.serial(
MAKCU move command failed / button command failed / not connected
MAKCU failed to apply physical motion block mask
```

⚠️ 具体串口命令字节未实证（无 MAKCU 设备插入）。**待有设备后抓串口/读源码再定。**

---

## 五、FERRUM（串口，架构实证）

- 后端类：`FerrumSerialClient`（open serial / set speed / send_command / parse_input / reader_loop）
- 功能：move(x,y,z)、button_down/up、click、ping、set_physical_motion_block_mask、连接状态回调
- 识别函数：`TextLooksLikeFerrumSerial`、`is_ferrum`（自动识别串口）
- 配置：`"ferrum": { "enabled": false, "port": "auto" }`
- 错误："Ferrum serial is not connected/open/port was not found"、"unsupported Ferrum baud rate"
- Web：`/api/ferrum/devices` → `list_ferrum_devices`

⚠️ 协议字节未实证（无设备）。

---

## 六、KMBOX B+（串口，架构实证）

- 后端类：`kmboxb_serial.cpp` / `KmboxbSerialClient`
- 串口：115200 baud（"kmbox B+ only supports 115200 baud in this release"）
- 交互式文本协议：期望提示符 `>>>`（"command timed out waiting for >>>"），响应上限 8192B
- 命令词：`km.baud(`, `km.serial()`, `km.click(`, `km.move(`, `km.buttons(0/1)`、`B+ reset command`
- 配置：`"kmboxb": { "enabled": false, "port": "auto" }`
- Web：`/api/kmboxb/devices` → `list_kmboxb_devices`

⚠️ 提示符之后的命令字节未实证（无设备）。

---

## 七、统一调度（MouseOutputWorker，符号实证）

```
MouseOutputWorker
  ├─ ReconnectBackend()                    ← 断线重连（物理状态回调驱动）
  ├─ set_makcu_socket_paths(cmd, ev)
  ├─ set_ferrum_options(...)
  ├─ set_physical_state_handler(cb)        ← 物理按键状态 → 宏引擎
  ├─ set_physical_motion_block_mask(mask)  ← 瞄准时阻塞物理鼠标移动
  └─ 各 backend: connect(cb) / disconnect / move(x,y,z) / button_down/up / click / ping
```

- 物理状态类型：`PhysicalMouseState`（mask + timestamp，同 usb-proxy <BQ）
- 物理鼠标硬件：Web `/api/hardware/mouse` 返回连接状态/服务状态/模式/物理鼠标识别

---

## 八、Web API 面（app.py 实证）

| 端点 | 方法 | daemon 命令 |
|---|---|---|
| /api/hardware/mouse | GET/PUT | get_mouse_hardware / set_mouse_hardware |
| /api/hardware/mouse/mode | PUT | set_mouse_proxy_mode（full_passthrough/synthetic） |
| /api/hardware/mouse/timing | PUT | set_mouse_proxy_timing |
| /api/makcu/devices | GET | list_makcu_devices |
| /api/ferrum/devices | GET | list_ferrum_devices |
| /api/kmboxb/devices | GET | list_kmboxb_devices |
| /api/mouse-output/test-circle | POST | test_mouse_circle |
| /api/diagnostics/usb-proxy.zip | GET | （打包诊断） |

### 前端 UI 字段（app.js 实证，输出设备页组件）

```
kmboxnet 卡片: enabled / ip / port / monitor_port / timeout_ms / uuid / encrypted / 连接状态 / 重连
makcu 卡片: enabled / port(auto 或列表) / high_speed / 设备列表 / 状态
ferrum 卡片: enabled / port / 设备列表 / 状态
kmboxb 卡片: enabled / port / 设备列表 / 状态
mode 选择: passthrough / full_passthrough / synthetic
blocked_physical_buttons + 圆测(test-circle)
```

---

## 九、结论与迁移映射（TTBOX → YU 后端）

| TTBOX OutputBackend | 依据 YU | 可信度 |
|---|---|---|
| LocalHidBackend | TTBOX 现有 AiboxHidOutput（写 /dev/hidg0）；YU 用 usb-proxy 透传 | 二者并存；TTBOX 保现有实现（本轮基线） |
| KmboxNetBackend | UDP 客户端：配置结构/枚举/ack 流程实证；**报文布局待盒子抓包** | 中（协议框架实证） |
| MakcuBackend | 独立代理进程 + socket：架构实证；**串口命令待设备** | 中（架构实证） |
| FerrumBackend | 串口后端：架构实证；**命令待设备** | 低-中 |
| KmboxBBackend | 串口 115200 + `>>>` 提示符：架构实证；**命令待设备** | 低-中 |

**纪律**：协议字节布局未实证处一律标注 UNVERIFIED，实现前必须补真实抓包/设备验证，不引用网络资料猜测。

---

## 十、调查留下的物证（板端可复核）

```
/opt/usb-proxy/bin/usb-proxy-mouse-client     ← USB HID 协议权威源码（本文 2.2 节全文引用）
/opt/usb-proxy/bin/usb-proxy-synthetic        ← synthetic 模式 usb-proxy 主程序（help 实证）
/opt/usb-proxy/board/run_usb_proxy.sh         ← 运行参数（2.1 节实证）
/opt/aiassistance/bin/makcu_mouse_proxy       ← MAKCU 代理（strings 实证 4.3）
/opt/aiassistance/scripts/configure_kmboxnet_usb.sh ← kmboxnet 自动配网（3.1 实证）
/opt/aiassistance/config/config.json          ← 全部后端配置（真机实时值）
/opt/aiassistance/web/app.py                  ← 全部 Web API（8 节实证）
/opt/aiassistance/web/static/app.js           ← 前端 UI 字段（8 节实证）
/opt/aiassistance/run/daemon.sock             ← daemon 命令通道（JSON-Line 协议）
/run/orangepi-mouse-passthrough/cmd.sock      ← USB HID 注入通道（2.2 实测）
```