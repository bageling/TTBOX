# INT8 模型切换手册（M2）

> 适用：把板端 RKNN 模型从 FP16 切到 INT8，并决定是否启用 DMA-BUF 直绑零拷贝。
> 本文档所有结论均带**源码行号锚点**，可逐条复核。凡标注「待板端实测」的，**不是结论，是待验证项**。

---

## 0. 先纠正一个容易踩错的假设

早前的计划写的是：「INT8（zp==-128）→ 把 `rknn_external_dma_input` 翻成 true → 启用零拷贝 XOR 路径」。

**这是错的。** 翻这个开关不会启用 XOR 路径，反而会被拒绝。

`core/src/rknn/RKNNEngine.cpp:363-372`：

```cpp
// 外部 DMA-BUF 直传不做 XOR，仅 UINT8 原生（方案 D）正确；
// INT8（kXorShift128）直传会绕过 XOR → 必错，故一律拒绝（design §G2.3.1）。
if (input_pass_mode() != InputPassMode::kUint8Native) {
    ...
    TTBOX_LOG_WARN("拒绝 external DMA-BUF 直绑：input_pass_mode != kUint8Native（避免绕过 XOR）");
    return false;
}
```

即：**DMA-BUF 直绑（真·零拷贝）只认 `kUint8Native`，与 INT8/zp 无关。**

---

## 1. 三条输入路径（互斥，由唯一判定入口裁决）

判定入口：`core/src/rknn/InputQuant.hpp:58` `classify_input_pass()`。谓词按顺序短路：

| 顺序 | 谓词（`InputQuant.hpp`） | 模式 | RGA → RKNN 搬运 | `rknn_external_dma_input=true` 时 |
|---|---|---|---|---|
| ① `:61-63` | `type==UINT8 ∧ fmt==NHWC` | `kUint8Native` | **零 CPU 触碰**：`rknn_create_mem_from_fd` 直绑 | ✅ **生效** |
| ② `:65-68` | `type==INT8 ∧ fmt==NHWC ∧ qnt==AFFINE_ASYMMETRIC ∧ zp==-128` | `kXorShift128` | CPU 拷贝 + XOR `0x80` | ❌ 被拒（`RKNNEngine.cpp:366`），回退拷贝+XOR |
| ③ `:70` | 其余（FP16 / zp≠-128 / DFP / NONE / NCHW） | `kCompatible` | `set_input()` 喂 UINT8，runtime 内部量化 | ❌ 被拒 |

关键点：

- **`scale` 不参与判定**（`InputQuant.hpp:56-57` 明令禁止在判定里引用 scale）。XOR 正确性只依赖 `zp==-128`。
- ②③ 的区别只是"谁来量化"：② 由我们自己 XOR（省掉 runtime 内部量化开销），③ 交给 runtime。**两者都不是真零拷贝**——像素仍要过一次 CPU。
- ① 才是真零拷贝，但它要求模型输入节点本身是 **UINT8**。
- ② 与 ① 互斥：② 的模型输入是 INT8，直绑必然绕过 XOR ⇒ 算错，所以代码显式拒绝。这是**正确设计**，不是 bug。

补充语义锚点：`RKNNEngine.hpp:122-125` —— `pass_through_active()` 的结构定义是
`zero_copy_ready_ && pass_mode_ == kXorShift128`，语义收窄为"**XOR 快路径已激活**"，不要拿它当"零拷贝已激活"用。

---

## 2. 转换命令

转换脚本 `tools/converter/convert_onnx_to_rknn.py`（默认即 INT8，不是 FP16 转换器）：

```bash
python3 tools/converter/convert_onnx_to_rknn.py \
  --onnx models/onnx/<your_model>.onnx \
  --dataset-root test_images \
  --dataset-count 100 \
  --target-platform rk3588 \
  --quantized-dtype w8a8 \
  --output models/rknn/<your_model>_rk3588.rknn
```

要点：

- **校准集是硬前置**：`--minimum-calibration-images` 默认 32，少于这个数直接拒绝导出（脚本内 `:101` 明确报错 "at least N are required for a stable INT8 export"）。`--dataset-count` 默认 100，从 `--dataset-root` 均匀采样。
- `--mean-values` 默认 `0,0,0`，`--std-values` 默认 `255,255,255`（`:1058-1068`）。即默认把输入归一化到 `[0,1]` 再不对称量化。
- `--no-quantization` 可产出浮点 RKNN 做 A/B 对照（跳过校准要求）。

### 2.1 想要 ① 还是 ②？

- **想要 ①（真零拷贝，UINT8 输入节点）**：目标产物必须是 `input type = UINT8 / NHWC`。默认 `mean=0,std=255` 的输出**大概率**落在这里 —— 但 **待板端实测确认**，不要凭推断下单。
- **想要 ②（XOR 快路径，省 runtime 量化）**：目标产物必须是 `input type = INT8 / NHWC / AFFINE_ASYMMETRIC / zp == -128`。零均值量化（`zp==-128`）的达成方式与 RKNN-Toolkit2 的 mean/std 配置强相关，**必须在板端用日志确认**（见下节）。

> 为什么不给死结论：`input_attr.type/zp` 由 RKNN 量化器在导出时决定，受 mean/std、量化方案、ONNX 首节点形态共同影响。任何"照这个参数就一定得到 zp=-128/UINT8"的说法都不可信。**唯一可信来源是板端 `rknn_query(INPUT_ATTR)` 的实测输出**，而代码已经把它打进日志了。

---

## 3. 怎么确认自己落在哪条路径

### 3.1 首选：看面板（T1.15 起已接线，无需翻日志）

`/api/state` 的 `state.model_input` 与 `/api/core/status` 的 `model_*` 字段已经把这套判据
投影到面板的「运行数据 → 输入通路」卡片上：

| 面板显示 | 含义 |
|---|---|
| `输入通路：XOR 快路径（CPU拷贝+XOR）`（绿） | 落在 ②，每帧走 XOR 搬运，`infer_set_input_ms` 应在 0.1ms 量级 |
| `输入通路：UINT8 原生直传`（绿） | 落在 ①，真零拷贝 |
| `输入通路：兼容 I/O（每帧内部量化）`（琥珀） | 落在 ③。**不是故障**，但每帧 set_input 贵一个量级 |
| 通道值下方的小字 | 输入张量（`int8/nhwc/affine_asymmetric · zp=-128 · scale=3.922e-3 · 640×640`） |
| 第三行小字（仅异常时出现） | **为什么**回落的人话说明，例：`INT8 但 zp=-4 ≠ -128（非零均值）⇒ …` |
| 值末尾 `（快路径 2/3 worker）` | worker 间偏斜（3 个 worker 只有 2 个吃到快路径） |
| `输入通路：未运行` | Core 未运行 / 没数据 —— **不会**显示成"兼容 I/O"（不知道 ≠ 已判定为慢） |

> 判据的唯一来源是 core（`classify_input_pass` 定性 + `describe_input_path` 出人话），
> Web 只做搬运与中文标签翻译。所以**面板说的和日志说的必然一致**。

### 3.2 备用：板端日志即判据（老固件 / 面板不可用时）

`RKNNEngine` 在 init 阶段打印全部判据：

| 日志 | 含义 |
|---|---|
| `RKNN 零拷贝 I/O 已绑定: input=N bytes, outputs=M`（`RKNNEngine.cpp:309`） | 落在 ① 或 ②，`zero_copy_ready_ == true` |
| `RKNN 输入量化守卫：拒绝零拷贝快路径（qnt=… scale=… zp=…）`（`:249-253`） | 落在 ③，**日志里直接给出实测的 qnt/scale/zp** |
| `拒绝 external DMA-BUF 直绑：input_pass_mode != kUint8Native`（`:370`） | 想直绑但落在 ②，被拒（回退拷贝+XOR，结果仍正确） |

**推荐动作**：切模型前先跑一次，把第二条日志里的 `qnt/scale/zp` 抄下来 —— 那就是这个模型输入节点的真实量化参数。

---

## 4. 切换顺序（必须按序，每一步都要可回退）

1. **不改配置**，先部署新 `.rknn` + 对齐 `model_input_width/height`，观察 §3 日志，记录实测路径。
2. 若实测落在 ② 或 ③：**`rknn_external_dma_input` 保持 `false`**（翻成 true 只会吃一条 WARN 然后回退，白折腾）。
3. 若实测落在 ①（UINT8/NHWC）：再把 `rknn_external_dma_input` 翻 `true`，然后**实测** e2e/`resize_ms` 变化。
4. 无论哪条路径，都必须做 A/B：同一段录像、同一 crop 尺寸，比对 `infer_ms` / `infer_set_input_ms` / `e2e_ms`。**没有 A/B 数字就不要宣布性能改善。**

### 4.1 三个配置文件不要漏改

| 文件 | `model_input_width` | `rknn_external_dma_input` |
|---|---|---|
| `config/default.json` | 640 | `false` |
| `config/yolo261n-rk3588.json` | 640 | （无此键） |
| `deploy/config/00-factory.json` | **256** | `false` |
| `deploy/config/default.json.prod` | **256** | `false` |

⚠️ **注意开发/生产输入尺寸已经不一致**：开发配置是 640，两个生产配置是 **256**。换模型时若只改一处，会出现"开发能跑、出厂跑不起来"。以生产配置为准对齐。

---

## 5. 未决项（建议的下一步）

1. **可观测性缺口（建议优先）**：`input_pass_mode` / `zero_copy_ready` / `input_zp` / `input_scale` **目前没有暴露到 Metrics / IPC / Web**，只存在于日志里（`Metrics.hpp` 的 `PipelineMetrics` 无对应字段，`IpcServer` 也不序列化）。后果：**面板上无法确认换了模型后快路径到底有没有生效**，只能翻 journalctl。
   - 建议链路（共 9 处）：`WorkerStats`（`WorkerPool.hpp:48`）→ `CoreRuntime.cpp:255-285` 聚合 → `Types.hpp` metrics 结构 → `Metrics.hpp::PipelineMetrics` → `IpcServer.cpp` 序列化 → `ttbox-web.py` 投影 → 面板展示。
   - 这一项做完，"换 INT8 模型"才算**可验证**而不是**靠猜**。
2. **板端实测**：`InputQuant.hpp` 的 XOR 路径是 NEON + 标量双实现，Windows 侧只跑了标量断言；`__ARM_NEON` 分支需板端回归。
3. **FP16 vs INT8 的 A/B 数字**：需在同一目标尺寸下产出两组数（当前仓库内**无任何 `.rknn` 产物**，模型另行部署）。

---

## 6. 相关源码锚点速查

| 主题 | 位置 |
|---|---|
| 输入量化判定（唯一入口） | `core/src/rknn/InputQuant.hpp:58-71` |
| XOR 0x80 搬运（NEON + 标量） | `core/src/rknn/InputQuant.hpp:77-94` |
| 零拷贝 I/O 绑定 / kCompatible 拒绝 | `core/src/rknn/RKNNEngine.cpp:227-313` |
| external DMA-BUF 直绑 / kUint8Native 守卫 | `core/src/rknn/RKNNEngine.cpp:347-399` |
| 模式语义（`pass_through_active`） | `core/src/rknn/RKNNEngine.hpp:110-125` |
| WorkerPool 按模式分发搬运 | `core/src/rknn/WorkerPool.cpp:262-320` |
| 状态聚合成指标 | `core/src/runtime/CoreRuntime.cpp:255-285` |
| 转换器 CLI 与校准集门槛 | `tools/converter/convert_onnx_to_rknn.py:1004-1104` |
