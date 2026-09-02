## 当前结论（2026-09-02 日间任务更新）

本轮继续确认：YU 的 `OverlayBox` 是逐个 TrackedTarget 的显示框转换，不做 union；YU 的 `TargetPoint` 是 TrackedTarget 矩形配合 AimProfile offset 计算；YU 的 CandidateRect 生成公式与 class 0–6 语义仍未恢复。因此 TTBOX 等价 CandidateBuilder 继续保持 `NOT READY`，禁止从单个局部框伪造完整目标。

本轮仅清理 Web 假成功接口，未修改 Core、TargetSelector、前端框逻辑或坐标缩放。


## 结论

本轮恢复了 YU Target Object 的主要边界，但没有恢复足够证据实现 TTBOX CandidateBuilder；CandidateBuilder 状态为 `NOT READY`。

## 1. 五层对象

- **YU Detection**：RKNN/底层 detector 输出的原始检测对象，至少含矩形、score、class。
- **YU TrackedTarget**：由 `CoreLoader::Detector::DetectTrack*()` 输出；`CopyCoreDetectTrackVectors()` 将底层 `AiCoreTrack` 转为该对象。它至少保存矩形、class/状态字段和 track 相关数据。
- **YU TargetPoint**：`BuildTargetPoint(TrackedTarget, AimProfile)` 在 `/opt/aiassistance/bin/aiassistance_daemon:0x133220` 生成；公式是矩形起点加 `AimProfile` 的归一化 offset×矩形尺寸，并支持 class-specific offset。
- **YU CandidateRect**：YU native/API 状态中的候选矩形；字段和稳定窗口已确认，但生成函数和完整公式仍未从可读源码恢复。
- **YU OverlayBox**：`BuildLoopoutOverlayBoxes(...):0x166330` 生成的显示框；逐个复制 TrackedTarget 矩形，按输出尺寸缩放并裁剪，不做 union。

## 2. 关键函数

- `BuildDetectionObject(TrackedTarget, AimProfile)`：组装供 `AimSelector::Select()` 使用的 `DetectionObject`，含原始矩形、类别/置信度以及按 AimProfile 派生的字段；不是已确认的 CandidateRect 生成器。
- `BuildTargetPoint(...)`：生成控制目标点，不生成矩形。
- `BuildLoopoutOverlayBoxes(...)`：把每个 TrackedTarget 矩形转换为每个 OverlayBox；没有相邻框 min/max union。
- `IsSameAimTargetCandidate(TrackedTarget, int, cv::Rect<int>, int)`：调用 `RectIou()`，并结合 IoU 约 0.45 阈值、中心距离及尺寸派生阈值判断候选连续性。
- `CalibrationCandidateWindow::Reset(string)`：清空候选稳定/采样窗口、计数和状态，保存 reset reason；不是矩形生成函数。

## 3. YU class 0–6

YU 模型确认为 7 类、256×256、6 输出；当前配置保存了 mask，但没有恢复出 labels：

```text
0 UNKNOWN
1 UNKNOWN
2 UNKNOWN
3 UNKNOWN
4 UNKNOWN
5 UNKNOWN
6 UNKNOWN
```

加密模型边界和现有配置没有提供可验证的类别名称。因此禁止将 YU 5/6 或 YU 3 映射为 TTBOX 类别。

## 4. TTBOX class 0–6

当前 `jwdl_sjzv11` manifest：

```text
0=head
1=body
2=enemy_1
3=enemy_2
4=enemy_3
5=item
6=other
```

TTBOX 当前真实 `class_id=3` 为 `enemy_2`；YU class 3 仍未知，两套类别语义不能视为一致。

## 5. TTBOX 当前真正缺失

```text
RKNN → DecodeNMS → DetectionBox[] → AimTargetTask
     → TargetSelector（单框过滤/排序/track 复用）
     → selected.box → IPC
```

TTBOX 缺少的是可验证的：

```text
DetectionBox[] → TrackedTarget/Candidate Object → CandidateRect
```

当前真实帧只有一个约 65×255、class 3 的框，没有足够同帧信息证明可以可靠生成完整人体矩形。缺失点不是前端 CSS，也不是坐标缩放。

## 6. 已经可以实现 / 仍不能猜

可以实现：

- TTBOX 原始 Detection telemetry
- TTBOX selected target telemetry
- 明确的 TargetPoint 层
- 在获得语义和算法证据后的 TrackedTarget 层

仍不能猜：

- YU class 0–6 名称
- CandidateRect 是否等于 TrackedTarget rect
- YU CandidateRect 的生成公式
- 用 TTBOX 单个局部框推导完整人体框
- 用附近框或固定扩展伪造等价行为

## YU_TARGET_RECONSTRUCTION_STATUS

```text
Detection              CONFIRMED
TrackedTarget          PARTIAL
TargetPoint            CONFIRMED
CandidateRect          PARTIAL
OverlayBox             CONFIRMED
BuildDetectionObject   PARTIAL
BuildTargetPoint       CONFIRMED
BuildLoopoutOverlayBoxes CONFIRMED
IsSameAimTargetCandidate CONFIRMED(partial logic)
CalibrationCandidateWindow PARTIAL
Class Mapping          UNKNOWN
TTBOX Equivalent       NOT READY
CandidateBuilder       NOT READY
```

## 本轮修改

仅新增：

- `docs/architecture/YU_TARGET_OBJECT_MODEL.md`
- `docs/architecture/TTBOX_YU_TARGET_REVERSE_REPORT.md`

未修改前端、CSS、坐标缩放、DecodeNMS、TargetSelector、PID、HID、采集、RGA、RKNN 和生产控制逻辑。
