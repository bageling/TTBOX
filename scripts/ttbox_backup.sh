#!/usr/bin/env bash
# ttbox_backup.sh — TTBOX 备份（D09，2026-09-18 定案 O14/I-29）
#
# 三处打包并校验：/etc/ttbox（配置）、/var/lib/ttbox（数据/授权/模型）、
# /opt/ttbox/releases（发布树，含可回滚的旧版本）。
# 产出 <outdir>/ttbox-backup-<ts>.tgz + 同名 .sha256 清单；restore 逐项校验后解开。
#
# 用法: ttbox_backup.sh [outdir]     （缺省 /var/backups）
set -euo pipefail

log()  { printf '[backup] %s\n' "$*"; }
die()  { printf '[backup][FATAL] %s\n' "$*" >&2; exit 1; }

OUT_DIR="${1:-/var/backups}"
TS="$(date +%Y%m%d-%H%M%S)"
STAGE="$(mktemp -d /var/tmp/ttbox-backup-XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

TARGETS=(/etc/ttbox /var/lib/ttbox /opt/ttbox/releases)
MANIFEST="${STAGE}/MANIFEST.sha256"

log "收集备份目标（存在才打包）：${TARGETS[*]}"
mkdir -p "${STAGE}/data"
for t in "${TARGETS[@]}"; do
    if [ -e "$t" ]; then
        cp -a "$t" "${STAGE}/data/"
    else
        log "跳过（不存在）: $t"
    fi
done

[ -d "${STAGE}/data/etc" ] || [ -d "${STAGE}/data/var" ] || [ -d "${STAGE}/data/opt" ] \
    || die "三处目标全不存在——这台机器上没有 TTBOX？"

# sha256 清单（相对 STAGE/data 的路径），restore 逐项复验
( cd "${STAGE}/data" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$MANIFEST"
log "清单条目数: $(wc -l < "$MANIFEST")"

OUT="${OUT_DIR}/ttbox-backup-${TS}.tgz"
mkdir -p "$OUT_DIR"
tar -C "$STAGE" -czf "$OUT" data MANIFEST.sha256
sha256sum "$OUT" > "${OUT}.sha256"

log "备份完成: ${OUT}"
log "sha256: $(awk '{print $1}' "${OUT}.sha256")"
log "恢复: ttbox.sh restore ${OUT}"
