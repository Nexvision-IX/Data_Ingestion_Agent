"""
Seed Mock SAP API with PO and GRN reference data for AP Automation Demo.
Run this after starting the Mock SAP API on http://127.0.0.1:8001.
Then go to Streamlit > Reference Data & Test Setup > Structured Sync > Start Structured Sync.
"""
import os
import requests

BASE_URL = os.getenv("MOCK_API_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
SAP_USERNAME = os.getenv("SAP_USERNAME", "sap_user")
SAP_PASSWORD = os.getenv("SAP_PASSWORD", "sap_pass")
AUTH = (SAP_USERNAME, SAP_PASSWORD)

PO_PAYLOADS = [
    {
        "document_type": "po",
        "po_number": "PO-CLEAN-001",
        "vendor_name": "SUPPLIER CLEAN SUPPLIES LTD",
        "vendor_number": "SUPPLIER_CLEAN_SUPPLIES_LTD",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Office supplies",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-GRN-MISSING-001",
        "vendor_name": "GRN Missing Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Consulting services",
                "qty": 5,
                "unit_price": 2000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-VENDOR-001",
        "vendor_name": "Correct Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Laptop accessories",
                "qty": 4,
                "unit_price": 2500.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-AMOUNT-001",
        "vendor_name": "Amount Test Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Professional services",
                "qty": 6,
                "unit_price": 1666.6666666666667,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-TAX-001",
        "vendor_name": "Tax Test Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Taxable supplies",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-CLOSED-001",
        "vendor_name": "Closed PO Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Closed",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Closed PO service",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-PENDING-GRN-001",
        "vendor_name": "Pending GRN Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Goods awaiting receipt",
                "qty": 8,
                "unit_price": 1250.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-POSTED-DUP-001",
        "vendor_name": "Posted Duplicate Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Previously posted service",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-PAYTERMS-001",
        "vendor_name": "Payment Terms Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Payment terms test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-DATE-001",
        "vendor_name": "Date Test Vendor Ltd",
        "po_date": "2026-01-10",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Date sequence test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-CONSUME-001",
        "vendor_name": "Consumption Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Consumption test item",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-OCR-LOW-001",
        "vendor_name": "OCR Low Confidence Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "OCR low confidence service",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-LINES-001",
        "vendor_name": "Missing Lines Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Reference item",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-TOTAL-ONLY-001",
        "vendor_name": "Total Only Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Reference item",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-OCR-RETRY-001",
        "vendor_name": "OCR Retry Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "OCR retry item",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-OCR-FAIL-001",
        "vendor_name": "OCR Failure Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Unreadable invoice test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-RESP-GENERAL-001",
        "vendor_name": "Response General Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Response test without GRN",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-RESP-TERMS-001",
        "vendor_name": "Response Terms Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Payment terms response test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-RECHECK-POSTED-001",
        "vendor_name": "Recheck Posted Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Posted recheck guardrail",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-CLEAN-NOT-PAID-001",
        "vendor_name": "Clean Not Paid Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Clean posting not paid",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-COMM-001",
        "vendor_name": "Communication Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Open",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Communication test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "po",
        "po_number": "PO-OWNER-001",
        "vendor_name": "Owner Assignment Vendor Ltd",
        "po_date": "2026-06-01",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "amount": 11800.0,
        "po_status": "Cancelled",
        "payment_terms": "NET 30",
        "line_items": [
            {
                "line_no": 1,
                "description": "Owner assignment test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    }
]

GRN_PAYLOADS = [
    {
        "document_type": "grn",
        "gr_number": "GRN-CLEAN-001",
        "po_number": "PO-CLEAN-001",
        "vendor_name": "SUPPLIER CLEAN SUPPLIES LTD",
        "vendor_number": "SUPPLIER_CLEAN_SUPPLIES_LTD",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Office supplies",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-VENDOR-001",
        "po_number": "PO-VENDOR-001",
        "vendor_name": "Correct Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Laptop accessories",
                "qty": 4,
                "unit_price": 2500.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-AMOUNT-001",
        "po_number": "PO-AMOUNT-001",
        "vendor_name": "Amount Test Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Professional services",
                "qty": 6,
                "unit_price": 1666.6666666666667,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-TAX-001",
        "po_number": "PO-TAX-001",
        "vendor_name": "Tax Test Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Taxable supplies",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-CLOSED-001",
        "po_number": "PO-CLOSED-001",
        "vendor_name": "Closed PO Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Closed PO service",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-PENDING-001",
        "po_number": "PO-PENDING-GRN-001",
        "vendor_name": "Pending GRN Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Pending",
        "line_items": [
            {
                "line_no": 1,
                "description": "Goods awaiting receipt",
                "qty": 8,
                "unit_price": 1250.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-POSTED-DUP-001",
        "po_number": "PO-POSTED-DUP-001",
        "vendor_name": "Posted Duplicate Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Previously posted service",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-PAYTERMS-001",
        "po_number": "PO-PAYTERMS-001",
        "vendor_name": "Payment Terms Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Payment terms test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-DATE-001",
        "po_number": "PO-DATE-001",
        "vendor_name": "Date Test Vendor Ltd",
        "gr_date": "2026-01-20",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Date sequence test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-CONSUME-001",
        "po_number": "PO-CONSUME-001",
        "vendor_name": "Consumption Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Consumption test item",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-OCR-LOW-001",
        "po_number": "PO-OCR-LOW-001",
        "vendor_name": "OCR Low Confidence Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "OCR low confidence service",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-LINES-001",
        "po_number": "PO-LINES-001",
        "vendor_name": "Missing Lines Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Reference item",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-TOTAL-ONLY-001",
        "po_number": "PO-TOTAL-ONLY-001",
        "vendor_name": "Total Only Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Reference item",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-OCR-RETRY-001",
        "po_number": "PO-OCR-RETRY-001",
        "vendor_name": "OCR Retry Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "OCR retry item",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-OCR-FAIL-001",
        "po_number": "PO-OCR-FAIL-001",
        "vendor_name": "OCR Failure Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Unreadable invoice test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-RESP-TERMS-001",
        "po_number": "PO-RESP-TERMS-001",
        "vendor_name": "Response Terms Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Payment terms response test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-RECHECK-POSTED-001",
        "po_number": "PO-RECHECK-POSTED-001",
        "vendor_name": "Recheck Posted Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Posted recheck guardrail",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-CLEAN-NOT-PAID-001",
        "po_number": "PO-CLEAN-NOT-PAID-001",
        "vendor_name": "Clean Not Paid Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Clean posting not paid",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-COMM-001",
        "po_number": "PO-COMM-001",
        "vendor_name": "Communication Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Communication test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    },
    {
        "document_type": "grn",
        "gr_number": "GRN-OWNER-001",
        "po_number": "PO-OWNER-001",
        "vendor_name": "Owner Assignment Vendor Ltd",
        "gr_date": "2026-06-15",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "document_total": 10000.0,
        "amount": 10000.0,
        "gr_status": "Received",
        "line_items": [
            {
                "line_no": 1,
                "description": "Owner assignment test",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0
            }
        ],
        "last_modified": "2026-06-29T10:00:00"
    }
]


def post_record(path, payload, key):
    url = f"{BASE_URL}{path}"
    try:
        response = requests.post(url, json=payload, auth=AUTH, timeout=60)
        if response.status_code < 400:
            print(f"OK   {key} -> {url}")
        else:
            print(f"FAIL {key} -> {response.status_code} {response.text}")
    except Exception as exc:
        print(f"ERROR {key} -> {exc}")


def main():
    print(f"Seeding Mock SAP API at {BASE_URL}")
    for po in PO_PAYLOADS:
        post_record("/sap/po", po, po.get("po_number"))
    for grn in GRN_PAYLOADS:
        post_record("/sap/gr", grn, grn.get("gr_number"))
    print("Done. Now run Structured Sync in Streamlit.")


if __name__ == "__main__":
    main()
