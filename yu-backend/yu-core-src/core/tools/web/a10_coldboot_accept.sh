#!/bin/bash
# a10_coldboot_accept.sh — TTBox v0.0.1 冷启动验收（全新 TF 卡烧录后执行，需求 14）
# 用法: bash a10_coldboot_accept.sh [--with-hid]   (--with-hid: 需真实键鼠已插入并配合移动)
# 原则: 实测数据，禁止伪造；物理条件不具备的项输出 NOT AVAILABLE。
set -u
OPT=/opt/ttbox
T=$OPT/tests
MODEL=$OPT/models/registry/installed/huangwa.rknn
B=http://127.0.0.1:8080
PASS=0; FAIL=0; NA=0
ok(){ PASS=$((PASS+1)); echo "  [PASS] $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  [FAIL] $1"; }
na(){ NA=$((NA+1)); echo "  [N/A ] $1"; }

echo "=== TTBox v0.0.1 冷启动验收 $(date -u +%FT%TZ) ==="

echo "== 1. 系统启动 =="
for s in ttbox-firstboot ttbox-runtime ttbox-web ttbox-hid; do
  ST=$(systemctl is-active $s 2>/dev/null)
  [ "$ST" = "active" ] || [ "$ST" = "exited" ] && ok "$s ($ST)" || bad "$s ($ST)"
done
U=$(findmnt -no UUID / 2>/dev/null)
C=$(grep -oE 'root=UUID=[0-9a-f-]+' /proc/cmdline | cut -d= -f3)
[ -n "$U" ] && [ "$U" = "$C" ] && ok "Boot root UUID=$U" || bad "Boot UUID 不一致 ($U/$C)"

echo "== 2. 网络 =="
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "$IP" ] && ok "IP=$IP" || bad "无 IP"
ping -c1 -W2 192.168.0.1 >/dev/null 2>&1 && ok "网关可达" || echo "    (网关不可达，若不在同一网段属正常)"

echo "== 3. Web =="
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 $B/ 2>/dev/null)
[ "$CODE" = "200" ] && ok "Web :8080 HTTP 200" || bad "Web HTTP $CODE"

echo "== 4. 第一屏 (DRM 输出) =="
D=$(ls /sys/class/drm/*/status 2>/dev/null | while read f; do echo "$f=$(cat "$f" 2>/dev/null)"; done | grep -c connected)
[ "$D" -ge 1 ] && ok "DRM connected=$D" || bad "DRM 无 connected 输出"

echo "== 5. 第二屏 / HDMI RX 1080p240 =="
TIM=$(sudo v4l2-ctl -d /dev/video0 --get-dv-timings 2>/dev/null)
W=$(echo "$TIM" | grep -oE 'Active width: [0-9]+' | grep -oE '[0-9]+')
FPS=$(echo "$TIM" | grep -oE '[0-9.]+ frames per second' | grep -oE '^[0-9.]+')
if [ "$W" = "1920" ] && [ -n "$FPS" ] && python3 -c "exit(0 if float('$FPS')>=235 else 1)" 2>/dev/null; then
  ok "HDMI RX 1920x1080@${FPS}fps"
elif [ -n "$W" ] && [ -n "$FPS" ]; then
  bad "HDMI RX ${W}@${FPS}（非 1080p240）"
else
  na "HDMI RX 无输入信号"
fi

echo "== 6. RGA / RKNPU / RKNN =="
if [ -e /dev/rga ] || [ -e /dev/rga0 ]; then ok "RGA 设备" ; else bad "RGA 缺失"; fi
[ -n "$(cat /sys/class/devfreq/fdab0000.npu/cur_freq 2>/dev/null)" ] && ok "RKNPU" || bad "RKNPU 缺失"
[ -f /usr/lib/librknnrt.so ] && ok "RKNN runtime" || bad "RKNN runtime 缺失"

echo "== 7. 黄瓦 320 INT8 ≈240 FPS（3W/8buf/A76/锁频）=="
OUT=$($T/test_worker_hw --model $MODEL --adapter --workers 3 --cores 4,5,6 --buffers 8 \
      --frames 1000 --inw 320 --inh 320 2>&1)
CAP=$(echo "$OUT" | grep -oE 'capture FPS=[0-9.]+' | tail -1 | cut -d= -f2)
INF=$(echo "$OUT" | grep -oE '(infer|inference) FPS=[0-9.]+' -i | tail -1 | cut -d= -f2)
PIPE=$(echo "$OUT" | grep -oE '总吞吐 FPS=[0-9.]+' | tail -1 | cut -d= -f2)
ERR=$(echo "$OUT" | grep -oE '错误=[0-9]+' | tail -1)
POLL=$(echo "$OUT" | grep -oE 'poll_timeouts=[0-9]+' | tail -1)
[ -n "$CAP" ] && ok "Capture FPS=$CAP" || bad "Capture 无输出"
[ -n "$INF" ] && ok "Inference FPS=$INF" || echo "    (Inference FPS 未单独输出: $INF)"
if [ -n "$PIPE" ] && python3 -c "exit(0 if float('$PIPE')>=235 else 1)" 2>/dev/null; then ok "Pipeline FPS=$PIPE (≈240)"; else bad "Pipeline FPS=$PIPE"; fi
echo "$OUT" | grep -q '错误=0' && ok "Error=0" || bad "$ERR"
echo "$OUT" | grep -q 'poll_timeouts=0' && ok "Poll Timeout=0" || bad "$POLL"

echo "== 8. 模型管理（Web API）=="
cp $MODEL /tmp/acc_upload.rknn
UP=$(curl -s --max-time 30 -X POST $B/api/models/upload -F "file=@/tmp/acc_upload.rknn")
echo "$UP" | grep -q '"ok": true' && ok "RKNN 上传" || bad "RKNN 上传: $UP"
AC=$(curl -s -X POST $B/api/models/activate -H 'Content-Type: application/json' -d '{"name":"acc_upload.rknn"}')
echo "$AC" | grep -q '"ok": true' && ok "模型切换(激活)" || bad "模型切换: $AC"
curl -s -X POST $B/api/models/activate -H 'Content-Type: application/json' -d '{"name":"huangwa.rknn"}' >/dev/null
DE=$(curl -s -X POST $B/api/models/remove -H 'Content-Type: application/json' -d '{"name":"acc_upload.rknn"}')
echo "$DE" | grep -q '"ok": true' && ok "模型删除" || bad "模型删除: $DE"
curl -s -X POST $B/api/models/activate -H 'Content-Type: application/json' -d '{"name":"huangwa.rknn"}' >/dev/null
ACT=$(cat $OPT/models/active_model.txt | tr -d '[:space:]')
[ "$ACT" = "huangwa.rknn" ] && ok "Rollback 到默认模型" || bad "Rollback: active=$ACT"

echo "== 9. ONNX 上传（离线转换管线，板端收 RKNN）=="
if [ -f /tmp/v26m_test.rknn ]; then
  U2=$(curl -s --max-time 30 -X POST $B/api/models/upload -F "file=@/tmp/v26m_test.rknn")
  echo "$U2" | grep -q '"ok": true' && ok "ONNX 转换产物(RKNN)上传" || bad "上传: $U2"
else
  na "无 ONNX 转换产物，跳过（ONNX→RKNN 为 PC 端离线工具）"
fi

echo "== 10. EDID（查看/选择/应用）=="
ED=$(curl -s -X POST $B/api/edid -H 'Content-Type: application/json' -d '{"action":"reload"}')
echo "$ED" | grep -q '"ok": true' && ok "EDID 应用(1080p240)" || bad "EDID 应用: $ED"
ls $OPT/edid/*.bin >/dev/null 2>&1 && ok "EDID 文件存在: $(ls $OPT/edid/*.bin 2>/dev/null | xargs -n1 basename | tr '\n' ' ')" || na "EDID 文件未找到"

echo "== 11. HID（真实键鼠）=="
HR=$($OPT/runtime/ttbox-hid-health --root $OPT/hid 2>&1 | tail -1)
echo "    $HR"
echo "$HR" | grep -q '0 FAIL' && ok "HID 健康" || bad "HID 健康: $HR"
HIDRAW=$(ls /dev/hidraw* 2>/dev/null | wc -l)
if [ "$HIDRAW" -ge 1 ]; then
  if echo "$*" | grep -q -- "--with-hid"; then
    echo "    >> 请在接下来 5 秒内移动鼠标/敲击键盘..."
    sleep 5
    $OPT/runtime/ttbox-hid-health --root $OPT/hid 2>&1 | tail -3
    na "真实键鼠 RX 计数需人工核对上方健康输出"
  else
    na "真实键鼠实测：未带 --with-hid（需插入键鼠后人工配合）"
  fi
else
  na "未检测到 hidraw 设备（真实键鼠未插入）"
fi

echo "== 12. AI + HID 并发 =="
SIG=$(sudo v4l2-ctl -d /dev/video0 --get-dv-timings 2>&1 | grep -c 'Pixelclock: [1-9]')
if [ "$SIG" = "0" ]; then
  na "HDMI RX 无输入，AI+HID 并发无法实测"
else
  $T/test_hid_load_sim --rate 8000 --duration 12 >/tmp/acc_hid.log 2>&1 &
  HP=$!
  sleep 1
  AIOUT=$($T/test_worker_hw --model $MODEL --adapter --workers 3 --cores 4,5,6 --buffers 8 \
          --frames 300 --inw 320 --inh 320 --duration 15 2>&1 | grep -E '总吞吐 FPS|错误=|poll_timeouts' | tr '\n' ' ')
  wait $HP
  HIDR=$(grep '目标 rate' /tmp/acc_hid.log)
  echo "    AI: $AIOUT"
  echo "    HID: $HIDR"
  echo "$AIOUT" | grep -q '错误=0' && echo "$HIDR" | grep -q 'drop=0' && ok "AI+HID 并发（详见上方输出）" || bad "AI+HID 并发异常"
fi

echo ""
echo "=== 冷启动验收汇总: $PASS PASS / $FAIL FAIL / $NA N/A ==="
[ $FAIL -eq 0 ] && echo "COLD_BOOT_ACCEPTANCE: PASS" || echo "COLD_BOOT_ACCEPTANCE: FAIL"
