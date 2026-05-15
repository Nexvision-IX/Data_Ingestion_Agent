from pathlib import Path
from dotenv import load_dotenv
from PIL import Image

from google import genai

import fitz
import json
import os


# -----------------------------------
# ENV
# -----------------------------------

load_dotenv(override=True)

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)


# -----------------------------------
# PATHS
# -----------------------------------

BASE_DIR = Path(__file__).parent

INPUT_DIR = BASE_DIR / "unstructured_inputs"
OUTPUT_DIR = BASE_DIR / "extracted_json"

OUTPUT_DIR.mkdir(exist_ok=True)


# -----------------------------------
# PDF TO IMAGES
# -----------------------------------

def pdf_to_images(pdf_path):

    images = []

    pdf_document = fitz.open(pdf_path)

    for page_num in range(len(pdf_document)):

        page = pdf_document.load_page(page_num)

        pix = page.get_pixmap(
            matrix=fitz.Matrix(3, 3)
        )

        image_path = (
            OUTPUT_DIR / f"temp_{page_num}.png"
        )

        pix.save(str(image_path))

        images.append(image_path)

    return images


# -----------------------------------
# GEMINI EXTRACTION
# -----------------------------------

def extract_invoice(file_path):

    prompt = """
Extract invoice data from this document.

Return ONLY valid JSON.

Schema:
{
  "document_type": "invoice",
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
    {
      "line_no": 1,
      "description": "",
      "qty": 0,
      "unit_price": 0,
      "line_amount": 0
    }
  ]
}

Correct obvious OCR mistakes if necessary.

Return JSON only.
Do not explain anything.
"""

    image_inputs = []

    # ---------------------------
    # PDF INPUT
    # ---------------------------

    if file_path.suffix.lower() == ".pdf":

        pdf_document = fitz.open(file_path)

        for page_num in range(len(pdf_document)):

            page = pdf_document.load_page(page_num)

            pix = page.get_pixmap(
                matrix=fitz.Matrix(3, 3)
            )

            temp_image_path = (
                OUTPUT_DIR /
                f"temp_{page_num}.png"
            )

            pix.save(str(temp_image_path))

            img = Image.open(
                temp_image_path
            ).copy()

            image_inputs.append(img)

            os.remove(temp_image_path)

    # ---------------------------
    # IMAGE INPUT
    # ---------------------------

    else:

        img = Image.open(file_path).copy()

        image_inputs.append(img)

    # ---------------------------
    # GEMINI CALL
    # ---------------------------

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[prompt] + image_inputs
    )

    return response.text
# -----------------------------------
# PROCESS DOCUMENTS
# -----------------------------------

def process_documents():

    files = list(INPUT_DIR.iterdir())

    print(f"\\nFiles Found: {len(files)}")

    for file_path in files:

        print(f"\\nProcessing -> {file_path.name}")

        try:

            extracted_json = extract_invoice(file_path)

            # remove markdown if Gemini returns it
            extracted_json = (
                extracted_json
                .replace("```json", "")
                .replace("```", "")
                .strip()
            )

            parsed_json = json.loads(
                extracted_json
            )

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

        except Exception as e:

            print(
                f"Failed -> {file_path.name}: {e}"
            )

    print(
        "\\nGEMINI VISION EXTRACTION COMPLETED"
    )


# -----------------------------------
# ENTRY POINT
# -----------------------------------

if __name__ == "__main__":

    process_documents()