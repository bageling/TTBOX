# RK3588 Worker Benchmark

日期：2026-08-25。固定条件：同一 `/opt/ttbox/models/current/model.rknn`（SHA256 `2b178f3dc5013a101242e988672be9199ceb492b7eaba4c6271231a00cb770aa`，7,445,558 bytes）、同一 `/dev/video0` 2560x1440 BGR3、同一 DMA-BUF/RGA/Decode/NMS、同一 35 秒、AI HID 关闭。

## 结果

| Workers | Capture FPS | Processed FPS | RKNN run ms | RGA/convert ms | Decode ms | E2E ms | Core0 | Core1 | Core2 | CPU | Temp | Errors |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 1 | 144.2 | 18.1 | 33.812 | 1.509 | 3.190 | 58.834 | 56.7% | 0.0% | 0.0% | 8.0% | N/A | 0 |
| 2 | 144.3 | 32.4 | 37.105 | 1.520 | 3.441 | 62.213 | 74.6% | 37.5% | 0.0% | 14.5% | N/A | 0 |
| 3 | 144.4 | 46.5 | 39.800 | 1.544 | 3.935 | 65.769 | 84.9% | 71.5% | 17.5% | 18.2% | N/A | 0 |

> `RKNN run ms` 使用 Worker 明细 `run avg`；`RGA ms` 使用 `convert avg`（RGA 输出转换阶段）；`Temp=N/A`：本次板端 `/sys/class/thermal` 未暴露可读 temp 节点，未伪造温度。

## Scaling

- 2 Worker scaling = `32.4 / 18.1 = 1.790x`
- 3 Worker scaling = `46.5 / 18.1 = 2.569x`

## Core 使用

- 1 Worker：core mask `0`；Core0 `56.7%`，Core1/Core2 `0.0%`。
- 2 Worker：worker masks `0,1`；Core0 `74.6%`，Core1 `37.5%`，Core2 `0.0%`。
- 3 Worker：worker masks `0,1,2`；Core0 `84.9%`，Core1 `71.5%`，Core2 `17.5%`。

## 统计

- 1 Worker：captured `5047`，processed `632`，dropped `4415`，errors `0`，skipped `0`。
- 2 Worker：captured `5050`，processed `1135`，dropped `3915`，errors `0`，skipped `475`。
- 3 Worker：captured `5057`，processed `1628`，dropped `3429`，errors `0`，skipped `510`。

## 结论

3 Worker 是当前吞吐最优：`46.5 FPS`，相对 1 Worker 提升 `2.569x`；代价是 E2E 从 `58.83 ms` 增至 `65.77 ms`，且每 Worker 吞吐下降。NPU Core 使用随 Worker 数增加而扩展，未修改 WorkerPool 或算法。
