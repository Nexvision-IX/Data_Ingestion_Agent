"""Exercise invoice artifact metadata persistence with temporary SQLite."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _metadata(
    artifact_type: str,
    relative_path: str,
    *,
    size_bytes: int,
) -> dict:
    local_path = PROJECT_ROOT / "storage" / relative_path
    return {
        "storage_backend": "local",
        "local_path": str(local_path),
        "uri": local_path.resolve().as_uri(),
        "content_type": (
            "application/json"
            if relative_path.endswith(".json")
            else "text/plain"
        ),
        "size_bytes": size_bytes,
        "artifact_type": artifact_type,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ap-artifact-db-") as temp_dir:
        database_path = Path(temp_dir) / "agent_artifacts.db"
        database_url = f"sqlite:///{database_path.as_posix()}"
        os.environ["APP_ENV"] = "development"
        os.environ["DATABASE_URL"] = database_url
        os.environ["MASTER_DATABASE_URL"] = database_url

        from ap_database.agent_artifact_models import ArtifactBase
        from ap_database.artifact_repository import (
            list_artifacts_for_invoice,
            list_artifacts_for_upload,
            save_artifact_metadata,
        )
        from ap_database.engines import get_agent_engine

        engine = get_agent_engine()
        ArtifactBase.metadata.create_all(bind=engine)

        invoice_number = "INV_RDS_TEST_001"
        upload_id = "upload-step-15c-001"
        records = {
            "original": _metadata(
                "original",
                "invoices/upload-step-15c-001/original/invoice.pdf",
                size_bytes=100,
            ),
            "extracted_text": _metadata(
                "extracted_text",
                "invoices/upload-step-15c-001/extracted_text/extracted.txt",
                size_bytes=200,
            ),
            "extracted_json": _metadata(
                "extracted_json",
                "invoices/upload-step-15c-001/extracted_json/invoice.json",
                size_bytes=300,
            ),
            "processing_metadata": _metadata(
                "processing_metadata",
                (
                    "invoices/upload-step-15c-001/metadata/"
                    "processing_metadata.json"
                ),
                size_bytes=400,
            ),
        }

        for artifact_type, metadata in records.items():
            save_artifact_metadata(
                metadata,
                invoice_number=invoice_number,
                upload_id=upload_id,
                original_filename=(
                    "invoice.pdf" if artifact_type == "original" else None
                ),
            )

        updated_original = dict(records["original"])
        updated_original["size_bytes"] = 101
        save_artifact_metadata(
            updated_original,
            invoice_number=invoice_number,
            upload_id=upload_id,
            original_filename="invoice.pdf",
        )

        by_invoice = list_artifacts_for_invoice(invoice_number)
        by_upload = list_artifacts_for_upload(upload_id)
        if len(by_invoice) != 4 or len(by_upload) != 4:
            raise AssertionError(
                "Expected four repeat-safe artifact metadata records."
            )

        original = next(
            item for item in by_invoice if item.artifact_type == "original"
        )
        if original.size_bytes != 101:
            raise AssertionError("Repeated save did not update original metadata.")

        print("Artifact repository test passed.")
        for artifact in by_invoice:
            print(
                f"{artifact.artifact_type}: "
                f"upload_id={artifact.upload_id}, uri={artifact.uri}"
            )

        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
