#!/usr/bin/env bash
# Snapshots edgeflow.db and prunes old snapshots. Run via the
# edgeflow-backup systemd timer (see deploy/systemd/), or by hand:
#   EDGEFLOW_DB_PATH=... EDGEFLOW_BACKUP_DIR=... ./deploy/backup-db.sh
set -euo pipefail

DB_PATH="${EDGEFLOW_DB_PATH:-$(dirname "$0")/../data/edgeflow.db}"
BACKUP_DIR="${EDGEFLOW_BACKUP_DIR:-$(dirname "$0")/../data/backups}"
RETENTION_DAYS="${EDGEFLOW_BACKUP_RETENTION_DAYS:-30}"

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "edgeflow-backup: sqlite3 CLI not found (apt install sqlite3)" >&2
    exit 1
fi

if [ ! -f "$DB_PATH" ]; then
    echo "edgeflow-backup: no database at $DB_PATH yet, nothing to back up"
    exit 0
fi

mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_DIR/edgeflow-$TIMESTAMP.db"

# `sqlite3 .backup` takes a consistent snapshot even while the engine is
# writing in WAL mode -- a plain `cp` could copy a torn/inconsistent file.
sqlite3 "$DB_PATH" ".backup '$DEST'"

find "$BACKUP_DIR" -name 'edgeflow-*.db' -mtime "+${RETENTION_DAYS}" -delete

echo "edgeflow-backup: backed up to $DEST"
