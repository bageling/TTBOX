# TTBOX candidate_rect 逆向调查报告

> 状态：调查完成，暂不实施 Candidate Builder。本文只记录已验证事实，不把推断写成事实。

## 结论摘要

1. YU 可读 Core 源码中没有找到 `candidate_rect` 的生成字段或函数实现。
2. YU 二进制存在独立 native 目标链：`TrackedTarget`、`BuildDetectionObject`、`BuildTargetPoint`、`BuildLoopoutOverlayBoxes`、`CalibrationCandidateWindow`、`IsSameAimTargetCandidate`。
3. YU 可读源码中的 `DetectionGeometryFilter::filter()` 只做 head/body 几何关联和过滤，成对输出原始框；源码注释明确写着“保留原始框”，没有生成 union 矩形。
4. YU 使用的模型清单显示 7 类、256×256、6 输出，但没有提供 0–6 的类别名称映射；预设里的 `class_filter_mask` 和 `auto_back_flick.class_id=0` 只能证明配置引用了类别编号，不能证明语义。
5. TTBOX 当前激活模型 `jwdl_sjzv11` 的仓库元数据明确给出：`0=head, 1=body, 2=enemy_1, 3=enemy_2, 4=enemy_3, 5=item, 6=other`。
6. RK3588 当前真实运行状态：`detections=1`、`detection_boxes=1`、`class_id=3`、框约 `65×255`。没有 head/body/limb 多框可供可靠融合。
7. 因此当前证据更符合“TTBOX 模型/类别体系与 YU native 模型/目标体系不同或至少尚未证明一致”，而不是“TTBOX 只漏了一个简单 union 步骤”。

## 1. YU candidate_rect 生成链

### 已确认的可读源码链

```text
YU detector
  → Detection / TrackedTarget（native 运行时）
  → BuildDetectionObject(TrackedTarget, AimProfile)
  → BuildTargetPoint(TrackedTarget, AimProfile)
  → BuildLoopoutOverlayBoxes(TrackedTarget[], CaptureConfig, ...)
  → native/API 状态
  → Web
```

### 文件与函数证据

| 层 | 文件/位置 | 函数/证据 | 结论 |
|---|---|---|---|
| 检测解码 | `yu-backend/yu-core-src/core/src/rknn/DecodeNMS.cpp:653-711` | `DecodeNMS::process_e2e()` | 读取 `[x1,y1,x2,y2,score,class]`，只产生 `DetectionBox` |
| 头身几何 | `yu-backend/yu-core-src/core/src/rknn/DetectionGeometryFilter.cpp:19-69` | `DetectionGeometryFilter::filter()` | 按 `head_class_id/body_class_id` 关联；输出原始 head/body 框，不生成 union |
| 目标选择 | `yu-backend/yu-core-src/core/src/mouse/TargetSelector.cpp:17-37,39-216` | `collect_candidates()`、`select()` | 置信度/class_filter/FOV/距离/track lock/continuity；`out.box` 仍是单个 DetectionBox |
| native 目标对象 | 板端 `/opt/aiassistance/bin/aiassistance_daemon` | `BuildDetectionObject(TrackedTarget, AimProfile)` | 二进制本地函数，源码未恢复 |
| native 目标点 | 同上 | `BuildTargetPoint(TrackedTarget, AimProfile)` | 根据 TrackedTarget 与 AimProfile 计算控制点；不是 candidate_rect 生成证据 |
| native 预览框 | 同上 | `BuildLoopoutOverlayBoxes(TrackedTarget[], CaptureConfig, ...)` | 由 tracked target 集合生成预览 OverlayBox；源码未恢复 |
| 候选稳定窗口 | 同上 | `CalibrationCandidateWindow::Reset()`、`IsSameAimTargetCandidate(...)` | 使用 track、class、矩形和连续性判断候选是否同一目标 |

### `candidate_rect` 的直接证据

- YU Web/状态字段中存在：`candidate_track_id`、`candidate_class_id`、`candidate_count`、`candidate_rect`、`stable_frames`、`center_jitter_px`、`size_variation`。
- 板端二进制字符串和符号确认上述状态和 native 函数存在。
- 在 `yu-backend/yu-core-src` 可读源码中全仓搜索，没有找到 `candidate_rect` 的字段定义、赋值语句或生成函数体。
- 因此目前能确认“YU native 运行时生成/维护 candidate_rect”，还不能从可读源码确认其完整数值公式。

### 是否 box union / 固定扩展

当前证据：

- `DetectionGeometryFilter::filter()` 没有 union；只 `out.push_back(head)` 和 `out.push_back(body)`。
- `TargetSelector::select()` 没有 union；每次选择只返回一个 `DetectionBox`。
- `BuildTargetPoint()` 的反汇编表现为对 tracked target 框和 AimProfile 比例做目标点计算，不是矩形 union。
- `BuildDetectionObject()` 的反汇编表现为组装目标对象、读取 track/class/offset 等字段；当前反汇编证据不足以证明其生成 candidate_rect。
- `BuildLoopoutOverlayBoxes()` 接收 `TrackedTarget[]`，是目前最接近 Web Overlay 生成的 native 入口，但其完整源码和结构体布局未恢复。
- 所以不能把 YU candidate_rect 认定为“所有附近框 union”，也不能认定为固定比例扩框。

## 2. YU class_id 语义

### 已验证内容

YU 板端模型清单：

```json
{
  "class_count": 7,
  "input_width": 256,
  "input_height": 256,
  "output_count": 6,
  "model_id": "sjzv11"
}
```

YU 预设：

```text
class_filter_mask = 127
loopout_overlay.class_mask = 1073741823
auto_back_flick.class_id = 0
```

### 尚未证实内容

YU 模型清单和预设没有给出：

```text
0 = ?
1 = ?
2 = ?
3 = ?
4 = ?
5 = ?
6 = ?
```

二进制存在 `ReadModelClassNames(json, ...)`，说明类别名称可能来自模型/配置运行时读取，但当前板端可读配置没有保存这 7 个名称。仅凭 `class_id=5/6` 出现在状态或字符串中，不能推出它们是 body、limb、item 或其他语义。

### 与 TTBOX 的对照

TTBOX 当前激活模型元数据已明确保存：

```text
0 = head
1 = body
2 = enemy_1
3 = enemy_2
4 = enemy_3
5 = item
6 = other
```

因此：

- TTBOX `class_id=3` 的真实语义是 `enemy_2`（仅对当前 TTBOX `jwdl_sjzv11` 模型成立）。
- YU `class_id=3` 的语义尚未获得证据。
- 不能把 TTBOX `class_id=3` 与 YU `class_id=3` 直接视为同义。
- 不能把 YU `class_id=5/6` 映射到 TTBOX 的 `item/other`。

## 3. TTBOX 当前完整链路

```text
RKNN
  ↓
InferenceWorker::run()                  core/src/rknn/WorkerPool.cpp:280-305
  ↓
ModelAdapter / DecodeNMS::process()     core/src/model/Decoder.hpp
  ↓
DetectionBox[]                           core/src/common/Types.hpp:44-51
  ↓
InferenceWorker 写入 AimTargetTask      core/src/rknn/WorkerPool.cpp:299-305
  ↓
AimThread::run()                         core/src/aim/AimThread.cpp:67
  ↓
TargetSelector::select()                 core/src/mouse/TargetSelector.cpp:39-216
  ↓
selected.box（单个控制目标）
  ↓
AimThread Status / PipelineMetrics
  ↓
IpcServer GET_STATUS
  ↓
Gateway /api/state
  ↓
Web
```

当前丢失完整候选矩形的位置：

```text
DecodeNMS 输出 DetectionBox[]
  → 没有 Candidate / TrackedTarget 领域对象
  → TargetSelector 直接从原始框中选一个 selected.box
  → IPC 只能得到单个控制框
```

`TargetSelector` 的 track 只对单个 `DetectionBox` 做连续帧匹配，不负责 head/body/limb 关联，也不维护 Candidate 矩形。

## 4. A/B 判断

### 情况 A：模型输出 head/body/limb，但 TTBOX 没有组合

当前真实证据不支持直接判定情况 A：

```text
当前 RK3588：detection_boxes 数量 = 1
当前唯一框：class_id = 3，约 65×255
```

单帧没有同时出现 `head`、`body` 或 limb 框。仅凭“YU 页面框更完整”不足以证明 TTBOX 模型其他输出被 DecodeNMS 丢弃。

### 情况 B：TTBOX 与 YU 模型/类别体系不同

当前证据更支持情况 B，理由：

1. YU 使用的模型条目为 `sjzv11`，YU 模型文件为加密模型 `巨无敌乱杀sjzv11_.rknn.enc`。
2. TTBOX 激活的是 `jwdl_sjzv11`，类别名称由 TTBOX manifest 明确给出。
3. YU native 使用 `TrackedTarget` 和额外的 preview/candidate 组织链，TTBOX 当前没有等价领域对象。
4. YU class 0–6 名称未从板端配置恢复，不能建立安全的类别映射。
5. TTBOX 当前真实帧只有一个 `enemy_2` 局部框，没有可验证的组成信息。

## 5. 本轮实施决定

暂不新增 `CandidateBuilder`，原因：

- 输入只有一个局部 `DetectionBox`，没有足够信息构造完整人体边界。
- YU 的类别语义未恢复，不能按 class_id 硬映射。
- YU candidate_rect 的 native 生成公式未恢复，不能用固定比例或邻近框阈值仿造。
- 在此时新增 Candidate Builder 只能生成一个未经证据支撑的伪矩形，违反真实数据原则。

## 6. 需要补齐的证据

要继续实现真正的 Core Candidate 层，至少需要以下一个真实来源：

1. YU native `BuildLoopoutOverlayBoxes` / candidate 生成的可读源码；或
2. YU 模型解密后的 RKNN 元数据、类别标签和同帧原始输出；或
3. TTBOX 当前模型在真实目标画面上持续输出 head/body/limb 等关联检测；或
4. 明确的产品模型更换决定，将 TTBOX 激活模型替换为类别和目标矩形语义已知的模型。

在这些证据出现前，正确状态是：

```text
Raw Detection：REAL
TTBOX selected target：REAL
TTBOX candidate_rect：缺失
YU candidate_rect：native 行为已证实，公式未恢复
完整人体框：VERIFY / PLANNED
前端伪造扩框：禁止
```

## 7. 本轮修改

本轮调查阶段没有修改：

- 前端 CSS
- 前端框放大逻辑
- 前端坐标缩放
- TTBOX Core
- DecodeNMS
- TargetSelector
- PID
- HID
- 采集/RGA/RKNN

仅新增本调查文档。
