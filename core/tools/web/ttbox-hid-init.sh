#!/bin/bash
# ttbox-hid-init.sh — HID Package 0.0.1 安装/激活（幂等，供 ttbox-hid.service 调用）
set -e
OPT=/opt/ttbox
PKG=$OPT/runtime/ttbox-hid-pkg
HID=$OPT/hid
SRC=/tmp/hid_pkg_src_001

# 已激活则跳过（幂等）
if [ -n "$($PKG --root $HID get-active 2>/dev/null)" ]; then
  exit 0
fi

# 构造干净包源（不能直接 import hid/ 根目录，含 registry 等运行时目录）
rm -rf $SRC
mkdir -p $SRC/config $SRC/descriptors
cp $HID/manifest.json $HID/VERSION $SRC/
cp $HID/config/*.json $SRC/config/ 2>/dev/null || true
cp $HID/descriptors/*.desc $SRC/descriptors/ 2>/dev/null || true

$PKG --root $HID init || true
$PKG --root $HID import $SRC 0.0.1
$PKG --root $HID validate 0.0.1
$PKG --root $HID install 0.0.1
$PKG --root $HID activate 0.0.1
echo "HID active=$($PKG --root $HID get-active)"
