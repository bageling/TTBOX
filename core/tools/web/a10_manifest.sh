#!/bin/bash
# a10_manifest.sh — 生成 image-manifest.json（板端执行，需 xz 完成后）
set -e
IMG=/home/ubuntu/TTBox-v0.0.1-orangepi5plus.img
H=/home/ubuntu
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
IMG_SHA=$(cut -d' ' -f1 "$IMG.sha256")
XZ_SHA=$(cut -d' ' -f1 "$IMG.xz.sha256")
UBOOT=$(dpkg -s u-boot-orangepi-5-plus 2>/dev/null | grep Version | awk '{print $2}')
RGA=$(dpkg -s librga2 2>/dev/null | grep Version | awk '{print $2}')
RKNN=$(strings /usr/lib/librknnrt.so 2>/dev/null | grep -oE '[0-9]\.[0-9]+\.[0-9]+' | sort -u | head -1)
COREVER=$(strings /opt/ttbox/runtime/ttbox_core_main | grep -oE 'ttbox_core v[0-9.]+' | head -1 | sed 's/ttbox_core v//')
HIDVER=$(cat /opt/ttbox/hid/VERSION)
MODEL_SHA=$(sha256sum /opt/ttbox/models/registry/installed/huangwa.rknn | cut -d' ' -f1)

python3 - "$TS" "$IMG_SHA" "$XZ_SHA" "$UBOOT" "$RGA" "$RKNN" "$COREVER" "$HIDVER" "$MODEL_SHA" <<'EOF'
import json, sys
ts, img_sha, xz_sha, uboot, rga, rknn, core, hid, model = sys.argv[1:]
m = {
    "image": "TTBox-v0.0.1-orangepi5plus.img",
    "ttbox_version": "0.0.1",
    "ubuntu_version": "Ubuntu 24.04.1 LTS (Noble Numbat)",
    "kernel_version": "6.1.0-1025-rockchip",
    "u_boot_version": uboot,
    "bl31_version": "bundled in u-boot.itb (2017.09+20240806.gitf73b1eed)",
    "rknn_runtime_version": rknn or "unknown",
    "rga_version": rga,
    "model_adapter_version": core or "0.1.0",
    "hid_version": hid,
    "web_version": "0.0.1",
    "build_timestamp": ts,
    "sha256": img_sha,
    "sha256_xz": xz_sha,
    "preset_model": {"file": "huangwa.rknn", "sha256": model},
    "layout": {
        "partition1": "primary vfat 4MiB @ 16MiB",
        "partition2": "rootfs ext4 8GiB @ 20MiB (start sector 40960)",
        "bootloader": "u-boot.itb @ 8MiB (RK3588)"
    }
}
p = "/home/ubuntu/TTBox-v0.0.1-orangepi5plus-manifest.json"
json.dump(m, open(p, "w"), indent=2, ensure_ascii=False)
print("manifest written:", p)
print(json.dumps(m, indent=2, ensure_ascii=False))
EOF
