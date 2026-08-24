#!/bin/bash
# a10_release.sh — 生成 TTBox v0.0.1 发布包（需求 15）
# 在板端构建/验证/manifest 之后执行：
#   a10_build_v2.sh → a10_verify_image.sh → a10_manifest.sh → a10_release.sh
set -e
H=/home/ubuntu
IMG=$H/TTBox-v0.0.1-orangepi5plus.img
REL=$H/TTBox-v0.0.1-release.zip

echo "== 1. 校验 IMG + sha256 + manifest 齐备 =="
[ -f "$IMG" ] || { echo "RELEASE FAIL: 缺 $IMG"; exit 1; }
[ -f "$IMG.sha256" ] || { echo "RELEASE FAIL: 缺 sha256"; exit 1; }
[ -f "$H/TTBox-v0.0.1-orangepi5plus-manifest.json" ] || { echo "RELEASE FAIL: 缺 manifest"; exit 1; }

echo "== 2. 校验 sha256 匹配 =="
(cd "$H" && sha256sum -c "$IMG.sha256") || { echo "RELEASE FAIL: IMG 与 sha256 不匹配"; exit 1; }

echo "== 3. 二次校验 UUID 三方一致（独立于构建脚本）=="
L=$(sudo losetup -f)
sudo losetup -P "$L" "$IMG"
M=/mnt/ttbox-rel
mkdir -p "$M"
sudo mount "${L}p2" "$M"
ACTUAL=$(sudo blkid -s UUID -o value "${L}p2")
FSTAB=$(sudo awk '$2=="/"{print $1}' "$M/etc/fstab" | sed 's/^UUID=//')
EXT=$(sudo grep -oE 'root=UUID=[0-9a-f-]+' "$M/boot/extlinux/extlinux.conf" | sed 's/root=UUID=//' | sort -u | tr '\n' ' ')
sudo umount "$M"
sudo losetup -d "$L"
echo "  actual=$ACTUAL fstab=$FSTAB extlinux=[$EXT]"
[ "$ACTUAL" = "$FSTAB" ] || { echo "RELEASE FAIL: actual != fstab"; exit 1; }
for u in $EXT; do [ "$u" = "$ACTUAL" ] || { echo "RELEASE FAIL: extlinux 不一致 $u"; exit 1; }; done
echo "  ★ 发布前 UUID 校验 PASS"

echo "== 4. 打包 release.zip =="
rm -f "$REL"
cd "$H"
python3 - "$REL" <<'EOF'
import json, zipfile, sys
rel = sys.argv[1]
with zipfile.ZipFile(rel, "w", zipfile.ZIP_DEFLATED) as z:
    z.write("TTBox-v0.0.1-orangepi5plus.img", "TTBox-v0.0.1-orangepi5plus.img")
    z.write("TTBox-v0.0.1-orangepi5plus.img.sha256", "TTBox-v0.0.1-orangepi5plus.img.sha256")
    z.write("TTBox-v0.0.1-orangepi5plus-manifest.json", "TTBox-v0.0.1-orangepi5plus-manifest.json")
print("release zip:", rel)
EOF

echo "== 5. 发布包清单 =="
ls -lh "$REL" "$IMG" "$IMG.sha256" "$H/TTBox-v0.0.1-orangepi5plus-manifest.json"
echo "RELEASE_DONE"
