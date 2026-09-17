# lib/ — 随包库作用域说明

> 权威裁决：`lib-scope-ruling.md`（架构师）。本文件把 **作用域口径** 写死在入库点，防止再次误拷。
> 一句话：**「设备库」不是一类，是两类** —— 按 **由谁提供、谁定版本** 二分。

## 两类作用域

| 作用域 | 成员 | 谁提供 | 是否随 release 打包 | 板端解析位置 |
|---|---|---|---|---|
| **release 作用域** | `librknnrt.so` | **我方** | ✅ **必须**打进 `releases/<ver>/lib/` | `$REL/lib/`（走 RUNPATH `$ORIGIN/../lib`） |
| **基础镜像作用域** | `librga.so.2`、`libjpeg.so.8`、`libopencv_core.so.4.5d`、`libopencv_imgproc.so.4.5d`、`libc`/`libstdc++`/`libm`/`libgcc_s`/`ld-linux` | **板端 BSP / 基础镜像** | ❌ **不打包** | 系统绝对路径（`/lib/aarch64-linux-gnu/` 等） |

- **release 作用域**：`librknnrt.so` 随 **RKNN SDK** 分发，**不是** RK3588 Ubuntu BSP 的组成部分；且 NPU 运行时 ABI 与模型转换工具链（rknn-toolkit）**版本强绑定** ⇒ 必须「库与 `bin/` 同版本、同目录树」。
- **基础镜像作用域**：`librga.so.2` 与内核 `rga` 驱动（板厂 BSP 内核模块）**配套**，其 ABI 由**系统/内核**决定；`libjpeg`/`libopencv` 由 Ubuntu 基础镜像提供。把它们塞进 release ⇒ 一旦与镜像内核/系统版本错配，**反而制造故障** ⇒ **能带 ≠ 该带**。

## ✅ 仓库 `lib/librknnrt.so` 1.5.2 过期残留 —— **已删除**（丙案）

**本目录现仅含本文件（`lib/README.md`）**；仓库副本已于 `d0e04b9` 从版本控制移除。
`git ls-files 'lib/*.so*'` 必须为空 —— 该不变量是「单一真源」的机械校验点。

| 副本 | 状态 | 大小 | sha256 | 版本 | BuildID |
|---|---|---|---|---|---|
| **仓库（本目录）** | ❌ **已删除** | `5241144 B`（历史值） | `9f53d7b1941338242e227fb097496261049c3aa1d8f2f343b27e439c325f6990` | **1.5.2**（2023-08-23，过期残留） | `7795f7cdc39c0c9f016ac0e22093bc96efd632f6` |
| **链接期 sysroot（唯一出货源）** | ✅ 在库 | `7726232 B` | `d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8` | **2.3.2**（2025-04-09，= 模型 toolkit） | `4f5001b81d147d0db1f48e68fe87a6029caa2ccb` |

- **铁律**：payload 的 `librknnrt.so` 必须与 **链接期所用那份** **逐字节同源**（`sha256(payload/lib/librknnrt.so) == sha256(链接期 librknnrt)`），否则**不予出货**。
- **出货源解析**（`scripts/ttbox_fhs_init.sh` 的 `sync_tree`）：`TTBOX_RKNNRT_SO`（显式覆盖）> 构建目录 `CMakeCache.txt` 的 `RKNNRT_LIBRARY`（= 链接期真值）；并按 sha256 `d31fc19c…`（2.3.2）**且** 版本串含 `2.3.2` **硬门禁**。
- **门禁**：命中 `9f53d7b1…`（1.5.2）或 sha256 不符或版本串不含 `2.3.2` ⇒ **非零退出**，绝不静默装货。
- **删除理由（丙案）**：留一个 1.5.2 副本在版本控制里 = 留一条**可被误引用的错版本真源**。门禁虽能拦，但「库不存在」比「库存在却被拦」强 —— 前者不产生诱惑。故**删库不删文档**：作用域口径（本文件）保留，错版本实体移除。
- **甲案**（统一到仓库副本，改 `RKNNRT_LIBRARY` 优先指向本目录）**已驳回**：该副本是 **1.5.2 陈旧快照**，不是版本控制点而是过期残留；ABI/版本以**模型侧 2.3.2** 为准。

### 指纹提取口径（避免人工誊写差错）

上表 2.3.2 行的四个指纹取自**链接期真值文件本身**，提取方式如下（BuildID 曾发生人工誊写转置差错，故固定口径）：

```bash
# sha256 / 大小
sha256sum -- "$SYSROOT/usr/lib/librknnrt.so"; stat -c%s -- "$SYSROOT/usr/lib/librknnrt.so"
# BuildID（readelf 不可用时可直接解析 .note.gnu.build-id 段）
readelf -n -- "$SYSROOT/usr/lib/librknnrt.so" | grep -i 'Build ID'
# 版本串（= 门禁 `grep -i 'librknnrt version'` 的匹配目标）
strings -a -- "$SYSROOT/usr/lib/librknnrt.so" | grep -i 'librknnrt version'
#   → librknnrt version: 2.3.2 (429f97ae6b@2025-04-09T09:09:27)
```

- 版本串内嵌 **源码哈希 `429f97ae6b`** 与 **构建日期 `2025-04-09`**，可作为「同一份二进制」的旁证（与 sha256 一致时互证）。
- 该二进制自身含拒载串 `RKNN Model version: %d.%d.%d not match with rknn runtime version: %d.%d.%d`（`strings` 可查）⇒ **「版本不匹配即硬拒」是 runtime 自身行为**，非我方推断；这正是「模型 toolkit 2.3.2 ⇒ 运行时必须 2.3.2」的机械依据。

> 若将来需要在本目录重新放置 `librknnrt.so`，**前置条件**：升级到 **2.3.2 且 sha256 等于 `d31fc19c…`**、并通过板端验证；否则**不得**作为出货源（本文件的作用域口径同步更新）。
