#!/bin/bash
# a10_build_rootfs.sh — rsync 当前 rootfs → 镜像 + 镜像内清理（第二阶段）
set -e
MNT=/mnt/ttbox-img

echo "== 5. rsync rootfs → 镜像 =="
sudo rsync -aHAX --numeric-ids \
  --exclude '/proc' --exclude '/sys' --exclude '/dev' --exclude '/run' \
  --exclude '/tmp' --exclude '/mnt' --exclude '/media' --exclude '/lost+found' \
  --exclude '/home/ubuntu/ttbox2' --exclude '/home/ubuntu/TTBox-v0.0.1-orangepi5plus.img' \
  --exclude '/var/lib/apt/lists' --exclude '/var/cache/apt' \
  --exclude '/var/lib/cloud/instance*' --exclude '/var/lib/cloud/instances' \
  / "$MNT/" 2>&1 | tail -3
echo "  rsync done"

echo "== 6. 镜像内清理（唯一性 / 敏感信息 / 运行残留）=="
sudo rm -f "$MNT"/etc/ssh/ssh_host_* "$MNT"/etc/ssh/ssh_host_*.pub
sudo rm -f "$MNT"/etc/machine-id "$MNT"/var/lib/dbus/machine-id
sudo rm -f "$MNT"/opt/ttbox/.firstboot-done
sudo rm -f "$MNT"/opt/ttbox/runtime/ttbox.sock "$MNT"/opt/ttbox/runtime/infer.log 2>/dev/null || true
sudo rm -rf "$MNT"/var/lib/apt/lists "$MNT"/var/cache/apt "$MNT"/var/cache/snapd 2>/dev/null || true
sudo rm -rf "$MNT"/home/ubuntu/.cache "$MNT"/home/ubuntu/.local/share 2>/dev/null || true
sudo rm -f "$MNT"/root/.bash_history "$MNT"/home/ubuntu/.bash_history 2>/dev/null || true
sudo rm -rf "$MNT"/tmp/* "$MNT"/var/tmp/* 2>/dev/null || true
sudo find "$MNT"/var/log -type f -exec truncate -s 0 {} \; 2>/dev/null || true
sudo rm -rf "$MNT"/var/backups/* 2>/dev/null || true

echo "== 7. 设置默认账号（ubuntu/ttbox）+ hostname =="
sudo mkdir -p "$MNT/proc" "$MNT/sys" "$MNT/dev"
sudo mount --bind /proc "$MNT/proc"
sudo mount --bind /sys "$MNT/sys"
sudo mount --bind /dev "$MNT/dev"
sudo chroot "$MNT" /bin/bash -c 'echo "ubuntu:ttbox" | chpasswd; echo "ttbox" > /etc/hostname; mkdir -p /opt/ttbox/models/registry/{installed,staging,cache,quarantine} /opt/ttbox/{runtime,logs}' 2>&1 | tail -2
sudo umount "$MNT/proc" "$MNT/sys" "$MNT/dev"

echo "== 8. 卸载 =="
LOOP=$(cat /tmp/a10_build_loop)
sudo umount "$MNT"
sudo losetup -d "$LOOP"
echo "  unmounted"

echo "== 9. 校验 =="
sudo sgdisk -p /home/ubuntu/TTBox-v0.0.1-orangepi5plus.img 2>/dev/null | tail -6
ls -lh /home/ubuntu/TTBox-v0.0.1-orangepi5plus.img
echo "BUILD_ROOTFS_DONE"
