#!/usr/bin/env bash
# Restore a pg_dump file into a database (spec 4.3).
#
# Usage:
#   scripts/restore.sh <dump-file> [database-name]
#
# Default target is the scratch database `trader_restore`, so a backup
# can be verified without touching the live `trader` database. Pass
# `trader` as the database name only for a real restore — the script
# drops and recreates the target database.
#
# Environment: TRADER_COMPOSE_FILE, TRADER_REPO_DIR as in backup.sh.

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <dump-file> [database-name]" >&2
    exit 2
fi

# Resolve to an absolute path before cd, so relative paths still point
# at the file the caller meant.
DUMP="$(realpath "$1")"
TARGET="${2:-trader_restore}"

REPO_DIR="${TRADER_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
COMPOSE_FILE="${TRADER_COMPOSE_FILE:-docker-compose.prod.yml}"

cd "$REPO_DIR"

if [[ ! -r "$DUMP" ]]; then
    echo "cannot read dump file: $DUMP" >&2
    exit 1
fi

db() { docker compose -f "$COMPOSE_FILE" exec -T db psql -U trader -d postgres -qc "$1"; }

# Reject an invalid dump before any database is dropped.
if ! docker compose -f "$COMPOSE_FILE" exec -T db pg_restore -l < "$DUMP" > /dev/null 2>&1; then
    echo "not a valid pg_dump file: $DUMP" >&2
    exit 1
fi

if [[ "$TARGET" == "trader" ]]; then
    echo "Restoring into the LIVE database 'trader' in 5 seconds. Ctrl-C to abort." >&2
    sleep 5
fi

db "DROP DATABASE IF EXISTS $TARGET;"
db "CREATE DATABASE $TARGET;"

docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_restore -U trader -d "$TARGET" --no-owner --no-privileges < "$DUMP"

echo "Restored $DUMP into database '$TARGET'."
docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U trader -d "$TARGET" -c \
    "SELECT schemaname, relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;"
