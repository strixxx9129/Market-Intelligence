#!/bin/sh
# Daily logical backup of PostgreSQL to object storage.
#
#   DATABASE_URL         postgres URL (a +asyncpg driver suffix is stripped)
#   BACKUP_URI           s3://bucket/prefix  or  file:///dir (CI, local drills)
#   S3_ENDPOINT_URL      S3 API endpoint (R2, SeaweedFS); credentials in AWS_ACCESS_KEY_ID /
#                        AWS_SECRET_ACCESS_KEY
#   BACKUP_KEEP_DAILY    days of daily dumps to keep (default 30)
#   BACKUP_KEEP_MONTHLY  months of 1st-of-month dumps to keep (default 12)
#
# Layout: <prefix>/daily/YYYY-MM-DD/marketlens.dump (+ .sha256), and on the 1st of each month
# a copy under <prefix>/monthly/YYYY-MM/. Exits non-zero on ANY failure, so the scheduler
# (and its alerting) sees a failed backup instead of a silent gap.
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_URI:?BACKUP_URI is required (s3://bucket/prefix or file:///dir)}"
KEEP_DAILY="${BACKUP_KEEP_DAILY:-30}"
KEEP_MONTHLY="${BACKUP_KEEP_MONTHLY:-12}"
PG_URL=$(printf '%s' "$DATABASE_URL" | sed 's#^postgresql+asyncpg://#postgresql://#')
DAY=$(date -u +%F)
MONTH=$(date -u +%Y-%m)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
DUMP="$WORK/marketlens.dump"

log() { printf '{"ts":"%s","event":"%s"}\n' "$(date -u +%FT%TZ)" "$*"; }

# ---- storage helpers: S3 via the AWS CLI, or a local directory --------------------------
s3() { aws ${S3_ENDPOINT_URL:+--endpoint-url "$S3_ENDPOINT_URL"} s3 "$@"; }

put() { # put <file> <relative key>
  case "$BACKUP_URI" in
    file://*)
      dest="${BACKUP_URI#file://}/$2"
      mkdir -p "$(dirname "$dest")" && cp "$1" "$dest"
      ;;
    *) s3 cp --only-show-errors "$1" "$BACKUP_URI/$2" ;;
  esac
}

list_dirs() { # list_dirs <relative prefix>: one sub-directory name per line
  case "$BACKUP_URI" in
    file://*) ls -1 "${BACKUP_URI#file://}/$1" 2>/dev/null || true ;;
    *) s3 ls "$BACKUP_URI/$1/" 2>/dev/null | awk '$1 == "PRE" { sub("/$", "", $2); print $2 }' ;;
  esac
}

remove_dir() {
  case "$BACKUP_URI" in
    # ${1:?}: an empty argument must fail, never become "delete the whole backup prefix".
    file://*) rm -rf "${BACKUP_URI#file://}/${1:?}" ;;
    *) s3 rm --only-show-errors --recursive "$BACKUP_URI/${1:?}/" ;;
  esac
}

older_than() { # older_than <YYYY-MM-DD|YYYY-MM> <cutoff>: compares as numbers (POSIX)
  a=$(printf '%s' "$1" | tr -d '-')
  b=$(printf '%s' "$2" | tr -d '-')
  case "$a" in
    '' | *[!0-9]*) return 1 ;; # not a date we wrote: never prune it
  esac
  [ "$a" -lt "$b" ]
}

# ---- 1. dump -----------------------------------------------------------------------------
log "backup_started day=$DAY"
# Custom format: compressed, and pg_restore can restore selectively and in parallel.
# --no-owner/--no-privileges: restorable into a database with different role names.
pg_dump --format=custom --compress=6 --no-owner --no-privileges --file="$DUMP" "$PG_URL"
# A dump that pg_restore can't read is not a backup: check before uploading.
pg_restore --list "$DUMP" > "$WORK/toc.txt"
TABLES=$(grep -c ' TABLE DATA ' "$WORK/toc.txt" || true)
(cd "$WORK" && sha256sum marketlens.dump > marketlens.dump.sha256)
SIZE=$(wc -c < "$DUMP" | tr -d ' ')

# ---- 2. upload ---------------------------------------------------------------------------
put "$DUMP" "daily/$DAY/marketlens.dump"
put "$DUMP.sha256" "daily/$DAY/marketlens.dump.sha256"
if [ "$(date -u +%d)" = "01" ]; then
  put "$DUMP" "monthly/$MONTH/marketlens.dump"
  put "$DUMP.sha256" "monthly/$MONTH/marketlens.dump.sha256"
fi

# ---- 3. retention ------------------------------------------------------------------------
NOW=$(date +%s)
CUT_DAY=$(date -u -d "@$((NOW - KEEP_DAILY * 86400))" +%F)
CUT_MONTH=$(date -u -d "@$((NOW - KEEP_MONTHLY * 31 * 86400))" +%Y-%m)
for d in $(list_dirs daily); do
  if older_than "$d" "$CUT_DAY"; then
    remove_dir "daily/$d"
    log "pruned daily/$d"
  fi
done
for m in $(list_dirs monthly); do
  if older_than "$m" "$CUT_MONTH"; then
    remove_dir "monthly/$m"
    log "pruned monthly/$m"
  fi
done

log "backup_finished day=$DAY bytes=$SIZE tables=$TABLES"
