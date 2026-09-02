# YU Target Object 模型

## 证据等级

- **CONFIRMED**：源码、符号、汇编或真机状态直接证明。
- **LIKELY**：由 ABI、调用关系和稳定内存访问模式推断。
- **UNKNOWN**：当前证据不足。

## 1. Detection

**CONFIRMED**

YU native detector 对外暴露：

```cpp
CoreLoader::Detector::DetectTrack(
    cv::Mat const&,
    std::vector<Detection>* detections,
    std::vector<TrackedTarget>* tracks,
    double* elapsed
)
```

二进制符号地址：`0x103260`。

`Detection` 是 detector 输出的原始检测对象；可读 TTBOX/YU 共用的 RKNN 解码器中，基础字段为：

```text
x1, y1, x2, y2, score, class_id
```

**CONFIRMED**：`DecodeNMS::process_e2e()` 在 `yu-backend/yu-core-src/core/src/rknn/DecodeNMS.cpp:653-711` 读取 `[x1,y1,x2,y2,score,class]`。

## 2. Detection → TrackedTarget

**CONFIRMED**：YU native 入口 `CopyCoreDetectTrackVectors(...)`：

```cpp
CopyCoreDetectTrackVectors(
  const std::vector<AiCoreDetection>&,
  int,
  const std::vector<AiCoreTrack>&,
  int,
  std::vector<Detection>* out_detections,
  std::vector<TrackedTarget>* out_tracks
)
```

符号地址：`0x101e10`。

调用者包括：

```text
CoreLoader::Detector::DetectTrackRawCapture 0x102560
CoreLoader::Detector::DetectTrackImage      0x102a50
CoreLoader::Detector::DetectTrack           0x103260
CoreLoader::Detector::RunPrepared           0x1027f0
```

**CONFIRMED**：`CopyCoreDetectTrackVectors` 将底层 `AiCoreTrack` 复制到 `TrackedTarget` 向量；代码存在独立的 `Detection` 与 `TrackedTarget` 输出向量，说明 TrackedTarget 不是 Detection 的简单别名。

## 3. TrackedTarget 结构

**LIKELY，基于汇编布局**

`CopyCoreDetectTrackVectors` 对每个 track 以 `0x20`（32）字节步长读取；并写入目标向量。`BuildLoopoutOverlayBoxes` 对每个 target 以 `0x20` 步长读取，并访问：

```text
+0x00..+0x0f：4 个连续 32 位数值，作为矩形四元组
+0x10       ：32 位字段，传入 OverlayBox
+0x14       ：字节/状态字段在其他构造路径出现
+0x18       ：32 位字段，在 BuildDetectionObject / BuildTargetPoint 被读取
+0x1c       ：至少存在浮点/状态访问的可能，需更多调用点确认
```

**CONFIRMED**：`BuildLoopoutOverlayBoxes` 从 target `+0x04` 开始读取 16 字节并复制到输出框，随后读取 target `+0x18` 写入输出框 `+0x10`。

**LIKELY**：前 4 个字段是 target 的矩形（x、y、w、h 或 x1、y1、x2、y2）；因为 OverlayBox 生成阶段对前两个值乘输出比例，并将后两个值作为宽高参与加法和裁剪。具体是 xywh 还是 xyxy，当前还不能只凭局部汇编定论。

**UNKNOWN**：完整 `TrackedTarget` 字段名、track 生命周期字段、预测框字段和时间戳字段。

## 4. BuildDetectionObject

文件：板端 `/opt/aiassistance/bin/aiassistance_daemon`。

符号：

```text
aiassistance::(anonymous namespace)::BuildDetectionObject(
  aiassistance::TrackedTarget const&,
  aiassistance::AimProfile const&
)
```

地址：`0x130880`，大小 560 字节。

**CONFIRMED**：函数返回一个小对象，调用者在 `0x19e060` 和 `0x19e5a4` 使用；输出对象至少包含：

```text
+0x00..+0x0f：从 TrackedTarget +0x04 复制的四元组
+0x10       ：从 TrackedTarget +0x1c 读取的浮点值
+0x14/+0x18 ：从 TrackedTarget +0x18 与 +0x00 读取的整数字段组合
+0x1c       ：TrackedTarget +0x14 的字节状态
+0x20..+0x27：AimProfile offset/比例处理后的两个浮点值
+0x28       ：置信度/有效性相关浮点值
+0x2c       ：按 class_id 查找 AimProfile class_offsets 后生成的整数字段
```

**LIKELY**：输出类型就是 `DetectionObject`，包含原始/跟踪矩形、类别、置信度、track 相关字段及控制参数派生值。

**CONFIRMED**：该函数不是 `candidate_rect` 的直接生成证据；它为后续 `AimSelector::Select(std::vector<DetectionObject>, ...)` 组装选择对象。

## 5. BuildTargetPoint

符号：

```text
BuildTargetPoint(TrackedTarget const&, AimProfile const&)
```

地址：`0x133220`，大小 340 字节。

**CONFIRMED**：返回两个浮点数，写入输出地址 `x8`。

其计算逻辑：

```text
读取 AimProfile offset_x / offset_y
限制到 [0, 1]
按 TrackedTarget 的 class_id 查找 class_offsets
若找到 class-specific offset，则使用该覆盖值
读取 TrackedTarget 的前部四元组矩形
输出 = 矩形起点 + offset * 矩形尺寸
```

**CONFIRMED**：TargetPoint 不是 bbox center 固定值，也不是预测轨迹中心；它是由目标矩形和 AimProfile 百分比偏移计算的点。

**CONFIRMED**：YU 的默认比例在预设中是 `offset_x=0.5`、`offset_y=0.5`；当前 native 函数还支持按 class_id 覆盖。

## 6. BuildLoopoutOverlayBoxes

符号：

```cpp
BuildLoopoutOverlayBoxes(
  const std::vector<TrackedTarget>&,
  const CaptureConfig&,
  int, int, int, int
) -> std::vector<HdmiOverlayBox>
```

地址：`0x166330`，大小 1028 字节。

**CONFIRMED**：函数逐个处理 `TrackedTarget`，先复制其 5 个字段到 `HdmiOverlayBox`，再计算输出图尺寸缩放因子：

```text
scale_x = output_width / source_width
scale_y = output_height / source_height
```

随后对每个矩形：

```text
x = round(source_x * scale_x)
y = round(source_y * scale_y)
w = round(source_w * scale_x)
h = round(source_h * scale_y)
```

再执行边界裁剪：

```text
x/y 不小于 0
x+w 不超过输出宽
y+h 不超过输出高
```

**CONFIRMED**：该函数没有遍历相邻目标后做 min/max union；它对 target vector 的每个元素生成一个 OverlayBox。

**LIKELY**：YU 页面看到的完整框来自 detector/tracker 产出的 TrackedTarget 矩形本身，或来自更早的 native track 几何处理，不是 OverlayBox 阶段扩出来的。

## 7. IsSameAimTargetCandidate

符号：

```cpp
IsSameAimTargetCandidate(
  TrackedTarget const&,
  int,
  cv::Rect<int> const&,
  int
) -> bool
```

地址：`0x1340b0`，大小 256 字节。

**CONFIRMED**：函数第一步调用 `RectIou(existing_rect, candidate_rect)`。

**CONFIRMED**：比较逻辑包含：

- IoU 上限裁剪到 1.0
- IoU 与约 `0.45` 的阈值比较分支
- 两个矩形中心点的欧氏距离平方比较
- 距离阈值由候选矩形尺寸派生
- 最终返回 bool

**LIKELY**：它的语义是“新候选矩形是否仍属于当前 TrackedTarget”，而不是生成 candidate_rect。

**CONFIRMED**：AimLoop 在 `0x1aed38`、`0x1aef30` 调用该函数，说明它参与标定/候选连续性维护。

## 8. CalibrationCandidateWindow::Reset

符号：

```cpp
CalibrationCandidateWindow::Reset(std::string const& reason)
```

地址：`0x1338c0`，大小 308 字节。

**CONFIRMED**：Reset 会：

- 释放窗口内部动态 vector
- 清空计数/状态字节
- 保存 reason 字符串
- 将多个浮点状态清零或重置
- 将候选/采样相关字段清零

**LIKELY**：该对象保存的是标定期间的连续候选观测窗口，不是检测器输出对象本身。

**UNKNOWN**：窗口中每个 observation 的完整结构和 `candidate_rect` 字段的精确偏移。

## 9. CandidateRect / OverlayBox 的区分

```text
Detection        = detector 原始检测结果
TrackedTarget    = native tracker 对检测结果建立的跟踪对象
TargetPoint      = TrackedTarget 矩形 + AimProfile 百分比偏移得到的控制点
DetectionObject  = 供 AimSelector 使用的派生选择对象
CandidateRect    = native/API 状态中的候选矩形，生成函数源码尚未恢复
OverlayBox       = TrackedTarget 矩形按输出画面尺寸缩放、裁剪后的显示框
```

**CONFIRMED**：OverlayBox 本身不是 union。

**UNKNOWN**：CandidateRect 是否直接等于 TrackedTarget 矩形，或来自标定候选窗口的另一份矩形。

## 10. class 语义

YU：

```text
class_count = 7
class_filter_mask = 127
```

**UNKNOWN**：YU 0–6 的名称。板端 `model-list.json` 未保存 labels；预设只证明使用了编号过滤，不能证明名称。

TTBOX 当前 `jwdl_sjzv11`：

```text
0=head
1=body
2=enemy_1
3=enemy_2
4=enemy_3
5=item
6=other
```

**CONFIRMED**：TTBOX `class_id=3` 是 `enemy_2`。

**UNKNOWN**：YU `class_id=3` 是否同义。禁止映射。

## 11. 实施状态

```text
Detection             CONFIRMED
Detection→TrackedTarget PARTIAL
TrackedTarget layout  LIKELY
DetectionObject       PARTIAL
TargetPoint           CONFIRMED
OverlayBox            CONFIRMED
CandidateRect         PARTIAL
IsSameAimTargetCandidate CONFIRMED(partial logic)
CalibrationWindow     PARTIAL
YU class mapping      UNKNOWN
TTBOX/YU semantic parity NOT READY
TTBOX CandidateBuilder NOT READY
```
