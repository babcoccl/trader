#!/usr/bin/env bash
# Nightly database backup (spec 4.3).
#
# Dumps the `trader` database from the running prod stack to
# $TRADER_BACKUP_DIR (must be on a second disk) and deletes dumps older
# than 14 days. Install on the box with a cron entry, e.g.:
#
#   17 3 * * * /opt/trader/scripts/backup.sh >> /var/log/trader-backup.log 2>&1
#
# Environment:
#   TRADER_BACKUP_DIR    dump directory on the second disk
#                        (default: /var/backups/trader)
#   TRADER_COMPOSE_FILE  compose file to run against
#                        (default: docker-compose.prod.yml)
#   TRADER_REPO_DIR      checkout directory (default: script's parent)

set -euo pipefail

REPO_DIR="${TRADER_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
COMPOSE_FILE="${TRADER_COMPOSE_FILE:-docker-compose.prod.yml}"
BACKUP_DIR="${TRADER_BACKUP_DIR:-/var/backups/trader}"
KEEP_DAYS=14

cd "$REPO_DIR"
mkdir -p "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/trader-$stamp.dump"

docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U trader -Fc trader > "$out.tmp"
mv "$out.tmp" "$out"

find "$BACKUP_DIR" -maxdepth 1 -name 'trader-*.dump' -mtime "+$KEEP_DAYS" -delete

echo "$(date -u +%FT%TZ) wrote $out ($(du -h "$out" | cut -f1))"
