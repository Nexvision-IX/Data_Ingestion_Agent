"""Exercise the local storage backend without requiring AWS access."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Force an isolated local backend before importing storage configuration.
os.environ["STORAGE_BACKEND"] = "local"

from ap_storage import (  # noqa: E402
    extracted_json_key,
    extracted_text_key,
    get_storage_service,
    load_storage_settings,
    original_invoice_key,
    processing_metadata_key,
)


def main() -> int:
    invoice_number = "INV_RDS_TEST_001"

    with tempfile.TemporaryDirectory(prefix="ap-storage-test-") as temp_dir:
        os.environ["STORAGE_PATH"] = temp_dir
        service = get_storage_service(load_storage_settings())

        keys = {
            "original": original_invoice_key(
                "INV_RDS_TEST_001.pdf",
                invoice_number=invoice_number,
            ),
            "extracted_text": extracted_text_key(
                invoice_number=invoice_number,
            ),
            "extracted_json": extracted_json_key(
                invoice_number=invoice_number,
            ),
            "processing_metadata": processing_metadata_key(
                invoice_number=invoice_number,
            ),
        }

        results = {
            "original": service.save_bytes(
                keys["original"],
                b"%PDF-1.4\nlocal storage test\n",
                content_type="application/pdf",
                artifact_type="original",
            ),
            "extracted_text": service.save_text(
                keys["extracted_text"],
                "Sample extracted invoice text.",
            ),
            "extracted_json": service.save_json(
                keys["extracted_json"],
                {"invoice_number": invoice_number, "status": "extracted"},
            ),
            "processing_metadata": service.save_json(
                keys["processing_metadata"],
                {
                    "invoice_number": invoice_number,
                    "storage_backend": "local",
                    "artifacts": keys,
                },
                artifact_type="processing_metadata",
            ),
        }

        for artifact_type, key in keys.items():
            if not service.exists(key):
                raise AssertionError(f"Missing local artifact: {key}")
            metadata = service.get_metadata(
                key,
                artifact_type=artifact_type,
            )
            local_path = Path(metadata["local_path"])
            if not local_path.is_file():
                raise AssertionError(f"Missing local file: {local_path}")
            if metadata["size_bytes"] <= 0:
                raise AssertionError(f"Empty local artifact: {local_path}")

        upload_key = original_invoice_key(
            "unsafe/../invoice upload.pdf",
            upload_id="upload/temporary 001",
        )
        if upload_key != (
            "invoices/upload_temporary_001/original/invoice_upload.pdf"
        ):
            raise AssertionError(f"Unexpected sanitized key: {upload_key}")

        print("Local storage test passed.")
        for artifact_type, metadata in results.items():
            print(f"\n{artifact_type}:")
            print(json.dumps(metadata, indent=2))
        print(f"\nSanitized upload-id key: {upload_key}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
