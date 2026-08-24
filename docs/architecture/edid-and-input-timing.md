# EDID 与输入时序架构

> 核心概念区分：
> - **EDID** = RX 端声明"允许 Source 输出的模式列表"（能力声明）
> - **V4L2** = HDMI 实际锁定的输入模式（G_FMT：width/height/pixelformat/实际帧率）
> - **Pipeline** = 根据 V4L2 实际时序**动态运行**
>
> **不要把 2K144 / 1080P240 写死**。它们只是能力标杆（白狼已验证：2560×1440@144、1920×1080@240）。
>
> **EDID 是商用输入兼容测试体系的一部分**：`resources/edid/` 提供真实显示器 EDID 测试资源库
> （来源 linuxhw/EDID，只收录真实 EDID 不伪造），`scripts/edid/` 提供校验/解析/加载/V4L2 验证工具；
> 测试矩阵见 `resources/edid/README.md`。

## 1. 概念与职责

| 概念 | 角色 | 数据来源 | 更新时机 |
|---|---|---|---|
| EDID | RX 向 Source 声明可输出模式 | hdmirx EDID 配置（`config/hdmirx_edid_identity.json` + EDID 脚本）+ 测试资源库 `resources/edid/` | 系统安装/恢复出厂/测试加载 |
| V4L2 G_FMT | 实际锁定模式（协商结果） | `/dev/video0` ioctl | 每次采集打开/时序变化 |
| 实际帧率 | 驱动 sequence + timestamp 差分 | V4L2 sequence（A-2 已采集 `sequence`/`timestamp_ms`） | 运行期持续 |
| Pipeline 参数 | 由实际时序推导 | Runtime 计算 | 时序变化时重配置 |

## 2. EDID 设计

- EDID 声明 RX 支持的模式集合（如：1080p60、1440p60/144、1080p240 等，视驱动/硬件能力）
- 声明为**能力**而非"要求"：Source 选择输出哪个模式由 Source 决定（游戏主机/PC 输出设置）
- 当前板端现状（实测）：hdmirx 被动接收，输入格式由信号源决定；当前信号源输出 1080p60 → V4L2 锁定 1920×1080 BGR3
- 2K144 可达条件：① EDID 声明 1440p144 ② Source 实际输出 1440p144 ③ V4L2 锁定 → 三者齐备才能实测（当前信号源不足，标注 UNTESTED）

```
EDID（声明能力）
   │ Source 协商
   ▼
Source 实际输出模式
   │ HDMI RX
   ▼
V4L2 实际锁定时序  ←── 唯一事实来源（G_FMT + sequence）
   │
   ▼
Pipeline 动态运行（RGA 目标尺寸/坐标映射/帧率上限均由它决定）
```

## 3. V4L2 时序检测（A-2 已具备的基础）

- `G_FMT`：width/height/pixelformat/num_planes/bytesperline/sizeimage（A-2 `FormatInfo` 已采集）
- `sequence` 差分 → 实际帧率（A-2 已采集 `FrameInfo.sequence`）
- `timestamp_ms` → 帧间隔/抖动（A-2 已采集）
- 无信号/时序变化 → `poll` 超时 / `EIO` → Runtime 识别为"待机"或"重协商"

## 4. Pipeline 动态参数推导

| 参数 | 推导 | 用途 |
|---|---|---|
| 缩放源 | V4L2 width×height | RGA 输入 |
| 缩放目标 | 模型输入（640×640 等） | RGA 输出 |
| 坐标映射 | 原图 ↔ 模型 缩放系数（sx=W/w, sy=H/h） | Decode 回原图（Python decode.py 已有语义） |
| 帧率上限 | V4L2 实际帧率 | 统计/丢帧判定 |
| 中心裁剪 | 若采用 crop 语义（A-3 决策） | 源 → 正方形 → 模型 |

- **禁止**在代码中写死 `1920×1080` / `2560×1440` / `240` / `144`；全部读取运行时实际值
- 时序变化检测：每帧对比 G_FMT（或定期重查）；变化 → Runtime 事件 → pipeline 重配置 → 日志记录新旧时序

## 5. 分辨率/帧率转换（能力参考，非固定模式）

| 场景 | V4L2 实际 | RGA | 模型 | 预期 |
|---|---|---|---|---|
| 当前 | 1920×1080@60 | crop/resize | 640×640 | Capture 60fps，AI 受推理吞吐限制（latest-frame 丢帧） |
| 标杆 1 | 2560×1440@144 | crop/resize | 640×640 | Capture 144fps（需信号源） |
| 标杆 2 | 1920×1080@240 | crop/resize | 640×640 | Capture 240fps（需信号源） |

> AI 吞吐（当前基线 infer ~74ms → 13.5fps）远低于输入帧率 → **latest-frame 语义 + drop 统计**是必须的；实际展示的 Input FPS 与 Inference FPS 分开（Web 已要求分别显示）。

## 6. EDID 测试资源库与工具（商用输入兼容测试体系）

- **资源库** `resources/edid/`：按时序分类（1080p240/1440p144/1440p165/4K120/other），
  每个 EDID 保存 `.hex`（linuxhw 原始）+ `.bin` + `.json`（厂商/型号/分辨率/刷新率/接口/像素时钟/色彩格式）
- **来源**：[linuxhw/EDID](https://github.com/linuxhw/EDID) 真实显示器 EDID，**只收录真实、不自行伪造**
- **工具** `scripts/edid/`：
  - `edid_info.py`：解析 EDID（厂商/型号/DTD 时序/接口/像素时钟/checksum）
  - `edid_verify.py`：hex→bin 转换 + 块长度 + checksum 校验
  - `edid_load.sh`：板端测试加载 EDID 并触发重协商（仅测试/能力声明）
  - `v4l2_timing.sh`：V4L2 实际时序验证（G_FMT / DV timings / 实测帧率）
- **Test Matrix**：见 `resources/edid/README.md`（1080p60/120/240、1440p144/165、4K120；
  仅「EDID 声明 + Source 输出 + V4L2 锁定」三者成立才 PASS）
- 当前 1K240 目标：**ASUS VG259QM EDID 已就绪**（1920x1080@240，DP，594.27MHz），待源输出 240fps 实测
- 这些工具为**测试辅助**（Python/Shell），不进入正式 AI 高速链路

## 7. 实现落地（阶段 E / 当前）

- EDID 工具（生成/写入 EDID 配置，`vendor/legacy/app/hdmirx_edid.py`、`scripts/hdmirx_edid.sh`、`config/hdmirx_edid_identity.json`）
- 时序检测单元（基于 A-2 FormatInfo + sequence）
- 动态重配置事件；1080P240/1440P144 真机验证（走 `resources/edid/` + `scripts/edid/`）

## 8. 验收口径

- Web 显示的实际 Input FPS 必须来自 V4L2 sequence 实测，不得使用配置值/猜测值
- Web 同时展示：EDID 声明能力、当前 V4L2 实际输入、当前 Pipeline FPS——**不允许把 EDID FPS 当成实际 FPS**
- 2K144 / 1080P240 仅在"EDID 声明 + Source 输出 + V4L2 锁定"三者成立时标注 PASS，否则如实标注 UNTESTED/受限
