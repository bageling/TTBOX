# RELEASE_BUILD.md — 出货构建留档（T1.15）

> 本文件由 `scripts/ttbox_build_release.sh` **自动生成**（门禁全绿后才写）。
> 字段定义：`build-reproducibility.md` §10；单行记录格式：§5。**手工改动无效**（下次构建覆盖）。
> `commit` 的语义 = **构建时被编译的源码 commit**（脚本不会有未提交的 core 源码改动）；
> 本留档自身、以及其后追加的提交都**不改变**该值 —— 故 HEAD 与它不一致属**正常**，不是错。
> `vector_hash` 只覆盖**配置向量那一半**（不含 commit），与 `commit` 合成 (源码, 向量) 二元组。

## 留档字段

| 字段 | 值 |
|---|---|
| `commit` | `b191b5e7f3ca08c2dffe48dd4332a14ce8bf58a3` |
| `toolchain` | /usr/bin/aarch64-linux-gnu-g++ (GCC 11.4.0) — aarch64-linux-gnu-g++ (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0 |
| `build_type` | `Release` |
| `generator` | `Ninja` |
| `sysroot` | `/mnt/c/Users/Administrator/WorkBuddy/2026-09-15-15-04-22/sysroot-aarch64` |
| `build_host` | Ubuntu 22.04.5 LTS @ PC-20260805NLLL |
| `build_time_utc` | 2026-09-18T01:17:43Z |
| `configure_args` | `cmake -S /mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main/core -B /mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main/build-aarch64-t114 -G Ninja -DTTBOX_SHIP=ON -DTTBOX_PROJECT_ROOT=/opt/ttbox -DTTBOX_CORE_BUILD_AUTH=OFF -DCMAKE_TOOLCHAIN_FILE=/mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main/deploy/cmake/toolchain-aarch64.cmake -DCMAKE_SYSROOT=/mnt/c/Users/Administrator/WorkBuddy/2026-09-15-15-04-22/sysroot-aarch64 -DTTBOX_CROSS_AARCH64=ON -DCMAKE_BUILD_TYPE=Release` |
| `product_path` | `/mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main/build-aarch64-t114/ttbox_core_main` |
| `md5` | `f72d56e5787f9523f9b53d68d24c4a8f` |
| `sha256` | `1a7bdd7467d12082720723820583cfb4fb80a06d3514af96f130b587cb3b4ff5` |
| `size` | 1051520 B |
| `buildid` | `8a4a75baf0c5b0624fca8a6c294a52b715bf696f` |
| `strings_gate` | `third=0/ip=0/cred=0`（(a)第三方域名=0 硬门禁 PASS / (b)凭据字面量=0 硬门禁 PASS / (c)自有端点=0 登记〔M1 期望 0，不判 FAIL〕） |
| `selfcheck` | RUNPATH 逐段 PASS / NEEDED 覆盖 PASS（10 项 NEEDED 全部覆盖：随包 1 + 基础镜像白名单） / librknnrt 同源 PASS / lib 闭集 1（host 侧 `readelf` 静态；★ 交叉产物 host **不能** `ldd`，运行期 `ldd` 归板端 T1.13） |
| `usbproxy` | sha256 `bf8b98c6d4bbdd86bcb122d6bfef62d4734a06524c675a6c3409fb9e3efcff25`（= `.sha256` 声明值 ✅）；旧目录字面量=0 ✅；重建：未执行（本脚本默认不做 usbproxy 重建；设 TTBOX_RELEASE_VERIFY_USBPROXY_REBUILD=1 启用，判据见 build-reproducibility.md §11②） |
| `repro_verify` | 同源 + 同配置向量、不同构建目录 ⇒ 逐字节相同 ✅（候选 B 层；md5 f72d56e5787f9523f9b53d68d24c4a8f） |
| `librknnrt_version` | `2.3.2` / sha256 `d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8`（须 = 2.3.2 / `d31fc19c…`；基准 = 链接期=模型=板端 三重一致） |
| `vector_hash` | `0505ccefc7315ac4df2f59b9c1c4f8b29a7127f9f469f8ddcafea29a9d016994`（**配置向量**指纹，**不含 commit**——可复现单元 = (源码 commit, 配置向量) 二元组；换任一向量项即变 ⇒ 与旧留档**不可比**） |
| `notes` | 上次留档对照：配置向量相同，但源码 commit 已变（70d07f7… → b191b5e…） ⇒ 本次 md5=f72d56e5787f9523f9b53d68d24c4a8f 与上次的差异属**预期改变**（非非确定性）；A4①=0（期望 0）/ A4②=1（期望 >=1）；CROSS_AARCH64=ON |

## §5 单行记录（供仓库外备份共用）

```
b191b5e7f3ca08c2dffe48dd4332a14ce8bf58a3 | /usr/bin/aarch64-linux-gnu-g++ (GCC 11.4.0) — aarch64-linux-gnu-g++ (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0 | /mnt/c/Users/Administrator/WorkBuddy/2026-09-15-15-04-22/sysroot-aarch64 | /mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main/build-aarch64-t114/ttbox_core_main | md5=f72d56e5787f9523f9b53d68d24c4a8f | BuildID=8a4a75baf0c5b0624fca8a6c294a52b715bf696f | 2026-09-18T01:17:43Z | Ubuntu 22.04.5 LTS @ PC-20260805NLLL | third=0/ip=0/cred=0
```

<!-- RELEASE_BUILD_RECORD: commit=b191b5e7f3ca08c2dffe48dd4332a14ce8bf58a3 md5=f72d56e5787f9523f9b53d68d24c4a8f sha256=1a7bdd7467d12082720723820583cfb4fb80a06d3514af96f130b587cb3b4ff5 buildid=8a4a75baf0c5b0624fca8a6c294a52b715bf696f vector_hash=0505ccefc7315ac4df2f59b9c1c4f8b29a7127f9f469f8ddcafea29a9d016994 -->

## 备注（人工追加，非脚本生成；下次 clean build 会覆盖本节）

> 本节由口径整改（无补丁）任务于 2026-09-18 人工追加，仅供交接说明；上方机器字段
> （commit/md5/sha256/buildid/vector_hash）一律由 `scripts/ttbox_build_release.sh` 生成，未手工改动。

- 本目录（`build-aarch64-t114`）在 1.4.4 之前持有 **1.4.3** 锚：core sha256
  `3f760bc1c5e939feb98e148e0f9414ec6df073a9c25abe5af67468b872ffceb0`，commit `70d07f76…`。
- 构建脚本每次发货前 `rm -rf` 本目录并重写本留档 ⇒ **旧锚已不在工作树**，仅存在于 git 历史
  （HEAD 之前）；因此 **1.4.2 / 1.4.3 旧锚现无法在本目录复核**（板上 `releases/1.4.1|1.4.2|1.4.3`
  仍在、可运行/可回滚，但无与之对应的可复核留档）。
- 1.4.4 构建合规（§6-C6）：**先提交源码 → 再构建 → 再提交留档**；`commit` 字段 =
  被编译源码 commit `b191b5e`，其后的追加提交（口径回归/预览修复）**不改变**该值。

