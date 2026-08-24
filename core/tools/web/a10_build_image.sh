#!/bin/bash
# a10_build_image.sh — 板端构建 TTBox-v0.0.1-orangepi5plus.img
# 布局: [GPT+IDB+U-Boot ITB 0~16.8MB] [p1 CIDATA 4MB] [p2 rootfs 8G]
set -e
IMG=/home/ubuntu/TTBox-v0.0.1-orangepi5plus.img
MNT=/mnt/ttbox-img

echo "== 1. 创建 img（sparse 8.8G）=="
rm -f "$IMG"
truncate -s 9000M "$IMG"

echo "== 2. 复制 boot 区（GPT + IDB + U-Boot ITB + CIDATA p1）=="
dd if=/dev/mmcblk1 of="$IMG" bs=1M count=21 conv=notrunc status=none

echo "== 3. 重建分区表（p2 = 8G rootfs）=="
P2_START=$(sgdisk -i 2 /dev/mmcblk1 2>/dev/null | grep 'First sector' | awk '{print $3}')
echo "  p2 start sector=$P2_START"
sgdisk -d 2 "$IMG" 2>/dev/null
sgdisk -n 2:$P2_START:+8G -t 2:8300 -c 2:rootfs "$IMG"
sgdisk -p "$IMG"

echo "== 4. loop 挂载 + mkfs.ext4 =="
LOOP=$(sudo losetup -f)
sudo losetup -P "$LOOP" "$IMG"
sudo mkfs.ext4 -q -L ttbox-rootfs "${LOOP}p2"
sudo mkdir -p "$MNT"
sudo mount "${LOOP}p2" "$MNT"
echo "  mounted ${LOOP}p2 -> $MNT"
echo "LOOP=$LOOP" | sudo tee /tmp/a10_build_loop >/dev/null
echo "PREP_DONE"
