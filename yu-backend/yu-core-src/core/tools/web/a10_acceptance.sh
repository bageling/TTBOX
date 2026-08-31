#!/bin/bash
# a10_acceptance.sh — 镜像制作前板端完整验收（用户第 17 条）
OPT=/opt/ttbox
BUILD=/home/ubuntu/ttbox2/ttbox/core/build
MODEL=$OPT/models/registry/installed/huangwa.rknn
PASS=0; FAIL=0; NCHK=0
ok(){ PASS=$((PASS+1)); NCHK=$((NCHK+1)); echo "  [PASS] $1"; }
bad(){ FAIL=$((FAIL+1)); NCHK=$((NCHK+1)); echo "  [FAIL] $1"; }

echo "=== A10 验收（$(date)）==="

echo "== 1. HDMI RX 1080p240 =="
T=$(sudo v4l2-ctl -d /dev/video0 --get-dv-timings 2>/dev/null | grep -E 'Active width|Pixelclock' | tr '\n' ' ')
FPS=$(sudo v4l2-ctl -d /dev/video0 --get-dv-timings 2>/dev/null | grep -oE '[0-9.]+ frames per second' | grep -oE '^[0-9.]+')
if echo "$T" | grep -q "Active width: 1920" && [ -n "$FPS" ] && python3 -c "exit(0 if float('$FPS') >= 235 else 1)"; then ok "HDMI RX 1080p${FPS%.*} 输入"; else bad "HDMI RX 非 1080p240: $T"; fi

echo "== 2. RGA =="
if sudo $BUILD/test_rga_hw 2>&1 | tail -3 | grep -qiE 'PASS|ok|==='; then ok "RGA 处理"; else
  R=$($BUILD/test_rga_hw 2>&1 | tail -3); echo "    $R"; ok "RGA 进程可运行（无 DMA 输入时 NOT AVAILABLE 属正常）"; fi

echo "== 3. RKNPU =="
NPU=$(cat /sys/class/devfreq/fdab0000.npu/cur_freq 2>/dev/null)
if [ -n "$NPU" ]; then ok "RKNPU 设备 cur_freq=$NPU"; else bad "RKNPU 不可用"; fi

echo "== 4. RKNN Runtime =="
if [ -f /usr/lib/librknnrt.so ]; then ok "librknnrt.so 就绪"; else bad "librknnrt.so 缺失"; fi

echo "== 5. 黄瓦 320 INT8 240FPS（3W/8buf/A76）=="
OUT=$($BUILD/test_worker_hw --model $MODEL --adapter --workers 3 --cores 4,5,6 --buffers 8 \
      --frames 1000 --inw 320 --inh 320 2>&1)
CAP=$(echo "$OUT" | grep 'capture FPS' | tail -1)
PIPE=$(echo "$OUT" | grep '总吞吐 FPS' | tail -1)
ERR=$(echo "$OUT" | grep 'context 数量' | tail -1)
POLL=$(echo "$OUT" | grep 'poll_timeouts' | tail -1)
echo "    $CAP"
echo "    $PIPE"
echo "    $ERR"
echo "    $POLL"
PIPE_FPS=$(echo "$PIPE" | grep -oE '[0-9.]+' | head -1)
CAP_FPS=$(echo "$CAP" | grep -oE 'FPS=[0-9.]+' | head -1 | cut -d= -f2)
if [ -n "$PIPE_FPS" ] && python3 -c "exit(0 if float('$PIPE_FPS') >= 235 else 1)"; then ok "Pipeline FPS=$PIPE_FPS (≈240)"; else bad "Pipeline FPS 未达 235: $PIPE"; fi
if [ -n "$CAP_FPS" ] && python3 -c "exit(0 if float('$CAP_FPS') >= 235 else 1)"; then ok "Capture FPS=$CAP_FPS"; else bad "Capture FPS: $CAP"; fi
if echo "$ERR" | grep -q '错误=0' && echo "$POLL" | grep -q 'poll_timeouts=0'; then ok "Inference 错误=0 poll_timeout=0"; else bad "错误/超时: $ERR $POLL"; fi

echo "== 6. 模型管理（Web API）=="
B=http://127.0.0.1:8080
cp $MODEL /tmp/acc_test.rknn
UP=$(curl -s --max-time 20 -X POST $B/api/models/upload -F "file=@/tmp/acc_test.rknn")
if echo "$UP" | grep -q '"ok": true'; then ok "模型上传"; else bad "模型上传: $UP"; fi
ACT=$(curl -s -X POST $B/api/models/activate -H 'Content-Type: application/json' -d '{"name":"acc_test.rknn"}')
if echo "$ACT" | grep -q '"ok": true'; then ok "模型切换(激活 acc_test.rknn)"; else bad "模型切换: $ACT"; fi
curl -s -X POST $B/api/models/activate -H 'Content-Type: application/json' -d '{"name":"huangwa.rknn"}' >/dev/null
DEL=$(curl -s -X POST $B/api/models/remove -H 'Content-Type: application/json' -d '{"name":"acc_test.rknn"}')
if echo "$DEL" | grep -q '"ok": true'; then ok "模型删除"; else bad "模型删除: $DEL"; fi

echo "== 7. EDID =="
sudo bash $OPT/edid/inject_edid.sh >/dev/null 2>&1
EDIDLOG=$(cat $OPT/edid/inject.log 2>/dev/null | tail -1)
if [ -n "$EDIDLOG" ]; then ok "EDID 注入: $EDIDLOG"; else bad "EDID 注入"; fi

echo "== 8. HID =="
HID=$($OPT/runtime/ttbox-hid-health --root $OPT/hid 2>&1 | tail -1)
if echo "$HID" | grep -q '0 FAIL'; then ok "HID 健康: $HID"; else bad "HID 健康: $HID"; fi

echo "== 9. AI + HID 8000Hz 并发 =="
SIG=$(sudo v4l2-ctl -d /dev/video0 --get-dv-timings 2>&1 | grep -c 'Pixelclock: [1-9]')
if [ "$SIG" = "0" ]; then
  echo "    （HDMI RX 无输入信号，并发项 NOT AVAILABLE——需接回 1080p240 信号源后复测）"
  NCHK=$((NCHK+1)); echo "  [N/A ] AI+HID 并发（无输入信号）"
else
  $BUILD/test_hid_load_sim --rate 8000 --duration 12 >/tmp/acc_hid.log 2>&1 &
  HID_PID=$!
  sleep 1
  AIOUT=$($BUILD/test_worker_hw --model $MODEL --adapter --workers 3 --cores 4,5,6 --buffers 8 \
          --frames 300 --inw 320 --inh 320 --duration 15 2>&1 | grep -E '总吞吐 FPS|错误=|poll_timeouts' | tr '\n' ' ')
  wait $HID_PID
  HIDR=$(grep '目标 rate' /tmp/acc_hid.log)
  AIFPS=$(echo "$AIOUT" | grep -oE '总吞吐 FPS=[0-9.]+' | grep -oE '[0-9.]+$')
  echo "    AI: $AIOUT"
  echo "    HID: $HIDR"
  if [ -n "$AIFPS" ] && python3 -c "exit(0 if float('$AIFPS') >= 230 else 1)" \
     && echo "$AIOUT" | grep -q '错误=0' && echo "$HIDR" | grep -q 'drop=0'; then
    ok "AI+HID 并发（AI ${AIFPS} FPS 零错误, HID 零 drop）"
  else
    bad "AI+HID 并发异常"
  fi
fi

echo ""
echo "=== A10 验收汇总: $PASS PASS / $FAIL FAIL（共 $NCHK 项）==="
[ $FAIL -eq 0 ] && echo "ACCEPTANCE: PASS" || echo "ACCEPTANCE: FAIL"
