"""Create the MLflow artifact bucket if it does not exist (idempotent, safe to re-run)."""

import os
import sys
import time

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

bucket = os.environ["MLFLOW_BUCKET"]
s3 = boto3.client("s3", endpoint_url=os.environ["MLFLOW_S3_ENDPOINT_URL"])

for attempt in range(30):
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"bucket '{bucket}' already exists")
        sys.exit(0)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            s3.create_bucket(Bucket=bucket)
            print(f"bucket '{bucket}' created")
            sys.exit(0)
        raise
    except EndpointConnectionError:
        print(f"object store not reachable yet (attempt {attempt + 1})")
        time.sleep(2)

sys.exit("object store never became reachable")
