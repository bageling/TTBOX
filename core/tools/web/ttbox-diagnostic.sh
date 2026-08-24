#!/bin/bash
# ttbox-diagnostic.sh — TTBox 成品系统诊断（需求 13）
# 检查: Boot Kernel DTB HDMI RGA NPU RKNN CPU GPU DDR EDID HID Model Web
# 输出: 每项 PASS/FAIL + 汇总；任意 FAIL 时退出码非 0
# 用法: sudo bash ttbox-diagnostic.sh
set -u
OPT=/opt/ttbox
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  [PASS] $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  [FAIL] $1"; }

echo "=== TTBox Diagnostic $(date -u +%FT%TZ) ==="
echo "== 设备: $(cat /proc/device-tree/model 2>/dev/null || echo unknown) =="

echo "== 1. Boot =="
ACTUAL=$(findmnt -no UUID / 2>/dev/null)
CMDLINE=$(cat /proc/cmdline)
ROOTUUID=$(echo "$CMDLINE" | grep -oE 'root=UUID=[0-9a-f-]+' | cut -d= -f3)
FSTABUUID=$(grep -vE '^\s*#|^\s*$' /etc/fstab | awk '$2=="/"{print $1}' | sed 's/^UUID=//')
if [ -n "$ACTUAL" ] && [ "$ACTUAL" = "$ROOTUUID" ] && [ "$ACTUAL" = "$FSTABUUID" ]; then
  ok "root=UUID=$ACTUAL == fstab == actual rootfs"
else
  bad "UUID 不一致 actual=$ACTUAL cmdline=$ROOTUUID fstab=$FSTABUUID"
fi

echo "== 2. Kernel =="
K=$(uname -r)
if [ "$K" = "6.1.0-1025-rockchip" ]; then ok "kernel $K"; else bad "kernel $K"; fi

echo "== 3. DTB =="
DTB=/lib/firmware/6.1.0-1025-rockchip/device-tree/rockchip/rk3588-orangepi-5-plus.dtb
DTBO=/lib/firmware/6.1.0-1025-rockchip/device-tree/rockchip/overlay/rk3588-hdmirx.dtbo
if [ -f "$DTB" ] && grep -q 'rk3588-orangepi-5-plus' /proc/device-tree/compatible 2>/dev/null; then ok "DTB rk3588-orangepi-5-plus"; else bad "DTB 缺失/不匹配"; fi
[ -f "$DTBO" ] && ok "DTBO rk3588-hdmirx.dtbo 存在" || bad "DTBO rk3588-hdmirx.dtbo 缺失"

echo "== 4. HDMI (RX/DRM) =="
DRM=$(ls /dev/dri/card* 2>/dev/null | head -1)
VIDEO=$(ls /dev/video* 2>/dev/null | head -1)
RX0=$(ls /dev/video0 2>/dev/null)
[ -n "$DRM" ] && ok "DRM $DRM" || bad "无 /dev/dri/card"
[ -n "$VIDEO" ] && ok "V4L2 $VIDEO" || bad "无 /dev/video"
[ -n "$RX0" ] && ok "HDMI RX /dev/video0（hdmirx 驱动已绑定）" || bad "HDMI RX 无 /dev/video0（检查 fdtoverlays rk3588-hdmirx.dtbo）"

echo "== 5. RGA =="
if [ -e /dev/rga ] || [ -e /dev/rga0 ]; then ok "RGA 设备 $(ls /dev/rga* 2>/dev/null | tr '\n' ' ')"; else bad "RGA 设备缺失"; fi
[ -f /usr/lib/librga.so ] && ok "librga.so" || echo "    (librga.so 未找到，忽略)"

echo "== 6. RKNPU =="
NPU_FREQ=$(cat /sys/class/devfreq/fdab0000.npu/cur_freq 2>/dev/null)
NPU_DRV=$(lsmod 2>/dev/null | grep -c rknpu)
[ -n "$NPU_FREQ" ] && ok "RKNPU cur_freq=$NPU_FREQ" || bad "RKNPU 不可用"
[ "$NPU_DRV" -ge 1 ] && ok "rknpu 驱动已加载" || echo "    (rknpu 为内核内建/无 lsmod 条目，以 devfreq 为准)"

echo "== 7. RKNN Runtime =="
if [ -f /usr/lib/librknnrt.so ]; then
  V=$(strings /usr/lib/librknnrt.so 2>/dev/null | grep -oE '2\.[0-9]+\.[0-9]+' | head -1)
  ok "librknnrt.so $V"
else bad "librknnrt.so 缺失"; fi

echo "== 8. CPU =="
NCPU=$(nproc)
GOV=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)
[ "$NCPU" -ge 8 ] && ok "CPU 8 核 ($NCPU)" || bad "CPU 核数异常: $NCPU"
[ "$GOV" = "performance" ] && ok "governor=performance" || echo "    (governor=$GOV)"
ONLINE_A76=$(cat /sys/devices/system/cpu/online 2>/dev/null)
[ -n "$ONLINE_A76" ] && ok "CPU online=$ONLINE_A76" || bad "CPU online 不可读"

echo "== 9. GPU =="
GFREQ=$(cat /sys/class/devfreq/fb000000.gpu/cur_freq 2>/dev/null)
[ -n "$GFREQ" ] && ok "GPU cur_freq=$GFREQ" || bad "GPU 不可用"

echo "== 10. DDR =="
DFREQ=$(cat /sys/class/devfreq/fd820000.dmc/cur_freq 2>/dev/null)
[ -n "$DFREQ" ] && ok "DDR cur_freq=$DFREQ" || echo "    (DDR 节点不可读，跳过)"

echo "== 11. EDID =="
EDID_LOG=$OPT/edid/inject.log
if [ -f "$EDID_LOG" ] && grep -qiE 'injected|ok|done|applied|1080p240|3840x240' "$EDID_LOG" 2>/dev/null; then
  ok "EDID 已注入: $(tail -1 "$EDID_LOG")"
elif [ -f "$EDID_LOG" ]; then
  bad "EDID 注入日志异常: $(tail -1 "$EDID_LOG")"
else
  echo "    (EDID 注入日志不存在，检查 $OPT/edid)"
fi

echo "== 12. HID =="
HIDRAW=$(ls /dev/hidraw* 2>/dev/null | wc -l)
[ "$HIDRAW" -ge 1 ] && ok "hidraw $HIDRAW 个" || echo "    (hidraw=0，若未插键鼠属正常)"
if [ -x "$OPT/runtime/ttbox-hid-health" ]; then
  HR=$($OPT/runtime/ttbox-hid-health --root $OPT/hid 2>&1 | tail -1)
  echo "$HR" | grep -q '0 FAIL' && ok "HID 健康: $HR" || bad "HID 健康: $HR"
else
  bad "ttbox-hid-health 缺失"
fi

echo "== 13. Model =="
MODEL=$OPT/models/registry/installed/huangwa.rknn
ACTIVE=$(cat $OPT/models/active_model.txt 2>/dev/null | tr -d '[:space:]')
[ -f "$MODEL" ] && ok "默认模型 huangwa.rknn 存在 ($(du -h "$MODEL" | cut -f1))" || bad "默认模型缺失"
[ "$ACTIVE" = "huangwa.rknn" ] && ok "active=$ACTIVE" || bad "active=$ACTIVE"

echo "== 14. Web :8080 =="
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/ 2>/dev/null)
[ "$CODE" = "200" ] && ok "Web HTTP $CODE" || bad "Web HTTP $CODE"

echo "== 15. 服务 =="
for s in ttbox-runtime ttbox-web ttbox-hid; do
  ST=$(systemctl is-active $s 2>/dev/null)
  [ "$ST" = "active" ] && ok "$s active" || bad "$s $ST"
done

echo ""
echo "=== Diagnostic 汇总: $PASS PASS / $FAIL FAIL ==="
[ $FAIL -eq 0 ] && echo "DIAGNOSTIC: PASS" || echo "DIAGNOSTIC: FAIL"
exit $FAIL
