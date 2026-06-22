"""Amazon S3 implementation of the artifact storage contract."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.exceptions import ClientError

from ap_storage.artifact_keys import (
    artifact_type_from_key,
    join_s3_prefix,
)
from ap_storage.settings import StorageSettings
from ap_storage.storage_service import ArtifactMetadata, StorageService


class S3StorageService(StorageService):
    def __init__(self, settings: StorageSettings):
        self.bucket_name = settings.s3_bucket_name
        self.prefix = settings.s3_prefix

        session_options: dict[str, Any] = {
            "region_name": settings.s3_region,
        }
        if settings.aws_profile:
            session_options["profile_name"] = settings.aws_profile

        session = boto3.Session(**session_options)
        self.client = session.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
        )

    def _object_key(self, key: str) -> str:
        return join_s3_prefix(self.prefix, key)

    def save_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        artifact_type: str,
    ) -> ArtifactMetadata:
        object_key = self._object_key(key)
        self.client.put_object(
            Bucket=self.bucket_name,
            Key=object_key,
            Body=data,
            ContentType=content_type,
            Metadata={"artifact_type": artifact_type},
        )
        return {
            "storage_backend": "s3",
            "bucket_name": self.bucket_name,
            "object_key": object_key,
            "uri": self.build_uri(key),
            "content_type": content_type,
            "size_bytes": len(data),
            "artifact_type": artifact_type,
        }

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(
                Bucket=self.bucket_name,
                Key=self._object_key(key),
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def build_uri(self, key: str) -> str:
        return f"s3://{self.bucket_name}/{self._object_key(key)}"

    def get_metadata(
        self,
        key: str,
        *,
        artifact_type: str | None = None,
    ) -> ArtifactMetadata:
        object_key = self._object_key(key)
        response = self.client.head_object(
            Bucket=self.bucket_name,
            Key=object_key,
        )
        stored_artifact_type = response.get("Metadata", {}).get(
            "artifact_type"
        )
        return {
            "storage_backend": "s3",
            "bucket_name": self.bucket_name,
            "object_key": object_key,
            "uri": f"s3://{self.bucket_name}/{object_key}",
            "content_type": response.get(
                "ContentType",
                "application/octet-stream",
            ),
            "size_bytes": int(response.get("ContentLength", 0)),
            "artifact_type": (
                artifact_type
                or stored_artifact_type
                or artifact_type_from_key(key)
            ),
        }
