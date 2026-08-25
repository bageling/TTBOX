# AIBOX API Map

## 已确认的本地控制面

白狼后端默认 `server.port=5200`，SSL 可开，线程 10，静态根 `./webui`。API 字符串和 Web JS 交叉显示服务控制、模型/包安装、EDID、系统、数据采集、认证和版本历史能力。

## TTBOX 映射

```text
AIBOX /api/aibox/*        → TTBOX /api/v1/runtime
AIBOX /api/tools/*        → TTBOX /api/v1/update + /api/v1/system
AIBOX model operations    → TTBOX /api/v1/models
AIBOX config storage      → TTBOX /api/v1/config
AIBOX status/log behavior → TTBOX /api/v1/health + /api/v1/logs
```

HTTP server 尚未在本次提交实现；Runtime Controller 保持 Web 无关。
