#!/bin/sh
# Start SeaweedFS (master + volume + filer + S3 gateway in one process).
# The S3 identity file is generated from env vars so no credentials are committed.
set -eu

cat > /tmp/s3.json <<EOF
{
  "identities": [
    {
      "name": "admin",
      "credentials": [{"accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}"}],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]
    }
  ]
}
EOF

# Each bucket is a separate collection that needs its own volumes. The default volume size
# (30 GB) leaves room for only a few volumes on a small dev disk, so a second bucket fails
# with "no free volumes left". Smaller volumes + a higher cap fix that.
exec weed server -dir=/data -ip.bind=0.0.0.0 -s3 -s3.port=8333 -s3.config=/tmp/s3.json \
    -master.volumeSizeLimitMB=1024 -volume.max=100
