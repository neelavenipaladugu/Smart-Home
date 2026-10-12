#!/usr/bin/env bash
# Back up config + database + face images to ~/pihome-backups (keeps the last 10).
# Add to cron for daily backups:  0 3 * * * bash ~/pihome/scripts/backup.sh
set -euo pipefail
HUB_DIR="$(cd "$(dirname "$0")/../hub" && pwd)"
DEST="$HOME/pihome-backups"
mkdir -p "$DEST"
FILE="$DEST/pihome-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$FILE" -C "$HUB_DIR" config.yaml data/smarthome.db data/faces 2>/dev/null || \
tar -czf "$FILE" -C "$HUB_DIR" config.yaml data/smarthome.db
ls -1t "$DEST"/pihome-*.tar.gz | tail -n +11 | xargs -r rm --
echo "Backup saved: $FILE"
