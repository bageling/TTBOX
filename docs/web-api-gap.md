# Web API 缺口审计（Web API Gap）

> 审计对象：TTBOX Core IPC 当前 11 条消息（v0.3）与 Web 需求对照。
> 结论先行：**Web 当前页面全部所需能力已由现有 IPC 覆盖，无必须新增的 API。**
> 本文件记录"已存在但未暴露/需要但暂缓"的候选缺口，扩展前必须先评估。

## 现有 IPC 清单与 Web 对应

| 消息 | 请求 | 成功响应 | 错误码 | Web 用途 |
|---|---|---|---|---|
| `PING` | `{"type":"PING"}` | `{status:0,data:{pong:true}}` | — | 健康检查 |
| `GET_STATUS` | `{"type":"GET_STATUS"}` | `{status:0,data:{running,runtime_running,app_name,version,uptime_ms,ipc_socket,config_file,metrics{...}}}` | 3=未注册 provider | 总览/系统状态轮询 |
| `GET_CONFIG` | `{"type":"GET_CONFIG"}` | `{status:0,data:{扁平宿主键值, runtime_profile:<紧凑JSON字符串>}}` | 3=未注册 | 全站配置读取 |
| `SET_CONFIG` | `{"type":"SET_CONFIG","params":{"profile":{...}}}` | `{status:0,data:{applied:true,persisted:bool}}` | 1=校验失败/缺参；3=未注册 | 全部参数修改（原子：解析→validate→update→落盘） |
| `RUNTIME_CONTROL` | `{"type":"RUNTIME_CONTROL","params":{"action":"start\|stop\|restart"}}` | `{status:0,data:{action}}` | 1=非法 action；3=未注册 | 总览启停 |
| `MODEL_LIST` | `{"type":"MODEL_LIST"}` | `{status:0,data:{models:[...],active}}` | 3=未注册 | 模型库/总览当前模型 |
| `MODEL_IMPORT` | `{"type":"MODEL_IMPORT","params":{"src_path","model_id","label?"}}` | `{status:0,data:{model_id}}` | 1=路径越权/非法 id；3=未注册 | 模型上传 |
| `MODEL_VALIDATE` | `{"type":"MODEL_VALIDATE","params":{"model_id"}}` | `{status:0,data:{model_id,action}}` | 1=staging 缺失/校验失败 | 模型校验 |
| `MODEL_INSTALL` | `{"type":"MODEL_INSTALL","params":{"model_id"}}` | `{status:0,data:{model_id,action}}` | 1=staging 缺失 | 模型安装 |
| `MODEL_ACTIVATE` | `{"type":"MODEL_ACTIVATE","params":{"model_id"}}` | `{status:0,data:{model_id,action}}` | 1=未安装；3=未注册 | 模型启用 |
| `MODEL_REMOVE` | `{"type":"MODEL_REMOVE","params":{"model_id"}}` | `{status:0,data:{model_id,action}}` | 1=激活中拒绝/未安装 | 模型删除 |

## 错误码/超时/离线行为（Web 客户端处理契约）

| 场景 | IPC 行为 | HTTP 层（Gateway） | Web 处理 |
|---|---|---|---|
| 请求非法 | status=1 + error 中文 | 400 | toast.error(显示 error) |
| provider 未注册 | status=3 | 502 | "服务内部错误" |
| 未知 type | status=4 | 501 | 不应出现（客户端只发已知 type） |
| Core 离线 | 连接失败/拒绝 | 502 "无法连接 Core IPC" | 全局离线态 + 自动重连 |
| 请求超时 | 客户端 timeout（当前 2s） | 504 | 离线态处理（同连接失败） |
| JSON 非法 | 服务端 status=1 | 400 | 客户端 JSON 由统一 Client 序列化，不应触发 |
| SET_CONFIG 校验失败 | status=1 + validate 原文（如"confidence 必须在 [0,1]"） | 400 | toast.error 原样展示，**不更新本地配置** |

## 候选缺口（记录，暂不实现）

### GAP-A：`/api/state` 全量聚合端点
- **现状**：Web 轮询需 3 个请求（status/config/models）才能渲染总览。
- **参考**：yu 真机（192.168.0.53:8080）用 `GET /api/state` 一次返回全量。
- **价值**：减少请求数、简化前端；**优先级：中**（3 个并行请求在 5s 轮询下可接受）。
- **实现方式**：Gateway 侧聚合（不动 Core IPC）；或前端 `Promise.all`（已可做，无需新 API）。

### GAP-B：模型热加载（activate 后立即生效）
- **现状**：Core 无热加载；activate 后需 restart AI 流水线。
- **价值**：用户体验提升；**优先级：低**（涉及 Core 推理链重载，属算法/运行时改动，需专项）。

### GAP-C：真实 PipelineMetrics（FPS/延迟）
- **现状**：`metrics` 是占位结构，恒 0；Web 显示"暂无数据"。
- **价值**：总览最核心的实时数字；**优先级：高**（但需板端 WorkerPool/AimThread 统计接入，Core 侧任务）。

### GAP-D：AimThread 实时状态（目标状态/热键状态）
- **现状**：AimThread::Status 有 consumed/target_frames/gated_frames 等，**未并入 GET_STATUS**。
- **价值**：总览"目标状态"（yu 的 aim.active 对应物）；**优先级：中**。
- **实现方式**：Application::status_provider 里把 aim_thread_.status() 并入 metrics 段（Core 小改，不破坏现有字段）。

### GAP-E：MJPEG 预览
- **现状**：无出流。
- **价值**：总览实时画面 + FOV 圈（yu 核心卖点）；**优先级：高**（板端 V4L2 已就绪，需新端点，Core 侧任务）。

### GAP-F：预设参数保存/载入
- **现状**：无。
- **价值**：多方案切换；**优先级：低**（可基于 GET/SET_CONFIG 在 Gateway 侧做文件快照，不动 Core）。

## 执行纪律

1. 上述 GAP 均**不立即扩展 IPC**；涉及 Core 的（B/C/D/E）单独立项评审。
2. Web 只消费现有 11 条消息；Gateway 可做聚合/文件类（A/F），Core 协议保持不变。
3. 新增页面/控件前先查 web-capability-map.md，不在表内 = 不做。
