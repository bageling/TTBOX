#!/bin/bash
# golden_info.sh — 记录 GOLDEN PASS 系统（当前运行系统）boot/HDMI 信息
OUT=/home/ubuntu/golden
mkdir -p $OUT
echo "=== uname -a ==="; uname -a
echo "=== os-release ==="; head -4 /etc/os-release
echo "=== /proc/cmdline ==="; cat /proc/cmdline
echo ""
echo "=== rootfs UUID ==="
findmnt -no UUID / 2>/dev/null
blkid $(findmnt -no SOURCE /) 2>/dev/null
echo "=== fstab ==="; cat /etc/fstab
echo ""
echo "=== /boot ==="; ls -la /boot/
echo "=== /boot 全部文件 ==="; find /boot -maxdepth 3 -type f | sort
echo "=== extlinux.conf ==="; cat /boot/extlinux/extlinux.conf
echo ""
echo "=== DTB/DTBO ==="
find /lib/firmware -iname '*.dtb' -o -iname '*.dtbo' 2>/dev/null | sort
echo "=== hdmirx dtbo 是否存在 ==="
ls -la /lib/firmware/6.1.0-1025-rockchip/device-tree/rockchip/overlay/rk3588-hdmirx.dtbo 2>&1
echo ""
echo "=== /dev/dri / /dev/video ==="
ls -l /dev/dri/ 2>&1
ls -l /dev/video* 2>&1
echo "=== drm status ==="
for x in /sys/class/drm/*/status; do echo "--- $x"; cat "$x" 2>&1; done
echo "=== drm enabled ==="
for x in /sys/class/drm/*/enabled; do echo "--- $x"; cat "$x" 2>&1; done
echo ""
echo "=== dmesg hdmi/drm/vop ==="
dmesg | grep -Ei "hdmi|hdmirx|drm|dw-hdmi|vop|rockchip" | tail -40
echo ""
echo "=== firmware 目录结构 ==="
find /lib/firmware -maxdepth 2 -type d 2>/dev/null | sort
echo "GOLDEN_INFO_DONE"
