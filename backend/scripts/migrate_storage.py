"""Copy lesson artifacts between two S3-compatible buckets (Neon -> Cloudflare R2).

Keys and bucket name are preserved, so DB references (s3://bucket/materials/...) stay valid.
Re-runnable: objects already present with the same size are skipped.

  SRC_ENDPOINT SRC_KEY_ID SRC_SECRET SRC_BUCKET [SRC_REGION]
  DST_ENDPOINT DST_KEY_ID DST_SECRET DST_BUCKET [DST_REGION=auto]
  python -m scripts.migrate_storage [--prefix materials/] [--dry-run]
"""
import argparse
import os
import sys

import boto3
from botocore.config import Config


def client(p, region):
    return boto3.client(
        "s3", endpoint_url=os.environ[f"{p}_ENDPOINT"],
        aws_access_key_id=os.environ[f"{p}_KEY_ID"], aws_secret_access_key=os.environ[f"{p}_SECRET"],
        region_name=os.getenv(f"{p}_REGION", region),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))


def keys(c, bucket, prefix):
    for page in c.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            yield o["Key"], o["Size"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="materials/")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    src, dst = client("SRC", "ap-southeast-1"), client("DST", "auto")
    sb, db = os.environ["SRC_BUCKET"], os.environ["DST_BUCKET"]
    have = dict(keys(dst, db, a.prefix))
    copied = skipped = failed = 0
    for key, size in keys(src, sb, a.prefix):
        if have.get(key) == size:
            skipped += 1
            continue
        if a.dry_run:
            copied += 1
            continue
        try:
            body = src.get_object(Bucket=sb, Key=key)
            dst.put_object(Bucket=db, Key=key, Body=body["Body"].read(),
                           ContentType=body.get("ContentType", "application/octet-stream"))
            copied += 1
        except Exception as e:  # keep going; a re-run retries the rest
            failed += 1
            print(f"FAILED {key}: {e}", file=sys.stderr)
    print(f"copied={copied} skipped={skipped} failed={failed}" + (" (dry run)" if a.dry_run else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
