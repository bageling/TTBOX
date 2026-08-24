#!/bin/bash
# edid_apply.sh — TTBox EDID 生成+注入（开机 oneshot）
#
# 流程：读 hardware_display.json → hdmirx_edid.py 生成 EDID → v4l2-ctl 注入
#       → 校验（get-edid）→ 写状态日志。
# 依赖：python3、v4l2-ctl（v4l-utils）、hdmirx 驱动已加载（/dev/video0 存在）。
set -u

OPT=/opt/ttbox
CONFIG=$OPT/config/hardware_display.json
EDID_TOOL=$OPT/scripts/edid/hdmirx_edid.py
EDID_BIN=$OPT/run/hdmirx_custom_edid.bin
LOG=$OPT/edid/apply.log
STATE=$OPT/run/hdmirx_edid_state.json

DEV=""
PROFILE=""
[ -r "$CONFIG" ] && DEV=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('device',''))" 2>/dev/null)
[ -r "$CONFIG" ] && PROFILE=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('profile',''))" 2>/dev/null)
PROFILE=${PROFILE:-boot-safe-full}
# device 为 auto/空 → 不传 --device，由 hdmirx_edid.py 自动检测
if [ -n "$DEV" ] && [ "$DEV" != "auto" ]; then
  DEV_OPT="--device $DEV"
else
  DEV_OPT=""
fi

mkdir -p "$OPT/run" "$OPT/edid"

echo "[edid-apply] $(date -Is) profile=$PROFILE device=${DEV:-auto}" >> "$LOG"

# 1. 无 V4L2 视频设备 → hdmirx 未启用（overlay 未开），静默跳过
if ! ls /dev/video[0-9]* >/dev/null 2>&1; then
  echo "[edid-apply] hdmirx 未启用（无视频设备），跳过注入" >> "$LOG"
  echo '{"applied":false,"reason":"no hdmirx device"}' > "$STATE"
  exit 0
fi

# 2. 生成 + 注入 + 校验（hdmirx_edid.py apply 内完成生成/写盘/VIDIOC_S_EDID/校验）
#    identity 由 hardware_display.json 覆盖（hdmirx_edid.py 支持环境变量覆盖）
if [ -r "$CONFIG" ]; then
  export HDMIRX_COMPAT_EDID_NAME=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('name',''))" 2>/dev/null)
  export HDMIRX_COMPAT_EDID_VENDOR=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('vendor',''))" 2>/dev/null)
  export HDMIRX_COMPAT_EDID_PRODUCT_ID=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('product_id',''))" 2>/dev/null)
  export HDMIRX_COMPAT_EDID_SERIAL=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('serial',''))" 2>/dev/null)
fi
HPD_SYS=/sys/class/hdmirx/hdmirx/status
# HPD OFF：模拟拔线，让源端在注入后做干净热插拔重握手（对齐 239 方案）
if [ -w "$HPD_SYS" ]; then
  echo off > "$HPD_SYS" 2>/dev/null || true
  echo "[edid-apply] HPD forced off" >> "$LOG"
  sleep 1
fi
if [ -f "$EDID_TOOL" ]; then
  # shellcheck disable=SC2086
  python3 "$EDID_TOOL" --output "$EDID_BIN" $DEV_OPT apply "$PROFILE" >> "$LOG" 2>&1
  RC=$?
else
  echo "[edid-apply] 缺少 $EDID_TOOL" >> "$LOG"
  RC=127
fi

# 3. 状态记录
if [ "$RC" -eq 0 ]; then
  echo '{"applied":true}' > "$STATE"
else
  echo '{"applied":false,"exit":'"$RC"'}' > "$STATE"
fi
# HPD ON：模拟插线，源端重新读取 EDID 并握手
if [ -w "$HPD_SYS" ]; then
  echo on > "$HPD_SYS" 2>/dev/null || true
  echo "[edid-apply] HPD forced on" >> "$LOG"
fi
echo "[edid-apply] done rc=$RC" >> "$LOG"
exit "$RC"
