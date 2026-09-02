# TTBOX Phase 8.4 — YU 参考实现映射

> 参考对象：真机 `192.168.0.53` 上的 YU 参考服务
> 采集时间：2026-09-01
> 参考服务：`aiassistance-web.service`，`/opt/aiassistance/web/app.py`，监听 `0.0.0.0:8080`
> TTBOX 服务：`ttbox-web.service`，监听 `0.0.0.0:8081`
> 原则：YU 只用于确认产品行为；TTBOX 不运行时依赖 YU，不接入 YU API、daemon、品牌或配置文件。

---

## 1. 参考实现边界

### 1.1 已确认的 YU 部署

| 项目 | 实际值 | 证据 |
|---|---|---|
| Web 根目录 | `/opt/aiassistance` | systemd `WorkingDirectory`、`run_web.sh` |
| Web 入口 | `/opt/aiassistance/web/app.py` | systemd `ExecStart=/opt/aiassistance/scripts/run_web.sh` |
| 模板目录 | `/opt/aiassistance/web/templates` | app.py Flask 初始化、目录扫描 |
| 静态目录 | `/opt/aiassistance/web/static` | app.py Flask 初始化、目录扫描 |
| 配置 | `/opt/aiassistance/config/config.json` | 真机文件读取 |
| 训练数据目录 | `/opt/aiassistance/config/motion-profiles` | app.py + 真机目录读取 |
| daemon socket | `/opt/aiassistance/run/daemon.sock` | `SOCKET_PATH`、daemon_call |
| Web 端口 | 8080 | systemd `AIASSISTANCE_PORT=8080`、实际监听 |
| Web 服务器 | Waitress | HTTP 响应 `Server: waitress` |
| daemon | `aiassistance-daemon.service` | systemd 实际 active |
| TTBOX 状态 | 8081/Core/Web active | 真机服务检查 |

YU 启动时没有停止、重启或修改 TTBOX。YU 服务在本阶段仅作为独立参考运行。

### 1.2 YU 真实运行结果

- `/api/state`：返回完整配置、运行状态、模型、预览、系统数据。
- `/api/motion-profiles`：返回真实 profile 状态；初始存在 `default` profile，样本为 0，个人模型不存在。
- `/api/control/calibration`：返回真实标定状态；当前无目标，状态 `idle/not_running`。
- `/api/models`：返回真实模型条目，包含 RKNN、输入尺寸、类别数、并发数、加密状态。
- `/api/themes`：返回本地默认主题；外部授权服务当前连接失败，状态带 `offline/sync_error`，不是无条件成功。
- `/api/network/wifi`：真实报告无无线网卡，不伪造可用 Wi-Fi。
- `/api/hailo/status`：真实报告 Hailo 设备不存在，`ready=false`。
- `/api/remote/models`：未配置远程电脑时真实返回错误，不返回空成功列表。
- `/api/events`：真实返回 SSE 状态事件；本阶段第一次错误来自错误地按 JSON 解析 SSE，不是 YU 接口返回 JSON。
- `/api/update/check`：真实尝试授权服务器，当前因连接失败返回错误；不标记为更新成功。

---

## 2. YU 页面与 TTBOX 产品映射总表

状态定义：

- **ORIGINAL**：TTBOX 已有自己的实现，行为目标一致。
- **FIXED**：TTBOX 有能力或页面位置，但链路曾断、返回假成功或状态错误；需要修根因。
- **MISSING**：YU 有真实产品能力，TTBOX 当前没有同等真实能力。
- **PLANNED**：确认有价值，但当前阶段暂不施工，已进入 TTBOX 规划。
- **NOT_NEEDED**：YU 能力依赖其他产品/硬件/商业系统，不进入 TTBOX 产品。
- **BROKEN**：TTBOX 现有入口明确返回假数据或无法工作，修复前保持此状态。

| YU 页面 | YU 产品能力 | YU 实现/接口 | TTBOX 对应 | 状态 | 判断 |
|---|---|---|---|---|---|
| 01 总览 | 状态、FPS、延迟、温度、内存、存储、预览、启停 | `/api/state`、`/api/system`、`/api/preview.mjpg`、`/api/control/start/stop` | 01 总览、`/api/state` | ORIGINAL | TTBOX 已有独立 Core/IPC 数据源，非复制 YU。 |
| 02 热键 | 主/副热键、any/all、类别、倍率、X/Y、类别偏移 | `/api/config` | 02 热键、`/api/config` | ORIGINAL | Phase 8.2 已浏览器闭环。 |
| 02 热键 | FOV 缩放、备用偏移切换、全局热键禁用 | `/api/config` 字段 | 02 热键 | PLANNED | 当前 TTBOX Core 没有对应真实消费点。 |
| 03 移动控制 | PID、预测、跟随、死区、丢失宽限 | `/api/config`、daemon `put_config` | 03 移动控制、Core RuntimeProfile | ORIGINAL | TTBOX 已有 Pid1/TargetSelector/AimStateMachine。 |
| 03 移动控制 | 自动/手动标定 | `/api/control/calibration*` → daemon | 03 移动控制 | FIXED | TTBOX 已替换假桩；自动完整闭环仍需真实目标。 |
| 03 移动控制 | 拉枪曲线 | `/api/config`、daemon/Core motion controller | 03 移动控制 | FIXED | TTBOX 配置已有，Core `PullCurve.hpp` 消费接线待修。 |
| 03 移动控制 | 持续提前量、物理移动屏蔽、开火锁Y | `/api/config`、daemon/C bridge | 03 移动控制 | FIXED | TTBOX 配置链已有，C bridge 运行效果待硬件验证。 |
| 03 移动控制 | 个人移动曲线训练 | `/api/motion-profiles*`、`/api/motion-training/sessions*` | 03 移动控制 | MISSING→本阶段施工 | YU 有真实样本/训练/模型/激活链；TTBOX 当前接口是假成功。 |
| 04 辅助 | 压枪 | `/api/config` → daemon/C bridge | 04 辅助 | ORIGINAL | TTBOX 已有真实配置链，效果需 HID 场景验收。 |
| 04 辅助 | 自动开火/连点/回甩 | `/api/config`、daemon/C bridge | 04 辅助 | ORIGINAL | TTBOX 已有独立实现和接口。 |
| 04 辅助 | 准星找色 | `/api/config`、daemon | 04 辅助 | ORIGINAL | TTBOX 有自己的图像/配置链。 |
| 04 辅助 | 风扇控制 | `/api/config`、PWM/温度 | 09 系统设置 | ORIGINAL | TTBOX 已有真实温度/PWM路径。 |
| 05 模型 | 本地模型上传、删除、选择、类别名、并发 | `/api/models*` | 05 模型库 | ORIGINAL | TTBOX ModelRegistry/IPC 已有。 |
| 05 模型 | 云加密模型登记 | `/api/models/cloud-encrypted` → YU daemon | 05 模型库 | PLANNED | 需要 TTBOX 自己的模型安全产品设计，不能复制 YU 密钥体系。 |
| 05 模型 | 远程电脑模型同步/远程导入 | `/api/remote/*` | 05 模型库 | PLANNED | 需先定义 TTBOX Remote Model Contract，不接 YU 远程协议。 |
| 06 显示/鼠标 | HDMI/EDID/显示状态 | `/api/hardware/display` | 06 显示与鼠标 | ORIGINAL | TTBOX 有真实 V4L2/sysfs/EDID 接口。 |
| 06 显示/鼠标 | HID 鼠标信息/模式/时序 | `/api/hardware/mouse*` | 06 显示与鼠标 | ORIGINAL | TTBOX 有自己的 HID 输出模型。 |
| 06 显示/鼠标 | 圆形输出测试、USB 诊断 | `/api/mouse-output/test-circle`、`/api/diagnostics/usb-proxy.zip` | 06 显示与鼠标 | ORIGINAL/FIXED | 接口已存在，需逐项真实硬件验证。 |
| 07 网络 | Wi-Fi/AP/Client/扫描/连接 | `/api/network/wifi*` | 07 网络配置 | ORIGINAL | TTBOX 使用真实 nmcli；无硬件时报告不可用。 |
| 07 网络 | 局域网屏蔽 | `/api/system/lan-blocklist*` | 07 网络配置 | FIXED | TTBOX 当前部分路由仍为占位，需单独产品化。 |
| 08 预设 | 保存、加载、删除、重命名、导入、导出 | `/api/presets*` | 08 预设参数 | FIXED | TTBOX 有基础实现；导入/导出/重命名需逐接口核验真实落盘。 |
| 09 系统 | 版本、设备、CPU/RAM/温度/存储、自启 | `/api/system*`、`/api/settings/auto-start` | 09 系统设置 | ORIGINAL/FIXED | 真实数据源已有，个别返回字段需统一。 |
| 10 更新 | OTA 检查、版本、状态、安装、取消、回滚、日志、USB | `/api/update/*` | 10 系统更新 | FIXED/PLANNED | TTBOX 有 release/update 组件；Web 入口与更新 IPC 仍需完整接通。 |
| 隐藏页 | Hailo | `/api/hailo/*` | 隐藏能力 | NOT_NEEDED | 真机 RK3588 无 Hailo 设备；TTBOX 主线是 RKNN。 |
| 隐藏页 | 主题商店/兑换/外部授权 | `/api/themes*` | 隐藏主题入口 | NOT_NEEDED/PLANNED | TTBOX 可保留本地主题，外部兑换体系不复制。 |
| 隐藏页 | 云端账号/授权/绑定 | `/api/license*`、外部服务 | TTBOX 授权 | NOT_NEEDED | TTBOX 重新定义自己的授权，不依赖 YU 服务。 |

---

## 3. 个人移动曲线训练：YU 行为与 TTBOX 设计

### 3.1 YU 实际行为

YU 的个人曲线训练不是页面假功能，证据包括：

- daemon socket：`/opt/aiassistance/run/daemon.sock`。
- 二进制字符串包含：`ai_core_motion_controller_create`、`ai_core_motion_controller_set_personal_model_json`、`ai_core_motion_controller_update_target_v2`、`ai_core_motion_controller_step_pending_move`。
- 独立数据目录：`config/motion-profiles/<profile_id>/profile.json`。
- 独立训练会话：开始 session → 获得 lease → 浏览器 Pointer Lock 采集 → 心跳 → 上传样本 → 结束 session。
- 样本协议：`aiassistance.motion-sample.v1`，包含 mode、completion、canvas、start、target、radius、browser 能力和带 dt/dx/dy 的 points。
- 训练目标：反应样本 72 条、连续切换样本 96 条，总计 168 条。
- 训练结果：模型质量、覆盖率、平均反应时间、路径效率；模型达到门槛后才能启用。
- 启用时带混合参数：curve、speed、reaction、max_reaction_delay_ms；停用个人模型后恢复默认曲线。
- 样本校验有硬限制：单样本最大 256KB、最多 2048 点、路径/时间间隔/坐标范围/目标完成状态均校验。

### 3.2 TTBOX 原生设计决定

TTBOX 采用自己的命名和架构：

- Domain：`MotionProfile`、`MotionTrainingSession`、`MotionSample`、`MotionModel`、`MotionMix`。
- 持久化：`config/motion-profiles/<id>/profile.json`，不读取 YU 目录。
- API：继续使用 TTBOX `/api/motion-profiles*`、`/api/motion-training/sessions*`，但响应由 TTBOX Gateway/Domain 产生，不调用 YU daemon。
- Core：在 `core/src/mouse/` 增加 TTBOX 自己的个人曲线模型加载、校验、混合和运行时消费；不引入 YU `libai_core.so`、`core_shim.so` 或 ABI。
- IPC：新增 TTBOX 自己的 `MOTION_PROFILE_*` / `MOTION_TRAINING_*` 命令，或由 Gateway 独立管理持久化后通过 `SET_CONFIG` 写入已定义的 RuntimeProfile 字段；最终选择以 Core 单一真源为准。
- 安全：只接受 `aiassistance.motion-sample.v1` 的行为语义，不保留该外部产品名称作为 TTBOX 公共 API/Domain 命名；TTBOX 公共协议命名为 `ttbox.motion-sample.v1`。

### 3.3 当前状态

- TTBOX 页面模板和 JS 已经有训练画布，但当前 Gateway 的 13 个训练路由是固定返回，属于 **BROKEN**。
- 本阶段先实现 TTBOX 自己的 profile/session/sample 持久化、严格校验、统计与模型生成/激活链。
- 真实鼠标 Pointer Lock 采集和 Core 输出消费完成后，才能标记 REAL；只有返回 JSON 不能标 REAL。

---

## 4. 03 移动控制重点复核

| 能力 | YU 参考事实 | TTBOX 当前事实 | Phase 8.4 处理 |
|---|---|---|---|
| 拉枪曲线 | 有默认曲线和个人模型替代关系 | `PullCurve.hpp` 存在，AimThread 未调用 | 修复 AimThread 消费点；个人模型另行接入 |
| 持续提前量 | 同向累计、渐入渐出、近距衰减参数 | C bridge 有实现；当前运行链未确认启用 | 保留 TTBOX 原生字段，真机验证 C bridge；不接 YU |
| 屏蔽物理移动 | 支持 X/Y 轴屏蔽并返回支持性/已应用状态 | TTBOX 配置链已有，状态回填已补 | 验证真实 HID/输入链；不复制 YU daemon |
| 自动标定 | 目标稳定→往返移动→响应测量→写 gain/kp | TTBOX 已有自己的状态机和 Core 目标反馈 | 修正边界、完成真实目标场景验证 |
| 个人曲线训练 | 168 样本、模型质量、启用混合 | TTBOX 当前是假接口 | 本阶段实现 TTBOX 原生能力 |

---

## 5. 全量 YU API → TTBOX 状态

### 总览 / 授权 / 主题 / 更新

| YU API | TTBOX API | 状态 |
|---|---|---|
| `GET /api/state` | `GET /api/state` | ORIGINAL |
| `GET /api/license`、`POST /api/license/activate` | 同路径 | FIXED/VERIFY，TTBOX 自己授权 |
| `GET /api/themes`、`PUT /api/themes/current` | 同路径 | PLANNED/NOT_NEEDED，保留本地主题边界 |
| `POST /api/update/check`、`POST /api/update/versions` | 同路径 | FIXED，需接 TTBOX Update Engine |
| `GET /api/update/status` | 同路径 | FIXED |
| `POST /api/update/install`、`POST /api/update/start` | TTBOX 更新 API | PLANNED，危险副作用单独验收 |
| `POST /api/update/cancel`、`POST /api/update/rollback`、`GET /api/update/log` | TTBOX 更新 API | PLANNED/FIXED |

### 运动训练

| YU API | TTBOX API | 当前状态 |
|---|---|---|
| `GET /api/motion-profiles` | 同路径 | BROKEN（当前固定空数组）→ MISSING |
| `POST /api/motion-profiles` | 同路径 | BROKEN（固定 default）→ MISSING |
| `PATCH /api/motion-profiles/<id>` | 同路径 | BROKEN → MISSING |
| `DELETE /api/motion-profiles/<id>` | 同路径 | BROKEN → MISSING |
| `GET /api/motion-profiles/<id>/export` | 同路径 | BROKEN → MISSING |
| `POST /api/motion-training/sessions` | 同路径 | BROKEN（固定 mock-session）→ MISSING |
| `PUT /api/motion-training/sessions/<id>/heartbeat` | 同路径 | BROKEN → MISSING |
| `POST /api/motion-training/sessions/<id>/samples` | 同路径 | BROKEN（固定成功）→ MISSING |
| `DELETE /api/motion-training/sessions/<id>` | 同路径 | BROKEN → MISSING |
| `POST /api/motion-profiles/<id>/train` | 同路径 | BROKEN → MISSING |
| `POST /api/motion-profiles/<id>/activate` | 同路径 | BROKEN → MISSING |
| `DELETE /api/motion-profiles/active` | 同路径 | BROKEN → MISSING |
| `DELETE /api/motion-profiles/<id>/samples` | 同路径 | BROKEN → MISSING |

### 模型 / 硬件 / 网络 / 预设

| YU API 组 | TTBOX 当前状态 |
|---|---|
| `/api/models*` | TTBOX 已有自己的 ModelRegistry/IPC；云加密和远程模型不直接复制，分别 PLANNED。 |
| `/api/hardware/display`、`/api/hardware/mouse*` | TTBOX 已有自己的硬件 API；按真实 sysfs/HID 数据验收。 |
| `/api/network/wifi*`、`/api/system/lan-blocklist*` | TTBOX 有入口；Wi-Fi/屏蔽能力按实际硬件和 OS 能力验收。 |
| `/api/presets*` | TTBOX 有基础链路；Phase 8.4 统一为完整快照、真实回读、导入导出校验。 |
| `/api/hailo/*` | NOT_NEEDED：TTBOX 目标硬件没有 Hailo。 |
| `/api/remote/*` | PLANNED：重新定义 TTBOX Remote Model Contract。 |

---

## 6. 本阶段施工决策

### 进入实现

1. TTBOX 原生个人移动曲线训练：Feature Spec → Domain → API → Gateway 持久化/校验 → Core 模型消费 → Web → 真机浏览器闭环。
2. 03 拉枪曲线：接回现有 `PullCurve.hpp`，不新增 YU 依赖。
3. 03 的配置和状态字段继续统一到 TTBOX `RuntimeProfile` 与 `/api/config`。

### 保留规划

- TTBOX 自己的云加密模型体系：需要独立密钥/授权/模型格式设计。
- TTBOX 自己的远程模型同步：需要独立协议、失败恢复、模型真源设计。
- TTBOX 主题系统：本地主题可做，外部商店不作为 TTBOX 依赖。
- 完整自动标定：需要真实 HDMI 游戏画面和目标，先不虚标。

### 明确不做

- 不启动或依赖 YU daemon。
- 不读取 YU `config.json`、`motion-profiles` 或 YU 运行时 socket。
- 不复制 YU `libai_core.so`、`core_shim.so`、`hook.so` 或其 ABI。
- 不把 YU 的 Hailo、外部授权、主题兑换、远程 Windows 服务作为 TTBOX 产品能力。
- 不为了让功能数量相同而实现没有 TTBOX 用户价值的页面。

---

## 7. 证据等级

- **已实测**：真机 systemd、端口、目录、HTTP 返回、配置/模型/profile 文件、YU daemon 字符串和服务状态。
- **已读代码**：YU `/opt/aiassistance/web/app.py`、模板、静态 JS、训练测试文件。
- **TTBOX 已验证**：Phase 8.3 浏览器闭环、Core 编译与单元测试、板端服务状态。
- **尚未验证**：YU 训练完整 Pointer Lock 采集 168 条样本；TTBOX 训练完整真实鼠标采集；03 拉枪/C bridge 的真实 HID 效果；更新安装/回滚的副作用流程。
