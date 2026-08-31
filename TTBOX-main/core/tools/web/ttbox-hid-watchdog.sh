#!/bin/bash
# ttbox-hid-watchdog.sh — 监测 hidraw 集合变化，变化则重启 forwarder
# 原因：罗技 Receiver 在 USB 重枚举时 hidraw 编号漂移，forwarder 持有 (deleted) fd
# 逻辑：每 5s 计算 /dev/hidraw* 签名；与上次不同 → 重启 ttbox-hid-forward
set -u

sig_file=/run/ttbox-hid-sig
interval=${1:-5}

sig() {
  # 签名 = 每个 hidraw 的编号 + 其 HID_PHYS 接口映射。
  # 仅比较 /dev/hidraw* 名称集合不够：USB 重枚举后编号集合可能不变，
  # 但「接口(input0/input1) ↔ hidraw 编号」映射会漂移 → 键盘/鼠标读错设备。
  for h in /sys/class/hidraw/hidraw*; do
    n=$(basename "$h")
    phys=$(grep -o 'HID_PHYS=.*' "$h/device/uevent" 2>/dev/null)
    echo "$n:$phys"
  done | md5sum | cut -d' ' -f1
}

prev=$(sig)
while true; do
  sleep "$interval"
  cur=$(sig)
  if [ "$cur" != "$prev" ]; then
    logger -t ttbox-hid-watchdog "hidraw 集合变化: $prev -> $cur, 重启 forwarder"
    systemctl restart ttbox-hid-forward
    prev=$(sig)
  fi
done
