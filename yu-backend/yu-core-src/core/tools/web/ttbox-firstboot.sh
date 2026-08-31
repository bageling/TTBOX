#!/bin/bash
# ttbox-firstboot.sh — 首次启动初始化（oneshot，执行一次后写标记）
# 只做初始化，不安装任何软件（软件已内嵌镜像）。
set -e
OPT=/opt/ttbox
FLAG=$OPT/.firstboot-done
[ -f "$FLAG" ] && exit 0

echo "[firstboot] start"

# 1. hostname 初始化
hostnamectl set-hostname ttbox 2>/dev/null || true
echo "ttbox" > /etc/hostname

# 2. SSH host key 重新生成（镜像内已清除，防镜像克隆共用密钥）
if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
  ssh-keygen -A || true
fi

# 3. Model Registry 初始化
mkdir -p "$OPT/models/registry/installed" "$OPT/models/registry/staging" \
         "$OPT/models/registry/cache" "$OPT/models/registry/quarantine"

# 4. HID 初始化（USB HID Gadget）
if [ -x "$OPT/scripts/a9_setup_hid_gadget.sh" ]; then
  bash "$OPT/scripts/a9_setup_hid_gadget.sh" enable 2>/dev/null || true
fi

# 5. EDID 初始化（1080p240）
if [ -x "$OPT/edid/inject_edid.sh" ]; then
  bash "$OPT/edid/inject_edid.sh" 2>/dev/null || true
fi

# 6. 锁频策略（CPU/GPU/NPU performance，保持已验证性能配置）
if [ -x "$OPT/scripts/setup_freq.sh" ]; then
  bash "$OPT/scripts/setup_freq.sh" 2>/dev/null || true
fi

# 7. Runtime 数据目录
mkdir -p "$OPT/runtime" "$OPT/logs"

touch "$FLAG"
echo "[firstboot] done"
