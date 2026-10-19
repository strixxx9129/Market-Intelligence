# MLflow tracking server: metadata in PostgreSQL, artifacts in S3 (SeaweedFS).
# The official image lacks the Postgres driver and boto3, so we build a small one.
FROM python:3.14-slim

RUN pip install --no-cache-dir \
        mlflow==3.16.1 \
        psycopg2-binary==2.9.13 \
        boto3==1.43.114

COPY ensure_bucket.py /usr/local/bin/ensure_bucket.py

EXPOSE 5000

# Non-root at runtime (the compose storage-init job only talks to S3, no root needed either).
RUN useradd --create-home --uid 1000 mlflow
USER mlflow
