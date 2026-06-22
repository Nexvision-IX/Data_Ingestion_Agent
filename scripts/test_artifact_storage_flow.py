"""Simulate a complete invoice artifact flow using local storage only."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ["STORAGE_BACKEND"] = "local"

from ap_storage import (  # noqa: E402
    InvoiceArtifactBundle,
    get_storage_service,
    load_storage_settings,
)


def main() -> int:
    upload_id = "upload-step-15b-001"
    invoice_number = "INV_RDS_TEST_001"

    with tempfile.TemporaryDirectory(prefix="ap-artifact-flow-") as temp_dir:
        os.environ["STORAGE_PATH"] = temp_dir
        bundle = InvoiceArtifactBundle(
            storage=get_storage_service(load_storage_settings()),
            upload_id=upload_id,
            original_filename="INV_RDS_TEST_001.pdf",
        )

        original = bundle.save_original(
            b"%PDF-1.4\nStep 15B test invoice\n",
            content_type="application/pdf",
        )
        extracted_text = bundle.save_extracted_text(
            "Invoice Number: INV_RDS_TEST_001"
        )
        bundle.record_invoice_number(invoice_number)
        extracted_json = bundle.save_extracted_json(
            {
                "invoice_number": invoice_number,
                "document_total": 118.0,
            }
        )
        processing_metadata = bundle.save_processing_metadata(
            status="success",
        )

        expected_relative_paths = {
            "original": (
                "invoices/upload-step-15b-001/original/"
                "INV_RDS_TEST_001.pdf"
            ),
            "extracted_text": (
                "invoices/upload-step-15b-001/extracted_text/extracted.txt"
            ),
            "extracted_json": (
                "invoices/upload-step-15b-001/extracted_json/invoice.json"
            ),
            "processing_metadata": (
                "invoices/upload-step-15b-001/metadata/"
                "processing_metadata.json"
            ),
        }
        metadata_by_type = {
            "original": original,
            "extracted_text": extracted_text,
            "extracted_json": extracted_json,
            "processing_metadata": processing_metadata,
        }

        root = Path(temp_dir).resolve()
        for artifact_type, relative_path in expected_relative_paths.items():
            expected_path = root / Path(*relative_path.split("/"))
            actual_path = Path(metadata_by_type[artifact_type]["local_path"])
            if actual_path != expected_path or not actual_path.is_file():
                raise AssertionError(
                    f"Unexpected {artifact_type} path: {actual_path}"
                )

        metadata_payload = json.loads(
            Path(processing_metadata["local_path"]).read_text(
                encoding="utf-8"
            )
        )
        if metadata_payload["invoice_number"] != invoice_number:
            raise AssertionError("Invoice number was not linked in metadata.")
        if metadata_payload["upload_id"] != upload_id:
            raise AssertionError("Upload ID was not preserved in metadata.")
        if metadata_payload["storage_backend"] != "local":
            raise AssertionError("Unexpected storage backend metadata.")

        print("Artifact storage flow test passed.")
        print(json.dumps(metadata_by_type, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
