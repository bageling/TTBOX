# 后台 API（Backend API）

> 设备 ↔ 云端接口定义。全部 HTTPS REST + JSON。
> 关键约束：**Backend 是管理面，非 AI 运行依赖**；设备离线时 AI Core 完全自治（见 `device-validation.md`）。

## 1. 通用约定

- Base URL：`https://<backend>/api/v1`
- 认证：设备注册后获得 `device_id` + `device_token`，后续请求带 `Authorization: Bearer <token>`
- 错误码（HTTP + body.code）：

| code | 含义 |
|---|---|
| 0 | OK |
| 1001 | 未认证 / token 失效 |
| 1002 | 设备未注册 |
| 1003 | License 无效/过期 |
| 2001 | 版本不存在 |
| 2002 | 兼容性不满足 |
| 2003 | 校验和不匹配 |
| 3001 | 请求格式错误 |

## 2. 端点一览

| 方法 | 路径 | 用途 | 设备端调用方 |
|---|---|---|---|
| POST | `/devices/register` | 设备注册（一次性，换取 device_id/token） | Agent |
| POST | `/devices/auth` | 认证 / 刷新 token | Agent |
| GET | `/devices/{id}/status` | 上报设备状态（含版本/时序） | Agent |
| GET | `/devices/{id}/license` | 查询 License（含宽限期） | Agent |
| POST | `/license/validate` | 离线激活/验证授权 | Agent |
| GET | `/updates/manifest` | 拉取 Update Manifest | Agent |
| GET | `/updates/{unit}/download` | 下载更新包（.ttbox-update） | Agent |
| GET | `/models` | 可用模型列表（含版本/校验和/兼容） | Agent/Web |
| GET | `/models/{id}/download` | 下载模型 | Agent/Web |
| GET | `/configs/templates` | 配置模板/默认配置 | Agent/Web |
| POST | `/telemetry` | 遥测上报（FPS/错误/温度，可选、节流） | Agent |

## 3. 请求/响应示例

### 3.1 设备注册
```json
POST /devices/register
{ "sn": "TTBOX-XXXX", "board": "orange-pi-5-plus", "firmware": "0.3.0" }
→ 200
{ "code": 0, "device_id": "dev-1234", "device_token": "tok-xxxx", "expires_at": 1735689600 }
```

### 3.2 License 查询
```json
GET /devices/dev-1234/license
→ 200
{
  "code": 0,
  "license": {
    "status": "active",                 // active | expired | grace | revoked
    "issued_at": 1720000000,
    "expires_at": 1751536000,           // 授权过期时间
    "grace_period_s": 604800,           // 宽限期（秒，7 天）
    "features": ["aim", "recognition"]
  }
}
```

### 3.3 更新 Manifest
```json
GET /updates/manifest?device_id=dev-1234&core=0.3.0&model=1.2.0&...
→ 200
{
  "code": 0,
  "manifest": {
    "units": {
      "core":  { "version": "0.3.1", "sha256": "…", "url": "/updates/core/download?ver=0.3.1", "compat": {…} },
      "model": { "version": "1.3.0", "sha256": "…", "url": "/updates/model/download?ver=1.3.0", "compat": {…} }
    }
  }
}
```

### 3.4 Telemetry（可选，节流 ≤1/min）
```json
POST /telemetry
{ "device_id": "dev-1234",
  "core_version": "0.3.0", "model_version": "1.2.0",
  "metrics": { "input_fps": 59.6, "inference_fps": 14.8, "e2e_p50_ms": 67.8,
               "cpu_pct": 15.2, "npu_pct": 40.0, "temp_c": 57.3 },
  "errors": { "count": 0 } }
```

## 4. 离线行为

- Agent 拉取 Manifest/授权失败（网络断开）→ 使用**本地缓存**：上次成功的 Manifest + 授权缓存
- 本地授权缓存含 `expires_at` + `grace_period_s`：到期后进入宽限期；宽限期结束且仍离线 → 停止需要授权的功能（AI Core 由设备策略决定，默认停止新推理但保持进程健康，见 device-validation.md）
- 离线期间手动更新：导入本地 `.ttbox-update` 包（不经过 Backend）

## 5. 安全

- HTTPS + TLS 1.2+；token 存于 `/opt/ttbox2/data/credentials.json`（0600）
- 所有下载校验 SHA256（+ 阶段 C 增加签名验证）
- Telemetry 不包含任何画面数据；只含聚合指标

## 6. 演进

- 阶段 C 实现 Agent 与 Backend 通信骨架；`backend-api.md` 是接口契约
- 本地开发可用 mock backend（脚本），便于离线联调
