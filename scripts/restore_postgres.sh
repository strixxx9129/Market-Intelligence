#!/bin/sh
# Restore a backup made by backup_postgres.sh into TARGET_DATABASE_URL.
#
#   restore_postgres.sh [daily/YYYY-MM-DD | monthly/YYYY-MM | latest]   (default: latest)
#
#   TARGET_DATABASE_URL  database to restore INTO (its tables are replaced: --clean)
#   BACKUP_URI, S3_ENDPOINT_URL, AWS_*   as for backup_postgres.sh
#   RESTORE_CONFIRM=yes  required, because restoring replaces every table in the target
#
# Procedure: docs/runbooks/restore.md (restore into a NEW database, verify, then point
# DATABASE_URL at it; never restore over production blindly).
set -eu

: "${TARGET_DATABASE_URL:?TARGET_DATABASE_URL is required}"
: "${BACKUP_URI:?BACKUP_URI is required}"
if [ "${RESTORE_CONFIRM:-}" != "yes" ]; then
  echo "refusing: set RESTORE_CONFIRM=yes (this replaces the target database's tables)" >&2
  exit 2
fi
WHICH="${1:-latest}"
PG_URL=$(printf '%s' "$TARGET_DATABASE_URL" | sed 's#^postgresql+asyncpg://#postgresql://#')
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

s3() { aws ${S3_ENDPOINT_URL:+--endpoint-url "$S3_ENDPOINT_URL"} s3 "$@"; }

get() { # get <relative key> <destination file>
  case "$BACKUP_URI" in
    file://*) cp "${BACKUP_URI#file://}/$1" "$2" ;;
    *) s3 cp --only-show-errors "$BACKUP_URI/$1" "$2" ;;
  esac
}

if [ "$WHICH" = "latest" ]; then
  # shellcheck disable=SC2012 # names are YYYY-MM-DD directories this tooling created
  case "$BACKUP_URI" in
    file://*) LAST=$(ls -1 "${BACKUP_URI#file://}/daily" 2>/dev/null | sort | tail -n 1) ;;
    *) LAST=$(s3 ls "$BACKUP_URI/daily/" | awk '$1 == "PRE" { sub("/$", "", $2); print $2 }' | sort | tail -n 1) ;;
  esac
  if [ -z "$LAST" ]; then
    echo "no backups found under $BACKUP_URI/daily" >&2
    exit 1
  fi
  WHICH="daily/$LAST"
fi

echo "restoring $WHICH"
get "$WHICH/marketlens.dump" "$WORK/marketlens.dump"
get "$WHICH/marketlens.dump.sha256" "$WORK/marketlens.dump.sha256"
(cd "$WORK" && sha256sum -c marketlens.dump.sha256) # corrupted download: stop here

pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error \
  --dbname="$PG_URL" "$WORK/marketlens.dump"

REVISION=$(psql "$PG_URL" -tAc 'SELECT version_num FROM alembic_version')
echo "restored $WHICH (alembic revision $REVISION)"
