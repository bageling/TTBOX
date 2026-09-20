#!/usr/bin/env bash
# ttbox_dtb_fix.sh —— 修复出厂 DTB 里 HDMI-RX 被禁用（status=disabled）
#
# 背景（2026-09-20 现场事故）：
#   出厂镜像的 rk3588-orangepi-5-plus.dtb 里 hdmirx-controller@fdee0000 的
#   status="disabled"，而 extlinux.conf 没有任何 fdtoverlays ⇒ rk3588-hdmirx.dtbo
#   从未被应用 ⇒ HDMI-RX 不 probe ⇒ /sys/class/hdmirx 与 /dev/video0 都不存在
#   ⇒ ①EDID 应用报「未找到可写 HDMI-RX HPD 节点」②HDMI 采集整体不可用。
#
# 本脚本把那份 DTB 换成"已验证可用"的一份（与出厂版唯一差异就是这一处 status）。
#
# ★ 三条安全约束（缺一不可）：
#   1. **指纹门禁**：只有当目标 DTB 的 sha256 精确等于已知的坏版本时才替换。
#      目标是好版本、或是任何不认识的版本 ⇒ 一律不动。绝不"看着像就换"。
#   2. **绝不影响安装结果**：恒退出 0（DTB 属于启动链，与运行树无关，
#      不允许因为它把一次 OTA 判失败）。
#   3. **写后立即校验**：替换完再算一次 sha256，不等于期望值就回滚原文件。
#
# ★ 生效时机：DTB 由 u-boot 在开机时读取 ⇒ 本脚本只换文件，**必须重启才生效**。
#
# 用法：
#   ttbox_dtb_fix.sh [目标根]       目标根默认 /（测试时可传假根，如 /tmp/fakeroot）
set -u

GOOD_SHA="277d9de87876a4e6160ae7ac048d4adadec73bbaa7706f39e2b5a5fe42379980"
BAD_SHA="7b8cc8925552c4a261bb2207e59c005541feda6e575d706cf8a2c5171c1de9a3"
DTB_REL="lib/firmware/5.10.0-1012-rockchip/device-tree/rockchip/rk3588-orangepi-5-plus.dtb"

ROOT="${1:-/}"
ROOT="${ROOT%/}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${TTBOX_DTB_SRC:-$HERE/../deploy/dtb/rk3588-orangepi-5-plus.dtb}"

log()  { printf '[ttbox-dtb-fix] %s\n' "$*"; }
warn() { printf '[ttbox-dtb-fix][WARN] %s\n' "$*" >&2; }

# ---- 0. 前置 ----
if [ ! -f "$SRC" ]; then
    warn "源 DTB 不存在: $SRC（跳过，不影响安装）"
    exit 0
fi
SRC_SHA="$(sha256sum "$SRC" 2>/dev/null | cut -d' ' -f1)"
if [ "$SRC_SHA" != "$GOOD_SHA" ]; then
    warn "源 DTB 指纹不符（got=$SRC_SHA want=$GOOD_SHA），拒绝使用（跳过）"
    exit 0
fi
if [ "$(id -u)" != "0" ] && [ -z "${TTBOX_DTB_FIX_TEST:-}" ]; then
    warn "非 root，跳过 DTB 修复"
    exit 0
fi

# ---- 1. 定位目标（按内核版本通配，只认 orangepi-5-plus 这一份）----
# 深度：lib/firmware/<内核ver>/device-tree/rockchip/<file>.dtb = 5 层，放宽到 6
TARGETS="$(find "${ROOT}/lib/firmware" -maxdepth 6 -type f \
           -path '*/device-tree/rockchip/rk3588-orangepi-5-plus.dtb' 2>/dev/null)"
if [ -z "$TARGETS" ]; then
    log "未找到目标 DTB（${ROOT}/${DTB_REL}），跳过"
    exit 0
fi

rc=0
while IFS= read -r dst; do
    [ -n "$dst" ] || continue
    cur="$(sha256sum "$dst" 2>/dev/null | cut -d' ' -f1)"
    case "$cur" in
        "$GOOD_SHA")
            log "$(basename "$dst") 已是正确的 DTB，无需改动"
            ;;
        "$BAD_SHA")
            log "$(basename "$dst") 是出厂坏版本（HDMI-RX disabled），开始替换"
            bak="${dst}.ttbox-bak-$(date +%Y%m%d%H%M%S)"
            if ! cp -a -- "$dst" "$bak" 2>/dev/null; then
                warn "备份失败，放弃替换: $dst"; rc=0; continue
            fi
            if ! cat "$SRC" > "$dst" 2>/dev/null; then
                warn "写入失败，回滚: $dst"; cat "$bak" > "$dst" 2>/dev/null; continue
            fi
            chmod 0644 "$dst" 2>/dev/null || true
            got="$(sha256sum "$dst" 2>/dev/null | cut -d' ' -f1)"
            if [ "$got" != "$GOOD_SHA" ]; then
                warn "替换后校验不符（got=$got），回滚: $dst"
                cat "$bak" > "$dst" 2>/dev/null
                continue
            fi
            log "已替换为可用 DTB（备份 $bak）⇒ **需重启后生效**"
            ;;
        *)
            warn "$(basename "$dst") 指纹未登记（got=$cur），不敢动，跳过"
            ;;
    esac
done <<EOF
$TARGETS
EOF

exit 0
