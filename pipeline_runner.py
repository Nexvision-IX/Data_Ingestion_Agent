import time
from pathlib import Path
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

def process_invoice_pipeline(file_path):

    file_path = Path(file_path)

    total_start = time.time()

    try:

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

            return {
                "status": "failed",
                "step": "ocr"
            }

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

            return {
                "status": "failed",
                "step": "groq"
            }

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

            "parsed_json": parsed_json
        }

    except Exception as e:

        return {
            "status": "failed",
            "error": str(e)
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