#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -r /etc/default/aiassistance ]]; then
  set -a
  # shellcheck source=/etc/default/aiassistance
  source /etc/default/aiassistance
  set +a
fi
export LD_LIBRARY_PATH="${ROOT_DIR}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export HAILORT_CONSOLE_LOGGER_LEVEL="${HAILORT_CONSOLE_LOGGER_LEVEL:-error}"

online_cpu_spec() {
  cat /sys/devices/system/cpu/online 2>/dev/null || {
    local count
    count="$(nproc 2>/dev/null || echo 1)"
    if [[ "${count}" =~ ^[0-9]+$ ]] && (( count > 1 )); then
      echo "0-$((count - 1))"
    else
      echo "0"
    fi
  }
}

cpu_from_end() {
  local offset="${1:-0}"
  local spec
  spec="$(online_cpu_spec)"
  awk -F, -v offset="${offset}" '
    {
      count = 0
      for (i = 1; i <= NF; i++) {
        split($i, r, "-")
        first = r[1] + 0
        last = (length(r[2]) ? r[2] : r[1]) + 0
        for (cpu = first; cpu <= last; cpu++) cpus[count++] = cpu
      }
      if (count <= 0) {
        print 0
      } else if (offset < count) {
        print cpus[count - 1 - offset]
      } else {
        print cpus[0]
      }
    }
  ' <<<"${spec}"
}

critical_cpu_range() {
  local spec
  spec="$(online_cpu_spec)"
  awk -F, '
    {
      count = 0
      for (i = 1; i <= NF; i++) {
        split($i, r, "-")
        first = r[1] + 0
        last = (length(r[2]) ? r[2] : r[1]) + 0
        for (cpu = first; cpu <= last; cpu++) cpus[count++] = cpu
      }
      if (count <= 0) {
        print "0"
        next
      }
      start = count > 4 ? count - 4 : 0
      contiguous = 1
      for (i = start + 1; i < count; i++) {
        if (cpus[i] != cpus[i - 1] + 1) contiguous = 0
      }
      if (contiguous && start < count - 1) {
        print cpus[start] "-" cpus[count - 1]
      } else {
        out = cpus[start]
        for (i = start + 1; i < count; i++) out = out "," cpus[i]
        print out
      }
    }
  ' <<<"${spec}"
}

preview_cpu_range() {
  local spec
  spec="$(online_cpu_spec)"
  awk -F, '
    {
      count = 0
      for (i = 1; i <= NF; i++) {
        split($i, r, "-")
        first = r[1] + 0
        last = (length(r[2]) ? r[2] : r[1]) + 0
        for (cpu = first; cpu <= last; cpu++) cpus[count++] = cpu
      }
      if (count <= 0) {
        print "0"
        next
      }
      last_index = count > 4 ? 3 : count - 1
      contiguous = 1
      for (i = 1; i <= last_index; i++) {
        if (cpus[i] != cpus[i - 1] + 1) contiguous = 0
      }
      if (contiguous && last_index > 0) {
        print cpus[0] "-" cpus[last_index]
      } else {
        out = cpus[0]
        for (i = 1; i <= last_index; i++) out = out "," cpus[i]
        print out
      }
    }
  ' <<<"${spec}"
}

export AIASSISTANCE_BIG_CORES="${AIASSISTANCE_BIG_CORES:-$(critical_cpu_range)}"
export AIASSISTANCE_CPUSET_AIM="${AIASSISTANCE_CPUSET_AIM:-$(cpu_from_end 0)}"
export AIASSISTANCE_CPUSET_ENGINE="${AIASSISTANCE_CPUSET_ENGINE:-$(cpu_from_end 1)}"
export AIASSISTANCE_CPUSET_INFERENCE="${AIASSISTANCE_CPUSET_INFERENCE:-$(cpu_from_end 2)}"
export AIASSISTANCE_CPUSET_RKNN="${AIASSISTANCE_CPUSET_RKNN:-${AIASSISTANCE_CPUSET_INFERENCE}}"
export AIASSISTANCE_CPUSET_HAILO="${AIASSISTANCE_CPUSET_HAILO:-${AIASSISTANCE_CPUSET_INFERENCE}}"
export AIASSISTANCE_CPUSET_REMOTE="${AIASSISTANCE_CPUSET_REMOTE:-${AIASSISTANCE_CPUSET_INFERENCE}}"
export AIASSISTANCE_CPUSET_AUX="${AIASSISTANCE_CPUSET_AUX:-${AIASSISTANCE_CPUSET_INFERENCE}}"
export AIASSISTANCE_CPUSET_CRITICAL="${AIASSISTANCE_CPUSET_CRITICAL:-$(critical_cpu_range)}"
export AIASSISTANCE_CPUSET_PREVIEW="${AIASSISTANCE_CPUSET_PREVIEW:-$(preview_cpu_range)}"
export AIASSISTANCE_CPUSET_HAILO_TEMP="${AIASSISTANCE_CPUSET_HAILO_TEMP:-$(preview_cpu_range)}"
if [[ "${AIASSISTANCE_ENSURE_USB_PROXY_RUNTIME:-1}" != "0" &&
      -x "$ROOT_DIR/scripts/ensure_usb_proxy_runtime.sh" ]]; then
  "$ROOT_DIR/scripts/ensure_usb_proxy_runtime.sh" --restart-if-changed || \
    echo "USB proxy runtime verification failed; continuing daemon startup" >&2
fi
if [[ "${AIASSISTANCE_APPLY_USB_PROXY_REALTIME:-1}" != "0" &&
      -x "$ROOT_DIR/scripts/apply_usb_proxy_realtime.sh" ]]; then
  "$ROOT_DIR/scripts/apply_usb_proxy_realtime.sh" >/dev/null 2>&1 || true
fi
if [[ "${AIASSISTANCE_ENSURE_MAKCU_SERVICE:-1}" != "0" &&
      -x "$ROOT_DIR/scripts/ensure_makcu_service.sh" ]]; then
  "$ROOT_DIR/scripts/ensure_makcu_service.sh" "$ROOT_DIR" >/dev/null 2>&1 || true
fi
if [[ "${AIASSISTANCE_KMBOXNET_USB_AUTO_CONFIG:-1}" != "0" &&
      -x "$ROOT_DIR/scripts/configure_kmboxnet_usb.sh" ]]; then
  if ! systemctl is-active --quiet aiassistance-kmboxnet-usb.service >/dev/null 2>&1; then
    "$ROOT_DIR/scripts/configure_kmboxnet_usb.sh" --watch &
  fi
fi
if [[ "${AIASSISTANCE_SYNC_DISPLAY_BOOT_EDID:-1}" != "0" &&
      -x "$ROOT_DIR/scripts/sync_display_boot_edid.sh" ]]; then
  sync_timeout="${AIASSISTANCE_SYNC_DISPLAY_BOOT_EDID_TIMEOUT_SEC:-20s}"
  if command -v timeout >/dev/null 2>&1; then
    timeout "$sync_timeout" "$ROOT_DIR/scripts/sync_display_boot_edid.sh" || \
      echo "HDMI RX boot EDID sync failed or timed out; continuing daemon startup" >&2
  else
    "$ROOT_DIR/scripts/sync_display_boot_edid.sh" || \
      echo "HDMI RX boot EDID sync failed; continuing daemon startup" >&2
  fi
fi
exec "$ROOT_DIR/bin/aiassistance_daemon" "$ROOT_DIR"
