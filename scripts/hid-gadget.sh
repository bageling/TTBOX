#!/usr/bin/env bash
# USB HID Gadget: 键盘 + 鼠标 (configfs), 通过 Type-C 口作为 HID 设备呈现
# 键盘 report 8 字节 (modifier+reserved+6 键), 鼠标 report 4 字节 (buttons+dx+dy+wheel)
# 与 ttbox/output/passthrough.py 的 HID report 格式一致。
set -euo pipefail

GADGET=/sys/kernel/config/usb_gadget/ttbox2
GADGET_NAME="ttbox2-hid"

log() { echo "[gadget] $*"; }

init_configfs() {
  if [[ ! -e /sys/kernel/config ]]; then
    log "configfs 未挂载"
    return 1
  fi
  modprobe libcomposite 2>/dev/null || true
}

setup() {
  init_configfs || return 1
  if [[ -e "${GADGET}/UDC" ]]; then
    log "已初始化, 跳过"
    return 0
  fi
  mkdir -p "${GADGET}"
  cd "${GADGET}"
  echo 0x1d6b > idVendor        # Linux Foundation
  echo 0x0104 > idProduct       # Multifunction Composite Gadget
  echo 0x0100 > bcdDevice
  echo 0x0200 > bcdUSB

  mkdir -p strings/0x409
  echo "ttbox2"             > strings/0x409/manufacturer
  echo "${GADGET_NAME}"     > strings/0x409/product
  echo "0123456789AB"       > strings/0x409/serialnumber

  mkdir -p configs/c.1/strings/0x409
  echo "Config 1"           > configs/c.1/strings/0x409/configuration
  echo 250                  > configs/c.1/MaxPower

  # 键盘 (HID 1, report 8 字节)
  mkdir -p functions/hid.usb0
  echo 1 > functions/hid.usb0/protocol
  echo 1 > functions/hid.usb0/subclass
  echo 8 > functions/hid.usb0/report_length
  echo -ne '\x05\x01\x09\x06\xa1\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0' > functions/hid.usb0/report_desc
  ln -s functions/hid.usb0 configs/c.1/

  # 鼠标 (HID 2, report 4 字节: buttons + x + y + wheel)
  mkdir -p functions/hid.usb1
  echo 2 > functions/hid.usb1/protocol
  echo 1 > functions/hid.usb1/subclass
  echo 4 > functions/hid.usb1/report_length
  echo -ne '\x05\x01\x09\x02\xa1\x01\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x03\x15\x00\x25\x01\x95\x03\x75\x01\x81\x02\x95\x01\x75\x05\x81\x03\x05\x01\x09\x30\x09\x31\x09\x38\x15\x81\x25\x7f\x75\x08\x95\x03\x81\x06\xc0\xc0' > functions/hid.usb1/report_desc
  ln -s functions/hid.usb1 configs/c.1/

  local udc
  udc="$(ls /sys/class/udc 2>/dev/null | head -n1 || true)"
  if [[ -z "${udc}" ]]; then
    log "WARN: 无可用 UDC (Type-C gadget 口未就绪), 稍后重试" >&2
    return 1
  fi
  echo "${udc}" > UDC
  log "HID gadget 已绑定: ${udc}"
  return 0
}

teardown() {
  [[ -e "${GADGET}" ]] || return 0
  echo "" > "${GADGET}/UDC" 2>/dev/null || true
  rm -f "${GADGET}/configs/c.1/hid.usb0" "${GADGET}/configs/c.1/hid.usb1" 2>/dev/null || true
  rmdir "${GADGET}/functions/hid.usb0" "${GADGET}/functions/hid.usb1" 2>/dev/null || true
  rmdir "${GADGET}/configs/c.1/strings/0x409" "${GADGET}/configs/c.1" 2>/dev/null || true
  rmdir "${GADGET}/strings/0x409" "${GADGET}" 2>/dev/null || true
  log "HID gadget 已移除"
}

status() {
  if [[ -e /dev/hidg0 && -e /dev/hidg1 ]]; then
    echo "HID gadget: OK (/dev/hidg0 /dev/hidg1)"
    return 0
  fi
  echo "HID gadget: 未就绪 (需要 setup)"
  return 1
}

case "${1:-setup}" in
  setup)    setup ;;
  teardown) teardown ;;
  status)   status ;;
  *) echo "用法: $0 {setup|teardown|status}" >&2; exit 1 ;;
esac
