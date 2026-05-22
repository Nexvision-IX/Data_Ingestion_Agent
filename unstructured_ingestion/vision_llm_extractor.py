from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from groq import Groq

from ingestion.master_ingestion import get_conn, init_db, upsert_invoice

# =========================================================
# ENV
# =========================================================

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_NAME = "llama-3.3-70b-versatile"

# =========================================================
# PATHS
# =========================================================

BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "extracted_text"
OUTPUT_DIR = BASE_DIR / "extracted_json"

OUTPUT_DIR.mkdir(exist_ok=True)

# =========================================================
# BASIC HELPERS
# =========================================================

def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def safe_float(value: Any) -> float:
    try:
        if value in [None, "", "null", "NULL"]:
            return 0.0

        s = (
            str(value)
            .replace("$", "")
            .replace("₹", "")
            .replace("€", "")
            .replace("£", "")
            .replace("¥", "")
            .strip()
        )

        if re.search(r"\d\.\d{3},\d{2}$", s):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")

        return float(s)
    except Exception:
        return 0.0


def safe_qty(value: Any):
    try:
        if value in [None, "", "null", "NULL"]:
            return None

        cleaned = str(value).replace(",", "").strip()
        qty = float(cleaned)

        if abs(qty - round(qty)) < 1e-9:
            return int(round(qty))

        return qty
    except Exception:
        return None


def normalize_date(value: Any) -> str:
    """
    Keep the original style if it looks like a date.
    Do not reformat.
    """
    v = safe_text(value)
    if not v:
        return ""

    v = v.strip(" \t\r\n:;,-./")
    if not re.search(r"\d", v):
        return ""

    return v


def detect_currency(text: str) -> str:
    text = text or ""
    if "$" in text:
        return "USD"
    if "₹" in text:
        return "INR"
    if "€" in text:
        return "EUR"
    if "£" in text:
        return "GBP"
    if "AED" in text.upper():
        return "AED"
    if "SAR" in text.upper():
        return "SAR"
    return ""


def normalize_ocr_text(text: str) -> str:
    """
    Light, generic cleanup only.
    No field extraction logic here.
    """
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\x0c", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# =========================================================
# PROMPT
# =========================================================

def build_prompt(structured_text: str) -> str:
    return f"""
You are an enterprise invoice extraction engine.

You receive OCR text that has already been lightly cleaned and line-normalized.
The text may still be noisy, but it preserves the document reading order better than raw OCR.

Extract invoice data ONLY from the text.
Do not invent values.
Do not use values from addresses as PO numbers.
Do not guess missing fields.

Rules:
1. Return ONLY valid JSON.
2. Use empty string "" if a text field is missing.
3. Use 0 for missing numeric fields.
4. Use null for missing qty in line items.
5. Preserve dates exactly as written.
6. Vendor name is the seller/issuer at the top of the invoice.
7. Invoice number is the document ID near Invoice # / Invoice No / Invoice Number.
8. PO number must come only from explicit PO labels.
9. Line items should include only actual billed rows.
10. Ignore footer rows like totals, subtotal, tax summary.

Return exactly this schema:
{{
  "document_type": "invoice",
  "source_system": "OCR_GROQ",
  "invoice_number": "",
  "po_number": "",
  "vendor_name": "",
  "invoice_date": "",
  "due_date": "",
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
      "qty": null,
      "unit_price": 0,
      "line_amount": 0
    }}
  ]
}}

OCR TEXT:
{structured_text}
"""

# =========================================================
# RESPONSE PARSING
# =========================================================

def extract_json_from_response(response_text: str) -> Dict[str, Any]:
    text = response_text.strip()
    text = text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise ValueError("Groq response did not contain valid JSON.")

# =========================================================
# LINE ITEMS
# =========================================================

def normalize_line_items(items: Any) -> List[Dict[str, Any]]:
    normalized_items: List[Dict[str, Any]] = []

    if not isinstance(items, list):
        return normalized_items

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        description = safe_text(item.get("description"))
        qty = safe_qty(item.get("qty"))
        unit_price = safe_float(item.get("unit_price"))
        line_amount = safe_float(item.get("line_amount"))

        if not any([description, qty is not None, unit_price, line_amount]):
            continue

        # If the model returned only one amount for a service row,
        # treat it as the line amount and also the unit price.
        if unit_price == 0.0 and line_amount > 0 and qty is None:
            unit_price = line_amount

        if line_amount == 0.0 and qty is not None and unit_price > 0:
            line_amount = round(qty * unit_price, 2)

        normalized_items.append({
            "line_no": idx + 1,
            "description": description,
            "qty": qty,
            "unit_price": unit_price,
            "line_amount": line_amount
        })

    return normalized_items

# =========================================================
# SCHEMA NORMALIZATION
# =========================================================

def normalize_invoice_schema(
    data: Dict[str, Any],
    raw_ocr_text: str = "",
    structured_ocr_text: str = ""
) -> Dict[str, Any]:

    currency = safe_text(data.get("currency")).upper()
    if not currency:
        currency = detect_currency(raw_ocr_text) or detect_currency(structured_ocr_text)

    normalized = {
        "document_type": safe_text(data.get("document_type")) or "invoice",
        "source_system": "OCR_GROQ",
        "invoice_number": safe_text(data.get("invoice_number")),
        "po_number": safe_text(data.get("po_number")),
        "vendor_name": safe_text(data.get("vendor_name")),
        "invoice_date": normalize_date(data.get("invoice_date")),
        "due_date": normalize_date(data.get("due_date")),
        "currency": currency,
        "document_subtotal": safe_float(data.get("document_subtotal")),
        "tax_amount": safe_float(data.get("tax_amount")),
        "vat_percent": safe_float(data.get("vat_percent")),
        "document_total": safe_float(data.get("document_total")),
        "payment_status": safe_text(data.get("payment_status")),
        "line_items": normalize_line_items(data.get("line_items", [])),
        "last_modified": safe_text(data.get("last_modified")),
        "raw_ocr_text": raw_ocr_text,
        "structured_ocr_text": structured_ocr_text,
        "validation_status": True,
        "review_required": False,
        "warnings": [],
        "field_confidence": {},
        "extraction_quality_score": 100,
        "extraction_timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    # Fill totals from line items if explicit totals are missing
    items_sum = round(
        sum(safe_float(item.get("line_amount")) for item in normalized["line_items"]),
        2
    )
    if normalized["document_total"] <= 0 and items_sum > 0:
        normalized["document_total"] = items_sum
    if normalized["document_subtotal"] <= 0 and items_sum > 0:
        normalized["document_subtotal"] = items_sum

    return normalized

# =========================================================
# VALIDATION
# =========================================================

def validate_invoice_json(data: Dict[str, Any]) -> Dict[str, Any]:
    warnings: List[str] = []
    review_required = False
    score = 100

    invoice_number = safe_text(data.get("invoice_number"))
    vendor_name = safe_text(data.get("vendor_name"))
    invoice_date = safe_text(data.get("invoice_date"))
    currency = safe_text(data.get("currency"))
    document_total = safe_float(data.get("document_total"))
    document_subtotal = safe_float(data.get("document_subtotal"))
    tax_amount = safe_float(data.get("tax_amount"))
    line_items = data.get("line_items", [])

    # Critical
    if not invoice_number:
        warnings.append("Invoice number missing")
        review_required = True
        score -= 20

    if not vendor_name:
        warnings.append("Vendor name missing")
        review_required = True
        score -= 15

    if document_total <= 0:
        warnings.append("Document total missing or zero")
        review_required = True
        score -= 20

    if not line_items:
        warnings.append("No line items detected")
        review_required = True
        score -= 20

    # Important but not critical
    if not invoice_date:
        warnings.append("Invoice date missing")
        score -= 5

    if not currency:
        warnings.append("Currency missing")
        score -= 3

    # Totals sanity
    if document_subtotal > 0 and document_total > 0:
        expected = round(document_subtotal + tax_amount, 2)
        if abs(expected - document_total) > 1.0:
            warnings.append(
                f"Subtotal + tax ({expected}) does not match total ({document_total})"
            )
            score -= 5

    items_sum = round(
        sum(safe_float(i.get("line_amount")) for i in line_items),
        2
    )
    if items_sum > 0 and document_total > 0 and abs(items_sum - document_total) > 1.0:
        warnings.append(
            f"Line items sum ({items_sum}) does not match total ({document_total})"
        )
        score -= 5

    field_confidence = {
        "invoice_number": "high" if invoice_number else "low",
        "vendor_name": "high" if vendor_name else "low",
        "invoice_date": "high" if invoice_date else "low",
        "currency": "high" if currency else "low",
        "document_total": "high" if document_total > 0 else "low",
        "line_items": "high" if line_items else "low",
    }

    data["warnings"] = warnings
    data["review_required"] = review_required
    data["validation_status"] = not review_required
    data["field_confidence"] = field_confidence
    data["extraction_quality_score"] = max(0, min(100, score))

    return data

# =========================================================
# LLM EXTRACTION
# =========================================================

def extract_invoice_data(ocr_text: str) -> Dict[str, Any]:
    structured_text = normalize_ocr_text(ocr_text)
    prompt = build_prompt(structured_text)

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    raw_response_text = response.choices[0].message.content
    parsed = extract_json_from_response(raw_response_text)

    normalized = normalize_invoice_schema(
        parsed,
        raw_ocr_text=ocr_text,
        structured_ocr_text=structured_text,
    )

    validated = validate_invoice_json(normalized)
    return validated

# =========================================================
# FILE PROCESSING
# =========================================================

def process_text_file(
    text_file_path,
    save_json: bool = True,
    write_db: bool = True
):
    text_file_path = Path(text_file_path)
    print(f"\nProcessing -> {text_file_path.name}")

    try:
        with open(text_file_path, "r", encoding="utf-8") as f:
            ocr_text = f.read()

        parsed_json = extract_invoice_data(ocr_text)

        parsed_json["last_modified"] = datetime.now().isoformat(timespec="seconds")
        parsed_json["source_document"] = text_file_path.name

        if save_json:
            output_file = OUTPUT_DIR / f"{text_file_path.stem}.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(parsed_json, f, indent=4)
            parsed_json["json_output_path"] = str(output_file)
            print(f"JSON saved -> {output_file.name}")

        parsed_json["db_inserted"] = False
        parsed_json["db_insert_error"] = ""

        if write_db:
            init_db()

            if safe_text(parsed_json.get("invoice_number")):
                with get_conn() as conn:
                    upsert_invoice(conn, parsed_json)
                    conn.commit()
                parsed_json["db_inserted"] = True
                print("Inserted into invoice_master")
            else:
                parsed_json["db_insert_error"] = "Missing invoice number"
                print("DB insert skipped — missing invoice number")

        return parsed_json

    except Exception as e:
        print(f"Failed -> {text_file_path.name}: {e}")
        return None

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    files = list(INPUT_DIR.glob("*.txt"))
    print(f"\nText Files Found: {len(files)}")

    for file_path in files:
        process_text_file(file_path)

    print("\nGROQ EXTRACTION COMPLETED")