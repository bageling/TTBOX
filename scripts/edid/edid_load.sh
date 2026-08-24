#!/usr/bin/env bash
# edid_load.sh — 板端 EDID 测试加载（仅测试/能力声明，不进入正式 AI 链路）
#
# 用途：把 resources/edid/ 下的 EDID bin 写入板端 HDMI RX 并触发重新协商，
#       使信号源可协商到目标时序（如 1080p240）。
#
# 用法：
#   bash edid_load.sh <edid.bin>          # 加载指定 EDID（先校验）
#   bash edid_load.sh list                # 列出 resources/edid 下可用 EDID
#
# 依赖：v4l2-ctl（v4l-utils）；写入机制复用 vendor/legacy/scripts/hdmirx_edid.sh
#       （若板端 EDID 为固件注入，需按板端实际机制扩展）。
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EDID_DIR="${PROJECT_DIR}/resources/edid"
HDMIRX_EDID="${PROJECT_DIR}/vendor/legacy/scripts/hdmirx_edid.sh"

if [ "$1" = "list" ]; then
    echo "可用 EDID（resources/edid/）："
    find "${EDID_DIR}" -name '*.bin' | sort
    exit 0
fi

EDID_BIN="${1:?用法: edid_load.sh <edid.bin> | list}"

if [ ! -f "${EDID_BIN}" ]; then
    echo "EDID 文件不存在: ${EDID_BIN}" >&2
    exit 1
fi

# 1. 校验
if ! python3 "${PROJECT_DIR}/scripts/edid/edid_verify.py" "${EDID_BIN}"; then
    echo "EDID 校验失败，拒绝加载" >&2
    exit 1
fi

# 2. 加载（复用 hdmirx EDID 机制；若脚本不可用则给出指引）
if [ -x "${HDMIRX_EDID}" ]; then
    echo "通过 hdmirx_edid.sh 加载 ${EDID_BIN} ..."
    bash "${HDMIRX_EDID}" load "${EDID_BIN}"
else
    echo "未找到 ${HDMIRX_EDID}" >&2
    echo "板端 EDID 注入机制需按硬件实际实现（如 DRM edid_firmware / 驱动注入）。" >&2
    echo "加载后运行 v4l2_timing.sh 验证实际锁定时序。" >&2
    exit 1
fi
