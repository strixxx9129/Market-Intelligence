#!/bin/sh
# Restore drill: back up SOURCE, restore it into a scratch database, prove they're identical.
# An untested backup is a hope, not a backup. Runs in CI on every push and monthly against
# real backups (docs/runbooks/restore.md).
#
#   DATABASE_URL         source database
#   DRILL_DATABASE_URL   scratch database (created if missing; its tables are replaced)
#   DRILL_ADMIN_URL      a database on the same server to run CREATE DATABASE from
#   BACKUP_URI, S3_ENDPOINT_URL, AWS_*   where the drill's backup goes
set -eu
: "${DATABASE_URL:?DATABASE_URL is required}"
: "${DRILL_DATABASE_URL:?DRILL_DATABASE_URL is required}"
: "${DRILL_ADMIN_URL:?DRILL_ADMIN_URL is required}"
: "${BACKUP_URI:?BACKUP_URI is required}"
SRC=$(printf '%s' "$DATABASE_URL" | sed 's#^postgresql+asyncpg://#postgresql://#')
DST=$(printf '%s' "$DRILL_DATABASE_URL" | sed 's#^postgresql+asyncpg://#postgresql://#')
ADMIN=$(printf '%s' "$DRILL_ADMIN_URL" | sed 's#^postgresql+asyncpg://#postgresql://#')
DB_NAME=${DST##*/}
DB_NAME=${DB_NAME%%\?*}

if [ "$SRC" = "$DST" ]; then
  echo "refusing: the drill database must differ from the source" >&2
  exit 2
fi
if ! psql "$ADMIN" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1; then
  psql "$ADMIN" -qc "CREATE DATABASE \"$DB_NAME\""
fi

backup_postgres.sh
RESTORE_CONFIRM=yes TARGET_DATABASE_URL="$DST" restore_postgres.sh latest

# Exact row counts of every table on both sides (planner estimates are not good enough).
counts() {
  psql "$1" -tAc "SELECT table_name FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1" |
    while read -r table; do
      printf '%s %s\n' "$table" "$(psql "$1" -tAc "SELECT count(*) FROM \"$table\"")"
    done
}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
counts "$SRC" > "$WORK/source.txt"
counts "$DST" > "$WORK/restored.txt"
if ! diff -u "$WORK/source.txt" "$WORK/restored.txt"; then
  echo "RESTORE DRILL FAILED: row counts differ" >&2
  exit 1
fi
SRC_REV=$(psql "$SRC" -tAc 'SELECT version_num FROM alembic_version')
DST_REV=$(psql "$DST" -tAc 'SELECT version_num FROM alembic_version')
if [ "$SRC_REV" != "$DST_REV" ]; then
  echo "RESTORE DRILL FAILED: alembic revision $DST_REV != $SRC_REV" >&2
  exit 1
fi
echo "RESTORE DRILL OK: $(wc -l < "$WORK/source.txt" | tr -d ' ') tables, identical row counts, revision $SRC_REV"
cat "$WORK/source.txt"
