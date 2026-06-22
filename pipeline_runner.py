import time
import mimetypes
import uuid
from pathlib import Path

from ap_storage import InvoiceArtifactBundle, get_storage_service
# -----------------------------------
# STRUCTURED INGESTION
# -----------------------------------
from ingestion.master_ingestion import (run_ingestion)
# -----------------------------------
# UNSTRUCTURED INGESTION
# -----------------------------------
from unstructured_ingestion.paddle_ocr_extractor import (process_document)
from unstructured_ingestion.vision_llm_extractor import (process_text_file)
# -----------------------------------
# STRUCTURED PIPELINE
# -----------------------------------
def sync_structured_sources():

    print(
        "\nStarting Structured Ingestion..."
    )

    start_time = time.time()

    try:

        ingestion_result = run_ingestion()

        total_time = round(
            time.time() - start_time,
            2
        )

        # ---------------------------
        # SAFETY CHECK
        # ---------------------------

        if not ingestion_result:

            return {

                "status": "failed",

                "error": (
                    "run_ingestion() "
                    "returned empty result"
                )
            }

        return {

            "status": "success",

            "total_time_sec": total_time,

            "details": ingestion_result
        }

    except Exception as e:

        return {

            "status": "failed",

            "error": str(e)
        }
# -----------------------------------
# UNSTRUCTURED PIPELINE
# -----------------------------------

def _create_artifact_bundle(file_path: Path) -> InvoiceArtifactBundle:
    bundle = InvoiceArtifactBundle(
        storage=get_storage_service(),
        upload_id=uuid.uuid4().hex,
        original_filename=file_path.name,
    )
    content_type = (
        mimetypes.guess_type(file_path.name)[0]
        or "application/octet-stream"
    )
    bundle.save_original(
        file_path.read_bytes(),
        content_type=content_type,
    )
    return bundle


def _save_failure_metadata(
    bundle: InvoiceArtifactBundle | None,
    *,
    step: str,
    error: str | None = None,
):
    if bundle is None:
        return None
    details = {"failed_step": step}
    if error:
        details["error"] = error
    try:
        return bundle.save_processing_metadata(
            status="failed",
            extra=details,
        )
    except Exception as metadata_error:
        print(
            "Processing metadata could not be stored: "
            f"{type(metadata_error).__name__}"
        )
        return None


def process_invoice_pipeline(
    file_path,
    artifact_bundle: InvoiceArtifactBundle | None = None,
):

    file_path = Path(file_path)
    bundle = artifact_bundle

    total_start = time.time()

    try:

        if bundle is None:
            bundle = _create_artifact_bundle(file_path)

        print(
            f"\nStarting OCR Pipeline "
            f"for {file_path.name}"
        )

        # ---------------------------
        # OCR STEP
        # ---------------------------

        ocr_start = time.time()

        text_file_path = process_document(
            file_path
        )

        ocr_time = round(
            time.time() - ocr_start,
            2
        )

        if not text_file_path:

            processing_metadata = _save_failure_metadata(
                bundle,
                step="ocr",
            )

            return {
                "status": "failed",
                "step": "ocr",
                "processing_metadata": processing_metadata,
            }

        extracted_text = Path(text_file_path).read_text(encoding="utf-8")
        bundle.save_extracted_text(extracted_text)

        # ---------------------------
        # GROQ STEP
        # ---------------------------

        groq_start = time.time()

        parsed_json = process_text_file(
            text_file_path
        )

        groq_time = round(
            time.time() - groq_start,
            2
        )

        if not parsed_json:

            processing_metadata = _save_failure_metadata(
                bundle,
                step="groq",
            )

            return {
                "status": "failed",
                "step": "groq",
                "processing_metadata": processing_metadata,
            }

        bundle.record_invoice_number(parsed_json.get("invoice_number"))
        bundle.save_extracted_json(parsed_json)
        processing_metadata = bundle.save_processing_metadata(
            status="success",
        )

        # ---------------------------
        # TOTAL TIME
        # ---------------------------

        total_time = round(
            time.time() - total_start,
            2
        )

        return {
            "status": "success",

            "ocr_time_sec": ocr_time,

            "groq_time_sec": groq_time,

            "total_time_sec": total_time,

            "parsed_json": parsed_json,

            "artifact_metadata": {
                **bundle.artifacts,
                "processing_metadata": processing_metadata,
                "upload_id": bundle.upload_id,
            },
        }

    except Exception as e:

        processing_metadata = _save_failure_metadata(
            bundle,
            step="pipeline",
            error=type(e).__name__,
        )

        return {
            "status": "failed",
            "error": str(e),
            "processing_metadata": processing_metadata,
        }


# -----------------------------------
# MANUAL TEST
# -----------------------------------

if __name__ == "__main__":

    # structured test
    structured_result = (
        sync_structured_sources()
    )

    print(structured_result)

    # unstructured test
    sample_file = Path(
        "unstructured_ingestion/"
        "unstructured_inputs/"
        "invoice_05.pdf"
    )

    result = process_invoice_pipeline(
        sample_file
    )

    print(result)
