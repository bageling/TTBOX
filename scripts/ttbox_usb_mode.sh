#!/bin/sh
# ttbox_usb_mode.sh — USB 鼠标透传模式的【运维入口】（show | set full | set synthetic）
#
# ─────────────────────────────────────────────────────────────────────────────
# 为何存在（2026-09-22 业主定案）：
#   模式的真源是 systemd 单元的 `Environment=USB_PROXY_MODE=full|synthetic`，
#   而面板（ttbox-web，ttbox 身份）**没有 root**，改不了单元 —— 原来面板上那个
#   「完整透传 / 合成模式」单选点了必然失败（PUT /api/hardware/mouse/mode 恒返回
#   ok:false），激活向导也因为这个 ok:false 在 82% 处抛错。面板已改为**只读展示**，
#   切换统一走本脚本（唯一入口），不再有人手改单元文件。
#
# 为什么用 drop-in 而不是改单元本体：
#   `deploy/systemd/ttbox-usbproxy.service` 会被发布/安装流程覆盖写回，
#   手改的那一行会静默丢失。drop-in（*.service.d/10-mode.conf）是独立文件、
#   优先级高于单元本体，且发布流程不动它 ⇒ 用户的选择留得住。
#
# 用法：
#   ttbox_usb_mode.sh show                 # 打印「单元请求值」与「进程实际值」
#   sudo ttbox_usb_mode.sh set full        # 完整透传（需盒子插物理 USB 鼠标）
#   sudo ttbox_usb_mode.sh set synthetic   # 合成模式（不需要外接鼠标）
#
# 退出码：0 成功；1 用法/参数错；2 需要 root；3 systemctl 操作失败
set -eu

UNIT=ttbox-usbproxy
UNITS_DIR=${TTBOX_UNIT_DIR:-/etc/systemd/system}
SYSTEMD=${TTBOX_SYSTEMD:-1}
DROPIN_DIR="$UNITS_DIR/$UNIT.service.d"
DROPIN="$DROPIN_DIR/10-mode.conf"
# 自测钩子：放行非 root 写入（仅离线夹具；生产不得设置）。
TEST_MODE=${TTBOX_USB_MODE_TEST:-}

usage()
{
	cat <<'EOF'
用法:
  ttbox_usb_mode.sh show
  sudo ttbox_usb_mode.sh set full|synthetic

说明: full=完整透传(盒子需插物理 USB 鼠标)；synthetic=合成模式(不需要外接鼠标)。
      show 会同时打印「单元请求值」与「进程实际值」——两者不一致时说明已自动降级。
EOF
}

unit_mode()
{
	if [ "$SYSTEMD" = "0" ]; then
		# 自测模式：直接读 drop-in，不碰 systemctl
		[ -f "$DROPIN" ] && sed -n 's/^Environment=USB_PROXY_MODE=//p' "$DROPIN" | tail -1
		return 0
	fi
	out=$(systemctl show -p Environment "$UNIT" 2>/dev/null || true)
	for token in $out; do
		case "$token" in
		USB_PROXY_MODE=*)
			printf '%s\n' "${token#USB_PROXY_MODE=}"
			return 0
			;;
		esac
	done
	return 0
}

# 进程实际跑的模式：命令行带 --synthetic_mouse 即合成（与 web 端同一判据）。
effective_mode()
{
	for d in /proc/[0-9]*; do
		[ -r "$d/cmdline" ] || continue
		cmd=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null || true)
		case "$cmd" in
		*usb-proxy*) ;;
		*) continue ;;
		esac
		case "$cmd" in
		*--synthetic_mouse*) printf 'synthetic\n'; return 0 ;;
		*--vendor_id*)       printf 'full_passthrough\n'; return 0 ;;
		esac
	done
	return 0
}

do_show()
{
	req=$(unit_mode)
	eff=$(effective_mode)
	printf '单元请求值: %s\n' "${req:-<未设置>}"
	printf '进程实际值: %s\n' "${eff:-<未运行>}"
	if [ -n "$req" ] && [ -n "$eff" ]; then
		case "$req:$eff" in
		full:synthetic)
			printf '注意: 已降级——单元要完整透传，但盒子上没找到物理 USB 鼠标。\n'
			printf '      插上鼠标后重启: sudo systemctl restart %s\n' "$UNIT"
			;;
		esac
	fi
}

do_set()
{
	mode=$1
	case "$mode" in
	full|synthetic) ;;
	*)
		printf '模式必须是 full 或 synthetic（收到: %s）\n' "$mode" >&2
		exit 1
		;;
	esac

	if [ "$(id -u)" != "0" ] && [ -z "$TEST_MODE" ]; then
		printf '需要 root：改 systemd 单元需写 %s 并重启 %s\n' "$DROPIN_DIR" "$UNIT" >&2
		printf '  sudo %s set %s\n' "$0" "$mode" >&2
		exit 2
	fi

	mkdir -p "$DROPIN_DIR"
	cat > "$DROPIN" <<EOF
# 由 scripts/ttbox_usb_mode.sh 写入（USB 鼠标透传模式）。
# 请勿手改单元本体 deploy/systemd/$UNIT.service —— 它会被发布流程覆盖。
[Service]
Environment=USB_PROXY_MODE=$mode
EOF
	printf '已写入 %s (USB_PROXY_MODE=%s)\n' "$DROPIN" "$mode"

	if [ "$SYSTEMD" = "0" ]; then
		printf '[自测] TTBOX_SYSTEMD=0，跳过 daemon-reload/restart\n'
	else
		if ! systemctl daemon-reload; then
			printf 'systemctl daemon-reload 失败\n' >&2
			exit 3
		fi
		if ! systemctl restart "$UNIT"; then
			printf 'systemctl restart %s 失败（看 journalctl -u %s）\n' "$UNIT" "$UNIT" >&2
			exit 3
		fi
	fi
	do_show
}

[ $# -ge 1 ] || { usage; exit 1; }
case "$1" in
show) do_show ;;
set)  [ $# -ge 2 ] || { usage; exit 1; }; do_set "$2" ;;
-h|--help|help) usage ;;
*) usage; exit 1 ;;
esac
