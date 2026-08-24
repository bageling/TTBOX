#!/usr/bin/env bash
# v4l2_timing.sh — V4L2 实际时序验证（EDID 测试辅助，板端运行）
#
# 输出 HDMI RX 实际锁定时序：分辨率/像素格式/DV timings/实际帧率。
# 原则：V4L2 实际时序 = 唯一运行事实；EDID 只声明能力，不代表实际输出。
#
# 用法： bash v4l2_timing.sh [device]   # 默认 /dev/video0
set -u

DEV="${1:-/dev/video0}"

echo "=== V4L2 实际格式 (G_FMT) ==="
v4l2-ctl -d "${DEV}" --get-fmt-video 2>/dev/null || { echo "无法打开 ${DEV}" >&2; exit 1; }

echo ""
echo "=== 实际锁定时序 (DV timings) ==="
v4l2-ctl -d "${DEV}" --query-dv-timings 2>/dev/null | grep -E 'Active|Total|Pixelclock|Frame format' || true

echo ""
echo "=== 实测帧率（2s 采样）==="
# 用 G_FMT 报告 + 驱动 sequence 差分（V4L2 Capture 已采集 sequence/timestamp_ms）
if v4l2-ctl -d "${DEV}" --stream-mmap --stream-count=120 --stream-to=/dev/null 2>/dev/null | grep -o 'fps' >/dev/null; then
    v4l2-ctl -d "${DEV}" --stream-mmap --stream-count=120 --stream-to=/dev/null 2>&1 | grep -E 'fps|frames' | tail -2
else
    echo "（无 stream 测试支持；实际帧率以 TTBox C++ Capture 的 sequence 差分为准）"
fi

echo ""
echo "=== 能力声明 vs 实际 ==="
echo "EDID = 输入能力声明（见 resources/edid/README.md）"
echo "V4L2 G_FMT/DV timings = 当前实际锁定时序（唯一事实）"
echo "禁止把 EDID 声明的 FPS 当作实际 FPS"
