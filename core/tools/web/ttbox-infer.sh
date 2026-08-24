#!/bin/bash
# ttbox-infer.sh — C++ 推理入口包装（供 ttbox-infer.service 调用）
# 模型优先使用 /opt/ttbox/models/active_model.txt 指向的 active 模型，
# 否则回退到 infer.json 中的默认模型。
set -e
CONF=/opt/ttbox/config/infer.json
BIN=/opt/ttbox/runtime/test_worker_hw
LOG=/opt/ttbox/runtime/infer.log
ACTIVE_FILE=/opt/ttbox/models/active_model.txt
MODELS_INSTALLED=/opt/ttbox/models/registry/installed
PROFILE=/opt/ttbox/run/runtime_profile.json

MODEL=""
if [ -f "$ACTIVE_FILE" ]; then
  A=$(cat "$ACTIVE_FILE" 2>/dev/null | tr -d '[:space:]')
  [ -n "$A" ] && [ -f "$MODELS_INSTALLED/$A" ] && MODEL="$MODELS_INSTALLED/$A"
fi
if [ -z "$MODEL" ]; then
  MODEL=$(python3 -c "import json;print(json.load(open('$CONF'))['model'])" 2>/dev/null) || true
fi
[ -z "$MODEL" ] && { echo "no model" >&2; exit 1; }

WORKERS=$(python3 -c "import json;print(json.load(open('$CONF'))['workers'])")
CORES=$(python3 -c "import json;print(json.load(open('$CONF'))['cores'])")
BUFFERS=$(python3 -c "import json;print(json.load(open('$CONF'))['buffers'])")
INW=$(python3 -c "import json;print(json.load(open('$CONF'))['in_w'])")
INH=$(python3 -c "import json;print(json.load(open('$CONF'))['in_h'])")

echo "[ttbox-infer] model=$MODEL workers=$WORKERS cores=$CORES buffers=$BUFFERS in=${INW}x${INH}"
stdbuf -oL "$BIN" --model "$MODEL" --adapter --workers "$WORKERS" --cores "$CORES" \
  --buffers "$BUFFERS" --inw "$INW" --inh "$INH" --frames 999999999 --duration 86400 \
  --report-every 5 --profile "$PROFILE" --mouse-fifo /run/ttbox-aim.fifo \
  2>&1 | tee "$LOG"
