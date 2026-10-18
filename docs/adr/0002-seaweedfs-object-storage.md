# ADR 0002: SeaweedFS instead of MinIO for S3-compatible object storage

- Status: Accepted
- Date: 2026-10-18

## Context

The planned stack used MinIO as the local S3-compatible store for MLflow artifacts and, later,
raw data files. During Phase 1 the public MinIO images (`minio/minio`, `minio/mc` on Docker
Hub, `quay.io/minio/*`, `bitnami/minio`) could no longer be pulled, after MinIO stopped
distributing community builds. A fresh clone must work with `make up`, so we need an image that
is publicly available and openly licensed.

## Decision

Use **SeaweedFS** (`chrislusf/seaweedfs`, Apache-2.0, pinned tag) in single-container
`weed server -s3` mode.

- S3 credentials are generated at container start from `S3_ACCESS_KEY` / `S3_SECRET_KEY`
  (`infra/seaweedfs/entrypoint.sh`), so nothing secret is committed.
- A one-shot `storage-init` service (boto3) creates the `mlflow` bucket idempotently.
- MLflow serves artifacts through its own proxy (`--serve-artifacts`), so clients only need the
  MLflow URL, not S3 credentials.

## Consequences

- All application code talks plain S3 (boto3 / MLflow) and stays store-agnostic; switching to
  AWS S3, Cloudflare R2, RustFS or MinIO later only changes the endpoint and credentials.
- No MinIO web console; use the S3 API, MLflow UI, or `weed shell` for inspection.

## Alternatives considered

- **RustFS**: closest MinIO drop-in, but a very young project.
- **Garage**: lightweight, but AGPL and needs a CLI layout/key bootstrap.
- **Building MinIO from source**: AGPL, slow builds, no upstream images to track.
