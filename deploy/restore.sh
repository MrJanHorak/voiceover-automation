#!/usr/bin/env bash
# Restore a backup made by backup.sh. Stops the studio, keeps the current data as data.before-restore-*, then restarts.
#   sudo bash deploy/restore.sh /var/backups/voiceover/voiceover-2026-11-22_031500.tar.gz
set -euo pipefail
ARCHIVE="${1:?Give the backup archive to restore}"
DATA="${VOICEOVER_DATA_DIR:-/var/lib/voiceover}/studio"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

tar -C "$WORK" -xzf "$ARCHIVE"
sqlite3 "$WORK/studio.sqlite3" "PRAGMA integrity_check;" | grep -qx ok
systemctl stop voiceover 2>/dev/null || true
if [ -d "$DATA" ]; then mv "$DATA" "$DATA.before-restore-$(date +%Y%m%d%H%M%S)"; fi
mkdir -p "$DATA"
cp -a "$WORK/files/." "$DATA/"
cp "$WORK/studio.sqlite3" "$DATA/studio.sqlite3"
chown -R voiceover:voiceover "$DATA" 2>/dev/null || true
systemctl start voiceover 2>/dev/null || true
echo "Restored $ARCHIVE into $DATA"
