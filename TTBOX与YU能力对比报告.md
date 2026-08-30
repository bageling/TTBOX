# TTBOX 与 YU 能力对比报告

- 日期：2026-08-30
- 对比对象：板端 yu（/opt/aiassistance，版本 2026.08.03.1） vs TTBOX（/opt/ttbox，本仓库主线）
- 方式：真机板端逐项核对（yu 100+ API 路由、config 全键、daemon 二进制功能词）+ TTBOX 全部 IPC/网关/config 键

---

## 一、总体结论

**yu 是完整"AI 外挂/自瞄平台"（AI + 射击宏 + 鼠标盒子 + 校准 + 远程 + 运维全家桶），TTBOX 目前只实现了核心 AI 检测/瞄准链路 + 基础 Web 换皮。**

差距分四层：
1. **消费级功能层（最缺）**：压枪/连点/回拉/准星检测/运动训练/校准/个人化运动曲线——TTBOX 完全没有
2. **外设层**：kmboxnet/makcu/ferrum/kmboxb 多鼠标盒子——TTBOX 只有本机 HID/FIFO
3. **平台层**：主题/远程拉模型/云授权/WiFi/扩容/OTA 更新——TTBOX 只有基础授权桩
4. **后端 API 面**：yu 100+ 端点 vs TTBOX 14 个——差了近 10 倍

---

## 二、逐项对比表

### 2.1 核心 AI 检测/瞄准链路（TTBOX 强项 ✅）

| 能力 | yu | TTBOX | 备注 |
|---|---|---|---|
| HDMI 采集 (V4L2/DMA-BUF) | ✅ | ✅ | TTBOX 更快：Capture 147 FPS |
| RGA 硬件缩放/ROI | ✅ | ✅ | 都有 |
| RKNN 推理 WorkerPool | ✅ | ✅ | TTBOX 3 worker 147 FPS |
| Hailo 加速后端 | ✅ | 无 | yu 支持 Hailo NPU 后端 |
| Decode/NMS | ✅ | ✅ | TTBOX 单输出+DFL 6输出 |
| 目标选择/跟踪 | ✅ | ✅ | TTBOX 有 TargetSelector |
| PID 控制 | ✅ (PID+Predict+Smooth) | ✅ (Pid1Controller+AlphaBetaGamma) | 都有，参数不同 |
| FOV 限制 | ✅ | ✅ | 都有 |
| 预测 lead | ✅ (continuous_lead) | ⚠️ 简单 predict | yu 有连续预测(渐变+近距禁用) |
| 检测置信度/IoU 热更新 | ✅ | ✅ | 本轮已打通 |
| 实时预览 | ✅ (MJPEG) | ✅ (MJPEG) | 本轮已修 |

### 2.2 消费级功能（TTBOX 缺失 ❌）

| 功能 | yu | TTBOX | 说明 |
|---|---|---|---|
| **压枪 Recoil** | ✅ | ❌ | 强度/速度/人机化曲线/触发延迟 |
| **连点 Rapid Fire** | ✅ | ❌ | 点击间隔/持续按住/热键 |
| **回拉 Auto Back Flick** | ✅ | ❌ | 甩枪后回拉/避弹/随机方向 |
| **准星检测 Crosshair** | ✅ | ❌ | 检测准星颜色/ROI |
| **自动开火 Auto Trigger** | ✅ | ❌ | 瞄准自动击发/连发模式/压枪联动 |
| **运动训练 Motion Training** | ✅ | ❌ | 采集手部动作样本训练个人运动模型 |
| **个人运动曲线 Personal Motion** | ✅ | ❌ | 个人手部运动曲线混合 |
| **校准 Calibration** | ✅ | ❌ | 鼠标增益/响应延迟标定 |
| **人机化 Humanize** | ✅ | ❌ | jitter/曲线/频率（TTBOX 只有 bezier 遗留参数） |
| **拉枪曲线 Pull Curve** | ✅ | ⚠️ | yu 有完整参数；TTBOX 有 bezier 参数但未接入 |
| **瞄准热键/多套配置** | ✅ (aim_profiles) | ⚠️ 单套 | yu 支持多 profile+热键切换 |
| **FOV/灵敏度/偏移** | ✅ | ⚠️ 部分 | yu 有 fov_scale/offset_switch/class_offset |

### 2.3 外设输出层（TTBOX 缺失 ❌）

| 外设 | yu | TTBOX | 说明 |
|---|---|---|---|
| 本机 HID 输出 | ✅ | ✅ | TTBOX 有 FifoHid/AiboxHid |
| **KMBox-Net (网络鼠标盒)** | ✅ | ❌ | TCP/UDP 协议 |
| **MAKCU 鼠标盒** | ✅ | ❌ | 独立 makcu_mouse_proxy |
| **FERRUM / KMBox-B** | ✅ | ❌ | 串口/其他盒子 |
| CATNet | ✅ | ❌ | 网络外设 |
| 物理鼠标阻塞 | ✅ | ⚠️ | yu 支持 aiming 时阻塞物理鼠标 |

### 2.4 平台/运维层（TTBOX 缺失 ❌）

| 能力 | yu | TTBOX | 说明 |
|---|---|---|---|
| 主题系统 | ✅ | ❌ | 在线主题+预览+换肤 |
| 远程拉取模型 | ✅ | ❌ | 云端/远程服务器导入模型 |
| 模型加密 | ✅ (.rknn.enc) | ❌ | 加密模型解密加载 |
| 云授权/激活 | ✅ | ⚠️ 本地桩 | yu 完整在线授权流程 |
| 模型上传云端 | ✅ | ❌ | 板端上传模型到云端 |
| OTA 更新 | ✅ | ❌ | 检查/下载/安装/回滚 |
| WiFi 管理 | ✅ | ❌ | 扫描/连接/AP热点 |
| LAN 屏蔽名单 | ✅ | ❌ | 局域网设备封锁 |
| 磁盘扩容 | ✅ | ❌ | 一键扩容 rootfs |
| 主机名/端口配置 | ✅ | ❌ | |
| 风扇控制 | ✅ | ❌ | 温度→PWM |
| 诊断导出 (usb-proxy.zip) | ✅ | ❌ | |
| 系统状态/存储监控 | ✅ | ❌ | |
| 公告系统 | ✅ | ❌ | |
| 手机端页面 (mobile) | ✅ | ❌ | yu 有 mobile 模板 |

### 2.5 API 面（TTBOX 严重偏少）

| 维度 | yu | TTBOX |
|---|---|---|
| Web API 端点 | **100+** | **14** |
| IPC 命令 | — | 11 |
| 覆盖域 | 授权/主题/更新/Hailo/系统/WiFi/运动/校准/外设/模型/预览 | 状态/配置/预览/模型/运行时 |

yu 独有 API 组（TTBOX 没有）：themes*、activation*、update*、hailo*、wifi*、motion-profiles*、motion-training*、control/calibration*、makcu/ferrum/kmboxb devices*、remote/*、diagnostics/*、presets*、events、announcement、xcsh background、lan-blocklist、system/*。

---

## 三、性能对比（TTBOX 优势区）

| 指标 | yu | TTBOX | 说明 |
|---|---|---|---|
| Capture FPS | ~30 | **147** | TTBOX capture_buffers=12 |
| Inference FPS | 27 (3 ctx) | **147** | TTBOX 3 worker 最新帧直通 |
| E2E 延迟 | ~80ms | **P50 11.5ms** | TTBOX 明显更低 |
| Preview | 单帧/流 | MJPEG 流 12fps | 已修 |
| NPU 占用 | 高 (Hailo 混合) | 低 (26-28%) | TTBOX 余量大 |

> yu 性能数据来自板端实测（rknn_context_bench + GET_STATUS），TTBOX 为本轮实测。

---

## 四、TTBOX 现有但 yu 没有/不同的（我们的特色）

- **Capture buffer 饥饿修复**：capture_buffers 动态配置，Capture 满速 147
- **Decode 自动分发**：单输出 / DFL 多输出 / E2E 三形态自动识别（yu 按固定模型格式）
- **分位延迟观测**：E2E/Infer/Decode P50/P95/P99 真实统计（yu 无）
- **参数端到端热更新**：Web→Gateway→IPC→RuntimeConfig→worker 无重启生效
- **DetectionGeometryFilter**：检测几何过滤（头部/身体配对过滤）

---

## 五、优先级建议（按差距影响排序）

### P0（外挂核心体验，最该补）
1. **外设输出层**：至少支持一个网络鼠标盒（kmboxnet 或 makcu），否则无法脱离物理鼠标用
2. **压枪 Recoil + 连点 Rapid Fire + 回拉 AutoBackFlick**：这仨是消费级自瞄的核心卖点，TTBOX 一个没有
3. **校准 Calibration**：鼠标增益标定，没它压枪/瞄准手感不对

### P1（体验完整度）
4. **人机化 Humanize**：jitter/曲线/频率，防检测关键
5. **多套 aim_profiles + 热键切换**：按游戏/场景切换配置
6. **手机端页面 mobile**：应急/演示方便

### P2（平台化）
7. **OTA 更新 + 模型加密**：产品化必备
8. **主题系统**：换肤，非核心但成本低
9. **远程模型拉取**：依赖服务器端，可后置

### P3（锦上添花）
10. WiFi 管理 / 风扇控制 / 诊断导出 / 公告
11. 准星检测 / 自动开火 / 运动训练（最复杂，最后做）

---

## 六、结论

**性能（采集/推理/延迟）TTBOX 已反超 yu；功能面（外设/宏/平台）TTBOX 只有 yu 的约 1/4。**

yu 是 8 个游戏通用、支持 3 种推理后端、5 种鼠标输出、带完整射击宏和运维体系的商业级平台。TTBOX 目前是"极速 AI 检测引擎 + 基础瞄准"，缺的是把 AI 结果转化成"能用的外挂功能"的那一层。

最短路径：先补 **kmboxnet 外设输出 + Recoil/RapidFire/AutoBackFlick 三件套**，就能把 147 FPS 的检测能力变成真正可用的产品。
