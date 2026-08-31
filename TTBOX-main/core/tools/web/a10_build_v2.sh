#!/bin/bash
# a10_build_v2.sh — 修复版镜像构建（ROOT CAUSE: root UUID 不匹配）
# 核心修复：mkfs 新 rootfs 后，将 /boot/extlinux/extlinux.conf 与 /etc/fstab 中的
# root=UUID 更新为【新 rootfs 的实际 UUID】（原脚本漏掉此步导致系统无法启动）
#
# 原则：GOLDEN Boot/Kernel/DT/DTBO/Firmware 原样复制（dd 前 21MB + rsync），
#       只修正 RootFS 内部配置，不替换任何 Rockchip 固件。
set -e
IMG=/home/ubuntu/TTBox-v0.0.1-orangepi5plus.img
MNT=/mnt/ttbox-img

echo "== 0. 前置检查（GOLDEN 板状态）=="
for t in sgdisk losetup mkfs.ext4 rsync blkid e2fsck; do
  command -v $t >/dev/null || { echo "BUILD FAIL: 缺少 $t"; exit 1; }
done
[ -d /opt/ttbox/runtime ] && [ -f /opt/ttbox/models/registry/installed/huangwa.rknn ] \
  || { echo "BUILD FAIL: /opt/ttbox 未部署或默认模型缺失（先执行 a10_deploy.sh）"; exit 1; }
for s in ttbox-runtime ttbox-web ttbox-hid; do
  [ -f /etc/systemd/system/$s.service ] || { echo "BUILD FAIL: 缺 $s.service"; exit 1; }
done
FREE=$(df -P /home/ubuntu | awk 'NR==2{print $4}')
echo "  /home/ubuntu 可用空间: $((FREE/1024/1024)) GB"
[ $((FREE/1024/1024)) -ge 10 ] || { echo "BUILD FAIL: 磁盘空间不足（需 >=10GB）"; exit 1; }
[ -b /dev/mmcblk1 ] || { echo "BUILD FAIL: /dev/mmcblk1 不存在"; exit 1; }
echo "  前置检查 PASS"

echo "== 1. 创建 img（sparse 8.8G）+ 复制 GOLDEN boot 区 =="
rm -f "$IMG"
truncate -s 9000M "$IMG"
dd if=/dev/mmcblk1 of="$IMG" bs=1M count=21 conv=notrunc status=none

echo "== 2. 重建分区表（p2 = 8G rootfs，保留原 p2 属性）=="
P2_START=$(sgdisk -i 2 /dev/mmcblk1 2>/dev/null | grep 'First sector' | awk '{print $3}')
echo "  p2 start sector=$P2_START"
sgdisk -d 2 "$IMG" 2>/dev/null
sgdisk -n 2:$P2_START:+8G -t 2:8300 -c 2:rootfs "$IMG"
# 恢复 GOLDEN p2 的分区属性（原表为 boot,esp）：legacy boot bit(0x02) + ESP bit(0x01)
sgdisk -A 2:set:2 "$IMG" 2>/dev/null || true
sgdisk -A 2:set:1 "$IMG" 2>/dev/null || true

echo "== 3. loop 挂载 + mkfs.ext4 =="
LOOP=$(losetup -f)
losetup -P "$LOOP" "$IMG"
mkfs.ext4 -q -L ttbox-rootfs "${LOOP}p2"
mkdir -p "$MNT"
mount "${LOOP}p2" "$MNT"

echo "== 4. rsync GOLDEN rootfs → 镜像（保留 Kernel/DTB/DTBO/Firmware）=="
rsync -aHAX --numeric-ids \
  --exclude '/proc' --exclude '/sys' --exclude '/dev' --exclude '/run' \
  --exclude '/tmp' --exclude '/mnt' --exclude '/media' --exclude '/lost+found' \
  --exclude '/snap' \
  --exclude '/home/ubuntu/ttbox2' \
  --exclude '/home/ubuntu/TTBox-*.img' --exclude '/home/ubuntu/TTBox-*.img.*' \
  --exclude '/home/ubuntu/*.tar.gz' --exclude '/home/ubuntu/golden' --exclude '/home/ubuntu/.*' \
  --exclude '/var/lib/apt/lists' --exclude '/var/cache/apt' \
  --exclude '/var/lib/cloud/instance*' --exclude '/var/lib/cloud/instances' \
  / "$MNT/"

echo "== 5. ★ 关键修复：更新 root UUID（extlinux.conf + fstab）=="
NEW_UUID=$(blkid -s UUID -o value "${LOOP}p2")
echo "  新 rootfs UUID = $NEW_UUID"
# 更新 extlinux.conf 中所有 root=UUID=...
sed -i "s/root=UUID=[0-9a-f-]*/root=UUID=$NEW_UUID/g" "$MNT/boot/extlinux/extlinux.conf"
# 更新 fstab 中根分区 UUID
sed -i "s/UUID=[0-9a-f-]*/$NEW_UUID/g" "$MNT/etc/fstab"
echo "  extlinux.conf root 行:"; grep -E '^\s+append' "$MNT/boot/extlinux/extlinux.conf"
echo "  fstab:"; grep -v '^#' "$MNT/etc/fstab" | grep -v '^$'

echo "== 5.1 ★ 强制一致性检查（不一致 BUILD FAIL）=="
FSTAB_UUID=$(awk '$2=="/"{print $1}' "$MNT/etc/fstab" | sed 's/^UUID=//')
EXT_COUNT=$(grep -cE 'root=UUID=' "$MNT/boot/extlinux/extlinux.conf")
EXT_SET=$(grep -oE 'root=UUID=[0-9a-f-]+' "$MNT/boot/extlinux/extlinux.conf" | sed 's/root=UUID=//' | sort -u | tr '\n' ' ')
echo "  actual=$NEW_UUID fstab=$FSTAB_UUID extlinux=[$EXT_SET] count=$EXT_COUNT"
[ -z "$FSTAB_UUID" ] && { echo "BUILD FAIL: fstab 无根 UUID"; exit 1; }
[ "$NEW_UUID" != "$FSTAB_UUID" ] && { echo "BUILD FAIL: actual != fstab"; exit 1; }
[ "$EXT_COUNT" -lt 1 ] && { echo "BUILD FAIL: extlinux 无 root=UUID"; exit 1; }
for u in $EXT_SET; do
  [ "$u" != "$NEW_UUID" ] && { echo "BUILD FAIL: extlinux 存在不一致 UUID=$u"; exit 1; }
done
echo "  ★ UUID 三方一致 PASS: $NEW_UUID == fstab == extlinux"

echo "== 5.2 固化 /opt/ttbox 目录结构与诊断工具 =="
mkdir -p "$MNT/opt/ttbox/bin" "$MNT/opt/ttbox/lib" "$MNT/opt/ttbox/data"
mkdir -p "$MNT/opt/ttbox/models/registry/installed" "$MNT/opt/ttbox/models/registry/staging" \
         "$MNT/opt/ttbox/models/registry/cache" "$MNT/opt/ttbox/models/registry/quarantine"
if [ -f /opt/ttbox/scripts/ttbox-diagnostic.sh ]; then
  cp /opt/ttbox/scripts/ttbox-diagnostic.sh "$MNT/opt/ttbox/scripts/" && chmod +x "$MNT/opt/ttbox/scripts/ttbox-diagnostic.sh"
fi
if [ -f /opt/ttbox/scripts/a10_coldboot_accept.sh ]; then
  cp /opt/ttbox/scripts/a10_coldboot_accept.sh "$MNT/opt/ttbox/scripts/" && chmod +x "$MNT/opt/ttbox/scripts/a10_coldboot_accept.sh"
fi
echo "  /opt/ttbox 结构:"; ls "$MNT/opt/ttbox/"

echo "== 6. 校验 boot 链完整（Kernel/DTB/DTBO）=="
ls -la "$MNT/boot/vmlinuz-6.1.0-1025-rockchip" "$MNT/boot/initrd.img-6.1.0-1025-rockchip"
ls -la "$MNT/usr/lib/firmware/6.1.0-1025-rockchip/device-tree/rockchip/rk3588-orangepi-5-plus.dtb"
ls -la "$MNT/usr/lib/firmware/6.1.0-1025-rockchip/device-tree/rockchip/overlay/rk3588-hdmirx.dtbo"
sha256sum "$MNT/boot/vmlinuz-6.1.0-1025-rockchip" "$MNT/usr/lib/firmware/6.1.0-1025-rockchip/device-tree/rockchip/rk3588-orangepi-5-plus.dtb" "$MNT/usr/lib/firmware/6.1.0-1025-rockchip/device-tree/rockchip/overlay/rk3588-hdmirx.dtbo"

echo "== 7. 镜像内清理（唯一性 / 敏感信息）=="
rm -f "$MNT"/etc/ssh/ssh_host_* "$MNT"/etc/machine-id "$MNT"/var/lib/dbus/machine-id
rm -f "$MNT"/opt/ttbox/.firstboot-done "$MNT"/opt/ttbox/runtime/ttbox.sock "$MNT"/opt/ttbox/runtime/infer.log
rm -rf "$MNT"/var/lib/apt/lists "$MNT"/var/cache/apt "$MNT"/var/cache/snapd
rm -rf "$MNT"/home/ubuntu/.cache "$MNT"/tmp/* "$MNT"/var/tmp/*
rm -f "$MNT"/root/.bash_history "$MNT"/home/ubuntu/.bash_history
find "$MNT"/var/log -type f -exec truncate -s 0 {} \; 2>/dev/null || true

echo "== 8. 默认账号（ubuntu/ttbox）+ hostname =="
mount --bind /proc "$MNT/proc" 2>/dev/null || true
mount --bind /sys "$MNT/sys" 2>/dev/null || true
mount --bind /dev "$MNT/dev" 2>/dev/null || true
chroot "$MNT" /bin/bash -c 'echo "ubuntu:071500" | chpasswd; echo "ttbox" > /etc/hostname' 2>&1 | tail -1
umount "$MNT/proc" "$MNT/sys" "$MNT/dev" 2>/dev/null || true

echo "== 9. 卸载 + fsck 一致性检查 =="
umount "$MNT"
echo "  e2fsck -f -n ${LOOP}p2 ..."
e2fsck -f -n "${LOOP}p2" >/tmp/ttbox_e2fsck.log 2>&1 || { echo "BUILD FAIL: e2fsck 发现错误"; tail -20 /tmp/ttbox_e2fsck.log; losetup -d "$LOOP"; exit 1; }
echo "  e2fsck PASS: $(tail -1 /tmp/ttbox_e2fsck.log)"
losetup -d "$LOOP"
echo "== 10. 分区表 =="
sgdisk -p "$IMG" 2>/dev/null | tail -5
ls -lh "$IMG"

echo "== 11. 镜像内容验证（挂载检查）=="
bash /home/ubuntu/ttbox2/ttbox/core/tools/web/a10_verify_image.sh

echo "== 12. 发布打包（xz + sha256）=="
bash /home/ubuntu/ttbox2/ttbox/core/tools/web/a10_package.sh

echo "== 13. Manifest 生成（依赖 sha256）=="
bash /home/ubuntu/ttbox2/ttbox/core/tools/web/a10_manifest.sh

echo "== 14. release.zip =="
bash /home/ubuntu/ttbox2/ttbox/core/tools/web/a10_release.sh

echo "BUILD_V2_DONE"
