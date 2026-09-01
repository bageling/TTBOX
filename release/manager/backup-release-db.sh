#!/bin/sh
set -eu
# 每次备份 SQLite 数据库；由 cron/systemd timer 调用。
DATA_DIR=${TTBOX_RELEASE_DATA:-/var/lib/ttbox/release-manager}
BACKUP_DIR="$DATA_DIR/backups"
mkdir -p "$BACKUP_DIR"
[ -f "$DATA_DIR/releases.db" ] || exit 0
stamp=$(date -u +%Y%m%dT%H%M%SZ)
cp -p "$DATA_DIR/releases.db" "$BACKUP_DIR/releases-$stamp.db"
find "$BACKUP_DIR" -type f -name 'releases-*.db' -mtime +30 -delete
