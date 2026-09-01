# TTBOX Phase 8.2 代码血缘审查报告

> 审查性质：只读考古 / Git 历史追踪 / 静态分析
> 审查时间：2026-09-01
> 审查范围：`45ead2f` + `3934002` 两个 Phase 8.2 提交，对照 TTBOX 1.0 基线（`6c27dbb`，2026-08-24）与本仓库全部历史
> 审查方式：`git log -S` / `git show` / `git blame` / 当前代码逐层追踪
> 禁止事项遵守情况：未修改产品代码、未触碰真机、未重启服务、未改 Git 历史

---

## 1. 一句话结论

**Phase 8.2 主要是"修复已有能力的接线 + 校验"：Core / Domain Model 零改动，全部 17 处代码改动集中在 Gateway 翻译层（`scripts/ttbox_web.py`）与前端状态文案（`app.js` / `index.html`）；没有为了适配面板新增任何 Core 字段、Domain 字段或算法逻辑。**

具体拆开说：

- 主/副热键、any/all、X 偏移、sensitivity、class_offsets、class_filter：**全部是 TTBOX 1.0（2026-08-24 快照）就有的原生 Core 能力**，Phase 8.2 只修 Gateway 漏传/错传，并做真机浏览器闭环验证。
- 热键 FOV 缩放、备用 X/Y、偏移切换、全局禁用：**Core 从来没有这些能力**，Phase 8.2 正确地把它们标为 PLANNED 并禁用假控件，而不是为面板新增 Core 字段。

---

## 2. 功能血缘表

| 功能 | 1.0 存在 | Phase 8.2 修改 | 新增能力 | Core 消费 | 最终分类 |
|------|---------|--------------|---------|-----------|---------|
| 主热键 | ✅ Core 原生（MouseTypes.hpp `aim_hotkey`，RuntimeProfile 序列化 6c27dbb） | 无（Gateway 映射 9f30e5f 已存在） | 否 | ✅ AimThread.cpp:84 热键门控 | ORIGINAL |
| 副热键 | ✅ Core 原生（`aim_hotkey2`） | 无 | 否 | ✅ AimThread.cpp:85 | ORIGINAL |
| any/all | ✅ Core 原生（`aim_hotkey_mode` + 枚举） | 无 | 否 | ✅ AimThread.cpp:90 `a&&b / a||b` | ORIGINAL |
| 目标类别 class_filter_mask | ✅ Core 原生（`inference.class_filter`，含序列化/反序列化） | Gateway：mask ↔ class_filter 双向映射恢复（3934002） | 否 | ✅ decode/Worker 消费 inference.class_filter | FIXED（9f30e5f 时映射被删，本次恢复） |
| sensitivity（移动倍率） | ✅ Core 原生（MouseTypes.hpp:134；9f30e5f 已接入输出链） | Gateway：恢复 `p0.sensitivity`（3934002 撤掉 9f30e5f 的删除） | 否 | ✅ AimThread.cpp 输出链 out_sensitivity | FIXED/EXTENDED（profile 级倍率回补） |
| X 偏移 | ✅ Core 原生（AimPointProfile.offset_x；b6d0caa 起 AimThread 消费） | Gateway 无改动（9f30e5f 已映射） | 否 | ✅ AimThread.cpp:63 `aim_ratio_x` | ORIGINAL（本次仅浏览器验证） |
| Y 偏移 | ✅ Core 原生（AimPointProfile.offset_y） | Gateway：修复 `body.pos` 覆盖 `p0.offset_y` 的优先级（3934002） | 否 | ✅ AimThread.cpp:64 `aim_ratio_y` | FIXED |
| class_offsets | ✅ Core 原生（ClassOffset 结构 + 序列化 + `aim_point_at()` 消费） | Gateway：补前端 class_offsets → Core（3934002）+ 回读（3934002） | 否 | ✅ AimPointProfile.hpp `aim_point_at()` 按 class_id 覆盖 | FIXED（原 Gateway 从未传递，本次接通） |
| 热键 FOV 缩放 | ❌ Core 无任何字段（`fov_scale` 只存在于前端 app.js） | 无（页面控件保留；矩阵标 PLANNED） | 否 | ❌ 无 | PLANNED |
| 备用 X/Y 偏移 | ❌ Core 无 `alternate_offset_*` 字段 | Gateway 仅回读兜底改为 offset 值（3934002），无 Core 落点 | 否 | ❌ 无 | PLANNED |
| 偏移切换键 | ❌ Core 无 `offset_switch_*` 字段（只有语义不同的 `switch_delay_ms` 类别偏移延迟） | 无 | 否 | ❌ 无 | PLANNED |
| 全局热键禁用 | ❌ Core 无 `hotkey_guard` 字段 | UI 禁用 + 标 PLANNED（45ead2f）；`/api/state` 曾固定返回 false | 否 | ❌ 无 | PLANNED |

**血缘判断依据（可复现命令）**：

```bash
# Core 字段最早出现
git log --all -S aim_hotkey -- core/src          # 2c7f8ce / 6c27dbb（远早于 Phase 8）
git log --all -S class_offsets -- core/src       # 6c27dbb
git log --all -S hotkey_guard -- core/src        # 无输出 → Core 从未有过
git log --all -S fov_scale -- core/src           # 无输出 → Core 从未有过
git log --all -S alternate_offset -- core/src    # 无输出 → Core 从未有过
```

---

## 3. Phase 8.2 改动精解（Gateway 为主）

### 3.1 `45ead2f`（Phase 8.2: 热键页面真实接入与远程浏览器闭环）— 17+/11-

| 文件 | 改动 | 性质 |
|------|------|------|
| `web/templates/index.html`（6+/6-） | 全局热键禁用/切换键控件加 `disabled` + `data-feature-status="planned"` + 文案 | 前端状态标识，非能力 |
| `web/static/app.js`（7+/1-） | `hotkeyGuardRuntimeStatus` 按 `data-feature-status` 显示"计划中" | 前端状态文案 |
| `docs/product/TTBOX_WEB_FEATURE_MATRIX.md`（4+/4-） | 全局禁用行 REAL→PLANNED | 文档 |

### 3.2 `3934002`（Phase 8.2: 热键页目标类别/倍率/X-Y偏移接通Core并完成浏览器闭环）— 17+/7-

`scripts/ttbox_web.py`（11+/6-，Gateway 翻译层）：

| 位置 | 原来 | 现在 | 性质 |
|------|------|------|------|
| `yu_body_to_profile` 4) | `class_filter_mask` 被忽略 | mask → `inference.class_filter` 位展开 | 恢复被 9f30e5f 删除的映射（FIXED） |
| `yu_body_to_profile` 5) | 只有 `body.sens`（总览全局）写 sensitivity | 增加 `p0.sensitivity`（热键 Profile 级）优先 | 恢复 profile 级倍率（EXTENDED） |
| `yu_body_to_profile` 5) | `body.pos` 无条件覆盖 `p0.offset_y` | 仅当 `p0.offset_y is None` 时才用 pos | 修复优先级覆盖（FIXED） |
| `yu_body_to_profile` 5) | class_offsets 从未传给 Core | `p0.class_offsets → mouse.class_offsets` | 补接线（FIXED） |
| `profile_to_yu` | `class_filter_mask` 固定 0 | 从 `inference.class_filter` 反算真实 mask | 读路径补真实值（FIXED） |
| `profile_to_yu` | `alternate_offset_x/y` 固定 0.5 | 兜底为 profile 的 offset | 读路径兜底（体验修正） |
| `profile_to_yu` | `class_offsets` 固定 [] | 从 `mouse.class_offsets` 回读 | 读路径补真实值（FIXED） |

## 4. Core 零改动确认

```bash
git show --stat 45ead2f   # 只有 docs + app.js + index.html
git show --stat 3934002   # 只有 docs + scripts/ttbox_web.py
git log -S fov_scale -- core/src   # 空
git log -S hotkey_guard -- core/src # 空
```

- 新增文件：`docs/product/FEATURES/hotkeys.md`（工作树未跟踪，本轮一并提交为文档；非产品代码）
- 修改文件：`scripts/ttbox_web.py`（Gateway 翻译层）、`web/static/app.js`、`web/templates/index.html`、`docs/product/TTBOX_WEB_FEATURE_MATRIX.md`
- Core / RuntimeConfig / Domain Model（`core/src/**`）：**0 文件 0 行**（`git show --stat` 均无 core/ 路径）
- 真机：仅部署了 `ttbox_web.py`（Gateway 进程，`systemctl restart ttbox-web`），未替换 Core 二进制，未改配置、网络、SSH

## 5. TTBOX Core 原生能力真实现状（对照审查重点字段）

| 字段 | Core 定义 | Core 读取 | Core 消费 | 状态 |
|------|-----------|-----------|-----------|------|
| `mouse.aim_hotkey/aim_hotkey2/aim_hotkey_mode` | MouseTypes.hpp:113-115 | RuntimeProfile.cpp:362-364 | AimThread.cpp:84-90 热键门控 → injection_allowed | ✅ 原生 |
| `mouse.sensitivity` | MouseTypes.hpp:134 | RuntimeProfile.cpp:382 | AimThread.cpp 输出链 out_gain | ✅ 原生 |
| `mouse.aim_point.offset_x/y` | AimPointProfile（MouseTypes.hpp:74-80） | RuntimeProfile.cpp:420-421 | AimThread.cpp:63-64 aim_ratio + CoordinateTransform reference_point | ✅ 原生 |
| `mouse.aim_point.class_offsets` | ClassOffset + vector | RuntimeProfile.cpp:429-437 | AimPointProfile.hpp `aim_point_at()` 按 class_id+priority 覆盖瞄准点 | ✅ 原生 |
| `inference.class_filter` | RuntimeProfile inference 结构 | RuntimeProfile.cpp 序列化 | decode/Worker 消费 | ✅ 原生 |

**不存在于 Core 的字段**（只有前端或 Gateway 字符串，Core 零引用）：`fov_scale`（热键 FOV）、`alternate_offset_*`、`offset_switch_*`、`hotkey_guard` / `global_hotkey_disable`。

## 6. 产品真相

### Q1：Core 到底有多少是自己的原生能力？

热键体系（主/副/模式）、移动倍率（sensitivity）、瞄准点 X/Y 偏移、类别级偏移（class_offsets）、类别过滤（class_filter）、FOV 距离、PID/Smith 控制链、输出链（scale/deadzone）等均为 **TTBOX 自研 Core 原生能力**（1.0 快照 6c27dbb 2026-08-24 即存在，部分更早在 2c7f8ce）。Web 前端（`app.js` 约 1.2 万行、`index.html`、`ttbox_web.py` YU 兼容层）是 8e8f8c7（2026-08-31）接入的 YU 风格控制台，属于"面板接入 Core"，不是 Core 能力来源。

### Q2：Phase 8.2 有没有为适配面板偷偷扩展 Core？

**没有。** 证据链：`git show --stat` 两个提交均无 `core/src` 路径改动；`git log -S` 证明 `fov_scale`/`hotkey_guard`/`alternate_offset` 在 Core 从未出现。Phase 8.2 只做了三件事：Gateway 翻译层补/修映射（3 处 FIXED + 1 处 EXTENDED + 回读补全）、前端计划区禁用与文案、矩阵文档。未新增任何 Core 字段、Domain 字段、API 或算法。

### Q3：03～10 页面应"对接已有能力"还是"新增产品能力"？

**双轨，且顺序明确：**

1. **优先对接已有能力**：Core 已具备热键、PID、输出链、类别过滤、类级偏移、预览链路等，多数只是 Gateway 映射缺失或前端未接，应沿用 Phase 8.2 模式（Core 零改动 + Gateway 翻译层接线 + 浏览器闭环验证）。
2. **出现"前端有控件、Core 无字段"时**：一律标 PLANNED 并禁用控件（Phase 8.2 已示范），禁止为面板临时加 Core 假字段。
3. **真正的新产品能力**（全局热键禁用、偏移切换、热键 FOV 缩放等）应作为正式产品需求立项，先写 Feature Spec 再动 Core，不进 Phase 8.x 的"接线"路径。

---

## 7. 审查附加发现（不修改，仅记录）

1. `docs/product/FEATURES/hotkeys.md` 为工作树未跟踪文件（上一轮产物），本轮随报告一并提交，供后续接手人参考。
2. 测试发布私钥曾入 Git 历史（`keys/release-private.pem` 删除记录 + `release/manager/test-fixtures/release-private.pem` A/D 记录），当前工作树已被 `.gitignore` 的 `**/*private*.pem` 忽略；历史 blob 仍存在于对象库，正式发布机器应轮换密钥。（本轮未做历史重写，如需处理单独立项。）