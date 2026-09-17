#!/bin/sh
# run-ttbox-usb-proxy.sh — TTBOX usb-proxy 启动脚本（自研）
#
# T1.06（DEP-04③）两处修复：
#   1）以【绝对路径】定位并 exec 二进制（`$PROJECT_DIR/usb-proxy`），不再 `exec ./usb-proxy`——
#      相对路径依赖调用者 cwd，systemd 未设 WorkingDirectory 或换目录启动时就找不到。
#   2）二进制缺失时【人话报错】并给出两条可复制的修复命令；绝不落到 shell 的裸 127
#      （"not found"）——旧写法删掉二进制后只有一行费解的 127，排障无从下手。
set -eu

USB_PROXY_DEVICE=${USB_PROXY_DEVICE:-fc000000.usb}
USB_PROXY_DRIVER=${USB_PROXY_DRIVER:-dwc3-gadget}
USB_PROXY_WAIT_SECONDS=${USB_PROXY_WAIT_SECONDS:-1}
USB_PROXY_EXTRA_ARGS=${USB_PROXY_EXTRA_ARGS:-}
USB_PROXY_MODE=${USB_PROXY_MODE:-full}   # full | synthetic
USB_PROXY_SOCKET_DIR=${USB_PROXY_SOCKET_DIR:-/run/ttbox-mouse-passthrough}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
# 二进制与脚本同属 release 树的 usbproxy/ 目录（T1.01 布局：releases/<ver>/usbproxy/）。
USB_PROXY_BIN=${USB_PROXY_BIN:-$PROJECT_DIR/usb-proxy}

# ---- 预检：二进制存在且可执行，缺失则人话报错（绝不裸 127）-------------------
if [ ! -e "$USB_PROXY_BIN" ]; then
	cat >&2 <<EOF
Stopped: usb-proxy binary missing: $USB_PROXY_BIN
Reason: the prebuilt binary must ship inside the release tree (T1.01 layout: releases/<ver>/usbproxy/).
Fix (pick ONE):
  1) Re-publish a complete payload that contains usbproxy/usb-proxy, then activate:
       scripts/ttbox_release_install.sh <ver> <payload_dir> --activate
  2) Build it on the board (needs the dev libraries):
       apt-get install -y libusb-1.0-0-dev liblua5.4-dev libjsoncpp-dev g++ && cd $PROJECT_DIR && make
EOF
	exit 1
fi
if [ ! -x "$USB_PROXY_BIN" ]; then
	printf 'Stopped: usb-proxy is not executable: %s\n' "$USB_PROXY_BIN" >&2
	printf '  Fix: chmod +x "%s"\n' "$USB_PROXY_BIN" >&2
	exit 1
fi

stop_conflicting_services()
{
	# TTBOX 独占 UDC：把板端其它 USB 透传服务先停掉，避免和 raw-gadget 抢控制器。
	for unit in usb-proxy.service usb-proxy-test.service mouse-passthrough.service \
		opi-mouse-gadget.service usbdevice.service; do
		if systemctl is-active --quiet "$unit" 2>/dev/null; then
			systemctl stop "$unit" >/dev/null 2>&1 || true
			printf 'Stopped conflicting service %s\n' "$unit"
		fi
	done
}

find_mouse()
{
	for dev in /sys/bus/usb/devices/*; do
		[ -f "$dev/idVendor" ] || continue
		[ -f "$dev/idProduct" ] || continue

		for intf in "$dev":*; do
			[ -f "$intf/bInterfaceClass" ] || continue
			class=$(cat "$intf/bInterfaceClass")
			protocol=$(cat "$intf/bInterfaceProtocol" 2>/dev/null || printf '00')

			if [ "$class" = "03" ] && [ "$protocol" = "02" ]; then
				printf '%s %s\n' "$(cat "$dev/idVendor")" "$(cat "$dev/idProduct")"
				return 0
			fi
		done
	done

	return 1
}

cd "$PROJECT_DIR"
mkdir -p "$USB_PROXY_SOCKET_DIR"
stop_conflicting_services

while [ ! -e "/sys/class/udc/$USB_PROXY_DEVICE" ]; do
	printf 'Waiting for USB device controller %s...\n' "$USB_PROXY_DEVICE"
	sleep "$USB_PROXY_WAIT_SECONDS"
done

ARGS="--device=$USB_PROXY_DEVICE --driver=$USB_PROXY_DRIVER"
ARGS="$ARGS --mouse_control_cmd_socket=$USB_PROXY_SOCKET_DIR/cmd.sock"
ARGS="$ARGS --mouse_control_event_socket=$USB_PROXY_SOCKET_DIR/event.sock"

if [ "$USB_PROXY_MODE" = "synthetic" ]; then
	printf 'TTBOX usb-proxy synthetic mode\n'
	ARGS="$ARGS --synthetic_mouse --enable_mouse_control"
else
	while ! ids=$(find_mouse); do
		printf 'Waiting for a USB HID mouse on the Orange Pi side...\n'
		sleep "$USB_PROXY_WAIT_SECONDS"
	done

	set -- $ids
	vendor_id=$1
	product_id=$2

	printf 'Using USB mouse %s:%s\n' "$vendor_id" "$product_id"
	ARGS="$ARGS --vendor_id=$vendor_id --product_id=$product_id --hid_passthrough_compat --enable_mouse_control"
fi

# shellcheck disable=SC2086
exec "$USB_PROXY_BIN" $ARGS $USB_PROXY_EXTRA_ARGS
