#!/bin/sh
# run-ttbox-usb-proxy.sh — TTBOX usb-proxy 启动脚本（自研）
#
# T1.06（DEP-04③）两处修复：
#   1）以【绝对路径】定位并 exec 二进制（`$PROJECT_DIR/usb-proxy`），不再 `exec ./usb-proxy`——
#      相对路径依赖调用者 cwd，systemd 未设 WorkingDirectory 或换目录启动时就找不到。
#   2）二进制缺失时【人话报错】并给出两条可复制的修复命令；绝不落到 shell 的裸 127
#      （"not found"）——旧写法删掉二进制后只有一行费解的 127，排障无从下手。
#
# 1.5.26（三处修复，2026-09-21 客户侧报障 + 本机镜像实测）：
#   a) 自带运行库目录 —— 出厂镜像缺 libjsoncpp.so.25，usb-proxy 起不来（详见下方注释）
#   b) find_mouse 三级判定 —— 旧判据只认 protocol=02，客户那只鼠标是 00，永远匹配不上
#   c) 两处 while 死等加超时 —— 旧写法找不到就永远等，进程根本不启动
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

# ---- 1.5.26(a)：自带运行库目录（出厂镜像缺 libjsoncpp，必须随包走）--------------
# 事由（2026-09-21 客户侧报障 + 只读挂载出厂镜像 work.img 实测）：
#   usb-proxy 的 NEEDED 含 libjsoncpp.so.25，而**出厂镜像 V4/V5 里没有这个库**
#   （实测：/usr/lib/aarch64-linux-gnu 下无 libjsoncpp*）。根因是镜像装依赖那步的
#   清单（image/steps/01_install_deps.sh）**只照 core 一个二进制的 ldd 实测**得出，
#   不含 usb-proxy 的依赖；开发板能跑只因装过 libjsoncpp-dev（台面污染）。
#   而 usb-proxy 二进制**无 RUNPATH 段**（Makefile 只有 `LDFLAGS += -pthread`），
#   只能靠 LD_LIBRARY_PATH 或系统库路径 —— 故在此显式前置自带目录。
#   本目录随 release 树整体换版本，不会跨版本串库。
USB_PROXY_LIBDIR=${USB_PROXY_LIBDIR:-$PROJECT_DIR/lib}
if [ -d "$USB_PROXY_LIBDIR" ]; then
	LD_LIBRARY_PATH="$USB_PROXY_LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
	export LD_LIBRARY_PATH
fi

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

# 1.5.26(b)：三级判定。客户实测 MCHOSE 鼠标 bInterfaceClass=03 / bInterfaceProtocol=00。
#   一级 03/02 —— 标准 HID 鼠标，精确命中
#   二级 03/≠01 —— 无协议描述符的 HID 复合设备（客户那只落在这级）
#   三级 找不到 —— 由调用方超时后降级 synthetic，绝不无限死等
#   旧判据只认 02 ⇒ 客户那只永远匹配不上 ⇒ while 死等 ⇒ usb-proxy 永不启动
#   ⇒ 电脑侧连"鼠标不存在"都看不到（进程都没起来）。这是报障的真根因。
#   不认 protocol=01（键盘），避免把键盘当鼠标。
# 输出：<idVendor> <idProduct> <protocol>
find_mouse()
{
	for dev in /sys/bus/usb/devices/*; do
		[ -f "$dev/idVendor" ] || continue
		[ -f "$dev/idProduct" ] || continue

		for intf in "$dev":*; do
			[ -f "$intf/bInterfaceClass" ] || continue
			[ "$(cat "$intf/bInterfaceClass")" = "03" ] || continue
			[ "$(cat "$intf/bInterfaceProtocol" 2>/dev/null || printf '00')" = "02" ] || continue
			printf '%s %s 02\n' "$(cat "$dev/idVendor")" "$(cat "$dev/idProduct")"
			return 0
		done
	done

	# 二级：class=03 但协议非 02 且非 01 的 HID（协议字段 00 = 未声明，复合设备常见）
	for dev in /sys/bus/usb/devices/*; do
		[ -f "$dev/idVendor" ] || continue
		[ -f "$dev/idProduct" ] || continue

		for intf in "$dev":*; do
			[ -f "$intf/bInterfaceClass" ] || continue
			[ "$(cat "$intf/bInterfaceClass")" = "03" ] || continue
			protocol=$(cat "$intf/bInterfaceProtocol" 2>/dev/null || printf '00')
			[ "$protocol" = "01" ] && continue   # 01 = 键盘，不认
			printf '%s %s %s\n' "$(cat "$dev/idVendor")" "$(cat "$dev/idProduct")" "$protocol"
			return 0
		done
	done

	return 1
}

cd "$PROJECT_DIR"
mkdir -p "$USB_PROXY_SOCKET_DIR"
stop_conflicting_services

# 1.5.26(c)-1：等 UDC。旧写法是无限死等（只能靠 systemd 超时杀，日志看不出为什么）。
# 现在超时即人话报错 + 退出 → 交 systemd 重启 + unit 的 StartLimit 兜底。
UDC_WAIT=${USB_PROXY_UDC_WAIT_SECONDS:-60}
_waited=0
while [ ! -e "/sys/class/udc/$USB_PROXY_DEVICE" ]; do
	if [ "$_waited" -ge "$UDC_WAIT" ]; then
		printf 'Stopped: USB device controller %s 在 %ss 内未出现。\n' \
			"$USB_PROXY_DEVICE" "$UDC_WAIT" >&2
		printf '  预期驱动 %s 已加载、且该 UDC 未被其它服务占用。查：\n' "$USB_PROXY_DRIVER" >&2
		printf '    ls /sys/class/udc/ ; systemctl status ttbox-usbproxy\n' >&2
		exit 1
	fi
	printf 'Waiting for USB device controller %s... (%ss/%ss)\n' \
		"$USB_PROXY_DEVICE" "$_waited" "$UDC_WAIT"
	sleep "$USB_PROXY_WAIT_SECONDS"
	_waited=$((_waited + USB_PROXY_WAIT_SECONDS))
done

ARGS="--device=$USB_PROXY_DEVICE --driver=$USB_PROXY_DRIVER"
ARGS="$ARGS --mouse_control_cmd_socket=$USB_PROXY_SOCKET_DIR/cmd.sock"
ARGS="$ARGS --mouse_control_event_socket=$USB_PROXY_SOCKET_DIR/event.sock"

# 1.5.26(c)-2：full 模式先找物理鼠标，超时降级 synthetic（AI 注入可用、物理透传不可用）。
ids=""
if [ "$USB_PROXY_MODE" != "synthetic" ]; then
	MOUSE_WAIT=${USB_PROXY_MOUSE_WAIT_SECONDS:-30}
	_waited=0
	while ! ids=$(find_mouse); do
		ids=""
		if [ "$_waited" -ge "$MOUSE_WAIT" ]; then
			printf 'WARN: %ss 内未找到物理 HID 鼠标，降级为合成鼠标模式。\n' "$MOUSE_WAIT" >&2
			printf '      合成模式下 AI 注入可用、物理鼠标透传不可用。\n' >&2
			printf '      插上 USB 鼠标后 systemctl restart ttbox-usbproxy 即可恢复物理透传。\n' >&2
			USB_PROXY_MODE=synthetic
			break
		fi
		printf 'Waiting for a USB HID mouse on the Orange Pi side... (%ss/%ss)\n' \
			"$_waited" "$MOUSE_WAIT"
		sleep "$USB_PROXY_WAIT_SECONDS"
		_waited=$((_waited + USB_PROXY_WAIT_SECONDS))
	done
fi

if [ "$USB_PROXY_MODE" = "synthetic" ]; then
	printf 'TTBOX usb-proxy synthetic mode\n'
	ARGS="$ARGS --synthetic_mouse --enable_mouse_control"
else
	set -- $ids
	vendor_id=$1
	product_id=$2

	printf 'Using USB mouse %s:%s\n' "$vendor_id" "$product_id"
	ARGS="$ARGS --vendor_id=$vendor_id --product_id=$product_id --hid_passthrough_compat --enable_mouse_control"
fi

# shellcheck disable=SC2086
exec "$USB_PROXY_BIN" $ARGS $USB_PROXY_EXTRA_ARGS
