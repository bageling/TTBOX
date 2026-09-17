# RELEASE_BUILD.md — 出货构建留档（T1.15）

> 本文件由 `scripts/ttbox_build_release.sh` **自动生成**（门禁全绿后才写）。
> 字段定义：`build-reproducibility.md` §10；单行记录格式：§5。**手工改动无效**（下次构建覆盖）。
> `commit` 的语义 = **构建时被编译的源码 commit**（脚本不会有未提交的 core 源码改动）；
> 本留档自身、以及其后追加的提交都**不改变**该值 —— 故 HEAD 与它不一致属**正常**，不是错。
> `vector_hash` 只覆盖**配置向量那一半**（不含 commit），与 `commit` 合成 (源码, 向量) 二元组。

## 留档字段

| 字段 | 值 |
|---|---|
| `commit` | `HEAD
unknown` |
| `toolchain` | /usr/bin/aarch64-linux-gnu-g++ (GCC 11.4.0) — aarch64-linux-gnu-g++ (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0 |
| `build_type` | `Release` |
| `generator` | `Ninja` |
| `sysroot` | `/mnt/c/Users/Administrator/WorkBuddy/2026-09-15-15-04-22/sysroot-aarch64` |
| `build_host` | Ubuntu 22.04.5 LTS @ PC-20260805NLLL |
| `build_time_utc` | 2026-09-17T04:22:04Z |
| `configure_args` | `cmake -S /mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main/core -B /mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main/build-aarch64-deploy -G Ninja -DTTBOX_SHIP=ON -DTTBOX_PROJECT_ROOT=/opt/ttbox -DTTBOX_CORE_BUILD_AUTH=OFF -DCMAKE_TOOLCHAIN_FILE=/mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main/deploy/cmake/toolchain-aarch64.cmake -DCMAKE_SYSROOT=/mnt/c/Users/Administrator/WorkBuddy/2026-09-15-15-04-22/sysroot-aarch64 -DTTBOX_CROSS_AARCH64=ON -DCMAKE_BUILD_TYPE=Release` |
| `product_path` | `/mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main/build-aarch64-deploy/ttbox_core_main` |
| `md5` | `f8b4134337370b4f5a36b31f45ba73ed` |
| `sha256` | `8c436ee9270044eb743b4d2151f0068277add705bb50c97953c2adf72bac022f` |
| `size` | 935440 B |
| `buildid` | `8b05acf870a02e9946c3fcd46d2645afbc81793d` |
| `strings_gate` | `third=0/ip=0/cred=0`（(a)第三方域名=0 硬门禁 PASS / (b)凭据字面量=0 硬门禁 PASS / (c)自有端点=0 登记〔M1 期望 0，不判 FAIL〕） |
| `selfcheck` | RUNPATH 逐段 PASS / NEEDED 覆盖 PASS（10 项 NEEDED 全部覆盖：随包 1 + 基础镜像白名单） / librknnrt 同源 PASS / lib 闭集 1（host 侧 `readelf` 静态；★ 交叉产物 host **不能** `ldd`，运行期 `ldd` 归板端 T1.13） |
| `usbproxy` | sha256 `bf8b98c6d4bbdd86bcb122d6bfef62d4734a06524c675a6c3409fb9e3efcff25`（= `.sha256` 声明值 ✅）；旧目录字面量=0 ✅；重建：未执行（本脚本默认不做 usbproxy 重建；设 TTBOX_RELEASE_VERIFY_USBPROXY_REBUILD=1 启用，判据见 build-reproducibility.md §11②） |
| `repro_verify` | 未执行（本脚本默认不做二次 clean build；设 TTBOX_RELEASE_VERIFY_REPRO=1 启用，判据见 build-reproducibility.md §13 V4） |
| `librknnrt_version` | `2.3.2` / sha256 `d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8`（须 = 2.3.2 / `d31fc19c…`；基准 = 链接期=模型=板端 三重一致） |
| `vector_hash` | `a2bbcbf395a1669699240bade6167919f2a79a622678cf28eaeb76364962a83a`（**配置向量**指纹，**不含 commit**——可复现单元 = (源码 commit, 配置向量) 二元组；换任一向量项即变 ⇒ 与旧留档**不可比**） |
| `notes` | 上次留档对照：无上次留档（首次）；A4①=0（期望 0）/ A4②=1（期望 >=1）；CROSS_AARCH64=ON |

## §5 单行记录（供仓库外备份共用）

```
HEAD
unknown | /usr/bin/aarch64-linux-gnu-g++ (GCC 11.4.0) — aarch64-linux-gnu-g++ (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0 | /mnt/c/Users/Administrator/WorkBuddy/2026-09-15-15-04-22/sysroot-aarch64 | /mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main/build-aarch64-deploy/ttbox_core_main | md5=f8b4134337370b4f5a36b31f45ba73ed | BuildID=8b05acf870a02e9946c3fcd46d2645afbc81793d | 2026-09-17T04:22:04Z | Ubuntu 22.04.5 LTS @ PC-20260805NLLL | third=0/ip=0/cred=0
```

<!-- RELEASE_BUILD_RECORD: commit=HEAD
unknown md5=f8b4134337370b4f5a36b31f45ba73ed sha256=8c436ee9270044eb743b4d2151f0068277add705bb50c97953c2adf72bac022f buildid=8b05acf870a02e9946c3fcd46d2645afbc81793d vector_hash=a2bbcbf395a1669699240bade6167919f2a79a622678cf28eaeb76364962a83a -->
