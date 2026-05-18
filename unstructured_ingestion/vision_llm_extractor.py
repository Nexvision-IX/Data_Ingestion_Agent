from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

import json
import os
from ingestion.master_ingestion import get_conn,init_db,upsert_invoice 

# -----------------------------------
# ENV
# -----------------------------------

load_dotenv()

client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)


# -----------------------------------
# PATHS
# -----------------------------------

BASE_DIR = Path(__file__).parent

INPUT_DIR = BASE_DIR / "extracted_text"
OUTPUT_DIR = BASE_DIR / "extracted_json"

OUTPUT_DIR.mkdir(exist_ok=True)


# -----------------------------------
# LLM EXTRACTION
# -----------------------------------

def extract_invoice_data(ocr_text):

    prompt = f"""
You are an invoice extraction engine.

Extract invoice data from this OCR text.

Correct obvious OCR mistakes if necessary.

Return ONLY valid JSON.

Schema:

{{
  "document_type": "invoice",
  "source_system": "OCR_GROQ",
  "invoice_number": "",
  "po_number": "",
  "vendor_name": "",
  "invoice_date": "",
  "currency": "",
  "document_subtotal": 0,
  "tax_amount": 0,
  "vat_percent": 0,
  "document_total": 0,
  "payment_status": "",
  "line_items": [
    {{
      "line_no": 1,
      "description": "",
      "qty": 0,
      "unit_price": 0,
      "line_amount": 0
    }}
    ],
    "last_modified": ""
}}

OCR TEXT:
{ocr_text}
"""

    response = client.chat.completions.create(

        model="llama-3.3-70b-versatile",

        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],

        temperature=0

    )

    return response.choices[0].message.content


# -----------------------------------
# PROCESS FILES
# -----------------------------------

def process_files():

    files = list(INPUT_DIR.glob("*.txt"))

    print(f"\nText Files Found: {len(files)}")

    for file_path in files:

        print(f"\nProcessing -> {file_path.name}")

        try:

            # ---------------------------
            # READ OCR TEXT
            # ---------------------------

            with open(
                file_path,
                "r",
                encoding="utf-8"
            ) as f:

                ocr_text = f.read()

            # ---------------------------
            # LLM EXTRACTION
            # ---------------------------

            extracted_json = extract_invoice_data(
                ocr_text
            )

            # ---------------------------
            # CLEAN JSON
            # ---------------------------

            extracted_json = (
                extracted_json
                .replace("```json", "")
                .replace("```", "")
                .strip()
            )

            parsed_json = json.loads(
                extracted_json
            )

            # ---------------------------
            # SAVE JSON
            # ---------------------------

            output_file = (
                OUTPUT_DIR /
                f"{file_path.stem}.json"
            )

            with open(
                output_file,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    parsed_json,
                    f,
                    indent=4
                )

            print(
                f"JSON saved -> {output_file.name}"
            )
            init_db()

            with get_conn() as conn:
                upsert_invoice(conn, parsed_json)
                conn.commit()

            print("Inserted into invoice_master")

        except Exception as e:

            print(
                f"Failed -> {file_path.name}: {e}"
            )

    print(
        "\nGROQ EXTRACTION COMPLETED"
    )


# -----------------------------------
# ENTRY POINT
# -----------------------------------

if __name__ == "__main__":

    process_files()