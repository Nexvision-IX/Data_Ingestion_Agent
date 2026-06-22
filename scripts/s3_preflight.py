"""Verify configured S3 bucket/prefix can be used by this runtime.

This is intended for EC2 deployment validation. It uses the normal boto3
credential chain, so EC2 should use the attached IAM instance role instead of
static AWS access keys.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from ap_storage.artifact_keys import join_s3_prefix
from ap_storage.settings import load_storage_settings


def main() -> int:
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError as exc:
        print("[FAILURE] boto3/botocore is not installed.")
        print(str(exc))
        return 1

    try:
        settings = load_storage_settings()
    except Exception as exc:
        print(f"[FAILURE] Could not load storage settings: {exc}")
        return 1

    if settings.backend != "s3":
        print(
            "[FAILURE] STORAGE_BACKEND must be 's3' for the S3 preflight. "
            f"Current value: {settings.backend!r}"
        )
        return 1

    session_options = {"region_name": settings.s3_region}
    if settings.aws_profile:
        session_options["profile_name"] = settings.aws_profile

    session = boto3.Session(**session_options)
    s3 = session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
    )
    sts = session.client("sts")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    object_key = join_s3_prefix(
        settings.s3_prefix,
        f"readiness/s3-preflight-{timestamp}.txt",
    )
    body = (
        "AP Automation S3 readiness preflight\n"
        f"timestamp={timestamp}\n"
        f"bucket={settings.s3_bucket_name}\n"
        f"key={object_key}\n"
    ).encode("utf-8")

    try:
        identity = sts.get_caller_identity()
        print(
            "[SUCCESS] AWS identity resolved: "
            f"{identity.get('Arn', '<unknown>')}"
        )
    except Exception as exc:
        print(f"[FAILURE] Could not resolve AWS identity: {exc}")
        return 1

    try:
        s3.head_bucket(Bucket=settings.s3_bucket_name)
        print(f"[SUCCESS] Bucket is reachable: {settings.s3_bucket_name}")
    except ClientError as exc:
        print(f"[FAILURE] Bucket head check failed: {exc}")
        return 1

    try:
        s3.put_object(
            Bucket=settings.s3_bucket_name,
            Key=object_key,
            Body=body,
            ContentType="text/plain; charset=utf-8",
            Metadata={"purpose": "ap-automation-readiness"},
        )
        print(
            "[SUCCESS] Wrote test object: "
            f"s3://{settings.s3_bucket_name}/{object_key}"
        )
    except Exception as exc:
        print(f"[FAILURE] Could not write S3 test object: {exc}")
        return 1

    try:
        metadata = s3.head_object(
            Bucket=settings.s3_bucket_name,
            Key=object_key,
        )
        size = metadata.get("ContentLength", 0)
        print(f"[SUCCESS] Read metadata for test object. Size: {size} bytes")
    except Exception as exc:
        print(f"[FAILURE] Could not read S3 test object metadata: {exc}")
        return 1

    try:
        response = s3.get_object(
            Bucket=settings.s3_bucket_name,
            Key=object_key,
        )
        downloaded = response["Body"].read()
        if downloaded != body:
            print("[FAILURE] S3 read-back content did not match written content.")
            return 1
        print("[SUCCESS] Read-back content matched.")
    except Exception as exc:
        print(f"[FAILURE] Could not read back S3 test object: {exc}")
        return 1

    print("\n[SUCCESS] S3 preflight completed.")
    print(f"Preflight object retained at: s3://{settings.s3_bucket_name}/{object_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())