# 设备验证（Device Validation / License）

> 目标：**设备验证与更新分离**；后台异常不能导致已授权 AI Core 停止运行。
> 设计本地授权缓存、过期时间、宽限期。AI Core 运行不依赖网络。

## 1. 核心原则

1. **AI Core 自治**：Core 只读本地授权缓存，不直接访问 Backend；Backend 仅影响"新功能的授权判定"和"更新"，不影响已运行帧路径
2. **验证与更新分离**：License 判定 ≠ 更新下载；更新失败/后台宕机不影响已授权功能
3. **宽限期兜底**：授权过期/后台不可达时进入宽限期，不突然停机

## 2. 授权状态机

```
              ┌────────────┐
  注册成功 ──▶│  Active    │──过期时间到──▶┌──────────┐
              └────────────┘               │ Grace(宽限)│
                  ▲                        └────┬─────┘
              ┌───────────┐  宽限期结束          │
              │  Revoked  │◀────离线+超期        ▼
              └───────────┘               ┌──────────┐
                                          │  Expired │
                                          └──────────┘
```

| 状态 | 行为 |
|---|---|
| Active | 全功能运行 |
| Grace | 后台不可达/授权将过期 → 仍运行，警告日志 + Web 提示；宽限期内尝试重新验证 |
| Expired | 停止授权功能（AI 推理停止；Web/Agent/系统管理保留） |
| Revoked | 后台明确吊销 → 按策略停止授权功能 |

## 3. 本地授权缓存（`/opt/ttbox2/data/license.json`）

```json
{
  "device_id": "dev-1234",
  "token": "tok-xxxx",
  "status": "active",
  "issued_at": 1720000000,
  "expires_at": 1751536000,
  "grace_period_s": 604800,
  "features": ["aim", "recognition"],
  "last_verified_at": 1720000000,
  "last_backend_reachable": 1720000000,
  "signature": "…"          // 本地校验签名（阶段 C）
}
```

判定逻辑（Agent 每次启动 + 周期刷新）：
```
now = 当前时间
if now < expires_at:  Active
elif now < expires_at + grace_period_s:
    if 后台可达且验证通过: 续期 → Active
    else: Grace
else:
    if 后台可达且验证通过: 续期 → Active
    else: Expired（停止授权功能）
```

## 4. 后台异常隔离

- Agent 崩溃 / 网络断开 / Backend 宕机 → **只影响授权刷新与更新**，不影响已授权 Core 帧路径
- 运行中的 Core 不感知 Backend 状态；由 Agent 在授权过期时才通知 Runtime 停止推理
- 宽限期设计保证：短暂断网（< 宽限期）无感；长时间断网有明确降级，而非瞬时停机

## 5. 设备注册流程

```
首次上电/恢复出厂
  Agent → POST /devices/register（SN + 硬件指纹）
  ← device_id + device_token（持久化 0600）
  Agent → POST /devices/auth（周期刷新）
  Agent → GET /devices/{id}/license（刷新授权缓存）
```

- 硬件指纹：SoC 序列号 / MAC / DTB 特征组合（阶段 C 定）
- 恢复出厂（Factory Reset）→ 重新注册（阶段 D）

## 6. 离线激活

- 无网络环境：管理员通过 Web/离线包导入 `license.lic`（签名文件），Agent 校验后写入缓存
- 离线授权有效期由包内 `expires_at` 决定

## 7. 与 Update 的关系

- 更新安装**不要求**授权有效（允许"续费后恢复"场景），但下载更新包时校验设备已注册（身份，非 License）
- 授权过期不影响更新到新版本；恢复授权后立即可用
