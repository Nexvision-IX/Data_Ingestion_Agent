import json
import re
from pathlib import Path


# -----------------------------------
# PATHS
# -----------------------------------

BASE_DIR = Path(__file__).parent

INPUT_DIR = BASE_DIR / "extracted_text"
OUTPUT_DIR = BASE_DIR / "extracted_json"

OUTPUT_DIR.mkdir(exist_ok=True)


# -----------------------------------
# HELPERS
# -----------------------------------

def extract_field(pattern, text):

    match = re.search(pattern, text, re.IGNORECASE)

    if match:
        return match.group(1).strip()

    return None


# -----------------------------------
# LINE ITEM EXTRACTION
# -----------------------------------

def extract_line_items(text):

    line_items = []

    lines = text.split("\n")

    line_no = 1

    for line in lines:

        # very basic invoice row detection
        match = re.search(

            r"^\d+\s+(.+?)\s+(\d+)\s+INR\s+([\d,]+\.\d+)\s+INR\s+([\d,]+\.\d+)",

            line
        )

        if match:

            description = match.group(1).strip()

            qty = int(match.group(2))

            unit_price = float(
                match.group(3).replace(",", "")
            )

            line_amount = float(
                match.group(4).replace(",", "")
            )

            line_items.append({

                "line_no": line_no,
                "description": description,
                "qty": qty,
                "unit_price": unit_price,
                "line_amount": line_amount
            })

            line_no += 1

    return line_items


# -----------------------------------
# MAIN EXTRACTION
# -----------------------------------

def extract_invoice_data(text):

    invoice_data = {

        "document_type": "invoice",

        "invoice_number": extract_field(
            r"INVOICE\s+([A-Z0-9]+)",
            text
        ),

        "po_number": extract_field(
            r"PO450\d+",
            text
        ),

        "vendor_name": extract_field(
            r"\d{4}-\d{2}-\d{2}\n(.+?)\nVendor",
            text
        ),

        "invoice_date": extract_field(
            r"Invoice Date\s+(\d{4}-\d{2}-\d{2})",
            text
        ),

        "currency": extract_field(
            r"Currency\s+([A-Z]+)",
            text
        ),

        "document_subtotal": extract_field(
            r"Subtotal\s+INR\s+([\d,]+\.\d+)",
            text
        ),

        "tax_amount": extract_field(
            r"VAT\s+\(18%\)\s+INR\s+([\d,]+\.\d+)",
            text
        ),

        "document_total": extract_field(
            r"Grand Total\s+INR\s+([\d,]+\.\d+)",
            text
        ),

        "line_items": extract_line_items(text)
    }

    return invoice_data


# -----------------------------------
# PROCESS FILES
# -----------------------------------

def process_files():

    files = list(INPUT_DIR.glob("*.txt"))

    print(f"\nText Files Found: {len(files)}")

    for file_path in files:

        print(f"\nProcessing -> {file_path.name}")

        with open(file_path, "r", encoding="utf-8") as f:

            text = f.read()

        invoice_json = extract_invoice_data(text)

        output_file = OUTPUT_DIR / f"{file_path.stem}.json"

        with open(output_file, "w", encoding="utf-8") as f:

            json.dump(invoice_json, f, indent=4)

        print(f"JSON saved -> {output_file.name}")

    print("\nINVOICE EXTRACTION COMPLETED")


# -----------------------------------
# ENTRY POINT
# -----------------------------------

if __name__ == "__main__":

    process_files()