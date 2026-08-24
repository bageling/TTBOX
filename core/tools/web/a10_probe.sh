#!/bin/bash
# a10_probe.sh — 定位 test_worker_hw 持续运行问题（stdbuf/tee 组合对照）
MODEL=/opt/ttbox/models/registry/installed/huangwa.rknn
BIN=/opt/ttbox/runtime/test_worker_hw
ARGS="--model $MODEL --adapter --workers 3 --cores 4,5,6 --buffers 8 --inw 320 --inh 320 --frames 999999999 --duration 86400"

echo "== A: 无 stdbuf / 输出文件 =="
sudo timeout 8 $BIN $ARGS > /tmp/A.log 2>&1
echo "A rc=$? lines=$(wc -l < /tmp/A.log)"

echo "== B: stdbuf -oL / 输出文件 =="
sudo timeout 8 stdbuf -oL $BIN $ARGS > /tmp/B.log 2>&1
echo "B rc=$? lines=$(wc -l < /tmp/B.log)"

echo "== C: 无 stdbuf / tee =="
sudo timeout 8 bash -c "$BIN $ARGS 2>&1 | tee /tmp/C.log" > /tmp/C.out 2>&1
echo "C rc=$? lines=$(wc -l < /tmp/C.log)"

echo "== D: stdbuf -oL / tee =="
sudo timeout 8 bash -c "stdbuf -oL $BIN $ARGS 2>&1 | tee /tmp/D.log" > /tmp/D.out 2>&1
echo "D rc=$? lines=$(wc -l < /tmp/D.log)"

echo "== E: frames=1000 对照（有限帧，应完成）=="
sudo timeout 20 $BIN --model $MODEL --adapter --workers 3 --cores 4,5,6 --buffers 8 --inw 320 --inh 320 --frames 1000 --duration 60 > /tmp/E.log 2>&1
echo "E rc=$? lines=$(wc -l < /tmp/E.log)"

echo "== A/B/D 末尾 ==="
for f in A B D; do echo "--- $f ---"; tail -3 /tmp/$f.log; done
