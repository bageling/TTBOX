# TTBOX 文档中心

这里是 TTBOX 模块版的全部中文说明。**文档分类规则与命名约定见 [`CONVENTIONS.md`](CONVENTIONS.md)。**

```text
本文 → 小白教程(guide/) → 架构(architecture/) → 完整链路 → 测试 → 问题排查
```

> **2026-09-17 代码梳理批次 0**：`docs/` 已按「消费方 + 生命周期」重新分类（架构真源 / 协议 / 运维 / 教程 / 分域 / 归档），
> 并对重叠的「盘点/瘦身/架构/代码地图」类报告做了**唯一真源归并**：重复的历史快照移入只读归档 [`archive/`](archive/)，
> 见下方「五、历史归档」。旧目录 `docs/架构/` 已改名 `docs/architecture/`（旧的 `docs/架构/` 留了指针文件）。

---

## 一、唯一真源表（★ 只信这一张表）

> 口径：**同一主题只允许一个"活真源"**；其余同主题文档一律为历史归档（只读）。若两张表冲突，**以本表为准**。

| 主题 | 唯一真源 | 说明 |
|---|---|---|
| 项目总入口 | [`../README.md`](../README.md) | 目录结构、链路、构建、部署 |
| 文档入口 / 分类规则 | 本文件 + [`CONVENTIONS.md`](CONVENTIONS.md) | 放哪判定树 + 命名约定 |
| 系统架构 | [`architecture/系统总览.md`](architecture/系统总览.md) | 分层全景 |
| 完整链路 | [`architecture/完整链路.md`](architecture/完整链路.md) | 画面→鼠标全链路 |
| 核心模块 | [`architecture/核心模块.md`](architecture/核心模块.md) | 每模块详解 |
| 数据流 | [`architecture/数据流.md`](architecture/数据流.md) | 每环节数据形态 |
| 插件系统 | [`architecture/插件系统.md`](architecture/插件系统.md) | 插件机制 |
| 目录结构 | [`architecture/目录结构.md`](architecture/目录结构.md) | 顶层树 + 是否进 payload |
| IPC 协议 | [`protocols/ipc-protocol.md`](protocols/ipc-protocol.md) | 网页↔核心通信（真值以源码为准） |
| 母版规格 | [`protocols/image-spec.md`](protocols/image-spec.md) | Ubuntu RK3588 母版规格 |
| **口径登记表** | [`protocols/config-path-env-registry.md`](protocols/config-path-env-registry.md) | 配置·常量·路径唯一真源：RUNTIME env allowlist + 跨语言同值常量（门禁 `scripts/ttbox_conventions_gate.sh` 断言） |
| RK3588 开发流程（强制） | [`ops/RK3588开发流程.md`](ops/RK3588开发流程.md) | 本机→交叉→上板强制流程 |
| 测试说明 | [`ops/测试说明.md`](ops/测试说明.md) | 怎么编译、怎么测试 |
| 修改指南 | [`ops/修改指南.md`](ops/修改指南.md) | 改代码前必读（"我要改 X 看哪里"） |
| 问题排查 | [`ops/问题排查.md`](ops/问题排查.md) | 常见问题与排查 |
| 出货硬约束护栏 | [`ops/release-constraints.md`](ops/release-constraints.md) | 「动哪条会炸什么」 |
| 构建目录规约 | [`ops/build-dirs.md`](ops/build-dirs.md) | detect_build_dir 判据与命名 |
| 构建可复现 | [`build/build-reproducibility.md`](build/build-reproducibility.md) | 可复现锚 |
| 真实 HDMI 闭环验证 | [`verification/真实HDMI闭环验证.md`](verification/真实HDMI闭环验证.md) | 性能基线数据出处（141FPS 等） |
| 死代码唯一判据 | [`废弃代码清单.md`](废弃代码清单.md) | ★ 唯一"死代码"判据表（吸收 stage1 清单结论） |
| 板端依赖 | [`../deploy/DEPENDENCIES.md`](../deploy/DEPENDENCIES.md) | 板端依赖清单 |
| 库作用域裁决 | [`../lib/README.md`](../lib/README.md) | 库作用域不变量锚 |
| AI 交接 | [`AI_HANDOFF.md`](AI_HANDOFF.md) | 接手阅读顺序 |
| 用户教程 | [`guide/小白教程/`](guide/小白教程/) + [`guide/小白使用说明.md`](guide/小白使用说明.md) | 面向使用者，活文档 |
| Web / 产品 / 研究 | [`web/`](web/)、[`product/`](product/)、[`research/`](research/) | 分域文档 |
| 历史交接（只读） | [`handover/`](handover/) | ★ 只能追加/归档，禁改写 |

---

## 二、你应该从哪条路开始

| 你是谁 | 推荐路线 |
|---|---|
| 完全不懂代码 | [guide/小白教程/01-TTBOX是什么.md](guide/小白教程/01-TTBOX是什么.md) 开始，一路看到 10 |
| 要操作板子 | [guide/小白使用说明.md](guide/小白使用说明.md) + [ops/问题排查.md](ops/问题排查.md) |
| 要改 C++ 核心 | [architecture/核心模块.md](architecture/核心模块.md) + [ops/修改指南.md](ops/修改指南.md) |
| RK3588 开发流程（强制） | [ops/RK3588开发流程.md](ops/RK3588开发流程.md) |
| 要接网页/API | [web/architecture/INDEX.md](web/architecture/INDEX.md) + [protocols/ipc-protocol.md](protocols/ipc-protocol.md) |
| 要部署新板 | [../deploy/DEPENDENCIES.md](../deploy/DEPENDENCIES.md) + [../README.md](../README.md) |
| 要清理旧代码 | [废弃代码清单.md](废弃代码清单.md) |

## 三、小白路线（必须按顺序）

| 顺序 | 文档 | 讲什么 |
|---|---|---|
| 1 | [guide/小白教程/01-TTBOX是什么.md](guide/小白教程/01-TTBOX是什么.md) | TTBOX 是什么 |
| 2 | [guide/小白教程/02-TTBOX怎么工作.md](guide/小白教程/02-TTBOX怎么工作.md) | 整体怎么工作 |
| 3 | [guide/小白教程/03-电脑画面怎么进入盒子.md](guide/小白教程/03-电脑画面怎么进入盒子.md) | HDMI 画面 |
| 4 | [guide/小白教程/04-AI是怎么识别目标的.md](guide/小白教程/04-AI是怎么识别目标的.md) | AI 识别 |
| 5 | [guide/小白教程/05-模型是什么.md](guide/小白教程/05-模型是什么.md) | 模型概念 |
| 6 | [guide/小白教程/06-检测框是什么.md](guide/小白教程/06-检测框是什么.md) | 检测框 |
| 7 | [guide/小白教程/07-坐标是怎么计算的.md](guide/小白教程/07-坐标是怎么计算的.md) | 坐标计算 |
| 8 | [guide/小白教程/08-插件是什么.md](guide/小白教程/08-插件是什么.md) | 插件 |
| 9 | [guide/小白教程/09-如何添加模型.md](guide/小白教程/09-如何添加模型.md) | 加模型 |
| 10 | [guide/小白教程/10-如何排查问题.md](guide/小白教程/10-如何排查问题.md) | 排查问题 |

## 四、开发 / 运维 / 分域

### 开发路线

| 文档 | 讲什么 |
|---|---|
| [architecture/系统总览.md](architecture/系统总览.md) | 系统分层全景 |
| [architecture/完整链路.md](architecture/完整链路.md) | 从画面到鼠标的完整链路 |
| [architecture/核心模块.md](architecture/核心模块.md) | 每个核心模块详解 |
| [architecture/数据流.md](architecture/数据流.md) | 数据在每个环节长什么样 |
| [architecture/插件系统.md](architecture/插件系统.md) | 插件怎么工作 |
| [ops/修改指南.md](ops/修改指南.md) | 改代码前必读 |
| [ops/测试说明.md](ops/测试说明.md) | 怎么编译、怎么测试 |
| [ops/RK3588开发流程.md](ops/RK3588开发流程.md) | RK3588 强制开发流程 |
| [protocols/ipc-protocol.md](protocols/ipc-protocol.md) | 网页与核心的通信协议 |
| [../modules/README.md](../modules/README.md) | 模块化语义视图（旧层，待出清） |

### 运维路线

| 文档 | 讲什么 |
|---|---|
| [ops/问题排查.md](ops/问题排查.md) | 常见问题与排查步骤 |
| [ops/release-constraints.md](ops/release-constraints.md) | 出货硬约束护栏 |
| [ops/build-dirs.md](ops/build-dirs.md) | 构建目录命名规约 |
| [../deploy/DEPENDENCIES.md](../deploy/DEPENDENCIES.md) | 板端依赖清单 |
| [../deploy/systemd/](../deploy/systemd/) | systemd 服务文件 |

### 模型 / 性能 / 平台

- [AI/模型系统.md](AI/模型系统.md)、[AI/模型输入格式.md](AI/模型输入格式.md)、[web/model/INDEX.md](web/model/INDEX.md)
- [build/build-reproducibility.md](build/build-reproducibility.md)
- [web/e2e/INDEX.md](web/e2e/INDEX.md)、[verification/真实HDMI闭环验证.md](verification/真实HDMI闭环验证.md)

## 五、历史归档（只读，不再更新）

以下目录只保留**历史过程记录**，代码可能已重构，阅读时以当前 `core/src/` 为准：

- [`archive/pre-refactor/`](archive/pre-refactor/) —— 整理前快照 / 瘦身报告 / 重构报告 / 旧代码地图（11 份）
- [`archive/performance/`](archive/performance/) —— 历史性能报告（`performance-rk3588*.md`，10 份）
- [`archive/stages/`](archive/stages/) —— 阶段报告（`a9-*` / `a10/` / `nightly/` / `delivery/` / `第13~15阶段` / PID 分析 / INT8 切换）
- [`handover/`](handover/) —— ★ 历史交接记录（**只能追加/归档，禁改写**）

这些报告的**重叠结论已归并**到上表「唯一真源」；如发现冲突，以真源为准。

---

如果你只想看懂一件事，先读 [guide/小白教程/01-TTBOX是什么.md](guide/小白教程/01-TTBOX是什么.md)。
