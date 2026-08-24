#!/bin/bash
# ttbox-perf.sh — 性能模式常驻服务（对齐 YU apply_performance_mode）
# 1) 锁频（CPU/GPU/NPU/DDR performance）
# 2) cpu_dma_latency=0（常驻保持 fd 打开，降低调度延迟）
# 3) hdmirx IRQ 亲和：hdmirx 中断绑定 CPU7（空闲超大核），隔离中断抢占推理线程（CPU4/5/6）

# 1) 锁频
if [ -x /opt/ttbox/scripts/setup_freq.sh ]; then
  bash /opt/ttbox/scripts/setup_freq.sh 2>/dev/null
fi

# 2) hdmirx IRQ → CPU7（smp_affinity 写 "80" 十六进制无前缀；0x 前缀会导致 EINVAL），hdmirx-5v 保留默认
for irq in $(awk '/rk_hdmirx-(hdmi|dma)/ {print $1}' /proc/interrupts | tr -d ':'); do
  echo 80 > /proc/irq/$irq/smp_affinity 2>/dev/null
  echo "[ttbox-perf] irq$irq -> $(cat /proc/irq/$irq/effective_affinity_list 2>/dev/null)"
done

# 3) cpu_dma_latency=0 常驻（保持 fd 打开；退出前挂起）
python3 -c "
import os, time, sys
try:
    fd = os.open('/dev/cpu_dma_latency', os.O_WRONLY)
    os.write(fd, b'\x00\x00\x00\x00')
except OSError as e:
    print('[ttbox-perf] cpu_dma_latency:', e); sys.exit(0)
print('[ttbox-perf] cpu_dma_latency=0 held')
try:
    while True:
        time.sleep(300)
except (KeyboardInterrupt, SystemExit):
    pass
finally:
    try: os.close(fd)
    except OSError: pass
"
