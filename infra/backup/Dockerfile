# Backup/restore runner: pg_dump/pg_restore + the AWS CLI (talks to any S3: R2, SeaweedFS).
# PG_MAJOR must be >= the server's major version (pg_dump can't dump a newer server).
# Build from the repo root: docker build -f infra/backup/Dockerfile .
ARG PG_MAJOR=16
FROM postgres:${PG_MAJOR}-alpine

RUN apk add --no-cache aws-cli \
    && adduser -D -u 1001 backup
COPY --chmod=755 scripts/backup_postgres.sh scripts/restore_postgres.sh scripts/restore_drill.sh /usr/local/bin/
USER backup
WORKDIR /tmp
# Scheduled daily (Fly scheduled machine, or any cron): runs once, exits non-zero on failure.
ENTRYPOINT []
CMD ["backup_postgres.sh"]
