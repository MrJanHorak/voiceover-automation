#!/usr/bin/env bash
# Nightly backup of the studio: a consistent copy of the database plus voices, trained voices,
# consent documents, training recordings and recordings. Keeps the last 14 days.
# Copy the archives off the server too (for example with Hetzner Storage Box or rclone).
set -euo pipefail
DATA="${VOICEOVER_DATA_DIR:-/var/lib/voiceover}/studio"
DEST="${VOICEOVER_BACKUP_DIR:-/var/backups/voiceover}"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$DEST" && chmod 700 "$DEST"
sqlite3 "$DATA/studio.sqlite3" ".backup '$WORK/studio.sqlite3'"
sqlite3 "$WORK/studio.sqlite3" "PRAGMA integrity_check;" | grep -qx ok
# Files from the data folder go under files/; the checked database copy sits at the top.
tar --transform 's,^\./,files/,' --exclude='./studio.sqlite3*' --exclude='./.studio.lock' \
  -czf "$DEST/voiceover-$STAMP.tar.gz" -C "$DATA" . -C "$WORK" studio.sqlite3
chmod 600 "$DEST/voiceover-$STAMP.tar.gz"
find "$DEST" -name 'voiceover-*.tar.gz' -mtime +14 -delete
echo "$(date -Is) backup written: $DEST/voiceover-$STAMP.tar.gz"
