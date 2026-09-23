#!/usr/bin/env bash
# 镜像内嵌前置侦察：工具链 + 分区表 + 容量
echo "== WSL 环境 =="
echo "user=$(id -un) uid=$(id -u) arch=$(uname -m) kernel=$(uname -r)"
echo "-- 工具 --"
for t in losetup parted sfdisk fdisk kpartx qemu-aarch64-static qemu-aarch64 mount rsync blkid dumpe2fs e2fsck resize2fs truncate chroot; do
  p="$(command -v "$t" 2>/dev/null || true)"
  printf '%-22s %s\n' "$t" "${p:-MISSING}"
done
echo "-- qemu/binfmt 包 --"
dpkg -l 2>/dev/null | awk '/^ii/ && ($2 ~ /qemu|binfmt/) {print $2, $3}'
echo "-- binfmt_misc 挂载 --"
ls /proc/sys/fs/binfmt_misc/ 2>/dev/null || echo "(无)"
echo
echo "== 镜像 =="
IMG="${1:-/mnt/c/Users/Administrator/Downloads/ubuntu-22.04-preinstalled-server-arm64-orangepi-5-plus.img}"
ls -l "$IMG"
echo "-- file --"
file "$IMG" 2>/dev/null
echo "-- 分区表 (sfdisk -d) --"
sfdisk -d "$IMG" 2>&1 | head -40
echo "-- blkid --"
blkid "$IMG"* 2>/dev/null || true
