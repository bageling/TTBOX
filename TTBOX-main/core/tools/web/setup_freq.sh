#!/bin/bash
# setup_freq.sh — 锁频：CPU/GPU/NPU/DDR performance governor（root 执行，无敏感信息）
# 对齐 YU 性能模式：CPU 全核 performance + GPU/NPU/DDR performance 锁频
for i in 0 1 2 3 4 5 6 7; do
  echo performance > /sys/devices/system/cpu/cpu$i/cpufreq/scaling_governor 2>/dev/null
done
echo performance > /sys/class/devfreq/fb000000.gpu/governor 2>/dev/null
if grep -q performance /sys/class/devfreq/fdab0000.npu/available_governors 2>/dev/null; then
  echo performance > /sys/class/devfreq/fdab0000.npu/governor 2>/dev/null
fi
if grep -q performance /sys/class/devfreq/dmc/available_governors 2>/dev/null; then
  echo performance > /sys/class/devfreq/dmc/governor 2>/dev/null
fi
echo "[setup_freq] cpu0=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null) cpu4=$(cat /sys/devices/system/cpu/cpu4/cpufreq/scaling_governor 2>/dev/null) gpu=$(cat /sys/class/devfreq/fb000000.gpu/governor 2>/dev/null) npu=$(cat /sys/class/devfreq/fdab0000.npu/governor 2>/dev/null) dmc=$(cat /sys/class/devfreq/dmc/governor 2>/dev/null)"
