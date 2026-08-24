#!/bin/bash
# a10_package.sh — xz 压缩 + sha256 + 版本收集（第三阶段）
set -e
IMG=/home/ubuntu/TTBox-v0.0.1-orangepi5plus.img
cd /home/ubuntu

echo "== 1. sha256(img) =="
sha256sum "$IMG" > "$IMG.sha256"
cat "$IMG.sha256"

echo "== 2. xz 压缩（-T0 -9）=="
xz -T0 -9 -k "$IMG"
ls -lh "$IMG.xz"

echo "== 3. sha256(xz) =="
sha256sum "$IMG.xz" > "$IMG.xz.sha256"
cat "$IMG.xz.sha256"

echo "== 4. 版本收集 =="
echo "UBUNTU=$(lsb_release -ds 2>/dev/null || grep PRETTY /etc/os-release | cut -d= -f2)"
echo "KERNEL=$(uname -r)"
echo "UBOOT=$(dpkg -s u-boot-orangepi-5-plus 2>/dev/null | grep Version | awk '{print $2}')"
echo "RGA=$(dpkg -s librga2 2>/dev/null | grep Version | awk '{print $2}')"
echo "RKNN=$(strings /usr/lib/librknnrt.so 2>/dev/null | grep -oE '2\.[0-9]+\.[0-9]+[^ ]*' | head -1)"
echo "RKNNLIB=$(sha256sum /usr/lib/librknnrt.so | cut -d' ' -f1)"
echo "COREVER=$(grep -oE 'kVersion[^;]*' /opt/ttbox/runtime/ttbox_core_main 2>/dev/null | head -1 || strings /opt/ttbox/runtime/ttbox_core_main | grep -oE '0\.[0-9]+\.[0-9]+' | head -1)"
echo "HIDVER=$(cat /opt/ttbox/hid/VERSION)"
echo "ITB=$(ls -la /usr/lib/u-boot/u-boot.itb | awk '{print $5\" \"$6\" \"$7\" \"$8}')"
echo "=== 版本信息收集完成 ==="
