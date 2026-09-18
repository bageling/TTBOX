# 文档与代码分类约定（CONVENTIONS）

> **归属**：代码梳理批次 0（2026-09-17）
> **依据**：`docs/handover/2026-09-17/代码梳理方案-2026-09-17.md` §2 / §3 / §6
> **用途**：**新增文件时"放哪"的判定表** + **命名约定**。命中即停，不必纠结。
> 出货硬约束见 [`ops/release-constraints.md`](ops/release-constraints.md)；文档总入口见 [`README.md`](README.md)。

---

## 一、核心维度：按「消费方 + 生命周期」而非「运行时分层」

本仓库真正的"架构"不是代码分层，而是**出货物边界**（payload 白名单、FHS release 树、编译期 `TTBOX_PROJECT_ROOT`、`scripts/` 路径锚）。
因此**顶层按「谁消费、何时消费、是否进 payload」划分**；运行时分层维度**下沉到 `core/src/` 内部**使用。

---

## 二、新增文件"放哪"判定表（★ 按顺序问，命中即停）

| 序 | 自问 | 命中 → 落点 |
|---|---|---|
| 1 | 它是 **C++ 源码** 吗？ | `core/src/<层>/`（跨模块接口进 `core/include/ttbox/core/`） |
| 2 | 它是 **C++ 的测试/工具** 吗？ | 单测 `core/tests/`；板端/仿真工具 `core/tools/` |
| 3 | 它是 **随包运行的 Python** 吗（被 web/core/fhs 运行期加载）？ | 插件 `plugins/<name>/`；框架 `framework/`；运动 `ttbox_motion/` |
| 4 | 它是 **构建/发布/运维脚本**，且被 FHS 或发布门禁引用？ | `scripts/`（★**不得**外移；被 fhs 引用还要同步改 `ttbox_fhs_init.sh`） |
| 5 | 它是 **离线/开发期工具**（不在板端跑）？ | `tools/`（模型转换/签发类） |
| 6 | 它是 **部署描述**（unit/toolchain/出厂配置）？ | `deploy/{systemd,cmake,config}/` |
| 7 | 它是 **配置模板** 吗？ | 开发模板 `config/`；出厂基线 `deploy/config/`；**运行期真值不在仓库** |
| 8 | 它是 **测试** 吗？ | C++→`core/tests/`；pytest→**就近包内 `tests/`**；跨模块/板端→`tests/` |
| 9 | 它是 **文档** 吗？ | `docs/<类别>/`（类别见 §四） |
| 10 | 以上都不是（一次性探针/抓取物/构建产物） | **不进库**：构建产物→build 目录（`.gitignore` 已覆盖）；探针→**不要落在仓库根**，用临时目录 |

### 决策树（Mermaid）

```mermaid
flowchart TD
  A[新文件] --> B{C++ 源码/测试?}
  B -- 源码 --> B1[core/src/层/]
  B -- 测试 --> B2[core/tests/]
  B -- 工具 --> B3[core/tools/]
  B -- 否 --> C{随包运行?}
  C -- 插件 --> C1[plugins/name/]
  C -- 框架/领域包 --> C2[framework/ 或 ttbox_motion/]
  C -- 否 --> D{被 FHS/发布门禁引用?}
  D -- 构建/发布/运维脚本 --> D1[scripts/  不可外移]
  D -- 部署描述 --> D2[deploy/  systemd|cmake|config]
  D -- 否 --> E{离线工具?}
  E -- 是 --> E1[tools/]
  E -- 否 --> F{测试?}
  F -- pytest --> F1[就近包内 tests/]
  F -- 跨模块/板端 --> F2[tests/]
  F -- 否 --> G{文档?}
  G -- 是 --> G1[docs/类别/]
  G -- 否 --> H[不入库: 构建产物/pycache/探针/凭据]
```

---

## 三、命名约定（**强制**）

| 对象 | 规则 | 现状示例 |
|---|---|---|
| 目录名（代码包） | 小写、`snake_case`、单数；包必有 `__init__.py` | ✅ `framework/`、`ttbox_motion/` |
| 目录名（文档） | 小写英文 `kebab-case` 主题名（`architecture/`、`ops/`、`protocols/`、`performance/`…） | ✅ 本轮已建；旧 `架构/` 已迁移 |
| C++ 文件 | 类名同 `PascalCase.hpp/.cpp`；实现类与接口分离（`Xxx.hpp` / `Xxx_stub.cpp`） | ✅ `CoreInterface.hpp`、`PreviewModule_stub.cpp` |
| 插件 | `plugins/<name>/{bin/{ttbox-<name>, ttbox-<name>.py},config/,…}`；`bin/ttbox-<name>` 为 **bash launcher** | ✅ `plugins/web/bin/ttbox-web` |
| pytest 文件 | `test_*.py`，就近包内 `tests/` | ✅ `framework/tests/` |
| C++ 测试 | `test_*.cpp`（CTest 注册名 = 去掉 `test_`） | ✅ `core/tests/test_pipeline.cpp` |
| shell 测试 | `test_*.sh`；集成脚本按域分目录 `tests/<域>/` | ✅ `tests/api/`、`tests/monitor/` |
| 文档命名 | **活真源**：`主题.md`；**一次性报告**：`主题-YYYY-MM-DD.md`；**版本化**：`主题-vX.Y.Z.md` | ✅ `M2.07.1-交付总结-2026-09-17.md` |
| 日期格式 | `YYYY-MM-DD`（ISO，本地日） | ✅ |
| 私有/临时 | 一律不入库（`.testkeys/`、`_*`、`*.local.json`、`*.log`） | ✅ `.gitignore` 已覆盖 |

---

## 四、`docs/` 分类（目标树）

```text
docs/
├── README.md            # 文档总入口 + 唯一真源表
├── CONVENTIONS.md       # 本文件
├── architecture/        # 活·架构真源（系统总览/完整链路/核心模块/数据流/插件系统/目录结构）
├── protocols/           # 活·协议与规格（ipc-protocol、image-spec）
├── ops/                 # 活·运维（问题排查/RK3588开发流程/测试说明/release-constraints/build-dirs）
├── guide/               # 活·面向使用者（小白教程/、小白使用说明）
├── web/                 # 分域（architecture/ e2e/ model/ preview/ verification/）
├── product/             # 产品/蓝图/特性/路线（含 项目路线图.md）
├── research/            # 研究
├── verification/        # 验证证据（真实HDMI闭环验证 —— 性能基线数据出处）
├── build/               # 构建可复现（build-reproducibility.md）
├── AI/                  # 模型系统说明
├── handover/YYYY-MM-DD/ # ★历史交接（只追加/归档，禁改写）
└── archive/             # ★只读归档
    ├── pre-refactor/    #   整理前/瘦身/重构报告/旧代码地图
    ├── performance/     #   旧性能报告
    └── stages/          #   第N阶段/a9/a10/nightly/delivery
```

**文档放哪的判定**：消费方是"架构读者/协议实现者/运维/使用者/产品" → 对应 `architecture|protocols|ops|guide|product/`；
一次性过程报告 → 直接进 `archive/<类>/`；历史交接 → `handover/`（**禁改写，只追加**）。

### 旧路径指针规则

若某文档被**在库源码注释/历史交接记录**按**旧路径**引用而**无法同步修改引用方**（如 `core/**` 不许改），
则在**旧路径留一个指针文件**（内容=一句"已迁移至新路径"+映射表），保证历史引用仍可解析。示例：`docs/架构/README.md`、`docs/ipc-protocol.md`。

### 链接自检（改名/移动文档后必跑）

```bash
python docs/check_links.py
# 递归覆盖 docs/** 全层（含 archive/）+ modules/** + 根 README.md；
# 输出 scanned_files / links_checked（仅仓库相对链接）/ absolute_file_uris（绝对 file:// URI，不计断链）/ broken_count
# 期望：broken_count=0
```

> `absolute_file_uris` 是历史快照里指向**旧工作区**（`G:\工作区\…`）的绝对 URI，属档案内容，不修不改写。

---

## 五、五档处置语义

| 档位 | 含义 |
|---|---|
| **保留** | 位置与职责都不动 |
| **移动** | 换位置但保留内容（`git mv`，保历史） |
| **合并** | 内容并入他处 |
| **归档** | 移入只读归档区（`docs/archive/` 或 `.archive-2026-09-17/`），不再维护 |
| **删除** | 直接移除（本轮梳理**一律不删**，改归档） |

> 本轮纪律：**判「删除」的一律改为「归档」**，供业主复核后再决定。本机产物归档区 = 仓库根 `.archive-2026-09-17/`（已在 `.gitignore`）。
