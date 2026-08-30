# BUG_LIST.md — 当前代码 BUG / 死配置清单（2026-08-30 扫描）

## 已发现未修复

| # | 级别 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| B1 | 中 | RuntimeProfile/MouseTypes + AimThread | `y_axis_fire_hotkey` / `y_axis_fire_release_delay_sec` 字段存在、可配置、UI 曾暴露，但 AimThread 从不消费 → 死配置 | P1：AimThread 增加开火判定（buttons & y_axis_fire_hotkey 持续计时→锁 Y 轴输出） |
| B2 | 中 | RuntimeProfile/MouseTypes + 输出链 | `output_deadzone` 无消费 → 死配置 | P2：AimThread send 前死区过滤 |
| B3 | 低 | RuntimeProfile/MouseTypes + TargetSelector | `selector_search_radius` 无消费（TargetSelector 用自适应 0.06×框宽） | P2：TargetSelector 支持配置化半径 |
| B4 | 中 | Application::status_provider | GET_STATUS `runtime_running` 字段曾缺失（G1 已修复输出） | ✅ 已修 |
| B5 | 高 | Application::run | CoreRuntime 启动失败时整个进程退出（IPC 一起死，Web 无法显示状态） | ✅ 已修：改为 WARN + 进程存活 + RUNTIME_CONTROL 可重试 |
| B6 | 中 | Application::config_provider | GET_CONFIG 返回启动时旧值，SET_CONFIG 热更新后回读不一致 | ✅ 已修：优先从 RuntimeConfig 内存快照取 |
| B7 | 环境 | RK3588 CMA | 板端长时间运行后 CMA 高阶连续页碎片化，1440p 11MB/帧 分配失败（v4l2-ctl 独立复现）；重启恢复 | 记录：板子重启后复验 FPS |

## 死配置处理记录（本扫描）

`y_axis_fire_hotkey`、`y_axis_fire_release_delay_sec`、`block_physical_x/y`、`output_deadzone`、`selector_search_radius` 共 6 个字段已从 Web paramSchema **下架**（不允许假功能），待 Core 实现消费后再上架。关联组 GROUP_META 已同步移除。

## 已修复（历史）

- F1 validate 无 isfinite 防线（inf 进 PID）— 已加总闸+回归
- F2 AimThread 余数 int16 截断无 clamp — 已修
- F3 GET_STATUS 无 runtime_running 输出 — 已修（B4）
- F4 CoreRuntime 启动失败连带 IPC 死亡 — 已修（B5）
- F5 GET_CONFIG 回读旧值 — 已修（B6）


## 2026-08-30 换皮版新增

| B8 | 高 | ttbox_gateway /api/state | 合成的 `state.license` 缺 `valid` 字段 → yu 前端 `licenseValid=false` → `redirectToActivationPage()` 无限刷新循环（页面蹦迪） | ✅ 已修：license 加 `valid:true/status:'valid'` |
| B9 | 中 | yu app.js | `window.location.reload()` 5 处无节流，任何循环条件都会变成刷新风暴 | ✅ 已修：bridge 加 10s 节流保险丝 |


## 2026-08-30 二阶段（真实后端对接）新增

| B10 | 高 | ttbox_gateway.py | `import base64` 丢失 → /api/preview.jpg 500（NameError: base64） | ✅ 已修 |
| B11 | 高 | 换皮桥接层 | yu UI 保存参数时 `_yu_body_to_profile` 只翻译 yu 字段、丢失 `preview`/`geometry_filter` 段 → Core validate 拒绝（"preview 尺寸/ROI 必须为正"）→ 前端显示"同步失败" | ✅ 已修：PUT 前先拉 canonical profile 做 merge（网关+bridge 双侧） |
