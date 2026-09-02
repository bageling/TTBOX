# YU / TTBOX Target Object 对照

## 总链路

```text
YU:
Detection → native tracker → TrackedTarget → TargetPoint / Candidate → OverlayBox → Web

TTBOX 当前:
DetectionBox → AimTargetTask → TargetSelector TrackEntry → selected.box → IPC → Web
```

## 对照表

| 层 | YU | TTBOX | 差异 | 证据 | 状态 |
|---|---|---|---|---|---|
| Detection | native detector 输出 Detection，随后同时产生 TrackedTarget | `DecodeNMS` 输出 `DetectionBox[]` | TTBOX 只有统一原始框对象 | YU daemon 符号；TTBOX `DecodeNMS.cpp` | PARTIAL |
| TrackedTarget | `CopyCoreDetectTrackVectors()` 从 `AiCoreTrack` 转换，32 字节对象，含矩形和跟踪字段 | `TargetSelector::TrackEntry` 保存 id、box、center、last_seen、lost_frames、active | TTBOX 有轻量 track，但没有独立目标对象语义 | YU `0x101e10`；TTBOX `TargetSelector.hpp:63` | PARTIAL |
| TargetPoint | `BuildTargetPoint(TrackedTarget, AimProfile)`，矩形起点 + offset×矩形尺寸，支持 class offset | `aim_point_at(selected.box, class_id, AimPointProfile)` | 数学职责基本等价，输入对象不同 | YU `0x133220`；TTBOX `AimThread.cpp:109` | PARTIAL |
| Candidate | native AimLoop/CalibrationCandidateWindow 维护候选连续性 | 没有独立 Candidate；selector 直接操作检测框 | TTBOX 候选和 track 未分层 | YU `IsSameAimTargetCandidate`；TTBOX selector | MISSING |
| CandidateRect | YU API 状态字段，native 生成公式未完整恢复 | 没有正式字段；历史代码仅有临时显示框 | TTBOX 未具备 YU 等价 candidate rect | YU native/API 证据；TTBOX IPC | MISSING |
| OverlayBox | `BuildLoopoutOverlayBoxes()` 对每个 TrackedTarget 框缩放、裁剪后输出 | Web 根据 `target_box` 做显示坐标换算 | TTBOX Web 仍消费单框，且不能替代 CandidateRect | YU `0x166330`；TTBOX `app.js` | PARTIAL |
| Selection | `BuildDetectionObject` → AimSelector，配合 track/candidate continuity | `TargetSelector::select`：track lock→rect lock→score | 行为目标相近，算法和输入对象不同 | YU native symbols；TTBOX selector | PARTIAL |
| Mouse | YU native aim loop 输出控制量 | TTBOX AimThread→OutputAction→输出后端 | TTBOX 代码链存在，板端最终消费者需专项确认 | 当前源码与真机状态 | VERIFY |
| HID | YU 使用 native 输出链 | TTBOX 当前状态为 `local_hid`，板端另有官方代理进程 | 实际物理输出路径未完成闭环证明 | 真机 `/api/state`、进程状态 | BROKEN |

## 类别语义

YU：

```text
0–6 = UNKNOWN
```

原因：YU 模型为加密模型，板端 `model-list.json` 只有 `class_count=7`，没有 labels。

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

不能将两边编号直接映射。

## 当前可实现边界

可以继续实现：

```text
TTBOX DetectionBox
→ 独立 TrackedTarget 数据对象
→ TargetPointBuilder
→ Candidate/continuity 层
```

但只有在以下信息补齐后，才可以定义 CandidateRect：

- YU native CandidateRect 生成公式；或
- YU 黑盒实验得到稳定的输入输出规律；或
- TTBOX 模型重新确认具备语义等价的目标框输出。

当前真实帧只有一个 `class=3`、约 `65×250` 的框，不能从单框可靠推导完整人体区域。
