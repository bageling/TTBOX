# TTBOX_WEB_CAPABILITY.md — TTBOX 当前能力盘点（Web 视角）

> 基于 `C:/Users/Administrator/Desktop/TTBOX/review/TTBOX-main` 源码 + 昨晚板端实测（0.53 并存部署）。

## TTBOX Core 已实现模块（全部可运行）

| 模块 | 状态 | 说明 |
|---|---|---|
| RuntimeConfig / RuntimeProfile | ✅ | 配置模型：validate(isfinite 总闸) → 内存原子替换 → 持久化 |
| Application | ✅ | 装配/生命周期/授权占位 |
| IpcServer | ✅ | 11 条消息：PING / GET_STATUS / GET_CONFIG / SET_CONFIG / RUNTIME_CONTROL / MODEL_LIST / MODEL_IMPORT / MODEL_VALIDATE / MODEL_INSTALL / MODEL_ACTIVATE / MODEL_REMOVE |
| ModelRegistry / ModelManagement | ✅ | 文件级 validator（非空+≥1KB）、staging/installed、白名单防穿越 |
| Capture (V4L2) | ✅ | 真机实测 1920x1080 BGR3 @240Hz，STREAMON OK |
| RGA | ✅ | 板端 librga 1.9.1 + 官方头文件，RgaProcessor 全链 |
| RKNN (NPU) | ✅ | 板端 librknnrt 2.3.2 + 自转 yolov8n_rk3588.rknn，三核 worker 实测 30% 负载 |
| TargetSelector | ✅ | 单目标/交叉不横跳/消失重获/排序翻转（6 用例测试） |
| Pid1Controller | ✅ | pid1 1:1 移植（Kp/Kd/Ki/Predict/Rate/Smooth 全支持） |
| Hotkey Gate | ✅ | 三层门（周期判定+reset+send 兜底）+ AiboxHidOutput 实时读 mask |
| HID (FifoHidOutput/AiboxHidOutput) | ✅ | fail-closed，无配置源拒绝注入 |
| AimThread | ✅ | Gate+pid1 接线，int16 clamp，余数 finite 兜底 |

## TTBOX 当前 IPC（11 条，非 5 条）

```
PING / GET_STATUS / GET_CONFIG / SET_CONFIG / RUNTIME_CONTROL
MODEL_LIST / MODEL_IMPORT / MODEL_VALIDATE / MODEL_INSTALL / MODEL_ACTIVATE / MODEL_REMOVE
```

## TTBOX 真实能力 vs yu 前端需求（Web 可接部分）

| 能力 | TTBOX 现状 | Web 接法 |
|---|---|---|
| 配置读 | GET_CONFIG 全量扁平 | 直接映射 |
| 配置写 | SET_CONFIG（validate→原子替换→落盘） | 直接映射，回读 canonical |
| 启停 | RUNTIME_CONTROL start/stop/restart | 直接映射 |
| 模型列表/导入/切换 | MODEL_* 五条 | 直接映射（multipart 上传→IPC） |
| 实时画面 | ❌ Core 无出流（V4L2 采集但无编码/HTTP 输出） | **缺口** |
| 温度/CPU/内存 | ❌ Core 不采集系统资源 | **缺口**（板端可另起小服务） |
| SSE 事件推送 | ❌ IPC 是请求-响应 | **缺口**（可加轮询替代） |
| 预设保存/加载 | ❌ 无 presets 概念 | **缺口**（Web 端本地存 JSON 或 Core 加 IPC） |
| 自动校准 | ❌ 无校准流程 | **缺口**（暂不做） |
| 个人移动曲线训练 | ❌ 无 | **放弃**（C 级） |
| 压枪/连点/背闪/准星找色 | ❌ 无 | **放弃**（C 级，TTBOX 无此能力） |
| WiFi/风扇/Hailo/主题/盒子/更新/授权 | ❌ 无 | **放弃**（C 级，yu 生态专属） |

## TTBOX Web 现有页面（本机 dev 已跑通）

总览 / 辅助设置 / 移动控制 / 检测与范围 / 模型库 / 画面输入 / 系统状态 —— 7 页，中文白话，真实 API。
