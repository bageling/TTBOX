#!/usr/bin/env bash
# aibox2 首次启动初始化 (aibox-firstboot.service, 一次性)
# 完成必要目录/Python/模型/RKNN/设备检查后写入完成标记, 退出。
set -euo pipefail

APP=/opt/aibox2
VENV_PY="${APP}/venv/bin/python"
MARKER="${APP}/data/.firstboot-done"

# 已完成则直接退出, 不重复初始化
if [[ -f "${MARKER}" ]]; then
  exit 0
fi

log() { echo "[firstboot] $*"; }

log "=== 创建运行目录 ==="
mkdir -p "${APP}/data" "${APP}/logs" "${APP}/runtime"
touch "${APP}/data/.keep" "${APP}/logs/.keep" "${APP}/runtime/.keep"

log "=== 检查 Python 环境 ==="
"${VENV_PY}" --version
"${VENV_PY}" -c "import numpy, evdev" || { log "ERROR: numpy/evdev 不可用" >&2; exit 1; }

log "=== 检查 RKNN Runtime ==="
if "${VENV_PY}" -c "import rknnlite.api" 2>/dev/null; then
  log "rknnlite OK"
else
  log "WARN: rknnlite 不可用 (AI 服务将无法启动)" >&2
fi

log "=== 检查模型 ==="
if [[ -f "${APP}/models/yolo261n-rk3588.rknn" ]]; then
  log "模型 OK: yolo261n-rk3588.rknn"
else
  log "WARN: 模型缺失: ${APP}/models/yolo261n-rk3588.rknn" >&2
fi

log "=== 检查采集设备 ==="
if [[ -e /dev/video0 ]]; then
  log "/dev/video0 存在"
else
  log "WARN: /dev/video0 不存在 (HDMI RX overlay 未启用或无信号)" >&2
fi

log "=== 检查输入设备 ==="
if [[ -d /dev/input ]]; then
  log "/dev/input 存在"
else
  log "WARN: /dev/input 不存在" >&2
fi

log "=== 初始化 HID Gadget ==="
if [[ ! -e /sys/kernel/config/usb_gadget ]]; then
  mount -t configfs none /sys/kernel/config 2>/dev/null || true
fi
modprobe libcomposite 2>/dev/null || true
if "${APP}/runtime/hid-gadget.sh" setup 2>/dev/null; then
  log "HID Gadget OK"
else
  log "WARN: HID Gadget 初始化失败 (passthrough 将等待)" >&2
fi

log "=== 环境自检 (doctor) ==="
"${VENV_PY}" -m aibox doctor || log "WARN: doctor 有告警, 见上方输出" >&2

touch "${MARKER}"
log "首次启动初始化完成"
exit 0
