#!/bin/bash
# edid_apply.sh — TTBox EDID 注入（开机 oneshot）
# 依赖：YU 的 hdmirx_edid C 工具（/opt/aiassistance/bin/hdmirx_edid）
set -u

OPT=/opt/ttbox
CONFIG=$OPT/config/hardware_display.json
EDID_BIN=$OPT/run/hdmirx_custom_edid.bin
LOG=$OPT/edid/apply.log
STATE=$OPT/run/hdmirx_edid_state.json
HDMIRX_TOOL=/opt/aiassistance/bin/hdmirx_edid

PROFILE=""
[ -r "$CONFIG" ] && PROFILE=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('profile',''))" 2>/dev/null)
PROFILE=${PROFILE:-boot-safe-full}
DEV=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('device',''))" 2>/dev/null)
DEV=${DEV:-auto}
NATIVE_MODE=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('native_mode',''))" 2>/dev/null)
NATIVE_MODE=${NATIVE_MODE:-1080p240}

mkdir -p "$OPT/run" "$OPT/edid"
echo "[edid-apply] $(date -Is) profile=$PROFILE device=$DEV" >> "$LOG"

# 1. 无 V4L2 设备 → 静默跳过
if ! ls /dev/video[0-9]* >/dev/null 2>&1; then
  echo "[edid-apply] hdmirx 未启用，跳过" >> "$LOG"
  echo '{"applied":false,"reason":"no hdmirx device"}' > "$STATE"
  exit 0
fi

# 2. 注入（C 工具内部完成 生成+S_EDID+校验）
if [ -x "$HDMIRX_TOOL" ]; then
  # 身份字段（hardware_display.json → 工具参数）
DNAME=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('name',''))" 2>/dev/null)
DVENDOR=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('vendor',''))" 2>/dev/null)
DPID=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('product_id',''))" 2>/dev/null)
DSERIAL=$(python3 -c "import json;print(json.load(open('$CONFIG')).get('serial',''))" 2>/dev/null)
IDENT_ARGS=""
[ -n "$DNAME" ] && IDENT_ARGS="$IDENT_ARGS --name $DNAME"
[ -n "$DVENDOR" ] && IDENT_ARGS="$IDENT_ARGS --vendor $DVENDOR"
[ -n "$DPID" ] && IDENT_ARGS="$IDENT_ARGS --product-id $DPID"
[ -n "$DSERIAL" ] && IDENT_ARGS="$IDENT_ARGS --serial $DSERIAL"

if [ "$DEV" != "auto" ] && [ -n "$DEV" ]; then
    "$HDMIRX_TOOL" --native "$NATIVE_MODE" $IDENT_ARGS --apply --device "$DEV" >> "$LOG" 2>&1
  else
    "$HDMIRX_TOOL" --native "$NATIVE_MODE" $IDENT_ARGS --apply >> "$LOG" 2>&1
  fi
  RC=$?
else
  echo "[edid-apply] 缺少 $HDMIRX_TOOL" >> "$LOG"
  RC=127
fi

# 3. 状态
if [ "$RC" -eq 0 ]; then
  echo '{"applied":true}' > "$STATE"
else
  echo '{"applied":false,"exit":'"$RC"'}' > "$STATE"
fi
echo "[edid-apply] done rc=$RC" >> "$LOG"
exit "$RC"
