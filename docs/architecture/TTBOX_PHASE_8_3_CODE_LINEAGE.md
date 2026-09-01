# TTBOX Phase 8.3 — 03「移动控制」代码血缘审查

> 日期：2026-09-01
> 范围：Web 控制台 03 移动控制页（control-page）每一个控件的完整代码血缘
> 方法：代码追踪 + Git 历史（`git log -S`）确认 Core 能力是否早已存在，禁止凭文件名猜测
> 结论先行：**本页 13 项功能中，8 项是 Core 原生能力（部分链路被误伤），1 项是纯 Gateway 接线问题，1 项能力完整但未接线，3 项是真正缺失的产品能力（PLANNED）**

---

## 1. 血缘主链路（页面 → Core 消费点）

```
Web 控件 (web/templates/index.html + web/static/app.js)
  ↓ window.ttbox.api（apiClient.js 唯一请求入口，request(method,path,body,opts)）
  ↓ PUT/GET /api/config（或 /api/control/calibration*）
scripts/ttbox_web.py（Flask Gateway，板端 8081）
  ↓ yu_body_to_profile / profile_to_yu（YU 格式 ↔ RuntimeProfile 翻译）
  ↓ IPC SET_CONFIG / GET_CONFIG（Unix socket /tmp/ttbox_core.sock）
core/src/ipc/IpcServer.cpp（SET_CONFIG 原子更新 → 校验 → 热更新 → 落盘）
  ↓ RuntimeConfig 快照
core/src/aim/AimThread.cpp（每帧读快照 → TargetSelector → Pid1 → 输出链）
  ↓ OutputAction
core/src/output/*（AiboxHidOutput/LocalHidBackend/OutputBackend，写 /dev/hidg0）
```

板端部署确认（2026-09-01 实测）：

- 运行中：`ttbox-core.service`（C++，`/opt/ttbox/core/build/ttbox_core_main`，8-31 21:01 编译）+ `ttbox-web.service`（Flask，`/opt/ttbox/web/ttbox_web.py`，13:51 启动）
- 板端 web 目录就是 `scripts/ttbox_web.py` + `web/` 前端产物（md5 与本地仓库一致）
- 旧版简易控制台 `core/tools/web/ttbox_web.py` 仍存在但未运行（8081 由 Flask 占用）

---

## 2. 逐控件血缘与判定

### 2.1 PID 控制器组（controller_kp_x/y、ki_x/y、kd_x/y、predict_x/y、rate_x/y、output_deadzone）— 判定：FIXED

| 环节 | 现状 |
|------|------|
| Web 控件 | `#controller_kp_x` 等，`collectConfig()` 收集进 `ai.controller`，PUT /api/config |
| Gateway | `CONTROLLER_NUMS` 直通映射 → `mouse.kp_x` 等（scripts/ttbox_web.py:296-308） |
| Gateway 回读 | `profile_to_yu` 全字段回填（:475-506） |
| Core 消费 | **AimThread.cpp:65-71**：`pid_x_.configure(kp_x, kd_x, predict_x, rate_x, smooth_x)`；`ki_x/ki_y` 存入 RuntimeProfile 但 **Pid1 控制器不消费**（ki 是 pid1 内部自适应项，无独立 ki 输入；MouseTypes.hpp 注释"预留 V1 纯 P 不使用"） |
| Git 历史 | `9f30e5f`（8-31）端到端贯通并板端 40/40 PASS；**`aecddfb`（9-1 凌晨）误删输出链（见下）** |

**问题（BROKEN 点）**：`9f30e5f` 在 AimThread 加的 sens×output_scale×output_deadzone 输出链和 `scfg.confidence` 真实读取，在 `aecddfb` 被整体回退（`git show aecddfb -- core/src/aim/AimThread.cpp` 删除 `out_sensitivity/out_deadzone/scaled_x` 及 confidence 读取）。本地主线当前 AimThread 恢复为写死 `scfg.confidence=0.0f`、`remainder += aibox_x`（无 sens/deadzone）。**板端源码+二进制仍是 9f30e5f 版本（有输出链）**，本地主线反而落后。

**处理**：将 9f30e5f 输出链回归到本地主线 AimThread（sens 全局缩放、output_deadzone 门控、confidence 真实读取）。这是把"本地主线 = 板端真机"拉齐，不是新增能力。

### 2.2 移动倍率（sens）— 判定：ORIGINAL（Core 早已存在）

- Web：`#sens` → `body.sens` → Gateway `mouse.sensitivity`（scripts/ttbox_web.py:385）
- Core：`MouseProfile.sensitivity`（默认 1.0），输出链 `out_gain = sensitivity × output_scale` 消费（同 2.1 的 FIXED 修复点）
- 结论：Core 原生能力，只需修好 2.1 的输出链回归即可真实生效

### 2.3 目标锁定（controller_selector_lost_grace_ms）— 判定：ORIGINAL

- Web → Gateway `mouse.lost_grace_ms`（CONTROLLER_NUMS）
- Core：`AimThread.cpp:62` `scfg.lost_grace_ms = profile->mouse.lost_grace_ms` → `AimStateMachine::update(event, lost_grace_ms)` 真实消费
- 链路完整，无需修改

### 2.4 开火延迟释放Y轴（aim_fire_lock_y / y_axis_fire_hotkey / y_axis_fire_release_delay_sec）— 判定：ORIGINAL（配置链路完整，消费在 C 桥）

- Web：checkbox + select + number → `ai.controller.*`
- Gateway：`CONTROLLER_BOOLS`（aim_fire_lock_y）→ `mouse.aim_fire_lock_y`；热键字符串 → 位掩码 `y_axis_fire_hotkey`；delay 直通
- Core 消费：`MouseTypes.hpp:157-159` 字段齐全；**C 桥 `ttbox-hid-bridge.c:340-343` 是唯一消费点**（`g_feat.fire_lock_y && (buttons & fire_hotkey_bit)` → 超时清 Y），但 C 桥配置键是 `ai_controller.aim_fire_lock_y`（扁平 features 命名），而 `core/tools/web/ttbox_web.py` 的 `features_to_conf` 只写 `mouse.*` 扁平键且它**已不在运行链路上**；主 Gateway `scripts/ttbox_web.py` 不写 features.conf → **C 桥拿不到配置**。
- 结论：Core（C 桥）能力存在，但当前运行链路（Flask Gateway）未接通 C 桥配置。属接线问题，标 FIXED 范围；C 桥是否在板端运行需真机确认（`ttbox-hid-bridge.c` 未在板端发现进程，疑为 legacy）。

### 2.5 拉枪曲线（pull_curve_*）— 判定：BROKEN（配置完整、实现完整、消费点被断）

- Web：checkbox + 3 数字 → `ai.controller.pull_curve_*`
- Gateway：`yu_body_to_profile` 组 `mouse.pull_curve` 子对象（scripts/ttbox_web.py:338-348），回读 `profile_to_yu` 补齐
- Core 配置：`MouseTypes.hpp:83-89 PullCurveConfig` + `RuntimeProfile.cpp:263-267/398-403` 序列化完整
- **Core 消费：`PullCurve.hpp` 实现类完整存在（弧线+抖动），但 29d3622 删除旧 MouseScheduler 后，注释声称"由 AimThread 调用"，实际 AimThread 完全没有调用**（`grep -rn PullCurve core/src --include="*.cpp"` 零引用）。C 桥的拉枪实现也被 29d3622 注释禁用（"已迁移到 C++ 端"→ 实际 C++ 端没人接）。
- 结论：能力实现完整，消费接线在重构中被丢。**需在 AimThread 输出链接入 PullCurve::apply**（EXTENDED：把现有类接回控制链，非新增算法）。

### 2.6 持续提前量（continuous_lead_*）— 判定：ORIGINAL（C 桥实现活着）

- Web → Gateway `mouse.continuous_lead` 子对象 → RuntimeProfile 完整
- 消费：**C 桥 `ttbox-hid-bridge.c:276-290 continuous_lead_output()` 完整实现**（同向累计→X 偏置→渐入渐出），配置键 `ai_controller.continuous_lead_*`；C++ `ContinuousLead.hpp` 实现类存在但同 2.5 未接入 AimThread（C 桥与 C++ 双实现，运行中的是 C 桥那个）。
- 结论：与 2.4 相同，C 桥在运行链路上的配置注入问题。核心算法 ORIGINAL。

### 2.7 屏蔽物理移动（block_physical_mouse_x/y_while_aiming）— 判定：ORIGINAL（C 桥实现）+ 接线 FIXED

- Web：2 个 checkbox → `ai.controller.block_physical_mouse_*`
- Gateway：`CONTROLLER_BOOLS` → `mouse.block_physical_x/y`（scripts/ttbox_web.py:312-313）
- Core 消费：`MouseTypes.hpp:170-171` 字段；**C 桥 `ttbox-hid-bridge.c:144-145` + inject_ai 内 `block_x/block_y` 是唯一实现**（瞄准时物理轴清零）。配置键同样是 `ai_controller.block_physical_*` 扁平命名 → 运行链路不通（同 2.4）。
- 前端 `renderPhysicalMotionBlockStatus` 读 `state.mouse_output.physical_motion_block_support/mask/error`，**Gateway `mouse_output` 只回 `{'mode':'passthrough'}`，无这些字段** → 徽标永远"等待连接"。
- 结论：能力 ORIGINAL（C 桥），当前 Gateway 缺字段回填 + C 桥配置注入 = FIXED 范围。**真机核验项**：C 桥是否部署运行（/run/ttbox-features.conf 不存在、无 bridge 进程 → 疑未启用）。

### 2.8 自动标定（autoCalibration*）— 判定：FIXED（Gateway 假桩 → 接真实状态机）

- Web：`pollAutoCalibration` GET /api/control/calibration；start/cancel/clear/PUT 手动保存（app.js:12005-12096）。前端渲染字段齐全：`runtime.{running,phase,progress,ready,reason,candidate_count,stable_ms}` + `calibration.{valid,gain_x_px_per_count,response_delay_ms,confidence,model_id,calibrated_at}`（app.js:11904-12003）
- **Gateway 现状（BROKEN）**：`scripts/ttbox_web.py:1245-1267` 四个路由全是假桩——GET 永远返回 `{'valid': False}`、PUT/start/cancel 永远返回固定成功。**与真实 Core 能力完全脱节**。
- 真实能力在哪：`core/tools/web/ttbox_web.py:1454-1891` 有**完整真实标定状态机**（`_cal` 状态机：stabilize→moving→measuring→done；10 轮往返注入；从 C++ 高频目标文件 `/run/ttbox-target.json` 读真实目标；写 `/opt/ttbox/config/calibration.json`；计算 gain/confidence/delay 并换算 kp 写入 profile；手动保存同样换算）。创建于 b6d0caa（8-29，早于主 Gateway 8e8f8c7），是旧简易控制台的后端。
- 注意：前端字段名（gain_x_px_per_count/response_delay_ms）与真状态机写出的字段（mouse_gain_x_px_per_count/mouse_response_delay_ms）有差异，需要适配层映射。
- 结论：**Core 能力存在（8-29 就有）**，主 Gateway 从 8-31 创建起就是假桩。处理 = 把真实标定模块引入主 Gateway（迁移复用，非新写），彻底替换假桩。**这是本页最重要的 FIXED**。

### 2.9 记录 10 秒移动日志（recordAimTraceButton）— 判定：ORIGINAL（能力真实，按钮被模板隐藏）

- Gateway：`/api/diagnostics/aim-trace` POST 真实存在（scripts/ttbox_web.py:1286-1323，后台线程 50Hz 采样 Core metrics → 写 /opt/ttbox/run/aim_trace.json）
- Core 数据源：`CoreRuntime::collect_metrics` 输出 `aim_error_x/y、mouse_dx/dy、aim_active`（本地缺失 error_x/y 字段，板端 9f30e5f 版本有：`out->aim_error_x = ast.error_x`）→ 即本地 AimThread 无 error_x 暴露的回归点之一
- 前端：`recordAimTraceButton` 绑定完整（app.js:10704-10731），但**模板 `{% if show_aim_trace_button %}` 且 Gateway 三处路由都传 `show_aim_trace_button=False`** → 按钮永远不显示。
- 结论：能力真实（ORIGINAL），纯接线问题：把 `show_aim_trace_button=True` 传出去即可。属 FIXED 范围。

### 2.10 恢复本页默认值（resetControllerDefaultsButton）— 判定：ORIGINAL（链路完整）

- 按钮 `hidden` 属性随分区切换（自动标定区隐藏），点击 → `resetCurrentMovementSectionDefaults` → 表单回填默认值 → PUT /api/config。链路完整。

### 2.11 个性曲线训练（motion training）— 判定：PLANNED

- Gateway 三处路由 `motion_training_available=False` + `motion_training_collection_available=False` → 模板不渲染 tab 和面板；`web/static/motion_training.js` 与 `motion_training_mobile.js` 存在但无入口。
- Core 无任何训练能力（C++ 全仓无训练代码）。**诚实地 PLANNED，不造假**。

### 2.12 自动标定子项：X/Y 轴响应、鼠标响应延迟手动输入（autoCalibrationGainX/Y/Delay）— 判定：随 2.8

- 前端字段 `gain_x_px_per_count` / `gain_y_px_per_count` / `response_delay_ms`（app.js:11824-11828）
- 真状态机写 `mouse_gain_x_px_per_count` 等 → 适配层需映射（见 2.8）

### 2.13 X轴/Y轴 预判（predict_x/y）、X/Y 跟随（rate_x/y）、基础死区（output_deadzone）— 判定：随 2.1（ORIGINAL/FIXED）

---

## 3. 汇总表

| # | 功能 | 判定 | 消费点 | 问题 | 处理 |
|---|------|------|--------|------|------|
| 1 | PID kp/ki/kd/predict/rate | ORIGINAL | AimThread Pid1 configure | ki 无独立消费（pid1 内部项） | 无需改 |
| 2 | sens 移动倍率 | ORIGINAL | 输出链 out_gain | 本地输出链被 aecddfb 误删 | 回归 9f30e5f 输出链 |
| 3 | output_deadzone 基础死区 | ORIGINAL | 输出链门控 | 同上 | 回归 |
| 4 | selector_lost_grace_ms | ORIGINAL | AimStateMachine | 无 | 无需改 |
| 5 | aim_fire_lock_y 开火锁Y | ORIGINAL | C 桥 fire_lock_y | 主 Gateway 不写 features.conf，C 桥配置断 | 接线 FIXED（待真机核验 C 桥） |
| 6 | pull_curve 拉枪曲线 | BROKEN | PullCurve.hpp 类完整，AimThread 未接入 | 29d3622 重构断线 | AimThread 接入（EXTENDED） |
| 7 | continuous_lead 持续提前量 | ORIGINAL | C 桥 continuous_lead_output | 同 5 配置断 | 接线 FIXED |
| 8 | block_physical 屏蔽物理移动 | ORIGINAL | C 桥 inject_ai block_x/y | 同 5 + Gateway 缺 mouse_output 字段回填 | 接线 FIXED |
| 9 | 自动标定 | FIXED | core/tools/web/ttbox_web.py 真状态机 | **主 Gateway 四路由全是假桩** | 迁移真状态机到主 Gateway |
| 10 | 手动标定参数保存 | FIXED | 同上 | 假桩 + 字段名差异 | 随 9 |
| 11 | 记录移动日志 | ORIGINAL | Gateway aim-trace 真实采样 | 模板 show_aim_trace_button=False | 传 True（FIXED） |
| 12 | 恢复本页默认值 | ORIGINAL | 完整 | 无 | 无需改 |
| 13 | 个性曲线训练 | PLANNED | 无 | Core 无能力 | 保留 UI 标记 PLANNED |

---

## 4. 必须修的真实问题（根因，不造假）

1. **AimThread 输出链回归**（本地主线落后板端）：sens × output_scale、output_deadzone 门控、scfg.confidence 真实读取、error_x/error_y 遥测——全部按 9f30e5f 恢复，让本地主线与板端真机（9f30e5f 二进制）一致。
2. **主 Gateway 标定四路由接真实状态机**：从 core/tools/web/ttbox_web.py 迁移（复用代码，不做 bridge/补丁），字段名映射到前端契约。
3. **记录移动日志按钮露出**：show_aim_trace_button=True。
4. **pull_curve 接入 AimThread 输出链**（能力复用现有类）。

## 5. 不修/不造假（诚实标注）

- 个性曲线训练：PLANNED，无 Core 能力，不写假 API。
- C 桥配置注入（fire_lock/block_physical/continuous_lead）：C 桥在板端未运行（无进程、无 features.conf）时，这些功能标 VERIFY（能力存在、运行链未启用），不伪造。
- ki_x/ki_y：pid1 控制器内部自适应，无独立 ki 输入，前端显示但标注"内部项"。
- 动目标验证（拉枪/提前量/屏蔽/锁Y）：需要真实游戏画面+目标，属硬件闭环验证项，本 Phase 不做假验证。

## 6. 附：板端/本地差异记录

| 文件 | 本地 HEAD | 板端 | 说明 |
|------|-----------|------|------|
| core/src/aim/AimThread.cpp | aecddfb（无输出链） | 9f30e5f（有输出链） | **本地落后板端**，需回归 |
| scripts/ttbox_web.py | 185314b6... | 185314b6... | 一致 |
| web/static/app.js | 89e19163... | 89e19163... | 一致 |
| web/templates/index.html | a52d535b... | a52d535b... | 一致 |
| core/src/runtime/CoreRuntime.cpp | aecddfb（无 error_x/y） | aecddfb^（有） | 随 AimThread 回归 |
| core/src/ipc/IpcServer.cpp | aecddfb（metrics 缺 aim_error_x 等） | aecddfb^（有） | 随 AimThread 回归 |

（md5 实测 2026-09-01 15:2x）
