# yu 真机（192.168.0.53:8080）功能盘点 → TTBOX 对照表

> 数据来源：真机 SSH（root/orangepi，OrangePi 5+ RK3588，Ubuntu 22.04）+ 板端 44 个 API 端点实测 + 98 个 data-config 控件 + 本地 yu 前端源码。
> 结论先行：**yu 前端 44 API / 98 控件 / 12 页面；TTBOX 当前可真实复现的核心功能约 60%**（AI 瞄准+检测+模型+启停全链路），
> yu 专属硬件功能（Hailo/KMB/压枪/连点/WiFi/风扇/EDID 注入等）TTBOX 没有对应 Core 能力 → 全部不做。

## 一、yu 页面 × TTBOX 对照

| yu 页面 | yu 实际内容（真机核实） | TTBOX 是否已有 | 对应 Core/API | Web 已接入 | 分级 |
|---|---|---|---|---|---|
| 01 总览 | 主控大圆钮 + 链路延迟/采集FPS/检测FPS/温度/目标状态 + 实时预览(MJPG+FOV圈+瞄准点) + FOV半径/瞄准点偏移/置信度/IoU 直调 | 部分 | GET_STATUS（fps/e2e_ms 占位）+ GET_CONFIG + RUNTIME_CONTROL | ✅ 主控/指标卡/快速调整；⏸ 预览（GAP-E） | A |
| 02 热键控制 | 热键总开关(hotkey_guard) + 移动倍率(sens) + PID 参数 | 部分 | mouse.aim_hotkey/2/mode + enabled（无 hotkey_guard 总闸——enabled 即总闸） | ✅ 辅助设置页 | A |
| 03 移动控制 | 页签：PID / 拉枪曲线 / 持续提前量；参数 X轴拉力/预判/积分/刹车/跟随/死区 | 部分 | mouse.kp/kd/predict/rate/smooth（无 ki/死区消费者、无 pull_curve/continuous_lead 消费者） | ✅ 拉力/预判/刹车/跟随/平滑；⏸ 无消费者字段不显示 | A |
| 04 辅助功能 | 压枪 / 开火 / 连点 / 背闪 / 十字线 | ❌ | Core 无消费者（recoil/rapid_fire/crosshair/back_flick 字段不存在） | — | C |
| 05 模型库 | 模型列表/上传/选择/bind-preset/类别名 | ✅ | MODEL_* 六消息 | ✅ | A |
| 06 显示与鼠标 | HDMI/EDID 配置 + 鼠标模式 | 部分 | 板端有 rk_hdmirx（v4l2 可读 1920x1080@240 实际输入！）+ /dev/hidg | ⏸ HDMI 页（见 ⑥） | A |
| 07 Hailo-8 | Hailo 状态/安装 | ❌ | 板端无 Hailo（/opt/hailort 存在但 TTBOX 不用） | — | C |
| 08 键鼠盒子 | kmbox/catnet/makcu/ferrum | ❌ | 无 | — | C |
| 09 网络配置 | WiFi AP/Client/黑名单 | ❌ | 无 | — | C |
| 10 预设参数 | 预设保存/加载/导入 | ⏸ | 无 API（可基于 GET/SET_CONFIG 做） | — | B |
| 11 系统状态 | 更新/存储/重启/关机/版本 | 部分 | GET_STATUS + 板端 system 接口 | ✅ 系统状态页 | A |
| 12 风扇控制 | PWM 风扇 | ❌ | 无 | — | C |

## 二、yu 44 个 API 端点 × TTBOX 对照

| 分类 | yu 端点 | TTBOX 对应 |
|---|---|---|
| 核心（可复现） | /api/state, /api/config, /api/control/start|stop, /api/models, /api/models/select, /api/models/import, /api/models/delete, /api/license | GET_STATUS/GET_CONFIG/SET_CONFIG/RUNTIME_CONTROL/MODEL_*（✅ 全部有） |
| 硬件（TTBOX 板端有，API 待接） | /api/hardware/display（HDMI/EDID 真数据！） | ⏸ 板端 rk_hdmirx 已出 1920x1080@240，Web 无对应页（GAP 记录） |
| yu 专属（不做） | hailo/install, kmbox/catnet/makcu/ferrum, wifi/*, fan_control, presets/import, update/*, system/reboot|poweroff, lan-blocklist, mouse-output/test-circle, diagnostics/* | ❌ |
| 状态（TTBOX 缺） | /api/state 一次全量 | ⏸ GAP-A（Gateway 聚合，不动 Core） |

## 三、真机硬件实测（0.53）

- 系统：OrangePi 5+（RK3588）Ubuntu 22.04，7.8G 内存，14G 盘
- **HDMI RX 实际输入：1920x1080 @ 239.99Hz**（/dev/video0 rk_hdmirx，BGR3）——真机基准
- EDID：JEF-635040（1080p240 原生）；advertised 1080p60，available 含 1080p240/1440p144
- 温度：57-58°C（thermal zone）
- 服务：aiassistance-web.service（Waitress 8080）+ aiassistance_daemon（未激活 locked 状态）
- 模型：model-list.json = []（空）
- /dev/hidg* 无（未激活未挂 gadget）

## 四、TTBOX 页面重排（第五阶段结论）

① 总览（主控+指标+快速调整+预览占位）② 辅助设置（开关/热键）③ 移动设置（拉力/响应/提前量/平滑）④ 检测设置（灵敏度/重叠/数量）⑤ 模型 ⑥ HDMI 输入（真机数据可接）⑦ 系统状态（自检）⑧ 预设（B 级，暂缓）

> ⑥ HDMI：TTBOX 板端 rk_hdmirx 直接可读实际输入（0.53 已证明），Web 侧待加 /api/v1/hdmi 端点（Gateway 透传板端 v4l2-ctl/EDID 数据，Core 不动）——优先级中。
