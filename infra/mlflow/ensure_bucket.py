"""Create the object-store buckets if they don't exist (idempotent, safe to re-run).

BUCKETS is a comma-separated list (e.g. "mlflow,backtests"); falls back to MLFLOW_BUCKET.
"""

import os
import sys
import time

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

buckets = [
    b.strip() for b in os.environ.get("BUCKETS", os.environ["MLFLOW_BUCKET"]).split(",") if b.strip()
]
s3 = boto3.client("s3", endpoint_url=os.environ["MLFLOW_S3_ENDPOINT_URL"])


def ensure(bucket: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"bucket '{bucket}' already exists")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            raise
        s3.create_bucket(Bucket=bucket)
        print(f"bucket '{bucket}' created")


for attempt in range(30):
    try:
        for name in buckets:
            ensure(name)
        sys.exit(0)
    except EndpointConnectionError:
        print(f"object store not reachable yet (attempt {attempt + 1})")
        time.sleep(2)

sys.exit("object store never became reachable")
