# 出货硬约束清单（"动哪条会炸什么"护栏）

> **归属**：代码梳理批次 0（2026-09-17）
> **依据**：`docs/handover/2026-09-17/代码梳理方案-2026-09-17.md` §6
> **用途**：**任何"梳理/重构/清理"动作前必读**。下表每一条都是**已验证的机械事实**，
> 不是风格建议。**违反任一条 = 可能拒绝出货 / 断部署链路 / 板端服务反复重启。**
> 相关：构建目录规约 [`build-dirs.md`](build-dirs.md)、库作用域裁决 [`../../lib/README.md`](../../lib/README.md)。

---

## 硬约束总表

| # | 约束 | 动它的后果 | 梳理动作必须遵守 |
|---|---|---|---|
| **C1** | `TTBOX_PROJECT_ROOT` 是**编译期常量**（`-DTTBOX_PROJECT_ROOT=/opt/ttbox`），非运行期 `getenv` | 改成运行期解析 / 移动 `config/` ⇒ A4 门禁 `strings` 断言 `${PROJ_ROOT}/config/default.json` 失败 ⇒ **拒绝出货** | `config/` **位置不动**；"源码里的 `config/`" ≠ "运行期 `/opt/ttbox/config/`" 是**两件事**，勿混 |
| **C2** | `ttbox_fhs_init.sh:24` 用 `dirname $BASH_SOURCE/..` 反推 `REPO_ROOT` | **移动 `scripts/`** ⇒ `deploy/config/`、`scripts/ttbox_release_install.sh`、`scripts/ttbox_ensure_services.sh` 全部解析失败 ⇒ **部署链路断** | `scripts/` **绝不外移**；若 fhs 引用的脚本要改名，**fhs_init 必须同步改** |
| **C3** | release 树 + `current` 软链（`/opt/ttbox/releases/<ver>`） | 打乱布局 / 不走 install ⇒ 回滚（切软链）失效 | 梳理**不触碰** `releases/` 结构 |
| **C4** | `detect_build_dir()` 判据 = **实测 RUNPATH 恰为 `$ORIGIN/../lib`（形 A）**，非目录名先到先得；候选顺序**硬编码** `build-aarch64-t114 build-aarch64 build` | 留陈旧同名近似目录 ⇒ 误选非形 A 产物 ⇒ **静默绕过 T1.16**（形 C 去共享目录找 `.so`） | 保留 `build-aarch64-t114`；**清掉陈旧近似目录**（降低误选风险）。详见 [`build-dirs.md`](build-dirs.md) |
| **C5** | `.gitignore` 大量**精心白名单**（last-match-wins；父目录被排除时无法再包含其内文件） | 随手改 `.gitignore` ⇒ 静默失效（如 `!lib/librknnrt.so`、`!usbproxy/Makefile`、`!docs/build/`、`_*` 须**根锚定**） | 梳理**不改 `.gitignore` 规则语义**；若必须改，逐条跑 `git check-ignore -v` 自检 |
| **C6** | `RELEASE_BUILD.md` 的 `commit` 字段语义 = **被编译的源码 commit** | 先构建后提交 / 手改留档 ⇒ 破坏 (源码, 向量) 二元组 ⇒ 复现锚失效 | **禁改**两份 `RELEASE_BUILD.md`（`build-aarch64-t114/`、`build-aarch64-deploy/`）；批次 2 **零 `core/src` 改动** ⇒ 该字段语义不受影响 |
| **C7** | `plugins/` 必须**整包拷贝**（web 需 `import plugins.system_host`） | 只拷子目录 ⇒ release 树 `plugins` 包残缺 ⇒ **web 启动 `ModuleNotFoundError`** | 若归档 `static/legacy/`，**保留 `plugins/__init__.py` 与 `system_host.py`/`system_common.py`** |
| **C8** | 预编译 ELF（无 shebang）须**显式赋可执行位**（MSYS/Git-Bash 判其非可执行）；`usbproxy/usb-proxy` 是入库 ELF（134K） | 当垃圾清 / 改其 mode ⇒ 板端 `run-ttbox-usb-proxy.sh` 预检 `exit 1` ⇒ 服务反复重启 | **勿清 `usbproxy/usb-proxy`**；勿改入库文件 mode（`ttbox_git_mode_check.sh` 有断言） |

---

## "动了收益低但风险高" → 一律**不建议**

| 对象 | 建议 | 理由 |
|---|---|---|
| `scripts/` 的任何移动/改名 | ❌ **强烈不建议** | C2：一刀断部署链路，收益（"更好看"）为 0 |
| `plugins/web/bin/ttbox-web.py`（5012 行）拆分 | ❌ **不建议（本阶段）** | 拆分=功能重构，非"梳理"；它是**出货稳定面**，改动风险远高于收益 |
| `framework/` / `ttbox_motion/` 物理移动 | ❌ **不建议** | 二者是 web 的**运行期根级包**（`sys.path.append(repo_root)` + FHS 落 `/opt/ttbox/<pkg>/`）；移动需同步改 web + fhs_init |
| `config/` 与 `deploy/config/` 合并 | ❌ **不建议** | 作用域不同（开发模板 vs 出厂基线），且 C1 的编译期断言依赖 `config/` |
| 改 `.gitignore` 现有规则 | ❌ **不建议** | C5：白名单是 last-match-wins 精巧设计，随手改会静默失效 |
| `core/src/` 内部分层目录改名 | ❌ **不建议** | 是 CMake 源列表 + 大量 `#include` 路径依赖；改名=大范围编译改动，零收益 |
| `lib/README.md` 删除 | ❌ **不建议** | 它是库作用域**不变量锚**（README 即裁决），删了会重现"误拷 1.5.2"风险 |
| `docs/handover/**` 改写 | ❌ **不建议** | 历史交接记录，只能追加/归档 |
| `platform/` 立即删除 | ⚠️ **暂缓** | 虽"结构不可达"，但**先实测**（板端 `import platform` 指向 stdlib 且 `/opt/ttbox` 无 `import platform` 命中）再删；且删=改 payload，须随发版 |
| `build-aarch64-t114/` 目录整体清理 | ❌ **不建议** | 内含入库的 `RELEASE_BUILD.md`（锚的一半）；**只清目录内其他产物** |
| `usbproxy/usb-proxy`（预编译 ELF） | ❌ **不建议** | C8：入库预编译 ELF 是既定设计；误清会导致板端服务挂 |
| `core/src/bench/` 立即外移 `core/tools/` | ⚠️ **暂缓** | 收益低（1 文件）而需动 CMake；列批次 4 可选，不强制 |

---

## 每批验收（通用判据）

| 检查 | 命令 | 期望 |
|---|---|---|
| 工作区干净 | `git status --short` | 只有本批预期改动 |
| 入库文件数不变 | `git ls-files \| wc -l` | **仍 = 704**（纯归档/移动批） |
| 两份留档仍在库 | `git ls-files 'build-aarch64*'` | 恰好 2 条 `RELEASE_BUILD.md` |
| CTest 注册数不变 | `cd core && ctest -N` | 计数不变 |
| 发布体检 | `bash scripts/ttbox_release_verify.sh` | PASS |
| 板端验收 | `python3 scripts/ttbox_m207_accept.py` | PASS（失败 → `--rollback` 切软链） |

> 删除/归档任意路径前的**唯一硬判据**：`git ls-files <path>` **必须为空**；非空 ⇒ **禁删**。
