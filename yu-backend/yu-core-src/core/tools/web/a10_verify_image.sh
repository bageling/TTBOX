#!/bin/bash
# a10_verify_image.sh — 挂载镜像 p2 验证内容完整性
set -e
IMG=/home/ubuntu/TTBox-v0.0.1-orangepi5plus.img
MNT=/mnt/ttbox-img
LOOP=$(sudo losetup -f)
sudo losetup -P "$LOOP" "$IMG"
sudo mount "${LOOP}p2" "$MNT"
echo "=== 1. /opt/ttbox 结构 ==="
sudo ls "$MNT/opt/ttbox/"
echo "=== 2. 模型 ==="
sudo ls -la "$MNT/opt/ttbox/models/registry/installed/"
sudo cat "$MNT/opt/ttbox/models/active_model.txt"
echo "=== 3. HID Package ==="
sudo cat "$MNT/opt/ttbox/hid/VERSION" 2>/dev/null
sudo cat "$MNT/opt/ttbox/hid/registry/active.json" 2>/dev/null
echo "=== 4. systemd 服务 ==="
sudo ls /etc/systemd/system/ 2>/dev/null | grep ttbox || sudo ls "$MNT/etc/systemd/system/" | grep ttbox
echo "enabled:"
sudo ls "$MNT/etc/systemd/system/multi-user.target.wants/" | grep ttbox
echo "=== 5. 清理验证 ==="
echo "ssh hostkey: $(sudo ls $MNT/etc/ssh/ | grep ssh_host_ | wc -l) (应为 0)"
echo "machine-id: $(sudo ls $MNT/etc/machine-id 2>&1)"
echo "firstboot-done: $(sudo ls $MNT/opt/ttbox/.firstboot-done 2>&1)"
echo "ttbox2 dev dir: $(sudo ls $MNT/home/ubuntu/ttbox2 2>&1 | head -1)"
echo "=== 6. 默认账号 shadow ==="
sudo grep '^ubuntu:' "$MNT/etc/shadow" | cut -c1-40
echo "=== 7. 关键二进制 ==="
sudo ls "$MNT/opt/ttbox/runtime/" | head
echo "=== 8. web ==="
sudo ls "$MNT/opt/ttbox/web/"
echo "=== 9. rootfs 使用 ==="
sudo df -h "$MNT" | tail -1
sudo umount "$MNT"
sudo losetup -d "$LOOP"
echo "VERIFY_DONE"
