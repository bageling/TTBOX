# TTBOX 当前功能地图（2026-08-30）

> 数据源：TTBOX Core C++（review/TTBOX-main）+ 已部署板端服务（/opt/ttbox）+ Web（ttbox-web）。

## Core 已具备能力（真实可用）

### 流水线（RK3588 板端实测通过）
- V4L2 采集（HDMI RX，BGR3，DMA-BUF 零拷贝）
- RGA 硬件缩放（center crop + ROI 热更新）
- RKNN NPU 推理（1~3 worker 分帧，librknnrt 2.3.2，yolov8n 实测 3 核 30%）
- Decode/NMS + 几何过滤（confidence/IOU/max_detections/类别过滤）
- TargetSelector（单目标锁定/防横跳/丢失重获）
- Pid1Controller（kp/kd/ki/predict/rate/smooth，1:1 移植）
- AimThread + Hotkey Gate 三层（无热键零注入 fail-closed）
- HID 输出（FIFO/AiboxHID，实时 mask 读取）
- 模型管理（staging/validate/install/activate/remove，删除保护）

### IPC（11 条，全部实测）
PING / GET_STATUS / GET_CONFIG / SET_CONFIG / RUNTIME_CONTROL
MODEL_LIST / MODEL_IMPORT / MODEL_VALIDATE / MODEL_INSTALL / MODEL_ACTIVATE / MODEL_REMOVE

### 真实指标（G1 已接线）
采集FPS（滚动）、推理FPS、处理帧数、丢帧数、推理耗时(avg)、解码耗时、E2E 耗时、FP16转换耗时、最近帧目标数、运行时长

## Web 已实现（ttbox-web，shadcn + @base-ui）
| 页面 | 状态 |
|---|---|
| 总览 | ✅ 状态卡/启停/快速调整(辅助范围/瞄准点偏移/置信度)/指标卡(unavailable 正确显示)/输入信号行 |
| 辅助设置 | ✅ AI开关/主副热键/触发方式 |
| 移动控制 | ✅ 24 字段 schema 驱动（拉力/预判/刹车/跟随/平滑/丢失宽限），中文白话+调高调低+立即生效 |
| 检测与范围 | ✅ 置信度/IOU/最大检测/FOV |
| 模型库 | ✅ 列表/上传/校验/安装/启用/删除保护 |
| 画面输入 | ✅ HDMI 状态（GET_HDMI，只读） |
| 系统状态 | ✅ 自检卡 + 连接状态 |

## 明确没有的能力（诚实清单）
- 视频预览出流（无 JPEG/MJPEG 编码）
- 板端温度/CPU/内存采集
- 压枪/连点/背闪/准星找色（无此模块）
- 预设保存/加载（无 IPC）
- 自动标定（无校准流程）
- 热键多档案（RuntimeProfile 仅单套热键）
- 拉枪曲线/持续提前量（PID1 无此算法）
- 硬件管理（显示器写入/USB HID 描述符/盒子协议）
- WiFi/风扇/Hailo/主题/授权/更新（平台层未实现）
