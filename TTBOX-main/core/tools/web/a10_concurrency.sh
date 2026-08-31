#!/bin/bash
# a10_concurrency.sh — AI + HID 并发定位
BUILD=/home/ubuntu/ttbox2/ttbox/core/build
MODEL=/opt/ttbox/models/registry/installed/huangwa.rknn
sudo pkill -f test_worker_hw 2>/dev/null; sudo pkill -f test_hid_load_sim 2>/dev/null
sleep 1

echo "=== T1 单独 AI（frames=200 duration=10）==="
sudo timeout 15 $BUILD/test_worker_hw --model $MODEL --adapter --workers 3 --cores 4,5,6 \
  --buffers 8 --frames 200 --inw 320 --inh 320 --duration 10 2>&1 | \
  grep -E 'capture FPS|总吞吐|poll_timeouts' | tail -2

echo "=== T2 先起 HID sim 再 AI ==="
sudo $BUILD/test_hid_load_sim --rate 8000 --duration 15 > /tmp/h1.log 2>&1 &
HIDPID=$!
sleep 1
sudo timeout 15 $BUILD/test_worker_hw --model $MODEL --adapter --workers 3 --cores 4,5,6 \
  --buffers 8 --frames 200 --inw 320 --inh 320 --duration 10 2>&1 | \
  grep -E 'capture FPS|总吞吐|poll_timeouts' | tail -2
wait $HIDPID
echo "HID: $(grep '目标 rate' /tmp/h1.log)"
