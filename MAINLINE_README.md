# TTBOX 当前主线源码

本目录是当前 TTBOX 主线的整理副本。

## 主线核心
- `core/`：唯一主线 C++ 核心源码，包含 capture、RGA、RKNN、model、target、mouse、HID、IPC、runtime、tests。
  - 高速链路：HDMI/V4L2 → DMA-BUF → RGA → RKNN WorkerPool → Decode/NMS → AimTargetMailbox → AimThread(Pid1Controller+AlphaBetaGamma) → HID/FIFO
  - 当前板端实测：Capture ~147 FPS、Detection ~147 FPS、E2E P50 ~11.5ms

## 配套源码
- `scripts/`：当前部署、EDID、HID、转换工具脚本，以及生产 Web 网关 `ttbox_gateway.py`。
- `web/static/`：Web 前端桥接层 `ttbox-bridge.js`（yu body ↔ RuntimeProfile 双向翻译、MJPEG 预览流）。
- `config/`：当前配置模板。
- `hid/`：当前 HID 描述和配置。
- `docs/`：架构、协议、性能和升级文档。

## 非主线
原目录中的 `vendor/legacy/`、`backup-*`、历史调试脚本不纳入本目录。
旧控制链（AiboxPpidController / SmithPredictor / test_aim_algorithm）已删除，由 Pid1Controller + AlphaBetaGammaFilter 替代。

## 约定
以后修改优先在本目录验证，再同步到部署目录。参数链路必须端到端真实生效（Web→Gateway→IPC→RuntimeConfig→worker），禁止前端假值。
