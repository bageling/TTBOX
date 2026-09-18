# 构建目录命名与选用规约（build-dirs）

> **归属**：代码梳理批次 4（2026-09-17）
> **依据**：`docs/handover/2026-09-17/代码梳理方案-2026-09-17.md` §4.1 / §6-C4
> **硬约束**：本文档**不授权**任何人改名 `build-aarch64-t114/` —— 它被
> `scripts/ttbox_fhs_init.sh` 的 `detect_build_dir()` **硬编码在候选顺序**里（见下），
> 改名 = 断发布链路。本文只讲**规约**，不改任何脚本、不改任何目录名。

---

## 一、唯一权威判据：**实测 RUNPATH 恰为「形 A」**，不是目录名

发布时的交叉编译产物目录由 `scripts/ttbox_fhs_init.sh` 的 `detect_build_dir()` 决定。
它**不看目录名先后**就拍板，而是逐个候选**实读二进制的 RUNPATH**，只认「形 A」：

| 形态 | RUNPATH 值 | 判定 |
|---|---|---|
| **形 A（唯一合格）** | 恰为 `$ORIGIN/../lib`（**一段**、自包含） | ✅ 采用 |
| 形 B（假回滚） | `$ORIGIN/../lib:/opt/ttbox/lib` | ❌ 不采纳（尾段跨版本共享目录） |
| 形 C（静默绕过） | `/opt/ttbox/lib` 等绝对/共享路径 | ❌ 不采纳（去共享目录找 `.so`，**静默绕过 T1.16**） |

> 判定在 `ttbox_fhs_init.sh:193`：`if [ "$actual" = '$ORIGIN/../lib' ]`。
> B/C 两类都会让门禁照样全绿（因为都可能链到同一个 2.3.2），**但只有形 A 的产物能就近加载 payload 内的 `librknnrt.so`** —— 这正是「不能只看目录名/不能只看门禁绿」的原因。

## 二、候选顺序（**硬编码**，勿凭直觉改）

`detect_build_dir()` 的循环（`ttbox_fhs_init.sh:172`）：

```sh
for d in "${TTBOX_BUILD_DIR:-}" build-aarch64-t114 build-aarch64 build; do
```

| 优先级 | 候选 | 说明 |
|---|---|---|
| 0 | `$TTBOX_BUILD_DIR`（环境变量显式指定） | **显式指定必须通过形 A 验证**，验证不过 ⇒ 直接失败（拒绝静默回退） |
| 1 | `build-aarch64-t114` | **规范目录**（形 A 构建）；★ **不可改名** |
| 2 | `build-aarch64` | 历史遗留名 |
| 3 | `build` | 兜底 |

> `scripts/ttbox_build_release.sh:74` 的构建默认值也是 `build-aarch64-t114`
> （`BUILD_DIR="${TTBOX_BUILD_DIR:-build-aarch64-t114}"`），与 `detect_build_dir` 口径一致。

## 三、陈旧近似目录为什么危险（**真实踩坑，各一次**）

`build-aarch64` 与 `build-aarch64-t114` **近似同名**，而 `detect_build_dir` 在候选 2 上也会
「实测 RUNPATH」——若某个陈旧目录**恰好残留**一个形态合格（甚至形 A）的旧产物，
就会**先被选中**，导致发布用的是**错误产物**：

- **M2.07**：误选过一次非预期产物。
- **M2.07.1**：又误选过一次。

⇒ 结论：**同名近似目录 = 误选风险源**。清理它们（批次 1）不是"好看"，是**降低误选概率**。
本轮已把 `build-aarch64` / `build-aarch64-authon` / `build-aarch64-t118` 移入
`.archive-2026-09-17/build-dirs/`（见该目录 `README.md`）。

## 四、新增构建目录该怎么命名

1. **优先复用规范目录** `build-aarch64-t114`（用 `TTBOX_BUILD_DIR` 指定产出位置时同理）。
2. 若确需并行/试验构建，命名必须**可区分且带语义前缀**，格式建议：
   `build-<arch>-<用途/变体>[-YYYYMMDD]`，例如：
   - `build-aarch64-exp-int8`（试验 INT8 零拷贝）
   - `build-host-yu`（host 单测对标，**已在用，保留**）
   - `build-aarch64-deploy`（部署留档构建，**含入库 `RELEASE_BUILD.md`，保留**）
3. **禁止**再新建与规范目录仅差「一个短后缀数字」的目录（如 `build-aarch64-t115`）——那正是 M2.07/M2.07.1 踩坑的形态。
4. **试验目录用完即归档**：不给它们长期驻留仓库根的机会。
5. 任何新目录都必须先过「形 A」判据：
   ```bash
   readelf -d build-aarch64-XXX/ttbox_core_main | grep -E '\((RUNPATH)\)'
   # 期望：RUNPATH 恰为 [$ORIGIN/../lib]（一段、自包含）
   ```

## 五、保留清单（⛔ 勿动）

| 目录/文件 | 角色 | 为何不可动 |
|---|---|---|
| `build-aarch64-t114/` | 规范交叉构建目录 | 持有 1.4.3 门禁产物（core sha256 `3f760bc1…`）与**入库**的 `RELEASE_BUILD.md`（可复现锚的一半）；名字被 `detect_build_dir` 硬编码 |
| `build-aarch64-deploy/` | 部署留档构建 | **含已入库**的 `RELEASE_BUILD.md`（脚本自动生成，禁改） |
| `build-host-yu/` | host 单测构建 | **仍在用** |
| `build-aarch64-t114/RELEASE_BUILD.md` | 出货留档 | `commit` 字段 = **被编译的源码 commit**；手改 / 先构建后提交都会破坏复现锚（§6-C6） |

## 六、自检命令

```bash
# ① 两份入库留档未被忽略（均应无输出）
git check-ignore -v build-aarch64-t114/RELEASE_BUILD.md
git check-ignore -v build-aarch64-deploy/RELEASE_BUILD.md
# ② 交叉构建目录的 RUNPATH 实测为形 A
readelf -d build-aarch64-t114/ttbox_core_main | grep RUNPATH
# ③ 发布体检（真 release 树权威）
bash scripts/ttbox_release_verify.sh
```

> 相关真源：`docs/build/build-reproducibility.md`（构建可复现）、
> `docs/ops/release-constraints.md`（硬约束护栏）、`lib/README.md`（库作用域裁决）。
