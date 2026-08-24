# aibox2 v0.01

OrangePi RK3588 AI 采集/推理/自瞄系统 — 模块化重构。

## 架构

```
aibox2/
├── aibox/
│   ├── main.py            # 统一 CLI (ai / web / passthrough / doctor / version)
│   ├── config.py          # 运行时配置 (默认值/校验/热重载)
│   ├── pipeline.py        # AI 主流水线 (采集→推理→自瞄→输出)
│   ├── capture/           # HDMI 采集 (V4L2) + EDID
│   ├── inference/         # RKNN 引擎 + YOLO 解码 + 模型管理
│   ├── aim/               # 自瞄 (PID+预测+贝塞尔拟人)
│   ├── output/            # 键鼠透传客户端
│   ├── web/               # Web 控制台 (标准库)
│   └── utils/             # 原子 IO
├── models/                # RKNN 模型
├── config/                # 运行时配置 JSON
├── tests/                 # 单元测试
└── vendor/legacy/        # 参考实现 (勿改)
```

## 快速开始

```bash
# 环境自检
python -m aibox doctor

# 启动 AI 流水线 (板端)
python -m aibox ai --model models/yolo261n-rk3588.rknn

# 启动 Web 控制台
python -m aibox web --port 8080
```

## 测试

```bash
python -m pytest tests/ -v
```

## 进程架构

```
web (aibox.web.console :8080) --共享文件--> ai (aibox.pipeline) --UNIX socket--> passthrough (键鼠透传)
```

- Web 与 AI 通过 `.ai_state.json` / `config/default.json` 共享状态与配置 (原子写)
- AI 与 passthrough 通过 `/tmp/km_passthrough.sock` (JSON UDP) 通信
- 故障隔离: 任一进程崩溃不影响其他进程
