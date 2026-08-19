from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from groq import Groq

from ingestion.master_ingestion import get_conn, init_db, upsert_invoice
from ingestion.ap_agent_trigger import trigger_ap_agent_process_new
from ap_database.extraction_confidence import (
    canonicalize_extraction_confidence,
)

# =========================================================
# ENV
# =========================================================
load_dotenv()

MODEL_NAME = os.getenv("GROQ_MODEL_NAME", "openai/gpt-oss-120b")
_client = None


def get_groq_client():
    global _client
    if _client is None:
        _client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _client


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
    """Keep the original invoice date style; do not reformat."""
    v = safe_text(value)
    if not v:
        return ""

    v = v.strip(" \t\r\n:;,-./")
    if not re.search(r"\d", v):
        return ""

    return v


def detect_currency(text: str) -> str:
    text = text or ""
    upper_text = text.upper()
    if "$" in text or " USD" in upper_text:
        return "USD"
    if "₹" in text or " INR" in upper_text:
        return "INR"
    if "€" in text or " EUR" in upper_text:
        return "EUR"
    if "£" in text or " GBP" in upper_text:
        return "GBP"
    if "AED" in upper_text:
        return "AED"
    if "SAR" in upper_text:
        return "SAR"
    return ""


def normalize_ocr_text(text: str) -> str:
    """Light, generic cleanup only. No field extraction logic here."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\x0c", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_payment_terms(value: Any) -> str:
    raw = safe_text(value).upper()
    if not raw:
        return ""

    compact = re.sub(r"[^A-Z0-9]+", "", raw)
    if compact in {"NET30", "N30", "NET30DAYS", "NET30DAY"}:
        return "NET 30"
    if compact in {"NET45", "N45", "NET45DAYS", "NET45DAY"}:
        return "NET 45"
    if compact in {"NET60", "N60", "NET60DAYS", "NET60DAY"}:
        return "NET 60"
    if compact in {"IMMEDIATE", "DUEONRECEIPT", "PAYABLEONRECEIPT"}:
        return "DUE_ON_RECEIPT"

    due_in_match = re.search(r"\bDUE\s+IN\s+(\d{1,3})\s+DAYS?\b", raw)
    if due_in_match:
        return f"NET {int(due_in_match.group(1))}"

    net_match = re.search(r"\bNET\s*(\d{1,3})\s*(?:DAYS?)?\b", raw)
    if net_match:
        return f"NET {int(net_match.group(1))}"

    return safe_text(value)


def clean_vendor_name(value: Any) -> str:
    vendor_name = safe_text(value)
    if not vendor_name:
        return ""

    return re.sub(
        r"^\s*(?:supplier\s+name|vendor\s+name|supplier|vendor)"
        r"\s*[:\-]?\s+",
        "",
        vendor_name,
        count=1,
        flags=re.IGNORECASE,
    ).strip()


def extract_payment_terms_from_text(text: str) -> str:
    normalized = normalize_ocr_text(text).upper()
    patterns = (
        r"\bPAYMENT\s+TERMS?\s*[:\-]?\s*(NET\s*\d{1,3}(?:\s*DAYS?)?)",
        r"\bTERMS?\s*[:\-]?\s*(NET\s*\d{1,3}(?:\s*DAYS?)?)",
        r"\b(NET\s*\d{1,3}(?:\s*DAYS?)?)\b",
        r"\b(DUE\s+IN\s+\d{1,3}\s+DAYS?)\b",
        r"\b(DUE\s+ON\s+RECEIPT)\b",
        r"\b(IMMEDIATE)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return normalize_payment_terms(match.group(1))
    return ""


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
9. Payment terms must come only from explicit labels like Payment Terms / Terms / Net 30 / Due on Receipt.
10. Line items should include only actual billed rows.
11. Ignore footer rows like totals, subtotal, tax summary.

Return exactly this schema:
{{
  "document_type": "invoice",
  "source_system": "OCR_GROQ",
  "invoice_number": "",
  "po_number": "",
  "vendor_name": "",
  "vendor_number": "",
  "invoice_date": "",
  "due_date": "",
  "currency": "",
  "document_subtotal": 0,
  "tax_amount": 0,
  "vat_percent": 0,
  "document_total": 0,
  "payment_terms": "",
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

        if unit_price == 0.0 and line_amount > 0 and qty is None:
            unit_price = line_amount

        if line_amount == 0.0 and qty is not None and unit_price > 0:
            line_amount = round(qty * unit_price, 2)

        normalized_items.append(
            {
                "line_no": idx + 1,
                "description": description,
                "qty": qty,
                "unit_price": unit_price,
                "line_amount": line_amount,
            }
        )

    return normalized_items


# =========================================================
# SCHEMA NORMALIZATION
# =========================================================
def normalize_invoice_schema(
    data: Dict[str, Any],
    raw_ocr_text: str = "",
    structured_ocr_text: str = "",
) -> Dict[str, Any]:
    currency = safe_text(data.get("currency")).upper()
    if not currency:
        currency = detect_currency(raw_ocr_text) or detect_currency(structured_ocr_text)

    combined_text = "\n".join([structured_ocr_text or "", raw_ocr_text or ""])
    payment_terms = normalize_payment_terms(data.get("payment_terms"))
    if not payment_terms:
        payment_terms = extract_payment_terms_from_text(combined_text)

    normalized = {
        "document_type": safe_text(data.get("document_type")) or "invoice",
        "source_system": "OCR_GROQ",
        "invoice_number": safe_text(data.get("invoice_number")),
        "po_number": safe_text(data.get("po_number")),
        "vendor_name": clean_vendor_name(data.get("vendor_name")),
        "vendor_number": safe_text(data.get("vendor_number")),
        "invoice_date": normalize_date(data.get("invoice_date")),
        "due_date": normalize_date(data.get("due_date")),
        "currency": currency,
        "document_subtotal": safe_float(data.get("document_subtotal")),
        "tax_amount": safe_float(data.get("tax_amount")),
        "vat_percent": safe_float(data.get("vat_percent")),
        "document_total": safe_float(data.get("document_total")),
        "payment_terms": payment_terms,
        "payment_status": safe_text(data.get("payment_status")),
        "line_items": normalize_line_items(data.get("line_items", [])),
        "last_modified": safe_text(data.get("last_modified")),
        "raw_ocr_text": raw_ocr_text,
        "structured_ocr_text": structured_ocr_text,
        "validation_status": True,
        "review_required": False,
        "warnings": [],
        "field_confidence": {},
        "extraction_confidence": None,
        "extraction_confidence_source": "UNAVAILABLE",
        "ocr_provider": "PADDLE_OCR",
        "extraction_provider": "GROQ",
        "extraction_model": MODEL_NAME,
        "extraction_attempt_number": 1,
        "retry_count": 0,
        "extraction_timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    items_sum = round(
        sum(safe_float(item.get("line_amount")) for item in normalized["line_items"]),
        2,
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
    po_number = safe_text(data.get("po_number"))
    payment_terms = safe_text(data.get("payment_terms"))

    # Critical extraction quality gates. PO and payment terms are intentionally
    # not critical here, because AP validation should raise business exceptions
    # for those instead of blocking OCR extraction.
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

    if not invoice_date:
        warnings.append("Invoice date missing")
        score -= 5

    if not currency:
        warnings.append("Currency missing")
        score -= 3

    if not po_number:
        warnings.append("PO number not extracted; AP validation will classify if required")
        score -= 2

    if not payment_terms:
        warnings.append("Payment terms not extracted; AP payment-terms control will compare when reference data exists")
        score -= 1

    if document_subtotal > 0 and document_total > 0:
        expected = round(document_subtotal + tax_amount, 2)
        if abs(expected - document_total) > 1.0:
            warnings.append(
                f"Subtotal + tax ({expected}) does not match total ({document_total})"
            )
            score -= 5

    items_sum = round(sum(safe_float(i.get("line_amount")) for i in line_items), 2)
    if items_sum > 0 and document_total > 0 and abs(items_sum - document_total) > 1.0:
        warnings.append(
            f"Line items sum ({items_sum}) does not match total ({document_total})"
        )
        score -= 5

    field_confidence = {
        "invoice_number": "high" if invoice_number else "low",
        "vendor_name": "high" if vendor_name else "low",
        "po_number": "high" if po_number else "low",
        "payment_terms": "high" if payment_terms else "low",
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
    canonical = canonicalize_extraction_confidence(data)
    data["extraction_confidence"] = canonical.extraction_confidence
    data["extraction_confidence_source"] = canonical.confidence_source
    data["field_confidence"] = canonical.field_confidence
    data["warnings"] = canonical.warnings
    data["raw_extraction_quality_evidence"] = (
        canonical.raw_quality_evidence
    )

    return data


# =========================================================
# LLM EXTRACTION
# =========================================================
def extract_invoice_data(ocr_text: str) -> Dict[str, Any]:
    structured_text = normalize_ocr_text(ocr_text)
    prompt = build_prompt(structured_text)

    response = get_groq_client().chat.completions.create(
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

    return validate_invoice_json(normalized)


# =========================================================
# FILE PROCESSING
# =========================================================
def process_text_file(
    text_file_path,
    save_json: bool = True,
    write_db: bool = True,
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

                try:
                    print("Triggering AP Agent for uploaded invoice...")
                    ap_agent_result = trigger_ap_agent_process_new(limit=50)
                    parsed_json["ap_agent_trigger"] = ap_agent_result
                    print("AP Agent trigger completed:")
                    print(ap_agent_result)

                except Exception as trigger_error:
                    parsed_json["ap_agent_trigger_error"] = str(trigger_error)
                    print(
                        "AP Agent trigger failed, "
                        "but invoice was inserted into invoice_master"
                    )
                    print(trigger_error)

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
